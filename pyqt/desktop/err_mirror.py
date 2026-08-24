"""防呆数据库（3.3 报错本地镜像）：记录自研工具/代码报错特征，
在后续 Agent 生成代码时注入系统提示，避免写出同样的历史 Bug。"""
import datetime
import threading
from pathlib import Path

from .config import DATA_DIR
from .storage import load_json, save_json

ERR_MIRROR_PATH = DATA_DIR / "err_mirror.json"
MAX_ENTRIES = 100
_LOCK = threading.Lock()


def record_error(kind: str, tool: str, args: str, message: str) -> None:
    """记录一次错误特征：错误类型 + 触发参数组合。"""
    if not kind:
        return
    with _LOCK:
        db = load_json(ERR_MIRROR_PATH, [])
        if not isinstance(db, list):
            db = []
        db.append(
            {
                "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "kind": kind,
                "tool": tool,
                "args": str(args)[:500],
                "message": str(message)[:300],
            }
        )
        save_json(ERR_MIRROR_PATH, db[-MAX_ENTRIES:])


def get_hints(limit: int = 15) -> str:
    """返回最近的错误特征提示文本，供系统提示词注入。"""
    db = load_json(ERR_MIRROR_PATH, [])
    if not db:
        return ""
    lines = []
    for e in db[-limit:]:
        lines.append(
            f"- {e.get('kind')} (工具: {e.get('tool')}, 参数: {e.get('args')[:120]}) -> {e.get('message')[:120]}"
        )
    return "本机历史报错镜像（请避免再次触发）：\n" + "\n".join(lines)


def clear() -> None:
    save_json(ERR_MIRROR_PATH, [])


def list_all() -> list:
    return load_json(ERR_MIRROR_PATH, [])
