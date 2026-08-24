"""算力漂移本地状态机（v8.6）。

用户在退出时选择「算力漂移退出」，系统在此留下一个持久标记：
    { project_id, active, target_server, drifted_at }
下一次打开时据此自动查询服务器是否已跑完（未跑完则锁定项目、允许把算力漂移回本地）。

数据文件：data/drift_state.json（原子写，绝不进工作区，防 AI rm -rf 摧毁）。
"""
import datetime
import hashlib
import json
from pathlib import Path

from .config import DATA_DIR, get_config
from .errors import log_error
from .storage import save_json

DRIFT_STATE_PATH: Path = DATA_DIR / "drift_state.json"


def project_id() -> str:
    """稳定的项目编号：基于工作区绝对路径的 sha256 前 12 位（跨重启一致）。"""
    ws = str(get_config().workspace or "").strip()
    if not ws:
        return ""
    return hashlib.sha256(ws.encode("utf-8")).hexdigest()[:12]


def read_drift_state() -> dict:
    try:
        if DRIFT_STATE_PATH.exists():
            return json.loads(DRIFT_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def write_drift_state(state: dict) -> None:
    try:
        DRIFT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # storage.save_json 唯一临时名：GUI 与漂移线程并发写不互踩
        save_json(DRIFT_STATE_PATH, state)
    except Exception as e:
        # 自动存错机制：状态写失败不静默吞掉，落 Err.log 便于排查
        log_error("写入漂移状态失败", e)


def begin_drift() -> dict:
    """记录「正在算力漂移」：项目编号 + 目标服务器 + 时间。"""
    state = {
        "project_id": project_id(),
        "active": True,
        "target_server": get_config().sync_server_url or "",
        "drifted_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_drift_state(state)
    return state


def end_drift() -> dict:
    """清除「正在算力漂移」标记（漂移回本地后）。"""
    state = read_drift_state()
    state["active"] = False
    state["ended_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_drift_state(state)
    return state


def is_drifting() -> bool:
    return bool(read_drift_state().get("active"))
