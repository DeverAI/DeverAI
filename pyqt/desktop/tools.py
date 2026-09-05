"""工具注册表：AI 可调用的工具（读写文件 / 编辑 / 搜索 / 执行命令行 / 子Agent / 资产银行）。

安全：
- 所有文件路径强制解析到工作区内（resolve + 前缀校验），越界直接拒绝。
- run_command 默认走"审批门"（UI 弹出允许/拒绝）。
- AI 删除文件默认禁用（ALLOW_AI_DELETE=False）。
- 工作区=应用自身目录时，保护 app/ data/ backups/ dev_log/ updates/ 等系统目录。
"""
import asyncio
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import APP_DIR, Config
from .llm import LLMError
from .locks import atomic_write, get_locks
from .search_tools import (tool_file_search, tool_port_declare, tool_port_read,
                           tool_todo_update)
from .storage import read_text, save_text

# v6.6 外交型任务：与外部系统交互、无本地破坏性副作用（浏览器/网络）。
# 硬性规定：允许全放行（不弹审批），但 AI 检测到不完全符合用户要求立即拦截。
DIPLOMATIC_TOOLS = {
    "browser_open", "browser_read", "browser_screenshot",
    "web_search",
}

PROTECTED_NAMES = {"pyqt", "webui", "lite", "data", "backups", "dev_log", "updates", "tests", "tools"}
PROTECTED_FILES = {"Err.log", "config.json"}
# v8.15 检修：Windows 文件系统大小写不敏感，目录改名 Data/ERR.LOG 后保护不得静默失效——
# 判定统一小写比较（对齐 webui/app/bridge.py 与 lite/app/lite_server.py 的同源修复）
PROTECTED_NAMES_LC = {n.lower() for n in PROTECTED_NAMES}
PROTECTED_FILES_LC = {n.lower() for n in PROTECTED_FILES}
_READ_PROT_NAMES_LC = {"data", "backups"}
_READ_PROT_FILES_LC = {"err.log", "config.json"}
# v8.15：grep 扫描并发闸——超时放弃等待后底层正则线程不可中断，限流防堆积
_GREP_SCAN_SEM = threading.Semaphore(2)
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".idea", ".vscode", "dist", "build"}

# v5: 重要文档（AGENT.txt 文档清单）——仅总司令可写，其余专家工具层直接拒绝
IMPORTANT_DOCS = {"design.md", "techniques.md", "fact.md", "future.md", "freqerr.md",
                  "agent.txt", "dev_log", "updates"}

# v5: 危险命令模式（审批门 danger/copilot 模式用）
# v8.13：与 app/security.py::DANGEROUS_PATTERNS 完全同源——补 rd /s、taskkill 顺序无关、
# --force 排除 --force-with-lease（此前桌面与命令桥后端两套清单，同一命令前后端判定不一致）。
DANGEROUS_PATTERNS = [
    r"\brm\s+-[a-z]*[rf]", r"\bdel\s+/[sfqi]", r"\brmdir\s+/s", r"\brd\s+/s\b",
    r"\bformat\b", r"\bdiskpart\b",
    r"\bmkfs\b", r"\bdd\s+if=", r":\(\)\{", r"\breg\s+delete\b", r"\bshutdown\b", r"\breboot\b",
    r"powershell\s+-enc", r"Invoke-Expression", r"\btaskkill\b(?=.*\s/f(?=\s|$))(?=.*\s/(?:im|pid)\b)",
    r">\s*/dev/", r"\bgit\s+push\s+.*--force(?!-with-lease)",
    r"\bdrop\s+(table|database)", r"\btruncate\s+table",
    # v8.13：执行代码/脚本的等价危险形式
    r"\bpython(?:3)?\s+-c\b", r"\bpy\s+-c\b",
    r"\bpowershell\s+(?:-[a-z]+\s+)*-(?:command|enc)\b", r"\bcmd(?:\.exe)?\s+/[cq]\b",
    r"\bRemove-Item\b.*-Recurse\b", r"\bshutil\.rmtree\b", r"\bos\.remove\b",
    # v8.15 检修：PowerShell 短参数/别名递归删除（Remove-Item -r、ri -Recurse 等）
    # 此前只匹配全称 -Recurse，danger 模式下可免审批静默递归删除
    r"\b(?:Remove-Item|ri|rm|del|erase|rmdir|rd)\b[^|\n;&]*\s-(?:recurse|r|rf|fr)\b",
    # git clean 带 -f 才真正删文件（-fdx 清空全部未跟踪文件）
    r"\bgit\s+clean\b[^|\n;&]*\s-[a-z]*f",
]


def is_dangerous(payload: dict) -> bool:
    """审批门危险内容检测：命中任一危险模式返回 True。

    v8.5.x 审查修复：支持显式 dangerous 标记（如 delete_file 这类本身即破坏性操作，
    其 payload 不含危险命令串，但必须在 danger 模式下仍强制弹审批）。
    """
    if payload.get("dangerous"):
        return True
    cmd = str(payload.get("command") or "")
    for pat in DANGEROUS_PATTERNS:
        try:
            # v8.13：DOTALL——危险词被换行拆开（taskkill\n/f /im）时 Python 默认 . 不跨行会漏判；
            # JS 前端用 [\s\S]* 已跨行，三端语义必须一致
            if re.search(pat, cmd, re.IGNORECASE | re.DOTALL):
                return True
        except re.error:
            continue
    return False


class ApprovalGate:
    """命令审批门：call_id -> asyncio.Future。"""

    def __init__(self):
        self._pending: dict = {}

    def request(self, call_id: str) -> asyncio.Future:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[call_id] = fut
        return fut

    def resolve(self, call_id: str, approved: bool) -> bool:
        fut = self._pending.pop(call_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result(approved)
        return True

    def has_pending(self, call_id: str) -> bool:
        return call_id in self._pending


@dataclass
class ToolContext:
    cfg: Config
    emit: Optional[callable] = None          # async (event: dict) -> None
    workspace: str = None
    call_id: str = ""
    approval: Optional[ApprovalGate] = None
    agent: Optional[object] = None           # 父 Agent（用于委托子Agent）
    vault: Optional[object] = None
    depth: int = 0                           # 子Agent 嵌套深度（防无限递归）
    expert_id: str = ""                     # v5: 专家身份（空=主对话/总司令）
    restrictions: list = field(default_factory=list)  # v5: 强制限制（如禁用某库）
    user_intent: str = ""                   # v6.6: 用户本轮原始要求（外交型任务合规判定上下文）

    def __post_init__(self):
        if self.workspace is None:
            self.workspace = self.cfg.workspace


# --------------------------------------------------------------------------
# 路径安全
# --------------------------------------------------------------------------
def resolve_ws(ctx: ToolContext, rel: str) -> Path:
    """将相对路径解析到工作区内；越界抛异常。"""
    if not (ctx.workspace or "").strip():
        raise PermissionError("未配置工作区，请在「AI → 设置」中选择工作区目录")
    root = Path(ctx.workspace).resolve()
    raw = str(rel or ".").strip()
    p = (root / raw).resolve()
    if p != root and root not in p.parents:
        raise PermissionError(f"路径越界（仅允许工作区内）：{rel}")
    return p


def _is_protected(ctx: ToolContext, p: Path) -> bool:
    """工作区是应用自身目录时，保护系统目录/文件（写/删/改名用）。"""
    try:
        rel = p.resolve().relative_to(APP_DIR)
    except ValueError:
        return False
    parts = rel.parts
    if not parts:
        return False
    if parts[0].lower() in PROTECTED_NAMES_LC:
        return True
    if rel.name.lower() in PROTECTED_FILES_LC:
        return True
    return False


def _is_read_protected(ctx: ToolContext, p: Path) -> bool:
    """v8.13.1：只读保护收窄——数据目录与密钥文件禁止读；static/app/desktop 源码允许 Agent 查看。"""
    try:
        rel = p.resolve().relative_to(APP_DIR)
    except ValueError:
        return False
    parts = rel.parts
    if not parts:
        return False
    # v8.15 检修：大小写不敏感比较（Windows），防 Data/ERR.LOG 变体绕过
    return (parts[0].lower() in _READ_PROT_NAMES_LC
            or rel.name.lower() in _READ_PROT_FILES_LC)


def _is_important_doc(ctx: ToolContext, p: Path) -> bool:
    """v5: 是否重要文档（相对工作区路径命中 AGENT.txt 文档清单）。"""
    try:
        rel = p.resolve().relative_to(Path(ctx.workspace).resolve())
    except ValueError:
        return False
    parts = rel.parts
    if not parts:
        return False
    if parts[0].lower() in IMPORTANT_DOCS:
        return True
    return False


def _write_guard(ctx: ToolContext, p: Path, rel: str) -> Optional[str]:
    """v5 写权校验：重要文档仅总司令；专家仅限申报文件清单。返回拒绝理由或 None。"""
    if not getattr(ctx.cfg, "ENABLE_LOCKS", False):
        return None
    expert_id = getattr(ctx, "expert_id", "") or ""
    if not expert_id or expert_id == "__commander__":
        return None  # 主对话/总司令不受所有权限制（重要文档对主对话仍开放）
    if _is_important_doc(ctx, p):
        return f"重要文档 {rel} 仅总司令可写，当前身份：{expert_id}。请上报总司令处理。"
    try:
        relp = str(p.resolve().relative_to(Path(ctx.workspace).resolve())).replace("\\", "/")
    except ValueError:
        relp = str(rel)
    if not get_locks().can_write(expert_id, relp):
        return (f"文件 {rel} 不在你的申报清单内（清单外一律只读）。"
                f"如确需修改，请调用 request_write_permission 工具向总司令请示并说明 purpose。")
    return None


def _format_error(exc: BaseException) -> dict:
    return {"ok": False, "output": f"错误({type(exc).__name__}): {exc}"}


def _safe_int(value, default: int = 0) -> int:
    """LLM 可控数值参数统一守卫：非数字字符串/异常类型回退默认值，不抛 RE。"""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    """LLM 可控浮点参数统一守卫（NaN 仍按默认处理）。"""
    try:
        v = float(value)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def _as_str_list(value):
    """LLM 可控 list 字段类型归一化：字符串按单元素处理，其余非容器按空列表。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(x) for x in value]
    return []


def _strict_bool(value, default: bool = False) -> bool:
    """严格布尔：bool 直接取；字符串仅 1/true/yes/on 为真（"false"/"0" 必须为假）。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _restriction_guard(ctx: ToolContext, content: str) -> Optional[str]:
    """v5: 强制限制内容校验（如禁用某库）：命中限制关键词直接拒绝写入。"""
    rs = getattr(ctx, "restrictions", None) or []
    if not rs or not content:
        return None
    low = content.lower()
    for r in rs:
        key = str(r).strip().lower()
        if key and key in low:
            return f"内容命中强制限制「{r}」，已拒绝写入。请改用符合限制的写法。"
    return None


async def _content_copilot_guard(ctx: ToolContext, content: str) -> Optional[str]:
    """v5 副驾驶钩子（危险代码片段）：写入内容命中危险模式且为 copilot 审批模式时，
    交副驾驶裁定；kill 则掐断。非 copilot 模式不额外调用（避免多余 token）。"""
    if not content or getattr(ctx.cfg, "approval_mode", "danger") != "copilot":
        return None
    if not is_dangerous({"command": content}):
        return None
    try:
        from .experts import copilot_check  # 延迟导入避免循环依赖
        verdict = await copilot_check(ctx.cfg, {"kind": "dangerous_code",
                                                "content": content[:800]})
    except Exception:
        # v8.17：副驾驶不可用时升级用户审批（绝不静默放行）
        return await _request_approval(
            ctx, "write_file",
            {"command": f"写入内容（副驾驶不可用，升级人工审批）",
             "dangerous": True})
    if verdict.get("kill"):
        await _emit(ctx, {"type": "copilot_block", "note": verdict.get("note", ""),
                          "payload": {"kind": "dangerous_code"}})
        return f"副驾驶已掐断本次写入：{verdict.get('note', '内容含危险指令')}"
    # v8.17：副驾驶不确定时升级用户审批（绝不静默放行）
    if verdict.get("uncertain"):
        return await _request_approval(
            ctx, "write_file",
            {"command": f"写入内容（副驾驶不确定，升级人工审批）",
             "dangerous": True})
    return None


# --------------------------------------------------------------------------
# 工具实现
# --------------------------------------------------------------------------
async def tool_list_dir(args, ctx: ToolContext) -> dict:
    rel = args.get("path") or "."
    p = resolve_ws(ctx, rel)
    # v8.13.1：data/backups 目录不可列；根目录列表隐藏受保护子项
    if _is_read_protected(ctx, p):
        return {"ok": False, "output": f"该目录属于受保护数据，禁止列出: {rel}"}
    if not p.exists():
        return {"ok": False, "output": f"目录不存在: {rel}"}
    if not p.is_dir():
        return {"ok": False, "output": f"不是目录: {rel}"}
    entries = []
    try:
        items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except PermissionError as e:
        return _format_error(e)
    truncated = False
    if len(items) > 500:
        items = items[:500]
        truncated = True
    for it in items:
        if _is_read_protected(ctx, it):
            continue  # v8.13.1：受保护数据不出现在目录列表
        try:
            st = it.stat()
        except OSError:
            st = None
        entries.append(
            {
                "name": it.name,
                "is_dir": it.is_dir(),
                "size": st.st_size if st else 0,
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)) if st else "",
            }
        )
    out_lines = [f"[目录] {rel}" + ("（已限制前 500 项）" if truncated else "")]
    for e in entries:
        flag = "[D] " if e["is_dir"] else "    "
        size = "" if e["is_dir"] else f"  {e['size']}B"
        out_lines.append(f"{flag}{e['name']}{size}  ({e['modified']})")
    # v8.13：AI 只应看到相对路径，meta.path 不再回传绝对路径
    return {"ok": True, "output": "\n".join(out_lines),
            "meta": {"entries": entries, "path": str(rel or ".").replace("\\", "/")}}


async def tool_read_file(args, ctx: ToolContext) -> dict:
    rel = args.get("path")
    p = resolve_ws(ctx, rel)
    # v8.13.1：读只查数据/密钥保护；static/app/desktop 源码允许 Agent 查看
    if _is_read_protected(ctx, p):
        return {"ok": False, "output": f"该文件属于受保护数据，禁止读取: {rel}"}
    if not p.is_file():
        return {"ok": False, "output": f"文件不存在: {rel}"}
    try:
        size = p.stat().st_size
    except OSError as e:
        return _format_error(e)
    if size > 2 * 1024 * 1024:  # M4: 与编辑器对齐的大小上限（先 stat，超大文件不整读）
        return {
            "ok": False,
            "output": f"文件过大（{size // 1024 // 1024}MB > 2MB）。请使用 run_command 分片处理，或使用 offset/limit 分页读取。",
        }
    try:
        data = p.read_bytes()
    except OSError as e:
        return _format_error(e)
    if b"\x00" in data[:8192]:
        return {
            "ok": True,
            "output": f"[二进制文件] {rel} ({len(data)} 字节)，不显示内容。",
            "meta": {"binary": True, "size": len(data)},
        }
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)
    offset = max(0, _safe_int(args.get("offset"), 0))
    limit = _safe_int(args.get("limit"), 0)
    if limit > 0:
        chunk = lines[offset : offset + limit]
    else:
        chunk = lines[offset:]
    body = "\n".join(chunk)
    prefix = f"[文件] {rel} | {total} 行 | {len(data)} 字节"
    if offset > 0:
        prefix += f" | 从第 {offset + 1} 行开始"
    return {
        "ok": True,
        "output": f"{prefix}\n{body}" if body else prefix,
        # v8.13：meta.path 只回相对路径（AI 不应看到绝对路径）
        "meta": {"path": str(rel).replace("\\", "/"), "lines": total, "size": len(data)},
    }


def _readonly_block(tool_name: str):
    """v8.3：AI 报警只读模式下拒绝写/执行类工具。"""
    from . import session_snap as _snap
    if _snap.is_readonly():
        return {"ok": False,
                "output": f"AI 只读模式（{_snap.alert_reason() or '报警中'}）：{tool_name} 已禁止。"}
    return None


def _tree_guard(ctx: ToolContext, rel: str):
    """v8.5 依赖树工具调用语义审查：操作的文件若依赖缺失/自身过小，先拦截。

    轻量：只查当前 rel 在依赖树中的条目（缺失依赖 + 大小下限），不遍历全树。
    用户拍板：agent 每调一个工具就拿 work tree 审查，触碰到"文件缺了/少了"先拦。
    """
    if not getattr(ctx.cfg, "ENABLE_DEP_TREE", True):
        return None
    try:
        from . import session_snap as _snap
        rid = _snap.current_round()
        if not rid:
            return None
        tree = _snap.get_tree(rid) or {}
        rel_norm = (rel or "").strip().replace("\\", "/")
        entry = tree.get(rel_norm)
        if not entry:
            return None
        root = Path(ctx.cfg.workspace or ".")
        issues = []
        for d in entry.get("deps", []):
            if not (root / str(d)).exists():
                issues.append(f"依赖缺失: {d}")
        min_size = int(entry.get("min_size") or 0)
        if min_size > 0:
            p = root / rel_norm
            try:
                size = p.stat().st_size if p.exists() else 0
            except OSError:
                size = 0
            if size < min_size:
                issues.append(f"文件过小: {size} < 下限 {min_size}")
        if not issues:
            return None
        return {"ok": False,
                "output": "依赖树审查拦截（操作文件涉及缺依赖/过小）：" + "；".join(issues)
                          + "。请先修复依赖，或经用户确认后再操作。"}
    except Exception:
        return None


async def tool_write_file(args, ctx: ToolContext) -> dict:
    block = _readonly_block("write_file")
    if block:
        return block
    rel = args.get("path")
    content = str(args.get("content") or "")
    p = resolve_ws(ctx, rel)
    if _is_protected(ctx, p):
        return {"ok": False, "output": f"该文件属于系统受保护文件，禁止写入: {rel}"}
    # v8.25 用户文件保护：PPT/Excel/Word/PDF等用户资产禁AI文本覆盖
    if getattr(ctx.cfg, "ENABLE_USER_FILE_PROTECT", True):
        try:
            from . import file_protect as _fp
            _reason = _fp.check_ai_write_block(ctx.workspace, str(rel))
            if _reason:
                return {"ok": False, "output": _reason}
        except Exception:
            pass
    g = _tree_guard(ctx, rel)
    if g:
        return g
    deny = _write_guard(ctx, p, rel)
    if deny:
        return {"ok": False, "output": deny}
    deny = _restriction_guard(ctx, content)
    if deny:
        return {"ok": False, "output": deny}
    deny = await _content_copilot_guard(ctx, content)
    if deny:
        return {"ok": False, "output": deny}
    old = ""
    old_size = 0
    existed = p.exists()
    crlf = False
    if existed:
        try:
            from .storage import sniff_crlf
            crlf = sniff_crlf(p)  # v8.14：保持原文件行尾风格（atomic_write 为精确写）
            old = p.read_text(encoding="utf-8", errors="replace")
            old_size = len(old.encode("utf-8"))
        except OSError:
            old = ""
            old_size = 0
            existed = False
    # v6.3 Checkpoint：写入前快照原文件内容（含空文件，防覆盖无痕）
    if getattr(ctx.cfg, "ENABLE_CHECKPOINT", True) and p.exists():
        try:
            from . import checkpoint as _ckpt
            _ckpt.save_checkpoint(str(rel), old, task_id=getattr(ctx, "task_id", "") or "default",
                                  source="ai")
        except Exception:
            pass  # 快照失败不阻塞写入（FreqErr：不得吞没主对话，但 checkpoint 是辅助）
    # v6.4 内联差异预览：写入前弹 diff 对比，用户接受才落盘
    # （v8.14b：diff 必须在行尾还原之前做——old/new 都处于规范化 \n 形态，
    #   否则 CRLF 文件每次都显示整文件变更）
    if not await _request_diff_approval(ctx, str(rel), old, content):
        return {"ok": False, "output": f"用户拒绝了写入 {rel}（差异预览取消）。"}
    # v8.15 检修：审批等待（最长 600s）期间文件可能被外部编辑器改动——
    # check-then-act 竞态会让 AI 静默覆盖用户保存的内容，且轮内回退快照也记的是旧值
    if existed:
        try:
            cur = p.read_text(encoding="utf-8", errors="replace")
            if cur != old:
                return {"ok": False,
                        "output": f"[!] 审批期间 {rel} 已被外部修改，为防覆盖已中止。请重新发起写入（diff 将基于最新内容）。"}
        except OSError:
            pass
    # v8.3 轮内回退快照 + 工具调用记录（审批通过后，防幽灵回退点）
    try:
        from . import session_snap as _snap
        rno = _snap.add_tool_call(_snap.current_round(), "write_file")
        _snap.tool_rollback(_snap.current_round(), str(rel), old,
                            tool_name="write_file", round_no=rno, existed=existed)
    except Exception:
        pass
    if crlf:
        # 编辑器/模型侧文本一律 \n 规范，写回前还原为原文件 CRLF 风格
        content = content.replace("\r\n", "\n").replace("\n", "\r\n")
    try:
        atomic_write(p, content)  # v5: 强制 CoW（临时文件+原子替换）
    except OSError as e:
        return _format_error(e)
    new_size = len(content.encode("utf-8"))
    await _emit(ctx, {"type": "file_changed", "path": str(rel)})
    # v8.3 依赖树增量更新
    try:
        from . import session_snap as _snap
        _snap.update_tree(_snap.current_round(), str(rel), new_size, "write")
    except Exception:
        pass
    return {
        "ok": True,
        "output": f"已写入 {rel}（{len(content.splitlines())} 行，{new_size} 字节）"
        + (f"，原 {old_size} 字节" if old_size else ""),
        "meta": {"path": str(rel).replace("\\", "/"), "size": new_size, "created": not bool(old)},
    }


async def tool_edit_file(args, ctx: ToolContext) -> dict:
    block = _readonly_block("edit_file")
    if block:
        return block
    rel = args.get("path")
    old_str = str(args.get("old_string") or "")
    new_str = str(args.get("new_string") or "")
    replace_all = _strict_bool(args.get("replace_all"))
    p = resolve_ws(ctx, rel)
    if _is_protected(ctx, p):
        return {"ok": False, "output": f"该文件属于系统受保护文件，禁止编辑: {rel}"}
    # v8.25 用户文件保护
    if getattr(ctx.cfg, "ENABLE_USER_FILE_PROTECT", True):
        try:
            from . import file_protect as _fp
            _reason = _fp.check_ai_write_block(ctx.workspace, str(rel))
            if _reason:
                return {"ok": False, "output": _reason}
        except Exception:
            pass
    g = _tree_guard(ctx, rel)
    if g:
        return g
    deny = _write_guard(ctx, p, rel)
    if deny:
        return {"ok": False, "output": deny}
    deny = _restriction_guard(ctx, new_str)
    if deny:
        return {"ok": False, "output": deny}
    deny = await _content_copilot_guard(ctx, new_str)
    if deny:
        return {"ok": False, "output": deny}
    if not p.is_file():
        return {"ok": False, "output": f"文件不存在: {rel}"}
    try:
        from .storage import sniff_crlf
        crlf = sniff_crlf(p)  # v8.14：保持原文件行尾风格
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return _format_error(e)
    # 模型提供的匹配串/替换串统一 \n 规范（JSON 里可能带 \r\n）
    old_str = old_str.replace("\r\n", "\n")
    new_str = new_str.replace("\r\n", "\n")
    if not old_str:
        return {"ok": False, "output": "old_string 不能为空。建议先 read_file 获取精确内容。"}
    count = text.count(old_str)
    if count == 0:
        # 给出附近文本帮助 AI 修正
        idx = text.find(old_str[:40])
        if idx == -1:
            return {"ok": False, "output": f"未找到要替换的内容，请先 read_file 核对原文。文件当前 {len(text.splitlines())} 行。"}
        return {"ok": False, "output": f"未找到完全匹配，但发现了相近片段：\n...{text[max(0,idx-60):idx+120]}...\n请基于该片段调整 old_string。"}
    if count > 1 and not replace_all:
        return {"ok": False, "output": f"匹配到 {count} 处，请设置 replace_all=true 或提供更精确的 old_string（含上下文）。"}
    new_text = text.replace(old_str, new_str) if replace_all else text.replace(old_str, new_str, 1)
    # v6.3 Checkpoint：编辑前快照原文件内容
    if getattr(ctx.cfg, "ENABLE_CHECKPOINT", True):
        try:
            from . import checkpoint as _ckpt
            _ckpt.save_checkpoint(str(rel), text, task_id=getattr(ctx, "task_id", "") or "default",
                                  source="ai")
        except Exception:
            pass
    # v6.4 内联差异预览：编辑前弹 diff 对比，用户接受才落盘
    # （v8.14b：diff 必须在行尾还原之前做，old/new 均为规范化 \n 形态）
    if not await _request_diff_approval(ctx, str(rel), text, new_text):
        return {"ok": False, "output": f"用户拒绝了编辑 {rel}（差异预览取消）。"}
    # v8.15 检修：同 write_file——审批等待期间外部改动不得被静默覆盖
    try:
        cur = p.read_text(encoding="utf-8", errors="replace")
        if cur != text:
            return {"ok": False,
                    "output": f"[!] 审批期间 {rel} 已被外部修改，为防覆盖已中止。请重新 read_file 后再编辑。"}
    except OSError:
        pass
    # v8.3 轮内回退快照 + 工具调用记录（审批通过后，防幽灵回退点）
    try:
        from . import session_snap as _snap
        rno = _snap.add_tool_call(_snap.current_round(), "edit_file")
        _snap.tool_rollback(_snap.current_round(), str(rel), text,
                            tool_name="edit_file", round_no=rno)
    except Exception:
        pass
    if crlf:
        # 写回前还原为原文件 CRLF 风格（atomic_write 为精确写）
        new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")
    try:
        atomic_write(p, new_text)  # v5: 强制 CoW
    except OSError as e:
        return _format_error(e)
    await _emit(ctx, {"type": "file_changed", "path": str(rel)})
    # v8.3 依赖树增量更新
    try:
        from . import session_snap as _snap
        _snap.update_tree(_snap.current_round(), str(rel), len(new_text.encode("utf-8")), "write")
    except Exception:
        pass
    return {"ok": True, "output": f"已编辑 {rel}：替换 {count} 处" if replace_all and count > 1 else f"已编辑 {rel}：替换 1 处",
            "meta": {"path": str(rel).replace("\\", "/"), "replaced": count, "old": old_str, "new": new_str}}


def _zip_dir_backup(src: Path, zip_path: Path) -> str:
    """整目录 zip 备份（同步实现；调用方必须丢 asyncio.to_thread 防阻塞事件循环）。

    os.walk(followlinks=False)：不跟随符号链接目录，防符号链接环无限递归。
    失败返回 ""（调用方按无备份处理，不静默吞：异常细节由调用方决定是否落日志）。
    """
    import zipfile
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(src, followlinks=False):
                for name in files:
                    fp = Path(root) / name
                    zf.write(fp, str(fp.relative_to(src)).replace("\\", "/"))
        return str(zip_path)
    except Exception:
        return ""


async def tool_delete_file(args, ctx: ToolContext) -> dict:
    block = _readonly_block("delete_file")
    if block:
        return block
    if not ctx.cfg.ALLOW_AI_DELETE:
        return {"ok": False, "output": "AI 删除文件功能默认禁用（ALLOW_AI_DELETE=False）。如需删除请在文件树中手动操作。"}
    rel = args.get("path")
    p = resolve_ws(ctx, rel)
    # v8.13 P0：禁止删除工作区根目录（"." / "" / "\\" 都会解析到 root，
    # 此前可一次清空整个工作区；与 app/bridge.py /fs/delete 对齐）
    if p == Path(ctx.workspace).resolve():
        return {"ok": False, "output": "禁止删除工作区根目录"}
    if _is_protected(ctx, p):
        return {"ok": False, "output": f"该文件属于系统受保护文件，禁止删除: {rel}"}
    # v8.25 用户文件保护：用户资产不允许AI删除（请用户手动处理或先隔离）
    if getattr(ctx.cfg, "ENABLE_USER_FILE_PROTECT", True):
        try:
            from . import file_protect as _fp
            _reason = _fp.check_ai_write_block(ctx.workspace, str(rel))
            if _reason:
                return {"ok": False, "output": _reason + "删除同样被禁止。"}
        except Exception:
            pass
    if not p.exists():
        return {"ok": False, "output": f"不存在: {rel}"}
    g = _tree_guard(ctx, rel)
    if g:
        return g
    # v8.5.x 审查修复：写权校验（专家仅能删申报清单内文件，清单外一律只读）
    reason = _write_guard(ctx, p, rel)
    if reason:
        return {"ok": False, "output": reason}
    # v8.5.x 审查修复：删除是破坏性操作，必须走审批门（dangerous 标记强制 danger 模式也弹窗）
    approved = await _request_approval(
        ctx, "delete_file", {"command": f"删除 {rel}", "path": str(rel), "dangerous": True})
    if not approved:
        return {"ok": False, "output": "用户拒绝了删除操作。", "meta": {"denied": True}}
    # v8.3 删除前记录回退点（防止删除后无法经轮内回退找回）
    old = ""
    dir_backup = ""
    was_dir = p.is_dir()
    if p.is_file():
        try:
            old = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            old = ""
        # v8.25 非Git自动备份增强：二进制/用户资产删除前做二进制checkpoint（文本快照会损坏）
        try:
            from . import file_protect as _fp2
            raw = p.read_bytes()
            if b"\x00" in raw[:8192] or _fp2.is_user_asset(str(rel)):
                from . import checkpoint as _ckpt_del
                if getattr(ctx.cfg, "ENABLE_CHECKPOINT", True):
                    _ckpt_del.save_checkpoint_bytes(
                        str(rel), raw, task_id=getattr(ctx, "task_id", "") or "default",
                        source="ai")
        except Exception:
            pass
    elif was_dir:
        # v8.6 目录删除前整目录 zip 备份（轮内可整目录回退，防删目录救不回）
        # v8.7 审查修复：zip 打包丢 to_thread 防阻塞事件循环；文件名加时间戳防同名目录碰撞
        try:
            from . import session_snap as _snap_dir
            rid = _snap_dir.current_round()
            if rid:
                bdir = _snap_dir.rollback_dir_path(rid)
                bdir.mkdir(parents=True, exist_ok=True)
                zip_path = bdir / (
                    str(rel).replace("/", "__").replace("\\", "__")
                    + f".{int(time.time() * 1000)}.zip")
                dir_backup = await asyncio.to_thread(_zip_dir_backup, p, zip_path)
        except Exception:
            dir_backup = ""
    try:
        if p.is_dir():
            import shutil
            await asyncio.to_thread(shutil.rmtree, p)
        else:
            p.unlink()
    except OSError as e:
        return _format_error(e)
    await _emit(ctx, {"type": "file_changed", "path": str(rel)})
    # v8.9 回退审核：AI 删除文件/目录必须留审计记录
    try:
        from . import audit as _audit
        _audit.audit_log("delete_file", str(rel),
                         f"AI 删除{'目录' if was_dir else '文件'}；删除前备份: "
                         + ("目录 zip" if dir_backup else ("文件内容快照" if old else "无内容备份")),
                         actor=getattr(ctx, "expert_id", "") or "ai")
    except Exception:
        pass
    # v8.3 轮内回退记录（删除前内容/目录 zip）+ 工具调用 + 依赖树移除节点
    try:
        from . import session_snap as _snap
        if old:
            rno = _snap.add_tool_call(_snap.current_round(), "delete_file")
            _snap.tool_rollback(_snap.current_round(), str(rel), old,
                                tool_name="delete_file", round_no=rno)
        elif dir_backup:
            rno = _snap.add_tool_call(_snap.current_round(), "delete_file")
            _snap.tool_rollback_dir(_snap.current_round(), str(rel), dir_backup,
                                    tool_name="delete_file", round_no=rno)
        _snap.update_tree(_snap.current_round(), str(rel), 0, "delete")
    except Exception:
        pass
    return {"ok": True, "output": f"已删除 {rel}"}


async def tool_grep(args, ctx: ToolContext) -> dict:
    pattern = args.get("pattern", "")
    rel = args.get("path") or "."
    glob_pat = args.get("glob") or ""
    ignore_case = _strict_bool(args.get("ignore_case"))
    line_numbers = _strict_bool(args.get("line_numbers"))
    head_limit = min(_safe_int(args.get("head_limit"), 50), 200)
    # v8.13：与 app/bridge 同源的高风险正则前置拒绝（交替型/嵌套量词灾难性回溯）
    if len(pattern) > 200:
        return {"ok": False, "output": "正则表达式过长（上限 200 字符）。"}
    if re.search(r"\([^()]*(?:\|[^()]*)+\)[+*{]", pattern) or \
            re.search(r"\([^()]*[+*?][^()]*\)[+*{]", pattern):
        return {"ok": False, "output": "正则包含高风险重复结构，请改写后再搜索。"}
    p = resolve_ws(ctx, rel)
    if not p.exists():
        return {"ok": False, "output": f"路径不存在: {rel}"}
    # v8.13.1：受保护数据不可 grep（防绕过 read_file 抓取密钥）
    if _is_read_protected(ctx, p):
        return {"ok": False, "output": f"该路径属于受保护数据，禁止搜索: {rel}"}
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        return {"ok": False, "output": f"正则无效: {e}"}

    # 同步扫描/正则匹配丢进 executor，避免灾难性正则或超大目录阻塞 asyncio loop
    # （阻塞 loop 会导致「停止」按钮失效、退出挂起）
    ws = Path(ctx.workspace).resolve() if ctx.workspace else None

    def _search():
        results = []
        scanned = 0
        if p.is_file():
            files = [p]
        else:
            files = []
            for root, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs
                           if d not in SKIP_DIRS and not _is_read_protected(ctx, Path(root) / d)]
                for name in names:
                    if glob_pat and not Path(name).match(glob_pat):
                        continue
                    fp = Path(root) / name
                    if _is_read_protected(ctx, fp):
                        continue
                    files.append(fp)
                    if len(files) > 20000:
                        break
                if len(files) > 20000:
                    break
        for f in files:
            try:
                # v8.5.x 审查修复：只读前 8KB 判断二进制，避免整读数 GB 大文件 OOM
                with open(f, "rb") as _fb:
                    head = _fb.read(8192)
                if b"\x00" in head:
                    continue
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for ln, line in enumerate(fh, 1):
                        scanned += 1
                        # 截断行以缓解灾难性回溯
                        line_txt = line.rstrip("\n")[:5000]
                        if rx.search(line_txt):
                            relpath = str(f.relative_to(ws)) if ws else str(f)
                            results.append(
                                f"{relpath}:{ln}: {line.rstrip()[:300]}" if line_numbers
                                else f"{relpath}: {line.rstrip()[:300]}")
                            if len(results) >= head_limit:
                                break
            except OSError:
                continue
            if len(results) >= head_limit:
                break
        return results, scanned

    try:
        # v8.15 检修：超时只是放弃等待，executor 线程里的灾难性正则会继续烧 CPU——
        # 信号量限制同时扫描数（2），防连续超时累积僵尸线程拖垮默认线程池
        with _GREP_SCAN_SEM:
            results, scanned = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, _search), timeout=30)
    except asyncio.TimeoutError:
        return {"ok": False, "output": "搜索超时（正则过于复杂或文件过多）。"}
    except (OSError, ValueError) as e:
        return _format_error(e)
    if not results:
        return {"ok": True, "output": f"未找到匹配（扫描 {scanned} 行）。"}
    return {"ok": True, "output": f"找到 {len(results)} 处匹配：\n" + "\n".join(results)}


async def tool_glob(args, ctx: ToolContext) -> dict:
    pattern = args.get("pattern", "**/*")
    rel = args.get("path") or "."
    p = resolve_ws(ctx, rel)
    if not p.is_dir():
        return {"ok": False, "output": f"目录不存在: {rel}"}
    # v8.13.1：受保护数据目录不可 glob（防泄露 data/backups 文件路径）
    if _is_read_protected(ctx, p):
        return {"ok": False, "output": f"该路径属于受保护数据，禁止匹配: {rel}"}

    def _walk():
        matches = []
        try:
            # M5: 跳过重型目录并限制数量，避免卡死 agent 循环
            for root, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs
                           if d not in SKIP_DIRS and not _is_read_protected(ctx, Path(root) / d)]
                for name in names:
                    fp = Path(root) / name
                    if _is_read_protected(ctx, fp):
                        continue
                    relp = str(fp.relative_to(p))
                    if Path(relp).match(pattern):
                        matches.append(relp)
                        if len(matches) >= 500:
                            break
                if len(matches) >= 500:
                    break
        except OSError as e:
            raise
        return matches

    try:
        matches = await asyncio.get_running_loop().run_in_executor(None, _walk)
    except OSError as e:
        return _format_error(e)
    matches = sorted(set(matches))
    note = "（已限制 500 条）" if len(matches) >= 500 else ""
    return {"ok": True, "output": f"匹配 {len(matches)} 个文件{note}：\n" + "\n".join(matches[:200])}


async def tool_run_command(args, ctx: ToolContext) -> dict:
    """执行命令行。默认审批门；支持 timeout 与后台运行(async)。"""
    block = _readonly_block("run_command")
    if block:
        return block
    cmd = str(args.get("command") or "").strip()
    if not cmd:
        return {"ok": False, "output": "命令为空。"}
    cwd_rel = args.get("cwd") or ""
    try:
        timeout = float(args.get("timeout") or 120)
    except (TypeError, ValueError):
        timeout = 120
    # v8.13：NaN 防护（与 app/bridge.py 一致，min/max 对 NaN 不生效）
    if timeout != timeout:
        timeout = 120
    timeout = min(max(timeout, 1), 600)
    background = _strict_bool(args.get("background"))
    # v8.15 检修：background=true 的意义就是长耗时构建，仍套 600s 熔断自相矛盾——
    # >10 分钟的后台构建必被杀树并以超时报完成。放宽到 4 小时（仍有界防失控）。
    if background:
        timeout = max(timeout, 4 * 3600)

    # ---- v8.18：持久环境 / 更新间隔 / 任务后杀进程 + 默认值 warning ----
    warnings: list[str] = []
    if args.get("timeout") is None:
        warnings.append(f"timeout 使用默认值 {timeout}s")
    term_env = None
    if args.get("env") is None:
        warnings.append("env 使用默认值（一次性：工作区根 + 当前进程环境）")
    else:
        from . import terms as _terms
        term_env = _terms.get_env(str(args.get("env")))
        if term_env is None:
            warnings.append(f"env '{str(args.get('env'))[:40]}' 不存在，已回退一次性会话")
    try:
        ui_val = args.get("update_interval")
        if ui_val is None:
            update_interval = 0.0
            warnings.append("update_interval 使用默认值 0（不发送运行中心跳）")
        else:
            update_interval = min(max(float(ui_val), 0.0), 120.0)
            if update_interval != update_interval:  # NaN
                update_interval = 0.0
    except (TypeError, ValueError):
        update_interval = 0.0
        warnings.append("update_interval 非法，使用默认值 0")
    if args.get("kill_after") is None:
        kill_after = True
        warnings.append("kill_after 使用默认值 true（超时/停止时杀整棵进程树）")
    else:
        kill_after = _strict_bool(args.get("kill_after"))

    # 环境落点：显式 cwd 优先，其次环境的 cwd_rel；环境变量合并进当前进程环境
    env_vars: dict = dict(term_env.get("env_vars") or {}) if term_env else {}
    env_cwd_abs = ""
    if term_env and not cwd_rel:
        env_cwd_abs = _terms.resolve_env_cwd_env(term_env)[0]
    if env_cwd_abs:
        p = Path(env_cwd_abs)
    elif cwd_rel:
        p = resolve_ws(ctx, cwd_rel)
    else:
        p = Path(ctx.workspace).resolve()

    # v8.25 用户文件保护：命令触碰用户资产（PPT/Excel/PDF等）直接拦截，不进审批
    if getattr(ctx.cfg, "ENABLE_USER_FILE_PROTECT", True):
        try:
            from . import file_protect as _fp
            _hits = _fp.command_touches_user_asset(cmd, ctx.workspace or ctx.cfg.workspace)
            if _hits:
                return {"ok": False,
                        "output": ("[用户文件保护] 命令涉及用户手工资产（"
                                   + "、".join(_hits[:5])
                                   + "），AI不允许执行相关命令（防覆盖用户修改）。"
                                     "请先提示用户“一键备份完整工作区”，并由用户手动执行该命令；"
                                     "如确需AI执行，请用户备份后在审批中明确授权。")}
        except Exception:
            pass
    # v8.5.x 审查修复：移除 LLM 可控的 _pre_approved 绕过——审批门是否放行统一由
    # _request_approval 内部四模式路由决定（ENABLE_APPROVAL/free/danger/copilot 均在其内处理）。
    approved = await _request_approval(
        ctx, "run_command", {"command": cmd, "cwd": cwd_rel or ".", "timeout": int(timeout)}
    )
    if not approved:
        return {"ok": False, "output": "用户拒绝了命令执行。", "meta": {"denied": True}}
    # v6.1 P2 修正：mkdir 副作用移到审批通过之后（拒绝不落盘）
    if cwd_rel:
        p.mkdir(parents=True, exist_ok=True)

    if background:
        if getattr(ctx.cfg, "low_memory_mode", False):
            # v6 低内存模式：不起额外后台进程，直接串行执行
            return await _exec_command(ctx, cmd, p, timeout, env_vars, update_interval, kill_after, warnings)
        from .background import get_bg_jobs  # v6 后台长任务登记
        jobs = get_bg_jobs()
        job_id = jobs.register(cmd)
        task = asyncio.create_task(_exec_command(ctx, cmd, p, timeout, env_vars, update_interval, kill_after, warnings))

        def _done(t):
            try:
                if t.cancelled():
                    jobs.finish(job_id, False, "已取消")
                elif t.exception() is not None:
                    jobs.finish(job_id, False, str(t.exception()))
                else:
                    r = t.result()
                    jobs.finish(job_id, bool(r.get("ok")), str(r.get("output", ""))[-800:])
            except Exception as e:  # 兜底：完成回调不得静默丢状态
                jobs.finish(job_id, False, str(e))

        task.add_done_callback(_done)
        # v8.14：持强引用防 GC——asyncio 文档明确警告仅挂回调的 task 可能被
        # 事件循环垃圾回收，表现为后台命令静默消失
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)
        return {"ok": True, "output": f"命令已在后台启动（异步构建，编号 {job_id}），"
                                       f"可回主对话继续其他事，完成后会自动提示：{cmd}"}
    return await _exec_command(ctx, cmd, p, timeout, env_vars, update_interval, kill_after, warnings)


def _kill_tree(proc) -> None:
    """终止进程树（Windows 用 taskkill /T 连子进程一起杀，避免孤儿进程）。

    v8.15 检修：内部 subprocess.run(taskkill, timeout=10) 是同步阻塞——
    在事件循环线程直接调用会冻结 GUI 最长 10s+，调用方一律走
    `await asyncio.to_thread(_kill_tree, proc)`。
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
            return
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


async def _exec_command(ctx: ToolContext, cmd: str, cwd: Path, timeout: float, env_vars: dict | None = None, update_interval: float = 0.0, kill_after: bool = True, warnings: list | None = None) -> dict:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    spawn_env = None
    if env_vars:
        spawn_env = {**os.environ, **{str(k): str(v) for k, v in env_vars.items()}}
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=str(cwd),
            env=spawn_env,
            creationflags=creationflags,
        )
    except OSError as e:
        return _format_error(e)
    # v8.18：默认值 warning 先行下发（AI 可见）
    if warnings:
        await _emit(ctx, {"type": "cmd_warning", "call_id": ctx.call_id, "warnings": warnings})
    lines = []
    truncated = 0
    total_bytes = 0
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    timed_out = False
    left_running = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            # v8.18：update_interval>0 时按间隔分片等待——静默期到点发心跳（K210 刷写静默场景）
            chunk = min(remaining, update_interval) if update_interval > 0 else remaining
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), chunk)
            except asyncio.TimeoutError:
                if update_interval > 0 and time.monotonic() < deadline:
                    tail = "\n".join(lines[-8:]) if lines else ""
                    await _emit(ctx, {"type": "cmd_update", "call_id": ctx.call_id,
                                     "elapsed_s": round(time.monotonic() - started, 1), "tail": tail})
                    continue
                timed_out = True
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            # M: 限制回传行数与总字节（单行巨量输出不得整行入内存/上下文）
            if len(text) > 64 * 1024:
                text = text[:64 * 1024] + "…(单行已截断)"
                truncated += 1
            total_bytes += len(text) + 1
            if total_bytes > 8 * 1024 * 1024:
                if kill_after:
                    await asyncio.to_thread(_kill_tree, proc)
                truncated += 1
                timed_out = True
                break
            if len(lines) < 2000:
                lines.append(text)
            else:
                truncated += 1
            await _emit(ctx, {"type": "cmd_output", "call_id": ctx.call_id, "line": text})
        if timed_out:
            # v8.18：kill_after=false → 超时不杀树，进程保留后台运行
            if kill_after:
                await asyncio.to_thread(_kill_tree, proc)
            else:
                left_running = True
        if left_running:
            rc = -1
        else:
            # M-4: EOF 后进程可能仍存活（如 daemonize），wait 必须限时避免"假卡死"
            try:
                rc = await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                if kill_after:
                    await asyncio.to_thread(_kill_tree, proc)
                    try:
                        rc = await asyncio.wait_for(proc.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        rc = -1
                    timed_out = True
                else:
                    left_running = True
                    rc = -1
    except BaseException:
        # S3: CancelledError 继承自 BaseException，必须兜底杀子进程，避免孤儿进程
        if proc.returncode is None:
            await asyncio.to_thread(_kill_tree, proc)
        try:
            rc = await asyncio.wait_for(proc.wait(), timeout=10)
        except BaseException:
            rc = -1
        timed_out = True
        raise
    output = "\n".join(lines)
    if truncated:
        output += f"\n…(输出过长，已省略 {truncated} 行)"
    if timed_out:
        kept = "（kill_after=false：进程已保留后台运行）" if left_running else ""
        output += f"\n[命令超时已熔断，> {int(timeout)}s]{kept}"
    # v8.18：默认值 warning 注入输出首行（AI 可见并可在下次显式传参）
    warn_line = ("[WARN] 使用默认值：" + "；".join(warnings) + "\n") if warnings else ""
    await _emit(ctx, {"type": "cmd_done", "call_id": ctx.call_id, "rc": rc, "left_running": left_running})
    try:
        cwd_rel = str(cwd.resolve().relative_to(Path(ctx.workspace).resolve())).replace("\\", "/")
    except Exception:
        cwd_rel = "."
    meta = {"rc": rc, "cwd": cwd_rel, "left_running": left_running}
    if warnings:
        meta["warnings"] = warnings
    return {
        "ok": rc == 0,
        "output": warn_line + (f"[退出码 {rc}] cwd={cwd_rel}\n{output}" if output else f"[退出码 {rc}] cwd={cwd_rel}\n(无输出)"),
        "meta": meta,
    }



# ---------------- v8.18 桌面端持久命令行环境 ----------------
async def tool_term_create(args, ctx: ToolContext) -> dict:
    """创建一个持久命令行环境（记住工作目录与环境变量）。"""
    block = _readonly_block("term_create")
    if block:
        return block
    from . import terms as _terms
    try:
        env = _terms.create_env(
            name=str(args.get("name") or ""),
            cwd_rel=str(args.get("cwd") or ""),
            env_vars=args.get("env_vars") if args.get("env_vars") is not None else None,
        )
    except ValueError as e:
        return {"ok": False, "output": str(e)}
    except Exception as e:
        return _format_error(e)
    return {"ok": True, "output": "环境已创建：" + env["name"] + "（id: " + env["id"] + "）"}


async def tool_term_list(args, ctx: ToolContext) -> dict:
    """列出所有持久命令行环境。"""
    from . import terms as _terms
    try:
        envs = _terms.list_envs()
    except Exception as e:
        return _format_error(e)
    if not envs:
        return {"ok": True, "output": "暂无持久命令行环境（可用 term_create 创建）。"}
    lines = []
    for e in envs:
        rc = e.get("last_rc")
        lines.append(
            "- [" + e["id"] + "] " + e["name"] + " | cwd=" + (e.get("cwd_rel") or ".")
            + " | last=" + (e.get("last_cmd") or "-")
            + " | rc=" + (str(rc) if rc is not None else "-")
        )
    return {"ok": True, "output": "持久命令行环境（" + str(len(envs)) + "个：" + "\n".join(lines)}


async def tool_term_delete(args, ctx: ToolContext) -> dict:
    """删除一个持久命令行环境。"""
    block = _readonly_block("term_delete")
    if block:
        return block
    from . import terms as _terms
    eid = str(args.get("id") or "").strip()
    if not eid:
        return {"ok": False, "output": "缺少 id 参数"}
    try:
        ok = _terms.delete_env(eid)
    except Exception as e:
        return _format_error(e)
    if not ok:
        return {"ok": False, "output": "环境不存在：" + eid}
    return {"ok": True, "output": "已删除环境：" + eid}


async def tool_workspace_info(args, ctx: ToolContext) -> dict:
    py = sys.version.split()[0]
    # v6.2 相对路径安全：AI 只见工作区代号，绝对路径不暴露
    from .codename import workspace_codename
    info = {
        "workspace": workspace_codename(),
        "os": f"{platform.system()} {platform.release()}",
        "python": py,
    }
    return {"ok": True, "output": json.dumps(info, ensure_ascii=False, indent=2), "meta": info}


async def tool_search_vault(args, ctx: ToolContext) -> dict:
    if not ctx.cfg.ENABLE_VAULT or ctx.vault is None:
        return {"ok": False, "output": "资产银行未启用。"}
    query = args.get("query", "")
    limit = min(_safe_int(args.get("limit"), 5), 10)
    # M12: 低于复用阈值的不返回，避免无关资产污染上下文；检索可能走 embedding API，丢线程防阻塞 loop
    hits = await asyncio.to_thread(
        ctx.vault.search, query, limit=limit, threshold=ctx.cfg.vault_threshold)
    if not hits:
        return {"ok": True, "output": f"资产银行中未找到相似度≥{ctx.cfg.vault_threshold} 的相关资产。"}
    lines = ["[资产银行检索结果]"]
    for h in hits:
        a = h["asset"]
        lines.append(
            f"- {a.get('title')} (相似度 {h['score']}) kind={a.get('kind')}\n"
            f"  描述: {a.get('description', '')[:100]}\n"
            f"  标签: {', '.join(a.get('tags', []))} | 场景: {a.get('scene', '')}"
        )
    return {"ok": True, "output": "\n".join(lines), "meta": {"hits": hits}}


async def tool_store_asset(args, ctx: ToolContext) -> dict:
    block = _readonly_block("store_asset")
    if block:
        return block
    if not ctx.cfg.ENABLE_VAULT or ctx.vault is None:
        return {"ok": False, "output": "资产银行未启用。"}
    asset = {
        "kind": args.get("kind") or "code",
        "title": args.get("title") or "未命名资产",
        "description": args.get("description") or args.get("prompt") or "",
        "tags": args.get("tags") or [],
        "scene": args.get("scene") or "",
        "prompt": args.get("prompt") or "",
        "content": args.get("content") or "",
    }
    if not asset["content"] and args.get("source_path"):
        sp = resolve_ws(ctx, args["source_path"])
        # v8.13.1：受保护数据不可复制进资产银行（防密钥/用户数据间接外流）
        if _is_read_protected(ctx, sp):
            return {"ok": False, "output": f"该文件属于受保护数据，禁止读取: {args['source_path']}"}
        asset["content"] = read_text(sp)
    ctx.vault.add(asset)
    await _emit(ctx, {"type": "vault_stored", "asset": asset})
    return {"ok": True, "output": f"已存入资产银行：{asset['title']}"}


async def tool_delegate_task(args, ctx: ToolContext) -> dict:
    """子Agent 委派：在全新上下文中执行任务（可执行命令行/长任务），仅回传紧凑结果。"""
    if not ctx.cfg.ENABLE_SUBAGENT:
        return {"ok": False, "output": "子Agent 功能未启用。"}
    if ctx.depth >= 3:  # M15: 防无限递归嵌套
        return {"ok": False, "output": "子Agent 嵌套层级过深（>3），已拒绝继续委派。请直接在当前上下文完成。"}
    task = args.get("task", "")
    if not task:
        return {"ok": False, "output": "任务描述为空。"}
    context_info = args.get("context") or ""
    from .agent import Agent  # 延迟导入避免循环依赖

    sub = Agent(
        ctx.cfg,
        emit=None,
        is_sub=True,
        sub_label="子Agent",
        parent_emit=ctx.emit,
        vault=ctx.vault,
        approval=ctx.approval,  # 共享父级审批门
        depth=ctx.depth + 1,
    )
    await _emit(ctx, {"type": "subagent_start", "label": "子Agent", "task": task})
    try:
        result = await sub.run_task(task, extra_context=context_info, max_rounds=8)
    except LLMError as e:
        return {"ok": False, "output": f"子Agent 失败: {e}"}
    await _emit(ctx, {"type": "subagent_done", "label": "子Agent", "summary": result.summary})
    return {"ok": True, "output": result.summary}


async def tool_plan_and_execute(args, ctx: ToolContext) -> dict:
    """AOE 规划执行：拆解 DAG → 强制查库(复用拦截) → 并行执行 → 汇总（4.1）。"""
    if not ctx.cfg.ENABLE_AOE:
        return {"ok": False, "output": "AOE 规划器未启用。"}
    if ctx.depth >= 3:  # M15: 防无限递归嵌套
        return {"ok": False, "output": "AOE 嵌套层级过深（>3），已拒绝继续规划。请直接在当前上下文完成。"}
    task = args.get("task", "")
    if not task:
        return {"ok": False, "output": "任务描述为空。"}
    from .planner import execute_aoe  # 延迟导入避免循环依赖

    result = await execute_aoe(ctx.cfg, task, emit=ctx.emit, vault=ctx.vault, parent_agent=ctx.agent, depth=ctx.depth + 1)
    return {"ok": True, "output": result.summary, "meta": {"plan": result.plan}}


# --------------------------------------------------------------------------
# v5 新工具：互联网搜索 / 文件搜索 / 待办 / 端口 / 工具查找
# --------------------------------------------------------------------------
async def tool_web_search(args, ctx: ToolContext) -> dict:
    """互联网搜索（三级回退）；结果可交助手模型压缩。"""
    if not getattr(ctx.cfg, "ENABLE_WEB_SEARCH", True):
        return {"ok": False, "output": "互联网搜索未启用。"}
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "output": "query 为空。"}
    if not await _diplomatic_guard(ctx, "web_search", args):
        return {"ok": False, "output": "外交型任务被拦截：AI 判定搜索词与用户要求不符。", "meta": {"denied": True}}
    from .search_tools import web_search_raw, summarize_with_helper
    results, channel = await web_search_raw(ctx.cfg, query, limit=min(_safe_int(args.get("limit"), 6), 10))
    if not results:
        return {"ok": False, "output": f"搜索失败或无结果（{channel}）。可换个关键词或稍后重试。"}
    raw = "\n".join(f"- {r['title']}\n  {r['url']}\n  {r.get('snippet', '')}" for r in results)
    if _strict_bool(args.get("summarize", True)):  # v8.14：严格布尔，"false" 字符串不再为真
        raw = await summarize_with_helper(ctx.cfg, raw, query)
    return {"ok": True, "output": f"[搜索：{query} | 通道：{channel}]\n{raw}",
            "meta": {"results": results, "channel": channel}}


# --------------------------------------------------------------------------
# v6.6 浏览器控制（超轻量：复用系统 Edge/Chrome headless，零新依赖）
# 全部为外交型任务：审批门全放行，但 AI 检测到不完全符合用户要求立即拦截。
# --------------------------------------------------------------------------
async def tool_browser_open(args, ctx: ToolContext) -> dict:
    """真实打开系统浏览器访问 URL（用户可见、可交互）。外交型：全放行+AI 检测不符即拦截。"""
    if not await _diplomatic_guard(ctx, "browser_open", args):
        return {"ok": False, "output": "外交型任务被拦截：AI 判定与用户要求不符，或参数不合规。", "meta": {"denied": True}}
    from .browser import browser_open
    return await asyncio.to_thread(browser_open, str(args.get("url") or ""))


async def tool_browser_read(args, ctx: ToolContext) -> dict:
    """无头渲染页面并提取正文文本（JS 执行后的真实内容）。外交型：全放行+AI 检测不符即拦截。"""
    if not await _diplomatic_guard(ctx, "browser_read", args):
        return {"ok": False, "output": "外交型任务被拦截：AI 判定与用户要求不符，或参数不合规。", "meta": {"denied": True}}
    from .browser import browser_read
    return await asyncio.to_thread(browser_read, str(args.get("url") or ""),
                                   _safe_int(args.get("timeout"), 30))


async def tool_browser_screenshot(args, ctx: ToolContext) -> dict:
    """无头截图保存到工作区（path 为相对工作区的路径，如 docs/page.png）。外交型：全放行+AI 检测不符即拦截。"""
    block = _readonly_block("browser_screenshot")
    if block:
        return block
    if not await _diplomatic_guard(ctx, "browser_screenshot", args):
        return {"ok": False, "output": "外交型任务被拦截：AI 判定与用户要求不符，或参数不合规。", "meta": {"denied": True}}
    from .browser import browser_screenshot
    rel = str(args.get("path") or "").strip()
    if not rel:
        return {"ok": False, "output": "缺少保存路径（path，相对工作区）。"}
    if not rel.lower().endswith((".png", ".jpg", ".jpeg")):
        return {"ok": False, "output": "path 必须以 .png/.jpg/.jpeg 结尾。"}
    try:
        p = resolve_ws(ctx, rel)
    except PermissionError as e:
        return {"ok": False, "output": str(e)}
    if _is_protected(ctx, p):
        return {"ok": False, "output": f"该路径属于系统受保护目录，禁止写入截图: {rel}"}
    p.parent.mkdir(parents=True, exist_ok=True)
    return await asyncio.to_thread(browser_screenshot, str(args.get("url") or ""), str(p),
                                   _safe_int(args.get("timeout"), 30))


# --------------------------------------------------------------------------
# v8.6 外部程序/浏览器自动化：AI 操作「某一个 exe」与浏览器（界面转可点击点）
# 安全红线：ENABLE_UI_AUTOMATION 开关（默认关）+ 副驾驶全程监督 + 危险动作硬拦截。
# --------------------------------------------------------------------------
# v8.6 外部程序自动化：exe_launch 启动成功的进程 pid 白名单（exe_close 只能关这些进程）
# v8.7 审查修复：dict[pid->ts] + 上限淘汰（防只增不减 + pid 复用误杀）；terminate 成功后移除
_EXE_LAUNCHED: dict = {}
_EXE_LAUNCHED_MAX = 64


def _register_launched_pid(pid: int) -> None:
    if len(_EXE_LAUNCHED) >= _EXE_LAUNCHED_MAX:
        oldest = min(_EXE_LAUNCHED, key=lambda p: _EXE_LAUNCHED[p])
        _EXE_LAUNCHED.pop(oldest, None)
    _EXE_LAUNCHED[pid] = time.time()


def _ui_auto_hard_check(tool: str, payload: dict, cfg) -> str:
    """UI 自动化硬规则前置校验（不依赖 LLM）：不合规返回拦截理由，合规返回 ""。"""
    if tool == "exe_launch":
        path = str((payload or {}).get("path") or "").strip()
        if not path:
            return "缺少可执行文件路径 path"
        allowed = str(getattr(cfg, "ui_automation_exe", "") or "").strip().strip("\"'")
        if not allowed:
            return "未配置允许启动的外部程序（ui_automation_exe），exe_launch 已禁用。"
        try:
            if Path(path.strip("\"'")).resolve() != Path(allowed).resolve():
                return "exe_launch 只允许启动用户配置的唯一外部程序（ui_automation_exe）。"
        except Exception:
            return "exe 路径非法。"
    if tool == "exe_type":
        text = str((payload or {}).get("text") or "")
        if len(text) > 2000:
            return "输入文本过长（≤2000 字符）。"
    return ""


async def _ui_auto_approval(ctx: ToolContext, tool: str, payload: dict, note: str) -> bool:
    """弹用户审批；无审批门/超时/取消一律拒绝并清理 pending（绝不悬挂）。"""
    if ctx.approval is None:
        return False
    try:
        fut = ctx.approval.request(ctx.call_id)
        await _emit(ctx, {"type": "approval_needed", "call_id": ctx.call_id, "tool": tool,
                          "payload": payload, "note": note})
        return bool(await asyncio.wait_for(fut, timeout=600.0))
    except asyncio.TimeoutError:
        ctx.approval.resolve(ctx.call_id, False)
        return False
    except BaseException:
        ctx.approval.resolve(ctx.call_id, False)
        raise


async def _ui_automation_guard(ctx: ToolContext, tool: str, payload: dict) -> bool:
    """v8.6 UI 自动化守卫：开关 + 硬规则 + 副驾驶全程监督 + 审批模式路由。

    用户要求：AI 操作外部 exe/浏览器时，copilot 全程盯着做主，禁止危险动作、禁止外传信息。
    v8.7 审查修复：与审批门四模式对齐——approval_mode=all 必弹窗；danger 模式下
    危险载荷（进程终止/带参启动）强制弹窗；free 全放行；copilot 模式由副驾驶判定。
    """
    if not getattr(ctx.cfg, "ENABLE_UI_AUTOMATION", False):
        await _emit(ctx, {"type": "ui_auto_block", "tool": tool,
                          "note": "UI 自动化未启用（ENABLE_UI_AUTOMATION=False），已拦截。"})
        return False
    reason = _ui_auto_hard_check(tool, payload, ctx.cfg)
    if reason:
        await _emit(ctx, {"type": "ui_auto_block", "tool": tool, "note": reason})
        return False
    try:
        from .experts import copilot_check  # 延迟导入避免循环依赖
        verdict = await copilot_check(ctx.cfg, {
            "kind": "ui_automation", "tool": tool, "payload": payload,
            "user_intent": (ctx.user_intent or "")[:400],
        })
    except Exception:
        verdict = {"kill": False, "uncertain": True, "note": "副驾驶不可用，升级用户审批"}
    if verdict.get("kill"):
        await _emit(ctx, {"type": "ui_auto_block", "tool": tool,
                          "note": verdict.get("note", "") or "副驾驶判定存在风险，已拦截"})
        return False
    if verdict.get("uncertain"):
        return await _ui_auto_approval(ctx, tool, payload,
                                       note="UI 自动化：副驾驶无法确认是否安全，请人工裁决")
    # 与 _request_approval 四模式同源：ENABLE_APPROVAL=False 全放行；
    # danger 模式下写操作（点击/输入/按键/关闭/终止）一律视为危险载荷，必须用户确认
    if not getattr(ctx.cfg, "ENABLE_APPROVAL", True):
        return True
    mode = getattr(ctx.cfg, "approval_mode", "danger") or "danger"
    if mode == "free":
        return True
    if mode == "all":
        return await _ui_auto_approval(ctx, tool, payload, note="UI 自动化：请确认执行")
    _UI_WRITE_TOOLS = {
        "exe_click", "exe_type", "exe_press_keys", "exe_close",
        "ui_control_click", "ui_control_set_text",
    }
    if mode == "danger" and (is_dangerous(payload) or tool in _UI_WRITE_TOOLS):
        return await _ui_auto_approval(ctx, tool, payload, note="UI 自动化：写操作/危险动作，请确认执行")
    return True


async def tool_browser_elements(args, ctx: ToolContext) -> dict:
    """无头渲染页面并抽取可交互元素（点击点），供 AI 理解页面结构。外交型：全放行+AI 检测不符即拦截。"""
    if not await _diplomatic_guard(ctx, "browser_elements", args):
        return {"ok": False, "output": "外交型任务被拦截：AI 判定与用户要求不符，或参数不合规。", "meta": {"denied": True}}
    from .browser import browser_elements
    return await asyncio.to_thread(browser_elements, str(args.get("url") or ""),
                                   _safe_int(args.get("timeout"), 30))


# --------------------------------------------------------------------------
# v8.9 直接操控浏览器（CDP：launch/navigate/click/type/press_keys/close）
# 安全：URL 与 selector 参数均过 _diplomatic_guard；点击/输入/按键为写操作，走外交型合规。
# --------------------------------------------------------------------------
async def tool_browser_launch(args, ctx: ToolContext) -> dict:
    """启动一个可被直接操控的浏览器窗口（CDP，仅 127.0.0.1 调试口）。"""
    block = _readonly_block("browser_launch")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_CTL", True) or not getattr(ctx.cfg, "ENABLE_BROWSER", True):
        return {"ok": False, "output": "直接操控浏览器未开启（ENABLE_BROWSER_CTL/ENABLE_BROWSER）。"}
    # def 中 url 非必填：先补默认值再走外交型硬校验，否则 LLM 不传 url 必被拦截
    args = dict(args)
    args.setdefault("url", "http://127.0.0.1:8765")
    if not await _diplomatic_guard(ctx, "browser_launch", args):
        return {"ok": False, "output": "外交型任务被拦截：AI 判定与用户要求不符，或参数不合规。", "meta": {"denied": True}}
    from . import browser_ctl
    url = str(args.get("url") or "http://127.0.0.1:8765")
    # v8.15: 默认无头，被拦截时自动回退到有头+反检测模式
    return await asyncio.to_thread(browser_ctl.launch_with_fallback, url)


async def tool_browser_navigate(args, ctx: ToolContext) -> dict:
    """受控浏览器跳转到新 URL。"""
    block = _readonly_block("browser_navigate")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_CTL", True) or not getattr(ctx.cfg, "ENABLE_BROWSER", True):
        return {"ok": False, "output": "直接操控浏览器未开启。"}
    if not await _diplomatic_guard(ctx, "browser_navigate", args):
        return {"ok": False, "output": "外交型任务被拦截：AI 判定与用户要求不符，或参数不合规。", "meta": {"denied": True}}
    from . import browser_ctl
    return await asyncio.to_thread(browser_ctl.navigate, str(args.get("url") or ""))


async def tool_browser_click(args, ctx: ToolContext) -> dict:
    """受控浏览器点击元素（selector=CSS 选择器，text=可见文本）。"""
    block = _readonly_block("browser_click")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_CTL", True) or not getattr(ctx.cfg, "ENABLE_BROWSER", True):
        return {"ok": False, "output": "直接操控浏览器未开启。"}
    if not await _diplomatic_guard(ctx, "browser_click", args):
        return {"ok": False, "output": "外交型任务被拦截：AI 判定与用户要求不符，或参数不合规。", "meta": {"denied": True}}
    from . import browser_ctl
    return await asyncio.to_thread(browser_ctl.click,
                                   str(args.get("selector") or ""),
                                   str(args.get("text") or ""))


async def tool_browser_type(args, ctx: ToolContext) -> dict:
    """向受控浏览器当前焦点元素输入文本。"""
    block = _readonly_block("browser_type")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_CTL", True) or not getattr(ctx.cfg, "ENABLE_BROWSER", True):
        return {"ok": False, "output": "直接操控浏览器未开启。"}
    if not await _diplomatic_guard(ctx, "browser_type", args):
        return {"ok": False, "output": "外交型任务被拦截：AI 判定与用户要求不符，或参数不合规。", "meta": {"denied": True}}
    from . import browser_ctl
    return await asyncio.to_thread(browser_ctl.type_text, str(args.get("text") or ""))


async def tool_browser_press_keys(args, ctx: ToolContext) -> dict:
    """受控浏览器按键（enter/tab/方向键/组合键 ctrl,c 等）。"""
    block = _readonly_block("browser_press_keys")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_CTL", True) or not getattr(ctx.cfg, "ENABLE_BROWSER", True):
        return {"ok": False, "output": "直接操控浏览器未开启。"}
    if not await _diplomatic_guard(ctx, "browser_press_keys", args):
        return {"ok": False, "output": "外交型任务被拦截：AI 判定与用户要求不符，或参数不合规。", "meta": {"denied": True}}
    from . import browser_ctl
    return await asyncio.to_thread(browser_ctl.press_keys, str(args.get("keys") or ""))


async def tool_browser_close(args, ctx: ToolContext) -> dict:
    """关闭受控浏览器并清理临时 profile。"""
    block = _readonly_block("browser_close")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_CTL", True) or not getattr(ctx.cfg, "ENABLE_BROWSER", True):
        return {"ok": False, "output": "直接操控浏览器未开启。"}
    if not await _diplomatic_guard(ctx, "browser_close", args):
        return {"ok": False, "output": "外交型任务被拦截：AI 判定与用户要求不符，或参数不合规。", "meta": {"denied": True}}
    from . import browser_ctl
    return await asyncio.to_thread(browser_ctl.close)


# --------------------------------------------------------------------------
# v8.14 F12 开发者工具（Networks / Storage / Console / Sources）
# 安全：基于已启动的 CDP 浏览器会话；仅操作用户已授权的服务
# --------------------------------------------------------------------------
async def tool_browser_networks_start(args, ctx: ToolContext) -> dict:
    """开始捕获受控浏览器的网络请求（F12 Networks 面板）。"""
    block = _readonly_block("browser_networks_start")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_DEVTOOLS", True):
        return {"ok": False, "output": "F12 开发者工具未开启（ENABLE_BROWSER_DEVTOOLS）。"}
    from . import browser_devtools
    return await asyncio.to_thread(browser_devtools.networks_start)


async def tool_browser_networks_stop(args, ctx: ToolContext) -> dict:
    """停止网络请求捕获并返回已捕获的请求（F12 Networks 面板）。"""
    block = _readonly_block("browser_networks_stop")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_DEVTOOLS", True):
        return {"ok": False, "output": "F12 开发者工具未开启。"}
    from . import browser_devtools
    return await asyncio.to_thread(browser_devtools.networks_stop)


async def tool_browser_networks_get(args, ctx: ToolContext) -> dict:
    """获取已捕获的网络请求（支持 URL/类型/方法/状态码过滤）。"""
    block = _readonly_block("browser_networks_get")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_DEVTOOLS", True):
        return {"ok": False, "output": "F12 开发者工具未开启。"}
    from . import browser_devtools
    return await asyncio.to_thread(
        browser_devtools.networks_get,
        str(args.get("filter_url") or ""),
        str(args.get("filter_type") or ""),
        str(args.get("filter_method") or ""),
        int(args.get("status_code") or 0),
        int(args.get("max_results") or 50),
    )


async def tool_browser_storage_get(args, ctx: ToolContext) -> dict:
    """获取页面存储信息（F12 Storage 面板：cookies/localStorage/sessionStorage）。"""
    block = _readonly_block("browser_storage_get")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_DEVTOOLS", True):
        return {"ok": False, "output": "F12 开发者工具未开启。"}
    from . import browser_devtools
    return await asyncio.to_thread(
        browser_devtools.storage_get,
        str(args.get("storage_type") or "all"),
        str(args.get("url") or ""),
    )


async def tool_browser_storage_set(args, ctx: ToolContext) -> dict:
    """设置页面存储项（F12 Storage 面板）。"""
    block = _readonly_block("browser_storage_set")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_DEVTOOLS", True):
        return {"ok": False, "output": "F12 开发者工具未开启。"}
    from . import browser_devtools
    return await asyncio.to_thread(
        browser_devtools.storage_set,
        str(args.get("storage_type") or "localStorage"),
        str(args.get("key") or ""),
        str(args.get("value") or ""),
        str(args.get("url") or ""),
    )


async def tool_browser_storage_clear(args, ctx: ToolContext) -> dict:
    """清除页面存储（F12 Storage 面板）。"""
    block = _readonly_block("browser_storage_clear")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_DEVTOOLS", True):
        return {"ok": False, "output": "F12 开发者工具未开启。"}
    from . import browser_devtools
    return await asyncio.to_thread(
        browser_devtools.storage_clear,
        str(args.get("storage_type") or "all"),
    )


async def tool_browser_console_get(args, ctx: ToolContext) -> dict:
    """获取控制台日志（F12 Console 面板）。"""
    block = _readonly_block("browser_console_get")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_DEVTOOLS", True):
        return {"ok": False, "output": "F12 开发者工具未开启。"}
    from . import browser_devtools
    return await asyncio.to_thread(
        browser_devtools.console_get,
        int(args.get("max_entries") or 100),
    )


async def tool_browser_console_eval(args, ctx: ToolContext) -> dict:
    """在页面上下文中执行 JavaScript（F12 Console 面板）。"""
    block = _readonly_block("browser_console_eval")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_DEVTOOLS", True):
        return {"ok": False, "output": "F12 开发者工具未开启。"}
    from . import browser_devtools
    return await asyncio.to_thread(
        browser_devtools.console_eval,
        str(args.get("expression") or ""),
        float(args.get("timeout") or 15.0),
    )


async def tool_browser_sources_list(args, ctx: ToolContext) -> dict:
    """获取页面加载的 JavaScript 源文件列表（F12 Sources 面板）。"""
    block = _readonly_block("browser_sources_list")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_DEVTOOLS", True):
        return {"ok": False, "output": "F12 开发者工具未开启。"}
    from . import browser_devtools
    return await asyncio.to_thread(
        browser_devtools.sources_list,
        str(args.get("filter_pattern") or ""),
    )


async def tool_browser_sources_get(args, ctx: ToolContext) -> dict:
    """获取指定 JavaScript 源文件的内容（F12 Sources 面板）。"""
    block = _readonly_block("browser_sources_get")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_DEVTOOLS", True):
        return {"ok": False, "output": "F12 开发者工具未开启。"}
    from . import browser_devtools
    return await asyncio.to_thread(
        browser_devtools.sources_get,
        str(args.get("url_or_index") or ""),
        int(args.get("max_len") or 10000),
    )


async def tool_browser_find_api_endpoints(args, ctx: ToolContext) -> dict:
    """从已捕获的网络请求中找出疑似 API 端点（用户已付费/已授权的服务）。"""
    block = _readonly_block("browser_find_api_endpoints")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_BROWSER_DEVTOOLS", True):
        return {"ok": False, "output": "F12 开发者工具未开启。"}
    from . import browser_devtools
    return await asyncio.to_thread(
        browser_devtools.find_api_endpoints,
        str(args.get("filter_pattern") or "api"),
        int(args.get("min_response_size") or 0),
    )


async def tool_exe_launch(args, ctx: ToolContext) -> dict:
    block = _readonly_block("exe_launch")
    if block:
        return block
    # v8.7：带参/自定义 cwd 启动按危险处理（danger 模式强制弹窗）
    if args.get("args") or args.get("cwd"):
        args = dict(args)
        args["dangerous"] = True
    if not await _ui_automation_guard(ctx, "exe_launch", args):
        return {"ok": False, "output": "UI 自动化被拦截。", "meta": {"denied": True}}
    from . import win_automate
    arg_list = _as_str_list(args.get("args"))
    res = await asyncio.to_thread(win_automate.launch_exe,
                                  str(args.get("path") or ""),
                                  arg_list,
                                  str(args.get("cwd") or ""))
    if res.get("ok"):
        try:
            _register_launched_pid(int(res.get("pid")))
        except (TypeError, ValueError):
            pass
    return res


async def tool_exe_list_windows(args, ctx: ToolContext) -> dict:
    if not await _ui_automation_guard(ctx, "exe_list_windows", args):
        return {"ok": False, "output": "UI 自动化被拦截。", "meta": {"denied": True}}
    from . import win_automate
    return await asyncio.to_thread(win_automate.list_windows)


async def tool_exe_screenshot(args, ctx: ToolContext) -> dict:
    block = _readonly_block("exe_screenshot")
    if block:
        return block
    if not await _ui_automation_guard(ctx, "exe_screenshot", args):
        return {"ok": False, "output": "UI 自动化被拦截。", "meta": {"denied": True}}
    from . import win_automate
    rel = str(args.get("path") or "").strip()
    if not rel.lower().endswith((".png", ".jpg", ".jpeg")):
        return {"ok": False, "output": "path 必须以 .png/.jpg/.jpeg 结尾。"}
    try:
        p = resolve_ws(ctx, rel)
    except PermissionError as e:
        return {"ok": False, "output": str(e)}
    if _is_protected(ctx, p):
        return {"ok": False, "output": f"该路径属于系统受保护目录，禁止写入截图: {rel}"}
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        hwnd = int(args.get("hwnd") or 0)
    except (TypeError, ValueError):
        hwnd = 0
    if not hwnd:
        hwnd = win_automate.find_window(str(args.get("title") or ""))
    if not hwnd:
        return {"ok": False, "output": "未找到目标窗口（请先用 exe_list_windows 查 hwnd，或提供 title）。"}
    return await asyncio.to_thread(win_automate.screenshot_window, hwnd, str(p))


async def tool_exe_click(args, ctx: ToolContext) -> dict:
    block = _readonly_block("exe_click")
    if block:
        return block
    if not await _ui_automation_guard(ctx, "exe_click", args):
        return {"ok": False, "output": "UI 自动化被拦截。", "meta": {"denied": True}}
    from . import win_automate
    try:
        x = int(args.get("x") or -1)
        y = int(args.get("y") or -1)
    except (TypeError, ValueError):
        return {"ok": False, "output": "x/y 必须是整数坐标。"}
    if x < 0 or y < 0:
        return {"ok": False, "output": "缺少有效坐标 x/y（≥0）。"}
    sw, sh = win_automate.screen_size()
    if sw > 0 and sh > 0 and (x >= sw or y >= sh):
        return {"ok": False, "output": f"坐标 ({x},{y}) 超出屏幕范围（{sw}x{sh}）。"}
    return await asyncio.to_thread(win_automate.click, x, y)


async def tool_exe_type(args, ctx: ToolContext) -> dict:
    block = _readonly_block("exe_type")
    if block:
        return block
    if not await _ui_automation_guard(ctx, "exe_type", args):
        return {"ok": False, "output": "UI 自动化被拦截。", "meta": {"denied": True}}
    from . import win_automate
    return await asyncio.to_thread(win_automate.type_text, str(args.get("text") or ""))


async def tool_exe_press_keys(args, ctx: ToolContext) -> dict:
    block = _readonly_block("exe_press_keys")
    if block:
        return block
    if not await _ui_automation_guard(ctx, "exe_press_keys", args):
        return {"ok": False, "output": "UI 自动化被拦截。", "meta": {"denied": True}}
    from . import win_automate
    return await asyncio.to_thread(win_automate.press_keys, str(args.get("keys") or ""))


async def tool_exe_close(args, ctx: ToolContext) -> dict:
    block = _readonly_block("exe_close")
    if block:
        return block
    # v8.7 审查修复：进程终止属破坏性操作，danger 模式强制弹窗
    args = dict(args)
    args["dangerous"] = True
    if not await _ui_automation_guard(ctx, "exe_close", args):
        return {"ok": False, "output": "UI 自动化被拦截。", "meta": {"denied": True}}
    from . import win_automate
    try:
        hwnd = int(args.get("hwnd") or 0)
        pid = int(args.get("pid") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "output": "hwnd/pid 必须是整数。"}
    # v8.7 硬闸：只能关闭由 exe_launch 启动过的进程/窗口，禁止杀任意进程
    if pid:
        if pid not in _EXE_LAUNCHED:
            return {"ok": False, "output": f"拒绝：pid={pid} 不是由 exe_launch 启动的进程，禁止终止。"}
        res = await asyncio.to_thread(win_automate.terminate_pid, pid)
        if res.get("ok"):
            _EXE_LAUNCHED.pop(pid, None)  # 已终止即出白名单（防 pid 复用误杀）
        return res
    if hwnd:
        wpid = win_automate.window_pid(hwnd)
        if not wpid or wpid not in _EXE_LAUNCHED:
            return {"ok": False, "output": f"拒绝：hwnd={hwnd} 不属于由 exe_launch 启动的进程，禁止关闭。"}
        return await asyncio.to_thread(win_automate.close_window, hwnd)
    return {"ok": False, "output": "需要 hwnd 或 pid 之一。"}


async def tool_exe_journal(args, ctx: ToolContext) -> dict:
    """v8.9 只读工具：查看 AI 对选定外部软件的探索操作记录。"""
    if not getattr(ctx.cfg, "ENABLE_EXE_JOURNAL", True):
        return {"ok": False, "output": "外部软件探索记录未开启（ENABLE_EXE_JOURNAL）。"}
    from . import tool_journal
    try:
        tail = int(args.get("tail") or 50)
    except (TypeError, ValueError):
        tail = 50
    tool = str(args.get("tool") or "").strip()
    items = tool_journal.read_journal(tail=tail, tool=tool)
    if not items:
        return {"ok": True, "output": "暂无外部软件探索记录。", "meta": {"count": 0}}
    lines = []
    for e in items[:50]:
        arg_text = ", ".join(f"{k}={v}" for k, v in (e.get("args") or {}).items())
        lines.append(f"- [{e['ts']}] {e['tool']} ({'成功' if e.get('ok') else '失败'}): {arg_text}\n    {e.get('output', '')[:150]}")
    return {"ok": True, "output": f"最近外部软件探索记录（共 {len(items)} 条）：\n" + "\n".join(lines),
            "meta": {"count": len(items)}}


# --------------------------------------------------------------------------
# DashScope API（通义万相：文生图/图生图/视频生成）
# --------------------------------------------------------------------------
async def tool_dashscope_image_generate(args, ctx: ToolContext) -> dict:
    """文生图：根据文字描述生成图片（DashScope 通义万相，同步调用）。"""
    block = _readonly_block("dashscope_image_generate")
    if block:
        return block
    from . import dashscope
    try:
        n = max(1, min(int(args.get("n") or 1), 4))
    except (TypeError, ValueError):
        n = 1
    return await asyncio.to_thread(
        dashscope.image_generate,
        str(args.get("prompt") or ""),
        str(args.get("model") or "wan2.7-image-pro"),
        str(args.get("size") or "1024*1024"),
        n,
        str(args.get("style") or "<auto>"),
        str(args.get("negative_prompt") or ""),
    )


async def tool_dashscope_image_edit(args, ctx: ToolContext) -> dict:
    """图生图：对输入图片按文字描述进行编辑（DashScope 通义万相，同步调用）。"""
    block = _readonly_block("dashscope_image_edit")
    if block:
        return block
    from . import dashscope
    return await asyncio.to_thread(
        dashscope.image_edit,
        str(args.get("image_url") or ""),
        str(args.get("prompt") or ""),
        str(args.get("model") or "qwen-image-edit-max"),
    )


async def tool_dashscope_video_generate(args, ctx: ToolContext) -> dict:
    """文生视频/图生视频（DashScope 通义万相，异步调用，自动轮询结果）。"""
    block = _readonly_block("dashscope_video_generate")
    if block:
        return block
    from . import dashscope
    return await asyncio.to_thread(
        dashscope.video_generate,
        str(args.get("prompt") or ""),
        str(args.get("model") or "wan2.7-t2v"),
        str(args.get("image_url") or ""),
    )


async def tool_dashscope_task_status(args, ctx: ToolContext) -> dict:
    """查询 DashScope 异步任务（视频生成）状态。"""
    from . import dashscope
    return await asyncio.to_thread(
        dashscope.task_status,
        str(args.get("task_id") or ""),
    )


async def tool_dashscope_list_models(args, ctx: ToolContext) -> dict:
    """列出 DashScope 可用模型（读 model-library/models.json）。category 可选：text-to-image/video-generation/image-edit。"""
    from . import dashscope
    category = str(args.get("category") or "").strip()
    return await asyncio.to_thread(dashscope.list_models, category)


# --------------------------------------------------------------------------
# UI 元素检视与可靠交互（控件级操作，替代脆弱坐标点击）
# --------------------------------------------------------------------------
async def tool_ui_enum_controls(args, ctx: ToolContext) -> dict:
    """枚举指定窗口的所有子控件（按钮/输入框/文本等），返回层级结构。hwnd=窗口句柄，recursive=是否递归（默认true），max_depth=最大递归深度（默认8）。"""
    from . import ui_inspect
    try:
        hwnd = int(args.get("hwnd") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "output": "hwnd 必须是整数。"}
    recursive = _strict_bool(args.get("recursive", True), default=True)
    try:
        max_depth = min(int(args.get("max_depth") or 8), 15)
    except (TypeError, ValueError):
        max_depth = 8
    return await asyncio.to_thread(ui_inspect.enum_child_windows, hwnd, recursive, max_depth)


async def tool_ui_find_control(args, ctx: ToolContext) -> dict:
    """在窗口子控件中按文本或类名定位控件。hwnd=父窗口句柄，text=文本包含（可选），class_name=类名包含（可选，与text至少一个）。"""
    from . import ui_inspect
    try:
        hwnd = int(args.get("hwnd") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "output": "hwnd 必须是整数。"}
    text = str(args.get("text") or "").strip()
    class_name = str(args.get("class_name") or "").strip()
    recursive = _strict_bool(args.get("recursive", True), default=True)
    return await asyncio.to_thread(ui_inspect.find_control, hwnd, text, class_name, recursive)


async def tool_ui_control_click(args, ctx: ToolContext) -> dict:
    """点击控件中心（比坐标点击更可靠）。hwnd=控件句柄。"""
    block = _readonly_block("ui_control_click")
    if block:
        return block
    if not await _ui_automation_guard(ctx, "ui_control_click", args):
        return {"ok": False, "output": "UI 自动化被拦截。", "meta": {"denied": True}}
    from . import ui_inspect
    try:
        hwnd = int(args.get("hwnd") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "output": "hwnd 必须是整数。"}
    return await asyncio.to_thread(ui_inspect.control_click, hwnd)


async def tool_ui_control_set_text(args, ctx: ToolContext) -> dict:
    """向控件输入文本（WM_SETTEXT，适用于Edit/输入框）。hwnd=控件句柄，text=要输入的文本。"""
    block = _readonly_block("ui_control_set_text")
    if block:
        return block
    if not await _ui_automation_guard(ctx, "ui_control_set_text", args):
        return {"ok": False, "output": "UI 自动化被拦截。", "meta": {"denied": True}}
    from . import ui_inspect
    try:
        hwnd = int(args.get("hwnd") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "output": "hwnd 必须是整数。"}
    return await asyncio.to_thread(ui_inspect.control_set_text, hwnd, str(args.get("text") or ""))


async def tool_ui_control_get_text(args, ctx: ToolContext) -> dict:
    """读取控件文本。hwnd=控件句柄。"""
    from . import ui_inspect
    try:
        hwnd = int(args.get("hwnd") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "output": "hwnd 必须是整数。"}
    return await asyncio.to_thread(ui_inspect.control_get_text, hwnd)


async def tool_ui_get_tree(args, ctx: ToolContext) -> dict:
    """获取窗口完整控件树（JSON结构，含层级/类名/文本/位置）。hwnd=窗口句柄，max_depth=最大递归深度（默认5）。"""
    from . import ui_inspect
    try:
        hwnd = int(args.get("hwnd") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "output": "hwnd 必须是整数。"}
    try:
        max_depth = min(int(args.get("max_depth") or 5), 10)
    except (TypeError, ValueError):
        max_depth = 5
    return await asyncio.to_thread(ui_inspect.get_window_tree, hwnd, max_depth)


async def tool_ui_get_control_info(args, ctx: ToolContext) -> dict:
    """获取单个控件的详细信息（文本/类名/位置/可见性/启用状态）。hwnd=控件句柄。"""
    from . import ui_inspect
    try:
        hwnd = int(args.get("hwnd") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "output": "hwnd 必须是整数。"}
    return await asyncio.to_thread(ui_inspect.get_control_info, hwnd)


# --------------------------------------------------------------------------
# v8.4 Python 应用截图 + UI 自截图确认（视觉专家审查）
# --------------------------------------------------------------------------
async def tool_app_screenshot(args, ctx: ToolContext) -> dict:
    """运行 Python UI 脚本并截图保存到工作区（制造 UI 后自检效果）。"""
    block = _readonly_block("app_screenshot")
    if block:
        return block
    from .app_shot import app_screenshot
    script = str(args.get("script") or "").strip()
    rel = str(args.get("path") or "").strip()
    if not script:
        return {"ok": False, "output": "缺少 script 参数（Python 脚本路径，相对工作区）。"}
    if not rel.lower().endswith((".png", ".jpg", ".jpeg")):
        return {"ok": False, "output": "path 必须以 .png/.jpg/.jpeg 结尾。"}
    try:
        sp = resolve_ws(ctx, script)
        p = resolve_ws(ctx, rel)
    except PermissionError as e:
        return {"ok": False, "output": str(e)}
    # v8.13.1：受保护数据目录的脚本不可运行；系统保护目录不可写截图
    if _is_read_protected(ctx, sp):
        return {"ok": False, "output": f"该脚本位于受保护数据目录，禁止运行: {script}"}
    if _is_protected(ctx, p):
        return {"ok": False, "output": f"该路径属于系统受保护目录，禁止写入截图: {rel}"}
    if not sp.is_file():
        return {"ok": False, "output": f"脚本不存在: {script}"}
    # 运行脚本等同执行代码：与 run_command 一致走审批门
    # （danger 模式对纯脚本路径默认放行，all 模式需用户确认）
    # v8.5.x 审查修复：移除 LLM 可控的 _pre_approved 绕过。
    approved = await _request_approval(
        ctx, "app_screenshot", {"script": script, "path": rel})
    if not approved:
        return {"ok": False, "output": "用户拒绝了脚本运行。", "meta": {"denied": True}}
    p.parent.mkdir(parents=True, exist_ok=True)
    extra = args.get("extra_args") or []
    try:
        timeout = int(args.get("timeout") or 30)
    except (TypeError, ValueError):
        timeout = 30
    return await asyncio.to_thread(
        app_screenshot, str(sp), str(p), timeout,
        str(args.get("title") or ""),
        extra_args=list(extra) if isinstance(extra, list) else [])


async def tool_ui_review(args, ctx: ToolContext) -> dict:
    """视觉审查截图：调用视觉专家模型（默认当前模型有图像能力则用主模型，
    否则用配置/注册表中的视觉专家模型）描述截图并给出 UI 改进建议。"""
    from .models import get_model, get_visual_expert_model, get_llm_cfg
    rel = str(args.get("path") or "").strip()
    if not rel:
        return {"ok": False, "output": "缺少 path 参数（截图相对工作区路径）。"}
    try:
        p = resolve_ws(ctx, rel)
    except PermissionError as e:
        return {"ok": False, "output": str(e)}
    # v8.13.1：受保护数据不可 base64 外发给视觉模型（对齐 web /fs/image）
    if _is_read_protected(ctx, p):
        return {"ok": False, "output": f"该文件属于受保护数据，禁止读取: {rel}"}
    if not p.is_file():
        return {"ok": False, "output": f"截图不存在: {rel}"}
    import base64
    from .app_shot import MAX_IMG_BYTES
    try:
        if p.stat().st_size > MAX_IMG_BYTES:
            return {"ok": False,
                    "output": f"截图过大（{p.stat().st_size} 字节 > {MAX_IMG_BYTES} 字节），"
                              "请缩小窗口或降低分辨率后重新截图再审查。"}
        raw = p.read_bytes()
    except OSError as e:
        return {"ok": False, "output": f"读取截图失败: {e}"}
    # v8.13.1：文件头魔数校验——config.json/users.json 等禁止借 ui_review 外发
    if raw[:4] == b"\x89PNG":
        mime = "image/png"
    elif raw[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    else:
        return {"ok": False, "output": "不是有效的 PNG/JPEG 图片，无法进行视觉审查。"}
    b64 = base64.b64encode(raw).decode("ascii")
    # 模型选择（用户要求：默认不调视觉专家，仅当主模型无视觉能力时再调）：
    # 1) 主模型 caps.image=True → 直接用主模型；
    # 2) 否则用视觉专家模型（配置优先 → 注册表第一个 image 模型）；
    # 3) 两者都没有 → 明确报错，不让 AI 误以为审查成功。
    main = get_model(getattr(ctx.cfg, "model", ""))
    model_id = ""
    if main is not None and (main.caps or {}).get("image"):
        model_id = main.id
    else:
        expert = get_visual_expert_model(ctx.cfg)
        model_id = expert.id if expert else ""
    if not model_id:
        return {"ok": False,
                "output": "主模型与视觉专家模型均不支持图片输入，无法进行视觉审查。请在设置中配置支持图像的模型。"}
    llm_cfg = get_llm_cfg(ctx.cfg, model_id)
    prompt = str(args.get("prompt") or "请仔细描述这张 UI 截图的布局、配色与问题，并给出具体改进建议。")
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ],
    }]
    try:
        from .llm import chat_complete
        resp = await chat_complete(llm_cfg, messages, max_tokens=800, timeout=45.0)
        content = (resp or {}).get("content")
        if not isinstance(content, str):
            content = str(content or "")
        return {"ok": True,
                "output": f"[视觉专家 {model_id} 审查 {rel}]\n{content}"}
    except Exception as e:
        return {"ok": False, "output": f"视觉审查调用失败: {e}"}


# --------------------------------------------------------------------------
# v6.6 外交型任务硬性规定：允许全放行，但 AI 检测到不完全符合用户要求立即拦截
# --------------------------------------------------------------------------
def _diplomatic_hard_check(tool: str, payload: dict) -> str:
    """外交型任务的硬规则前置校验（不依赖 LLM）：不合规返回拦截理由，合规返回 ""。"""
    url = str((payload or {}).get("url") or "").strip()
    if tool.startswith("browser_"):
        if tool in ("browser_open", "browser_read", "browser_screenshot",
                    "browser_elements", "browser_launch", "browser_navigate"):
            if not url:
                return "缺少 url 参数"
            if len(url) > 2048 or " " in url:
                return "url 非法（含空格或超长）"
            if not re.match(r"^https?://", url, re.IGNORECASE):
                return "url 必须以 http(s):// 开头（禁止 file://、javascript: 等）"
            if tool == "browser_screenshot":
                path = str((payload or {}).get("path") or "").strip()
                if not path:
                    return "缺少保存路径 path"
                if not path.lower().endswith((".png", ".jpg", ".jpeg")):
                    return "path 必须以 .png/.jpg/.jpeg 结尾"
        elif tool == "browser_click":
            if not str((payload or {}).get("selector") or "").strip() and not str((payload or {}).get("text") or "").strip():
                return "缺少 selector 或 text 参数"
            for key in ("selector", "text"):
                v = str((payload or {}).get(key) or "")
                if len(v) > 500:
                    return f"{key} 过长（≤500 字符）"
        elif tool == "browser_type":
            text = str((payload or {}).get("text") or "")
            if not text:
                return "缺少 text 参数"
            if len(text) > 2000:
                return "text 过长（≤2000 字符）"
        elif tool == "browser_press_keys":
            keys = str((payload or {}).get("keys") or "")
            if not keys:
                return "缺少 keys 参数"
            if len(keys) > 100:
                return "keys 过长（≤100 字符）"
    if tool == "web_search":
        q = str((payload or {}).get("query") or "").strip()
        if not q:
            return "缺少查询关键词 query"
        if len(q) > 500:
            return "query 过长（≤500 字符）"
    return ""


async def _diplomatic_guard(ctx: ToolContext, tool: str, payload: dict) -> bool:
    """外交型任务拦截门（硬性规定，独立于 ENABLE_APPROVAL/approval_mode 生效）：
    1) 硬规则不合规 → 立即拦截（不执行）；
    2) AI（副驾驶）核查调用是否符合用户要求 → 不符合立即拦截；
    3) 拿不准 → 升级用户审批（绝不静默放行）；
    4) 确认符合 → 放行（不弹窗）。
    """
    reason = _diplomatic_hard_check(tool, payload)
    if reason:
        await _emit(ctx, {"type": "diplomatic_block", "tool": tool,
                          "payload": payload, "note": f"外交型任务硬规则拦截：{reason}"})
        return False
    try:
        from .experts import copilot_check  # 延迟导入避免循环依赖
        verdict = await copilot_check(ctx.cfg, {
            "kind": "diplomatic", "tool": tool, "payload": payload,
            "user_intent": (ctx.user_intent or "")[:400],
        })
    except Exception:
        verdict = {"kill": False, "uncertain": True, "note": "副驾驶不可用，升级用户审批"}
    if verdict.get("kill"):
        await _emit(ctx, {"type": "diplomatic_block", "tool": tool,
                          "payload": payload, "note": verdict.get("note", "") or "AI 判定不符合用户要求，已拦截"})
        return False
    if verdict.get("uncertain"):
        # 拿不准 → 升级用户审批（与 copilot 模式语义一致，绝不静默放行）
        if ctx.approval is None:
            return False
        try:
            fut = ctx.approval.request(ctx.call_id)
            await _emit(ctx, {"type": "approval_needed", "call_id": ctx.call_id, "tool": tool,
                              "payload": payload, "note": "外交型任务：副驾驶无法确认是否符合要求，请人工裁决"})
            return bool(await asyncio.wait_for(fut, timeout=600.0))
        except asyncio.TimeoutError:
            ctx.approval.resolve(ctx.call_id, False)
            return False
        except BaseException:
            ctx.approval.resolve(ctx.call_id, False)
            raise
    return True


async def tool_request_write_permission(args, ctx: ToolContext) -> dict:
    """专家向总司令申请临时写权限：先过副驾驶（事件触发点），放行后清单外可写。"""
    purpose = str(args.get("purpose") or "").strip()
    if not purpose:
        return {"ok": False, "output": "必须说明申请写权限的理由（purpose）。", "meta": {}}
    if not ctx.expert_id:
        return {"ok": False, "output": "仅专家可调用该工具。", "meta": {}}
    from .experts import commander_write_permission_check  # 延迟导入避免循环依赖
    r = await commander_write_permission_check(ctx.cfg, ctx.expert_id, purpose)
    if not r.get("allow"):
        await _emit(ctx, {"type": "copilot_block", "note": r.get("note", ""),
                          "payload": {"kind": "permission_release"}})
        return {"ok": False, "output": f"总司令/副驾驶未放行：{r.get('note', '')}", "meta": {}}
    await _emit(ctx, {"type": "copilot_note",
                      "verdict": {"kill": False, "note": r.get("note", "")},
                      "payload": {"kind": "permission_release", "expert": ctx.expert_id}})
    return {"ok": True, "output": "已获得临时写权限（本轮有效），现在可写入申报清单外的文件。", "meta": {}}


async def tool_search_tool(args, ctx: ToolContext) -> dict:
    """工具查找元工具：裁剪后可查询被隐藏的工具及其用法（KV 缓存友好方案 A）。"""
    query = str(args.get("query") or "").strip()
    # v8.14：取局部快照（build_tool_defs 会整体替换 _ALL_DEFS 引用）
    defs = list(globals().get("_ALL_DEFS", {}).values())
    visible = set(args.get("_visible") or [])
    if not query:
        hidden = [d["function"]["name"] for d in defs if d["function"]["name"] not in visible]
        return {"ok": True, "output": f"当前隐藏的工具：{', '.join(hidden) or '无'}。用 query 参数查具体用法。"}
    from .matcher import rank as _match_rank
    fn_list = [d["function"] for d in defs]
    docs = [fn["name"] + " " + fn.get("description", "") for fn in fn_list]
    hit = {fn_list[i]["name"] for i, _s in await asyncio.to_thread(
        _match_rank, query, docs, min_score=0.25)}
    lines = []
    for fn in fn_list:
        if fn["name"] in hit or any(k in query.lower() for k in TOOL_MATCH.get(fn["name"], [])):
            lines.append(f"- {fn['name']}: {fn.get('description', '')[:120]}")
    if not lines:
        return {"ok": True, "output": f"没有与「{query}」相关的工具。"}
    return {"ok": True, "output": "[匹配的工具]\n" + "\n".join(lines[:8])}


# --------------------------------------------------------------------------
# v6 自研工具库（工具设计专家：查重→构建→审核→入库）与端口精确定位
# --------------------------------------------------------------------------
async def tool_build_tool(args, ctx: ToolContext) -> dict:
    """v6 构建自研工具：查重→LLM 设计→审核（过了才入库：工具库+资产银行）。"""
    block = _readonly_block("build_tool")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_TOOLSMITH", True):
        return {"ok": False, "output": "自研工具库未开启（ENABLE_TOOLSMITH）。", "meta": {}}
    requirement = str(args.get("requirement") or "").strip()
    if not requirement:
        return {"ok": False, "output": "必须提供工具需求描述（requirement）。", "meta": {}}
    from . import toolsmith  # 延迟导入
    dup = await asyncio.to_thread(toolsmith.dedup_report, requirement, vault=ctx.vault)
    top = await asyncio.to_thread(toolsmith.find_duplicates, requirement, limit=1)
    if top and top[0]["score"] >= 0.75:
        t = top[0]["tool"]
        return {"ok": True, "meta": {"duplicate": t},
                "output": (f"查重命中：已有自研工具 {t.get('name')}（相似度 {top[0]['score']}）。"
                           "为避免重复劳动，建议直接 use_tool 复用；确需新建请说明差异后重试。")}
    try:
        spec = await toolsmith.build_tool_via_llm(ctx.cfg, requirement, dup)
    except Exception as e:
        return {"ok": False, "output": f"工具设计失败：{e}", "meta": {}}
    from .experts import finalize_tool_build  # 审核+入库统一入口（避免循环导入）
    msg = await finalize_tool_build(ctx.cfg, spec, requirement, dup,
                                    vault=ctx.vault, emit=ctx.emit)
    return {"ok": msg.startswith("[OK]") or "已入库" in msg, "output": msg,
            "meta": {"tool_name": spec.get("name")}}


async def tool_list_tools(args, ctx: ToolContext) -> dict:
    """v6 列出已过审的自研工具（未过审不暴露）。"""
    if not getattr(ctx.cfg, "ENABLE_TOOLSMITH", True):
        return {"ok": False, "output": "自研工具库未开启。", "meta": {}}
    from . import toolsmith
    tools = toolsmith.list_active_tools()
    if not tools:
        return {"ok": True, "output": "自研工具库暂无过审工具。可用 build_tool 构建。", "meta": {}}
    lines = [f"- {t.get('name')}（{t.get('kind')}）: {t.get('description', '')}\n"
             f"  参数: {', '.join(t.get('params') or []) or '无'}"
             + (f" | 示例: {t.get('example', '')[:80]}" if t.get('example') else "")
             for t in tools]
    return {"ok": True, "output": "[自研工具]\n" + "\n".join(lines), "meta": {"count": len(tools)}}


async def tool_use_tool(args, ctx: ToolContext) -> dict:
    """v6 执行自研工具（prompt 型直接跑；script 型引导走 run_command 审批）。

    v6.4 用例反哺：ENABLE_CASE_FEEDBACK 开启时走 use_tool_with_feedback，
    连续失败 2 次自动生成修复钩子存入资产库，下次同类上下文自动注入。
    """
    if not getattr(ctx.cfg, "ENABLE_TOOLSMITH", True):
        return {"ok": False, "output": "自研工具库未开启。", "meta": {}}
    name = str(args.get("name") or "").strip()
    inputs = args.get("inputs") if isinstance(args.get("inputs"), dict) else {}
    if not name:
        return {"ok": False, "output": "必须提供工具名（name）。", "meta": {}}
    from . import toolsmith
    # v6.4 用例反哺：开启时走增强版，自动匹配/生成修复钩子
    if getattr(ctx.cfg, "ENABLE_CASE_FEEDBACK", True):
        r = await toolsmith.use_tool_with_feedback(ctx.cfg, name, inputs)
    else:
        r = await toolsmith.use_tool(ctx.cfg, name, inputs)
    # 失败不在这里记防呆库：agent._execute_tools 对 ok=False 统一记 ToolFail，避免双重记录
    # v6.3 工具医生：use_tool 内部已自动 record_tool_bug，这里不重复
    return r


# --------------------------------------------------------------------------
# v6.3 工具医生（Tool Doctor）：bug 收集 / 诊断 / 修复 / 回归
# --------------------------------------------------------------------------
async def tool_list_tool_bugs(args, ctx: ToolContext) -> dict:
    """v6.3 列出待修工具 bug。"""
    if not getattr(ctx.cfg, "ENABLE_TOOL_DOCTOR", True):
        return {"ok": False, "output": "工具医生未开启（ENABLE_TOOL_DOCTOR）。", "meta": {}}
    from . import toolsmith
    bugs = toolsmith.load_tool_bugs("pending")
    if not bugs:
        return {"ok": True, "output": "无待修工具 bug。", "meta": {"count": 0}}
    # 按工具名聚合计数
    agg = {}
    for b in bugs:
        n = b.get("tool_name", "?")
        agg[n] = agg.get(n, 0) + 1
    lines = [f"- {n}: {c} 条 bug" for n, c in sorted(agg.items(), key=lambda x: -x[1])]
    recent = bugs[-5:]
    detail = "\n".join(
        f"  [{b.get('bug_id', '')}] {b.get('tool_name', '')}: {b.get('reason', '')[:80]}"
        for b in recent
    )
    return {"ok": True, "output": f"待修工具 bug（共 {len(bugs)} 条，{len(agg)} 个工具）：\n"
                                  + "\n".join(lines) + "\n最近 5 条：\n" + detail,
            "meta": {"count": len(bugs), "tools": len(agg)}}


async def tool_fix_tool_bugs(args, ctx: ToolContext) -> dict:
    """v6.3 工具医生：批量诊断+修复 pending bug（沙箱回归通过才覆盖入库）。"""
    block = _readonly_block("fix_tool_bugs")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_TOOL_DOCTOR", True):
        return {"ok": False, "output": "工具医生未开启（ENABLE_TOOL_DOCTOR）。", "meta": {}}
    if not getattr(ctx.cfg, "ENABLE_TOOLSMITH", True):
        return {"ok": False, "output": "自研工具库未开启，无法修复。", "meta": {}}
    # 工具医生会覆盖已入库工具：与写文件同等审批，避免 LLM 静默改写已审核资产
    approved = await _request_approval(
        ctx, "fix_tool_bugs", {"command": "工具医生修复并覆盖自研工具", "dangerous": True})
    if not approved:
        return {"ok": False, "output": "用户拒绝了工具修复覆盖。", "meta": {"denied": True}}
    try:
        limit = int(args.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 20))
    from . import toolsmith
    r = await toolsmith.fix_tool_bugs(ctx.cfg, limit=limit)
    return r


async def tool_clear_tool_bug(args, ctx: ToolContext) -> dict:
    """v6.3 标记单条 bug 为已修（手动确认）。"""
    block = _readonly_block("clear_tool_bug")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_TOOL_DOCTOR", True):
        return {"ok": False, "output": "工具医生未开启。", "meta": {}}
    bug_id = str(args.get("bug_id") or "").strip()
    if not bug_id:
        return {"ok": False, "output": "必须提供 bug_id。", "meta": {}}
    from . import toolsmith
    ok = toolsmith.clear_tool_bug(bug_id)
    return {"ok": ok, "output": f"bug {bug_id} 已标记为已修。" if ok else f"未找到 pending 的 bug {bug_id}。"}


# --------------------------------------------------------------------------
# v8.9 资料（资产）检修：inspect_asset / repair_assets
# --------------------------------------------------------------------------
async def tool_inspect_asset(args, ctx: ToolContext) -> dict:
    """检查资产银行记录完整性，报告缺失字段/空内容/非法 tags/重复 id。"""
    if not getattr(ctx.cfg, "ENABLE_VAULT", True):
        return {"ok": False, "output": "资产银行未开启（ENABLE_VAULT）。", "meta": {}}
    if ctx.vault is None:
        return {"ok": False, "output": "资产银行不可用。", "meta": {}}
    r = ctx.vault.inspect_assets()
    total = r.get("total", 0)
    probs = r.get("problems") or []
    if not probs:
        return {"ok": True, "output": f"资产银行共 {total} 条记录，全部健康。",
                "meta": r}
    lines = [f"- [{p['id']}] {p['title']}: {'; '.join(p['problems'])}" for p in probs[:50]]
    return {"ok": True,
            "output": (f"资产银行共 {total} 条，{r.get('healthy', 0)} 条健康、"
                       f"{len(probs)} 条有问题：\n" + "\n".join(lines)
                       + ("\n...(仅列前 50 条)" if len(probs) > 50 else "")
                       + "\n可用 repair_assets 批量修复可自动修复的问题。"),
            "meta": r}


async def tool_repair_assets(args, ctx: ToolContext) -> dict:
    """批量修复资产银行可自动修复的问题（补 id/时间戳、tags 规范化、超长截断）。"""
    block = _readonly_block("repair_assets")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_VAULT", True):
        return {"ok": False, "output": "资产银行未开启（ENABLE_VAULT）。", "meta": {}}
    if ctx.vault is None:
        return {"ok": False, "output": "资产银行不可用。", "meta": {}}
    raw = args.get("dry_run", False)
    dry = raw if isinstance(raw, bool) else str(raw).lower() == "true"
    r = ctx.vault.repair_assets(dry_run=dry)
    remaining = r.get("problems_remaining") or []
    return {"ok": True,
            "output": (f"{'[试运行] ' if dry else ''}修复 {r.get('repaired', 0)} 条资产记录；"
                       f"剩余问题 {len(remaining)} 条。"
                       + ("" if not remaining else "\n" + "\n".join(
                           f"- [{p['id']}] {p['title']}: {'; '.join(p['problems'])}"
                           for p in remaining[:20]))),
            "meta": r}


# --------------------------------------------------------------------------
# v6.3 Notepad 暂存（跨轮中间结果）+ Checkpoint 恢复查询
# --------------------------------------------------------------------------
async def tool_notepad_save(args, ctx: ToolContext) -> dict:
    """v6.3 暂存一条中间结果（跨轮可用）。"""
    block = _readonly_block("notepad_save")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_NOTEPAD", True):
        return {"ok": False, "output": "Notepad 未开启。", "meta": {}}
    from . import notepad
    key = str(args.get("key") or "").strip()
    content = str(args.get("content") or "")
    if not key:
        return {"ok": False, "output": "必须提供 key。", "meta": {}}
    ok = notepad.save(key, content)
    return {"ok": ok, "output": f"已暂存「{key}」（{len(content)} 字符）。" if ok else "暂存失败。"}


async def tool_notepad_read(args, ctx: ToolContext) -> dict:
    """v6.3 读取暂存内容。"""
    if not getattr(ctx.cfg, "ENABLE_NOTEPAD", True):
        return {"ok": False, "output": "Notepad 未开启。", "meta": {}}
    from . import notepad
    key = str(args.get("key") or "").strip()
    if not key:
        return {"ok": False, "output": "必须提供 key。", "meta": {}}
    content = notepad.read(key)
    if not content:
        return {"ok": False, "output": f"暂存「{key}」不存在或为空。"}
    return {"ok": True, "output": content, "meta": {"key": key, "length": len(content)}}


async def tool_notepad_list(args, ctx: ToolContext) -> dict:
    """v6.3 列出所有暂存 key。"""
    if not getattr(ctx.cfg, "ENABLE_NOTEPAD", True):
        return {"ok": False, "output": "Notepad 未开启。", "meta": {}}
    from . import notepad
    keys = notepad.list_keys()
    if not keys:
        return {"ok": True, "output": "Notepad 为空。", "meta": {"count": 0}}
    lines = [f"- {k['key']}（{k['ts']}）: {k['preview']}" for k in keys]
    return {"ok": True, "output": f"Notepad 暂存（共 {len(keys)} 条）：\n" + "\n".join(lines),
            "meta": {"count": len(keys)}}


async def tool_notepad_clear(args, ctx: ToolContext) -> dict:
    """v6.3 清除暂存（单条或全部）。"""
    block = _readonly_block("notepad_clear")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_NOTEPAD", True):
        return {"ok": False, "output": "Notepad 未开启。", "meta": {}}
    from . import notepad
    key = str(args.get("key") or "").strip()
    n = notepad.clear(key)
    scope = f"「{key}」" if key else "全部"
    return {"ok": True, "output": f"已清除 {scope} 暂存（{n} 条）。"}


# ==================== v1.1.0 DeveraiIntegrityService 完整性校验工具 ====================

async def tool_integrity_sign(args, ctx: ToolContext) -> dict:
    """对数据生成 HMAC-SHA256 签名。args: {data: base64_string, ctx: tool|asset|doc|voice-task|voice-constraint}"""
    if not getattr(ctx.cfg, "ENABLE_INTEGRITY", True):
        return {"ok": False, "output": "完整性校验未开启（ENABLE_INTEGRITY）。", "meta": {}}
    import base64
    try:
        raw = base64.b64decode(str(args.get("data", "")))
    except Exception:
        return {"ok": False, "output": "data 必须是 base64 编码", "meta": {}}
    ctx_type = str(args.get("ctx") or "tool").strip()
    try:
        from .deverai_integrity import get_integrity_service
        svc = get_integrity_service()
        sig = svc.signIntegrity(raw, ctx_type)
        content_hash = svc.getContentHash(raw)
        return {"ok": True, "output": f"签名完成（ctx={ctx_type}）", "meta": {"signature": sig, "content_hash": content_hash, "ctx": ctx_type}}
    except Exception as e:
        return {"ok": False, "output": f"签名失败: {e}", "meta": {}}


async def tool_integrity_verify(args, ctx: ToolContext) -> dict:
    """验证数据的 HMAC-SHA256 签名。args: {data: base64_string, signature, ctx}"""
    if not getattr(ctx.cfg, "ENABLE_INTEGRITY", True):
        return {"ok": False, "output": "完整性校验未开启（ENABLE_INTEGRITY）。", "meta": {}}
    import base64
    try:
        raw = base64.b64decode(str(args.get("data", "")))
    except Exception:
        return {"ok": False, "output": "data 必须是 base64 编码", "meta": {}}
    sig = str(args.get("signature", "")).strip()
    ctx_type = str(args.get("ctx") or "tool").strip()
    try:
        from .deverai_integrity import get_integrity_service
        svc = get_integrity_service()
        valid = svc.verifyIntegrity(raw, sig, ctx_type)
        return {"ok": True, "output": f"签名验证{'通过' if valid else '失败'}（ctx={ctx_type}）", "meta": {"valid": valid, "ctx": ctx_type}}
    except Exception as e:
        return {"ok": False, "output": f"验证失败: {e}", "meta": {}}


async def tool_integrity_hash(args, ctx: ToolContext) -> dict:
    """快速内容哈希。args: {data: base64_string}"""
    if not getattr(ctx.cfg, "ENABLE_INTEGRITY", True):
        return {"ok": False, "output": "完整性校验未开启（ENABLE_INTEGRITY）。", "meta": {}}
    import base64
    try:
        raw = base64.b64decode(str(args.get("data", "")))
    except Exception:
        return {"ok": False, "output": "data 必须是 base64 编码", "meta": {}}
    try:
        from .deverai_integrity import get_integrity_service
        svc = get_integrity_service()
        h = svc.getContentHash(raw)
        return {"ok": True, "output": f"SHA-256: {h}", "meta": {"hash": h}}
    except Exception as e:
        return {"ok": False, "output": f"哈希失败: {e}", "meta": {}}


async def tool_integrity_self_check(args, ctx: ToolContext) -> dict:
    """运行完整性自检。"""
    if not getattr(ctx.cfg, "ENABLE_INTEGRITY", True):
        return {"ok": False, "output": "完整性校验未开启（ENABLE_INTEGRITY）。", "meta": {}}
    try:
        from .deverai_integrity import get_integrity_service
        svc = get_integrity_service()
        result = svc.self_check()
        status = "通过" if result["ok"] else "失败"
        types_info = ", ".join(f"{k}={('OK' if v else 'FAIL')}" for k, v in result.get("types", {}).items())
        return {"ok": result["ok"], "output": f"完整性自检{status}: {types_info}", "meta": result}
    except Exception as e:
        return {"ok": False, "output": f"自检失败: {e}", "meta": {}}


async def tool_list_checkpoints(args, ctx: ToolContext) -> dict:
    """v6.3 列出最近的 AI 改动 checkpoint（供用户在 GUI 恢复，Agent 也可查询）。"""
    if not getattr(ctx.cfg, "ENABLE_CHECKPOINT", True):
        return {"ok": False, "output": "Checkpoint 未开启。", "meta": {}}
    from . import checkpoint as ckpt
    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    items = ckpt.list_checkpoints(limit=limit)
    if not items:
        return {"ok": True, "output": "无 checkpoint 记录。", "meta": {"count": 0}}
    lines = [f"- [{i['ts']}] {i['rel_path']}（task: {i['task_id']}）" for i in items]
    return {"ok": True, "output": f"最近 checkpoint（共 {len(items)} 条）：\n" + "\n".join(lines),
            "meta": {"count": len(items)}}


async def tool_list_audits(args, ctx: ToolContext) -> dict:
    """v8.9 只读工具：查看最近的回退/删除/恢复等审计记录。"""
    if not getattr(ctx.cfg, "ENABLE_AUDIT_LOG", True):
        return {"ok": False, "output": "审计日志未开启（ENABLE_AUDIT_LOG）。", "meta": {}}
    from . import audit as _audit
    try:
        tail = int(args.get("tail") or 50)
    except (TypeError, ValueError):
        tail = 50
    action = str(args.get("action") or "").strip()
    items = _audit.list_audits(tail=tail, action=action)
    if not items:
        return {"ok": True, "output": "暂无审计记录。", "meta": {"count": 0}}
    lines = [f"- [{i['ts']}] {i['actor']} {i['action']} {i['target']}\n    {i['detail']}"
             for i in items[:50]]
    return {"ok": True, "output": f"最近审计记录（共 {len(items)} 条）：\n" + "\n".join(lines),
            "meta": {"count": len(items)}}


async def tool_backup_workspace(args, ctx: ToolContext) -> dict:
    """v8.25 一键备份完整工作区（非Git自动备份Worktree的整包出口）。"""
    block = _readonly_block("backup_workspace")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_FULL_BACKUP", True):
        return {"ok": False, "output": "完整备份未开启（ENABLE_FULL_BACKUP）。"}
    from . import file_protect as _fp
    label = str(args.get("label") or "full").strip() or "full"
    label = "".join(c for c in label if c.isalnum() or c in "_-")[:32] or "full"
    ws = ctx.workspace or ctx.cfg.workspace
    ok, zp, count = await asyncio.to_thread(_fp.backup_workspace_full, ws, label)
    if not ok:
        return {"ok": False, "output": zp}
    rel = zp
    try:
        rel = str(Path(zp).relative_to(Path(ws).resolve())).replace("\\", "/")
    except ValueError:
        pass
    await _emit(ctx, {"type": "file_changed", "path": rel})
    return {"ok": True, "output": f"已一键备份完整工作区：{rel}（{count} 个文件）",
            "meta": {"path": rel, "count": count}}


async def tool_scan_ambiguous_files(args, ctx: ToolContext) -> dict:
    """v8.25 重名/命名不清扫描（copilot+worktree发现奇怪点即调此工具要求用户识别）。"""
    from . import file_protect as _fp
    ws = ctx.workspace or ctx.cfg.workspace
    groups = await asyncio.to_thread(_fp.detect_ambiguous, ws)
    if not groups:
        return {"ok": True, "output": "未发现重名/命名不清文件。", "meta": {"count": 0}}
    lines = []
    for g in groups[:30]:
        lines.append(f"- [{g['group']}] {g['reason']}\n  " + "\n  ".join(g["files"][:8]))
    tail = "" if len(groups) <= 30 else f"\n…还有 {len(groups) - 30} 组未列出"
    return {"ok": True,
            "output": (f"发现 {len(groups)} 组重名/命名不清文件，请用户逐组识别："
                       "确认保留哪个、其余备份/转移（quarantine_files）。\n" + "\n".join(lines) + tail),
            "meta": {"count": len(groups), "groups": groups[:30]}}


async def tool_quarantine_files(args, ctx: ToolContext) -> dict:
    """v8.25 备份/转移重名文件到 backups/quarantine/<ts>/（保留原相对路径）。"""
    block = _readonly_block("quarantine_files")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_AMBIGUOUS_GUARD", True):
        return {"ok": False, "output": "重名治理未开启（ENABLE_AMBIGUOUS_GUARD）。"}
    files = _as_str_list(args.get("files"))
    reason = str(args.get("reason") or "")[:300]
    if not files:
        return {"ok": False, "output": "请提供 files（要备份/转移的相对路径列表）。"}
    if len(files) > 100:
        return {"ok": False, "output": "单次最多隔离 100 个文件。"}
    approved = await _request_approval(
        ctx, "quarantine_files",
        {"command": f"备份转移 {len(files)} 个重名文件到 quarantine（{reason or '重名治理'}）",
         "dangerous": True})
    if not approved:
        return {"ok": False, "output": "用户拒绝了备份转移操作。", "meta": {"denied": True}}
    from . import file_protect as _fp
    ws = ctx.workspace or ctx.cfg.workspace
    ok, qdir, moved = await asyncio.to_thread(_fp.quarantine_files, ws, files, reason)
    if not ok:
        return {"ok": False, "output": qdir}
    for r in moved:
        await _emit(ctx, {"type": "file_changed", "path": r})
    rel = qdir
    try:
        rel = str(Path(qdir).relative_to(Path(ws).resolve())).replace("\\", "/")
    except ValueError:
        pass
    return {"ok": True, "output": f"已备份转移 {len(moved)} 个文件到 {rel}",
            "meta": {"dir": rel, "moved": moved}}


async def tool_copy_user_asset(args, ctx: ToolContext) -> dict:
    """v8.26 工作副本：禁碰=拷贝出去改。把用户资产拷贝为 workcopy/ 副本，原文件不动。"""
    block = _readonly_block("copy_user_asset")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_WORK_COPY", True):
        return {"ok": False, "output": "工作副本未开启（ENABLE_WORK_COPY）。"}
    rel = str(args.get("path") or "").strip()
    if not rel:
        return {"ok": False, "output": "请提供 path（要拷贝的相对路径）。"}
    approved = await _request_approval(
        ctx, "copy_user_asset",
        {"command": f"拷贝工作副本 {rel} → workcopy/（原文件不动）", "dangerous": True})
    if not approved:
        return {"ok": False, "output": "用户拒绝了工作副本创建。", "meta": {"denied": True}}
    from . import file_protect as _fp
    ws = ctx.workspace or ctx.cfg.workspace
    ok, info = await asyncio.to_thread(
        _fp.make_workcopy, ws, rel, str(args.get("actor") or "AI"))
    if not ok:
        return {"ok": False, "output": str(info)}
    await _emit(ctx, {"type": "file_changed", "path": info["rel"]})
    return {"ok": True,
            "output": (f"已生成工作副本：{info['rel']}（原文件 {info['src']} 保持不变，"
                       "后续只在工作副本上操作）。"),
            "meta": {"copy": info["rel"], "src": info["src"]}}


async def tool_port_refs(args, ctx: ToolContext) -> dict:
    """v6 隐患 2 修正：精确定位端口号引用（文件:行号+片段），明确禁止盲替。"""
    port = str(args.get("port") or "").strip()
    if not port.isdigit():
        return {"ok": False, "output": "必须提供数字端口号（port）。", "meta": {}}
    import re as _re
    from pathlib import Path as _Path
    pattern = _re.compile(rf"(?<!\d){_re.escape(port)}(?!\d)")
    ws = _Path(ctx.workspace)
    EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".md", ".html", ".css",
            ".yaml", ".yml", ".toml", ".txt", ".ini", ".cfg", ".env", ".sh", ".bat"}
    SKIP_DIRS = {"backups", "__pycache__", ".git", "node_modules", "data", ".venv", "venv"}
    def _scan():
        hits, scanned = [], 0
        for p in ws.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in EXTS:
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(ws).parts):
                continue
            scanned += 1
            if scanned > 3000:
                break
            try:
                if p.stat().st_size > 500_000:
                    continue
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if pattern.search(line):
                        rel = str(p.relative_to(ws)).replace("\\", "/")
                        hits.append(f"- {rel}:{i} | {line.strip()[:120]}")
                        if len(hits) >= 60:
                            break
            except OSError:
                continue
            if len(hits) >= 60:
                break
        return hits, scanned

    # v8.5.x 审查修复：扫描丢进 executor，避免同步 rglob+read_text 阻塞事件循环
    try:
        hits, scanned = await asyncio.get_running_loop().run_in_executor(None, _scan)
    except (OSError, ValueError) as e:
        return _format_error(e)
    header = (f"端口 {port} 的引用定位（扫描 {scanned} 个文件）。\n"
              "[!] 这些只是文本匹配结果：改端口前必须逐处确认语义（硬编码/注释/文档示例），"
              "禁止全局查找替换盲改。改完记得 port_declare 更新登记。\n")
    return {"ok": True, "output": header + ("\n".join(hits) if hits else "未找到引用。"),
            "meta": {"hits": len(hits)}}


# --------------------------------------------------------------------------
# 工具清单与辅助
# --------------------------------------------------------------------------
def _p(properties, required=None):
    # required=[] 表示"无必填"，不能因空列表为 falsy 回退成全部必填
    req = list(properties.keys()) if required is None else list(required)
    return {"type": "object", "properties": properties, "required": req}


def build_tool_defs(cfg: Config, expert_mode: bool = False, allow_delegate: bool = True) -> list:
    # v4 三形态：chat/experts 形态只暴露只读工具，不主动改文件/执行命令
    mode = getattr(cfg, "agent_mode", "builder") or "builder"
    readonly = mode in ("chat", "experts") and not expert_mode  # 专家实例自带写工具集
    defs = [
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "列出工作区内目录的内容（文件/子目录、大小、修改时间）。",
                "parameters": _p({"path": {"type": "string", "description": "相对工作区的目录路径，默认根目录"}}),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取工作区内的文件内容（支持按行分页）。修改文件前请先读取确认。",
                "parameters": _p({
                    "path": {"type": "string", "description": "相对工作区的文件路径"},
                    "offset": {"type": "integer", "description": "从第几行开始（从0计）"},
                    "limit": {"type": "integer", "description": "最多返回多少行"},
                }, ["path"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": "在文件/目录中按正则搜索文本，返回带行号的匹配行。",
                "parameters": _p({
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "path": {"type": "string", "description": "搜索路径（文件或目录），默认工作区根"},
                    "glob": {"type": "string", "description": "文件名过滤，如 *.py"},
                    "ignore_case": {"type": "boolean"},
                    "line_numbers": {"type": "boolean"},
                    "head_limit": {"type": "integer"},
                }, ["pattern"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "glob",
                "description": "按通配符模式列出工作区内的文件（从路径末尾匹配，如 *.py 会包含子目录中的 .py 文件）。",
                "parameters": _p({
                    "pattern": {"type": "string", "description": "如 **/*.py 或 *.py"},
                    "path": {"type": "string", "description": "相对工作区的起始目录"},
                }, []),
            },
        },
    ]
    if not readonly:
        defs.extend([
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "创建或覆盖写入工作区内的文件（UTF-8）。会自动创建父目录。",
                "parameters": _p({
                    "path": {"type": "string", "description": "相对工作区的文件路径"},
                    "content": {"type": "string", "description": "完整文件内容"},
                }),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "精确替换文件中的一段文本。old_string 必须与文件内容完全匹配（建议先 read_file）。",
                "parameters": _p({
                    "path": {"type": "string"},
                    "old_string": {"type": "string", "description": "要被替换的原文（唯一匹配或加上下文）"},
                    "new_string": {"type": "string", "description": "替换后的新文本"},
                    "replace_all": {"type": "boolean", "description": "是否替换所有匹配处"},
                }, ["path", "old_string", "new_string"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "在工作区中执行命令行（shell 命令），支持超时与后台运行。长耗时构建请使用 background=true。未指定的可选参数按默认值执行并在结果中返回 warning。",
                "parameters": _p({
                    "command": {"type": "string", "description": "要执行的完整命令"},
                    "cwd": {"type": "string", "description": "工作目录（相对工作区；指定 env 时默认用环境的目录）"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认120，最大600"},
                    "background": {"type": "boolean", "description": "是否后台异步执行（用于长耗时构建，立即返回）"},
                    "env": {"type": "string", "description": "持久命令行环境的 id 或名称（term_create 创建；同一环境多次执行继承 cwd 与环境变量）"},
                    "update_interval": {"type": "number", "description": "更新间隔秒数 0-120：静默期到点把输出尾部心跳回传（长命令如固件刷写建议 10-30）。默认 0=不发心跳"},
                    "kill_after": {"type": "boolean", "description": "超时/停止后是否杀整棵进程树，默认 true；false 时进程保留后台运行（适合保留服务器/烧录器守护）"},
                }, ["command"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "workspace_info",
                "description": "获取工作区与运行环境信息（OS、Python 版本、工作区路径）。",
                "parameters": _p({}),
            },
        },
        # v8.18 桌面端持久命令行环境
        {
            "type": "function",
            "function": {
                "name": "term_create",
                "description": "创建一个持久命令行环境（记住工作目录与环境变量）。后续 run_command 通过 env 参数在该环境里多次执行命令，上下文（cwd/env）持续保留。",
                "parameters": _p({
                    "name": {"type": "string", "description": "环境名称（可选，默认 env-N）"},
                    "cwd": {"type": "string", "description": "工作目录（相对路径，默认工作区根）"},
                    "env_vars": {"type": "object", "description": "环境变量对象 {KEY: VALUE}"},
                }, []),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "term_list",
                "description": "列出所有持久命令行环境（含 id、名称、工作目录、最后使用的命令与退出码）。",
                "parameters": _p({}),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "term_delete",
                "description": "删除一个持久命令行环境（仅销毁上下文，不影响已结束的进程）。",
                "parameters": _p({
                    "id": {"type": "string", "description": "要删除的环境 id"},
                }, ["id"]),
            },
        },
        ])
    else:
        defs.append({
            "type": "function",
            "function": {
                "name": "workspace_info",
                "description": "获取工作区与运行环境信息（OS、Python 版本、工作区路径）。",
                "parameters": _p({}),
            },
        })
    if cfg.ENABLE_VAULT:
        defs.append({
            "type": "function",
            "function": {
                "name": "search_vault",
                "description": "检索本地资产银行（可复用的代码片段/模板/图标等）。做重复性工作前应先检索复用。",
                "parameters": _p({
                    "query": {"type": "string", "description": "语义查询，如'登录按钮图标 蓝色扁平'"},
                    "limit": {"type": "integer"},
                }, ["query"]),
            },
        })
        # v8.13：store_asset 是写资产库的工具，chat 只读形态不得暴露
        if not readonly:
            defs.append({
                "type": "function",
                "function": {
                    "name": "store_asset",
                    "description": "将可复用的生成物存入资产银行（需提供多维说明书：标题/描述/标签/场景/生成指令）。",
                    "parameters": _p({
                        "title": {"type": "string"},
                        "kind": {"type": "string", "description": "code/icon/chart/template/data 等"},
                        "content": {"type": "string", "description": "资产内容"},
                        "description": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "视觉风格标签"},
                        "scene": {"type": "string", "description": "适用场景"},
                        "prompt": {"type": "string", "description": "生成该资产的关键指令/参数"},
                    }, ["title", "content"]),
                },
            })
        defs.append({
            "type": "function",
            "function": {
                "name": "inspect_asset",
                "description": "检查资产银行（资料库）记录完整性：缺失字段/空内容/非法 tags/重复 id。发现资料异常时用这个做体检。",
                "parameters": _p({}, []),
            },
        })
        if not readonly:
            defs.append({
                "type": "function",
                "function": {
                    "name": "repair_assets",
                    "description": "批量修复资产银行可自动修复的问题（补 id/时间戳、tags 规范化、超长字段截断）；dry_run=true 只检查不落盘。标题/内容为空的记录只报告不删除。",
                    "parameters": _p({
                        "dry_run": {"type": "boolean", "description": "是否试运行（默认 false=直接修复）"},
                    }, []),
                },
            })
    # v8.13：delegate/plan 内部可写文件/执行命令，chat 只读形态不得暴露
    if cfg.ENABLE_SUBAGENT and allow_delegate and not readonly:
        defs.append({
            "type": "function",
            "function": {
                "name": "delegate_task",
                "description": "委派子Agent：在全新独立上下文中执行一个子任务（如长时间命令行构建、独立小模块编写），只回传紧凑结论。适合任务拆解后的子项。",
                "parameters": _p({
                    "task": {"type": "string", "description": "子任务的完整描述"},
                    "context": {"type": "string", "description": "必要的前置上下文（尽量精简）"},
                }, ["task"]),
            },
        })
    if cfg.ENABLE_AOE and allow_delegate and not readonly:
        defs.append({
            "type": "function",
            "function": {
                "name": "plan_and_execute",
                "description": "AOE 规划执行：把复杂任务拆成 DAG，无依赖分支并行执行（默认20秒熔断），规划前自动检索资产银行做复用拦截。适合多步骤、可并行的复杂任务。",
                "parameters": _p({
                    "task": {"type": "string", "description": "任务目标描述"},
                }, ["task"]),
            },
        })
    # ---- v5 新工具 ----
    if getattr(cfg, "ENABLE_WEB_SEARCH", True):
        defs.append({
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "互联网搜索：查资料/Benchmark/文档/最新动态。结果自动压缩为要点摘要。",
                "parameters": _p({
                    "query": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "结果条数，默认6"},
                    "summarize": {"type": "boolean", "description": "是否交助手模型压缩，默认true"},
                }, ["query"]),
            },
        })
    # ---- v6.6 浏览器控制（超轻量，外交型任务：全放行+AI 检测不符即拦截）----
    if getattr(cfg, "ENABLE_BROWSER", True) and not readonly:
        defs.append({
            "type": "function",
            "function": {
                "name": "browser_open",
                "description": "在系统浏览器中真实打开网页（用户可见可交互）。外交型任务：需要外链/演示/让用户看页面时用。",
                "parameters": _p({
                    "url": {"type": "string", "description": "http(s):// 开头的完整网址"},
                }, ["url"]),
            },
        })
        defs.append({
            "type": "function",
            "function": {
                "name": "browser_read",
                "description": "无头读取网页渲染后的正文内容（JS 执行后的真实文本）。查在线文档/抓取页面内容时用。",
                "parameters": _p({
                    "url": {"type": "string", "description": "http(s):// 开头的完整网址"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认30，最大90"},
                }, ["url"]),
            },
        })
        defs.append({
            "type": "function",
            "function": {
                "name": "browser_screenshot",
                "description": "无头截取网页整页图片保存到工作区（path 相对工作区，.png/.jpg 结尾）。",
                "parameters": _p({
                    "url": {"type": "string", "description": "http(s):// 开头的完整网址"},
                    "path": {"type": "string", "description": "相对工作区的保存路径，如 docs/page.png"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认30，最大90"},
                }, ["url", "path"]),
            },
        })
        defs.append({
            "type": "function",
            "function": {
                "name": "browser_elements",
                "description": "把网页转成可点击点：无头渲染页面后抽取链接/按钮/输入框等可交互元素，返回带 index 的结构化清单（tag/type/text/href/placeholder）。理解别人产品结构、做竞品差异分析时用。",
                "parameters": _p({
                    "url": {"type": "string", "description": "http(s):// 开头的完整网址"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认30，最大90"},
                }, ["url"]),
            },
        })
        # ---- v8.9 直接操控浏览器（CDP，纯标准库）----
        if getattr(cfg, "ENABLE_BROWSER_CTL", True):
            _bctl_defs = [
                ("browser_launch", "启动一个可被直接操控的浏览器窗口（CDP 调试口仅 127.0.0.1）。默认无头模式，被风控拦截时自动回退到有头+反检测模式。需要真实点击/输入/跳转网页时先启动。", [
                    ("url", "string", "初始打开的网址（默认本机网页版地址）"),
                ], []),
                ("browser_navigate", "让受控浏览器跳转到新网址（自动注入反检测脚本）。", [
                    ("url", "string", "http(s):// 开头的完整网址"),
                ], ["url"]),
                ("browser_click", "在受控浏览器页面中点击元素：selector=CSS 选择器（如 #submit、button.primary），text=链接/按钮的可见文本（二选一）。", [
                    ("selector", "string", "CSS 选择器（与 text 二选一）"),
                    ("text", "string", "可见文本（与 selector 二选一）"),
                ], []),
                ("browser_type", "向受控浏览器当前焦点元素输入文本（Unicode）。", [
                    ("text", "string", "要输入的文本（≤2000 字符）"),
                ], ["text"]),
                ("browser_press_keys", "受控浏览器按键：单键（enter/tab/esc/方向键/f1-f12）或组合键（ctrl,c）。", [
                    ("keys", "string", "按键名，多键逗号分隔"),
                ], ["keys"]),
                ("browser_close", "关闭受控浏览器并清理临时 profile。", [], []),
            ]
            # v8.14 F12 开发者工具（Networks / Storage / Console / Sources）
            if getattr(cfg, "ENABLE_BROWSER_DEVTOOLS", True):
                _bctl_defs.extend([
                    ("browser_networks_start", "开始捕获受控浏览器的网络请求（F12 Networks 面板）。后续页面加载/跳转产生的请求将被记录（上限 500 条）。", [], []),
                    ("browser_networks_stop", "停止网络请求捕获并返回已捕获的请求摘要（F12 Networks 面板）。", [], []),
                    ("browser_networks_get", "获取已捕获的网络请求（F12 Networks 面板）。支持 URL/类型/方法/状态码过滤。", [
                        ("filter_url", "string", "URL 包含的字符串（如 'api'、'endpoint'，可选）"),
                        ("filter_type", "string", "资源类型（XHR/Fetch/Document/Script/Stylesheet/Image，可选）"),
                        ("filter_method", "string", "请求方法（GET/POST/PUT/DELETE，可选）"),
                        ("status_code", "integer", "状态码过滤（0=不过滤，可选）"),
                        ("max_results", "integer", "最大返回条数（默认 50，最大 200）"),
                    ], []),
                    ("browser_storage_get", "获取页面存储信息（F12 Storage 面板）。", [
                        ("storage_type", "string", "存储类型：cookies / localStorage / sessionStorage / all（默认 all）"),
                        ("url", "string", "指定 URL（仅 cookies 需要，可选）"),
                    ], []),
                    ("browser_storage_set", "设置页面存储项（F12 Storage 面板）。", [
                        ("storage_type", "string", "存储类型：cookies / localStorage / sessionStorage"),
                        ("key", "string", "键名"),
                        ("value", "string", "值"),
                        ("url", "string", "URL（仅 cookies 需要，可选）"),
                    ], ["key", "value"]),
                    ("browser_storage_clear", "清除页面存储（F12 Storage 面板）。", [
                        ("storage_type", "string", "存储类型：cookies / localStorage / sessionStorage / all（默认 all）"),
                    ], []),
                    ("browser_console_get", "获取控制台日志（F12 Console 面板）。", [
                        ("max_entries", "integer", "最大返回条数（默认 100，最大 200）"),
                    ], []),
                    ("browser_console_eval", "在页面上下文中执行 JavaScript 表达式（F12 Console 面板）。", [
                        ("expression", "string", "JavaScript 表达式（≤2000 字符）"),
                        ("timeout", "number", "超时秒数（默认 15，最大 60）"),
                    ], ["expression"]),
                    ("browser_sources_list", "获取页面加载的 JavaScript 源文件列表（F12 Sources 面板）。", [
                        ("filter_pattern", "string", "URL/ID 过滤字符串（可选）"),
                    ], []),
                    ("browser_sources_get", "获取指定 JavaScript 源文件的内容（F12 Sources 面板）。", [
                        ("url_or_index", "string", "脚本 URL 或索引"),
                        ("max_len", "integer", "最大返回字符数（默认 10000）"),
                    ], ["url_or_index"]),
                    ("browser_find_api_endpoints", "从已捕获的网络请求中找出疑似 API 端点（仅返回用户已付费/已授权服务的数据，不绕过任何付费墙）。", [
                        ("filter_pattern", "string", "URL 过滤字符串（默认 'api'）"),
                        ("min_response_size", "integer", "最小响应体大小（过滤小响应，默认 0）"),
                    ], []),
                ])
            for _name, _desc, _params, _required in _bctl_defs:
                _props = {}
                for _pn, _pt, _pdesc in _params:
                    _props[_pn] = {"type": _pt, "description": _pdesc}
                defs.append({
                    "type": "function",
                    "function": {
                        "name": _name,
                        "description": _desc,
                        "parameters": _p(_props, _required),
                    },
                })
    # ---- v8.4 Python 应用截图 / UI 自截图确认（本地 UI 制作后自检 + 视觉专家审查）----
    if getattr(cfg, "ENABLE_APP_SHOT", True) and not readonly:
        defs.append({
            "type": "function",
            "function": {
                "name": "app_screenshot",
                "description": "运行工作区内的 Python UI 脚本（PyQt/Tkinter 等），等窗口出现后截图保存到工作区。制造本地 GUI 应用后必须用这个自检效果。参数 script=脚本相对路径，path=截图保存相对路径(.png/.jpg)，title=窗口标题片段(可选，多窗口时定位)，timeout=等待秒数(默认30)。",
                "parameters": _p({
                    "script": {"type": "string", "description": "Python 脚本相对工作区路径，如 ui/myapp.py"},
                    "path": {"type": "string", "description": "截图保存相对路径，如 docs/ui.png"},
                    "title": {"type": "string", "description": "窗口标题包含片段（可选）"},
                    "timeout": {"type": "integer", "description": "等待窗口秒数，默认30，最大120"},
                    "extra_args": {"type": "array", "items": {"type": "string"},
                                   "description": "传给脚本的额外参数（可选）"},
                }, ["script", "path"]),
            },
        })
    if getattr(cfg, "ENABLE_UI_REVIEW", True) and not readonly:
        defs.append({
            "type": "function",
            "function": {
                "name": "ui_review",
                "description": "视觉审查一张截图：调用视觉专家模型（配置优先，否则注册表第一个支持图像的模型）描述布局/配色/问题并给改进建议。制造 UI 截图后、或用户要求'看看效果'时用。path=截图相对路径，prompt=审查重点(可选)。",
                "parameters": _p({
                    "path": {"type": "string", "description": "截图相对工作区路径，如 docs/ui.png"},
                    "prompt": {"type": "string", "description": "审查重点，默认描述布局配色问题并给建议"},
                }, ["path"]),
            },
        })
    defs.append({
        "type": "function",
        "function": {
            "name": "file_search",
            "description": "文件搜索（复合）：按语义找文件名（相似度）+可选内容正则定位。找文件优先用这个。",
            "parameters": _p({
                "query": {"type": "string", "description": "文件名/路径语义查询"},
                "path": {"type": "string", "description": "搜索起点，默认工作区根"},
                "glob": {"type": "string", "description": "文件名过滤，如 *.py"},
                "content": {"type": "boolean", "description": "是否同时做内容匹配，默认false"},
                "limit": {"type": "integer"},
            }, ["query"]),
        },
    })
    # ---- v8.6 外部程序/浏览器自动化（默认关，副驾驶全程监督）----
    if getattr(cfg, "ENABLE_UI_AUTOMATION", False) and not readonly:
        _exe_defs = [
            ("exe_launch", "启动用户配置的唯一外部 exe（ui_automation_exe）。返回 pid。", [
                ("path", "string", "可执行文件绝对路径（必须等于配置的 ui_automation_exe）"),
                ("args", "array", "命令行参数（可选）"),
                ("cwd", "string", "工作目录（可选）"),
            ], ["path"]),
            ("exe_list_windows", "枚举所有可见顶层窗口（hwnd/标题/pid），供截图/点击/关闭定位窗口。", [], []),
            ("exe_screenshot", "截图指定外部程序窗口保存到工作区（path 相对工作区 .png/.jpg）。", [
                ("path", "string", "截图保存相对路径"),
                ("hwnd", "integer", "窗口句柄（可选，与 title 二选一）"),
                ("title", "string", "窗口标题包含片段（可选）"),
            ], ["path"]),
            ("exe_click", "在屏幕绝对坐标 (x,y) 左键单击一次。", [
                ("x", "integer", "屏幕 x 坐标"),
                ("y", "integer", "屏幕 y 坐标"),
            ], ["x", "y"]),
            ("exe_type", "向当前焦点窗口输入文本（Unicode）。", [
                ("text", "string", "要输入的文本（≤2000 字符）"),
            ], ["text"]),
            ("exe_press_keys", "按常用功能键，多键逗号分隔（如 ctrl,c 或 enter）。", [
                ("keys", "string", "按键名，支持 enter/tab/esc/方向键/ctrl/shift/alt/f1-f12 等"),
            ], ["keys"]),
            ("exe_close", "关闭外部程序窗口或结束进程。", [
                ("hwnd", "integer", "窗口句柄（可选）"),
                ("pid", "integer", "进程号（可选）"),
            ], []),
        ]
        for name, desc, params, required in _exe_defs:
            props = {}
            for pname, ptype, pdesc in params:
                if ptype == "array":
                    props[pname] = {"type": "array", "items": {"type": "string"}, "description": pdesc}
                else:
                    props[pname] = {"type": ptype, "description": pdesc}
            defs.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": _p(props, required),
                },
            })
    # v8.9 外部软件探索记录（只读，与 ENABLE_UI_AUTOMATION 无关——UI 自动化关闭后仍可回看历史记录）
    if getattr(cfg, "ENABLE_EXE_JOURNAL", True):
        defs.append({
            "type": "function",
            "function": {
                "name": "exe_journal",
                "description": "查看 AI 对选定外部软件的探索操作记录（ts/工具/参数/结果）。回看 AI 探索了什么、点了哪里、输入了什么。",
                "parameters": _p({
                    "tail": {"type": "integer", "description": "返回条数（默认 50，最大 500）"},
                    "tool": {"type": "string", "description": "按工具名过滤（如 exe_click）"},
                }, []),
            },
        })
    # ---- DashScope API（通义万相：文生图/图生图/视频生成）----
    _dash_key_ok = bool(str(getattr(cfg, "dashscope_api_key", "") or "").strip())
    if not readonly and _dash_key_ok:
        _dashscope_defs = [
            ("dashscope_image_generate", "文生图：根据文字描述生成图片（DashScope 通义万相，同步调用，返回图片 URL）。需要配置 dashscope_api_key。", [
                ("prompt", "string", "画面描述文字（中文/英文，≤2000字符）"),
                ("model", "string", "模型名（默认 wan2.7-image-pro，可用 dashscope_list_models 查看）"),
                ("size", "string", "图片尺寸（默认 1024*1024，可选 720*1280/1280*720）"),
                ("n", "integer", "生成数量（1-4，默认 1）"),
                ("style", "string", "风格（默认 <auto>，可选 <photorealistic>/<anime>/<oil painting> 等）"),
                ("negative_prompt", "string", "负面描述（不希望出现的内容）"),
            ], ["prompt"]),
            ("dashscope_image_edit", "图生图：对输入图片按文字描述进行编辑（如换背景、改风格）。需要配置 dashscope_api_key。", [
                ("image_url", "string", "输入图片 URL（http(s):// 或 data:image/...）"),
                ("prompt", "string", "编辑要求描述（≤2000字符）"),
                ("model", "string", "模型名（默认 qwen-image-edit-max）"),
            ], ["image_url", "prompt"]),
            ("dashscope_video_generate", "文生视频/图生视频：根据文字或图片生成视频（异步调用，自动轮询结果）。需要配置 dashscope_api_key。", [
                ("prompt", "string", "视频画面描述（≤2000字符）"),
                ("model", "string", "模型名（默认 wan2.7-t2v，图生视频用 wan2.7-i2v）"),
                ("image_url", "string", "输入图片 URL（非空时走图生视频）"),
            ], ["prompt"]),
            ("dashscope_task_status", "查询 DashScope 异步任务（视频生成）状态。task_id=dashscope_video_generate 返回的 task_id。", [
                ("task_id", "string", "异步任务 ID"),
            ], ["task_id"]),
        ]
        for _name, _desc, _params, _required in _dashscope_defs:
            _props = {}
            for _pn, _pt, _pdesc in _params:
                if _pt == "array":
                    _props[_pn] = {"type": "array", "items": {"type": "string"}, "description": _pdesc}
                else:
                    _props[_pn] = {"type": _pt, "description": _pdesc}
            defs.append({
                "type": "function",
                "function": {
                    "name": _name,
                    "description": _desc,
                    "parameters": _p(_props, _required),
                },
            })
    # 模型列表为只读工具：任何形态都可查询（不消耗/不需要 API Key）
    defs.append({
        "type": "function",
        "function": {
            "name": "dashscope_list_models",
            "description": "列出 DashScope 可用模型（读 model-library/models.json）。可选 category 过滤：text-to-image/video-generation/image-edit。",
            "parameters": _p({
                "category": {"type": "string", "description": "分类过滤（可选：text-to-image/video-generation/image-edit）"},
            }, []),
        },
    })
    # ---- UI 元素检视与可靠交互（控件级操作，替代脆弱坐标点击）----
    # 全部 ui_* 工具统一受 ENABLE_UI_AUTOMATION 开关裁剪；chat/experts 只读形态仅保留检视类
    _UI_READONLY_TOOLS = {"ui_enum_controls", "ui_find_control", "ui_control_get_text",
                          "ui_get_tree", "ui_get_control_info"}
    if getattr(cfg, "ENABLE_UI_AUTOMATION", False):
        _ui_defs = [
            ("ui_enum_controls", "枚举指定窗口的所有子控件（按钮/输入框/文本等），返回层级结构。先 exe_list_windows 获取 hwnd，再用此工具分析界面。", [
                ("hwnd", "integer", "窗口句柄（从 exe_list_windows 获取）"),
                ("recursive", "boolean", "是否递归枚举所有子控件（默认 true）"),
                ("max_depth", "integer", "最大递归深度（默认 8，最大 15）"),
            ], ["hwnd"]),
            ("ui_find_control", '在窗口子控件中按文本或类名定位控件（如找"登录"按钮、"用户名"输入框）。返回 hwnd 供后续操作。', [
                ("hwnd", "integer", "父窗口句柄"),
                ("text", "string", "控件文本包含的内容（与 class_name 至少一个）"),
                ("class_name", "string", "控件类名包含的内容（如 Button/Edit/Static）"),
                ("recursive", "boolean", "是否递归查找（默认 true）"),
            ], []),
            ("ui_control_click", "点击控件中心（比坐标点击更可靠）。hwnd 由 ui_find_control 获取。", [
                ("hwnd", "integer", "控件句柄"),
            ], ["hwnd"]),
            ("ui_control_set_text", "向控件输入文本（WM_SETTEXT，适用于 Edit/输入框等）。hwnd 由 ui_find_control 获取。", [
                ("hwnd", "integer", "控件句柄"),
                ("text", "string", "要输入的文本（≤2000 字符）"),
            ], ["hwnd", "text"]),
            ("ui_control_get_text", "读取控件文本内容。hwnd 由 ui_find_control 获取。", [
                ("hwnd", "integer", "控件句柄"),
            ], ["hwnd"]),
            ("ui_get_tree", "获取窗口完整控件树（JSON 结构，含层级/类名/文本/位置）。用于全面分析界面结构。", [
                ("hwnd", "integer", "窗口句柄"),
                ("max_depth", "integer", "最大递归深度（默认 5，最大 10）"),
            ], ["hwnd"]),
            ("ui_get_control_info", "获取单个控件的详细信息（文本/类名/位置/可见性/启用状态）。", [
                ("hwnd", "integer", "控件句柄"),
            ], ["hwnd"]),
        ]
        if readonly:
            _ui_defs = [d for d in _ui_defs if d[0] in _UI_READONLY_TOOLS]
        for _name, _desc, _params, _required in _ui_defs:
            _props = {}
            for _pn, _pt, _pdesc in _params:
                if _pt == "array":
                    _props[_pn] = {"type": "array", "items": {"type": "string"}, "description": _pdesc}
                else:
                    _props[_pn] = {"type": _pt, "description": _pdesc}
            defs.append({
                "type": "function",
                "function": {
                    "name": _name,
                    "description": _desc,
                    "parameters": _p(_props, _required),
                },
            })
    defs.append({
        "type": "function",
        "function": {
            "name": "search_tool",
            "description": "工具查找：查询当前可用/被隐藏的工具及其用途。不确定用哪个工具时先问它。",
            "parameters": _p({"query": {"type": "string", "description": "想做的事，如'执行命令'"}}, []),
        },
    })
    defs.append({
        "type": "function",
        "function": {
            "name": "port_refs",
            "description": "定位某端口号在工作区内的全部引用（文件:行号+片段）。改端口必用；禁止全局替换盲改。",
            "parameters": _p({"port": {"type": "string", "description": "端口号，如 8080"}}),
        },
    })
    # v6.1 P1 修正：delete_file 此前有处理器/匹配序列却从未暴露，ALLOW_AI_DELETE 开关形同虚设
    if getattr(cfg, "ALLOW_AI_DELETE", False) and not readonly:
        defs.append({
            "type": "function",
            "function": {
                "name": "delete_file",
                "description": "删除工作区内的文件或目录（危险，仅在用户开启 ALLOW_AI_DELETE 时可用；系统保护目录永远禁删）。",
                "parameters": _p({"path": {"type": "string", "description": "工作区相对路径"}}),
            },
        })
    # ---- v6 自研工具库（工具设计专家）----
    if getattr(cfg, "ENABLE_TOOLSMITH", True) and not readonly:
        defs.append({
            "type": "function",
            "function": {
                "name": "build_tool",
                "description": "构建自研工具（工具设计专家）：自动查重→设计→审核→过了才入库。接到'造工具/写脚本/做转换器'类需求时用。",
                "parameters": _p({"requirement": {"type": "string", "description": "工具需求完整描述"}}),
            },
        })
        defs.append({
            "type": "function",
            "function": {
                "name": "list_tools",
                "description": "列出已过审入库的自研工具。做新工具前先查是否已有。",
                "parameters": _p({}, []),
            },
        })
        defs.append({
            "type": "function",
            "function": {
                "name": "use_tool",
                "description": "执行一个自研工具（prompt 型直接出结果；script 型引导走 run_command）。",
                "parameters": _p({
                    "name": {"type": "string", "description": "工具名"},
                    "inputs": {"type": "object", "description": "模板参数键值对"},
                }, ["name"]),
            },
        })
        # v6.3 工具医生：bug 收集/诊断/修复/回归
        if getattr(cfg, "ENABLE_TOOL_DOCTOR", True):
            defs.append({
                "type": "function",
                "function": {
                    "name": "list_tool_bugs",
                    "description": "列出待修的自研工具 bug（use_tool 失败时自动收集）。看有哪些工具需要修。",
                    "parameters": _p({}, []),
                },
            })
            defs.append({
                "type": "function",
                "function": {
                    "name": "fix_tool_bugs",
                    "description": "工具医生：批量诊断并修复待修 bug。链路：读 bug→读原工具 impl→LLM 诊断→沙箱跑 example 回归→通过才覆盖入库，失败保留原版可回退。",
                    "parameters": _p({
                        "limit": {"type": "integer", "description": "最多修复几个工具（默认 5，上限 20）"},
                    }, []),
                },
            })
            defs.append({
                "type": "function",
                "function": {
                    "name": "clear_tool_bug",
                    "description": "手动标记单条 bug 为已修（用户确认无需自动修复时用）。",
                    "parameters": _p({
                        "bug_id": {"type": "string", "description": "bug 编号（list_tool_bugs 可查）"},
                    }, ["bug_id"]),
                },
            })
    # v6.3 Notepad 暂存（跨轮中间结果）
    if getattr(cfg, "ENABLE_NOTEPAD", True):
        # v8.13：notepad_save/clear 是写操作，chat 只读形态不暴露
        if not readonly:
            defs.append({
                "type": "function",
                "function": {
                    "name": "notepad_save",
                    "description": "暂存一条中间结果（跨轮可用）。多步重构进度/待回填 TODO/对比基线等场景。",
                    "parameters": _p({
                        "key": {"type": "string", "description": "暂存键名（英文小写下划线）"},
                        "content": {"type": "string", "description": "暂存内容"},
                    }, ["key", "content"]),
                },
            })
        defs.append({
            "type": "function",
            "function": {
                "name": "notepad_read",
                "description": "读取暂存内容。",
                "parameters": _p({"key": {"type": "string", "description": "暂存键名"}}, ["key"]),
            },
        })
        defs.append({
            "type": "function",
            "function": {
                "name": "notepad_list",
                "description": "列出所有暂存 key 与预览。",
                "parameters": _p({}, []),
            },
        })
        if not readonly:
            defs.append({
                "type": "function",
                "function": {
                    "name": "notepad_clear",
                    "description": "清除暂存（key 为空清全部，非空清单条）。",
                    "parameters": _p({"key": {"type": "string", "description": "要清除的键名（空=全部）"}}, []),
                },
            })
    # v8.18 Agent 长期记忆库（教训沉淀 + 检索注入）
    if getattr(cfg, "ENABLE_AGENT_MEMORY", True):
        defs.append({
            "type": "function",
            "function": {
                "name": "memory_read",
                "description": "检索长期记忆库中的历史教训/用户偏好/项目知识（跨会话沉淀）。执行硬件刷写、烧录、底层调试、重复性任务前先查，避开已知坑。",
                "parameters": _p({
                    "query": {"type": "string", "description": "检索关键词（空=返回高频教训）"},
                    "limit": {"type": "integer", "description": "返回条数（默认 5，最多 10）"},
                }, []),
            },
        })
        if not readonly:
            defs.append({
                "type": "function",
                "function": {
                    "name": "memory_record",
                    "description": "把本回合发现的教训/用户偏好写入长期记忆库（跨会话复用，算力漂移时随快照同步合体）。只记模式级错误与稳定偏好，不记一次性小事。",
                    "parameters": _p({
                        "kind": {"type": "string", "description": "fault（故障）/ lesson（教训）/ preference（用户偏好）/ knowledge（项目知识）"},
                        "scope": {"type": "string", "description": "global（跨项目通用坑）/ local（仅本项目）"},
                        "phenomenon": {"type": "string", "description": "错误现象或偏好内容（一句话）"},
                        "root_cause": {"type": "string", "description": "可能的远因"},
                        "solution": {"type": "string", "description": "下次的正确做法"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "检索标签（如 K210/超时）"},
                    }, ["phenomenon"]),
                },
            })
    # v6.3 Checkpoint 查询（恢复由 GUI 右键菜单触发）
    if getattr(cfg, "ENABLE_CHECKPOINT", True):
        defs.append({
            "type": "function",
            "function": {
                "name": "list_checkpoints",
                "description": "列出最近的 AI 改动 checkpoint（write/edit 前自动快照的原文件）。供查询历史改动。",
                "parameters": _p({"limit": {"type": "integer", "description": "返回条数（默认 20）"}}, []),
            },
        })
    # v8.9 回退审核日志（只读）
    if getattr(cfg, "ENABLE_AUDIT_LOG", True):
        defs.append({
            "type": "function",
            "function": {
                "name": "list_audits",
                "description": "查看最近的回退/删除/恢复/漂移回本地等审计记录（tail 默认 50，可按 action 过滤）。",
                "parameters": _p({
                    "tail": {"type": "integer", "description": "返回条数（默认 50，最大 500）"},
                    "action": {"type": "string", "description": "按动作过滤，如 delete_file/restore_session"},
                }, []),
            },
        })
    # v8.25 用户文件保护 + 一键备份 + 重名治理（三层贯通：config字段+defs裁剪+设置对话框SWITCHES）
    if getattr(cfg, "ENABLE_FULL_BACKUP", True) and not readonly:
        defs.append({
            "type": "function",
            "function": {
                "name": "backup_workspace",
                "description": "一键备份完整工作区到 backups/<时间>_full.zip（含data/会话快照，非Git自动备份出口）。用户要求备份时用；动用户资产文件前先建议用户备份。",
                "parameters": _p({
                    "label": {"type": "string", "description": "备份标签（默认full，字母数字_-）"},
                }, []),
            },
        })
    if getattr(cfg, "ENABLE_WORK_COPY", True) and not readonly:
        defs.append({
            "type": "function",
            "function": {
                "name": "copy_user_asset",
                "description": "v8.26 工作副本：把工作区文件（尤其用户资产 PPT/Excel/Word/PDF）拷贝为 workcopy/ 下的工作副本（时间-作者-内容命名），原文件保持不变。处理用户资产内容前先征得用户同意再调用。",
                "parameters": _p({
                    "path": {"type": "string", "description": "原文件相对路径"},
                    "actor": {"type": "string", "description": "作者标识（默认AI）"},
                }, ["path"]),
            },
        })
    if getattr(cfg, "ENABLE_AMBIGUOUS_GUARD", True):
        defs.append({
            "type": "function",
            "function": {
                "name": "scan_ambiguous_files",
                "description": "扫描重名/命名不清文件（副本/带(1)/新建未命名等）。copilot或依赖树发现文件名奇怪时必须调用，结果请用户逐组识别。",
                "parameters": _p({}, []),
            },
        })
        if not readonly:
            defs.append({
                "type": "function",
                "function": {
                    "name": "quarantine_files",
                    "description": "把用户确认不要的重名文件备份转移到 backups/quarantine/<时间>/（保留原相对路径，可恢复）。需用户审批。",
                    "parameters": _p({
                        "files": {"type": "array", "items": {"type": "string"}, "description": "要转移的相对路径列表"},
                        "reason": {"type": "string", "description": "原因说明"},
                    }, ["files"]),
                },
            })
    if expert_mode:
        defs.append({
            "type": "function",
            "function": {
                "name": "todo_update",
                "description": "更新你的私有待办清单（勿碰根目录 todo.md）。action=set整体替换/add追加/done标记完成。开始工作前先拆解待办，逐项完成。",
                "parameters": _p({
                    "action": {"type": "string", "description": "set | add | done"},
                    "todos": {"type": "array", "description": "action=set 时的完整清单 [{text, done}]"},
                    "text": {"type": "string", "description": "action=add/done 时的条目文本"},
                }, ["action"]),
            },
        })
        defs.append({
            "type": "function",
            "function": {
                "name": "port_declare",
                "description": "登记/更新端口到 ports.md（需要、变更、使用端口时必须调用）。写前自动抢租约锁。",
                "parameters": _p({
                    "port": {"type": "string", "description": "端口号"},
                    "status": {"type": "string", "description": "状态（锁定/释放/占用）"},
                    "type": {"type": "string", "description": "类型（http/db/ws 等）"},
                    "location": {"type": "string", "description": "所在文件/服务"},
                    "call_how": {"type": "string", "description": "调取方式"},
                    "callers": {"type": "string", "description": "调用数量与位置"},
                }, ["port"]),
            },
        })
        defs.append({
            "type": "function",
            "function": {
                "name": "port_read",
                "description": "读取 ports.md 端口登记表（只读）。",
                "parameters": _p({}, []),
            },
        })
        defs.append({
            "type": "function",
            "function": {
                "name": "request_write_permission",
                "description": "向总司令申请临时写权限（需过副驾驶审查）。仅当确需修改申报清单外文件时调用。",
                "parameters": _p({"purpose": {"type": "string", "description": "申请理由与目标文件"}}),
            },
        })
    # v1.1.0 DeveraiIntegrityService 完整性校验工具
    if getattr(cfg, "ENABLE_INTEGRITY", True) and not readonly:
        defs.append({
            "type": "function",
            "function": {
                "name": "integrity_sign",
                "description": "对数据生成 HMAC-SHA256 签名（防篡改）。data 为 base64 编码，ctx 为类型：tool/asset/doc/voice-task/voice-constraint。",
                "parameters": _p({
                    "data": {"type": "string", "description": "base64 编码的待签名数据"},
                    "ctx": {"type": "string", "description": "受保护类型：tool/asset/doc/voice-task/voice-constraint"},
                }, ["data"]),
            },
        })
        defs.append({
            "type": "function",
            "function": {
                "name": "integrity_verify",
                "description": "验证数据的 HMAC-SHA256 签名是否有效（timing-safe 比较）。",
                "parameters": _p({
                    "data": {"type": "string", "description": "base64 编码的原始数据"},
                    "signature": {"type": "string", "description": "十六进制编码的签名"},
                    "ctx": {"type": "string", "description": "受保护类型：tool/asset/doc/voice-task/voice-constraint"},
                }, ["data", "signature"]),
            },
        })
        defs.append({
            "type": "function",
            "function": {
                "name": "integrity_hash",
                "description": "快速计算数据的 SHA-256 内容哈希（用于快速比对）。",
                "parameters": _p({
                    "data": {"type": "string", "description": "base64 编码的数据"},
                }, ["data"]),
            },
        })
    if getattr(cfg, "ENABLE_INTEGRITY", True):
        defs.append({
            "type": "function",
            "function": {
                "name": "integrity_self_check",
                "description": "运行 DeveraiIntegrityService 自检：验证所有受保护类型的签名/验证功能正常，并测试跨类型重放攻击防护。",
                "parameters": _p({}, []),
            },
        })
    # v5: 注册全量清单供裁剪与 search_tool 使用
    # v8.14：先构建新 dict 再整体替换引用——此前 clear+逐条重灌期间，
    # 并发调用（并行专家/AOE 子 Agent 的 to_thread）会读到空表甚至抛
    # "dict changed size during iteration"
    globals()["_ALL_DEFS"] = {d["function"]["name"]: d for d in defs}
    return defs


# --------------------------------------------------------------------------
# v5 工具匹配序列与裁剪（仅 token 计费模式生效；KV 缓存友好的方案 A）
# --------------------------------------------------------------------------
_ALL_DEFS: dict = {}
_BG_TASKS: set = set()  # v8.14：后台任务强引用集，防 create_task 结果被 GC

TOOL_MATCH = {
    "write_file": ["写", "创建", "生成", "新增文件", "输出文件", "保存", "write", "create"],
    "edit_file": ["修改", "编辑", "替换", "重构", "修复", "改", "edit", "fix", "patch"],
    "grep": ["搜索内容", "查找代码", "正则", "定位代码", "grep", "匹配行"],
    "glob": ["文件列表", "查找文件", "glob", "匹配文件", "目录遍历"],
    "run_command": ["命令", "执行", "运行", "构建", "测试", "安装", "启动", "command", "build", "test", "pip", "npm", "run"],
    # v8.18 持久命令行环境
    "term_create": ["创建环境", "新建终端环境", "持久环境", "env create"],
    "term_list": ["环境列表", "列出环境", "查看环境", "env list"],
    "term_delete": ["删除环境", "移除环境", "env delete"],
    "delete_file": ["删除", "移除", "delete", "remove"],
    "delegate_task": ["委派", "子任务", "并行子任务", "delegate"],
    "plan_and_execute": ["规划", "拆解", "复杂任务", "dag", "plan"],
    "search_vault": ["资产", "复用", "模板", "vault"],
    "store_asset": ["存资产", "入库", "复用沉淀", "store"],
    "inspect_asset": ["资产检查", "资料检查", "资料体检", "资产体检", "检查资产", "资料完整性"],
    "repair_assets": ["修复资产", "资产修复", "修复资料", "资料修复"],
    "web_search": ["搜索", "查资料", "互联网", "网络", "benchmark", "文档查询", "search", "web"],
    "file_search": ["找文件", "搜索文件", "定位文件", "文件名"],
    "port_declare": ["端口", "port", "占用端口", "服务端口"],
    "port_read": ["端口", "port"],
    "port_refs": ["端口引用", "端口定位", "找端口", "改端口", "端口位置", "port"],
    "todo_update": ["待办", "todo", "清单"],
    "request_write_permission": ["写权限", "权限", "越权写"],
    "build_tool": ["造工具", "写工具", "做工具", "建工具", "写个脚本", "做个脚本", "转换器", "小工具", "工具设计"],
    "list_tools": ["工具列表", "自研工具", "已有工具", "工具库"],
    "use_tool": ["用工具", "调用工具", "运行工具", "执行工具"],
    "list_tool_bugs": ["工具bug", "工具错误", "工具失败", "工具问题", "坏工具"],
    "fix_tool_bugs": ["修工具", "修复工具", "工具医生", "tool doctor", "工具修复"],
    "clear_tool_bug": ["清除bug", "标记已修", "bug已修"],
    "notepad_save": ["暂存", "记下", "notepad", "备忘"],
    "notepad_read": ["读暂存", "读备忘", "notepad"],
    "notepad_list": ["暂存列表", "备忘录", "notepad"],
    "notepad_clear": ["清暂存", "清备忘"],
    "memory_read": ["记忆", "教训", "历史错误", "之前错过", "上次错", "memory"],
    "memory_record": ["记住这个", "记入记忆", "沉淀教训", "记住教训", "memory record"],
    "list_checkpoints": ["checkpoint", "历史改动", "恢复点", "改动历史"],
    "list_audits": ["审计", "审计日志", "回退记录", "删除记录", "恢复记录", "audit"],
    # v6.6 浏览器控制
    "browser_open": ["打开网页", "打开浏览器", "外链", "网页", "浏览器", "演示页面", "open url"],
    "browser_read": ["读网页", "抓取网页", "页面内容", "网页正文", "在线文档", "爬网页", "浏览网页"],
    "browser_screenshot": ["截图", "网页截图", "页面截图", "screen shot", "整页截图"],
    "browser_elements": ["可点击", "可交互元素", "按钮清单", "页面结构", "点击点", "竞品分析", "理解页面", "element"],
    "browser_launch": ["启动浏览器", "打开受控浏览器", "直接操控浏览器", "browser launch"],
    "browser_navigate": ["浏览器跳转", "受控浏览器跳转", "navigate"],
    "browser_click": ["浏览器点击", "受控浏览器点击", "点击按钮", "browser click"],
    "browser_type": ["浏览器输入", "受控浏览器输入", "browser type"],
    "browser_press_keys": ["浏览器按键", "受控浏览器按键", "browser key"],
    "browser_close": ["关闭受控浏览器", "browser close"],
    # v8.14 F12 开发者工具
    "browser_networks_start": ["开始抓包", "网络捕获", "network capture", "开始记录网络", "抓网络请求"],
    "browser_networks_stop": ["停止抓包", "停止网络捕获", "network stop"],
    "browser_networks_get": ["获取网络请求", "网络记录", "network requests", "查看网络", "F12 networks"],
    "browser_storage_get": ["获取存储", "查看cookie", "查看localStorage", "F12 storage", "页面存储"],
    "browser_storage_set": ["设置存储", "设置cookie", "设置localStorage", "storage set"],
    "browser_storage_clear": ["清除存储", "清除cookie", "清除localStorage", "storage clear"],
    "browser_console_get": ["控制台日志", "console log", "F12 console", "浏览器日志"],
    "browser_console_eval": ["执行JS", "执行JavaScript", "console eval", "eval JS", "页面执行"],
    "browser_sources_list": ["源文件列表", "JS源文件", "F12 sources", "脚本列表"],
    "browser_sources_get": ["获取源文件", "JS内容", "source content", "脚本内容"],
    "browser_find_api_endpoints": ["查找API", "API端点", "发现接口", "find api", "付费API", "接口发现"],
    # v8.4 Python 应用截图 / UI 自截图确认
    "app_screenshot": ["应用截图", "运行截图", "python截图", "窗口截图", "ui截图", "界面截图", "运行看看", "效果图", "截图", "GUI截图", "界面效果"],
    "ui_review": ["视觉审查", "截图审查", "看看效果", "UI审查", "界面审查", "视觉专家", "图片审查", "审查截图"],
    # v8.6 外部程序/浏览器自动化
    "exe_launch": ["启动程序", "运行exe", "打开外部程序", "launch"],
    "exe_list_windows": ["枚举窗口", "列出窗口", "窗口列表"],
    "exe_screenshot": ["外部程序截图", "窗口截图", "程序截图"],
    "exe_journal": ["探索记录", "操作记录", "exe journal", "软件探索"],
    "exe_click": ["点击坐标", "鼠标点击", "click"],
    "exe_type": ["输入文本", "键盘输入", "type"],
    "exe_press_keys": ["按键", "快捷键", "回车", "press key"],
    "exe_close": ["关闭程序", "结束进程", "关闭窗口", "close"],
    # v1.1.0 DeveraiIntegrityService 完整性校验
    "integrity_sign": ["签名", "防篡改", "数据签名", "integrity sign", "hmac签名"],
    "integrity_verify": ["验证签名", "校验签名", "签名验证", "integrity verify", "防篡改验证"],
    "integrity_hash": ["内容哈希", "数据哈希", "sha256", "integrity hash", "快速哈希"],
    "integrity_self_check": ["完整性自检", "自检", "integrity check", "签名自检", "防篡改自检"],
    # DashScope API（通义万相）
    "dashscope_image_generate": ["文生图", "生成图片", "AI绘图", "AI生图", "画一张", "image generate", "text to image", "画画", "出图"],
    "dashscope_image_edit": ["图生图", "编辑图片", "修图", "改图", "图片编辑", "image edit", "p图", "改图片"],
    "dashscope_video_generate": ["文生视频", "生成视频", "AI视频", "做视频", "video generate", "text to video", "图生视频", "视频生成"],
    "dashscope_task_status": ["任务状态", "视频生成状态", "查询任务", "task status", "生成进度"],
    "dashscope_list_models": ["可用模型", "模型列表", "通义万相模型", "万相模型", "list models", "模型清单"],
    # UI 元素检视与可靠交互
    "ui_enum_controls": ["枚举控件", "列出控件", "控件列表", "子窗口", "enum controls", "窗口控件"],
    "ui_find_control": ["找控件", "定位控件", "查找按钮", "查找输入框", "find control", "定位元素"],
    "ui_control_click": ["点击控件", "控件点击", "点击按钮", "control click", "点击元素"],
    "ui_control_set_text": ["控件输入", "设置控件文本", "输入框填写", "control type", "填写输入框"],
    "ui_control_get_text": ["读取控件文本", "获取控件文本", "控件内容", "control text", "获取文本"],
    "ui_get_tree": ["窗口树", "控件树", "窗口结构", "window tree", "UI结构", "界面结构"],
    # v8.25 一键备份 + 重名治理
    "backup_workspace": ["一键备份", "完整备份", "备份工作区", "备份", "backup", "full backup"],
    "scan_ambiguous_files": ["重名", "重复文件", "命名不清", "文件名奇怪", "副本", "copy", "(1)", "ambiguous", "scan duplicate"],
    "quarantine_files": ["隔离", "转移", "备份转移", "quarantine", "整理重复文件"],
    "copy_user_asset": ["工作副本", "生成副本", "拷贝一份", "copy_user_asset", "副本处理"],
    "ui_get_control_info": ["控件信息", "控件详情", "control info", "元素信息"],
}

# 裁剪后无条件保留的基础工具（读类 + 元工具）
BASE_TOOL_NAMES = {"list_dir", "read_file", "workspace_info", "file_search", "search_tool",
                   "app_screenshot", "ui_review"}

# 专家实例的写工具保底：裁剪不得把专家变成只读（P1）
EXPERT_WRITE_TOOLS = {"write_file", "edit_file", "run_command"}


def select_tools(cfg: Config, defs: list, todo_text: str, expert: bool = False) -> list:
    """v5 工具裁剪：仅 token 计费模式生效。按当前待办/任务文本与工具匹配序列做
    轻量匹配（低内存→子串；否则走 matcher 三级引擎 char/bm25/api，阈值 0.35），
    并上基础工具集。非计费模式原样返回（不裁剪）。expert=True 时写工具保底保留。"""
    if not getattr(cfg, "token_mode", False):
        return defs
    text = (todo_text or "").strip().lower()
    if not text:
        return defs
    low_mem = bool(getattr(cfg, "low_memory_mode", False))
    keep = set(BASE_TOOL_NAMES)
    if expert:
        keep |= EXPERT_WRITE_TOOLS
    if low_mem:
        for d in defs:
            name = d["function"]["name"]
            if any(k in text for k in TOOL_MATCH.get(name, [])):
                keep.add(name)
    else:
        from .matcher import rank as _match_rank
        _names, _docs = [], []
        for d in defs:
            name = d["function"]["name"]
            keys = TOOL_MATCH.get(name, [])
            if not keys:
                continue
            _names.append(name)
            _docs.append(" ".join(keys))
        # v8.1：匹配走三级引擎（char/bm25/api），单轨不混用，避免互相干扰
        for _i, _s in _match_rank(todo_text, _docs, min_score=0.35):
            keep.add(_names[_i])
    return [d for d in defs if d["function"]["name"] in keep]


# ---------------- v8.18 Agent 长期记忆库 ----------------
async def tool_memory_read(args, ctx: ToolContext) -> dict:
    """检索长期记忆库（历史教训/用户偏好/项目知识）。执行底层/重复任务前先查。"""
    if not getattr(ctx.cfg, "ENABLE_AGENT_MEMORY", True):
        return {"ok": False, "output": "长期记忆库未启用（ENABLE_AGENT_MEMORY）。"}
    from . import memory as agent_memory
    query = str(args.get("query") or "").strip()
    limit = min(max(_safe_int(args.get("limit"), 5), 1), 10)
    try:
        entries = await asyncio.to_thread(agent_memory.query, query, limit)
    except Exception as e:
        return _format_error(e)
    if not entries:
        return {"ok": True, "output": "记忆库中暂无相关条目。"}
    lines = ["[长期记忆检索结果]"]
    for e in entries:
        lines.append(
            f"- [{e.get('scope','?')}/{e.get('kind','?')}] {str(e.get('phenomenon',''))[:150]}"
            f" → 远因: {str(e.get('root_cause') or '')[:120]}"
            f" → 对策: {str(e.get('solution') or '')[:120]}"
            f"（命中 {e.get('count', 1)} 次）"
        )
    return {"ok": True, "output": "\n".join(lines), "meta": {"count": len(entries)}}


async def tool_memory_record(args, ctx: ToolContext) -> dict:
    """把本回合发现的教训/用户偏好写入长期记忆库（跨会话复用）。"""
    block = _readonly_block("memory_record")
    if block:
        return block
    if not getattr(ctx.cfg, "ENABLE_AGENT_MEMORY", True):
        return {"ok": False, "output": "长期记忆库未启用（ENABLE_AGENT_MEMORY）。"}
    from . import memory as agent_memory
    phenomenon = str(args.get("phenomenon") or "").strip()
    if not phenomenon:
        return {"ok": False, "output": "phenomenon 不能为空（一句话描述错误现象或偏好）。"}
    try:
        entry = await asyncio.to_thread(
            agent_memory.record,
            str(args.get("kind") or "lesson"),
            str(args.get("scope") or "local"),
            phenomenon,
            str(args.get("root_cause") or ""),
            str(args.get("solution") or ""),
            _as_str_list(args.get("tags"))[:5],
            "agent",
        )
    except ValueError as e:
        return {"ok": False, "output": str(e)}
    except Exception as e:
        return _format_error(e)
    action = "更新已有条目" if entry.get("_merged") else "新增条目"
    return {"ok": True, "output": f"已{action}（记忆库）：{str(entry.get('phenomenon', ''))[:120]}"}


TOOL_HANDLERS = {
    "list_dir": tool_list_dir,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "delete_file": tool_delete_file,
    "grep": tool_grep,
    "glob": tool_glob,
    "run_command": tool_run_command,
    # v8.18 持久命令行环境
    "term_create": tool_term_create,
    "term_list": tool_term_list,
    "term_delete": tool_term_delete,
    "workspace_info": tool_workspace_info,
    "search_vault": tool_search_vault,
    "store_asset": tool_store_asset,
    "inspect_asset": tool_inspect_asset,
    "repair_assets": tool_repair_assets,
    "delegate_task": tool_delegate_task,
    "plan_and_execute": tool_plan_and_execute,
    # v5
    "web_search": tool_web_search,
    "file_search": tool_file_search,
    "todo_update": tool_todo_update,
    "port_declare": tool_port_declare,
    "port_read": tool_port_read,
    "search_tool": tool_search_tool,
    "request_write_permission": tool_request_write_permission,
    # v6
    "build_tool": tool_build_tool,
    "list_tools": tool_list_tools,
    "use_tool": tool_use_tool,
    "port_refs": tool_port_refs,
    # v6.3 工具医生
    "list_tool_bugs": tool_list_tool_bugs,
    "fix_tool_bugs": tool_fix_tool_bugs,
    "clear_tool_bug": tool_clear_tool_bug,
    # v6.3 Notepad / Checkpoint
    "notepad_save": tool_notepad_save,
    "notepad_read": tool_notepad_read,
    "notepad_list": tool_notepad_list,
    "notepad_clear": tool_notepad_clear,
    # v8.18 Agent 长期记忆库
    "memory_read": tool_memory_read,
    "memory_record": tool_memory_record,
    "list_checkpoints": tool_list_checkpoints,
    "list_audits": tool_list_audits,
    # v8.25 一键备份 + 重名治理
    "backup_workspace": tool_backup_workspace,
    "scan_ambiguous_files": tool_scan_ambiguous_files,
    "quarantine_files": tool_quarantine_files,
    "copy_user_asset": tool_copy_user_asset,
    # ---- v6.6 浏览器控制（外交型）
    "browser_open": tool_browser_open,
    "browser_read": tool_browser_read,
    "browser_screenshot": tool_browser_screenshot,
    "browser_elements": tool_browser_elements,
    # v8.9 直接操控浏览器（CDP）
    "browser_launch": tool_browser_launch,
    "browser_navigate": tool_browser_navigate,
    "browser_click": tool_browser_click,
    "browser_type": tool_browser_type,
    "browser_press_keys": tool_browser_press_keys,
    "browser_close": tool_browser_close,
    # v8.14 F12 开发者工具（Networks / Storage / Console / Sources）
    "browser_networks_start": tool_browser_networks_start,
    "browser_networks_stop": tool_browser_networks_stop,
    "browser_networks_get": tool_browser_networks_get,
    "browser_storage_get": tool_browser_storage_get,
    "browser_storage_set": tool_browser_storage_set,
    "browser_storage_clear": tool_browser_storage_clear,
    "browser_console_get": tool_browser_console_get,
    "browser_console_eval": tool_browser_console_eval,
    "browser_sources_list": tool_browser_sources_list,
    "browser_sources_get": tool_browser_sources_get,
    "browser_find_api_endpoints": tool_browser_find_api_endpoints,
    # v8.4 Python 应用截图 / UI 自截图确认
    "app_screenshot": tool_app_screenshot,
    "ui_review": tool_ui_review,
    # v8.6 外部程序/浏览器自动化
    "exe_launch": tool_exe_launch,
    "exe_list_windows": tool_exe_list_windows,
    "exe_screenshot": tool_exe_screenshot,
    "exe_click": tool_exe_click,
    "exe_type": tool_exe_type,
    "exe_press_keys": tool_exe_press_keys,
    "exe_close": tool_exe_close,
    "exe_journal": tool_exe_journal,
    # v1.1.0 DeveraiIntegrityService 完整性校验
    "integrity_sign": tool_integrity_sign,
    "integrity_verify": tool_integrity_verify,
    "integrity_hash": tool_integrity_hash,
    "integrity_self_check": tool_integrity_self_check,
    # DashScope API（通义万相）
    "dashscope_image_generate": tool_dashscope_image_generate,
    "dashscope_image_edit": tool_dashscope_image_edit,
    "dashscope_video_generate": tool_dashscope_video_generate,
    "dashscope_task_status": tool_dashscope_task_status,
    "dashscope_list_models": tool_dashscope_list_models,
    # UI 元素检视与可靠交互
    "ui_enum_controls": tool_ui_enum_controls,
    "ui_find_control": tool_ui_find_control,
    "ui_control_click": tool_ui_control_click,
    "ui_control_set_text": tool_ui_control_set_text,
    "ui_control_get_text": tool_ui_control_get_text,
    "ui_get_tree": tool_ui_get_tree,
    "ui_get_control_info": tool_ui_get_control_info,
}


def _unattended_note(ctx: "ToolContext", tool: str, payload: dict) -> None:
    """v8.27 不看守模式：危险动作被跳过时记入保留进度台账（工作区 unattended_progress.json）。

    台账随工作区快照往返（漂移上服务器、回本机都接得上）；失败静默（不阻塞主流程）。
    """
    try:
        import json as _json
        import datetime as _dt
        from .storage import load_json as _lj, save_json as _sj
        ws = str(getattr(ctx, "workspace", "") or (ctx.cfg.workspace if ctx.cfg else "") or "")
        if not ws:
            return
        p = Path(ws) / "unattended_progress.json"
        data = _lj(p, {}) or {}
        items = data.get("skipped") or []
        items.append({
            "ts": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tool": str(tool),
            "brief": str(payload.get("command") or payload.get("path")
                         or payload.get("label") or payload.get("files") or "")[:200],
        })
        data["skipped"] = items[-200:]
        data["updated"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _sj(p, data)
    except Exception:
        pass


async def _request_approval(ctx: ToolContext, tool: str, payload: dict) -> bool:
    """v5 审批门四模式路由：all（全弹）/ danger（仅危险弹）/ copilot（副驾驶代批）/ free（全放）。
    需要弹 UI 时发出审批请求并等待；超时/异常按拒绝处理并清理 pending。

    v6.6 外交型任务硬性规定（独立于 ENABLE_APPROVAL / approval_mode，无条件生效）：
    外交型任务允许全放行（不弹窗），但一旦 AI 检测到不完全符合用户要求就立即拦截。
    v8.27 不看守模式（ENABLE_UNATTENDED，最高优先）：无人值守不弹审批——非危险动作自动放行，
    危险动作跳过并记入保留进度台账，绝不阻塞等待。
    """
    cfg = ctx.cfg
    # v8.27 不看守模式（最高优先，先于外交型路由）：无人值守不弹审批——
    # 非危险动作（含外交型工具，其载荷不带 dangerous 标志）自动放行；
    # 危险动作跳过并记入保留进度台账，绝不阻塞等待（外交型副驾驶 uncertain 最长 600s）。
    if getattr(cfg, "ENABLE_UNATTENDED", False):
        if payload.get("dangerous") or is_dangerous(payload):
            _unattended_note(ctx, tool, payload)
            return False
        return True
    if tool in DIPLOMATIC_TOOLS:
        return await _diplomatic_guard(ctx, tool, payload)
    if not getattr(cfg, "ENABLE_APPROVAL", True):
        return True
    mode = getattr(cfg, "approval_mode", "danger") or "danger"
    if mode == "free":
        return True
    if mode == "danger" and not is_dangerous(payload):
        return True
    if mode == "copilot":
        # 副驾驶代批：恶意危险直接掐断；非恶意放行；拿不准才升级用户
        try:
            from .experts import copilot_check  # 延迟导入避免循环依赖
            verdict = await copilot_check(cfg, {"kind": "approval", "tool": tool, **payload})
        except Exception:
            verdict = {"kill": False, "uncertain": True, "note": "副驾驶不可用，升级用户审批"}
        if verdict.get("kill"):
            await _emit(ctx, {"type": "copilot_block", "note": verdict.get("note", ""),
                              "payload": payload})
            return False
        if not verdict.get("uncertain"):
            return True
        # 拿不准 → 落入下方用户审批
    if ctx.approval is None:
        return False
    try:
        fut = ctx.approval.request(ctx.call_id)
        await _emit(ctx, {"type": "approval_needed", "call_id": ctx.call_id, "tool": tool, "payload": payload})
        return bool(await asyncio.wait_for(fut, timeout=600.0))
    except asyncio.TimeoutError:
        ctx.approval.resolve(ctx.call_id, False)  # M14: 清理残留 Future
        return False
    except BaseException:
        ctx.approval.resolve(ctx.call_id, False)
        raise


async def _emit(ctx: ToolContext, event: dict) -> None:
    if ctx.emit is not None:
        try:
            await ctx.emit(event)
        except Exception:
            pass


async def _request_diff_approval(ctx: ToolContext, rel: str, old: str, new: str) -> bool:
    """v6.4 内联差异预览审批：发出 diff_preview_needed 事件，UI 弹 DiffPreviewDialog，
    用户点"接受"返回 True 才落盘，"拒绝"返回 False 取消写入。

    复用 ApprovalGate 的 Future 机制（与命令审批门共用 call_id）。
    新文件（old 为空）不弹 diff（无对比意义），直接放行。
    超大 diff（>2000 行）跳过预览避免 UI 卡死，直接放行。
    """
    cfg = ctx.cfg
    if getattr(cfg, "ENABLE_UNATTENDED", False):
        return True  # v8.28 不看守：diff 预览需人工确认，无人值守直接放行（危险动作仍走台账）
    if not getattr(cfg, "ENABLE_DIFF_PREVIEW", True):
        return True
    # 新文件无对比意义
    if not old.strip():
        return True
    # v8.13：超大 diff 不再直接放行——跳过预览后改走审批门（dangerous 标记，danger 模式也弹窗），
    # 避免 LLM 制造大 diff 绕过写前确认
    if max(len(old.splitlines()), len(new.splitlines())) > 2000:
        # v8.17：无审批门环境（测试/无UI）保持与 diff≤2000 路径一致——直接放行；
        # 有审批门时走审批（dangerous 标记，danger 模式也弹窗）
        if ctx.approval is None or ctx.emit is None:
            return True
        return await _request_approval(
            ctx, "write_file",
            {"command": f"写入 {rel}（差异超过 2000 行，跳过差异预览）",
             "path": str(rel), "dangerous": True})
    if ctx.approval is None or ctx.emit is None:
        return True  # 无 UI 环境（测试）或无人渲染事件（防 future 悬挂）直接放行
    try:
        fut = ctx.approval.request(ctx.call_id)
        await _emit(ctx, {
            "type": "diff_preview_needed",
            "call_id": ctx.call_id,
            "rel": str(rel),
            "old": old,
            "new": new,
        })
        return bool(await asyncio.wait_for(fut, timeout=600.0))
    except asyncio.TimeoutError:
        ctx.approval.resolve(ctx.call_id, False)
        return False
    except BaseException:
        ctx.approval.resolve(ctx.call_id, False)
        raise
