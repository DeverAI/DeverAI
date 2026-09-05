"""统一 API Key 读取模块（v8.16）。

优先级：
1. 环境变量（DEVERAI_API_KEY / DEVERAI_SEARCH_KEY / DEVERAI_DRIFT_KEY / DEVERAI_DASHSCOPE_KEY）
2. 项目根目录下 api.txt 文件（每行格式：API_NAME=VALUE）
3. fallback 值（默认空串）

设计原则：
- api.txt 便于用户手动填写，不落盘敏感字段到 config.json
- 环境变量优先级更高（CI/CD / 部署场景）
- 读取结果不缓存，每次调用的最新值
"""

import os
from pathlib import Path

# api.txt 路径（项目根目录，与本文件同目录）
_API_TXT_PATH = Path(__file__).resolve().parent / "api.txt"

# 环境变量名映射
_ENV_NAMES = {
    "deverai_api_key": "DEVERAI_API_KEY",
    "deverai_search": "DEVERAI_SEARCH_KEY",
    "deverai_drift": "DEVERAI_DRIFT_KEY",
    "deverai_dashscope": "DEVERAI_DASHSCOPE_KEY",
}


def _load_api_txt() -> dict:
    """从 api.txt 加载 key-value 对。

    文件格式：每行 API_NAME=VALUE，# 开头为注释，空行跳过。
    示例：
        # DeverAI API Keys
        deverai_api_key=sk-xxxxxxxx
        deverai_search=tvly-xxxxxxxx
    """
    result = {}
    try:
        if _API_TXT_PATH.exists():
            for line in _API_TXT_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    result[k.strip()] = v.strip()
    except Exception:
        pass
    return result


def get_api_key(api_name: str, fallback: str = "") -> str:
    """获取指定名称的 API Key。

    参数：
        api_name: api.txt 中的 key 名（如 "deverai_api_key"）
        fallback: 找不到时返回的默认值

    返回：
        API Key 值，或 fallback。
    """
    # 1. 优先环境变量
    env_name = _ENV_NAMES.get(api_name)
    if env_name:
        val = os.environ.get(env_name, "")
        if val:
            return val

    # 2. 其次 api.txt
    api_txt = _load_api_txt()
    val = api_txt.get(api_name, "")
    if val:
        return val

    # 3. fallback
    return fallback


def list_api_keys() -> dict:
    """列出所有已配置的 API Key（值打码）。用于诊断。"""
    api_txt = _load_api_txt()
    result = {}
    for name in _ENV_NAMES.keys():
        val = get_api_key(name)
        if val:
            # 打码显示：前4后4
            if len(val) > 8:
                result[name] = val[:4] + "*" * (len(val) - 8) + val[-4:]
            else:
                result[name] = "***"
        else:
            result[name] = "(未配置)"
    return result
