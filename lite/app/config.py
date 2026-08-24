"""服务端配置（Host Server）：仅负责 UI 下发 + 身份验证 + 无状态 LLM 代理 + 本地资源桥。

一切 Agent 逻辑（对话/工具调用/规划/资产/模式）都在浏览器端执行。
"""
import os
import secrets
from dataclasses import dataclass, asdict
from pathlib import Path

from .storage import load_json, save_json

APP_DIR = Path(__file__).resolve().parent.parent.parent  # project root (DeverAI/)
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = DATA_DIR / "server_config.json"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8732
    secret: str = ""                 # 会话签名密钥（首次启动自动生成）
    session_ttl_hours: int = 72
    allow_register: bool = True      # 是否开放注册
    bridge_workspace: str = ""       # 本地命令桥工作区（设置中授权）
    allow_ai_delete: bool = False    # 命令桥是否允许删除文件
    llm_allow_loopback: bool = True  # LLM 代理是否允许环回地址（本地 LLM 如 ollama；生产多租户设 False）
    power_authorized: bool = False   # 电源操作（关机/休眠）是否已授权

    def save(self) -> None:
        save_json(CONFIG_PATH, asdict(self))


_cfg: ServerConfig | None = None


def _strict_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _coerce_field(cfg: ServerConfig, k: str, v) -> None:
    """v8.13：配置加载/更新强制类型与范围，非法值回退默认并落 Err.log。

    手工编辑或旧版本遗留的 "false" 字符串、abc 整数、list 路径等此前会
    造成 SSRF 开关反转 / set_cookie 类型错误 / .strip() AttributeError 500。
    """
    try:
        cur = getattr(cfg, k)
        if isinstance(cur, bool):
            setattr(cfg, k, _strict_bool(v))
        elif isinstance(cur, int):
            ival = int(v)
            if k == "port":
                ival = min(max(ival, 1), 65535)
            elif k == "session_ttl_hours":
                ival = min(max(ival, 1), 8760)
            setattr(cfg, k, ival)
        elif isinstance(cur, str):
            sval = str(v or "")
            if k in ("secret", "bridge_workspace") and len(sval) > 1000:
                sval = ""
            setattr(cfg, k, sval)
        else:
            setattr(cfg, k, v)
    except Exception as e:
        try:
            from .errors import log_error
            log_error(f"[lite] 配置字段 {k} 非法，已回退默认", e)
        except Exception:
            pass


# 实际监听端口（内存注入，不落盘）：由启动入口 set_runtime_port() 设置，
# 供 SSRF 自环防护判断"本服务自身端口"，避免默认端口与启动端口不一致导致防护失效。
# v8.14：启动入口同时写 DEVERAI_RUNTIME_PORT 环境变量——uvicorn --reload 的子进程
# 会重新导入本模块，仅靠进程内存注入会丢失，导致自环防护比对到错误端口。
_RUNTIME_PORT_ENV = "DEVERAI_RUNTIME_PORT"
_runtime_port: int | None = None


def set_runtime_port(port: int) -> None:
    global _runtime_port
    if isinstance(port, int) and port > 0:
        _runtime_port = port
        try:
            os.environ[_RUNTIME_PORT_ENV] = str(port)
        except Exception:
            pass


def reset_runtime_port() -> None:
    """清除内存注入端口（测试隔离用）。"""
    global _runtime_port
    _runtime_port = None


def self_port() -> int:
    global _runtime_port
    if _runtime_port is None:
        try:
            envp = int(os.environ.get(_RUNTIME_PORT_ENV, "") or 0)
            if envp > 0:
                _runtime_port = envp
        except Exception:
            pass
    if _runtime_port is not None:
        return _runtime_port
    return get_config().port


def init_config() -> ServerConfig:
    global _cfg
    data = load_json(CONFIG_PATH, {})
    # v8.13：配置文件必须是对象；合法 JSON 数组/null/字符串一律回退默认并落 Err.log
    if not isinstance(data, dict):
        data = {}
        try:
            from .errors import log_error
            log_error("[lite] server_config.json 顶层类型非法，已回退默认配置", None)
        except Exception:
            pass
    cfg = ServerConfig()
    for k, v in data.items():
        if hasattr(cfg, k):
            _coerce_field(cfg, k, v)
    if not cfg.secret:
        cfg.secret = secrets.token_hex(32)
        cfg.save()
    _cfg = cfg
    return cfg


def get_config() -> ServerConfig:
    if _cfg is None:
        return init_config()
    return _cfg


def update_config(**fields) -> ServerConfig:
    cfg = get_config()
    for k, v in fields.items():
        if hasattr(cfg, k):
            _coerce_field(cfg, k, v)
    cfg.save()
    return cfg
