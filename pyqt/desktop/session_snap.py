"""v8.3 任务级快照：一轮对话（用户消息→回复完成）的完整打包与回退。

- begin_round(cfg)：开启新一轮（round_id = ts_hex），清空旧轮残留
- tool_rollback(cfg, rel, content, tool_name)：轮内文件级快照（保留 round_keep_rollback 次
  工具调用点；不断覆盖最新安全快照，给用户 2 次工具调用前回退机会）
- commit_round(cfg, ws, meta)：回复完成时——AI 生成标题、todo/devlog/tree 快照、
  整个工作区（不含备份/data）压缩 zip、清理旧轮多次快照、总上限淘汰
- list_sessions() / get_session(round_id) / delete_session(round_id)
- restore_session(round_id, ws)：解压 zip 恢复工作区（防 zip-slip 越界）

设计要点：
- 快照目录 data/sessions/{round_id}/：snapshot.zip + meta.json + rollback/snap_{i}.json
- 标题：AI 依据工具调用序列/输入生成一句话摘要（无 key 时用首条工具名+时间兜底）
- 全工作区级 zip 仅一轮一次（tree_update_at_round_end 决定在回合结束/开始时打包）
- 任务完成（commit）后：本轮多次 rollback 快照清除（删除，不留回收站），
  直到下一轮对话产生新快照
- 恢复前可由上层触发"后台审查对话"（guard/审查模块），无 P0 才允许恢复
"""
from __future__ import annotations

import datetime
import json
import os
import secrets
import shutil
import threading
import zipfile
from pathlib import Path
from typing import Optional

from .config import DATA_DIR, get_config
from .errors import log_error
from .storage import load_json, save_json

SESSION_DIR: Path = DATA_DIR / "sessions"

# ---- v8.3 报警只读标志（模块级，单实例应用）----
_READONLY = False
_ALERT_REASON = ""
_CURRENT_ROUND = ""
_META_LOCK = threading.Lock()  # P2-16：meta 读改写串行化，防与 commit/gc 并发丢更新


def set_readonly(on: bool, reason: str = "") -> None:
    """AI 主动报警时置只读：停止快照清理、写工具拒绝。"""
    global _READONLY, _ALERT_REASON
    _READONLY = bool(on)
    _ALERT_REASON = reason or ""


def is_readonly() -> bool:
    return _READONLY


def alert_reason() -> str:
    return _ALERT_REASON


def set_current_round(round_id: str) -> None:
    global _CURRENT_ROUND
    _CURRENT_ROUND = round_id or ""


def current_round() -> str:
    return _CURRENT_ROUND

# zip 打包时排除的顶层目录/文件（含备份、数据、缓存、git）
EXCLUDE_TOP = {"backups", "data", "sessions", ".git", "__pycache__", "node_modules",
               ".idea", ".vscode", "venv", ".venv", ".mypy_cache", ".pytest_cache"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".tmp", ".bak"}


def _now() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def new_round_id() -> str:
    return _now() + "_" + secrets.token_hex(2)


def _round_dir(round_id: str) -> Path:
    safe = _safe(round_id)
    if not safe or safe != str(round_id):
        raise ValueError("非法会话 id")
    return SESSION_DIR / safe


def _safe(name: str) -> str:
    return "".join(c for c in str(name) if c.isalnum() or c in "_-")


def begin_round(cfg=None) -> str:
    """开启新一轮对话。返回 round_id。

    tree_update_at_round_end=False 时：回合开始时即打包完整快照。
    迁移上一轮依赖树校验注入的问题到新轮（专家读取 current_round 时能命中）。"""
    cfg = cfg or get_config()
    if not getattr(cfg, "ENABLE_SESSION_SNAP", True):
        return ""
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        rid = new_round_id()
        d = _round_dir(rid)
        d.mkdir(parents=True, exist_ok=True)
        # P1-1 修复：把最近 committed 轮的 inject 迁移过来（跨轮透传）
        prev_inject = {}
        try:
            prev = [s for s in list_sessions() if s.get("status") == "committed"]
            if prev:
                prev_meta = _load_meta(prev[0]["round_id"])
                prev_inject = prev_meta.get("inject") or {}
        except Exception:
            prev_inject = {}
        save_json(d / "meta.json", {
            "round_id": rid, "ts": _now(), "title": "",
            "input": "", "todo": "", "devlog": "", "tree": {},
            "tool_calls": [], "size_mb": 0.0, "status": "active",
            "inject": prev_inject,
        })
        # v8.3 P1-4：False=回合开始时打包（tree_update_at_round_end）
        if not getattr(cfg, "tree_update_at_round_end", True):
            ws = getattr(cfg, "workspace", "") or ""
            if ws:
                pack_workspace(rid, ws)
        return rid
    except Exception:
        return ""


def _load_meta(rid: str) -> dict:
    try:
        return load_json(_round_dir(rid) / "meta.json", {})
    except Exception:
        return {}


def _save_meta(rid: str, meta: dict) -> None:
    try:
        save_json(_round_dir(rid) / "meta.json", meta)
    except Exception as e:
        # v8.13：快照元数据写失败落 Err.log（错误不得吞没）
        log_error(f"快照元数据写失败 {rid}", e)


def update_tree(rid: str, rel: str, size: int, op: str = "write") -> None:
    """文件操作后增量更新依赖树（写入当前轮 meta.tree）。"""
    if not rid or not getattr(get_config(), "ENABLE_DEP_TREE", True):
        return
    try:
        from . import dep_tree as _deps
        with _META_LOCK:
            m = _load_meta(rid)
            tree = _deps.update_on_file_op(m.get("tree") or {}, rel, size, op)
            m["tree"] = tree
            _save_meta(rid, m)
    except Exception as e:
        log_error(f"依赖树增量更新失败 {rel}", e)


def get_tree(rid: str) -> dict:
    return (_load_meta(rid) or {}).get("tree", {})


def inject_problems(rid: str, rel: str, problems: list) -> None:
    """v8.3 缺口5：把 tree 校验问题按文件注入（存 meta.inject[rel]）。

    按 rel 维度而非专家维度存储——专家 id 每轮变化、且轮末锁已释放，
    按专家存会导致数据送不到。专家读取时用自己申报的 files 过滤。"""
    if not rid:
        return
    with _META_LOCK:
        m = _load_meta(rid)
        inject = m.get("inject") or {}
        cur = inject.get(rel) or []
        for p in problems:
            if p not in cur:
                cur.append(p)
        inject[rel] = cur[-20:]
        m["inject"] = inject
        _save_meta(rid, m)


def get_inject(rid: str) -> dict:
    """读取当前轮待注入问题 {rel: [problems]}。"""
    return (_load_meta(rid) or {}).get("inject") or {}


def set_round_input(rid: str, user_input: str) -> None:
    if not rid:
        return
    with _META_LOCK:
        m = _load_meta(rid)
        m["input"] = user_input or ""
        _save_meta(rid, m)


def add_tool_call(rid: str, tool_name: str) -> int:
    """记录一次工具调用，返回自增序号（供 tool_rollback 作为回退点编号）。

    P1-B（查修）：读改写必须持 _META_LOCK——AOE 并行子 Agent 并发调用时，
    无锁会互相覆盖 call_count（两节点拿到相同 round_no → snap_{idx}.json 互相覆盖）。"""
    if not rid:
        return 0
    with _META_LOCK:
        m = _load_meta(rid)
        calls = m.get("tool_calls") or []
        calls.append(str(tool_name))
        m["tool_calls"] = calls[-200:]
        cnt = int(m.get("call_count") or 0) + 1
        m["call_count"] = cnt
        _save_meta(rid, m)
        return cnt


def tool_rollback(rid: str, rel: str, content: str, tool_name: str = "",
                  cfg=None, round_no: int = 0, existed: bool = True) -> Optional[str]:
    """轮内文件级快照：第 round_no 次工具调用前被改文件的旧内容。

    保留最近 round_keep_rollback 次工具调用点（不断覆盖最新安全快照）。
    existed=False 表示该文件原本不存在（新建），回退时应删除而非写成空文件。
    返回该调用点快照路径。"""
    if not rid:
        return None
    cfg = cfg or get_config()
    keep = max(1, int(getattr(cfg, "round_keep_rollback", 2)))
    try:
        d = _round_dir(rid) / "rollback"
        d.mkdir(parents=True, exist_ok=True)
        idx = int(round_no)
        if idx <= 0:
            # v8.5.x 审查修复：round_no<=0 会静默覆盖 snap_0 回退点；回退到当前 call_count 兜底
            idx = int((_load_meta(rid).get("call_count") or 0))
        if idx <= 0:
            return None  # 仍无有效序号，拒绝写，避免覆盖 snap_0
        p = d / f"snap_{idx}.json"
        data = {"tool": tool_name or "", "ts": _now(), "files": {rel: content}}
        if not existed:
            data["absent"] = [rel]
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        # 保留最近 keep 个调用点，删更旧的
        snaps = sorted(d.glob("snap_*.json"),
                       key=lambda x: int(x.stem.split("_")[1]))
        for old in snaps[:-keep]:
            old.unlink(missing_ok=True)
        return str(p)
    except Exception:
        return None


def rollback_dir_path(rid: str) -> Path:
    """轮内目录回退备份目录（存被删目录的 zip，供回退时恢复）。"""
    return _round_dir(rid) / "rollback" / "dirs"


def tool_rollback_dir(rid: str, rel: str, zip_path: str, tool_name: str = "",
                      round_no: int = 0) -> Optional[str]:
    """轮内目录级回退：记录被删目录的 zip 备份路径，回退时可整目录还原。

    v8.7 审查修复：zip 路径以「相对 rollback 目录」存盘（"dirs/xxx.zip"），
    工作区/项目整体移动后回退点仍然有效；绝对路径只在恢复时按快照目录解析。
    """
    if not rid:
        return None
    try:
        d = _round_dir(rid) / "rollback"
        d.mkdir(parents=True, exist_ok=True)
        idx = int(round_no)
        if idx <= 0:
            idx = int((_load_meta(rid).get("call_count") or 0))
        if idx <= 0:
            return None
        try:
            zp = Path(zip_path)
            if zp.is_absolute():
                stored = os.path.relpath(zp, d).replace("\\", "/")
            else:
                stored = str(zp).replace("\\", "/")
        except Exception:
            stored = str(zip_path).replace("\\", "/")
        p = d / f"snap_{idx}.json"
        data = {"tool": tool_name or "", "ts": _now(), "dirs": {rel: stored}}
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(p)
    except Exception:
        return None


def rollback_points(rid: str) -> list:
    """当前轮可回退的工具调用点：[{round_no, tool, ts, files:[rel...]}] 新→旧。"""
    if not rid:
        return []
    try:
        d = _round_dir(rid) / "rollback"
        if not d.exists():
            return []
        out = []
        for p in sorted(d.glob("snap_*.json"),
                        key=lambda x: int(x.stem.split("_")[1])):
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "round_no": int(p.stem.split("_")[1]),
                "tool": data.get("tool", ""),
                "ts": data.get("ts", ""),
                "files": list((data.get("files") or {}).keys()),
                "path": str(p),
            })
        out.reverse()  # 新→旧
        return out
    except Exception:
        return []


def restore_rollback_point(rid: str, round_no: int, ws: str) -> tuple[bool, str]:
    """把某次工具调用点前的文件内容写回。返回 (ok, note)。"""
    if not rid:
        return False, "无会话"
    try:
        p = _round_dir(rid) / "rollback" / f"snap_{int(round_no)}.json"
        if not p.exists():
            return False, f"回退点 {round_no} 不存在"
        data = json.loads(p.read_text(encoding="utf-8"))
        root = Path(ws)
        absent = set(data.get("absent") or [])
        # 先全量校验再落盘：任何一项越界/非法即整体拒绝，避免半恢复残留（与 restore_session 对齐）
        for rel, _content in (data.get("files") or {}).items():
            target = (root / rel).resolve()
            if target != root and root not in target.parents:
                return False, f"路径越界: {rel}"
        for rel in absent:
            target = (root / rel).resolve()
            if target != root and root not in target.parents:
                return False, f"路径越界: {rel}"
        for rel, zip_path in (data.get("dirs") or {}).items():
            target = (root / rel).resolve()
            if target != root and root not in target.parents:
                return False, f"路径越界: {rel}"
            try:
                zp = Path(zip_path)
                if zp.is_absolute():
                    zp = zp.resolve()
                else:
                    zp = (_round_dir(rid) / "rollback" / zip_path).resolve()
                rb = (_round_dir(rid) / "rollback").resolve()
                if rb not in zp.parents and zp != rb:
                    return False, f"回退包路径越界: {zip_path}"
            except Exception:
                return False, f"回退包路径非法: {zip_path}"
            if not zp.exists():
                continue
            base = target.resolve()
            try:
                with zipfile.ZipFile(zp) as zf:
                    for m in zf.namelist():
                        dest = (target / m).resolve()
                        if base not in dest.parents and dest != base:
                            return False, f"回退包路径越界: {m}"
            except Exception:
                return False, f"回退包损坏: {zip_path}"
        for rel, content in (data.get("files") or {}).items():
            target = (root / rel).resolve()
            if rel in absent:
                # 原本不存在的文件（新建后被改/删），回退=删除该文件，而非写成空文件
                target.unlink(missing_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # v8.14：newline="" 精确写，回退内容不再被隐式行尾翻译改写
            with open(target, "w", encoding="utf-8", newline="") as f:
                f.write(str(content))
        for rel, zip_path in (data.get("dirs") or {}).items():
            target = (root / rel).resolve()
            zp = Path(zip_path)
            if zp.is_absolute():
                zp = zp.resolve()
            else:
                zp = (_round_dir(rid) / "rollback" / zip_path).resolve()
            if not zp.exists():
                continue
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zp) as zf:
                zf.extractall(target)
        try:
            from . import audit as _audit
            _audit.audit_log("rollback_point", str(rid),
                             f"回退到第 {round_no} 次工具调用前（{data.get('tool','')}），"
                             f"涉及 {len(data.get('files', {}))} 个文件 + {len(data.get('dirs', {}))} 个目录",
                             actor="user")
        except Exception:
            pass
        return True, f"已回退到第 {round_no} 次工具调用前（{data.get('tool','')}）"
    except Exception as e:
        return False, f"回退失败: {e}"


def _iter_zip_files(root: Path):
    """遍历打包目录，产出 (abs_path, arcname)。"""
    for p in root.rglob("*"):
        if p.is_symlink() or not p.is_file():
            continue
        rel = p.relative_to(root)
        top = rel.parts[0] if rel.parts else ""
        if top in EXCLUDE_TOP or p.suffix.lower() in EXCLUDE_SUFFIX:
            continue
        yield p, str(rel).replace("\\", "/")


def pack_workspace(rid: str, ws: str) -> float:
    """把工作区（排除备份/data 等）压缩到 {round}/snapshot.zip。返回大小 MB。"""
    root = Path(ws)
    try:
        zp = _round_dir(rid) / "snapshot.zip"
        count = 0
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p, arc in _iter_zip_files(root):
                try:
                    zf.write(p, arc)
                    count += 1
                except OSError:
                    continue
        size_mb = round(zp.stat().st_size / 1024 / 1024, 2)
        m = _load_meta(rid)
        m["size_mb"] = size_mb
        m["zip_file_count"] = count
        _save_meta(rid, m)
        return size_mb
    except Exception as e:
        log_error(f"工作区快照打包失败 {rid}", e)
        return 0.0


def _gc_sessions(cfg=None) -> None:
    """总上限淘汰：所有 committed 轮 zip 总大小超上限删最旧（保留当前 active 轮）。"""
    if is_readonly():
        return  # v8.3 报警只读：停止一切快照清理
    cfg = cfg or get_config()
    limit_mb = max(50, int(getattr(cfg, "session_snapshot_max_mb", 3072)))
    try:
        rows = []
        for m in SESSION_DIR.glob("*/meta.json"):
            try:
                meta = load_json(m, {})
            except Exception:
                continue
            rid = meta.get("round_id", m.parent.name)
            size = float(meta.get("size_mb") or 0)
            rows.append({"rid": rid, "ts": meta.get("ts", ""), "size": size,
                         "status": meta.get("status", ""), "dir": m.parent})
        active = {r["rid"] for r in rows if r["status"] == "active"}
        committed = [r for r in rows if r["status"] == "committed"]
        committed.sort(key=lambda x: x["ts"])
        total = sum(r["size"] for r in rows)
        for r in committed:
            if total <= limit_mb:
                break
            total -= r["size"]
            delete_session(r["rid"])
    except Exception as e:
        log_error("快照 GC 失败", e)


def commit_round(rid: str, ws: str, cfg=None, title: str = "",
                 todo_text: str = "", devlog_text: str = "", tree: dict = None,
                 pack: bool = True, keep_rollback: bool = False) -> dict:
    """回复完成时提交：生成标题（调用方传入或兜底）、记录 todo/devlog/tree、
    打包 zip（tree_update_at_round_end=True 时）、清理本轮多次 rollback、上限淘汰。

    返回 meta。任务完成后本轮多次快照清除（删除，不留回收站）。
    v8.3 报警只读态：停止一切清理（不删 rollback、不淘汰旧快照），
    保留回退点供用户选择恢复。keep_rollback=True（取消/出错）同样保留回退点。"""
    cfg = cfg or get_config()
    if not rid:
        return {}
    m = _load_meta(rid)
    m["todo"] = todo_text or m.get("todo", "")
    m["devlog"] = devlog_text or m.get("devlog", "")
    if tree:
        m["tree"] = tree
    if title:
        m["title"] = title
    if pack and getattr(cfg, "tree_update_at_round_end", True):
        m["size_mb"] = pack_workspace(rid, ws)
    m["status"] = "committed"
    _save_meta(rid, m)
    # v8.3 报警只读：停止快照清理，保留回退点供用户选择
    if is_readonly() or keep_rollback:
        return m
    # 任务完成：清除本轮全部回退快照（含目录 zip，删除，不留回收站）
    try:
        rb = _round_dir(rid) / "rollback"
        if rb.exists():
            shutil.rmtree(rb, ignore_errors=True)
    except Exception:
        pass
    _gc_sessions(cfg)
    return m


def list_sessions() -> list:
    """所有快照轮次 [{round_id, ts, title, size_mb, status, tool_calls}] 新→旧。"""
    out = []
    try:
        if not SESSION_DIR.exists():
            return out
        for m in SESSION_DIR.glob("*/meta.json"):
            try:
                meta = load_json(m, {})
            except Exception:
                continue
            out.append({
                "round_id": meta.get("round_id", m.parent.name),
                "ts": meta.get("ts", ""),
                "title": meta.get("title", ""),
                "size_mb": float(meta.get("size_mb") or 0),
                "status": meta.get("status", ""),
                "tool_calls": meta.get("tool_calls") or [],
                "input": meta.get("input", "")[:2000],
                "owner": meta.get("owner", ""),  # P1-3：会话归属（网页版多用户隔离用）
            })
        out.sort(key=lambda x: x["ts"], reverse=True)
    except Exception:
        pass
    return out


def get_session(round_id: str) -> dict:
    return _load_meta(round_id)


def delete_session(round_id: str) -> bool:
    """删除某轮快照（含 zip/rollback/meta）。非法 id（如 ".." 净化后为空）一律拒绝。"""
    rid = str(round_id or "")
    if not rid or _safe(rid) != rid:
        return False
    try:
        d = _round_dir(rid)
        if d.exists():
            # v8.5.x 审查修复：rglob+unlink 无法删子目录（rollback/），改用 rmtree，
            # 否则空目录壳永久残留并随 _gc_sessions 不断累积。
            import shutil
            shutil.rmtree(d, ignore_errors=True)
        return True
    except Exception:
        return False


def restore_session(round_id: str, ws: str) -> tuple[bool, str]:
    """解压快照 zip 恢复工作区（防 zip-slip：先全量校验再落盘）。"""
    zp = _round_dir(round_id) / "snapshot.zip"
    if not zp.exists():
        return False, "该快照无工作区打包（可能未启用打包）"
    root = Path(ws).resolve()
    restored = 0
    try:
        with zipfile.ZipFile(zp) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            # P2-20：先全量校验条目，再落盘，避免越界时半恢复残留
            targets = []
            for info in infos:
                name = info.filename.replace("\\", "/")
                target = (root / name).resolve()
                if target != root and root not in target.parents:
                    return False, f"快照含越界路径: {name}"
                targets.append((info, target))
            for info, target in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                restored += 1
        try:
            from . import audit as _audit
            _audit.audit_log("restore_session", str(round_id),
                             f"从任务快照恢复工作区，写回 {restored} 个文件", actor="user")
        except Exception:
            pass
        return True, f"已从快照恢复工作区（{restored} 个文件）"
    except Exception as e:
        return False, f"恢复失败: {e}"


def generate_title(rid: str, cfg=None, llm_fn=None) -> str:
    """AI 生成一句话标题（上一轮更新摘要）。llm_fn(cfg, messages) -> str 可注入。"""
    m = _load_meta(rid)
    calls = m.get("tool_calls") or []
    if llm_fn:
        try:
            msg = [{"role": "system", "content":
                        "用一句话（≤30字）概括这轮对话对工作区的更新，只输出标题。"},
                   {"role": "user", "content":
                        f"用户输入: {(m.get('input') or '')[:500]}\n工具调用: {', '.join(calls[:30])}"}]
            title = llm_fn(cfg, msg)
            if title and len(str(title).strip()) > 0:
                return str(title).strip()[:60]
        except Exception:
            pass
    # 兜底
    first = calls[0] if calls else "编辑"
    return f"{first} ({_now()[9:13]})"  # v8.14：原 [8:12] 截出 "_153" 怪后缀，改为 HHMM
