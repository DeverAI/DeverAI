"""v8.30 上传入库（Future.md 方案1：桌面拖拽/粘贴入工作区 uploads/）。

- 目标位：uploads/<时间戳>-<actor>/<原文件名>——入区即受用户文件保护、命名规则与
  一键备份覆盖；drift 上传/manifest/全量备份按普通工作区文件处理（增量靠 mtime）。
- 敏感名拒绝：config.json/err.log/api.txt/api_keys.py/.env + 常见密钥后缀（与
  sync.build_drift_upload 的 excl_name 同源，防密钥绕道入区击穿信封模型）。
- 不 checkpoint：uploads/ 前缀在 checkpoint.py 跳过（Future.md 约束，防 C 盘膨胀；
  session_snap 整包与一键备份仍是安全网）。
- 本模块不依赖 Qt：剪贴板图片由 gui 侧用 alloc_dest 分配目标位后 QImage.save 落盘。
"""
from __future__ import annotations
import datetime
import shutil
from pathlib import Path

UPLOAD_DIR_NAME = "uploads"
MAX_UPLOAD_BYTES = 512 * 1024 * 1024   # 单文件上限（防误拖盘镜像/巨型压缩包）
SENSITIVE_NAMES = {"config.json", "err.log", "api.txt", "api_keys.py", ".env"}
SENSITIVE_SUFFIXES = (".pem", ".key", ".pfx", ".p12")


def is_sensitive_name(name: str) -> bool:
    """敏感/空文件名判定（小写比对，Windows 大小写不敏感语义一致）。"""
    n = str(name or "").strip().lower()
    return (not n) or n in SENSITIVE_NAMES or n.endswith(SENSITIVE_SUFFIXES)


def alloc_dest(ws: str, filename: str, actor: str = "user") -> tuple:
    """分配 uploads/<ts>-<actor>/<名> 目标位（同目录同名自动加序号）。

    只取 basename 防路径成分；返回 (绝对目标路径, 相对路径)，
    工作区不存在/文件名非法或敏感时返回 (None, 原因)。"""
    ws_p = Path(str(ws or "")).resolve()
    if not ws_p.is_dir():
        return None, "工作区不存在"
    name = Path(str(filename or "")).name
    if is_sensitive_name(name):
        return None, f"敏感文件名拒绝入库：{name}"
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_actor = "".join(c for c in str(actor or "user") if c.isalnum() or c in "_-")[:32] or "user"
    dest_dir = ws_p / UPLOAD_DIR_NAME / f"{ts}-{safe_actor}"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return None, f"创建 uploads 目录失败: {e}"
    stem, suffix = Path(name).stem, Path(name).suffix
    dest = dest_dir / name
    k = 1
    while dest.exists():
        dest = dest_dir / f"{stem}_{k}{suffix}"
        k += 1
    try:
        return dest, dest.relative_to(ws_p).as_posix()
    except ValueError:
        return None, "目标位越界"


def ingest_file(ws: str, src: str, actor: str = "user") -> tuple:
    """把本机文件拷入工作区 uploads/。返回 (ok, 信息)：ok=True 信息=相对路径。"""
    src_p = Path(str(src or ""))
    try:
        if not src_p.is_file() or src_p.is_symlink():
            return False, "源不是普通文件"
        if src_p.stat().st_size > MAX_UPLOAD_BYTES:
            return False, f"超过单文件上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB"
    except OSError as e:
        return False, f"读取源文件失败: {e}"
    dest, info = alloc_dest(ws, src_p.name, actor)
    if dest is None:
        return False, info
    try:
        # v8.31 审计修复：用 shutil.copy（不保留源 mtime）——drift minimal 增量按
        # mtime > 上次推送基准筛文件（sync.build_drift_upload），copy2 会带着源文件的
        # 旧 mtime，导致刚入库的老文件永远进不了增量包（续算端工作区缺文件）。
        shutil.copy(src_p, dest)
        # v8.31 审计修复：拷后复核大小——先查后拷存在源文件拷贝期间变大的 TOCTOU 窗口
        if dest.stat().st_size > MAX_UPLOAD_BYTES:
            dest.unlink(missing_ok=True)
            return False, f"超过单文件上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB（拷贝期间变大）"
    except OSError as e:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"拷贝失败: {e}"
    return True, info
