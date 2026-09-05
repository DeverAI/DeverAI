"""v8.29 检修轮轮前备份：本轮将修改的文件拷贝到 backups/20260905_v829_pre/（保留相对结构）。
仅做拷贝，不修改/删除任何文件。运行：python _backup_v829_pre.py"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BAK = ROOT / "backups" / "20260905_v829_pre"
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
    print(f"[OK] 已备份 {n} 个文件到 {BAK.name}/")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
