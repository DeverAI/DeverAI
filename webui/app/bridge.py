"""本地资源桥：浏览器（UI 中的 Agent）无法直接操作本机 OS，
此桥以"被 UI 调用"的方式提供工作区内的命令执行与文件读写（兜底）。

- 所有接口要求登录；命令/文件操作均限定在已授权的工作区（bridge_workspace）内。
- Agent 的决策与工具编排在浏览器端完成，本桥只是被动的 OS 适配层。
- v6.2 相对路径安全：响应永不返回绝对路径，AI 只见 codename + 相对路径。
"""
import asyncio
import base64
import datetime
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from .auth import current_user
from .config import get_config, update_config, DATA_DIR
from .storage import save_text, load_json, save_json
from .codename import workspace_codename
from .errors import log_error
from . import security

router = APIRouter(prefix="/api/bridge", tags=["bridge"])

# v8.13：与 desktop/tools.py 同源的工作区系统目录保护——工作区=项目根时，
# 写/删/改名保护 app/desktop/data/backups/dev_log/updates/static 与 config.json/Err.log；
# v8.13.1 只读保护收窄：仅 data/backups 与密钥文件禁止读，static/app/desktop 源码允许 Agent 查看。
_APP_ROOT = Path(__file__).resolve().parent.parent.parent  # project root (DeverAI/)
PROTECTED_NAMES = {"pyqt", "webui", "lite", "data", "backups", "dev_log", "updates", "tests", "tools"}
PROTECTED_FILES = {"Err.log", "config.json"}
READ_PROTECTED_NAMES = {"data", "backups"}
READ_PROTECTED_FILES = {"Err.log", "config.json"}


def _protected_parts(p: Path, root: Path):
    """返回 (parts, rel_name)；工作区不是项目根或解析失败返回 None。"""
    try:
        root = root.resolve()
        p = p.resolve()
        if root != _APP_ROOT.resolve():
            return None
        rel = p.relative_to(root)
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if not parts:
        return None
    return parts, rel.name


def _is_protected(p: Path, root: Path) -> bool:
    """写/删/改名保护（与 desktop/tools.py::_is_protected 对齐）。"""
    info = _protected_parts(p, root)
    if info is None:
        return False
    parts, name = info
    return parts[0] in PROTECTED_NAMES or name in PROTECTED_FILES


def _is_read_protected(p: Path, root: Path) -> bool:
    """只读保护：数据目录与密钥文件禁止读取；源码目录允许 Agent 查看。"""
    info = _protected_parts(p, root)
    if info is None:
        return False
    parts, name = info
    return parts[0] in READ_PROTECTED_NAMES or name in READ_PROTECTED_FILES


SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".idea", ".vscode", "dist", "build"}
# v6.2 P2-3：文件操作上限（防 DoS）
_MAX_WRITE_SIZE = 10 * 1024 * 1024  # 单文件写入上限 10MB
_MAX_TREE_ENTRIES = 5000            # 目录树条目上限


def _workspace() -> Path:
    cfg = get_config()
    ws = (cfg.bridge_workspace or "").strip()
    if not ws:
        raise HTTPException(409, "尚未授权本地工作区，请在设置中授权")
    p = Path(ws).resolve()
    if not p.is_dir():
        raise HTTPException(409, "工作区目录不存在")  # v6.2：错误信息不暴露绝对路径
    return p


def _readonly_block(user: dict) -> None:
    """网页版 Guard 只读状态与文件桥写/命令端口联动（fail-closed）。"""
    try:
        from .snap_bridge import _is_readonly
        if _is_readonly(user):
            raise HTTPException(423, "AI 只读模式已锁定，写/命令操作被禁止")
    except ImportError:
        pass  # 快照桥不可用时保持原行为（模块缺失属环境问题）


def _redact_ws(text: str) -> str:
    """对外回显前脱敏工作区/家目录绝对路径。"""
    out = str(text or "")
    try:
        candidates = {str(_workspace()), str(Path.home())}
    except Exception:
        return out
    for path in candidates:
        if not path:
            continue
        out = out.replace(path, "~")
        out = out.replace(path.replace("\\", "/"), "~")
        out = out.replace(path.replace("/", "\\"), "~")
    return out


def _strict_bool(v) -> bool:
    """严格布尔解析：bool 直接取；字符串只有 1/true/yes/on 为真（"false"/"0" 为假）。"""
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _resolve(rel: str) -> Path:
    root = _workspace()
    raw = str(rel or ".").strip()
    if len(raw) > 1000:
        raise HTTPException(400, "路径过长")
    p = (root / raw).resolve()
    if p != root and root not in p.parents:
        raise HTTPException(403, "路径越界")  # v6.2：错误信息不暴露 rel（可能含 ..）
    return p


# ------------------------------------------------------------------ #
@router.get("/workspace")
async def get_workspace(user: dict = Depends(current_user)):
    cfg = get_config()
    # v6.2 相对路径安全：响应返回 codename 而非绝对路径；authorized 标志仍真实
    return {
        "authorized": bool(cfg.bridge_workspace),
        "workspace": workspace_codename() if cfg.bridge_workspace else "",
        "allow_ai_delete": cfg.allow_ai_delete,
    }


@router.post("/workspace")
async def set_workspace(body: dict, user: dict = Depends(current_user)):
    path = str(body.get("path") or "").strip()
    if not path:
        raise HTTPException(400, "路径不能为空")
    if len(path) > 1000:
        raise HTTPException(400, "路径过长")
    p = Path(path).resolve()
    if not p.is_dir():
        raise HTTPException(400, "目录不存在")  # v6.2：不回 path
    update_config(bridge_workspace=str(p))
    # 返回 codename 给前端，绝对路径不外泄
    return {"ok": True, "workspace": workspace_codename()}


@router.post("/allow_ai_delete")
async def set_allow_ai_delete(body: dict, user: dict = Depends(current_user)):
    """网页设置页「允许 AI 删除」开关同步到命令桥后端（此前只写 localStorage，后端恒 403）。"""
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须为 JSON 对象")
    update_config(allow_ai_delete=_strict_bool(body.get("allow")))
    return {"ok": True, "allow_ai_delete": get_config().allow_ai_delete}


@router.post("/feature_flags")
async def set_feature_flags(body: dict, user: dict = Depends(current_user)):
    """网页语音助手/完整性开关同步到本地桌面配置（仅允许这两个白名单布尔字段）。"""
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须为 JSON 对象")
    allowed = ("ENABLE_VOICE_ASSISTANT", "ENABLE_INTEGRITY")
    updates = {}
    for key in allowed:
        if key in body:
            updates[key] = _strict_bool(body.get(key))
    if not updates:
        return {"ok": True, "updated": []}
    try:
        from pyqt.desktop.config import update_config as desktop_update_config
        desktop_update_config(**updates)
    except Exception as e:
        log_error("[web] 同步功能开关失败", e)
        raise HTTPException(500, "功能开关同步失败")
    return {"ok": True, "updated": list(updates)}


@router.post("/power_authorized")
async def set_power_authorized(body: dict, user: dict = Depends(current_user)):
    """网页设置页「授权自动执行电源操作」开关同步到服务端（用户显式操作，不是 AI 工具）。"""
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须为 JSON 对象")
    update_config(power_authorized=_strict_bool(body.get("authorized")))
    return {"ok": True, "power_authorized": get_config().power_authorized}


@router.get("/pick")
async def pick_workspace(user: dict = Depends(current_user)):
    """弹出系统文件夹选择对话框（tkinter），用于授权命令桥工作区。"""
    try:
        import tkinter as tk
        from tkinter import filedialog

        def _ask():
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory()
            root.destroy()
            return path

        path = await asyncio.to_thread(_ask)
        if path:
            p = Path(path).resolve()
            update_config(bridge_workspace=str(p))
            # v6.2：返回 codename 给前端，绝对路径不外泄
            return {"ok": True, "workspace": workspace_codename()}
        return {"ok": False, "message": "未选择文件夹"}
    except Exception as e:
        raise HTTPException(500, "无法弹出系统对话框，请手动输入路径")  # v6.2：不回 e（可能含路径）


@router.post("/power")
async def power(body: dict, user: dict = Depends(current_user)):
    """肝完睡觉模式的电源操作（需 power_authorized 授权 + 速率限制）。"""
    cfg = get_config()
    # v6.2 P1-6：后端强制授权检查（前端 sleep_authorized 可被绕过）
    if not cfg.power_authorized:
        raise HTTPException(403, "电源操作未授权")
    # v6.2 P1-6：速率限制（1 次/小时），防误触/滥用
    if not security.check_power_rate(user.get("username", "")):
        raise HTTPException(429, "电源操作过于频繁，请稍后再试")
    action = str(body.get("action") or "shutdown")
    # P2-5：action 白名单校验——任意非法值此前都会落入关机分支，属非预期破坏性动作
    if action not in ("shutdown", "hibernate"):
        raise HTTPException(400, "不支持的电源操作")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        if action == "hibernate":
            # v6.2 P2-9：平台兼容——Windows 用 /h，Linux/macOS 语法不同
            if os.name == "nt":
                subprocess.Popen(["shutdown", "/h"], creationflags=creationflags)
            else:
                raise HTTPException(501, "当前平台不支持休眠")
            security.audit("power", user=user.get("username", ""), detail=action)
            return {"ok": True, "message": "已执行休眠"}
        if os.name == "nt":
            subprocess.Popen(["shutdown", "/s", "/t", "15"], creationflags=creationflags)
        else:
            # Linux/macOS：需 root，通常不可用，返回提示
            raise HTTPException(501, "当前平台不支持关机")
        security.audit("power", user=user.get("username", ""), detail=action)
        return {"ok": True, "message": "已执行关机（15 秒后）"}
    except HTTPException:
        raise
    except Exception:
        # v6.2 P1-6：不回显异常细节（可能含路径/环境信息）
        raise HTTPException(500, "电源操作执行失败")


# ------------------------------------------------------------------ #
@router.post("/run_command")
async def run_command(body: dict, user: dict = Depends(current_user)):
    _readonly_block(user)
    cmd = str(body.get("command") or "").strip()
    if not cmd:
        raise HTTPException(400, "命令为空")
    # v8.13：命令长度上限（与 lite_server 对齐，给出明确 4xx）
    if len(cmd) > 32000:
        raise HTTPException(400, "命令过长")
    # 后端危险命令拦截：命中危险模式且请求未带 danger_ok 显式确认 → 拒绝
    #（前端审批卡确认后置 danger_ok=true；裸调 API 的破坏性命令被拦截）
    if security.is_dangerous_cmd(cmd) and not _strict_bool(body.get("danger_ok")):
        raise HTTPException(403, "危险命令需用户在界面确认后执行")
    cwd = _workspace()
    rel = str(body.get("cwd") or "")
    if rel:
        cwd = _resolve(rel)
        if not cwd.is_dir():
            raise HTTPException(400, "cwd 不是目录")  # v6.2：不回 rel
    try:
        t = float(body.get("timeout") or 120)
        if t != t:  # P2-11：NaN 防护（json.loads 默认接受 NaN，wait_for 对 NaN 行为未定义）
            t = 120
        timeout = min(max(t, 1), 600)
    except (TypeError, ValueError):
        timeout = 120

    async def _kill_tree() -> None:
        """v8.14：Windows 下 proc.kill() 只杀 shell 本体，孙进程（cmd /c start ...）残留；
        改用 taskkill /T /F 杀整棵进程树，失败回退 proc.kill()。"""
        if proc is None or proc.returncode is not None:
            return
        if os.name == "nt" and proc.pid:
            try:
                k = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(proc.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    await asyncio.wait_for(k.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
            except Exception:
                pass
        try:
            if proc.returncode is None:
                proc.kill()
        except ProcessLookupError:
            pass

    async def _gen():
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = None
        try:
            try:
                # v8.14：limit 提升到 1MB——StreamReader 默认 64KB 行上限会让
                # 单行超长输出（如压缩后的 bundle.js）触发 ValueError 断流
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    stdin=asyncio.subprocess.DEVNULL,
                    cwd=str(cwd),
                    creationflags=creationflags,
                    limit=1024 * 1024,
                )
            except Exception:
                # P2-3（查修）：OSError 消息可能含 cwd/命令路径 → 泛化文案防绝对路径泄露
                yield f'data: {json.dumps({"error": "启动失败"}, ensure_ascii=False)}\n\n'
                yield f'data: {json.dumps({"done": True, "rc": -1}, ensure_ascii=False)}\n\n'
                return
            deadline = time.monotonic() + timeout
            timed_out = False
            rc = -1
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), remaining)
                except asyncio.TimeoutError:
                    timed_out = True
                    break
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                yield f'data: {json.dumps({"line": text}, ensure_ascii=False)}\n\n'
            if timed_out:
                await _kill_tree()
            try:
                rc = await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                await _kill_tree()
                # P2-16：二次等待同样限时（对齐 lite_server），极端情况下不挂起生成器
                try:
                    rc = await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    rc = -1
                    timed_out = True
            yield f'data: {json.dumps({"done": True, "rc": rc, "timed_out": timed_out}, ensure_ascii=False)}\n\n'
        except asyncio.CancelledError:
            # v8.14：客户端断开走取消语义——清理后重抛，不在取消路径中 yield
            await _kill_tree()
            if proc is not None and proc.stdout:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
            raise
        except Exception as e:
            # v8.14：通用兜底（对齐 lite_server）——任何异常都保证前端收到 done 事件，
            # 而不是 SSE 流被掐断后以 rc:-1 误报命令失败
            await _kill_tree()
            try:
                from .errors import log_error
                log_error("[web] bridge/run_command 流异常", e)
            except Exception:
                pass
            yield f'data: {json.dumps({"done": True, "rc": -1, "error": "执行异常"}, ensure_ascii=False)}\n\n'
        finally:
            # 内存安全：客户端断开/异常/超时时确保子进程被 kill、管道被关闭
            if proc is not None:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                # 显式关闭 stdout 管道防 fd 泄漏
                if proc.stdout:
                    try:
                        proc.stdout.close()
                    except Exception:
                        pass
                try:
                    # v8.14：等待也限时，防止极端情况下生成器悬挂
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except Exception:
                    pass

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ------------------------------------------------------------------ #
# 文件桥（兜底：浏览器无 File System Access API 时使用）
# ------------------------------------------------------------------ #
@router.get("/fs/tree")
async def fs_tree(path: str = "", user: dict = Depends(current_user)):
    p = _resolve(path)
    # v8.13.1：data/backups 目录本身禁列，根目录列表中隐藏受保护子项（不泄露数据目录结构）
    if _is_read_protected(p, _workspace()):
        raise HTTPException(403, "该目录属于受保护数据，禁止列出")

    def _scan() -> dict:
        if not p.is_dir():
            raise HTTPException(404, "不是目录")  # v6.2：不回 path
        entries = []
        try:
            items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            raise HTTPException(403, "无权限访问该目录")
        except OSError:
            raise HTTPException(500, "读取目录失败")
        root = _workspace()
        items = [it for it in items
                 if it.name not in SKIP_DIRS and not _is_read_protected(it, root)]
        truncated = False
        # v6.2 P2-3：目录树条目上限，防百万文件目录内存暴涨
        if len(items) > _MAX_TREE_ENTRIES:
            items = items[:_MAX_TREE_ENTRIES]
            truncated = True
        for it in items:
            try:
                st = it.stat()
            except OSError:
                st = None
            try:
                is_dir = it.is_dir()
            except OSError:
                is_dir = False
            entries.append({
                "name": it.name,
                "is_dir": is_dir,
                "size": st.st_size if st else 0,
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)) if st else "",
            })
        return {"ok": True, "children": entries, "truncated": truncated}

    # v8.14：iterdir/逐项 stat 是同步磁盘 IO，移入线程池防阻塞事件循环（对齐 grep 先例）
    return await asyncio.to_thread(_scan)


@router.get("/fs/read")
async def fs_read(path: str = "", user: dict = Depends(current_user)):
    p = _resolve(path)
    # v8.13.1：读只受「数据目录/密钥文件」保护，源码目录允许 Agent 查看
    if _is_read_protected(p, _workspace()):
        raise HTTPException(403, "该文件属于受保护数据，禁止读取")

    def _read() -> dict:
        # v8.14：stat/is_file 的 OSError 保护（同步自 lite_server v8.12 修复——
        # Windows 上 ACL 受限/被占用文件此前会裸 500）
        try:
            if not p.is_file():
                raise HTTPException(404, "文件不存在")  # v6.2：不回 path
            size = p.stat().st_size
        except OSError:
            raise HTTPException(404, "文件不存在或不可访问")
        if size > 2 * 1024 * 1024:
            raise HTTPException(413, "文件超过 2MB，编辑器不支持打开")
        try:
            # P2-13：二进制检测——含 NUL 字节按二进制处理，明确提示而非返回 U+FFFD 乱码
            # v8.14：只读前 8KB 探测 NUL，不再为取头部整读全文件
            with open(p, "rb") as f:
                head = f.read(8192)
            if b"\x00" in head:
                raise HTTPException(415, "二进制文件，不支持文本读取")
            content = p.read_text(encoding="utf-8", errors="replace")
        except HTTPException:
            raise
        except OSError:
            raise HTTPException(500, "读取文件失败")
        return {"ok": True, "content": content, "lines": len(content.splitlines()), "size": size}

    # v8.14：同步磁盘 IO 移入线程池
    return await asyncio.to_thread(_read)


@router.post("/fs/write")
async def fs_write(body: dict, user: dict = Depends(current_user)):
    _readonly_block(user)
    rel = str(body.get("path") or "").strip()
    if not rel or rel in (".", "\\", "/"):
        raise HTTPException(400, "path 不能为空或指向工作区根目录")
    p = _resolve(rel)
    if _is_protected(p, _workspace()):
        raise HTTPException(403, "该文件属于系统受保护文件，禁止写入")
    content = str(body.get("content") or "")
    # v6.2 P2-3：写入大小上限，防撑满磁盘
    if len(content.encode("utf-8", errors="ignore")) > _MAX_WRITE_SIZE:
        raise HTTPException(413, "文件内容超过 10MB 上限")
    if p.is_dir():
        raise HTTPException(400, "path 是目录")  # P2-10（查修）：目录写 400，与 lite 版一致
    p.parent.mkdir(parents=True, exist_ok=True)
    save_text(p, content)
    return {"ok": True}


@router.post("/fs/mkdir")
async def fs_mkdir(body: dict, user: dict = Depends(current_user)):
    _readonly_block(user)
    rel = str(body.get("path") or "").strip()
    if not rel or rel in (".", "\\", "/"):
        raise HTTPException(400, "path 不能为空或指向工作区根目录")
    p = _resolve(rel)
    # v8.13.1：mkdir 与写文件同权，系统保护目录不可创建
    if _is_protected(p, _workspace()):
        raise HTTPException(403, "该目录属于系统受保护目录，禁止创建")
    if p.exists() and not p.is_dir():
        raise HTTPException(409, "已存在同名文件")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise HTTPException(500, "创建目录失败")
    return {"ok": True}


@router.post("/fs/delete")
async def fs_delete(body: dict, user: dict = Depends(current_user)):
    _readonly_block(user)
    # v6.2 P1-5：后端强制 allow_ai_delete 检查（前端防护可被绕过）
    cfg = get_config()
    if not cfg.allow_ai_delete:
        raise HTTPException(403, "AI 删除已被禁用")
    p = _resolve(body.get("path", ""))
    # v8.13：禁止删除工作区根目录（"." / "" / 反斜杠都会解析到 root，
    # 此前 allow_ai_delete 开启时可一次清空整个工作区）
    if p == _workspace():
        raise HTTPException(400, "禁止删除工作区根目录")
    if _is_protected(p, _workspace()):
        raise HTTPException(403, "该文件属于系统受保护文件，禁止删除")
    if not p.exists():
        raise HTTPException(404, "不存在")

    def _delete() -> None:
        try:
            if p.is_dir():
                import shutil
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError:
            raise HTTPException(500, "删除失败")  # v6.2：不回 e（可能含路径）

    # v8.14：rmtree 大目录可能耗时数秒到数十秒，移入线程池防冻结整个事件循环
    await asyncio.to_thread(_delete)
    return {"ok": True}


@router.post("/fs/rename")
async def fs_rename(body: dict, user: dict = Depends(current_user)):
    _readonly_block(user)
    old = _resolve(body.get("old", ""))
    new = _resolve(body.get("new", ""))
    if old == _workspace() or new == _workspace():
        raise HTTPException(400, "禁止重命名工作区根目录")
    if _is_protected(old, _workspace()) or _is_protected(new, _workspace()):
        raise HTTPException(403, "该文件属于系统受保护文件，禁止重命名")
    if not old.exists():
        raise HTTPException(404, "源不存在")
    if new.exists():
        raise HTTPException(409, "目标已存在")
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
    except OSError as e:
        log_error(f"[web] fs/rename 失败 old={old.name}", e)
        raise HTTPException(500, "重命名失败")  # v6.2：不回 e
    return {"ok": True}


# ------------------------------------------------------------------ #
# v6.4 Web 端搜索桥：grep / glob（让 Web 端 Agent 拥有和桌面端对等的搜索能力）
# ------------------------------------------------------------------ #
_MAX_GREP_RESULTS = 200          # grep 结果上限
_MAX_GREP_SCAN_LINES = 200000    # grep 扫描行数上限（防超大仓库卡死）
_MAX_GLOB_RESULTS = 1000         # glob 结果上限
_MAX_IMAGE_BYTES = 3 * 1024 * 1024  # v8.4：图片读取上限（与桌面端 MAX_IMG_BYTES 对齐）


# ------------------------------------------------------------------ #
# v8.4 Web 端应用截图 / 视觉审查数据桥（供浏览器端 Agent 自截图确认 UI）
# ------------------------------------------------------------------ #
@router.post("/app_screenshot")
async def app_screenshot(body: dict, user: dict = Depends(current_user)):
    """运行工作区内 Python UI 脚本并截图保存（浏览器端 Agent 无法直接枚举窗口/抓屏，
    必须走后端桥调用本机截图模块 desktop/app_shot.py）。

    body: {script, path, title, timeout, extra_args}
    与桌面端 app_screenshot 工具语义一致：script=脚本相对路径，path=截图保存相对路径(.png/.jpg)。
    """
    _readonly_block(user)
    script = str(body.get("script") or "").strip()
    rel = str(body.get("path") or "").strip()
    if not script:
        raise HTTPException(400, "缺少 script 参数")
    if not rel.lower().endswith((".png", ".jpg", ".jpeg")):
        raise HTTPException(400, "path 必须以 .png/.jpg/.jpeg 结尾")
    sp = _resolve(script)
    if _is_read_protected(sp, _workspace()):
        raise HTTPException(403, "该脚本位于受保护数据目录，禁止运行")
    if not sp.is_file():
        raise HTTPException(404, "脚本不存在")
    p = _resolve(rel)
    # v8.13.1：截图保存与写文件同权，系统保护目录不可写入
    if _is_protected(p, _workspace()):
        raise HTTPException(403, "该路径属于系统受保护目录，禁止写入截图")
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        from pyqt.desktop.app_shot import app_screenshot as _shot
    except ImportError:
        # 仅"模块不存在"归因为环境不支持；模块自身 bug 归 500（区分，避免误报）
        raise HTTPException(501, "当前环境不支持应用截图")
    except Exception:
        raise HTTPException(500, "截图模块加载失败")
    try:
        timeout = float(body.get("timeout") or 30)
        if timeout != timeout:  # NaN 防护：min/max 对 NaN 恒 False 会漏网
            timeout = 30
        timeout = min(max(timeout, 5), 120)
    except (TypeError, ValueError):
        timeout = 30
    extra = body.get("extra_args") or []
    if not isinstance(extra, list):
        raise HTTPException(400, "extra_args 必须是数组")
    if len(extra) > 32:
        raise HTTPException(400, "extra_args 最多 32 个")
    for i, a in enumerate(extra):
        if not isinstance(a, str) or len(a) > 512:
            raise HTTPException(400, f"extra_args[{i}] 必须是字符串且长度 ≤512")
    title = str(body.get("title") or "")
    if len(title) > 200:
        raise HTTPException(400, "title 过长（≤200）")
    try:
        # wait_for 兜底：防止截图子进程极端异常时请求永久悬挂
        r = await asyncio.wait_for(
            asyncio.to_thread(
                _shot, str(sp), str(p), timeout, title, extra_args=extra),
            timeout=timeout + 60)
    except asyncio.TimeoutError:
        raise HTTPException(504, "截图超时")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "截图执行失败")
    return {"ok": bool(r.get("ok")), "output": _redact_ws(r.get("output", "")), "rel": rel}


@router.get("/fs/image")
async def fs_image(path: str = "", user: dict = Depends(current_user)):
    """读取工作区图片为 base64（供浏览器端 ui_review 构造多模态请求）。限 3MB。

    按文件头魔数判定真实格式（PNG/JPEG），防止任意文件借"图片"名义外发；非图片 415。
    """
    if not path:
        raise HTTPException(400, "path 不能为空")
    p = _resolve(path)
    if _is_read_protected(p, _workspace()):
        raise HTTPException(403, "该文件属于受保护数据，禁止读取")
    if not p.is_file():
        raise HTTPException(404, "文件不存在")
    try:
        size = p.stat().st_size
    except OSError:
        raise HTTPException(500, "读取文件信息失败")
    if size > _MAX_IMAGE_BYTES:
        raise HTTPException(413, f"图片超过 {_MAX_IMAGE_BYTES} 字节，无法用于视觉审查")
    import base64
    try:
        data = p.read_bytes()
    except OSError:
        raise HTTPException(500, "读取图片失败")
    head = data[:4]
    if head[:4] == b"\x89PNG":
        mime = "image/png"
    elif head[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    else:
        raise HTTPException(415, "不是有效的 PNG/JPEG 图片，无法用于视觉审查")
    return {"ok": True, "base64": base64.b64encode(data).decode("ascii"),
            "mime": mime, "name": p.name}


@router.get("/fs/grep")
async def fs_grep(
    pattern: str = "",
    path: str = "",
    glob: str = "",
    ignore_case: bool = False,
    line_numbers: bool = True,
    user: dict = Depends(current_user),
):
    """v6.4 Web 端 grep：在工作区内正则搜索文件内容。

    对标桌面端 tool_grep，支持 glob 过滤、忽略大小写、行号显示。
    扫描丢进 executor 避免阻塞 event loop（防灾难性正则）。
    """
    import re as _re
    if not pattern:
        raise HTTPException(400, "pattern 不能为空")
    # P1-3：pattern 长度上限（对齐 lite_server），防灾难性回溯正则的 CPU DoS
    if len(pattern) > 200:
        raise HTTPException(400, "正则表达式过长（上限 200 字符）")
    try:
        rx = _re.compile(pattern, _re.IGNORECASE if ignore_case else 0)
    except _re.error:
        raise HTTPException(400, "正则表达式无效")
    # v8.13：拒绝典型灾难性回溯形态——嵌套量词 (a+)+ 与交替型 (a|aa)+ 都会被拦；
    # 工作线程无法被取消，必须前置阻断 ReDoS
    if (_re.search(r"\([^()]*[+*][^()]*\)[+*]", pattern)
            or _re.search(r"\([^()]*(?:\|[^()]*)+\)[+*{]", pattern)
            or _re.search(r"\([^()]*[+*?][^()]*\)[+*{]", pattern)):
        raise HTTPException(400, "正则包含高风险重复结构，请改写")
    root = _workspace()
    search_root = _resolve(path) if path else root
    # v8.13：显式路径不存在时明确 404（此前 os.walk 对不存在目录静默返回空结果）
    if not search_root.exists():
        raise HTTPException(404, "搜索路径不存在")
    # v8.13.1：data/backups/密钥文件不可 grep（否则可绕过 fs/read 保护抓取密钥）
    if _is_read_protected(search_root, root):
        raise HTTPException(403, "该路径属于受保护数据，禁止搜索")

    def _scan():
        results = []
        scanned = 0
        if search_root.is_file():
            files = [search_root]
        else:
            files = []
            for r, dirs, names in os.walk(search_root):
                dirs[:] = [d for d in dirs
                           if d not in SKIP_DIRS and not _is_read_protected(Path(r) / d, root)]
                for name in names:
                    if glob and not Path(name).match(glob):
                        continue
                    fp = Path(r) / name
                    if _is_read_protected(fp, root):
                        continue
                    files.append(fp)
                    if len(files) > 20000:
                        break
                if len(files) > 20000:
                    break
        for f in files:
            try:
                head = f.read_bytes()[:8192]
                if b"\x00" in head:
                    continue
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for ln, line in enumerate(fh, 1):
                        scanned += 1
                        if scanned > _MAX_GREP_SCAN_LINES:
                            break
                        line_txt = line.rstrip("\n")[:5000]
                        m = rx.search(line_txt)
                        if m:
                            relpath = str(f.relative_to(root)).replace("\\", "/")
                            results.append({
                                "file": relpath,
                                "line": ln,
                                "text": line.rstrip()[:300],
                            })
                            if len(results) >= _MAX_GREP_RESULTS:
                                return results, scanned
            except OSError:
                continue
            if scanned > _MAX_GREP_SCAN_LINES:
                break
        return results, scanned

    try:
        results, scanned = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(None, _scan), timeout=30)
    except asyncio.TimeoutError:
        # 超时兜底：工作线程仍在后台跑（受行数上限约束），请求侧不再等待
        raise HTTPException(408, "搜索超时（正则过于复杂或文件过多）")
    except (OSError, ValueError):
        raise HTTPException(500, "搜索执行失败")
    return {"ok": True, "matches": results, "count": len(results), "scanned_lines": scanned}


@router.get("/fs/glob")
async def fs_glob(
    pattern: str = "**/*",
    path: str = "",
    user: dict = Depends(current_user),
):
    """v6.4 Web 端 glob：按文件名模式匹配工作区内文件。

    对标桌面端 tool_glob，支持 ** 和 * 通配符。
    """
    root = _workspace()
    search_root = _resolve(path) if path else root
    if len(pattern or "") > 500:
        raise HTTPException(400, "glob pattern 过长")
    if not search_root.exists():
        raise HTTPException(404, "搜索路径不存在")
    if _is_read_protected(search_root, root):
        raise HTTPException(403, "该路径属于受保护数据，禁止匹配")

    def _match():
        from fnmatch import fnmatch
        out = []
        # v8.13：改用 os.walk 并在遍历时剪掉 SKIP_DIRS（rglob 会完整走进 node_modules 等
        # 巨型目录，大工作区 glob 明显变慢）；v8.13.1 同时剪掉受保护数据目录
        for r, dirs, names in os.walk(search_root):
            dirs[:] = [d for d in dirs
                       if d not in SKIP_DIRS and not _is_read_protected(Path(r) / d, root)]
            for name in names:
                p = Path(r) / name
                if not p.is_file() or _is_read_protected(p, root):
                    continue
                rel = str(p.relative_to(search_root)).replace("\\", "/")
                if fnmatch(rel, pattern):
                    out.append(rel)
                    if len(out) >= _MAX_GLOB_RESULTS:
                        return out
        return out

    try:
        files = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(None, _match), timeout=30)
    except asyncio.TimeoutError:
        raise HTTPException(408, "glob 搜索超时（文件过多）")
    except (OSError, ValueError):
        raise HTTPException(500, "glob 执行失败")
    return {"ok": True, "files": files, "count": len(files)}


# ------------------------------------------------------------------ #
# v8.5.7 补齐：联网搜索 + 无头浏览器（对齐桌面 web_search / browser_read / browser_screenshot）
# ------------------------------------------------------------------ #
_MAX_SEARCH_RESULTS = 10


async def _valid_bridge_url(url: str) -> bool:
    """URL 白名单校验（http/https、长度受限、禁空格/控制字符、禁内网/环回防 SSRF）。

    v8.13：域名解析在后台线程执行（DNS 是阻塞调用，同步解析会卡住事件循环）。
    """
    import re
    import socket
    from urllib.parse import urlparse
    from .proxy import _is_private_nonloopback, _is_loopback, _normalize_numeric_ip
    url = (url or "").strip()
    if not url or len(url) > 2048:
        return False
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return False
    if " " in url or any(ch in url for ch in "\r\n\t"):
        return False
    try:
        p = urlparse(url)
        p.port  # 触发非法端口 ValueError
        host = (p.hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    # 数值形态 host（127.1/0x7f000001/2130706433）先归一化，命中则按 IP 判定，不走 DNS
    norm = _normalize_numeric_ip(host)
    if norm:
        host = norm
    infos = None
    try:
        import ipaddress
        ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except OSError:
            return False
    # SSRF：拒绝环回/内网/链路本地/未指定地址（与 LLM 代理 SSRF 防护一致，FreqErr #72）
    if _is_private_nonloopback(host, infos) or _is_loopback(host, infos):
        return False
    return True


@router.post("/web_search")
async def web_search(body: dict, user: dict = Depends(current_user)):
    """DuckDuckGo 零 key 联网搜索（复用 desktop/search_tools）。"""
    query = str(body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query 为空")
    if len(query) > 500:
        raise HTTPException(400, "query 过长")
    try:
        limit = min(max(int(body.get("limit") or 6), 1), _MAX_SEARCH_RESULTS)
    except (TypeError, ValueError):
        limit = 6
    try:
        from pyqt.desktop.search_tools import _search_duckduckgo
        results = await _search_duckduckgo(query, limit=limit)
    except Exception as e:
        # v6.2：泛化错误，不回显异常细节（可能含环境信息）；详情落 Err.log
        log_error("[web] DuckDuckGo 搜索失败", e)
        raise HTTPException(502, "搜索失败，请稍后重试")
    if not results:
        return {"ok": True, "results": [], "channel": "DuckDuckGo", "note": "无结果"}
    return {"ok": True, "results": results, "channel": "DuckDuckGo"}


@router.post("/browser_read")
async def browser_read(body: dict, user: dict = Depends(current_user)):
    """无头抓取网页正文（复用 desktop/browser，零第三方依赖）。"""
    url = str(body.get("url") or "").strip()
    if not await _valid_bridge_url(url):
        raise HTTPException(400, "URL 无效")
    try:
        timeout = min(max(int(body.get("timeout") or 30), 5), 90)
    except (TypeError, ValueError):
        timeout = 30
    try:
        from pyqt.desktop.browser import browser_read as _br
        result = await asyncio.to_thread(_br, url, timeout)
    except Exception as e:
        log_error("浏览器抓取失败", e)
        raise HTTPException(502, "浏览器抓取失败")
    # 失败文案可能含本机绝对路径/浏览器 stderr，统一泛化回显（FreqErr #76）
    if not isinstance(result, dict) or not result.get("ok"):
        return {"ok": False, "output": "浏览器抓取失败（可能是无可用浏览器或页面无法访问）。"}
    return result


@router.post("/browser_screenshot")
async def browser_screenshot(body: dict, user: dict = Depends(current_user)):
    """无头截图保存到工作区（复用 desktop/browser）。"""
    url = str(body.get("url") or "").strip()
    rel = str(body.get("path") or "").strip()
    if not await _valid_bridge_url(url):
        raise HTTPException(400, "URL 无效")
    if not rel:
        raise HTTPException(400, "缺少 path")
    if not rel.lower().endswith((".png", ".jpg", ".jpeg")):
        raise HTTPException(400, "path 必须以 .png/.jpg/.jpeg 结尾")
    p = _resolve(rel)
    # v8.13.1：截图保存与写文件同权，系统保护目录不可写入
    if _is_protected(p, _workspace()):
        raise HTTPException(403, "该路径属于系统受保护目录，禁止写入截图")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise HTTPException(500, "创建目录失败")
    try:
        timeout = min(max(int(body.get("timeout") or 30), 5), 90)
    except (TypeError, ValueError):
        timeout = 30
    try:
        from pyqt.desktop.browser import browser_screenshot as _bs
        result = await asyncio.to_thread(_bs, url, str(p), timeout)
    except Exception as e:
        log_error("截图失败", e)
        raise HTTPException(502, "截图失败")
    # 失败文案可能含本机绝对路径/浏览器 stderr，统一泛化回显
    if not isinstance(result, dict) or not result.get("ok"):
        return {"ok": False, "output": "截图失败（可能是无可用浏览器或页面无法访问）。"}
    return result


# ------------------------------------------------------------------ #
# v8.9 自动化工具/资料检修桥：复用 desktop/toolsmith.py 与 vault 检修逻辑
# ------------------------------------------------------------------ #
async def _desktop_cfg(body: dict, request: Request = None):
    """由前端请求体构造临时 desktop Config（不落盘）。前端把 api_base/api_key/model 随请求传入。

    SSRF 防护：api_base 与 LLM 代理同一套校验（禁内网，环回由 llm_allow_loopback 开关控制）。
    """
    try:
        from dataclasses import replace as _replace
        from pyqt.desktop.config import get_config as _get_desktop_cfg
    except Exception:
        raise HTTPException(500, "桌面模块不可用")
    base = _get_desktop_cfg()
    cfg = _replace(base)
    api_base = str(body.get("api_base") or "").strip()
    if api_base:
        if request is None:
            raise HTTPException(400, "缺少请求上下文")
        from .proxy import _validate_base
        api_base = await _validate_base(api_base, request)
        cfg.api_base_url = api_base.rstrip("/")
    if str(body.get("api_key") or "").strip():
        key = str(body.get("api_key") or "").strip()
        if len(key) > 4096:
            raise HTTPException(400, "api_key 过长")
        cfg.api_key = key
    if str(body.get("model") or "").strip():
        model = str(body.get("model") or "").strip()
        if len(model) > 200:
            raise HTTPException(400, "model 过长")
        cfg.model = model
    return cfg


@router.post("/toolsmith/list")
async def toolsmith_list(body: dict, user: dict = Depends(current_user)):
    """列出已过审的自研工具。"""
    try:
        from pyqt.desktop.config import get_config as _get_desktop_cfg
        if not getattr(_get_desktop_cfg(), "ENABLE_TOOLSMITH", True):
            return {"ok": False, "tools": [], "count": 0, "output": "自研工具库未开启（ENABLE_TOOLSMITH）。"}
        from pyqt.desktop.toolsmith import list_active_tools
        tools = await asyncio.to_thread(list_active_tools)
    except HTTPException:
        raise
    except Exception as e:
        log_error("[web] 自研工具列表失败", e)
        raise HTTPException(500, "自研工具库不可用")
    return {"ok": True, "tools": tools, "count": len(tools)}


@router.post("/toolsmith/build")
async def toolsmith_build(body: dict, request: Request, user: dict = Depends(current_user)):
    """构建自研工具：查重 → LLM 设计 → 审核 → 过了才入库。"""
    requirement = str(body.get("requirement") or "").strip()
    if not requirement:
        raise HTTPException(400, "requirement 为空")
    if len(requirement) > 2000:
        raise HTTPException(400, "requirement 过长")
    try:
        from pyqt.desktop.config import get_config as _get_desktop_cfg
        if not getattr(_get_desktop_cfg(), "ENABLE_TOOLSMITH", True):
            raise HTTPException(403, "自研工具库未开启（ENABLE_TOOLSMITH）。")
        from desktop import toolsmith
        from pyqt.desktop.experts import finalize_tool_build
        cfg = await _desktop_cfg(body, request)
        dup = await asyncio.to_thread(toolsmith.dedup_report, requirement)
        top = await asyncio.to_thread(toolsmith.find_duplicates, requirement, 1)
        if top and top[0]["score"] >= 0.75:
            t = top[0]["tool"]
            return {"ok": True, "duplicate": t,
                    "output": f"查重命中：已有自研工具 {t.get('name')}，建议直接 use_tool 复用。"}
        spec = await toolsmith.build_tool_via_llm(cfg, requirement, dup)
    except HTTPException:
        raise
    except Exception as e:
        log_error("工具构建失败", e)
        raise HTTPException(502, "工具构建失败，请检查模型配置或稍后重试")
    try:
        msg = await finalize_tool_build(cfg, spec, requirement, dup)
    except Exception as e:
        log_error("工具审核入库失败", e)
        raise HTTPException(502, "工具审核入库失败")
    return {"ok": msg.startswith("✅") or "已入库" in msg, "output": msg,
            "tool_name": spec.get("name")}


@router.post("/toolsmith/use")
async def toolsmith_use(body: dict, request: Request, user: dict = Depends(current_user)):
    """执行一个自研工具（prompt 型）。"""
    name = str(body.get("name") or "").strip()
    inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
    if not name:
        raise HTTPException(400, "name 为空")
    if len(name) > 80:
        raise HTTPException(400, "name 过长")
    try:
        from pyqt.desktop.config import get_config as _get_desktop_cfg
        if not getattr(_get_desktop_cfg(), "ENABLE_TOOLSMITH", True):
            raise HTTPException(403, "自研工具库未开启（ENABLE_TOOLSMITH）。")
        from desktop import toolsmith
        cfg = await _desktop_cfg(body, request)
        r = await toolsmith.use_tool_with_feedback(cfg, name, inputs)
    except Exception as e:
        log_error("自研工具执行失败", e)
        raise HTTPException(502, "自研工具执行失败")
    return r


@router.post("/toolsmith/bugs")
async def toolsmith_bugs(body: dict, user: dict = Depends(current_user)):
    """列出待修工具 bug。"""
    try:
        from pyqt.desktop.config import get_config as _get_desktop_cfg
        if not getattr(_get_desktop_cfg(), "ENABLE_TOOL_DOCTOR", True):
            return {"ok": False, "bugs": [], "count": 0, "output": "工具医生未开启（ENABLE_TOOL_DOCTOR）。"}
        from pyqt.desktop.toolsmith import load_tool_bugs
        bugs = await asyncio.to_thread(load_tool_bugs, "pending")
    except HTTPException:
        raise
    except Exception as e:
        log_error("[web] 工具 bug 列表失败", e)
        raise HTTPException(500, "工具 bug 库不可用")
    return {"ok": True, "bugs": bugs, "count": len(bugs)}


@router.post("/toolsmith/fix")
async def toolsmith_fix(body: dict, request: Request, user: dict = Depends(current_user)):
    """工具医生批量修复。"""
    try:
        limit = min(max(int(body.get("limit") or 5), 1), 20)
    except (TypeError, ValueError):
        limit = 5
    try:
        from pyqt.desktop.config import get_config as _get_desktop_cfg
        if not getattr(_get_desktop_cfg(), "ENABLE_TOOL_DOCTOR", True):
            raise HTTPException(403, "工具医生未开启（ENABLE_TOOL_DOCTOR）。")
        from desktop import toolsmith
        cfg = await _desktop_cfg(body, request)
        r = await toolsmith.fix_tool_bugs(cfg, limit=limit)
    except Exception as e:
        log_error("工具修复失败", e)
        raise HTTPException(502, "工具修复失败")
    return r


@router.post("/toolsmith/clear_bug")
async def toolsmith_clear_bug(body: dict, user: dict = Depends(current_user)):
    """手动标记单条 bug 已修。"""
    bug_id = str(body.get("bug_id") or "").strip()
    if not bug_id:
        raise HTTPException(400, "bug_id 为空")
    try:
        from pyqt.desktop.config import get_config as _get_desktop_cfg
        if not getattr(_get_desktop_cfg(), "ENABLE_TOOL_DOCTOR", True):
            raise HTTPException(403, "工具医生未开启（ENABLE_TOOL_DOCTOR）。")
        from pyqt.desktop.toolsmith import clear_tool_bug
        ok = await asyncio.to_thread(clear_tool_bug, bug_id)
    except HTTPException:
        raise
    except Exception as e:
        log_error("[web] 标记工具 bug 失败", e)
        raise HTTPException(500, "工具 bug 库不可用")
    return {"ok": ok}


@router.post("/assets/inspect")
async def assets_inspect(body: dict, user: dict = Depends(current_user)):
    """检查资产银行记录完整性。"""
    try:
        from pyqt.desktop.config import get_config as _get_desktop_cfg
        if not getattr(_get_desktop_cfg(), "ENABLE_VAULT", True):
            raise HTTPException(403, "资产银行未开启（ENABLE_VAULT）。")
        from pyqt.desktop.vault import Vault
        r = await asyncio.to_thread(Vault().inspect_assets)
    except HTTPException:
        raise
    except Exception as e:
        log_error("[web] 资产检查失败", e)
        raise HTTPException(500, "资产银行不可用")
    return r


@router.post("/assets/repair")
async def assets_repair(body: dict, user: dict = Depends(current_user)):
    """批量修复资产银行可自动修复的问题。"""
    raw = body.get("dry_run", False)
    dry = raw if isinstance(raw, bool) else str(raw).lower() == "true"
    try:
        from pyqt.desktop.config import get_config as _get_desktop_cfg
        if not getattr(_get_desktop_cfg(), "ENABLE_VAULT", True):
            raise HTTPException(403, "资产银行未开启（ENABLE_VAULT）。")
        try:
            from desktop import session_snap as _snap
            if _snap.is_readonly():
                raise HTTPException(423, "项目处于只读保护中，禁止修复资产")
        except HTTPException:
            raise
        except Exception as e:
            # v8.13：只读检查失败必须 fail-closed（此前吞掉后放行修复，保护形同虚设）
            log_error("[web] 资产修复只读检查失败", e)
            raise HTTPException(423, "只读状态检查失败，禁止修复资产")
        from pyqt.desktop.vault import Vault
        r = await asyncio.to_thread(Vault().repair_assets, dry_run=dry)
    except HTTPException:
        raise
    except Exception as e:
        log_error("资产修复失败", e)
        raise HTTPException(500, "资产修复失败")
    return r


# ==================== 语音助手「小龙」数据端点 ====================
# 读写 data/voice_tasks.json / voice_constraints.json / notepad.json
# 所有端点受 ENABLE_VOICE_ASSISTANT 与登录态双重保护

VOICE_TASKS_PATH = DATA_DIR / "voice_tasks.json"
VOICE_CONSTRAINTS_PATH = DATA_DIR / "voice_constraints.json"
_VOICE_TASK_LOCK = asyncio.Lock()
_VOICE_MAX_TASKS = 500
_VOICE_MAX_CONSTRAINTS = 200


def _voice_check_enabled():
    """检查语音助手开关，未开启则 403。"""
    try:
        from pyqt.desktop.config import get_config as _get_cfg
        if not getattr(_get_cfg(), "ENABLE_VOICE_ASSISTANT", False):
            raise HTTPException(403, "语音助手未开启（ENABLE_VOICE_ASSISTANT）")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(403, "无法读取语音助手配置")


def _load_voice_json(path: Path, default):
    data = load_json(path, default)
    return data if isinstance(data, (dict, list)) else default


def _save_voice_json(path: Path, data) -> None:
    save_json(path, data)


def _gen_voice_id() -> str:
    return "vt_" + secrets.token_hex(6)


def _now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


@router.get("/voice/tasks")
async def voice_get_tasks(user: dict = Depends(current_user)):
    """获取语音任务列表。"""
    _voice_check_enabled()
    data = await asyncio.to_thread(_load_voice_json, VOICE_TASKS_PATH, {"tasks": []})
    return data if isinstance(data, dict) else {"tasks": data if isinstance(data, list) else []}


@router.post("/voice/tasks")
async def voice_add_task(body: dict, user: dict = Depends(current_user)):
    """添加语音任务。body: {title, detail?, priority?, tags?}"""
    _voice_check_enabled()
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "任务标题不能为空")
    if len(title) > 500:
        raise HTTPException(400, "任务标题过长（上限 500 字符）")
    priority = body.get("priority", "mid")
    if priority not in ("low", "mid", "high"):
        priority = "mid"
    tags = body.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    tags = [str(t)[:32] for t in tags if isinstance(t, str)][:10]
    async with _VOICE_TASK_LOCK:
        data = await asyncio.to_thread(_load_voice_json, VOICE_TASKS_PATH, {"tasks": []})
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
        if isinstance(tasks, dict):
            tasks = list(tasks.values())
        now = _now_iso()
        task = {
            "id": _gen_voice_id(),
            "title": title,
            "detail": str(body.get("detail") or "")[:2000],
            "status": "pending",
            "priority": priority,
            "tags": tags,
            "progress": 0,
            "createdAt": now,
            "updatedAt": now,
        }
        tasks.append(task)
        if len(tasks) > _VOICE_MAX_TASKS:
            tasks = tasks[-_VOICE_MAX_TASKS:]
        result = {"tasks": tasks}
        await asyncio.to_thread(_save_voice_json, VOICE_TASKS_PATH, result)
    return {"ok": True, "task": task}


@router.post("/voice/tasks/continue")
async def voice_continue_next(user: dict = Depends(current_user)):
    """将最早一个 pending 任务标记为 done，并生成执行报告。"""
    _voice_check_enabled()
    async with _VOICE_TASK_LOCK:
        data = await asyncio.to_thread(_load_voice_json, VOICE_TASKS_PATH, {"tasks": []})
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
        if isinstance(tasks, dict):
            tasks = list(tasks.values())
        for t in tasks:
            if t.get("status") == "pending":
                t["status"] = "done"
                t["progress"] = 100
                t["updatedAt"] = _now_iso()
                await asyncio.to_thread(_save_voice_json, VOICE_TASKS_PATH, {"tasks": tasks})
                return {"ok": True, "message": f"已完成任务：{t.get('title', '')}", "task": t}
        return {"ok": False, "message": "没有待办任务，全部已完成。"}


@router.get("/voice/constraints")
async def voice_get_constraints(user: dict = Depends(current_user)):
    """获取限制条件列表。"""
    _voice_check_enabled()
    data = await asyncio.to_thread(_load_voice_json, VOICE_CONSTRAINTS_PATH, {"constraints": []})
    return data if isinstance(data, dict) else {"constraints": data if isinstance(data, list) else []}


@router.post("/voice/constraints")
async def voice_add_constraint(body: dict, user: dict = Depends(current_user)):
    """添加限制条件。body: {text}"""
    _voice_check_enabled()
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "限制条件不能为空")
    if len(text) > 500:
        raise HTTPException(400, "限制条件过长（上限 500 字符）")
    async with _VOICE_TASK_LOCK:
        data = await asyncio.to_thread(_load_voice_json, VOICE_CONSTRAINTS_PATH, {"constraints": []})
        constraints = data.get("constraints", []) if isinstance(data, dict) else []
        if isinstance(constraints, dict):
            constraints = list(constraints.values())
        now = _now_iso()
        constraint = {
            "id": "vc_" + secrets.token_hex(6),
            "text": text,
            "createdAt": now,
        }
        constraints.append(constraint)
        if len(constraints) > _VOICE_MAX_CONSTRAINTS:
            constraints = constraints[-_VOICE_MAX_CONSTRAINTS:]
        result = {"constraints": constraints}
        await asyncio.to_thread(_save_voice_json, VOICE_CONSTRAINTS_PATH, result)
    return {"ok": True, "constraint": constraint}


@router.post("/voice/notepad")
async def voice_inject_notepad(body: dict, user: dict = Depends(current_user)):
    """注入上下文到 notepad。body: {text}"""
    _voice_check_enabled()
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "内容不能为空")
    try:
        from pyqt.desktop.notepad import save as notepad_save
        import datetime as _dt
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        key = f"voice_{ts}"
        ok = await asyncio.to_thread(notepad_save, key, text)
        return {"ok": ok, "key": key, "message": f"已暂存到 notepad[{key}]"}
    except Exception as e:
        log_error("[voice] notepad 注入失败", e)
        raise HTTPException(500, "notepad 注入失败")


# ==================== DeveraiIntegrityService 完整性校验端点 ====================
# 提供 signIntegrity / verifyIntegrity / getContentHash / self_check 四个 RPC

_INTEGRITY_SERVICE = None


def _get_integrity_service():
    """获取 DeveraiIntegrityService 单例（懒加载）。"""
    global _INTEGRITY_SERVICE
    if _INTEGRITY_SERVICE is None:
        try:
            from pyqt.desktop.deverai_integrity import get_integrity_service
            _INTEGRITY_SERVICE = get_integrity_service()
        except Exception as e:
            log_error("[integrity] 服务加载失败", e)
            raise HTTPException(503, "完整性服务不可用")
    return _INTEGRITY_SERVICE


def _integrity_check_enabled():
    """检查完整性校验开关，未开启则 403。"""
    try:
        from pyqt.desktop.config import get_config as _get_cfg
        if not getattr(_get_cfg(), "ENABLE_INTEGRITY", True):
            raise HTTPException(403, "完整性校验未开启（ENABLE_INTEGRITY）")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(403, "无法读取完整性配置")


@router.post("/integrity/sign")
async def integrity_sign(body: dict, user: dict = Depends(current_user)):
    """对数据签名。body: {data: base64, ctx: tool|asset|doc|voice-task|voice-constraint}"""
    _integrity_check_enabled()
    svc = _get_integrity_service()
    import base64
    try:
        raw = base64.b64decode(body.get("data", ""))
    except Exception:
        raise HTTPException(400, "data 必须是 base64 编码")
    ctx = str(body.get("ctx") or "tool").strip()
    sig = await asyncio.to_thread(svc.signIntegrity, raw, ctx)
    content_hash = await asyncio.to_thread(svc.getContentHash, raw)
    return {"ok": True, "signature": sig, "content_hash": content_hash, "ctx": ctx}


@router.post("/integrity/verify")
async def integrity_verify(body: dict, user: dict = Depends(current_user)):
    """验证签名。body: {data: base64, signature, ctx}"""
    _integrity_check_enabled()
    svc = _get_integrity_service()
    import base64
    try:
        raw = base64.b64decode(body.get("data", ""))
    except Exception:
        raise HTTPException(400, "data 必须是 base64 编码")
    sig = str(body.get("signature", "")).strip()
    ctx = str(body.get("ctx") or "tool").strip()
    ok = await asyncio.to_thread(svc.verifyIntegrity, raw, sig, ctx)
    return {"ok": True, "valid": ok, "ctx": ctx}


@router.post("/integrity/hash")
async def integrity_hash(body: dict, user: dict = Depends(current_user)):
    """快速内容哈希。body: {data: base64}"""
    _integrity_check_enabled()
    svc = _get_integrity_service()
    import base64
    try:
        raw = base64.b64decode(body.get("data", ""))
    except Exception:
        raise HTTPException(400, "data 必须是 base64 编码")
    h = await asyncio.to_thread(svc.getContentHash, raw)
    return {"ok": True, "hash": h}


@router.post("/integrity/self_check")
async def integrity_self_check(user: dict = Depends(current_user)):
    """运行完整性自检。"""
    _integrity_check_enabled()
    svc = _get_integrity_service()
    result = await asyncio.to_thread(svc.self_check)
    return {"ok": True, "result": result}


# --------------------------------------------------------------------------
# DashScope API 代理（通义万相：文生图/图生图/视频生成）
# 服务端代理：API Key 仅存 config.json，不暴露给浏览器端
# --------------------------------------------------------------------------
@router.post("/dashscope/image_generate")
async def dashscope_image_generate(body: dict, user: dict = Depends(current_user)):
    """文生图代理。body: {prompt, model?, size?, n?, style?, negative_prompt?}"""
    try:
        from pyqt.desktop.dashscope import image_generate
    except Exception as e:
        log_error("[web] DashScope 模块加载失败", e)
        raise HTTPException(503, "DashScope 模块不可用")
    return await asyncio.to_thread(
        image_generate,
        str(body.get("prompt") or ""),
        str(body.get("model") or "wan2.7-image-pro"),
        str(body.get("size") or "1024*1024"),
        body.get("n", 1),
        str(body.get("style") or "<auto>"),
        str(body.get("negative_prompt") or ""),
    )


@router.post("/dashscope/image_edit")
async def dashscope_image_edit(body: dict, user: dict = Depends(current_user)):
    """图生图代理。body: {image_url, prompt, model?}"""
    try:
        from pyqt.desktop.dashscope import image_edit
    except Exception as e:
        log_error("[web] DashScope 模块加载失败", e)
        raise HTTPException(503, "DashScope 模块不可用")
    return await asyncio.to_thread(
        image_edit,
        str(body.get("image_url") or ""),
        str(body.get("prompt") or ""),
        str(body.get("model") or "qwen-image-edit-max"),
    )


@router.post("/dashscope/video_generate")
async def dashscope_video_generate(body: dict, user: dict = Depends(current_user)):
    """文生视频/图生视频代理。body: {prompt, model?, image_url?}"""
    try:
        from pyqt.desktop.dashscope import video_generate
    except Exception as e:
        log_error("[web] DashScope 模块加载失败", e)
        raise HTTPException(503, "DashScope 模块不可用")
    return await asyncio.to_thread(
        video_generate,
        str(body.get("prompt") or ""),
        str(body.get("model") or "wan2.7-t2v"),
        str(body.get("image_url") or ""),
    )


@router.get("/dashscope/task_status/{task_id}")
async def dashscope_task_status(task_id: str, user: dict = Depends(current_user)):
    """查询异步任务状态。"""
    try:
        from pyqt.desktop.dashscope import task_status
    except Exception as e:
        log_error("[web] DashScope 模块加载失败", e)
        raise HTTPException(503, "DashScope 模块不可用")
    return await asyncio.to_thread(task_status, task_id)


@router.get("/dashscope/list_models")
async def dashscope_list_models(category: str = "", user: dict = Depends(current_user)):
    """列出可用模型（读 model-library/models.json）。"""
    try:
        from pyqt.desktop.dashscope import list_models
    except Exception as e:
        log_error("[web] DashScope 模块加载失败", e)
        raise HTTPException(503, "DashScope 模块不可用")
    return await asyncio.to_thread(list_models, category)
