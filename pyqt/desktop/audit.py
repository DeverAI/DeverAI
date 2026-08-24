"""回退审核机制（v8.9）：结构化审计日志 data/audit.jsonl。

职责：记录一切「动作型」敏感操作——文件回退/恢复、目录删除与还原、快照清理、
算力漂移回本地/复位、资产修复等。与 Err.log 分离：Err.log 只记错误，
audit.jsonl 记录人为/系统回退动作，供事后审核与前端展示。

- 开关 ENABLE_AUDIT_LOG=False 时停止追加（不删除历史）。
- 原子追加：先写 .tmp 再 os.replace；单条 detail 截断防膨胀。
- 读取：list_audits(tail) 返回最近 N 条（倒序）。
"""
import datetime
import json
import os
from pathlib import Path

from .config import DATA_DIR, get_config

AUDIT_PATH: Path = DATA_DIR / "audit.jsonl"
_MAX_DETAIL = 2000


def _enabled() -> bool:
    try:
        return bool(getattr(get_config(), "ENABLE_AUDIT_LOG", True))
    except Exception:
        return True


def audit_log(action: str, target: str = "", detail: str = "", actor: str = "system") -> dict | None:
    """追加一条审计记录。返回条目 dict；开关关闭或写失败返回 None（写失败落 Err.log）。"""
    if not _enabled():
        return None
    entry = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "actor": str(actor or "system")[:80],
        "action": str(action or "")[:80],
        "target": str(target or "")[:300],
        "detail": str(detail or "")[:_MAX_DETAIL],
    }
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        # 直接 O_APPEND 追加：单行 write 在 append 模式下不会破坏其它并发写入者的记录；
        # 固定 tmp + os.replace 在并发下会互相覆盖/失败（见 FreqErr）。
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
    except Exception as e:
        try:
            from .errors import log_error
            log_error("写入审计日志失败", e)
        except Exception:
            pass
        return None


def list_audits(tail: int = 50, action: str = "") -> list:
    """读取最近 tail 条审计记录（默认倒序，最新在前）。action 非空时按动作过滤。"""
    if not AUDIT_PATH.exists():
        return []
    out = []
    try:
        with open(AUDIT_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if action and e.get("action") != action:
                    continue
                out.append(e)
    except Exception:
        pass
    try:
        tail = max(0, min(int(tail), 500))
    except (TypeError, ValueError):
        tail = 50
    out.reverse()
    return out[:tail]
