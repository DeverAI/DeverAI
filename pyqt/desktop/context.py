"""上下文管理：子Agent 压缩历史，防止主上下文膨胀。

压缩策略：当历史估算 token 超过阈值时，将【安全边界之前】的旧消息交给一次
独立（全新上下文）LLM 调用压缩为摘要，替换为一条 system 摘要消息。
安全边界 = 最后一条发起工具调用的 assistant 消息，保证 tool 结果链完整，
避免违反 OpenAI 协议（tool 消息必须跟在对应 assistant tool_calls 之后）。
"""
import asyncio
from typing import Awaitable, Callable, List, Tuple

from .config import Config
from .llm import chat_complete, estimate_tokens

EmitType = Callable[[dict], Awaitable[None]]

COMPRESS_SUMMARY_SYSTEM = (
    "你是上下文压缩专员（子Agent，全新上下文）。请把下面的历史对话压缩成一份"
    "紧凑但信息完整的摘要。必须保留：用户的目标、已完成的工具操作与关键结果、"
    "关键结论、当前进度、尚未完成的事项。不要遗漏重要细节，也不要添加新内容。"
    "只输出摘要正文，不要任何前缀。"
)


def safe_compress_boundary(messages: List[dict], keep_recent: int) -> int:
    """返回应保留的起始下标（压缩 [0:idx)）。保证工具链完整。"""
    n = len(messages)
    last_tool_calls = -1
    for i, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            last_tool_calls = i
    if last_tool_calls == -1:
        return max(0, n - keep_recent)
    return max(0, last_tool_calls)


async def summarize_messages(cfg: Config, messages: List[dict]) -> str:
    """子Agent：全新上下文压缩历史。"""
    msgs = [{"role": "system", "content": COMPRESS_SUMMARY_SYSTEM}] + messages
    msg = await chat_complete(cfg, msgs, temperature=0.2, max_tokens=1600, timeout=180.0)
    return (msg.get("content") or "").strip()


def _msg_tokens(m: dict) -> int:
    """估算单条消息 token，含 tool_calls 参数（M18）。"""
    n = estimate_tokens(str(m.get("content") or ""))
    for tc in m.get("tool_calls") or []:
        n += estimate_tokens((tc.get("function") or {}).get("arguments") or "")
    return n


async def compress_history(
    cfg: Config, messages: List[dict], emit: EmitType = None
) -> Tuple[List[dict], bool]:
    """若历史超阈值则压缩。返回 (新消息列表, 是否发生压缩)。"""
    if not cfg.ENABLE_SUBAGENT:
        return messages, False
    threshold = cfg.compress_threshold_tokens
    if cfg.token_mode:  # Token 计费模式：激进压缩
        threshold = max(2000, threshold // 2)
    total = sum(_msg_tokens(m) for m in messages)
    if total <= threshold:
        return messages, False
    idx = safe_compress_boundary(messages, cfg.context_keep_recent)
    if idx < 2:
        return messages, False
    old, recent = messages[:idx], messages[idx:]
    try:
        summary = await asyncio.wait_for(
            summarize_messages(cfg, old), timeout=180.0
        )
    except Exception:
        return messages, False
    if not summary:
        return messages, False
    # 保留原始 system prompt（含安全约束），避免压缩后 AI 不受"只能写相对路径"等约束
    new_messages = []
    if messages and messages[0].get("role") == "system":
        new_messages.append(messages[0])
    new_messages.append({"role": "system", "content": "【历史上下文摘要】(由子Agent压缩生成)\n" + summary})
    new_messages.extend(recent)
    if emit is not None:
        try:
            await emit(
                {
                    "type": "compress",
                    "summary": summary,
                    "removed": len(old),
                    "kept": len(recent),
                }
            )
        except Exception:
            pass
    return new_messages, True
