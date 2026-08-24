"""DeverAI Lite Server — 超轻量后端（网页远控专用）。

设计目标：
1. 极简：只保留鉴权 + LLM 代理 + 文件桥 + 命令桥，无多余路由。
2. 低内存：无全局缓存累积，SSE 连接断开即清理，子进程超时即 kill。
3. 无泄漏：所有 async generator 有 finally 清理；子进程管道显式关闭。

复用 app.auth / app.config / app.storage（它们本身无状态、无内存泄漏风险）。
不包含：邮箱注册、项目下载、资产银行、审计日志等重路由。
"""
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .config import DATA_DIR, get_config, init_config
from .security import is_dangerous_cmd
from .storage import save_text

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"  # lite/static/

app = FastAPI(title="DeverAI Lite", version="1.0.0")

# 静态文件（仅挂载 static/，不挂载其他目录）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static-lite")


# v8.13：未处理异常统一落根目录 Err.log 并回泛化 500（与完整版 server.py 对齐，
# 防止 Lite 端裸堆栈/路径泄露）
from fastapi.responses import JSONResponse as _JSONResponse  # noqa: E402


@app.exception_handler(Exception)
async def _unhandled_exc(request: Request, exc: Exception):
    try:
        from .errors import log_error
        log_error(f"[lite] {request.method} {request.url.path}", exc)
    except Exception:
        pass
    return _JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


def _client_ip(request: Request) -> str:
    """获取客户端 IP（只信任本机反代场景下的 XFF，与完整版 server.py 同规则）。"""
    direct = request.client.host if request.client else "unknown"
    if direct in ("127.0.0.1", "::1", "localhost"):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return direct


_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".idea", ".vscode", "dist", "build"}
_MAX_WRITE = 5 * 1024 * 1024  # 5MB
_MAX_TREE = 2000

# v8.13：与 desktop/tools.py 同源的系统目录保护（工作区=项目根时）；
# v8.13.1 只读保护收窄：data/backups 与密钥文件禁止读，源码目录允许查看。
_APP_ROOT = Path(__file__).resolve().parent.parent.parent  # project root (DeverAI/)
_PROTECTED_NAMES = {"pyqt", "webui", "lite", "data", "backups", "dev_log", "updates", "tests", "tools"}
_PROTECTED_FILES = {"Err.log", "config.json"}
_READ_PROTECTED_NAMES = {"data", "backups"}
_READ_PROTECTED_FILES = {"Err.log", "config.json"}


def _protected_parts(p: Path, root: Path):
    try:
        root = root.resolve()
        p = p.resolve()
        if root != _APP_ROOT.resolve():
            return None
        rel = p.relative_to(root)
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if not parts:
        return None
    return parts, rel.name


def _is_protected(p: Path, root: Path) -> bool:
    info = _protected_parts(p, root)
    if info is None:
        return False
    parts, name = info
    return bool(parts and (parts[0] in _PROTECTED_NAMES or name in _PROTECTED_FILES))


def _is_read_protected(p: Path, root: Path) -> bool:
    info = _protected_parts(p, root)
    if info is None:
        return False
    parts, name = info
    return bool(parts and (parts[0] in _READ_PROTECTED_NAMES or name in _READ_PROTECTED_FILES))


@app.on_event("startup")
async def _startup():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_config()


# ------------------------------------------------------------------ #
# 页面
# ------------------------------------------------------------------ #
@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "lite.html"))


@app.get("/api/server/info")
async def server_info():
    cfg = get_config()
    # v8.12：补 allow_register/bridge_authorized 字段（lite.html 据此驱动注册按钮与工作区提示）
    return {
        "name": "DeverAI Lite", "version": "1.0.0",
        "has_users": _user_count() > 0,
        "allow_register": bool(cfg.allow_register),
        "bridge_authorized": bool(cfg.bridge_workspace),
    }


# ------------------------------------------------------------------ #
# 鉴权（精简：仅用户名+密码，无邮箱注册）
# ------------------------------------------------------------------ #
def _user_count() -> int:
    from .users import user_count
    return user_count()


from pydantic import BaseModel as _BM  # noqa: E402


class _AuthBody(_BM):
    username: str = ""
    password: str = ""


@app.post("/api/auth/register")
async def register(body: _AuthBody, response: Response, request: Request):
    cfg = get_config()
    if not cfg.allow_register:
        raise HTTPException(403, "注册已关闭")
    # v8.13：Lite 鉴权与完整版同源限速（防开放注册被爆破/批量建号）
    from .security import check_login_rate, is_ip_suspicious
    ip = _client_ip(request)
    if is_ip_suspicious(ip):
        raise HTTPException(403, "请求过于频繁，请稍后再试")
    if not check_login_rate(f"reg:{ip}"):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    from .users import create_user
    try:
        await asyncio.to_thread(create_user, body.username.strip(), body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.set_session(response, body.username.strip(), cfg.secret, cfg.session_ttl_hours, request)
    return {"ok": True, "username": body.username.strip()}


@app.post("/api/auth/login")
async def login(body: _AuthBody, response: Response, request: Request):
    cfg = get_config()
    # v8.13：登录限速 + 异常 IP 检测（与完整版 server.py 同源）
    from .security import check_login_rate, is_ip_suspicious, record_login_failure
    ip = _client_ip(request)
    if is_ip_suspicious(ip):
        raise HTTPException(403, "请求过于频繁，请稍后再试")
    if not check_login_rate(f"login:{ip}"):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    from .users import verify_user
    u = await asyncio.to_thread(verify_user, body.username.strip(), body.password)
    if u is None:
        record_login_failure(ip, body.username)
        raise HTTPException(401, "用户名或密码错误")
    auth.set_session(response, u["username"], cfg.secret, cfg.session_ttl_hours, request)
    return {"ok": True, "username": u["username"]}


@app.post("/api/auth/logout")
async def logout(response: Response):
    auth.clear_session(response)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user: dict = Depends(auth.current_user)):
    return {"ok": True, "username": user["username"]}


# ------------------------------------------------------------------ #
# LLM 代理（复用 app.proxy 的 SSRF 防护逻辑，内联以减少路由表）
# ------------------------------------------------------------------ #
import ipaddress
import socket
from urllib.parse import urlparse
import re

_FORBIDDEN = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}
_SENSITIVE_RE = re.compile(r"(sk-[A-Za-z0-9]{6,})|(https?://[^\s\"']{8,})|(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", re.I)


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
                # v8.13：前导零按八进制解析（0177.0.0.1 → 127.0.0.1）
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
    """IPv4-mapped IPv6（::ffff:127.0.0.1）显式解包为 IPv4 再判定。

    部分旧 Python 版本 is_loopback/is_private 不展开 ipv4_mapped，会漏判。
    """
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        return a.ipv4_mapped
    return a


def _is_loopback(host: str, infos=None) -> bool:
    host = (host or "").lower().strip()
    if host in _FORBIDDEN or host.endswith(".localhost"):
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


def _is_private(host: str, infos=None) -> bool:
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


def _to_bool(v) -> bool:
    """宽松布尔解析：True/False、"true"/"false"/"1"/"0"；无法解析默认 False。"""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


async def _validate_base(base: str, request: Request) -> str:
    # 非字符串 base_url（dict/list 等）先强制转字符串，避免 .strip() AttributeError → 500
    base = str(base or "").strip()
    if not base or len(base) > 500:
        raise HTTPException(400, "模型服务地址无效")
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
    # v8.13：数值形态 host（127.1、0x7f000001、2130706433）先归一化，归一化成功即 IP
    # 字面量并跳过 DNS——此前 Windows 上 getaddrinfo("127.1") 直接失败，误报无法解析。
    norm = _normalize_numeric_ip(host)
    if norm:
        host = norm
    # v8.12：getaddrinfo 是阻塞调用——非 IP 字面量域名在后台线程解析，防阻塞事件循环
    infos = None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except OSError:
            # P2-6：解析失败直接拒绝，不再让 _is_private/_is_loopback 二次同步解析（阻塞）
            raise HTTPException(400, "api_base 域名无法解析")
        if not infos:
            raise HTTPException(400, "api_base 域名无法解析")
    if _is_private(host, infos):
        raise HTTPException(400, "不允许将代理指向内网地址")
    cfg = get_config()
    if _is_loopback(host, infos) and not cfg.llm_allow_loopback:
        raise HTTPException(400, "不允许将代理指向本机地址")
    # 自身端口：优先取本请求实际到达端口（reload 等场景也准确），回落内存注入/配置端口
    from .config import self_port
    own = request.url.port or self_port()
    if _is_loopback(host, infos) and port == own:
        raise HTTPException(400, "不允许将代理指向本服务自身")
    return base.rstrip("/")


def _sanitize(text: str) -> str:
    return _SENSITIVE_RE.sub("[REDACTED]", text)[:400]


@app.post("/api/llm/chat")
async def llm_chat(request: Request, user: dict = Depends(auth.current_user)):
    # v8.13：请求体前置上限，超大 JSON 不进入解析
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
    base = await _validate_base(body.get("base_url"), request)
    api_key = str(body.get("api_key") or "")
    model = str(body.get("model") or "").strip()
    messages = body.get("messages")
    if not api_key:
        raise HTTPException(400, "缺少 API Key")
    if len(api_key) > 4096:
        raise HTTPException(400, "API Key 过长")
    if not model:
        raise HTTPException(400, "缺少模型名称")
    if len(model) > 200:
        raise HTTPException(400, "模型名称过长")
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
    # 消息体总长上限（对齐 proxy.py，防无限制透传撑爆上游/本服务内存）
    if len(json.dumps(messages, ensure_ascii=False)) > 2_000_000:
        raise HTTPException(400, "对话消息过长")

    target = base + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "stream": _to_bool(body.get("stream"))}
    if body.get("temperature") is not None:
        try:
            temperature = float(body["temperature"])
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
        client = None
        try:
            client = httpx.AsyncClient(timeout=timeout)
            async with client.stream("POST", target, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    raw = (await resp.aread()).decode("utf-8", errors="replace")[:600]
                    yield f'event: error\ndata: {json.dumps({"status": resp.status_code, "body": _sanitize(raw)}, ensure_ascii=False)}\n\n'
                    return
                if payload["stream"]:
                    async for line in resp.aiter_lines():
                        if line:
                            yield line + "\n\n"
                else:
                    data = (await resp.aread()).decode("utf-8", errors="replace")
                    yield f'data: {data}\n\n'
        except Exception as e:
            yield f'event: error\ndata: {json.dumps({"message": _sanitize(str(e))}, ensure_ascii=False)}\n\n'
        finally:
            if client:
                await client.aclose()

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ------------------------------------------------------------------ #
# 文件桥（精简：tree/read/write/grep + pick/workspace，无 delete/rename/mkdir）
# ------------------------------------------------------------------ #
def _workspace() -> Path:
    cfg = get_config()
    ws = (cfg.bridge_workspace or "").strip()
    if not ws:
        raise HTTPException(409, "尚未授权工作区")
    p = Path(ws).resolve()
    if not p.is_dir():
        raise HTTPException(409, "工作区目录不存在")
    return p


def _resolve(rel: str) -> Path:
    root = _workspace()
    raw = str(rel or ".").strip()
    if len(raw) > 1000:
        raise HTTPException(400, "路径过长")
    p = (root / raw).resolve()
    if p != root and root not in p.parents:
        raise HTTPException(403, "路径越界")
    return p


@app.get("/api/bridge/workspace")
async def get_workspace(user: dict = Depends(auth.current_user)):
    cfg = get_config()
    from .codename import workspace_codename
    return {"authorized": bool(cfg.bridge_workspace), "workspace": workspace_codename() if cfg.bridge_workspace else ""}


@app.post("/api/bridge/workspace")
async def set_workspace(body: dict, user: dict = Depends(auth.current_user)):
    path = str(body.get("path") or "").strip()
    if not path:
        raise HTTPException(400, "路径不能为空")
    if len(path) > 1000:
        raise HTTPException(400, "路径过长")
    p = Path(path).resolve()
    if not p.is_dir():
        raise HTTPException(400, "目录不存在")
    from .config import update_config
    update_config(bridge_workspace=str(p))
    from .codename import workspace_codename
    return {"ok": True, "workspace": workspace_codename()}


@app.get("/api/bridge/pick")
async def pick_workspace(user: dict = Depends(auth.current_user)):
    try:
        import tkinter as tk
        from tkinter import filedialog

        def _ask():
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory()
            root.destroy()
            return path

        path = await asyncio.to_thread(_ask)
        if path:
            p = Path(path).resolve()
            from .config import update_config
            update_config(bridge_workspace=str(p))
            from .codename import workspace_codename
            return {"ok": True, "workspace": workspace_codename()}
        return {"ok": False, "message": "未选择文件夹"}
    except Exception:
        raise HTTPException(500, "无法弹出系统对话框")


@app.get("/api/bridge/fs/tree")
async def fs_tree(path: str = "", user: dict = Depends(auth.current_user)):
    p = _resolve(path)
    if _is_read_protected(p, _workspace()):
        raise HTTPException(403, "该目录属于受保护数据，禁止列出")

    def _scan() -> dict:
        if not p.is_dir():
            raise HTTPException(404, "不是目录")
        entries = []
        try:
            items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            raise HTTPException(403, "无权限访问该目录")
        except OSError:
            raise HTTPException(500, "读取目录失败")
        root = _workspace()
        items = [it for it in items
                 if it.name not in _SKIP_DIRS and not _is_read_protected(it, root)]
        truncated = False
        if len(items) > _MAX_TREE:
            items = items[:_MAX_TREE]
            truncated = True
        for it in items:
            try:
                st = it.stat()
            except OSError:
                st = None
            try:
                is_dir = it.is_dir()
            except OSError:
                is_dir = False
            entries.append({"name": it.name, "is_dir": is_dir, "size": st.st_size if st else 0})
        return {"ok": True, "children": entries, "truncated": truncated}

    # v8.14：iterdir/stat 是同步磁盘 IO，移入线程池防阻塞事件循环（对齐 grep 先例）
    return await asyncio.to_thread(_scan)


@app.get("/api/bridge/fs/read")
async def fs_read(path: str = "", user: dict = Depends(auth.current_user)):
    p = _resolve(path)
    # v8.13.1：读只受数据/密钥文件保护，源码目录允许 Agent 查看
    if _is_read_protected(p, _workspace()):
        raise HTTPException(403, "该文件属于受保护数据，禁止读取")

    def _read() -> dict:
        try:
            if not p.is_file():
                raise HTTPException(404, "文件不存在")
            size = p.stat().st_size
        except OSError:
            raise HTTPException(404, "文件不存在或不可访问")  # v8.12：stat/is_file 异常保护
        if size > 2 * 1024 * 1024:
            raise HTTPException(413, "文件超过 2MB")
        # 二进制检测（对齐 bridge.py P2-13）：含 NUL 字节明确 415，而非返回 U+FFFD 乱码
        # v8.14：只读前 8KB 探测 NUL，不再为取头部整读全文件
        try:
            with open(p, "rb") as f:
                head = f.read(8192)
            if b"\x00" in head:
                raise HTTPException(415, "二进制文件，不支持文本读取")
            content = p.read_text(encoding="utf-8", errors="replace")
        except HTTPException:
            raise
        except OSError:
            raise HTTPException(500, "读取文件失败")
        return {"ok": True, "content": content, "lines": len(content.splitlines())}

    # v8.14：同步磁盘 IO 移入线程池
    return await asyncio.to_thread(_read)


@app.post("/api/bridge/fs/write")
async def fs_write(body: dict, user: dict = Depends(auth.current_user)):
    rel = str(body.get("path") or "").strip()
    if not rel or rel in (".", "\\", "/"):
        raise HTTPException(400, "path 不能为空或指向工作区根目录")
    p = _resolve(rel)
    if _is_protected(p, _workspace()):
        raise HTTPException(403, "该文件属于系统受保护文件，禁止写入")
    if p.is_dir():
        raise HTTPException(400, "path 指向目录，无法写入文件")
    content = str(body.get("content") or "")
    if len(content.encode("utf-8", errors="ignore")) > _MAX_WRITE:
        raise HTTPException(413, "文件内容超过 5MB 上限")

    def _write() -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        save_text(p, content)

    # v8.14：同步磁盘 IO 移入线程池
    await asyncio.to_thread(_write)
    return {"ok": True}


@app.get("/api/bridge/fs/grep")
async def fs_grep(pattern: str = "", path: str = "", glob: str = "",
                  ignore_case: bool = False, user: dict = Depends(auth.current_user)):
    """精简版 grep：在工作区内正则搜索。"""
    import re as _re
    if not pattern:
        raise HTTPException(400, "pattern 不能为空")
    if len(pattern) > 200:
        raise HTTPException(400, "pattern 过长（≤200 字符）")
    try:
        rx = _re.compile(pattern, _re.IGNORECASE if ignore_case else 0)
    except _re.error:
        raise HTTPException(400, "正则表达式无效")
    # v8.13：拒绝典型灾难性回溯形态（嵌套量词 + 交替型），与 bridge.py 一致
    if (_re.search(r"\([^()]*[+*][^()]*\)[+*]", pattern)
            or _re.search(r"\([^()]*(?:\|[^()]*)+\)[+*{]", pattern)
            or _re.search(r"\([^()]*[+*?][^()]*\)[+*{]", pattern)):
        raise HTTPException(400, "正则包含高风险重复结构，请改写")
    root = _workspace()
    search_root = _resolve(path) if path else root
    # v8.13：显式路径不存在时明确 404（此前 os.walk 对不存在目录静默返回空结果）
    if not search_root.exists():
        raise HTTPException(404, "搜索路径不存在")
    # v8.13.1：受保护数据不可 grep（防绕过 fs/read 抓取密钥）
    if _is_read_protected(search_root, root):
        raise HTTPException(403, "该路径属于受保护数据，禁止搜索")

    def _scan():
        results = []
        scanned = 0
        files = []
        if search_root.is_file():
            files = [search_root]
        else:
            for r, dirs, names in os.walk(search_root):
                dirs[:] = [d for d in dirs
                           if d not in _SKIP_DIRS and not _is_read_protected(Path(r) / d, root)]
                for name in names:
                    if glob and not Path(name).match(glob):
                        continue
                    fp = Path(r) / name
                    if _is_read_protected(fp, root):
                        continue
                    files.append(fp)
                    if len(files) > 10000:
                        break
                if len(files) > 10000:
                    break
        for f in files:
            try:
                head = f.read_bytes()[:8192]
                if b"\x00" in head:
                    continue
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for ln, line in enumerate(fh, 1):
                        scanned += 1
                        if scanned > 200000:
                            break
                        if rx.search(line.rstrip("\n")[:5000]):
                            relpath = str(f.relative_to(root)).replace("\\", "/")
                            results.append({"file": relpath, "line": ln, "text": line.rstrip()[:300]})
                            if len(results) >= 200:
                                return results, scanned
            except OSError:
                continue
            if scanned > 200000:
                break
        return results, scanned

    try:
        results, scanned = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(None, _scan), timeout=30)
    except asyncio.TimeoutError:
        # 超时兜底（对齐 bridge.py）：工作线程受行数上限约束，请求侧不再等待
        raise HTTPException(408, "搜索超时（正则过于复杂或文件过多）")
    except (OSError, ValueError):
        raise HTTPException(500, "搜索执行失败")
    return {"ok": True, "matches": results, "count": len(results), "scanned_lines": scanned}


# ------------------------------------------------------------------ #
# 命令桥（SSE 流式，有资源清理保证）
# ------------------------------------------------------------------ #
@app.post("/api/bridge/run_command")
async def run_command(body: dict, user: dict = Depends(auth.current_user)):
    cmd = str(body.get("command") or "").strip()
    if not cmd:
        raise HTTPException(400, "命令为空")
    # v8.13：命令长度上限（OS 亦有上限，此处给出明确 4xx 而非启动失败流）
    if len(cmd) > 32000:
        raise HTTPException(400, "命令过长")
    # 后端危险命令拦截：命中危险模式且未带 danger_ok 显式确认 → 拒绝
    #（lite.html 危险命令 confirm 通过后置 danger_ok=true；裸调 API 的破坏性命令被拦截）
    if is_dangerous_cmd(cmd) and not _to_bool(body.get("danger_ok")):
        raise HTTPException(403, "危险命令需在界面确认后执行")
    cwd = _workspace()
    rel = str(body.get("cwd") or "")
    if rel:
        cwd = _resolve(rel)
        if not cwd.is_dir():
            raise HTTPException(400, "cwd 不是目录")
    try:
        timeout = float(body.get("timeout") or 120)
        if timeout != timeout:  # NaN 防护：min/max 对 NaN 恒 False 会漏网
            timeout = 120
        timeout = min(max(timeout, 1), 600)
    except (TypeError, ValueError):
        timeout = 120

    async def _kill_tree() -> None:
        """v8.14：Windows 下 proc.kill() 只杀 shell 本体，孙进程（cmd /c start ...）残留；
        改用 taskkill /T /F 杀整棵进程树，失败回退 proc.kill()。"""
        if proc is None or proc.returncode is not None:
            return
        if os.name == "nt" and proc.pid:
            try:
                k = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(proc.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    await asyncio.wait_for(k.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
            except Exception:
                pass
        try:
            if proc.returncode is None:
                proc.kill()
        except ProcessLookupError:
            pass

    async def _gen():
        proc = None
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            # v8.14：limit 提升到 1MB——StreamReader 默认 64KB 行上限会让
            # 单行超长输出触发 ValueError 断流（对齐 webui bridge.py）
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL, cwd=str(cwd), creationflags=creationflags,
                limit=1024 * 1024,
            )
            deadline = time.monotonic() + timeout
            timed_out = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), remaining)
                except asyncio.TimeoutError:
                    timed_out = True
                    break
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                yield f'data: {json.dumps({"line": text}, ensure_ascii=False)}\n\n'
            if timed_out:
                await _kill_tree()
            try:
                rc = await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                await _kill_tree()
                try:
                    rc = await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    rc = -1
                timed_out = True
            yield f'data: {json.dumps({"done": True, "rc": rc, "timed_out": timed_out}, ensure_ascii=False)}\n\n'
        except asyncio.CancelledError:
            # v8.13：客户端断开走取消语义——清理后重抛，不在取消路径中 yield
            await _kill_tree()
            try:
                if proc:
                    await asyncio.wait_for(proc.wait(), timeout=10)
            except BaseException:
                pass
            raise
        except Exception:
            await _kill_tree()
            try:
                if proc:
                    await asyncio.wait_for(proc.wait(), timeout=10)
            except BaseException:
                pass
            yield f'data: {json.dumps({"done": True, "rc": -1, "error": "执行异常"}, ensure_ascii=False)}\n\n'
        finally:
            # 显式关闭读端管道，防客户端断开/超时路径下 fd 泄漏（对齐 bridge.py）
            if proc is not None and proc.stdout is not None:
                try:
                    proc.stdout.close()
                except Exception:
                    pass

    return StreamingResponse(_gen(), media_type="text/event-stream")
