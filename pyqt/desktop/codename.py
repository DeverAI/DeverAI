"""DeverAI 桌面版相对路径安全：大代号（codename）机制（v6.2）。

镜像 app/codename.py 的设计：
- AI 视角下工作区只显示大代号（如 "WORKSPACE-7K3M"），不暴露绝对路径
- AI 写文件/命令只能用相对路径，绝对路径由系统层（tools.py）翻译
- 代号映射存内存不落盘

与 app/codename.py 独立（桌面版与网页版代码独立，但语义一致）。
"""
from __future__ import annotations
import secrets
import string

_WORKSPACE_CODENAME: str = ""
_FILE_CODENAMES: dict[str, str] = {}
_FILE_CODENAMES_REV: dict[str, str] = {}

_ALPHABET = string.ascii_uppercase + string.digits


def _rand_suffix(n: int = 4) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def workspace_codename() -> str:
    global _WORKSPACE_CODENAME
    if not _WORKSPACE_CODENAME:
        _WORKSPACE_CODENAME = "WORKSPACE-" + _rand_suffix(4)
    return _WORKSPACE_CODENAME


def codename_for(rel_path: str) -> str:
    rel = str(rel_path or "").strip().replace("\\", "/")
    if not rel:
        return workspace_codename()
    if rel in _FILE_CODENAMES:
        return _FILE_CODENAMES[rel]
    # 映射上限：与 app/codename.py 一致，超限淘汰最早插入项（内存有界）
    if len(_FILE_CODENAMES) >= 5000:
        try:
            oldest = next(iter(_FILE_CODENAMES))
            _FILE_CODENAMES_REV.pop(_FILE_CODENAMES.pop(oldest), None)
        except StopIteration:
            pass
    base = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0][:8].upper() or "FILE"
    base = "".join(c for c in base if c.isalnum())
    if not base:
        base = "FILE"
    cn = f"{base}-{_rand_suffix(3)}"
    while cn in _FILE_CODENAMES_REV:
        cn = f"{base}-{_rand_suffix(3)}"
    _FILE_CODENAMES[rel] = cn
    _FILE_CODENAMES_REV[cn] = rel
    return cn


def resolve_codename(cn: str) -> str | None:
    return _FILE_CODENAMES_REV.get(cn)


def reset() -> None:
    """重置所有代号（仅测试用）。"""
    global _WORKSPACE_CODENAME
    _WORKSPACE_CODENAME = ""
    _FILE_CODENAMES.clear()
    _FILE_CODENAMES_REV.clear()
