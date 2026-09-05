"""v8.25 用户文件保护 + 非Git自动备份Worktree增强 + 一键全量备份 + 重名治理。

背景（用户需求）：
1. 非Git环境下加强文件自动备份（WorkTree=checkpoint/session_snap/dep_tree/audit，不依赖git）。
2. PPT/Excel/Word/PDF等用户改过的文件：AI不允许执行相关命令或用工具覆盖，必须提示并要授权。
3. 用户一键备份完整工作区。
4. 多个文件名不清楚/重复时（copilot+worktree发现奇怪点）自动要求识别、备份/转移。

设计：
- USER_SUFFIXES：视为"用户手工资产"的二进制/办公后缀。默认即受保护（用户可能在外部改过），
  AI文本工具（write/edit）一律拒绝（会损坏二进制），run_command触碰到也拦截。
- 用户修改感知：data/file_protect.json记录 {rel: {size, mtime, actor}}；AI经工具写入后记
  actor=ai；scan时若文件mtime/size与记录不一致且actor=ai，说明人类在外部改过→标记
  user_modified=true。此后AI写/命令触碰必须被拦，要求用户一键备份+明确授权。
- 一键全量备份：backup_workspace_full(ws) 把工作区（不含backups/.git/缓存）打包到
  backups/<ts>_full.zip，返回路径与文件数。调用方（工具/桥/GUI按钮）共用此函数。
- 重名/不清治理：detect_ambiguous(ws) 按"归一化词干"分组 + 不清命名正则，返回
  [{group, files, reason}]；quarantine_files(ws, files) 移入 backups/quarantine/<ts>/ 保留原相对路径。
- v8.26 工作副本（禁碰语义=拷贝出去改，原内容不修改）：make_workcopy(ws, rel) 把工作区文件
  按「时间-作者-内容」拷贝为 workcopy/ 下副本；workcopy/ 内 AI 产物豁免用户资产保护。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Optional

from .config import DATA_DIR
from .errors import log_error
from .storage import load_json, save_json

STATE_PATH = DATA_DIR / "file_protect.json"

# ---- 用户手工资产后缀（AI文本工具禁止直接覆盖）----
USER_SUFFIXES = {
    ".ppt", ".pptx", ".pot", ".potx", ".pps", ".ppsx", ".odp",
    ".xls", ".xlsx", ".xlsm", ".csv", ".ods",
    ".doc", ".docx", ".odt", ".rtf", ".wps",
    ".pdf",
    ".psd", ".ai", ".sketch", ".fig",
    ".mp4", ".mov", ".avi", ".mkv",
    ".zip", ".rar", ".7z",
}

# ---- 不清/重复文件名模式 ----
UNCLEAR_PATTERNS = [
    (re.compile(r"\(\d+\)"), "带(数字)后缀，疑似重复下载/复制"),
    (re.compile(r"副本|复件|copy of|copy\b", re.IGNORECASE), "含'副本/copy'字样，疑似重复"),
    (re.compile(r"新建|未命名|untitled|untitled-|new |新しい", re.IGNORECASE), "新建/未命名，语义不清"),
    (re.compile(r"~\$|^~"), "Office临时锁文件残留"),
    (re.compile(r"\.bak$|\.tmp$|\.old$", re.IGNORECASE), "备份/临时后缀残留"),
    (re.compile(r"^\s*$"), "空文件名"),
]

COPY_MARKER_RE = re.compile(
    r"(\(\d+\)|\s*[-_ ]?(副本|复件|copy(\s*of)?)( ?\d+)?\s*)$", re.IGNORECASE)
# v8.25修复：旧版另带 |\d+ 会把"v820/v821/v822"版号尾数也剥掉，导致dev_log版本史
# 全被误判为重复组。纯数字尾缀（file1/file2）多为合法序列，不做归一，只认
# "(数字)"/副本/copy类明确复制标记。

# ---- v8.26 工作副本 ----
WORKCOPY_DIR = "workcopy"

# v8.26 阶段3：工作副本不得成为敏感数据外带通道（禁读防线同源）
_WORKCOPY_DENY_TOP = {"backups", "data", "sessions", ".git", ".worktrees",
                      "__pycache__", "node_modules", ".venv", "venv"}
_WORKCOPY_DENY_NAME = {"config.json", "err.log", "api.txt", "api_keys.py", ".env"}


def _is_workcopy(rel: str) -> bool:
    """v8.26：workcopy/ 目录内的文件是 AI 经审批创建的工作副本，豁免用户资产保护。

    仅当路径确实位于 workcopy/ 之内（无 .. 穿越、无盘符冒号）才豁免，
    防 "workcopy/../报告.pptx" 借首段绕过用户资产保护（v8.26 阶段3 修复）。
    """
    r = str(rel or "")
    parts = [x for x in r.replace("\\", "/").split("/") if x]
    return (bool(parts) and parts[0].lower() == WORKCOPY_DIR
            and ".." not in parts and ":" not in r)


def _norm_stem(name: str) -> str:
    stem = Path(name).stem.strip().lower()
    stem = COPY_MARKER_RE.sub("", stem).strip(" -_()（）")
    stem = re.sub(r"\s+", "", stem)
    return stem or name.lower()


def is_user_asset(rel: str) -> bool:
    if _is_workcopy(rel):
        return False
    return Path(str(rel or "")).suffix.lower() in USER_SUFFIXES


# ---- 状态存取 ----
def _load_state() -> dict:
    try:
        return load_json(STATE_PATH, {}) or {}
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        save_json(STATE_PATH, st)
    except Exception as e:
        log_error("file_protect 状态保存失败", e)


def note_ai_write(ws: str, rel: str) -> None:
    """AI经工具成功写入后调用：记录指纹，actor=ai。"""
    try:
        p = Path(ws) / str(rel or "").strip().replace("\\", "/")
        if not p.is_file():
            return
        st = _load_state()
        files = st.setdefault("files", {})
        try:
            s = p.stat()
        except OSError:
            return
        files[str(rel).replace("\\", "/")] = {
            "size": s.st_size, "mtime": s.st_mtime,
            "actor": "ai", "user_modified": False,
            "ts": datetime.datetime.now().strftime("%Y%m%d_%H%M%S"),
        }
        _save_state(st)
    except Exception:
        pass


def sync_user_modified(ws: str) -> list:
    """扫描用户资产文件，对比指纹发现人类外部修改。返回新标记为user_modified的rel列表。

    规则：记录actor=ai但当前size/mtime变化 → 人类改过 → user_modified=true。
    无记录的老文件（用户历史资产）→ 补记 actor=human, user_modified=true（默认受保护）。
    """
    root = Path(ws)
    if not root.exists():
        return []
    st = _load_state()
    files = st.setdefault("files", {})
    changed = []
    try:
        for p in root.rglob("*"):
            try:
                if not p.is_file() or p.is_symlink():
                    continue
                rel = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            top = rel.split("/", 1)[0]
            if top in ("backups", "data", "sessions", ".git", "__pycache__",
                       "node_modules", ".venv", "venv", "workcopy"):
                continue
            if not is_user_asset(rel):
                continue
            try:
                s = p.stat()
            except OSError:
                continue
            rec = files.get(rel)
            if rec is None:
                files[rel] = {"size": s.st_size, "mtime": s.st_mtime,
                              "actor": "human", "user_modified": True,
                              "ts": datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}
                changed.append(rel)
            elif rec.get("actor") == "ai" and not rec.get("user_modified"):
                if rec.get("size") != s.st_size or abs(float(rec.get("mtime") or 0) - s.st_mtime) > 1.0:
                    rec["user_modified"] = True
                    rec["actor"] = "human"
                    rec["size"] = s.st_size
                    rec["mtime"] = s.st_mtime
                    changed.append(rel)
    except Exception as e:
        log_error("file_protect 同步失败", e)
    if changed:
        _save_state(st)
    return changed


def check_ai_write_block(ws: str, rel: str) -> Optional[str]:
    """AI写/删/改名前调用。返回拦截理由，None=放行。

    - 用户资产后缀：文本工具一律拦截（二进制会被utf-8损坏 + 用户手工成果）。
    - 普通文本文件：若state标记user_modified且开关保护开，同样提示先备份/授权？
      本轮只对用户资产后缀硬拦截，文本文件走原有"审批期间外部修改"竞态保护，避免误伤代码流。
    """
    r = str(rel or "").strip().replace("\\", "/")
    if not r:
        return "路径为空"
    if is_user_asset(r):
        # 同步一次，确保刚被用户改过的文件即时受保护
        try:
            sync_user_modified(ws)
        except Exception:
            pass
        st = _load_state()
        rec = (st.get("files") or {}).get(r)
        if rec and rec.get("actor") == "ai" and not rec.get("user_modified"):
            # AI自己刚生成、用户没碰过：允许继续用工具改（仍建议二进制走命令桥手动处理）
            return None
        return (f"[用户文件保护] {r} 疑似用户手工资产（Office/PDF/二进制），"
                "AI不允许用write_file/edit_file直接覆盖（会损坏格式且丢失用户修改）。"
                "请先提示用户“一键备份完整工作区”；如确需AI处理该文件内容，"
                "经用户批准后调用 copy_user_asset 生成 workcopy/ 工作副本（原文件不动，只在副本上操作）。")
    return None


def command_touches_user_asset(cmd: str, ws: str) -> list:
    """解析命令串中是否触碰用户资产文件。返回命中的rel列表。"""
    cmd = str(cmd or "")
    if not cmd:
        return []
    root = Path(ws)
    hits = []
    # v8.25修复：token按空白/引号切分（旧正则把"libreoffice a.pptx"整段当文件名，
    # 含空格的路径永远不存在→真实命中被漏过）。取每段basename判后缀。
    toks = re.findall(r"[^\s\"'<>|]+?\.[A-Za-z0-9]{2,5}", cmd)
    cands = set()
    for t in toks:
        t = str(t).strip().strip("\"'")
        if not t:
            continue
        cands.add(t)
        cands.add(t.replace("\\", "/").rsplit("/", 1)[-1])  # basename也候选
    # 再加完整rel包含匹配（命令里写了相对路径）
    try:
        st = _load_state()
        known = set((st.get("files") or {}).keys())
    except Exception:
        known = set()
    for c in list(cands) + list(known):
        c = str(c).strip()
        if not c or not is_user_asset(c):
            continue
        # 取basename在命令中出现，或完整rel出现
        base = c.rsplit("/", 1)[-1]
        if base and base in cmd:
            # 仅当文件真实存在才算命中（防误伤）
            try:
                if (root / c).exists() or (root.rglob(base) and True):
                    # rglob确认存在性（basename级）
                    found = False
                    for _ in root.rglob(base):
                        found = True
                        break
                    if found or (root / c).exists():
                        hits.append(c if (root / c).exists() else base)
            except Exception:
                continue
    return sorted(set(hits))


# ---- v8.26 工作副本：禁碰=拷贝出去改，原内容不修改 ----
def make_workcopy(ws: str, rel: str, actor: str = "AI") -> tuple:
    """把工作区文件拷贝为 workcopy/ 工作副本，原文件永不修改。

    命名（用户裁决：优先已有惯例，无惯例按 时间-作者-内容）：
    workcopy/YYYYMMDD-<actor>-<stem><ext>，冲突追加 -2/-3…
    返回 (ok, info)；成功 info={"rel": 副本相对路径, "src": 原rel}，失败 info=错误文案。
    """
    r = str(rel or "").strip().strip("\"'").replace("\\", "/")
    if not r or r.startswith("/") or ".." in r.split("/") or ":" in r:
        return False, "路径非法（仅允许工作区相对路径）"
    if _is_workcopy(r):
        return False, "该文件已在工作副本目录内，无需再次拷贝"
    parts = [x for x in r.split("/") if x]
    if parts and parts[0].lower() in _WORKCOPY_DENY_TOP:
        return False, "该路径属于系统/敏感目录（data/backups 等），禁止创建工作副本"
    if any(p.lower() in _WORKCOPY_DENY_NAME for p in parts):
        return False, "该文件为敏感文件（密钥/运行数据），禁止创建工作副本"
    src = Path(ws) / r
    if not src.is_file():
        return False, f"文件不存在: {r}"
    try:
        if src.stat().st_size > 200 * 1024 * 1024:
            return False, "文件超过 200MB，不自动创建工作副本"
    except OSError as e:
        return False, f"读取文件失败: {e}"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", src.stem.strip() or "file")[:80]
    actor = "".join(ch for ch in str(actor or "AI").strip()
                    if ch.isalnum() or ch in "_-")[:20] or "AI"
    ts = datetime.datetime.now().strftime("%Y%m%d")
    out_dir = Path(ws) / WORKCOPY_DIR
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        final = f"{ts}-{actor}-{stem}{src.suffix.lower()}"
        n = 1
        while (out_dir / final).exists():
            n += 1
            final = f"{ts}-{actor}-{stem}-{n}{src.suffix.lower()}"
        shutil.copy2(src, out_dir / final)
    except OSError as e:
        log_error("工作副本创建失败", e)
        return False, f"拷贝失败: {e}"
    rel_out = f"{WORKCOPY_DIR}/{final}"
    try:
        from . import audit as _audit
        _audit.audit_log("workcopy", rel_out, f"由 {r} 生成工作副本", actor="ai")
    except Exception:
        pass
    return True, {"rel": rel_out, "src": r}


# ---- 一键全量备份 ----
FULL_EXCLUDE_TOP = {"backups", ".git", "__pycache__", "node_modules",
                    ".venv", "venv", ".idea", ".vscode", ".mypy_cache",
                    ".pytest_cache", "dist", "build"}
FULL_EXCLUDE_SUFFIX = {".pyc", ".pyo", ".tmp"}


def backup_workspace_full(ws: str, label: str = "full") -> tuple[bool, str, int]:
    """一键备份完整工作区到 backups/<ts>_<label>.zip。返回(ok, zip_path, 文件数)。

    包含data/（会话/快照/保护状态随包走，非Git自动备份Worktree的完整性就靠它），
    但排除backups/自身（防递归撑爆）与.git/缓存。
    """
    root = Path(ws)
    if not root.exists():
        return False, "工作区不存在", 0
    bdir = root / "backups"
    try:
        bdir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"备份目录创建失败: {e}", 0
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    zp = bdir / f"{ts}_{label}.zip"
    count = 0
    try:
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p in root.rglob("*"):
                try:
                    if p.is_symlink() or not p.is_file():
                        continue
                    rel = p.relative_to(root)
                except ValueError:
                    continue
                top = rel.parts[0] if rel.parts else ""
                if top in FULL_EXCLUDE_TOP:
                    continue
                if p.suffix.lower() in FULL_EXCLUDE_SUFFIX:
                    continue
                # 跳过本次正在写的zip自身
                try:
                    if p.resolve() == zp.resolve():
                        continue
                except OSError:
                    pass
                try:
                    zf.write(p, str(rel).replace("\\", "/"))
                    count += 1
                except OSError:
                    continue
        try:
            from . import audit as _audit
            _audit.audit_log("backup_full", str(zp.name),
                             f"一键备份完整工作区：{count} 个文件", actor="user")
        except Exception:
            pass
        return True, str(zp), count
    except Exception as e:
        log_error("一键备份失败", e)
        try:
            zp.unlink(missing_ok=True)
        except Exception:
            pass
        return False, f"备份失败: {e}", 0


# ---- 重名/不清检测与隔离 ----
def detect_ambiguous(ws: str) -> list:
    """扫描工作区，返回[{group, files, reason}]。copilot+worktree发现奇怪点即调此函数。

    - 同归一化词干多文件（去副本标记/空格/大小写后相同）→ 重复嫌疑。
    - 命中UNCLEAR_PATTERNS → 语义不清。
    同文件可同时命中多条，按group聚合去重。
    """
    root = Path(ws)
    out = []
    if not root.exists():
        return out
    try:
        sync_user_modified(ws)
    except Exception:
        pass
    by_stem: dict[str, list] = {}
    unclear: list = []
    try:
        for p in root.rglob("*"):
            try:
                if p.is_symlink() or not p.is_file():
                    continue
                rel = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            top = rel.split("/", 1)[0]
            if top in ("backups", "data", "sessions", ".git", "__pycache__",
                       "node_modules", ".venv", "venv", "workcopy"):
                continue
            # v8.25修复：跳过各级__pycache__与.pyc（编译产物多版本共存是常态，非用户重名）
            if "__pycache__" in Path(rel).parts or p.suffix.lower() in (".pyc", ".pyo"):
                continue
            name = p.name
            stem = _norm_stem(name) + "|" + p.suffix.lower()
            by_stem.setdefault(stem, []).append(rel)
            for rx, why in UNCLEAR_PATTERNS:
                if rx.search(name):
                    unclear.append({"rel": rel, "reason": why})
                    break
    except Exception as e:
        log_error("重名扫描失败", e)
        return out
    seen = set()
    for stem, files in by_stem.items():
        if len(files) > 1:
            # v8.25修复：同目录并排放两份=高度疑似重复；跨目录同名多为三端同源/
            # 图标同步/dev_log归档等已知架构，措辞降级为"确认即可"，免误报打扰。
            parents = {f.rsplit("/", 1)[0] if "/" in f else "." for f in files}
            bases = {f.rsplit("/", 1)[-1].lower() for f in files}
            if len(parents) == 1:
                reason = ("同目录重复（高度疑似）：同一目录下出现多份归一化同名文件（"
                          + "、".join(sorted(bases)[:6]) + "），请确认保留哪个、其余备份/转移")
            else:
                reason = ("跨目录同名（可能是三端同源/图标同步/归档关系，确认一致即可）："
                          + "、".join(sorted(bases)[:6]))
            out.append({"group": stem.split("|")[0][:40] or stem[:40],
                        "files": sorted(files), "reason": reason})
            seen.update(files)
    # 语义不清但未进重复组的，单条成组（要求用户识别）
    for u in unclear:
        if u["rel"] not in seen:
            out.append({"group": u["rel"].rsplit("/", 1)[-1][:40],
                        "files": [u["rel"]], "reason": "命名不清：" + u["reason"]})
    out.sort(key=lambda x: (-len(x["files"]), x["group"]))
    return out[:200]


def quarantine_files(ws: str, rels: list, reason: str = "") -> tuple[bool, str, list]:
    """把指定文件移入 backups/quarantine/<ts>/（保留原相对路径），返回(ok, dir, moved)。"""
    root = Path(ws)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    qdir = root / "backups" / "quarantine" / ts
    moved = []
    try:
        qdir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"隔离目录创建失败: {e}", []
    for rel in rels or []:
        r = str(rel or "").strip().replace("\\", "/")
        if not r or r in (".", "/", "\\"):
            continue
        try:
            src = (root / r).resolve()
        except Exception:
            continue
        if src != root and root not in src.parents:
            continue
        if not src.exists() or not src.is_file():
            continue
        dst = qdir / r
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append(r)
        except OSError:
            continue
    try:
        note = qdir / "_README.txt"
        note.write_text(f"隔离时间: {ts}\n原因: {reason or '重名/命名不清，用户确认隔离'}\n文件:\n"
                        + "\n".join(moved), encoding="utf-8")
    except Exception:
        pass
    try:
        from . import audit as _audit
        _audit.audit_log("quarantine", ts, f"隔离 {len(moved)} 个文件：{reason[:100]}", actor="user")
    except Exception:
        pass
    # 同步保护状态：被隔离的不再参与拦截
    try:
        st = _load_state()
        files = st.get("files") or {}
        for r in moved:
            files.pop(r, None)
        st["files"] = files
        _save_state(st)
    except Exception:
        pass
    return True, str(qdir), moved


def protect_status(ws: str) -> dict:
    """供面板/工具展示：受保护文件数 + 最近同步发现的人类修改。"""
    try:
        fresh = sync_user_modified(ws)
    except Exception:
        fresh = []
    st = _load_state()
    files = st.get("files") or {}
    owned = sorted(files.keys())
    return {"protected_count": len(owned), "protected": owned[:200],
            "newly_modified": fresh, "suffixes": sorted(USER_SUFFIXES)}
