"""OpenAI 兼容 LLM 客户端：流式 chat/completions + 工具调用 + 非流式补全。

兼容 DeepSeek / Kimi(Moonshot) / 智谱 / OpenAI / Ollama(/v1) 等。
"""
import asyncio
import json
from typing import AsyncGenerator, List, Optional

import httpx

from .config import Config, get_config


class LLMError(Exception):
    pass


def estimate_tokens(text: str) -> int:
    """启发式 token 估算：中英文混合约 3 字符/token。"""
    if not text:
        return 0
    return max(1, len(text) // 3)


def _headers(cfg: Config) -> dict:
    return {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }


def _url(cfg: Config) -> str:
    return cfg.api_base_url.rstrip("/") + "/chat/completions"


def _base_payload(cfg: Config, messages, tools, tool_choice, temperature, max_tokens, stream) -> dict:
    payload = {
        "model": cfg.model,
        "messages": messages,
        "stream": stream,
        "temperature": temperature if temperature is not None else cfg.temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    elif cfg.max_tokens:
        payload["max_tokens"] = cfg.max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"
    return payload


async def stream_chat(
    cfg: Config,
    messages: list,
    tools: Optional[list] = None,
    tool_choice: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> AsyncGenerator[dict, None]:
    """流式对话。逐条 yield 事件：
    {"type":"delta","content":str} / {"type":"tool_call_delta","index":int,"id":str|None,
    "name":str|None,"arguments":str} / {"type":"done","finish_reason":str,"usage":dict|None}
    """
    if not cfg.api_key:
        raise LLMError("未配置 API Key，请在设置中填写模型服务地址与密钥。")
    payload = _base_payload(cfg, messages, tools, tool_choice, temperature, max_tokens, stream=True)
    timeout = httpx.Timeout(600.0, connect=30.0)
    _pending_usage: Optional[dict] = None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", _url(cfg), json=payload, headers=_headers(cfg)) as resp:
                if resp.status_code != 200:
                    # v8.14b：只读前 600 字节即止（对齐 webui proxy.py）——异常上游超大错误体不再整读入内存
                    chunks = []
                    got = 0
                    async for chunk in resp.aiter_bytes():
                        chunks.append(chunk)
                        got += len(chunk)
                        if got >= 600:
                            break
                    body = b"".join(chunks)[:600].decode("utf-8", errors="replace")
                    raise LLMError(f"模型服务返回 HTTP {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        # v8.15 检修：部分兼容厂商（智谱 GLM 流式末包）usage 随空 choices 下发，
                        # 直接 continue 会丢 token 统计——缓存住，finish 事件兜底用
                        if chunk.get("usage"):
                            _pending_usage = chunk.get("usage")
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield {"type": "delta", "content": content}
                    for tc in delta.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        yield {
                            "type": "tool_call_delta",
                            "index": tc.get("index", 0),
                            "id": tc.get("id"),
                            "name": fn.get("name"),
                            "arguments": fn.get("arguments"),
                        }
                    fr = choice.get("finish_reason")
                    if fr:
                        yield {"type": "done", "finish_reason": fr,
                               "usage": chunk.get("usage") or _pending_usage}
    except httpx.HTTPError as e:
        # 网络层异常统一转友好提示（原始 traceback 由调用方 log_error 写 Err.log）
        raise LLMError(
            "无法连接模型服务（网络不可达或服务未启动），请检查网络、API 地址与端口后重试。"
        ) from e


async def chat_complete(
    cfg: Config,
    messages: list,
    tools: Optional[list] = None,
    tool_choice: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: float = 180.0,
) -> dict:
    """非流式补全，返回 OpenAI 消息对象 {role, content, tool_calls?}。"""
    if not cfg.api_key:
        raise LLMError("未配置 API Key，请在设置中填写模型服务地址与密钥。")
    payload = _base_payload(cfg, messages, tools, tool_choice, temperature, max_tokens, stream=False)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0)) as client:
            resp = await client.post(_url(cfg), json=payload, headers=_headers(cfg))
    except httpx.HTTPError as e:
        raise LLMError(
            "无法连接模型服务（网络不可达或服务未启动），请检查网络、API 地址与端口后重试。"
        ) from e
    if resp.status_code != 200:
        body = resp.text[:600]
        raise LLMError(f"模型服务返回 HTTP {resp.status_code}: {body}")
    try:
        data = resp.json()
    except Exception as e:
        raise LLMError("模型服务返回内容不是合法 JSON。") from e
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("模型服务返回空 choices")
    return choices[0].get("message") or {}


async def embed_texts(model, texts: List[str], timeout: float = 30.0) -> List[List[float]]:
    """v8.1：OpenAI 兼容 /embeddings 端点（异步版）。

    供未来异步调用点使用；同步路径走 matcher.api_embed（含缓存/分批/降级）。
    model 为注册表 kind=embedding 条目（url/api_key 为空时回退全局）。
    """
    cfg = get_config()
    base = (model.url or cfg.api_base_url or "").rstrip("/")
    key = (model.api_key or cfg.api_key or "").strip()
    if not base or not key:
        raise LLMError("Embedding 模型未配置 url/api_key，请在设置中选择 Embedding 条目。")
    payload = {"model": model.id, "input": list(texts)}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
            resp = await client.post(base + "/embeddings", json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise LLMError(
            "无法连接 Embedding 服务（网络不可达或服务未启动），请检查网络与地址后重试。"
        ) from e
    if resp.status_code != 200:
        body = resp.text[:400]
        raise LLMError(f"Embedding 服务返回 HTTP {resp.status_code}: {body}")
    try:
        data = resp.json()
    except Exception as e:
        raise LLMError("Embedding 服务返回内容不是合法 JSON。") from e
    items = data.get("data") or []
    return [d["embedding"] for d in items]


def extract_json(content: str):
    """从 LLM 输出中稳健地提取 JSON（容忍 ```json 包裹或前后文本）。"""
    if not content:
        raise ValueError("空内容无法解析 JSON")
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    # 找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"未找到 JSON 对象: {content[:200]}")
    return json.loads(text[start : end + 1])


async def test_connection(cfg: Config) -> str:
    """测试 API 连通性，返回友好提示。"""
    msgs = [{"role": "user", "content": "请只回复两个字：正常"}]
    msg = await chat_complete(cfg, msgs, max_tokens=8, timeout=30.0)
    return (msg.get("content") or "").strip() or "连接成功"
