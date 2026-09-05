"""Agent 长期记忆库（v8.18）：跨会话教训 + 用户画像 + 项目知识。

对标业界 Agent 记忆机制的轻量本地实现（零新依赖）：
- 文件式记忆（CLAUDE.md / Cursor rules）：结构化条目落盘 data/agent_memory.json
- 检索注入（Mem0）：按 bigram 相似度取 top-k 拼进系统提示（复用 matcher）
- 自编辑（MemGPT）：Agent 用 memory_read / memory_record 工具读写
- 反思沉淀（Reflexion）：主循环犯错（用户纠正 / 工具连续失败）→ 副驾驶提炼教训入库
- 漂移合体：build_snapshot 携带条目，回传按 id + 相似度合并去重

条目字段：{id, kind, scope, phenomenon, root_cause, solution, tags, count,
          created_at, updated_at, source}
- kind：fault（故障）/ lesson（教训）/ preference（用户偏好）/ knowledge（项目知识）
- scope：global（跨项目通用坑）/ local（仅本项目/该工作区相关）

并发：threading.Lock 串行化读写（GUI 线程 + 副驾驶反思线程 + 工具线程共用）。
数据文件绝不进工作区（防 AI 误删），漂移时随快照同步。
"""
import datetime
import threading
import uuid

from .config import DATA_DIR
from .matcher import char_similarity
from .storage import load_json, save_json

MEMORY_PATH = DATA_DIR / "agent_memory.json"
MAX_ENTRIES = 300
_MERGE_SIM = 0.5      # 现象相似度 ≥ 此值视为同一条教训（合并 count）
_QUERY_MIN = 0.18     # 检索最低分
_LOCK = threading.Lock()

_KINDS = ("fault", "lesson", "preference", "knowledge")
_SCOPES = ("global", "local")


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load() -> dict:
    data = load_json(MEMORY_PATH, None)
    if not isinstance(data, dict):
        data = {}
    entries = data.get("entries")
    if not isinstance(entries, list):
        entries = []
    profile = data.get("profile")
    if not isinstance(profile, dict):
        profile = {}
    return {"version": 1, "profile": profile,
            "entries": [e for e in entries if isinstance(e, dict) and e.get("phenomenon")]}


def _save(store: dict) -> None:
    save_json(MEMORY_PATH, store)


def _norm_text(t: str) -> str:
    return "".join(str(t or "").split())


def record(kind: str, scope: str, phenomenon: str, root_cause: str = "",
           solution: str = "", tags=None, source: str = "copilot") -> dict:
    """记录/合并一条记忆。现象与已有条目相似度达阈值时合并（count+1，字段取更完整者）。

    返回条目 dict；合并时附带 _merged=True 供调用方区分新增/更新。
    phenomenon 为空抛 ValueError（调用方兜底）。
    """
    phen = _norm_text(phenomenon)
    if not phen:
        raise ValueError("phenomenon 不能为空")
    if kind not in _KINDS:
        kind = "lesson"
    if scope not in _SCOPES:
        scope = "local"
    tag_list = [str(t).strip()[:20] for t in (tags or []) if str(t).strip()][:5]
    with _LOCK:
        store = _load()
        entries = store["entries"]
        hit = None
        for e in entries:
            if e.get("kind") == kind and char_similarity(phen, _norm_text(e.get("phenomenon"))) >= _MERGE_SIM:
                hit = e
                break
        ts = _now()
        if hit is not None:
            hit["count"] = int(hit.get("count") or 1) + 1
            hit["updated_at"] = ts
            # 字段取更完整者（新值更长则覆盖）
            for k, v in (("root_cause", root_cause), ("solution", solution)):
                if len(str(v or "").strip()) > len(str(hit.get(k) or "")):
                    hit[k] = str(v).strip()[:300]
            merged_tags = [str(t) for t in (hit.get("tags") or [])]
            for t in tag_list:
                if t not in merged_tags and len(merged_tags) < 5:
                    merged_tags.append(t)
            hit["tags"] = merged_tags
            out = dict(hit)
        else:
            entry = {
                "id": uuid.uuid4().hex[:10],
                "kind": kind, "scope": scope,
                "phenomenon": str(phenomenon).strip()[:300],
                "root_cause": str(root_cause or "").strip()[:300],
                "solution": str(solution or "").strip()[:300],
                "tags": tag_list,
                "count": 1,
                "created_at": ts, "updated_at": ts,
                "source": str(source or "agent")[:20],
            }
            entries.append(entry)
            out = dict(entry)
        # 容量上限：按 updated_at 保最新 MAX_ENTRIES 条
        if len(entries) > MAX_ENTRIES:
            entries.sort(key=lambda e: str(e.get("updated_at") or ""), reverse=True)
            del entries[MAX_ENTRIES:]
        store["entries"] = entries
        _save(store)
    out["_merged"] = hit is not None
    return out


def query(text: str, limit: int = 5) -> list:
    """检索记忆条目。text 为空或无命中时回落到高频条目（count 降序）。"""
    with _LOCK:
        entries = _load()["entries"]
    if not entries:
        return []
    q = _norm_text(text)
    scored = []
    for e in entries:
        if q:
            hay_p = _norm_text(e.get("phenomenon"))
            hay_t = _norm_text(" ".join([str(x) for x in (e.get("tags") or [])])
                               + " " + str(e.get("root_cause") or ""))
            s = max(char_similarity(q, hay_p), char_similarity(q, hay_t))
            s += 0.12 * min(int(e.get("count") or 1), 3) / 3.0
            if s < _QUERY_MIN:
                continue
        else:
            s = float(int(e.get("count") or 1))
        scored.append((s, e))
    scored.sort(key=lambda x: (-x[0], -int(x[1].get("count") or 1)))
    if not scored and q:
        # 无命中回落高频教训（跨任务通用的坑最值得预注入）
        fallback = sorted(entries, key=lambda e: -int(e.get("count") or 1))
        return list(fallback[:min(2, limit)])
    return [e for _, e in scored[:max(1, limit)]]


def render_block(text: str, limit: int = 4) -> str:
    """渲染系统提示注入块。无条目返回空串（不占上下文）。"""
    entries = query(text, limit=limit)
    if not entries:
        return ""
    lines = ["【长期记忆·历史教训（动手前对照，避开已知坑）】"]
    for e in entries:
        phen = str(e.get("phenomenon") or "")[:120]
        rc = str(e.get("root_cause") or "")[:100]
        sol = str(e.get("solution") or "")[:120]
        line = f"- {phen}"
        if rc:
            line += f" → 远因: {rc}"
        if sol:
            line += f" → 对策: {sol}"
        lines.append(line)
    return "\n".join(lines)[:1800]


def export_entries() -> list:
    """导出全部条目（漂移快照用，深拷贝防并发改写）。"""
    with _LOCK:
        entries = _load()["entries"]
    return [dict(e) for e in entries]


def import_entries(entries) -> dict:
    """漂移回传合体：按 id 精确合并，再按相似度去重合并。返回 {added, merged}。"""
    if not isinstance(entries, list):
        return {"added": 0, "merged": 0}
    added = merged = 0
    with _LOCK:
        store = _load()
        mine = store["entries"]
        by_id = {str(e.get("id")): e for e in mine if e.get("id")}
        ts = _now()
        for raw in entries:
            if not isinstance(raw, dict) or not str(raw.get("phenomenon") or "").strip():
                continue
            eid = str(raw.get("id") or "")
            hit = by_id.get(eid) if eid else None
            if hit is None:
                phen = _norm_text(raw.get("phenomenon"))
                kind = raw.get("kind") if raw.get("kind") in _KINDS else "lesson"
                for e in mine:
                    if e.get("kind") == kind and char_similarity(phen, _norm_text(e.get("phenomenon"))) >= _MERGE_SIM:
                        hit = e
                        break
            if hit is not None:
                hit["count"] = max(int(hit.get("count") or 1), int(raw.get("count") or 1))
                hit["updated_at"] = ts
                for k in ("root_cause", "solution"):
                    if len(str(raw.get(k) or "")) > len(str(hit.get(k) or "")):
                        hit[k] = str(raw.get(k)).strip()[:300]
                merged += 1
            else:
                entry = {
                    "id": eid or uuid.uuid4().hex[:10],
                    "kind": raw.get("kind") if raw.get("kind") in _KINDS else "lesson",
                    "scope": raw.get("scope") if raw.get("scope") in _SCOPES else "local",
                    "phenomenon": str(raw.get("phenomenon")).strip()[:300],
                    "root_cause": str(raw.get("root_cause") or "").strip()[:300],
                    "solution": str(raw.get("solution") or "").strip()[:300],
                    "tags": [str(t)[:20] for t in (raw.get("tags") or []) if str(t).strip()][:5],
                    "count": max(1, int(raw.get("count") or 1)),
                    "created_at": str(raw.get("created_at") or ts),
                    "updated_at": ts,
                    "source": str(raw.get("source") or "drift")[:20],
                }
                mine.append(entry)
                if entry["id"]:
                    by_id[entry["id"]] = entry
                added += 1
        if len(mine) > MAX_ENTRIES:
            mine.sort(key=lambda e: str(e.get("updated_at") or ""), reverse=True)
            del mine[MAX_ENTRIES:]
        store["entries"] = mine
        _save(store)
    return {"added": added, "merged": merged}


def profile_get() -> dict:
    with _LOCK:
        return dict(_load()["profile"])


def profile_update(patch: dict) -> dict:
    """合并更新用户画像（键值覆盖，值截断 500 字）。返回最新画像。"""
    if not isinstance(patch, dict):
        return profile_get()
    with _LOCK:
        store = _load()
        for k, v in patch.items():
            key = str(k).strip()[:40]
            if not key:
                continue
            store["profile"][key] = str(v).strip()[:500]
        _save(store)
        return dict(store["profile"])


def list_all() -> list:
    with _LOCK:
        entries = _load()["entries"]
    return sorted(entries, key=lambda e: (str(e.get("updated_at") or "")), reverse=True)


def clear() -> None:
    with _LOCK:
        _save({"version": 1, "profile": _load()["profile"], "entries": []})
