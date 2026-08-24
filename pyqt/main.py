"""DeverAI 启动入口：PyQt5 桌面应用（UI 即 Agent 运行地）。

一切本地直连：subprocess 执行命令行、Path 直接读写文件、httpx 直连 LLM。
运行：python main.py
"""
from desktop.gui import main

if __name__ == "__main__":
    main()
