"""v8.37 精确复刻 test_changes_bar 构造（临时脚本）"""
import asyncio
import json as _json
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
    cfg.ENABLE_UNATTENDED = True
    events = []

    async def _cap(ev):
        events.append(ev)

    ag = Agent(cfg, emit=_cap)

    async def _go():
        await ag._execute_tools([{
            "id": "c1", "name": "write_file",
            "arguments": _json.dumps({"path": "a.txt", "content": "hi"}),
        }], [])

    asyncio.run(_go())
    print("after _go: changes =", ag._changes)
    events.clear()

    async def _fake_inner(*a, **k):
        ag._changes.append({"path": "cancel.txt", "action": "write_file"})
        print("INNER raising, changes =", ag._changes)
        raise asyncio.CancelledError()

    ag._run_inner = _fake_inner
    try:
        asyncio.run(ag.run("触发取消"))
    except asyncio.CancelledError:
        print("RUN: CancelledError propagated")
    except BaseException as e:
        print("RUN: other:", type(e).__name__, e)
    types = [e.get("type") for e in events]
    print("event types:", types)
    fc = [e for e in events if e.get("type") == "file_changes"]
    print("file_changes events:", fc)
