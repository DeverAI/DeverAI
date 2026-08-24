"""DeverAI Lite 启动入口 — 超轻量网页远控（极简、低内存、无泄漏）。

与 web_main.py（完整版）并存，互不干扰。
Lite 版只保留核心功能：鉴权 + LLM 代理 + 文件读写 + 命令执行。
无 Monaco/hljs/marked 等 CDN 依赖，前端单文件内联。

运行：
    python lite_main.py                # 默认 127.0.0.1:8733
    python lite_main.py --host 0.0.0.0 --port 8733 --no-browser
"""
from __future__ import annotations
import argparse
import os
import sys
import threading
import time
import webbrowser


def _open_browser(url: str, delay: float = 1.5) -> None:
    def _go():
        time.sleep(delay)
        try:
            webbrowser.open(url, new=1, autoraise=True)
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="DeverAI Lite — 超轻量网页远控")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8733, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    # v8.13：端口范围校验
    if not 1 <= args.port <= 65535:
        print("[lite] 端口必须在 1-65535 之间", file=sys.stderr)
        sys.exit(1)

    try:
        from app.lite_server import app  # noqa: F401
    except Exception as e:
        print(f"[lite] 加载 app.lite_server 失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 内存注入实际监听端口（不落盘）：供 SSRF 自环防护比对"本服务自身端口"
    # v8.14：同时写环境变量，uvicorn --reload 子进程重新导入 config 时可回退读取
    try:
        from app.config import init_config, set_runtime_port
        init_config()
        set_runtime_port(args.port)
        os.environ["DEVERAI_RUNTIME_PORT"] = str(args.port)
    except Exception as e:
        print(f"[lite] 配置初始化失败: {e}", file=sys.stderr)
        sys.exit(1)

    host_display = f"[{args.host}]" if ":" in args.host and not args.host.startswith("[") else args.host
    url = f"http://{host_display}:{args.port}/"
    # v8.13：与 web_main.py 一致的安全提醒——Lite 同样带文件/命令桥
    if args.host in ("0.0.0.0", "::"):
        print("[lite] 警告：监听 0.0.0.0 会把文件/命令桥与 LLM 代理暴露给局域网；"
              "仅限可信网络，建议保持 127.0.0.1。", file=sys.stderr)
    if not args.no_browser:
        _open_browser(url)

    try:
        import uvicorn
    except ImportError:
        print("[lite] 缺少 uvicorn，请先安装：pip install uvicorn fastapi", file=sys.stderr)
        sys.exit(1)

    print(f"[lite] DeverAI Lite 启动：{url}")
    uvicorn.run(
        "app.lite_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="warning",  # 减少日志内存
    )


if __name__ == "__main__":
    main()
