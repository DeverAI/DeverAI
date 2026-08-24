"""v5 新工具集：互联网搜索（三级回退）、文件搜索（复合）、专家私有待办、端口文档。

- 互联网搜索：① 模型自带（caps.web_search，在请求体层面处理）② DuckDuckGo HTML（零 key）
  ③ 用户配置的付费搜索 API（Tavily/Serper 兼容）。结果交助手模型压缩到 ≤1500 token。
- 文件搜索：文件名相似度 + 内容正则 复合为一个工具（用户拍板：减少工具量）。
- 专家待办：内存私有待办（不碰根 todo.md，那是总司令的），每完成一项自动 heartbeat 续约。
- 端口文档：ports.md（写前先抢租约锁，同一时刻单一持有者，归总司令调度）。
"""
import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .llm import chat_complete, estimate_tokens
from .locks import atomic_write, get_locks
from .models import get_llm_cfg, resolve_model_id

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
SEARCH_MAX_TOKENS = 1500          # 助手模型摘要上限（token）
PORTS_FILE = "ports.md"
PORTS_HEADER = ("# 端口文档（AI 维护）\n\n"
                "> 端口使用情况登记表：需要/变更/使用端口时必须更新本表。\n\n"
                "| 端口 | 状态 | 类型 | 位置 | 调取方式 | 调用数量与位置 |\n"
                "|------|------|------|------|----------|----------------|\n")


# ---------------------------------------------------------------- 互联网搜索

async def _search_duckduckgo(query: str, limit: int = 6) -> list:
    """DuckDuckGo HTML 接口（零 key，脆弱但免费）。返回 [{title, url, snippet}]。"""
    url = "https://html.duckduckgo.com/html/"
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0),
                                 follow_redirects=True) as client:
        resp = await client.post(url, data={"q": query}, headers={"User-Agent": UA})
        resp.raise_for_status()
        html = resp.text
    results = []
    # 结果链接：<a rel="nofollow" class="result__a" href="...">标题</a>
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if href.startswith("//duckduckgo.com/l/?uddg="):
            import urllib.parse as _up
            try:
                # uddg 的值本身是 URL 编码的目标地址，直接 unquote 即可；
                # 对其调 parse_qs 会得到空 dict，导致真实 URL 永远解不出来。
                href = _up.unquote(href.split("uddg=", 1)[1].split("&", 1)[0])
            except Exception:
                pass
        results.append({"title": title, "url": href, "snippet": ""})
        if len(results) >= limit:
            break
    # 摘要：<a class="result__snippet"...>
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    for i, sn in enumerate(snippets[:limit]):
        if i < len(results):
            results[i]["snippet"] = re.sub(r"<[^>]+>", "", sn).strip()[:300]
    return results


async def _search_paid(cfg, query: str, limit: int = 6) -> list:
    """付费搜索 API（用户配置 search_api_url/search_api_key，Tavily 兼容请求体）。"""
    payload = {"query": query, "max_results": limit}
    headers = {"Authorization": f"Bearer {cfg.search_api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        resp = await client.post(cfg.search_api_url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    out = []
    for r in (data.get("results") or [])[:limit]:
        out.append({"title": r.get("title", ""), "url": r.get("url", ""),
                    "snippet": (r.get("content") or "")[:300]})
    return out


async def summarize_with_helper(cfg, raw: str, topic: str) -> str:
    """助手模型压缩搜索结果到 ≤SEARCH_MAX_TOKENS token。失败时截断返回原文。"""
    if not raw:
        return ""
    budget_chars = SEARCH_MAX_TOKENS * 3
    if estimate_tokens(raw) <= SEARCH_MAX_TOKENS:
        return raw
    try:
        llm_cfg = get_llm_cfg(cfg, resolve_model_id(cfg, getattr(cfg, "helper_model", "")))
        msg = await chat_complete(llm_cfg, [
            {"role": "system", "content": "你是搜索摘要助手：把原始搜索结果压缩为与主题相关的要点，保留关键事实与出处链接，不得编造。"},
            {"role": "user", "content": f"主题：{topic}\n\n原始结果：\n{raw[:SEARCH_MAX_TOKENS * 6]}"},
        ], max_tokens=SEARCH_MAX_TOKENS, timeout=90.0)
        return (msg.get("content") or raw[:budget_chars]).strip()
    except Exception:
        return raw[:budget_chars] + "\n…(摘要失败，已截断)"


async def web_search_raw(cfg, query: str, limit: int = 6) -> tuple:
    """三级回退搜索，返回 (results, channel)。全部失败返回 ([], 错误说明)。"""
    if getattr(cfg, "search_api_key", "") and getattr(cfg, "search_api_url", ""):
        try:
            rs = await _search_paid(cfg, query, limit)
            if rs:
                return rs, "付费API"
        except Exception:
            pass  # 降级到 DDG
    try:
        rs = await _search_duckduckgo(query, limit)
        if rs:
            return rs, "DuckDuckGo"
    except Exception as e:
        return [], f"搜索通道全部失败：{type(e).__name__}: {e}"
    return [], "未找到结果"


# ---------------------------------------------------------------- 专家私有待办

@dataclass
class ExpertTodoStore:
    """专家私有待办（内存）：expert_id -> [{text, done}]。总司令才读写根 todo.md。"""
    _todos: dict = field(default_factory=dict)

    def update(self, expert_id: str, items: list) -> list:
        # items 为完整列表则整体替换；单条增量由 action 控制
        normalized = []
        for it in items or []:
            if isinstance(it, dict):
                text = str(it.get("text", "")).strip()
                done = bool(it.get("done"))
            else:
                # 容错：LLM 可能传入纯字符串列表（["todo1","todo2"]），归一为 dict
                text = str(it).strip()
                done = False
            if text:
                normalized.append({"text": text, "done": done})
        self._todos[expert_id] = normalized
        return self._todos[expert_id]

    def get(self, expert_id: str) -> list:
        return list(self._todos.get(expert_id, []))

    def clear(self, expert_id: str) -> None:
        self._todos.pop(expert_id, None)

    def just_completed(self, expert_id: str, text: str) -> bool:
        """判断某条是否刚变为 done（用于触发 heartbeat）。"""
        return any(t["done"] and t["text"] == text for t in self._todos.get(expert_id, []))


_todo_store = ExpertTodoStore()


def get_todo_store() -> ExpertTodoStore:
    return _todo_store


async def tool_todo_update(args, ctx) -> dict:
    """专家私有待办：action=set 整体替换 / add / done(text)。完成项自动 heartbeat 续约。"""
    expert_id = getattr(ctx, "expert_id", "") or "__main__"
    action = args.get("action") or "set"
    store = get_todo_store()
    if action == "add":
        cur = store.get(expert_id)
        cur.append({"text": str(args.get("text") or "").strip(), "done": False})
        items = store.update(expert_id, cur)
    elif action == "done":
        text = str(args.get("text") or "").strip()
        cur = store.get(expert_id)
        for t in cur:
            if not t["done"] and (t["text"] == text or text in t["text"]):
                t["done"] = True
                break
        items = store.update(expert_id, cur)
        # 每完成一个待办项自动 heartbeat（用户拍板，专家无感）
        if getattr(ctx.cfg, "ENABLE_LOCKS", False):
            get_locks().heartbeat(f"expert:{expert_id}", expert_id)
    else:
        items = store.update(expert_id, args.get("todos") or [])
    if ctx.emit is not None:
        try:
            await ctx.emit({"type": "expert_todos", "expert_id": expert_id, "todos": items})
        except Exception:
            pass
    done_n = sum(1 for t in items if t["done"])
    return {"ok": True, "output": f"待办已更新：{done_n}/{len(items)} 完成",
            "meta": {"todos": items}}


# ---------------------------------------------------------------- 文件搜索（复合）

async def tool_file_search(args, ctx) -> dict:
    """复合文件搜索：文件名相似度 + 内容正则，一次返回。"""
    query = str(args.get("query") or "").strip()
    rel = args.get("path") or "."
    glob_pat = args.get("glob") or ""
    # v8.14：严格布尔解析——bool("false") 为 True，模型传 "false" 会语义反转
    content = str(args.get("content") or "").strip().lower() in ("1", "true", "yes", "on") \
        if not isinstance(args.get("content"), bool) else bool(args.get("content"))
    try:
        limit = min(int(args.get("limit") or 20), 60)
    except (TypeError, ValueError):
        limit = 20
    if not query:
        return {"ok": False, "output": "query 为空。"}
    from .tools import resolve_ws, SKIP_DIRS, _is_read_protected  # 复用路径安全与跳过目录
    p = resolve_ws(ctx, rel)
    if not p.exists():
        return {"ok": False, "output": f"路径不存在: {rel}"}
    # v8.13.1：受保护数据不可文件搜索（防绕过 read_file 抓取密钥/目录结构）
    if _is_read_protected(ctx, p):
        return {"ok": False, "output": f"该路径属于受保护数据，禁止搜索: {rel}"}
    ws = Path(ctx.workspace).resolve()

    def _scan():
        names, bodies = [], []
        files = []
        if p.is_file():
            files = [p]
        else:
            import os
            for root, dirs, fnames in os.walk(p):
                dirs[:] = [d for d in dirs
                           if d not in SKIP_DIRS and not _is_read_protected(ctx, Path(root) / d)]
                for name in fnames:
                    if glob_pat and not Path(name).match(glob_pat):
                        continue
                    fp = Path(root) / name
                    if _is_read_protected(ctx, fp):
                        continue
                    files.append(fp)
                    if len(files) > 20000:
                        files = files[:20000]
                        break
                if len(files) > 20000:
                    break
        # 1) 文件名相似度（v8.1 三级匹配引擎）
        from .matcher import rank as _match_rank
        scored = []
        rels = []
        for f in files:
            try:
                rels.append(str(f.relative_to(ws)))
            except ValueError:
                rels.append(str(f))
        file_docs = [f"{f.name} {rel}" for f, rel in zip(files, rels)]
        for _i, _s in _match_rank(query, file_docs, min_score=0.30):
            scored.append((_s, rels[_i]))
        names = [f"{relp}（相似度 {s:.2f}）" for s, relp in scored[:limit]]
        # 2) 内容正则（可选）
        if content:
            try:
                rx = re.compile(re.escape(query), re.IGNORECASE)
            except re.error:
                rx = None
            if rx:
                for f in files:
                    try:
                        head = f.read_bytes()[:8192]
                        if b"\x00" in head:
                            continue
                        text = f.read_text(encoding="utf-8", errors="replace")
                        hits = []
                        for ln, line in enumerate(text.splitlines(), 1):
                            if rx.search(line[:5000]):
                                hits.append(f"{ln}: {line.strip()[:200]}")
                                if len(hits) >= 5:
                                    break
                        if hits:
                            relp = str(f.relative_to(ws))
                            bodies.append(f"{relp}\n" + "\n".join(f"  {h}" for h in hits))
                            if len(bodies) >= limit:
                                break
                    except OSError:
                        continue
        return names, bodies

    loop = asyncio.get_running_loop()
    names, bodies = await loop.run_in_executor(None, _scan)
    out = []
    if names:
        out.append(f"[文件名匹配 {len(names)} 条]\n" + "\n".join(names))
    if bodies:
        out.append(f"[内容匹配 {len(bodies)} 个文件]\n" + "\n".join(bodies))
    if not out:
        return {"ok": True, "output": f"未找到与「{query}」相关的文件。"}
    return {"ok": True, "output": "\n\n".join(out),
            "meta": {"names": names, "bodies": bodies}}


# ---------------------------------------------------------------- 端口文档

def _ports_path(ctx) -> Path:
    return Path(ctx.workspace).resolve() / PORTS_FILE


async def tool_port_declare(args, ctx) -> dict:
    """声明/更新端口登记（写 ports.md 前先抢租约锁，同一时刻单一持有者）。"""
    from . import session_snap as _snap
    if _snap.is_readonly():
        return {"ok": False,
                "output": f"AI 只读模式（{_snap.alert_reason() or '报警中'}）：port_declare 已禁止。"}
    port = str(args.get("port") or "").strip()
    if not port:
        return {"ok": False, "output": "port 不能为空。"}
    row = {
        "端口": port,
        "状态": args.get("status") or "锁定",
        "类型": args.get("type") or "未分类",
        "位置": args.get("location") or "",
        "调取方式": args.get("call_how") or "",
        "调用数量与位置": args.get("callers") or "",
    }
    locks = get_locks()
    expert_id = getattr(ctx, "expert_id", "") or "__commander__"
    try:
        await locks.acquire(PORTS_FILE, expert_id, ttl=30.0, timeout=5.0)
    except Exception as e:
        return {"ok": False, "output": f"端口文档锁获取失败（{e}），请稍后重试或请总司令协调。"}
    try:
        path = _ports_path(ctx)
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else PORTS_HEADER
        lines = text.splitlines()
        cell = "| " + " | ".join(row.values()) + " |"
        replaced = False
        for i, ln in enumerate(lines):
            if ln.startswith(f"| {port} "):
                lines[i] = cell
                replaced = True
                break
        if not replaced:
            lines.append(cell)
        atomic_write(path, "\n".join(lines) + "\n")
    finally:
        locks.release(PORTS_FILE, expert_id)
    if ctx.emit is not None:
        try:
            await ctx.emit({"type": "file_changed", "path": str(_ports_path(ctx)), "rel": PORTS_FILE})
        except Exception:
            pass
    return {"ok": True, "output": f"端口 {port} 已登记（{'更新' if replaced else '新增'}）到 {PORTS_FILE}"}


async def tool_port_read(args, ctx) -> dict:
    """读取端口文档（无锁，只读）。"""
    path = _ports_path(ctx)
    if not path.exists():
        return {"ok": True, "output": f"{PORTS_FILE} 尚不存在（未登记过端口）。"}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return {"ok": True, "output": f.read(6000)}
    except OSError as e:
        return {"ok": False, "output": f"读取端口文档失败: {e}"}
