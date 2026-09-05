# -*- coding: utf-8 -*-
"""DeverAI 快速回归入口：一键顺序运行两套测试（桌面离屏 + 双服务器冒烟）。

历史说明：本文件诞生于 2026-08-27 轮的审计探针脚本（_audit_tmp.py），因工作流
「不使用命令行删除项目文件」纪律保留在根目录，现已转正为测试快捷入口。
如嫌根目录杂乱，可自行改名（如 run_tests.py）或删除，不影响任何功能。

用法：
    python _audit_tmp.py            # 全部
    python _audit_tmp.py gui        # 仅桌面离屏回归
    python _audit_tmp.py server     # 仅双服务端冒烟
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SUITES = {
    "gui": [sys.executable, str(ROOT / "tests" / "test_desktop_offscreen.py")],
    "server": [sys.executable, str(ROOT / "tests" / "test_smoke_servers.py")],
}


def main() -> int:
    which = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    chosen = list(SUITES.keys()) if which == "all" else [which]
    if not set(chosen) <= set(SUITES):
        print(f"未知套件: {which}（可选 all/{'/'.join(SUITES)}）")
        return 2
    worst = 0
    for key in chosen:
        print(f"\n===== {key} =====")
        r = subprocess.run(SUITES[key], cwd=str(ROOT))
        worst = max(worst, r.returncode)
    print("\n===== 汇总 =====")
    print("[OK] 所选套件全部通过" if worst == 0 else f"[FAIL] 存在失败（最坏退出码 {worst}）")
    return worst


if __name__ == "__main__":
    sys.exit(main())
