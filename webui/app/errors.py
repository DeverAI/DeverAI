"""错误记录与自动存错机制：所有运行时错误(RE)捕获后写入 Err.log。

规则：Err.log 仅支持读取与清空；修复完成后必须清空内容。
"""
import datetime
import re
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent.parent  # project root (DeverAI/)
ERR_LOG = APP_DIR / "Err.log"


def log_error(context: str, exc: BaseException = None) -> None:
    """记录一条错误。context 为发生场景描述，exc 为异常对象。"""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"[{ts}] {context}"]
    if exc is not None:
        try:
            lines.append(
                "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
            )
        except Exception:
            lines.append(f"<无法格式化异常: {exc}>")
    try:
        # v8.13：Err.log 大小轮转（保留一份 .old），防止无人清空时无限增长
        try:
            if ERR_LOG.exists() and ERR_LOG.stat().st_size > 5 * 1024 * 1024:
                old = ERR_LOG.with_suffix(".log.old")
                old.unlink(missing_ok=True)
                ERR_LOG.replace(old)
        except OSError:
            pass
        with open(ERR_LOG, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass  # 日志本身失败不再抛出，避免二次错误


def read_errors() -> str:
    # v8.13：只读取末尾最多 1MB（Err.log 长时间未清可能很大，整读+整脱敏会打爆内存）
    _MAX_READ = 1024 * 1024
    try:
        size = ERR_LOG.stat().st_size
        with open(ERR_LOG, "r", encoding="utf-8", errors="replace") as f:
            if size > _MAX_READ:
                f.seek(size - _MAX_READ)
                text = "…(日志过大，仅显示末尾 1MB)\n" + f.read(_MAX_READ)
            else:
                text = f.read()
    except FileNotFoundError:
        return ""
    return _redact_paths(text)


def _redact_paths(text: str) -> str:
    """脱敏本机绝对路径，避免 Err.log 回显给登录用户时泄露路径。

    本地 Err.log 文件本身保持完整，脱敏只作用于对外返回的 read_errors()。
    """
    try:
        home = str(Path.home())
        app = str(APP_DIR)
    except Exception:
        return text
    for path, label in ((app, "<APP>"), (home, "~")):
        if not path:
            continue
        text = text.replace(path, label)
        # Windows 反斜杠路径（traceback 常见）统一替换
        text = text.replace(path.replace("\\", "/"), label)
    # P2-8：其余任意盘符绝对路径（如 D:\Projects\...、第三方库路径）整体脱敏
    text = re.sub(r"\b[A-Za-z]:[\\/][^\"'\s,;<>()]+", "<PATH>", text)
    return text


def clear_errors() -> None:
    try:
        ERR_LOG.write_text("", encoding="utf-8")
    except Exception:
        pass
