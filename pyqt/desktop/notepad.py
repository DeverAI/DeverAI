"""v6.3 Notepad 暂存：Agent 跨轮暂存中间结果（对标 Cursor notepad）。

存 data/notepad.json，单文件多 key。Agent 可用 notepad_save/notepad_read/notepad_list/notepad_clear。
典型场景：多步重构进度、待回填的 TODO、对比基线、跨子Agent 传递大上下文。
"""
from __future__ import annotations
import threading
from pathlib import Path

from .config import DATA_DIR
from .storage import load_json, save_json

NOTEPAD_PATH: Path = DATA_DIR / "notepad.json"
_LOCK = threading.Lock()
_MAX_ENTRIES = 100          # 最多 100 个 key（防无上限增长）
_MAX_VALUE_LEN = 20000      # 单值上限 20k 字符（防撑爆）


def _load() -> dict:
    data = load_json(NOTEPAD_PATH, {})
    return data if isinstance(data, dict) else {}


def _save(d: dict) -> None:
    save_json(NOTEPAD_PATH, d)


def save(key: str, content: str) -> bool:
    """暂存一条。key 非空，content 截断到 _MAX_VALUE_LEN。超额自动淘汰最旧。"""
    key = str(key or "").strip()
    if not key:
        return False
    content = str(content or "")[:_MAX_VALUE_LEN]
    with _LOCK:
        d = _load()
        # 已存在则更新（不动顺序）；新键超额淘汰最旧（按插入序，dict 保序）
        if key not in d and len(d) >= _MAX_ENTRIES:
            # 删最早的 key（Python 3.7+ dict 保插入序）
            first_key = next(iter(d))
            del d[first_key]
        d[key] = {"content": content, "ts": _now()}
        _save(d)
    return True


def read(key: str) -> str:
    """读取一条；不存在返回空串。"""
    key = str(key or "").strip()
    if not key:
        return ""
    with _LOCK:
        d = _load()
    entry = d.get(key)
    if isinstance(entry, dict):
        return str(entry.get("content", ""))
    return ""


def list_keys() -> list:
    """列出所有 key（带 ts 与内容前 60 字预览）。"""
    with _LOCK:
        d = _load()
    out = []
    for k, v in d.items():
        c = str(v.get("content", "")) if isinstance(v, dict) else ""
        out.append({"key": k, "preview": c[:60], "ts": v.get("ts", "") if isinstance(v, dict) else ""})
    return out


def clear(key: str = "") -> int:
    """清除一条（key 非空）或全部（key 空）。返回清除条数。"""
    with _LOCK:
        d = _load()
        if key:
            if key in d:
                del d[key]
                _save(d)
                return 1
            return 0
        n = len(d)
        d.clear()
        _save(d)
        return n


def _now() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
