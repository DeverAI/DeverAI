"""v8.37 取消轮 wrapper 最小重现（临时脚本，跑完即删）"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\Users\david\Documents\all_projects\DeverAI\pyqt")
from desktop.config import Config
from desktop.agent import Agent

with tempfile.TemporaryDirectory() as td:
    cfg = Config()
    cfg.workspace = td
    cfg.ENABLE_DIFF_PREVIEW = False
    cfg.ENABLE_CHECKPOINT = False
    cfg.ENABLE_SESSION_SNAP = False
    events = []

    async def _cap(ev):
        events.append(ev)
        print("EVENT:", ev.get("type"))

    ag = Agent(cfg, emit=_cap)

    async def _fake_inner(*a, **k):
        ag._changes.append({"path": "cancel.txt", "action": "write_file"})
        print("INNER: raising CancelledError, changes =", ag._changes)
        raise asyncio.CancelledError()

    ag._run_inner = _fake_inner
    try:
        asyncio.run(ag.run("触发取消"))
        print("RUN: returned normally (unexpected)")
    except asyncio.CancelledError:
        print("RUN: CancelledError propagated")
    except BaseException as e:
        print("RUN: other exception:", type(e).__name__, e)
    print("file_changes in events:", any(e.get("type") == "file_changes" for e in events))
    print("all event types:", [e.get("type") for e in events])
