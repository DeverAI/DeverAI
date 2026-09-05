"""v8.30 轮后备份：本轮改动文件拷贝到 backups/20260905_v830_post/。运行：python _backup_v830_post.py"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BAK = ROOT / "backups" / "20260905_v830_post"
FILES = [
    "pyqt/desktop/uploads_ingest.py",
    "pyqt/desktop/checkpoint.py",
    "pyqt/desktop/gui.py",
    "tests/test_desktop_offscreen.py",
    "Future.md",
    "Fact.md",
    "dev_log/20260905_v830.md",
    "updates/20260905_v830.md",
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
