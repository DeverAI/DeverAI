"""v8.3 建议系统（TRAE CUE 式）：文档驱动 → AI 疯狂建议 → 筛选展示。

- build_context(cfg)：读取 Design/Techniques/Fact/Future/FreqErr + 当前上下文，拼接提示
- generate_suggestions(cfg, context, llm_fn)：异步调用模型产出 JSON 建议列表
- filter_dedupe(items)：按类型聚合去重、按价值排序、限量展示
- 时机：用户消息发送完成时（用户无法发送消息的间隙）展示 AI 精选建议
  两端同步：桌面版（desktop/suggest.py + gui）与网页版（static/js）共用此逻辑，
  Lite 版不引入（轻服务器跑不动）

建议分三类：functional（功能性）/ technical（技术性）/ art（美术性）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

APP_DIR = Path(__file__).resolve().parent.parent
DOCS = ["Design.md", "Techniques.md", "Fact.md", "Future.md", "FreqErr.md", "AGENT.txt"]

SUGGEST_SYSTEM = (
    "你是资深产品/技术/视觉顾问。基于提供的项目文档与当前工作状态，站在刁钻角度"
    "疯狂提出改进建议。严格输出 JSON 数组，每项："
    '{"type":"functional|technical|art","title":"一句话标题（≤20字）",'
    '"detail":"具体做法（≤80字）","value":0-10}。'
    "要求：覆盖三类；避免已完全实现的功能；value 高=收益大。只输出 JSON。"
)


def build_context(cfg, workspace: str = "", current_input: str = "",
                  current_mode: str = "") -> str:
    """拼接文档要点 + 当前状态。文档各自截断防爆 token。"""
    chunks = [f"工作区: {workspace or cfg.workspace}", f"当前输入: {current_input[:200]}",
              f"当前模式: {current_mode}"]
    for name in DOCS:
        p = APP_DIR / name
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # 取标题行 + 正文前 2500 字
        lines = [ln for ln in text.splitlines() if ln.strip()][:80]
        body = "\n".join(lines)[:2500]
        chunks.append(f"=== {name} ===\n{body}")
    return "\n\n".join(chunks)[:12000]


def _suggest_value(item) -> float:
    """value 字段守卫：LLM/旧数据可能给非数字，按 0 排序不抛错。"""
    try:
        return float(item.get("value") or 0)
    except (TypeError, ValueError):
        return 0.0


def filter_dedupe(items: list, max_items: int = 6) -> list:
    """按标题去重、按 value 降序、限量；保证三类尽量都有。"""
    seen = set()
    out = []
    for it in sorted(items or [], key=lambda x: -_suggest_value(x)):
        title = str(it.get("title", "")).strip()
        if not title or title in seen:
            continue
        seen.add(title)
        it["type"] = str(it.get("type") or "functional")
        out.append(it)
    # 分类均衡：尽量保留每类至少 1 条
    by_type: dict = {}
    for it in out:
        by_type.setdefault(it["type"], []).append(it)
    final = []
    for t in ("functional", "technical", "art"):
        final.extend(by_type.get(t, [])[:1])
    for it in out:
        if it not in final:
            final.append(it)
        if len(final) >= max_items:
            break
    return final[:max_items]


async def generate_suggestions(cfg, context: str, llm_fn: Optional[Callable] = None,
                               model_id: str = "") -> list:
    """异步生成建议。llm_fn(cfg, messages, max_tokens) -> str 可注入（测试用）。"""
    if not getattr(cfg, "ENABLE_SUGGEST", True):
        return []
    from .llm import chat_complete
    from .models import get_llm_cfg
    llm_cfg = get_llm_cfg(cfg, model_id) if model_id else cfg
    messages = [{"role": "system", "content": SUGGEST_SYSTEM},
                {"role": "user", "content": context}]
    try:
        if llm_fn:
            raw = await llm_fn(llm_cfg, messages)
        else:
            resp = await chat_complete(llm_cfg, messages, max_tokens=800, timeout=40.0)
            raw = resp.get("content", "")
        data = json.loads(_strip_json(raw))
        if isinstance(data, dict):
            data = data.get("suggestions") or data.get("items") or []
        return filter_dedupe(data if isinstance(data, list) else [])
    except Exception:
        return []


def _strip_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    s, e = text.find("["), text.rfind("]")
    if s >= 0 and e > s:
        return text[s:e + 1]
    return text
