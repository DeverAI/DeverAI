"""上下文守门专家（v4）：分块 → 每轮轻量模型守门（保留/永久禁用）→ 规则守护。

- 分块：history 按 role==user 切块，一个块 = 一次用户发起的完整对话段
  （user + 后续 assistant/tool 消息）。块 uid = 用户消息 md5 前 16 位，用于持久化禁用。
- 守门（pre-round）：调用 expert_model（为空则用主模型）输出严格 JSON：
    {"keep": N 从最新往回保留的块数, "ban": [uid...], "reason": "..."}
  未保留的古早块不进本轮调用（交由压缩摘要），ban 的块写入 data/ctx_bans.json
  永久生效（除非用户主动引用回退）。守门失败绝不阻塞主对话。
- 规则守护（post-round）：检查对话 AI 最后一轮是否遵守规则、回复正常合理
  未绕过限制，输出 {"ok": bool, "note": str}，由 UI 渲染守护卡片。
"""
import asyncio
import hashlib
from typing import List, Optional, Tuple

from .config import DATA_DIR, Config
from .errors import log_error
from .llm import chat_complete, extract_json
from .storage import load_json, save_json

BANS_PATH = DATA_DIR / "ctx_bans.json"
MAX_BANS = 500

GATE_SYSTEM = (
    "你是上下文守门专家（轻量审查员）。下面是按时间排列的对话块摘要（旧→新）。\n"
    "任务：为下一轮对话决定上下文保留策略。\n"
    "1. keep：从【最新】往回数需要保留的块数（整数；近期相关讨论必须保留；"
    "与当前任务无关的古早块可以不保留）。\n"
    "2. ban：应【永久禁止自动引用】的块 uid 列表（仅限明显古早、无用、与任何"
    "后续工作无关的块；宁缺毋滥）。\n"
    "3. reason：一句话说明。\n"
    '只输出严格 JSON：{"keep": 3, "ban": ["uid1"], "reason": "..."}'
)

GUARD_SYSTEM = (
    "你是 AI 规则守护员。检查下面这一轮对话中【助手】的表现：\n"
    "1. 是否遵守了系统规则与安全限制（不越权、不绕过审批/工作区限制）；\n"
    "2. 回复是否正常、合理、完整，没有答非所问或被诱导绕过限制。\n"
    '只输出严格 JSON：{"ok": true/false, "note": "一句话结论"}'
)


# ---------------------------------------------------------------------------
# 分块
# ---------------------------------------------------------------------------
def block_uid(user_content: str) -> str:
    return hashlib.md5(str(user_content or "").encode("utf-8")).hexdigest()[:16]


def split_blocks(history: List[dict]) -> List[dict]:
    """把消息列表切成块：每个 role==user 消息开启一个新块。

    返回 [{"uid": str, "start": int, "end": int, "user": str, "msgs": [...]}]
    开头若存在非 user 消息（如历史摘要 system），归入 uid 为 "" 的前置块。
    """
    blocks: List[dict] = []
    cur: Optional[dict] = None
    for i, m in enumerate(history):
        if m.get("role") == "user":
            if cur is not None:
                cur["end"] = i
                blocks.append(cur)
            content = m.get("content") or ""
            cur = {
                "uid": block_uid(content),
                "start": i,
                "end": len(history),
                "user": str(content),
                "msgs": [m],
            }
        else:
            if cur is None:
                cur = {"uid": "", "start": i, "end": len(history), "user": "", "msgs": []}
            cur["msgs"].append(m)
    if cur is not None:
        cur["end"] = len(history)
        blocks.append(cur)
    return blocks


# ---------------------------------------------------------------------------
# 永久禁用库
# ---------------------------------------------------------------------------
def load_bans() -> List[dict]:
    db = load_json(BANS_PATH, [])
    return db if isinstance(db, list) else []


def save_bans(db: List[dict]) -> None:
    save_json(BANS_PATH, db[-MAX_BANS:])


def is_banned(uid: str) -> bool:
    if not uid:
        return False
    return any(b.get("uid") == uid for b in load_bans())


def ban_block(uid: str, preview: str = "", by: str = "user") -> None:
    """永久禁止某块被自动引用（用户按钮或守门专家判定）。"""
    if not uid:
        return
    import datetime
    db = load_bans()
    if any(b.get("uid") == uid for b in db):
        return
    db.append({
        "uid": uid,
        "preview": str(preview)[:120],
        "by": by,
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    save_bans(db)


def unban_block(uid: str) -> None:
    db = [b for b in load_bans() if b.get("uid") != uid]
    save_bans(db)


# ---------------------------------------------------------------------------
# 装配本轮上下文：ban 过滤 + 引用强制注入 + 守门保留
# ---------------------------------------------------------------------------
def assemble_history(
    history: List[dict],
    keep_recent_blocks: Optional[int] = None,
) -> Tuple[List[dict], dict]:
    """按 ban 库过滤历史块，返回 (过滤后的消息列表, 统计信息)。

    keep_recent_blocks: 守门专家给出的保留块数（None 表示未经守门，全保留）。
    被 ban 的块永远排除；未被保留的古早块同样排除（不进入本轮调用）。
    """
    blocks = split_blocks(history)
    bans = {b.get("uid") for b in load_bans()}
    stats = {"total": len(blocks), "kept": 0, "banned": 0, "dropped": 0}
    if not blocks:
        return list(history), stats
    named = [b for b in blocks if b["uid"]]
    # v8.5.x 审查修复：保留窗口基于"未 ban 的命名块"计算，否则 ban 块挤占名额，
    # 导致实际保留块数少于 keep_recent_blocks（上下文被过度裁剪）。
    keepable = [b for b in named if b["uid"] not in bans]
    if keep_recent_blocks is None:
        keep_recent_blocks = len(keepable)
    keep_recent_blocks = max(0, min(keep_recent_blocks, len(keepable)))
    keep_from = len(keepable) - keep_recent_blocks
    out: List[dict] = []
    named_idx = 0  # 仅对未 ban 的命名块计数，避免前置无名块导致 off-by-one
    for b in blocks:
        if b["uid"]:
            if b["uid"] in bans:
                stats["banned"] += 1
                continue
            if named_idx < keep_from:
                stats["dropped"] += 1
                named_idx += 1
                continue
            named_idx += 1
        stats["kept"] += 1
        out.extend(b["msgs"])
    return out, stats


# ---------------------------------------------------------------------------
# 守门专家（pre-round）
# ---------------------------------------------------------------------------
def _expert_cfg(cfg: Config) -> Config:
    """守门用轻量配置：expert_model 独立；为空则用主模型。"""
    c = Config()
    c.api_base_url = cfg.api_base_url
    c.api_key = cfg.api_key
    c.model = (cfg.expert_model or cfg.model or "").strip()
    c.temperature = 0.0
    c.max_tokens = 400
    return c


def _blocks_digest(blocks: List[dict], max_chars: int = 150) -> str:
    lines = []
    for b in blocks:
        if not b["uid"]:
            continue
        lines.append(f"- uid={b['uid']} | {b['user'][:max_chars]}")
    return "\n".join(lines)


async def gate_history(cfg: Config, history: List[dict], user_message: str) -> Tuple[Optional[int], List[str], str]:
    """守门：返回 (keep 块数|None=不限制, 建议 ban 的 uid 列表, reason)。失败返回 (None, [], "")。"""
    blocks = [b for b in split_blocks(history) if b["uid"]]
    if len(blocks) < 3 or not cfg.api_key:
        return None, [], ""
    digest = _blocks_digest(blocks)
    try:
        msg = await asyncio.wait_for(
            chat_complete(
                _expert_cfg(cfg),
                [
                    {"role": "system", "content": GATE_SYSTEM},
                    {"role": "user", "content":
                        f"对话块（旧→新，共 {len(blocks)} 块）：\n{digest}\n\n"
                        f"用户即将发起的新消息：{str(user_message)[:300]}"},
                ],
                timeout=60.0,
            ),
            timeout=90.0,
        )
        try:
            data = extract_json(msg.get("content") or "")
            if not isinstance(data, dict):
                # v8.5.x 审查修复：合法 JSON 但非对象（null/数组/字符串）时降级，
                # 而非随后 data.get 抛 AttributeError 击穿整轮 run
                return None, [], ""
        except ValueError:
            # v6.1：轻量模型偏题返回纯文本（如压缩摘要）不属异常，
            # 静默降级为不限制，避免反复写 Err.log
            return None, [], ""
    except Exception as e:
        log_error("上下文守门失败（不阻塞主流程）", e)
        return None, [], ""
    keep = data.get("keep")
    try:
        keep = int(keep) if keep is not None else None
    except (TypeError, ValueError):
        keep = None
    if keep is not None:
        keep = max(1, min(keep, len(blocks)))
    ban = data.get("ban") or []
    valid_uids = {b["uid"] for b in blocks}
    ban = [u for u in ban if isinstance(u, str) and u in valid_uids]
    return keep, ban, str(data.get("reason") or "")


# ---------------------------------------------------------------------------
# 规则守护（post-round）
# ---------------------------------------------------------------------------
async def guard_check(cfg: Config, user_message: str, assistant_reply: str) -> Optional[dict]:
    """检查对话 AI 本轮是否遵守规则。返回 {"ok": bool, "note": str} 或 None（跳过）。"""
    if not cfg.api_key or not str(assistant_reply or "").strip():
        return None
    try:
        msg = await asyncio.wait_for(
            chat_complete(
                _expert_cfg(cfg),
                [
                    {"role": "system", "content": GUARD_SYSTEM},
                    {"role": "user", "content":
                        f"【用户】{str(user_message)[:800]}\n\n【助手】{str(assistant_reply)[:1500]}"},
                ],
                timeout=60.0,
            ),
            timeout=90.0,
        )
        data = extract_json(msg.get("content") or "")
        if not isinstance(data, dict):
            return None  # v8.5.x 审查修复：非对象 JSON 降级，避免 data.get 抛 AttributeError
    except Exception:
        return None  # 守护失败静默跳过，绝不影响主对话
    return {"ok": bool(data.get("ok", True)), "note": str(data.get("note") or "")}
