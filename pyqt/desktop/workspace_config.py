"""工作区级设置：data/workspaces/{hash}.json，工作区内覆盖全局模型等。

用户拍板（重要安全约束）：
- 工作区设置集中存本机 data/workspaces/（而非工作区根目录 .deverai/）。
- 原因：work tree、重要文档、记忆、工作区设置这些"难以复原"的东西绝不放工作区之内，
  否则 AI 一个 rm -rf 全部灰飞烟灭且无法回退。集中存 data/ 保持工作区干净可重建。

覆盖语义：工作区设置里非空字段覆盖全局对应字段；空/未设置则沿用全局。
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from .config import DATA_DIR
from .storage import load_json, save_json

WS_DIR = DATA_DIR / "workspaces"

# 工作区可覆盖字段白名单：字段名 -> 类型（非该类型忽略，防脏数据）
WS_OVERRIDABLE = {
    "model": str,              # pilot 主模型（空=沿用全局）
    "copilot_model": str,      # 副驾驶模型（空=沿用全局）
    "suggest_models": list,    # 建议生成模型（空列表=沿用全局）
    "agent_mode": str,         # chat | builder | experts（空=沿用全局）
}


def ws_hash(workspace: str) -> str:
    return hashlib.sha1(str(workspace or "").strip().encode("utf-8")).hexdigest()[:16]


def ws_path(workspace: str) -> Path:
    return WS_DIR / f"{ws_hash(workspace)}.json"


def load_ws(workspace: str) -> dict:
    """加载工作区设置（返回原始 dict，字段缺失/非法会被忽略）。"""
    return load_json(ws_path(workspace), {}) or {}


def save_ws(workspace: str, data: dict) -> None:
    """保存工作区设置（仅保留白名单且类型正确的字段）。"""
    clean = {}
    for k, typ in WS_OVERRIDABLE.items():
        if k in data and isinstance(data[k], typ):
            clean[k] = data[k]
    save_json(ws_path(workspace), clean)


def apply_ws_overrides(cfg, workspace: str):
    """返回工作区覆盖后的 cfg 快照（不修改原全局 cfg，避免污染）。"""
    over = load_ws(workspace)
    kwargs = {}
    for k in WS_OVERRIDABLE:
        v = over.get(k)
        if v:  # 非空才覆盖
            kwargs[k] = v
    return replace(cfg, **kwargs) if kwargs else cfg
