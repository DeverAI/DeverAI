"""DeverAI 网页版启动入口（v6.2 双入口并存）。

启动 FastAPI + uvicorn，并自动打开浏览器。
与 main.py（桌面版 PyQt5）互不干扰，二者功能对等但代码独立。

运行：
    python web_main.py                # 默认 127.0.0.1:8765
    python web_main.py --host 0.0.0.0 --port 8765 --no-browser

设计边界（见 Design.md 决策 43）：
- app/ 严格保持"三件事"——下发 UI（static/）、验证身份（HMAC Cookie）、无状态转发（LLM 代理 + 本地资源桥）。
- Agent 决策、工具编排、规划、压缩、资产、模式全部在浏览器端 JS 执行。
- API Key 只存在于浏览器 localStorage，服务端不落盘。
"""
from __future__ import annotations
import argparse
import os
import sys
import threading
import time
import webbrowser


def _open_browser(url: str, delay: float = 1.5) -> None:
    """延迟打开浏览器，等 uvicorn 起来后再访问。"""
    def _go():
        time.sleep(delay)
        try:
            webbrowser.open(url, new=1, autoraise=True)
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="DeverAI 网页版启动入口")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    parser.add_argument("--reload", action="store_true", help="开发模式（热重载）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    # v8.13：端口范围校验（uvicorn 对 0/越界端口报错晦涩）
    if not 1 <= args.port <= 65535:
        print("[web_main] 端口必须在 1-65535 之间", file=sys.stderr)
        sys.exit(1)

    # 提前导入，便于在 uvicorn 启动前暴露 import 错误
    try:
        from app.server import app  # noqa: F401
    except Exception as e:
        print(f"[web_main] 加载 app.server 失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 内存注入实际监听端口（不落盘）：供 SSRF 自环防护比对"本服务自身端口"
    # v8.14：同时写环境变量，uvicorn --reload 子进程重新导入 config 时可回退读取
    try:
        from app.config import init_config, set_runtime_port
        init_config()
        set_runtime_port(args.port)
        os.environ["DEVERAI_RUNTIME_PORT"] = str(args.port)
    except Exception as e:
        print(f"[web_main] 配置初始化失败: {e}", file=sys.stderr)
        sys.exit(1)

    host_display = f"[{args.host}]" if ":" in args.host and not args.host.startswith("[") else args.host
    url = f"http://{host_display}:{args.port}/"
    # 安全提醒：0.0.0.0 暴露 = 局域网内任何人可注册并操作本机文件/命令（本地资源桥语义）
    if args.host in ("0.0.0.0", "::"):
        print("[web_main] 警告：监听 0.0.0.0 会把本地资源桥（文件/命令/LLM 代理）暴露给局域网；"
              "仅限可信网络，建议保持 127.0.0.1 或置于鉴权反向代理之后。", file=sys.stderr)
    if not args.no_browser:
        _open_browser(url)

    try:
        import uvicorn
    except ImportError:
        print("[web_main] 缺少 uvicorn，请先安装：pip install uvicorn fastapi", file=sys.stderr)
        sys.exit(1)

    print(f"[web_main] DeverAI 网页版启动：{url}")
    uvicorn.run(
        "app.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
