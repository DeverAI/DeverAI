"""无状态 LLM 转发代理：浏览器把 base_url/api_key 随请求发给本代理，本代理转发并流式透传。

- 服务端【不存储、不落盘】任何 API Key，密钥只存在于浏览器内存。
- 统一走同源代理，规避各家 LLM 提供商对浏览器直连的 CORS 限制。
"""
import ipaddress
import json
import re
import socket
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from .auth import current_user
from .config import get_config

router = APIRouter(prefix="/api/llm", tags=["llm-proxy"])

_FORBIDDEN_NETLOCS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}
_MAX_URL = 500


def _normalize_numeric_ip(host: str):
    """按浏览器 GURL 语义归一化「数值形态 host」为点分十进制；非数值形态返回 None。

    v8.7 审查修复：inet_aton 不识别十六进制/混合形式（0x7f000001、0x7f.0.0.1），
    而 httpx/浏览器会按数值 IP 解析 → SSRF 旁路。此处先归一化再判环回/内网。
    """
    h = (host or "").lower()
    if not h or not re.fullmatch(r"[0-9a-fx.]+", h):
        return None
    parts = h.split(".")
    if len(parts) > 4 or any(not p for p in parts):
        return None
    vals = []
    for p in parts:
        try:
            # v8.14：0x 十六进制前缀必须优先于前导零八进制判断（否则 0x7f000001 被误判为八进制失败 → SSRF 绕过）
            if p.startswith("0x") or p.startswith("0X"):
                v = int(p, 16)
            elif len(p) > 1 and p.startswith("0"):
                # v8.13：前导零按八进制解析（与 inet_aton/GURL 旧式语义一致）
                v = int(p, 8)
            else:
                v = int(p, 10)
        except ValueError:
            return None
        if not 0 <= v <= 0xFFFFFFFF:
            return None
        vals.append(v)
    try:
        if len(vals) == 1:
            v = vals[0]
            out = [(v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF]
        elif len(vals) == 2:
            a, b = vals
            if a > 0xFF or b > 0xFFFFFF:
                return None
            out = [a, (b >> 16) & 0xFF, (b >> 8) & 0xFF, b & 0xFF]
        elif len(vals) == 3:
            a, b, c = vals
            if a > 0xFF or b > 0xFF or c > 0xFFFF:
                return None
            out = [a, b, (c >> 8) & 0xFF, c & 0xFF]
        else:
            if any(v > 0xFF for v in vals):
                return None
            out = vals
    except Exception:
        return None
    return ".".join(str(v) for v in out)


def _ip_of(host: str):
    """把 host 文本解析为 ipaddress 对象；支持十六进制/127.1/2130706433 等旧式写法。"""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    norm = _normalize_numeric_ip(host)
    if norm:
        try:
            return ipaddress.ip_address(norm)
        except ValueError:
            pass
    try:
        return ipaddress.ip_address(socket.inet_aton(host))
    except OSError:
        return None


def _canon_ip(a):
    """v8.14b：IPv4-mapped IPv6（::ffff:127.0.0.1）显式解包为 IPv4 再判定。

    部分旧 Python 版本 is_loopback/is_private 不展开 ipv4_mapped，会漏判
    （与 lite_server 同源修复，保持两版语义一致）。
    """
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        return a.ipv4_mapped
    return a


def _is_loopback(host: str, infos=None) -> bool:
    """判断 host 是否为环回地址（127.0.0.1/localhost/::1/127.1 缩写）。"""
    host = (host or "").lower().strip()
    if host in _FORBIDDEN_NETLOCS or host.endswith(".localhost"):
        return True
    a = _canon_ip(_ip_of(host))
    if a is not None:
        return a.is_loopback
    if infos is None:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
    # 混合解析安全：任一解析结果命中环回即视为环回（any 而非 all）——
    # all 语义下「公网 + 环回」双解析可绕过拦截，而下游 HTTP 客户端可能连到环回那一条
    return bool(infos) and any(_canon_ip(ipaddress.ip_address(i[4][0])).is_loopback for i in infos)


def _is_private_nonloopback(host: str, infos=None) -> bool:
    """判断 host 是否为非环回的私有/内网地址（10.x/172.16-31.x/192.168.x/链路本地）。

    环回地址（本地 LLM）由 llm_allow_loopback 开关单独控制。
    """
    a = _canon_ip(_ip_of(host))
    if a is not None:
        return (a.is_private or a.is_link_local or a.is_unspecified
                or not a.is_global) and not a.is_loopback
    if infos is None:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
    return bool(infos) and any(
        (_canon_ip(ipaddress.ip_address(i[4][0])).is_private
         or _canon_ip(ipaddress.ip_address(i[4][0])).is_link_local
         or _canon_ip(ipaddress.ip_address(i[4][0])).is_unspecified
         or not _canon_ip(ipaddress.ip_address(i[4][0])).is_global)
        and not _canon_ip(ipaddress.ip_address(i[4][0])).is_loopback
        for i in infos
    )


async def _validate_base(base: str, request: Request) -> str:
    # P2-2：非字符串 base_url（dict/list 等）此前会 AttributeError → 500，先强制转字符串
    base = str(base or "").strip()
    if not base:
        raise HTTPException(400, "缺少模型服务地址 (base_url)")
    if len(base) > _MAX_URL:
        raise HTTPException(400, "模型服务地址过长")
    p = urlparse(base)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise HTTPException(400, "模型服务地址必须为 http(s):// 格式")
    # v8.13：带 query/fragment 的 base 拼接 /chat/completions 会生成错误 URL，直接拒绝
    if p.query or p.fragment:
        raise HTTPException(400, "模型服务地址不能带查询参数或锚点")
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        raise HTTPException(400, "端口无效")
    host = (p.hostname or "").lower()
    if not host:
        raise HTTPException(400, "模型服务地址缺少主机名")
    # v8.13：数值形态 host（127.1、0x7f000001、2130706433）先归一化再判定；
    # 归一化成功即为 IP 字面量，跳过 DNS 解析——此前 Windows 上 getaddrinfo("127.1")
    # 直接失败，把本应判为环回的地址误报为「域名无法解析」（test_lite 3b 回归）。
    norm = _normalize_numeric_ip(host)
    if norm:
        host = norm
    infos = None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # P2-7：getaddrinfo 是阻塞调用——非 IP 字面量域名在后台线程解析，防阻塞事件循环
        try:
            import asyncio as _aio
            infos = await _aio.to_thread(socket.getaddrinfo, host, None)
        except OSError:
            raise HTTPException(400, "模型服务地址域名无法解析")
        if not infos:
            raise HTTPException(400, "模型服务地址域名无法解析")
    # v6.2 P1-2：SSRF 防护——始终阻止非环回内网地址（防探测 Redis/PG 等内部服务）
    if _is_private_nonloopback(host, infos):
        raise HTTPException(400, "不允许将代理指向内网地址")
    cfg = get_config()
    # 环回地址由开关控制：本地 LLM（ollama/llama.cpp）需要环回；生产多租户关闭
    if _is_loopback(host, infos) and not cfg.llm_allow_loopback:
        raise HTTPException(400, "不允许将代理指向本机地址")
    # 始终阻止指向本服务自身端口的自环（自身端口优先取本请求实际到达端口，回落注入/配置端口）
    from .config import self_port
    if _is_loopback(host, infos) and port == (request.url.port or self_port()):
        raise HTTPException(400, "不允许将代理指向本服务自身")
    return base.rstrip("/")


# v6.2 P2-5：上游错误体脱敏正则（移除 URL、Key 片段、内网 IP）
_SENSITIVE_RE = re.compile(
    r"(sk-[A-Za-z0-9]{6,})"            # API Key 片段
    r"|(https?://[^\s\"']{8,})"        # URL
    r"|(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",  # IP
    re.IGNORECASE,
)


def _sanitize_error(text: str) -> str:
    """脱敏上游错误信息，避免泄露内部 URL/Key/IP。"""
    return _SENSITIVE_RE.sub("[REDACTED]", text)[:400]


@router.post("/chat")
async def llm_chat(request: Request, user: dict = Depends(current_user)):
    # v8.13：请求体前置上限，超大 JSON 不进入解析（防内存/CPU DoS）
    cl = request.headers.get("content-length", "")
    try:
        if cl and int(cl) > 5_000_000:
            raise HTTPException(413, "请求体过大")
    except ValueError:
        pass
    try:
        raw_body = await request.body()
        if len(raw_body) > 5_000_000:
            raise HTTPException(413, "请求体过大")
        body = json.loads(raw_body or b"{}")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "请求体不是合法 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体格式错误")

    model = str(body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "缺少模型名称")
    if len(model) > 200:
        raise HTTPException(400, "模型名称过长")
    # 模型注册表解析：model 在注册表里且有独立 url/api_key 时，覆盖浏览器传的全局配置
    # （与桌面版主对话行为一致，避免"选择注册表模型后 url/api_key 不生效"）
    try:
        from desktop import models as models_mod
        m = models_mod.get_model(model)
        if m is not None:
            if m.url:
                body["base_url"] = m.url
            if m.api_key:
                body["api_key"] = m.api_key
    except Exception:
        pass
    base = await _validate_base(body.get("base_url"), request)
    api_key = str(body.get("api_key") or "")
    messages = body.get("messages")
    if not api_key:
        raise HTTPException(400, "缺少 API Key")
    if len(api_key) > 4096:
        raise HTTPException(400, "API Key 过长")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "缺少对话消息")
    # 消息形状校验：每条必须是 dict 且 role 为非空字符串（防畸形请求透传上游）
    if len(messages) > 500:
        raise HTTPException(400, "对话消息过多")
    for m in messages:
        if not isinstance(m, dict) or not isinstance(m.get("role"), str) or not m.get("role").strip():
            raise HTTPException(400, "对话消息格式错误")
        if not isinstance(m.get("content"), (str, type(None), list)):
            raise HTTPException(400, "对话消息内容格式错误")
    # 消息体总长上限（防无限制透传撑爆上游/本服务内存）
    if len(json.dumps(messages, ensure_ascii=False)) > 2_000_000:
        raise HTTPException(400, "对话消息过长")

    target = base + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if isinstance(body.get("stream"), bool):
        stream_flag = body["stream"]
    else:
        stream_flag = str(body.get("stream") or "").strip().lower() in ("1", "true", "yes", "on")
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream_flag,
    }
    temperature = body.get("temperature")
    if temperature is not None:
        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            raise HTTPException(400, "temperature 必须为数字")
        if not (-2.0 <= temperature <= 2.0):
            raise HTTPException(400, "temperature 超出允许范围")
        payload["temperature"] = temperature
    if body.get("max_tokens"):
        try:
            max_tokens = int(body["max_tokens"])
        except (TypeError, ValueError):
            raise HTTPException(400, "max_tokens 必须为整数")
        if not 1 <= max_tokens <= 1_000_000:
            raise HTTPException(400, "max_tokens 超出允许范围")
        payload["max_tokens"] = max_tokens
    tools = body.get("tools")
    if tools:
        if not isinstance(tools, list):
            raise HTTPException(400, "tools 必须为数组")
        if len(tools) > 128:
            raise HTTPException(400, "tools 数量过多")
        if len(json.dumps(tools, ensure_ascii=False)) > 2_000_000:
            raise HTTPException(400, "tools 定义过长")
        for t in tools:
            if (not isinstance(t, dict) or t.get("type") != "function"
                    or not isinstance(t.get("function"), dict)
                    or not isinstance(t["function"].get("name"), str)
                    or not t["function"].get("name").strip()):
                raise HTTPException(400, "tools 定义格式错误")
        payload["tools"] = tools
        payload["tool_choice"] = body.get("tool_choice") or "auto"

    timeout = httpx.Timeout(600.0, connect=30.0)

    async def _gen():
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", target, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        # v8.14：只读前 600 字节即止——此前 aread() 先整读错误体再截断，
                        # 异常上游可用超大错误体压内存
                        chunks = []
                        got = 0
                        async for chunk in resp.aiter_bytes():
                            chunks.append(chunk)
                            got += len(chunk)
                            if got >= 600:
                                break
                        body_text = b"".join(chunks)[:600].decode("utf-8", errors="replace")
                        # v6.2 P2-5：脱敏后再回传，避免泄露内部 URL/Key/IP
                        yield f'event: error\ndata: {json.dumps({"status": resp.status_code, "body": _sanitize_error(body_text)}, ensure_ascii=False)}\n\n'
                        return
                    if payload["stream"]:
                        async for line in resp.aiter_lines():
                            if line:
                                yield line + "\n\n"
                    else:
                        data = (await resp.aread()).decode("utf-8", errors="replace")
                        yield f'data: {data}\n\n'
        except Exception as e:
            # v6.2 P2-5：异常信息脱敏，不回传原始连接细节
            yield f'event: error\ndata: {json.dumps({"message": _sanitize_error(str(e))}, ensure_ascii=False)}\n\n'

    return StreamingResponse(_gen(), media_type="text/event-stream")
