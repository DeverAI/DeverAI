"""JSON 持久化工具：原子写入（临时文件 + os.replace）防止中途损坏。"""
import json
import os
import uuid
from pathlib import Path
from typing import Any


def _tmp_path(path: Path) -> Path:
    # 唯一临时文件名，避免 AOE 并行子Agent 并发写同一文件时互踩 .tmp
    return path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")


def load_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default if default is not None else {}
    except json.JSONDecodeError:
        return default if default is not None else {}


def save_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def save_text(path: Path, text: str, eol: bool | None = None) -> None:
    """原子写文本。

    v8.14：newline="" 精确写入（不再做 \n→os.linesep 隐式翻译）。
    此前编辑器保存会把 LF 文件整体翻成 CRLF，与 AI 工具的精确写
    （locks.atomic_write 同为 newline=""）语义互相矛盾，造成整文件行尾漂移。
    eol=True 时把文本统一转成 CRLF 后写入（供"保持原文件行尾风格"调用方使用）；
    eol=None 表示按传入文本原样写。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if eol:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    tmp = _tmp_path(path)
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def sniff_crlf(path: Path) -> bool:
    """探测既有文件是否 CRLF 行尾（读前 64KB 判断）。文件不存在返回 False。"""
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
        return b"\r\n" in head
    except OSError:
        return False


def read_text(path: Path, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return default


def load_bytes(path: Path) -> bytes | None:
    """读取二进制文件，不存在返回 None。"""
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def save_bytes(path: Path, data: bytes) -> None:
    """原子写入二进制文件（临时文件 + os.replace）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
