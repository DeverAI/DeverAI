"""v8.6 Win32 外部程序自动化（仅 Windows）：AI 操作「某一个 exe」的底层原语。

能力（全部经 ctypes 调 user32，零第三方依赖）：
- launch_exe(path, args)      启动外部 exe，返回 pid
- list_windows()              枚举可见顶层窗口（hwnd/标题/pid）
- find_window(title)          按标题片段找窗口 hwnd
- bring_to_front(hwnd)        窗口置前
- screenshot_window(hwnd, path)  截图保存（复用 app_shot 的主线程 QScreen 抓取）
- click(x, y)                 屏幕坐标左键单击
- type_text(text)             Unicode 键盘输入
- press_keys(keys)            常用功能键（enter/tab/esc/上下左右等）
- close_window(hwnd) / terminate_pid(pid)  关闭窗口/结束进程

安全约束（上层 tools.py 负责，本模块不做任何放行判断）：
- 仅提供机械动作原语，不判断"该不该点"；调用方必须先过审批门 + 副驾驶监督。
- 本模块不访问网络、不读写文件，只操作给定坐标/文本/进程。
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Optional

if os.name == "nt":
    import ctypes
    from ctypes import wintypes
else:
    ctypes = None  # type: ignore
    wintypes = None  # type: ignore

# 鼠标事件常量
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004

# 键盘事件常量
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004

# 常用功能键 → 虚拟键码
_VK_MAP = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "space": 0x20, "up": 0x26, "down": 0x28, "left": 0x25,
    "right": 0x27, "home": 0x24, "end": 0x23, "delete": 0x2E, "insert": 0x2D,
    "pageup": 0x21, "pagedown": 0x22, "ctrl": 0x11, "control": 0x11,
    "shift": 0x10, "alt": 0x12, "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
}


def _u32():
    return ctypes.windll.user32


def _is_available() -> bool:
    return os.name == "nt" and ctypes is not None


def launch_exe(path: str, args: Optional[list] = None, cwd: str = "") -> dict:
    """启动外部 exe。返回 {ok, pid, output}。"""
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    p = Path(str(path or "")).resolve()
    if not p.is_file():
        return {"ok": False, "output": f"可执行文件不存在: {p}"}
    cmd = [str(p)]
    if args:
        cmd.extend(str(a) for a in args)
    try:
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen(
            cmd, cwd=str(Path(cwd).resolve() if cwd else p.parent),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=creationflags)
    except OSError as e:
        return {"ok": False, "output": f"启动失败: {e}"}
    return {"ok": True, "pid": proc.pid, "output": f"已启动 {p.name}（pid={proc.pid}）"}


def list_windows() -> dict:
    """枚举所有可见顶层窗口。返回 {ok, windows:[{hwnd,title,pid}]}。"""
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    u32 = _u32()
    found = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        try:
            if not u32.IsWindowVisible(hwnd):
                return True
            length = u32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            u32.GetWindowTextW(hwnd, buf, length + 1)
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append({"hwnd": int(hwnd), "title": buf.value, "pid": int(pid.value)})
        except Exception:
            pass
        return True

    try:
        u32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception as e:
        return {"ok": False, "output": f"枚举窗口失败: {e}"}
    return {"ok": True, "windows": found, "count": len(found)}


def find_window(title: str) -> int:
    """按标题片段找第一个可见窗口 hwnd，找不到返回 0。"""
    if not _is_available() or not title:
        return 0
    res = list_windows()
    if not res.get("ok"):
        return 0
    low = title.lower()
    for w in res.get("windows") or []:
        if low in (w.get("title") or "").lower():
            return int(w.get("hwnd", 0))
    return 0


def bring_to_front(hwnd: int) -> dict:
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    try:
        u32 = _u32()
        u32.ShowWindow(int(hwnd), 9)  # SW_RESTORE
        u32.SetForegroundWindow(int(hwnd))
        return {"ok": True, "output": "窗口已置前"}
    except Exception as e:
        return {"ok": False, "output": f"置前失败: {e}"}


def _grab_win32(hwnd: int, path: str) -> bool:
    """纯 Win32 截图（GDI PrintWindow → QImage 保存；无需 Qt 应用实例）。

    v8.23：网页版/服务器场景无 QApplication，app_shot._grab 直接返回 False。
    QImage 不依赖应用实例即可构造/保存，PrintWindow(…, PW_RENDERFULLCONTENT)
    可捕获 DirectComposition 内容（Chrome/Win11 应用），后台窗口也可截。
    """
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    rect = wintypes.RECT()
    if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        return False
    w = int(rect.right - rect.left)
    h = int(rect.bottom - rect.top)
    if w <= 0 or h <= 0 or w > 20000 or h > 20000:
        return False
    hdc = user32.GetWindowDC(int(hwnd))
    if not hdc:
        return False
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    try:
        gdi32.SelectObject(memdc, bmp)
        # 2 = PW_RENDERFULLCONTENT
        if not user32.PrintWindow(int(hwnd), memdc, 2):
            return False

        class _BMI(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        bmi = _BMI()
        bmi.biSize = ctypes.sizeof(_BMI)
        bmi.biWidth = w
        bmi.biHeight = -h  # 自上而下
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        rows = gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        if not rows:
            return False
        from PyQt6.QtGui import QImage
        img = QImage(buf, w, h, w * 4, QImage.Format.Format_ARGB32)
        if img.isNull():
            return False
        return img.save(str(path), "PNG")
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(int(hwnd), hdc)


def screenshot_window(hwnd: int, path: str) -> dict:
    """截图指定窗口保存到本地（优先 app_shot 主线程抓取；无 Qt 时 Win32 回退）。"""
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    if not int(hwnd):
        return {"ok": False, "output": "无效窗口句柄。"}
    grabbed = False
    try:
        from . import app_shot as _as
        grabbed = _as._grab(int(hwnd), str(path))
    except Exception:
        grabbed = False
    if not grabbed:
        try:
            grabbed = _grab_win32(int(hwnd), str(path))
        except Exception:
            grabbed = False
    if not grabbed:
        return {"ok": False, "output": "截图失败（窗口可能不可见）。"}
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return {"ok": False, "output": "截图失败：未生成有效文件。"}
    return {"ok": True, "output": f"截图已保存：{p.name}（{p.stat().st_size} 字节）"}


def click(x: int, y: int) -> dict:
    """屏幕绝对坐标左键单击。"""
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    try:
        u32 = _u32()
        u32.SetCursorPos(int(x), int(y))
        time.sleep(0.05)
        u32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        u32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return {"ok": True, "output": f"已在 ({x}, {y}) 单击"}
    except Exception as e:
        return {"ok": False, "output": f"点击失败: {e}"}


def type_text(text: str) -> dict:
    """Unicode 键盘输入（SendInput）。"""
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    text = str(text or "")
    if not text:
        return {"ok": False, "output": "缺少要输入的文本。"}
    try:
        u32 = _u32()
        for ch in text:
            _send_unicode(u32, ch)
        return {"ok": True, "output": f"已输入 {len(text)} 个字符"}
    except Exception as e:
        return {"ok": False, "output": f"输入失败: {e}"}


def press_keys(keys: str) -> dict:
    """按下常用功能键，多个用逗号分隔（如 "ctrl,c" 表示 Ctrl+C，或 "enter"）。"""
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    parts = [k.strip().lower() for k in str(keys or "").split(",") if k.strip()]
    if not parts:
        return {"ok": False, "output": "缺少按键。"}
    try:
        u32 = _u32()
        unknown = [k for k in parts if k not in _VK_MAP]
        if unknown:
            return {"ok": False, "output": f"不支持的按键: {', '.join(unknown)}（支持 enter/tab/esc/方向键/ctrl/shift/alt/f1-f12 等）"}
        # 先按下所有键，再按相反顺序抬起
        for k in parts:
            u32.keybd_event(_VK_MAP[k], 0, 0, 0)
        for k in reversed(parts):
            u32.keybd_event(_VK_MAP[k], 0, 0x0002, 0)  # KEYEVENTF_KEYUP
        return {"ok": True, "output": f"已按下 {', '.join(parts)}"}
    except Exception as e:
        return {"ok": False, "output": f"按键失败: {e}"}


def close_window(hwnd: int) -> dict:
    """关闭窗口（WM_CLOSE）。"""
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    try:
        _u32().PostMessageW(int(hwnd), 0x0010, 0, 0)  # WM_CLOSE
        return {"ok": True, "output": "已发送关闭窗口消息"}
    except Exception as e:
        return {"ok": False, "output": f"关闭窗口失败: {e}"}


def window_pid(hwnd: int) -> int:
    """查询窗口所属进程 pid（供上层校验窗口是否由白名单 exe 启动）。失败返回 0。"""
    if not _is_available() or not int(hwnd or 0):
        return 0
    try:
        pid = wintypes.DWORD()
        _u32().GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def screen_size() -> tuple:
    """主屏分辨率 (宽, 高)；不可用时返回 (0, 0)。供坐标边界校验。"""
    if not _is_available():
        return (0, 0)
    try:
        u32 = _u32()
        return (int(u32.GetSystemMetrics(0)), int(u32.GetSystemMetrics(1)))
    except Exception:
        return (0, 0)


def terminate_pid(pid: int) -> dict:
    """结束进程树（taskkill /T /F）。"""
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    try:
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                       capture_output=True, timeout=10)
        return {"ok": True, "output": f"已结束进程 {pid}（含子进程）"}
    except Exception as e:
        return {"ok": False, "output": f"结束进程失败: {e}"}


# ------------------------------------------------------------------ 内部实现
if os.name == "nt":
    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("union", _INPUTUNION),
        ]


def _send_unicode(u32, ch: str) -> None:
    """用 SendInput 发送一个 Unicode 字符（按下+抬起）。"""
    code = ord(ch)
    down = _INPUT()
    down.type = _INPUT_KEYBOARD
    down.union.ki.wVk = 0
    down.union.ki.wScan = code
    down.union.ki.dwFlags = _KEYEVENTF_UNICODE
    down.union.ki.time = 0
    down.union.ki.dwExtraInfo = None
    up = _INPUT()
    up.type = _INPUT_KEYBOARD
    up.union.ki.wVk = 0
    up.union.ki.wScan = code
    up.union.ki.dwFlags = _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP
    up.union.ki.time = 0
    up.union.ki.dwExtraInfo = None
    arr = (_INPUT * 2)(down, up)
    u32.SendInput(2, arr, ctypes.sizeof(_INPUT))
