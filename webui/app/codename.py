"""相对路径安全：大代号（codename）映射机制（v6.2）。

设计目标（Design.md 决策 45）：
- AI 视角下工作区只显示大代号（如 "WORKSPACE-7K3M"），不暴露绝对路径
- AI 写文件/命令只能用相对路径，绝对路径由系统层（bridge.py / desktop/tools.py）翻译
- 代号映射存内存不落盘（每次服务启动重新生成，安全）

约束：
- AI 只见 codename + 相对路径，杜绝通过路径泄露用户名/目录结构/系统盘符
- 服务端在边界处统一脱敏：response 永不返回 bridge_workspace 绝对路径
- 客户端 AI 的 system_prompt 注入"只能写相对路径"约束
"""
from __future__ import annotations
import os
import secrets
import string

# 内存中的工作区代号（服务启动时生成，不落盘）
_WORKSPACE_CODENAME: str = ""

# 文件级代号映射（路径相对工作区 -> 代号）
# 仅在 AI 主动查询时生成，按需扩展；服务重启后失效
_FILE_CODENAMES: dict[str, str] = {}
_FILE_CODENAMES_REV: dict[str, str] = {}

_CODENAME_ALPHABET = string.ascii_uppercase + string.digits  # 不含易混 0/O 的话需精简，这里保留以保持简单


def _rand_suffix(n: int = 4) -> str:
    """生成 n 位大写字母+数字后缀。"""
    return "".join(secrets.choice(_CODENAME_ALPHABET) for _ in range(n))


def workspace_codename() -> str:
    """获取当前工作区的代号（首次调用时生成，整个服务生命周期不变）。"""
    global _WORKSPACE_CODENAME
    if not _WORKSPACE_CODENAME:
        _WORKSPACE_CODENAME = "WORKSPACE-" + _rand_suffix(4)
    return _WORKSPACE_CODENAME


def codename_for(rel_path: str) -> str:
    """为相对路径生成稳定代号（同一 rel_path 同一会话返回相同代号）。

    仅在需要时调用（如 AI 主动询问路径代号）。AI 写文件仍用相对路径，不必先查代号。
    """
    rel = str(rel_path or "").strip().replace("\\", "/")
    if not rel:
        return workspace_codename()
    if rel in _FILE_CODENAMES:
        return _FILE_CODENAMES[rel]
    # v8.13：映射上限（淘汰最早插入项，防多文件长期运行无限增长）
    if len(_FILE_CODENAMES) >= 5000:
        try:
            _oldest = next(iter(_FILE_CODENAMES))
            _old_cn = _FILE_CODENAMES.pop(_oldest)
            _FILE_CODENAMES_REV.pop(_old_cn, None)
        except StopIteration:
            pass
    # 用文件名 + 短后缀，AI 友好且稳定
    base = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0][:8].upper() or "FILE"
    # 去除非字母数字
    base = "".join(c for c in base if c.isalnum())
    if not base:
        base = "FILE"
    cn = f"{base}-{_rand_suffix(3)}"
    # 防极小概率撞
    while cn in _FILE_CODENAMES_REV:
        cn = f"{base}-{_rand_suffix(3)}"
    _FILE_CODENAMES[rel] = cn
    _FILE_CODENAMES_REV[cn] = rel
    return cn


def resolve_codename(cn: str) -> str | None:
    """代号反查相对路径；不存在返回 None。

    用于 AI 偶尔用代号引用文件时（极少见，主要路径形态仍是相对路径）。
    """
    return _FILE_CODENAMES_REV.get(cn)


def mask_absolute(abs_path: str) -> str:
    """把绝对路径脱敏为代号（用于日志/响应）。

    v6.2 P2-4：仅当 abs_path 在当前工作区内时才脱敏为代号；
    工作区外的路径原样返回（避免掩盖越界访问的痕迹）。
    """
    if not abs_path:
        return ""
    from .config import get_config
    ws = (get_config().bridge_workspace or "").strip()
    if ws and str(abs_path).startswith(ws):
        return workspace_codename()
    return str(abs_path)


def reset() -> None:
    """重置所有代号（仅测试用）。"""
    global _WORKSPACE_CODENAME
    _WORKSPACE_CODENAME = ""
    _FILE_CODENAMES.clear()
    _FILE_CODENAMES_REV.clear()


# AI 系统提示词约束（agent.js / desktop/tools.py 注入）
SYSTEM_PROMPT_CONSTRAINT = """【路径安全约束】
- 你只能看到工作区的大代号（如 {WS_TAG}），绝对路径由系统层翻译，你不可见。
- 所有文件/命令操作只能写相对路径（如 "desktop/gui.py"、"tests/test_smoke.py"），禁止尝试使用绝对路径。
- 系统返回的路径信息都是相对路径或代号，不要尝试反推绝对路径或用户名。
- list_dir 返回的 name 是文件名（相对当前目录），read_file/write_file 的 path 是相对工作区的相对路径。
- 这样设计是为了减少隐私与安全风险（用户名、目录结构不暴露给 AI）。
""".strip()
