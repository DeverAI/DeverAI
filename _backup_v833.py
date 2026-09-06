"""v8.33 轮后备份：本轮改动文件拷贝到 backups/20260905_v833_post/。运行：python _backup_v833.py"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BAK = ROOT / "backups" / "20260905_v833_post"
FILES = [
    "pyqt/desktop/coordination.py",
    "pyqt/desktop/tools.py",
    "pyqt/desktop/agent.py",
    "pyqt/desktop/config.py",
    "pyqt/desktop/settings_dialog.py",
    "pyqt/desktop/ide_extras.py",
    "pyqt/desktop/gui.py",
    "webui/app/bridge.py",
    "webui/static/js/tools.js",
    "webui/static/index.html",
    "tests/test_desktop_offscreen.py",
    "dev_log/20260905_v833.md",
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
    print(f"[OK] backed up {n} files to backups/20260905_v833_post/")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
