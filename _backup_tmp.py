"""v8.26 轮前备份：把本轮将修改的文件拷贝到 backups/20260905_v826_pre/（保留相对结构）。
仅做拷贝，不修改/删除任何文件。运行：python _backup_tmp.py
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BAK = ROOT / "backups" / "20260905_v826_pre"
FILES = [
    "pyqt/desktop/file_protect.py",
    "pyqt/desktop/tools.py",
    "pyqt/desktop/config.py",
    "pyqt/desktop/settings_dialog.py",
    "pyqt/desktop/agent.py",
    "pyqt/desktop/gui.py",
    "webui/app/bridge.py",
    "webui/app/storage.py",
    "webui/static/js/editor.js",
    "webui/static/js/tools.js",
    "webui/static/js/core.js",
    "webui/static/index.html",
    "tests/test_smoke_servers.py",
    "tests/test_desktop_offscreen.py",
]

def main() -> int:
    n = 0
    for rel in FILES:
        src = ROOT / rel
        if not src.is_file():
            print(f"[SKIP] 不存在: {rel}")
            continue
        dst = BAK / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    print(f"[OK] 已备份 {n} 个文件到 backups/20260905_v826_pre/")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
