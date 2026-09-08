"""v8.37 轮后备份：本轮改动文件拷贝到 backups/20260907_v837_post/。运行：python _backup_v837.py"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BAK = ROOT / "backups" / "20260907_v837_post"
FILES = [
    "pyqt/desktop/agent.py",
    "pyqt/desktop/tools.py",
    "pyqt/desktop/gui.py",
    "pyqt/desktop/settings_dialog.py",
    "pyqt/cli_main.py",
    "tests/test_desktop_offscreen.py",
    "webui/static/js/agent.js",
    "webui/static/js/chat.js",
    "webui/static/js/voice-pet.js",
    "webui/static/index.html",
    "dev_log/20260907_v837.md",
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
    print(f"[OK] backed up {n} files to backups/20260907_v837_post/")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
