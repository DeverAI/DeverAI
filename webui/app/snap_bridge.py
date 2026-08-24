"""网页版快照桥（v8.5 对齐批次1）— 把桌面版可靠性能力暴露给浏览器端 Agent。

复用 desktop 模块（同机进程内）：
- desktop/checkpoint.py   文件级版本快照（write/edit 前自动备份 + 版本回退）
- desktop/session_snap.py 任务级会话快照（一轮对话=标题+工作区zip+轮内回退点+只读报警）
- desktop/dep_tree.py     依赖树（import 扫描 + 大小下限校验 + 校验问题注入）

网页版独立维护只读报警状态（不干扰桌面 GUI 的 session_snap._READONLY），
其余复用桌面实现，保证两端行为一致。

鉴权：所有端点要求登录；工作区限定在命令桥授权目录（bridge_workspace）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import zipfile
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import current_user
from .config import get_config
from .bridge import _workspace, _resolve, _is_protected, _is_read_protected
from .errors import log_error

router = APIRouter(prefix="/api/bridge", tags=["snap"])

# 网页版独立的只读报警/激活轮次状态（按用户隔离，防多用户/多标签互锁）
# 结构：{ username: {"readonly": bool, "reason": str, "round_id": str} }
# v8.14：OrderedDict 支持真实 LRU 淘汰
_STATE = OrderedDict()
_STATE_LOCK = threading.Lock()

# P2-7：checkpoint/save 后端直读的文本大小上限（防整读超大文件 OOM）
_CK_MAX_BYTES = 50 * 1024 * 1024


def _user_state(user: dict) -> dict:
    """取用户状态（惰性建项；命中即移到末尾，实现真实 LRU）。"""
    name = str((user or {}).get("username") or "anon")
    with _STATE_LOCK:
        st = _STATE.get(name)
        if st is not None:
            # v8.14：命中项 move_to_end，此前注释称 LRU 实为 FIFO，
            # 极端情况下活跃用户的 readonly 状态会被误淘汰
            try:
                _STATE.move_to_end(name)
            except AttributeError:
                pass
            return st
        # v8.13：多用户长期运行上限（LRU：淘汰最久未用项）
        if len(_STATE) >= 5000:
            try:
                _STATE.pop(next(iter(_STATE)), None)
            except StopIteration:
                pass
        st = {"readonly": False, "reason": "", "round_id": ""}
        _STATE[name] = st
        return st


def _is_readonly(user: dict) -> bool:
    return bool(_user_state(user).get("readonly"))


def _active_round(user: dict) -> str:
    return str(_user_state(user).get("round_id") or "")


def _set_active_round(user: dict, rid: str) -> None:
    _user_state(user)["round_id"] = rid or ""


def _desktop_modules():
    """延迟导入桌面模块（仅 Windows+桌面环境存在；失败给出明确错误）。"""
    try:
        from desktop import checkpoint, session_snap, dep_tree
        return checkpoint, session_snap, dep_tree
    except Exception as e:
        # P2-1：异常详情（含本机绝对路径）落 Err.log，对外只回泛化文案
        log_error("[web] 快照模块导入失败", e)
        raise HTTPException(501, "当前环境不支持快照功能")


def _session_cfg(enabled: bool = True):
    """构造 session_snap 需要的轻量 cfg（覆盖桌面 config 的全局开关）。"""
    return SimpleNamespace(
        ENABLE_SESSION_SNAP=enabled,
        ENABLE_DEP_TREE=True,
        tree_update_at_round_end=True,
        round_keep_rollback=2,
        workspace=str(_workspace()),
    )


def _rel_to(root, path) -> str:
    """绝对路径 → 相对展示路径（防泄露 Windows 用户名/目录结构到响应）。"""
    try:
        return str(Path(str(path)).resolve().relative_to(Path(str(root)).resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path) or ""


def _redact_ws(text: str) -> str:
    """把输出中的工作区绝对路径替换为 ~（同时覆盖正斜杠/反斜杠两种写法）。"""
    try:
        ws = str(_workspace())
    except Exception:
        return str(text or "")
    out = str(text or "")
    for cand in (ws, ws.replace("\\", "/"), ws.replace("/", "\\")):
        if cand:
            out = out.replace(cand, "~")
    return out


def _check_owner(snap, rid: str, user: dict):
    """校验会话归属：owner 为空（旧数据）或属于当前用户才放行。"""
    if not str(rid or "") or len(str(rid)) > 64:
        raise HTTPException(404, "会话不存在")
    m = snap.get_session(rid)
    if not m:
        raise HTTPException(404, "会话不存在")
    name = str((user or {}).get("username") or "anon")
    owner = str(m.get("owner") or "")
    if owner and owner != name:
        raise HTTPException(403, "无权访问他人会话")
    return m


def _write_owner(snap, rid: str, name: str):
    """begin 后把归属写入 meta（P1-3 会话按用户隔离）。"""
    try:
        with snap._META_LOCK:
            m = snap._load_meta(rid)
            m["owner"] = name
            snap._save_meta(rid, m)
    except Exception as e:
        # v8.13：归属写失败不再静默吞——落 Err.log（自动存错机制），会话建立本身不阻塞
        log_error(f"[web] 会话归属写失败 {rid}", e)


def _reject_protected_rel(rel: str, for_read: bool = False) -> None:
    """快照/回退涉及的 rel 必须与文件桥同一套保护矩阵（P0-1/P0-2 修复）。"""
    try:
        p = _resolve(rel)
    except HTTPException:
        raise
    root = _workspace()
    if _is_read_protected(p, root):
        raise HTTPException(403, "该路径属于受保护数据，禁止快照/回退")
    if not for_read and _is_protected(p, root):
        raise HTTPException(403, "该路径属于系统受保护文件，禁止回退覆写")


def _rel_is_read_protected(rel: str, root) -> bool:
    """列表过滤用：越界/受保护路径一律过滤，绝不外露。"""
    try:
        return _is_read_protected(_resolve(rel), root)
    except HTTPException:
        return True


# ---------------------------------------------------------------------------
# 只读报警（Guard）
# ---------------------------------------------------------------------------
@router.get("/guard")
async def get_guard(user: dict = Depends(current_user)):
    st = _user_state(user)
    with _STATE_LOCK:
        return {"ok": True, "readonly": st["readonly"], "reason": st["reason"]}


@router.post("/guard")
async def set_guard(body: dict, user: dict = Depends(current_user)):
    st = _user_state(user)
    raw = body.get("on")
    # v8.13：拒绝 "false" 字符串被 bool() 判为 True 的歧义输入
    if isinstance(raw, bool):
        on = raw
    else:
        on = str(raw or "").strip().lower() in ("1", "true", "yes", "on")
    with _STATE_LOCK:
        st["readonly"] = on
        st["reason"] = str(body.get("reason") or "")[:200] if st["readonly"] else ""
    return {"ok": True, "readonly": st["readonly"], "reason": st["reason"]}


# ---------------------------------------------------------------------------
# 文件级 Checkpoint（版本快照回退）
# ---------------------------------------------------------------------------
class CkSaveBody(BaseModel):
    path: str          # 相对工作区路径
    content: str = ""  # 写入新内容前的原文件内容（可为空串）
    task_id: str = ""
    source: str = "ai"


@router.post("/checkpoint/save")
async def ck_save(body: CkSaveBody, user: dict = Depends(current_user)):
    checkpoint, _, _ = _desktop_modules()
    rel = str(body.path or "").strip().replace("\\", "/")
    if not rel:
        raise HTTPException(400, "path 不能为空")
    # v8.13：元数据长度上限（防 checkpoint meta 被写成巨物）
    if len(body.task_id or "") > 128 or len(body.source or "") > 32:
        raise HTTPException(400, "task_id/source 过长")
    if len(body.content or "") > 10 * 1024 * 1024:
        raise HTTPException(400, "content 过大")
    p = _resolve(rel)  # 越界校验 + 取真实路径
    # P0-1：checkpoint 保存/恢复与文件桥同源——data/backups 与密钥文件禁止被复制/外带
    _reject_protected_rel(rel, for_read=True)
    # P2-7：前端内容为空时由后端直接读原文件（绕过浏览器端 2MB 读取上限）
    content = body.content
    if not content and p.exists():
        if p.stat().st_size > _CK_MAX_BYTES:
            # 超大文件：整读有 OOM 风险，跳过备份（写操作不阻塞）
            return {"ok": True, "bak": None, "note": "文件过大，跳过快照"}
        try:
            raw = p.read_bytes()
            # 二进制/非 UTF-8 文件：utf-8 解码会生成 U+FFFD 损坏备份，跳过
            if b"\x00" in raw[:4096]:
                return {"ok": True, "bak": None, "note": "二进制文件，跳过快照"}
            content = raw.decode("utf-8", errors="replace")
        except Exception:
            content = ""
    if not content and not p.exists():
        # 新文件无可快照内容，静默跳过（与桌面 save_checkpoint 语义一致）
        return {"ok": True, "bak": None}
    try:
        bak = await asyncio.to_thread(
            checkpoint.save_checkpoint, rel, content,
            body.task_id, body.source or "ai")
    except Exception:
        raise HTTPException(500, "保存 checkpoint 失败")
    # P1-1（查修）：bak 返回相对 CHECKPOINT_DIR 路径，防泄露绝对路径/用户名
    if bak:
        bak = _rel_to(checkpoint.CHECKPOINT_DIR, bak)
    return {"ok": True, "bak": bak}


@router.get("/checkpoint/files")
async def ck_files(user: dict = Depends(current_user)):
    checkpoint, _, _ = _desktop_modules()
    try:
        files = await asyncio.to_thread(checkpoint.list_files)
    except Exception:
        raise HTTPException(500, "读取 checkpoint 列表失败")
    # P0-1：受保护路径的 checkpoint 不外露（历史遗留记录静默过滤）
    root = _workspace()
    files = [
        f for f in files
        if not _rel_is_read_protected(f.get("rel_path", ""), root)
    ]
    # 补充 source_name 展示名（对齐桌面版）+ bak_path 相对化（P1-1 防路径泄露）
    for f in files:
        for v in f.get("versions") or []:
            v["source_name"] = checkpoint.source_name(v.get("source", "ai"))
            v["bak_path"] = _rel_to(checkpoint.CHECKPOINT_DIR, v.get("bak_path", ""))
    return {"ok": True, "files": files}


@router.get("/checkpoint/versions")
async def ck_versions(path: str = "", user: dict = Depends(current_user)):
    checkpoint, _, _ = _desktop_modules()
    if not path:
        raise HTTPException(400, "path 不能为空")
    rel = str(path).strip().replace("\\", "/")
    _reject_protected_rel(rel, for_read=True)
    try:
        vers = await asyncio.to_thread(checkpoint.list_versions, rel)
    except Exception:
        raise HTTPException(500, "读取版本列表失败")
    # P1-1（查修）：bak_path 相对化
    for v in vers:
        v["bak_path"] = _rel_to(checkpoint.CHECKPOINT_DIR, v.get("bak_path", ""))
    return {"ok": True, "versions": vers}


class CkRestoreBody(BaseModel):
    bak_path: str      # checkpoint 绝对路径（仅允许来自 data/checkpoints）
    path: str = ""     # 目标相对路径（空=从 meta 读）


@router.post("/checkpoint/restore")
async def ck_restore(body: CkRestoreBody, user: dict = Depends(current_user)):
    checkpoint, _, _ = _desktop_modules()
    rel = str(body.path or "").strip().replace("\\", "/")
    # 校验 bak_path 必须在 checkpoints 目录内（防任意文件读取）
    root = Path(str(checkpoint.CHECKPOINT_DIR)).resolve()
    raw = str(body.bak_path or "")
    # P1-2：前端回传的是 ck_files/ck_versions 相对 CHECKPOINT_DIR 的路径——
    # Path(raw).resolve() 会把相对路径按服务器 CWD 解析（恒得到绝对路径），
    # 导致原「is_absolute 判断」恒真、重拼逻辑成为死代码、恢复请求恒 403。
    # 修复：用 os.path.isabs 判断相对/绝对，相对路径拼 root 再 resolve 校验。
    if os.path.isabs(raw):
        bak = Path(raw).resolve()
    else:
        bak = (root / raw).resolve()
    # v8.13：bak_path 必须是 CHECKPOINT_DIR 内的具体文件，目录本身也拒绝
    if root not in bak.parents:
        raise HTTPException(403, "bak_path 越界")
    # P0-1：目标原文件路径（请求或 meta）同样必须过读保护，禁止经 restore 取回密钥/数据
    if not rel:
        try:
            meta_path = Path(str(bak) + ".meta")
            for line in meta_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("rel_path="):
                    rel = line.split("=", 1)[1].strip()
                    break
        except Exception:
            rel = ""
    if rel:
        _reject_protected_rel(rel, for_read=True)
    try:
        ok, content = await asyncio.to_thread(
            checkpoint.restore_checkpoint, str(bak), rel)
    except Exception:
        raise HTTPException(500, "读取 checkpoint 失败")
    if not ok:
        raise HTTPException(404, _redact_ws(str(content)))
    # 恢复前先对当前文件快照（防后悔，可二次回滚）—— 由前端写入新内容前自行调用 save
    return {"ok": True, "content": content, "rel": rel}


# ---------------------------------------------------------------------------
# 任务级会话快照（session_snap）
# ---------------------------------------------------------------------------
@router.post("/sessions/begin")
async def sess_begin(body: dict, user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    raw_enabled = body.get("enabled", True)
    enabled = raw_enabled if isinstance(raw_enabled, bool) else \
        str(raw_enabled or "").strip().lower() in ("1", "true", "yes", "on")
    rid = await asyncio.to_thread(snap.begin_round, _session_cfg(enabled))
    if rid:  # P1-3（查修）：记录会话归属，多用户部署下互不可见/互不可操作
        name = str((user or {}).get("username") or "anon")
        await asyncio.to_thread(_write_owner, snap, rid, name)
    _set_active_round(user, rid or "")
    return {"ok": True, "round_id": rid, "readonly": _is_readonly(user)}


@router.post("/sessions/{rid}/input")
async def sess_input(rid: str, body: dict, user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    _check_owner(snap, rid, user)  # P1-3：归属校验
    text = str(body.get("input") or "")
    if len(text) > 200_000:
        raise HTTPException(400, "输入过长")
    await asyncio.to_thread(snap.set_round_input, rid, text)
    return {"ok": True}


@router.post("/sessions/{rid}/tool_call")
async def sess_tool_call(rid: str, body: dict, user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    _check_owner(snap, rid, user)  # P1-3：归属校验
    tool = str(body.get("tool") or "").strip()
    if not tool or len(tool) > 200:
        raise HTTPException(400, "tool 不能为空且长度 ≤200")
    cnt = await asyncio.to_thread(snap.add_tool_call, rid, tool)
    return {"ok": True, "count": cnt}


class RollbackBody(BaseModel):
    rel: str
    content: str = ""
    tool_name: str = ""
    round_no: int = 0


@router.post("/sessions/{rid}/rollback_point")
async def sess_rollback_point(rid: str, body: RollbackBody, user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    _check_owner(snap, rid, user)  # P1-3：归属校验
    if not body.rel or not body.rel.strip():
        raise HTTPException(400, "rel 不能为空")
    if not 1 <= int(body.round_no or 0) <= 10_000:
        raise HTTPException(400, "round_no 非法")
    if len(body.content or "") > 10 * 1024 * 1024:
        raise HTTPException(400, "content 过大")
    if len(body.tool_name or "") > 200:
        raise HTTPException(400, "tool_name 过长")
    p = _resolve(body.rel)
    # P0-2：回退点路径与文件桥同源保护，禁止把受保护路径写入可回退快照
    _reject_protected_rel(body.rel, for_read=False)
    # v8.12：旧内容由后端直读（前端传空串时），与 checkpoint/save 同语义——
    # 免去浏览器端二次回传大文件；existed 由后端按文件是否存在判定（新建文件回退=删除）
    existed = p.exists()
    content = body.content
    readable = True
    if not content and existed:
        try:
            if p.stat().st_size > _CK_MAX_BYTES:
                readable = False  # 超大文件跳过内容
            else:
                raw = p.read_bytes()
                if b"\x00" in raw[:4096]:
                    readable = False  # 二进制跳过内容
                else:
                    content = raw.decode("utf-8", errors="replace")
        except Exception:
            readable = False
    # P2-1：文件存在但内容不可读（超大/二进制/IO 异常）时跳过回退点——
    # 记录空内容会让回退把原文件清空（数据丢失），与 ck_save「跳过备份」语义一致
    if existed and not readable and not content:
        return {"ok": True, "path": "", "note": "文件不可读（超大/二进制），跳过回退点"}
    try:
        out = await asyncio.to_thread(
            snap.tool_rollback, rid, body.rel, content,
            body.tool_name, _session_cfg(), body.round_no, existed)
    except Exception:
        raise HTTPException(500, "保存回退点失败")
    # P1-1（查修）：path 相对 SESSION_DIR 返回
    return {"ok": True, "path": _rel_to(snap.SESSION_DIR, out) if out else ""}


@router.get("/sessions/{rid}/rollback_points")
async def sess_rollback_points(rid: str, user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    _check_owner(snap, rid, user)  # P1-3：归属校验
    pts = await asyncio.to_thread(snap.rollback_points, rid)
    # P1-1（查修）：points[].path 相对化
    for p in pts or []:
        if p.get("path"):
            p["path"] = _rel_to(snap.SESSION_DIR, p["path"])
    return {"ok": True, "points": pts}


@router.post("/sessions/{rid}/rollback/{round_no}")
async def sess_restore_rollback(rid: str, round_no: int, user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    _check_owner(snap, rid, user)  # P1-3：归属校验
    if not 1 <= int(round_no) <= 10_000:
        raise HTTPException(400, "round_no 非法")
    # P0-2：恢复前逐 rel 校验保护矩阵，任何受保护目标命中即整体 403（fail-closed）
    try:
        rp = Path(snap._round_dir(rid)) / "rollback" / f"snap_{int(round_no)}.json"
        if rp.exists():
            data = json.loads(rp.read_text(encoding="utf-8"))
            for rel in list((data.get("files") or {}).keys()) + \
                    list((data.get("absent") or [])) + \
                    list((data.get("dirs") or {}).keys()):
                _reject_protected_rel(str(rel), for_read=False)
    except HTTPException:
        raise
    except Exception as e:
        log_error(f"[web] 回退点校验失败 rid={rid} round={round_no}", e)
        raise HTTPException(400, "回退点数据无效")
    ok, msg = await asyncio.to_thread(
        snap.restore_rollback_point, rid, round_no, str(_workspace()))
    if not ok:
        raise HTTPException(400, _redact_ws(str(msg)))
    # P1-1（查修）：output 可能含绝对路径 → 替换工作区路径脱敏
    return {"ok": True, "output": _redact_ws(str(msg))}


class CommitBody(BaseModel):
    title: str = ""
    todo: str = ""
    devlog: str = ""
    pack: bool = True
    keep_rollback: bool = False


@router.post("/sessions/{rid}/commit")
async def sess_commit(rid: str, body: CommitBody, user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    _check_owner(snap, rid, user)  # P1-3：归属校验
    # v8.13：快照元数据长度上限（防单请求撑爆 meta.json）
    if len(body.title or "") > 2000 or len(body.todo or "") > 50_000 or len(body.devlog or "") > 50_000:
        raise HTTPException(400, "快照元数据过长")
    # P1-4：commit 时保留轮内回退点取决于当前 guard 是否仍处于只读态
    #（AI 报告问题后整个轮次都在只读，提交后仍保留回退点便于用户复检回退）
    keep = bool(body.keep_rollback) or _is_readonly(user)
    try:
        m = await asyncio.to_thread(
            snap.commit_round, rid, str(_workspace()), _session_cfg(),
            body.title, body.todo, body.devlog, None, body.pack, keep)
    except Exception:
        raise HTTPException(500, "提交快照失败")
    finally:
        # P2-1/P2-6：仅清空属于本轮的激活轮（防并发/交错 commit 误清他轮）；异常路径也清理
        if _active_round(user) == rid:
            _set_active_round(user, "")
    return {"ok": True, "meta": m}


@router.get("/sessions")
async def sess_list(user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    sessions = await asyncio.to_thread(snap.list_sessions)
    # P1-3（查修）：按 owner 过滤（owner 为空的旧数据视为可见，不阻断迁移期）
    name = str((user or {}).get("username") or "anon")
    sessions = [s for s in sessions
                if not (s.get("owner") or "") or s.get("owner") == name]
    return {"ok": True, "sessions": sessions, "active": _active_round(user)}


@router.get("/sessions/{rid}")
async def sess_get(rid: str, user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    m = _check_owner(snap, rid, user)  # P1-3：归属校验
    if not m:
        raise HTTPException(404, "会话不存在")
    return {"ok": True, "session": m}


@router.delete("/sessions/{rid}")
async def sess_delete(rid: str, user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    _check_owner(snap, rid, user)  # P1-3：归属校验
    ok = await asyncio.to_thread(snap.delete_session, rid)
    if not ok:
        raise HTTPException(500, "删除失败")
    return {"ok": True}


@router.post("/sessions/{rid}/restore")
async def sess_restore(rid: str, user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    _check_owner(snap, rid, user)  # P1-3：归属校验
    # 恢复前逐 zip 条目过保护矩阵：受保护目录/文件命中即整体 403（fail-closed）
    try:
        zp = Path(snap._round_dir(rid)) / "snapshot.zip"
        if zp.exists():
            with zipfile.ZipFile(zp) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    _reject_protected_rel(
                        info.filename.replace("\\", "/"), for_read=False)
    except HTTPException:
        raise
    except Exception as e:
        log_error(f"[web] 会话快照条目校验失败 rid={rid}", e)
        raise HTTPException(400, "快照数据无效")
    ok, msg = await asyncio.to_thread(snap.restore_session, rid, str(_workspace()))
    if not ok:
        raise HTTPException(400, _redact_ws(str(msg)))
    # P1-1（查修）：output 脱敏
    return {"ok": True, "output": _redact_ws(str(msg))}


# ---------------------------------------------------------------------------
# 依赖树（dep_tree）
# ---------------------------------------------------------------------------
# P2-8：tree/scan 短时缓存（60s），避免连续对话每轮全量重扫大工作区
_TREE_CACHE = {}
_TREE_CACHE_TTL = 60.0
_TREE_CACHE_LOCK = threading.Lock()


@router.get("/tree/scan")
async def tree_scan(user: dict = Depends(current_user)):
    _, _, deps = _desktop_modules()
    ws = str(_workspace())
    now = time.time()
    key = f"{ws}|{str((user or {}).get('username') or 'anon')}"
    with _TREE_CACHE_LOCK:
        hit = _TREE_CACHE.get(key)
        if hit and now - hit[0] < _TREE_CACHE_TTL:
            return {"ok": True, "tree": hit[1], "problems": hit[2]}
    try:
        tree = await asyncio.get_running_loop().run_in_executor(
            None, deps.scan_workspace, ws, None)
        problems = await asyncio.get_running_loop().run_in_executor(
            None, deps.check_tree, tree, ws)
    except Exception as e:
        log_error("[web] 依赖树扫描失败", e)
        raise HTTPException(500, "依赖树扫描失败")
    with _TREE_CACHE_LOCK:
        _TREE_CACHE[key] = (now, tree, problems)
        # P2-16（查修）：容量上限，防多用户/多工作区切换后内存缓慢增长
        if len(_TREE_CACHE) > 200:
            expired = [k for k, (t, _, _) in _TREE_CACHE.items() if now - t > _TREE_CACHE_TTL]
            for k in expired:
                _TREE_CACHE.pop(k, None)
            if len(_TREE_CACHE) > 200:
                _TREE_CACHE.clear()
    return {"ok": True, "tree": tree, "problems": problems}


class TreeUpdateBody(BaseModel):
    rel: str
    op: str = "write"


@router.post("/tree/update")
async def tree_update(body: TreeUpdateBody, user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    if not body.rel or not body.rel.strip():
        raise HTTPException(400, "rel 不能为空")
    if body.op not in ("write", "delete"):
        raise HTTPException(400, "op 仅支持 write/delete")
    rid = _active_round(user)
    if not rid:
        return {"ok": True, "note": "无激活轮次，跳过"}
    try:
        # size 由后端读取真实字节数（前端字符数与字节不一致会导致误报）
        _reject_protected_rel(body.rel, for_read=True)
        p = _resolve(body.rel)
        size = p.stat().st_size if p.exists() else 0
        await asyncio.to_thread(snap.update_tree, rid, body.rel, size, body.op)
    except Exception as e:
        # P2-10：不再静默吞错误——失败落 Err.log 并回 ok:False，前端可感知
        log_error(f"[web] tree/update {body.rel}", e)
        return {"ok": False, "note": "依赖树更新失败"}
    return {"ok": True}


class TreeReportBody(BaseModel):
    rel: str
    deps: list = []
    min_size: int = 0


@router.post("/tree/expert_report")
async def tree_expert_report(body: TreeReportBody, user: dict = Depends(current_user)):
    _, _, deps = _desktop_modules()
    # v8.13：输入上限 + 错误脱敏（此前裸回 e，可能泄露本机绝对路径）
    if not body.rel or not body.rel.strip():
        raise HTTPException(400, "rel 不能为空")
    if len(body.deps or []) > 500:
        raise HTTPException(400, "deps 过多")
    if not all(isinstance(d, str) and len(d) <= 500 for d in (body.deps or [])):
        raise HTTPException(400, "deps 元素必须为字符串且长度 ≤500")
    if not 0 <= int(body.min_size or 0) <= 10 * 1024 * 1024 * 1024:
        raise HTTPException(400, "min_size 非法")
    try:
        # 从激活轮读当前树，应用 expert_report 后写回
        from .snap_util import apply_expert_report
        ok, note = await asyncio.to_thread(
            apply_expert_report, _active_round(user), body.rel,
            body.deps, body.min_size)
    except HTTPException:
        raise
    except Exception as e:
        log_error(f"[web] tree/expert_report {body.rel}", e)
        raise HTTPException(500, "专家报告失败")
    if not ok:
        raise HTTPException(400, _redact_ws(str(note)))
    return {"ok": ok, "note": _redact_ws(str(note))}


@router.get("/tree/inject")
async def tree_inject(user: dict = Depends(current_user)):
    _, snap, _ = _desktop_modules()
    rid = _active_round(user)
    inj = await asyncio.to_thread(snap.get_inject, rid) if rid else {}
    return {"ok": True, "inject": inj}
