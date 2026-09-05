"""v8.29 轮后备份：本轮改动文件拷贝到 backups/20260905_v829_post/。运行：python _backup_v829_post.py"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BAK = ROOT / "backups" / "20260905_v829_post"
FILES = [
    "pyqt/cli_main.py",
    "pyqt/desktop/agent.py",
    "pyqt/desktop/gui.py",
    "webui/app/bridge.py",
    "webui/static/js/chat.js",
    "webui/static/index.html",
    "tests/test_smoke_servers.py",
    "tests/test_desktop_offscreen.py",
    "Future.md",
    "FreqErr.md",
    "dev_log/20250905_v829.md",
    "updates/20250905_v829.md",
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
    print(f"[OK] backed up {n} files to {BAK.name}/")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
