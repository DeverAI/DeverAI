"""模型自动打分与画像更新（v5 决策 24 b.1 落地）。

机制：
- 对每个 chat 模型，用 judge_model 调用 web_search 搜 Benchmark 给 score_metrics 打分。
- 时间戳：每个指标 score 的 `updated`（ISO 日期）+ `estimated`（是否"搜不到 benchmark 时的估分"）。
- 按 score_refresh_hours 判断是否需要重搜：有缺失分 / 有估分 / 最旧 updated 超间隔。
- 搜不到 benchmark 时参考同类模型估分，并标记 estimated=True（下次优先重搜）。
- 同时更新模型画像 intro（能干什么 / 干什么事 / 特点）与 caps。

轻量化：复用 search_tools.web_search_raw + summarize_with_helper + llm.chat_complete，
零新依赖。所有更新走 models.upsert_model 原子落盘。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Optional

from .llm import chat_complete, extract_json
from .models import ModelInfo, get_llm_cfg, load_models, resolve_model_id, upsert_model

# 每模型每次自动打分最多搜几条结果（控制在 5 条内，轻量）
_SCORE_SEARCH_LIMIT = 5
# 自动打分互斥：先占位再 await（FreqErr #171），并发触发直接拒绝重复任务
_score_lock = threading.Lock()
_score_running = False


def _parse_ts(ts: str) -> Optional[datetime]:
    """解析 ISO 日期/时间戳（date.today().isoformat() = YYYY-MM-DD）。"""
    ts = (ts or "").strip()
    if not ts:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts[:19], fmt)
        except ValueError:
            continue
    return None


def model_needs_refresh(m: ModelInfo, cfg) -> bool:
    """判断模型是否需要自动重搜打分。

    需要刷新：① 有缺失分；② 有估分（搜不到 benchmark 的，应优先重搜）；
    ③ 最旧 updated 超过 score_refresh_hours（模型路由真实实力可能已变化）。
    """
    hours = int(getattr(cfg, "score_refresh_hours", 168) or 0)
    if hours <= 0:
        return False
    scores = (m.scores or {})
    if not scores:
        return True
    oldest: Optional[datetime] = None
    for s in scores.values():
        if not isinstance(s, dict):
            continue
        if s.get("value") is None:
            return True
        if s.get("estimated"):
            return True
        ts = _parse_ts(s.get("updated", ""))
        if ts is None:
            return True  # 有分但无时间戳 → 视为需重搜（保持时间戳机制完整）
        if oldest is None or ts < oldest:
            oldest = ts
    if oldest is None:
        return True
    return datetime.now() - oldest > timedelta(hours=hours)


def _score_prompt(metrics: list, name: str, id_: str, search_text: str) -> str:
    joined = "、".join(metrics)
    return (
        "你是模型评测打分员。根据下方搜索结果，对模型「{name}」（id={id_}）在以下指标上 "
        "打 0-10 分（可为小数）：{joined}。\n"
        "规则：\n"
        "1. 仅依据搜索结果给出的真实信息打分；某指标完全没有信息时，该指标 value 填 null。\n"
        "2. 同时写一段 80 字内的中文画像（intro）：这个模型能干什么、擅长什么、特点。\n"
        "3. caps 用布尔表示能力：attachment（附件输入）/ image（图片输入）/ web_search（自主搜索）/ crawl（自主爬取）。\n"
        "4. 只输出 JSON，不要任何解释。\n\n"
        "搜索结果：\n{search_text}\n\n"
        '输出 JSON 格式：{{"scores": {{"指标名": 数值或null, ...}}, "intro": "...", '
        '"caps": {{"attachment": bool, "image": bool, "web_search": bool, "crawl": bool}}}}'
    ).format(name=name, id_=id_, joined=joined, search_text=search_text)


async def auto_score_model(cfg, m: ModelInfo, search_fn=None) -> dict:
    """对单个模型搜 benchmark 并打分，更新 scores/intro/caps。返回结果摘要。

    search_fn(query, limit) -> list，缺省用 search_tools.web_search_raw 的结果部分。
    """
    from .search_tools import web_search_raw, summarize_with_helper

    metrics = list(getattr(cfg, "score_metrics", []) or [])
    query = f"{m.name or m.id} benchmark 评测 推理 幻觉率 指令遵循"
    try:
        if search_fn is not None:
            results = await search_fn(query, _SCORE_SEARCH_LIMIT)
        else:
            results, _channel = await web_search_raw(cfg, query, _SCORE_SEARCH_LIMIT)
    except Exception as e:
        return {"model": m.id, "ok": False, "note": f"搜索失败: {e}"}
    if not results:
        return {"model": m.id, "ok": False, "note": "无搜索结果"}

    raw = "\n".join(
        f"- {r.get('title','')} {r.get('url','')}\n  {r.get('snippet','')}"
        for r in results if isinstance(r, dict)
    )
    if not raw.strip():
        return {"model": m.id, "ok": False, "note": "搜索结果为空"}
    try:
        compact = await summarize_with_helper(cfg, raw, f"{m.name} benchmark 评测")
    except Exception:
        compact = raw[:4000]

    judge_cfg = get_llm_cfg(cfg, resolve_model_id(cfg, getattr(cfg, "judge_model", "")))
    try:
        msg = await chat_complete(judge_cfg, [
            {"role": "system", "content": "你是严谨的模型评测打分员，只输出 JSON。"},
            {"role": "user", "content": _score_prompt(metrics, m.name or m.id, m.id, compact)},
        ], temperature=0.1, max_tokens=1500, timeout=120.0)
        data = extract_json(msg.get("content") or "")
    except Exception as e:
        return {"model": m.id, "ok": False, "note": f"打分调用失败: {e}"}

    if not isinstance(data, dict):
        return {"model": m.id, "ok": False, "note": "打分输出非 JSON 对象"}

    today = datetime.now().strftime("%Y-%m-%d")
    ms = load_models()
    target = next((x for x in ms if x.id == m.id), None)
    if target is None:
        return {"model": m.id, "ok": False, "note": "模型已被删除"}

    scored = 0
    estimated = 0
    scores = data.get("scores") or {}
    if not isinstance(scores, dict):
        return {"model": m.id, "ok": False, "note": "打分输出 scores 非对象"}
    for metric in metrics:
        val = scores.get(metric)
        if val is None:
            # 搜不到该指标 benchmark → 记 null + estimated（下次优先重搜）
            s = dict((target.scores or {}).get(metric) or {})
            s["value"] = None
            s["fixed"] = False
            s["updated"] = today
            s["estimated"] = True
            target.scores = dict(target.scores or {})
            target.scores[metric] = s
            estimated += 1
            continue
        try:
            v = max(0.0, min(10.0, float(val)))
        except (TypeError, ValueError):
            continue
        s = dict((target.scores or {}).get(metric) or {})
        s["value"] = v
        s["fixed"] = True
        s["updated"] = today
        s["estimated"] = False
        target.scores = dict(target.scores or {})
        target.scores[metric] = s
        scored += 1

    # 更新画像 intro / caps（非空才覆盖，避免清空用户手填信息）
    intro = str(data.get("intro") or "").strip()
    caps = data.get("caps") or {}
    if intro:
        target.intro = intro
    if isinstance(caps, dict):
        for k in ("attachment", "image", "web_search", "crawl"):
            if k in caps:
                target.caps = dict(target.caps or {})
                target.caps[k] = bool(caps[k])

    upsert_model(target)
    return {"model": m.id, "ok": True, "scored": scored, "estimated": estimated,
            "intro_updated": bool(intro), "date": today}


async def auto_score_all(cfg, force: bool = False) -> dict:
    """遍历所有 chat 模型，对需要刷新的执行自动打分。返回汇总。

    force=True 时忽略时间戳，全部重搜（用户手动触发）。
    同一时间只允许一个打分任务：并发重入会重复计费并造成 models.json lost update。
    """
    global _score_running
    with _score_lock:
        if _score_running:
            return {"ok": False, "skipped": 0, "processed": 0, "total": 0,
                    "results": [], "note": "已有自动打分任务进行中"}
        _score_running = True
    try:
        models = [m for m in load_models() if m.kind != "embedding"]
        results = []
        for m in models:
            if not force and not model_needs_refresh(m, cfg):
                results.append({"model": m.id, "skipped": True})
                continue
            r = await auto_score_model(cfg, m)
            results.append(r)
        return {
            "ok": True,
            "total": len(models),
            "processed": sum(1 for r in results if r.get("ok")),
            "skipped": sum(1 for r in results if r.get("skipped")),
            "results": results,
        }
    finally:
        with _score_lock:
            _score_running = False
