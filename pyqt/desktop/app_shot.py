"""v8.4 Python 应用截图：运行本地 UI 脚本 → 等待窗口出现 → 截图保存 → 关闭进程。

- app_screenshot(script, out_path, timeout, extra_args)：用当前 python 解释器运行
  脚本（cwd=脚本目录），轮询等待顶层窗口出现（Windows: FindWindow/EnumWindows；
  其它平台 fallback 等待固定时间），用 Qt QScreen.grabWindow 截图保存，结束后杀进程。
- find_window_pid(pid, title_hint)：枚举顶层窗口找指定 pid 的第一个可见窗口 hwnd。
- 设计：轻量化零第三方依赖（PyQt5 已装）；适配 PyQt/Tkinter 等任何能创建窗口的脚本。
- 截图质量：先置前窗口（SetForegroundWindow）再 grabWindow，保证内容渲染完整。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QObject, pyqtSlot  # noqa: F401  # _GrabBridge 需要 QObject 基类

# 每次等待窗口的最大轮询时长（秒）
_WAIT_STEP = 0.35
# 截图文件大小上限（字节）：超过提示减小，防止 base64 后打爆多模态 API
MAX_IMG_BYTES = 3 * 1024 * 1024


def find_window_pid(pid: int, title_hint: str = "") -> int:
    """枚举系统顶层窗口，找指定进程的第一个可见窗口。返回 hwnd 或 0。"""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.EnumWindows.argtypes = [wintypes.WNDENUMPROC, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
    except Exception:
        return 0

    found = []

    def _cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            pid_ = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_))
            if pid_.value != pid:
                return True
            if title_hint:
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if title_hint.lower() not in buf.value.lower():
                    return True
            found.append(int(hwnd))
        except Exception:
            pass
        return True  # 继续枚举

    try:
        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        return 0
    return int(found[0]) if found else 0


def _foreground(hwnd: int) -> None:
    """尽量把窗口带到前台，确保内容渲染。"""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow(hwnd)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    except Exception:
        pass


class _GrabBridge(QObject):
    """把 Qt 抓图投递回主线程执行（跨线程访问 QScreen 属未定义行为）。

    QMetaObject.invokeMethod + BlockingQueuedConnection 会在接收线程
    （主线程，通常跑着 Qt 事件循环）执行槽并等待完成，规避 worker 线程
    直接操作 GUI 对象导致的偶发黑屏/崩溃。无 QApplication 时返回 False
    而不是硬造实例（非主线程构造 QApplication 可能 qFatal）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ok = False

    @pyqtSlot("long long", str)
    def _do(self, hwnd: int, out_path: str) -> None:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            self.ok = False
            return
        screen = app.primaryScreen()
        if screen is None:
            self.ok = False
            return
        try:
            pix = screen.grabWindow(int(hwnd))
            self.ok = pix is not None and not pix.isNull() and pix.save(out_path, "PNG")
        except Exception:
            self.ok = False


def _grab(hwnd: int, out_path: str) -> bool:
    """在主线程执行 QScreen.grabWindow 截图（跨线程安全）。"""
    from PyQt5.QtCore import QMetaObject, Q_ARG, Qt
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return False  # 无 Qt 应用（web/lite/服务器场景）：不硬造实例
    _foreground(hwnd)
    time.sleep(0.2)
    # 已处在 Qt 对象所在线程（罕见，如测试在主线程直连）：直接执行
    if threading.current_thread() is app.thread():
        bridge = _GrabBridge()
        bridge._do(hwnd, out_path)
        return bridge.ok
    bridge = _GrabBridge()
    bridge.moveToThread(app.thread())  # 归属主线程，invokeMethod 才会投递到主线程事件循环
    QMetaObject.invokeMethod(
        bridge, "_do",
        Qt.BlockingQueuedConnection,
        Q_ARG("long long", int(hwnd)), Q_ARG("str", str(out_path)))
    return bridge.ok


def _wait_and_shoot(pid: int, out_path: str, timeout: float,
                    title_hint: str = "") -> bool:
    """轮询等待窗口出现并截图。返回是否成功。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = find_window_pid(pid, title_hint)
        if hwnd:
            if _grab(hwnd, out_path):
                return True
            # 窗口找到了但截图失败（渲染未完成等），稍后重试而不是直接放弃
            time.sleep(_WAIT_STEP)
            continue
        time.sleep(_WAIT_STEP)
    return False


def _kill_proc_tree(proc) -> None:
    """终止进程树（Windows taskkill /T 连子进程一起杀，避免孙进程孤儿）。"""
    if proc is None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10)
            if proc.poll() is None:
                proc.kill()
        else:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
    except Exception:
        pass


def app_screenshot(script: str, out_path: str, timeout: int = 30,
                   title_hint: str = "", python: Optional[str] = None,
                   extra_args: Optional[list] = None) -> dict:
    """运行 Python UI 脚本并截图。

    script：脚本绝对路径或相对路径（会 resolve 到绝对路径）。
    out_path：截图保存绝对路径。
    title_hint：窗口标题包含片段（可选，用于多窗口时定位）。
    python：python 解释器路径，默认 sys.executable（当前解释器）。
    extra_args：传给脚本的额外参数（可选）。

    仅 Windows 支持（依赖 ctypes EnumWindows 找窗口）；其它平台返回明确错误。
    """
    if os.name != "nt":
        return {"ok": False, "output": "app_screenshot 仅支持 Windows（需要枚举顶层窗口）。"}
    script = str(script)
    p_script = Path(script)
    if not p_script.is_absolute():
        p_script = p_script.resolve()
    if not p_script.is_file():
        # 回显相对路径，不泄露绝对路径（与相对路径安全红线一致）
        return {"ok": False, "output": f"脚本不存在: {script}"}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    py = python or sys.executable
    cmd = [py, str(p_script)]
    if extra_args:
        cmd.extend(str(a) for a in extra_args)
    try:
        timeout = min(max(int(timeout or 30), 5), 120)
    except (TypeError, ValueError):
        timeout = 30

    # Windows 隐藏控制台窗口；但 UI 窗口本身仍会显示
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = None
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(p_script.parent), stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, **kwargs)
    except OSError as e:
        # 异常信息可能含绝对路径/命令行，统一泛化
        from .errors import log_error
        log_error("app_screenshot 启动脚本失败", e)
        return {"ok": False, "output": "启动脚本失败，请检查脚本路径与 Python 环境。"}

    try:
        try:
            ok = _wait_and_shoot(proc.pid, str(out), timeout, title_hint)
        except Exception as e:
            from .errors import log_error
            log_error("app_screenshot 截图执行异常", e)
            return {"ok": False, "output": f"截图失败: {type(e).__name__}"}
        if not ok:
            # 先杀进程树（否则 GUI 子进程存活时 stderr 管道不关闭，
            # read() 会永久阻塞挂死调用线程）
            _kill_proc_tree(proc)
            # 回吐 stderr 尾部诊断，帮助排查"启动即崩"的脚本
            tail = ""
            try:
                if proc.stderr:
                    _, err = proc.communicate(timeout=5)
                    if err:
                        text = err.decode("utf-8", "replace")[-500:]
                        text = text.replace(str(p_script.parent), "<script_dir>")
                        tail = "\n脚本 stderr: " + text
            except Exception:
                pass
            return {"ok": False,
                    "output": f"等待窗口超时（{timeout}s），未截到窗口。{tail}"}
        if not out.is_file() or out.stat().st_size == 0:
            return {"ok": False, "output": "截图失败：未生成有效图片文件。"}
        return {"ok": True,
                "output": f"已运行 {p_script.name} 并截图保存：{out.name}（{out.stat().st_size} 字节）"}
    finally:
        _kill_proc_tree(proc)

