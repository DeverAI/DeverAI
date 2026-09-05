"""v8.27 轮前备份：把本轮将修改的文件拷贝到 backups/20260905_v827_pre/（保留相对结构）。
仅做拷贝，不修改/删除任何文件。运行：python _backup_v827.py
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BAK = ROOT / "backups" / "20260905_v827_pre"
FILES = [
    "pyqt/desktop/sync.py",
    "pyqt/desktop/config.py",
    "pyqt/desktop/settings_dialog.py",
    "pyqt/desktop/agent.py",
    "pyqt/desktop/tools.py",
    "pyqt/desktop/gui.py",
    "pyqt/cli_main.py",
    "sync_server.py",
    "tests/test_desktop_offscreen.py",
    "tests/test_smoke_servers.py",
]

def main() -> int:
    n = 0
    for rel in FILES:
        src = ROOT / rel
        if not src.is_file():
            print(f"[SKIP] missing: {rel}")
            continue
        dst = BAK / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    print(f"[OK] backed up {n} files to backups/20260905_v827_pre/")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
