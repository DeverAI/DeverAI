"""v8.31 审计轮轮前备份。运行：python _backup_v831_pre.py"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BAK = ROOT / "backups" / "20260905_v831_pre"
FILES = [
    "pyqt/desktop/uploads_ingest.py",
    "pyqt/desktop/checkpoint.py",
    "pyqt/desktop/gui.py",
    "tests/test_desktop_offscreen.py",
]

def main() -> int:
    n = 0
    for rel in FILES:
        src = ROOT / rel
        if not src.is_file():
            print("[SKIP] missing: " + rel)
            continue
        dst = BAK / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    print("[OK] backed up " + str(n) + " files")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
