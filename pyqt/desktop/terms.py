"""v8.18 桌面端持久命令行环境池（cwd+env 轻量会话）。

环境 = 记住工作目录与环境变量的轻量上下文；每条命令继承后新起进程（Windows 友好）。
持久化 data/term_envs.json（原子写），跨重启可用。
与 webui/app/bridge.py 同名文件结构对齐，但独立实现（不共享代码）。
"""

import datetime
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR

TERM_ENVS_PATH: Path = DATA_DIR / "term_envs.json"
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_TERM_ENVS = 12
_MAX_ENV_VARS = 20

_lock = threading.Lock()


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load() -> dict:
    try:
        if not TERM_ENVS_PATH.exists():
            return {"envs": []}
        with open(TERM_ENVS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("envs"), list):
            return {"envs": []}
        return {"envs": [e for e in data["envs"] if isinstance(e, dict) and e.get("id")]}
    except Exception:
        return {"envs": []}


def _save(data: dict) -> None:
    """原子写（先写临时文件再替换），防崩溃半写。"""
    tmp = TERM_ENVS_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, TERM_ENVS_PATH)


def list_envs() -> list:
    """返回全部环境列表（按创建时间升序）。"""
    with _lock:
        return [dict(e) for e in _load()["envs"]]


def get_env(ref: str) -> Optional[dict]:
    """按 id 或 name 解析环境。返回副本或 None。"""
    ref = (ref or "").strip()
    if not ref:
        return None
    with _lock:
        for e in _load()["envs"]:
            if e.get("id") == ref or e.get("name") == ref:
                return dict(e)
    return None


def create_env(name: str = "", cwd_rel: str = "", env_vars: Optional[dict] = None) -> dict:
    """创建一个持久命令行环境。返回环境 dict。"""
    name = (name or "").strip()[:40]
    cwd_rel = (cwd_rel or "").strip().replace("\\", "/")
    clean_vars: dict[str, str] = {}
    if env_vars:
        if len(env_vars) > _MAX_ENV_VARS:
            raise ValueError(f"env_vars 最多 {_MAX_ENV_VARS} 项")
        for k, v in env_vars.items():
            ks = str(k).strip()
            vs = str(v if v is not None else "")
            if not _ENV_KEY_RE.match(ks):
                raise ValueError(f"环境变量名不合法: {ks[:40]}")
            clean_vars[ks] = vs[:2000]
    with _lock:
        data = _load()
        envs = data["envs"]
        if len(envs) >= _MAX_TERM_ENVS:
            raise ValueError(f"环境数量已达上限（{_MAX_TERM_ENVS}），请先删除不用的环境")
        if name and any(e.get("name") == name for e in envs):
            raise ValueError("同名环境已存在")
        import secrets
        eid = "te_" + secrets.token_hex(5)
        env = {
            "id": eid,
            "name": name or f"env-{len(envs) + 1}",
            "cwd_rel": cwd_rel,
            "env_vars": clean_vars,
            "created_at": _now(),
            "last_used": "",
            "last_cmd": "",
            "last_rc": None,
        }
        envs.append(env)
        _save(data)
    return dict(env)


def delete_env(eid: str) -> bool:
    """删除一个环境。返回是否命中。"""
    eid = (eid or "").strip()
    if not eid:
        return False
    with _lock:
        data = _load()
        envs = data["envs"]
        new_envs = [e for e in envs if e.get("id") != eid]
        if len(new_envs) == len(envs):
            return False
        data["envs"] = new_envs
        _save(data)
    return True


def touch_env(env: dict, cmd: str = "", rc: Optional[int] = None) -> None:
    """更新 last_used/last_cmd/last_rc。"""
    eid = (env or {}).get("id")
    if not eid:
        return
    with _lock:
        data = _load()
        for e in data["envs"]:
            if e.get("id") == eid:
                e["last_used"] = _now()
                if cmd:
                    e["last_cmd"] = cmd[:200]
                if rc is not None:
                    e["last_rc"] = rc
                _save(data)
                break


def resolve_env_cwd_env(env: dict) -> tuple[str, dict]:
    """返回 (cwd_abs_or_empty, env_vars_dict)。cwd 为空串表示用工作区根。"""
    cwd_rel = (env or {}).get("cwd_rel") or ""
    cwd_abs = ""
    if cwd_rel:
        try:
            p = (DATA_DIR.parent / cwd_rel).resolve()
            if p.is_dir():
                cwd_abs = str(p)
        except Exception:
            pass
    return cwd_abs, dict((env or {}).get("env_vars") or {})
