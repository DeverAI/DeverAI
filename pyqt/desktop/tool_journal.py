"""外部软件探索记录（v8.9）：把 AI 通过 exe_* 工具对选定软件的操作写入
data/ui_automation_journal.jsonl，供事后回看「AI 探索了什么、点了哪里、输入了什么」。

与 Err.log 分离：Err.log 只记错误，本 journal 记录 UI 自动化动作。
开关 ENABLE_EXE_JOURNAL=False 时停止追加（不删除历史）。
"""
import datetime
import json
from pathlib import Path

from .config import DATA_DIR, get_config

JOURNAL_PATH: Path = DATA_DIR / "ui_automation_journal.jsonl"
_MAX_DETAIL = 2000


def _enabled() -> bool:
    try:
        return bool(getattr(get_config(), "ENABLE_EXE_JOURNAL", True))
    except Exception:
        return True


def record(tool: str, args: dict | None = None, result: dict | None = None) -> dict | None:
    """追加一条操作记录。写失败落 Err.log，不阻塞主流程。"""
    if not _enabled():
        return None
    args_safe = {}
    for k, v in (args or {}).items():
        s = str(v)
        if k in ("text",) and len(s) > 200:
            s = s[:200] + "…"
        args_safe[k] = s[:_MAX_DETAIL]
    entry = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tool": str(tool or "")[:80],
        "args": args_safe,
        "ok": bool((result or {}).get("ok", False)),
        "output": str((result or {}).get("output", ""))[:_MAX_DETAIL],
    }
    try:
        JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        # O_APPEND 直接追加：并发下不互相覆盖（固定 tmp + os.replace 会丢记录/失败）
        with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
    except Exception as e:
        try:
            from .errors import log_error
            log_error("写入 UI 自动化操作记录失败", e)
        except Exception:
            pass
        return None


def read_journal(tail: int = 50, tool: str = "") -> list:
    """读取最近 tail 条操作记录（倒序，最新在前）。"""
    if not JOURNAL_PATH.exists():
        return []
    out = []
    try:
        with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if tool and e.get("tool") != tool:
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
