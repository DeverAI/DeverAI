"""UI 元素检视与可靠交互（v1.0）：基于 Win32 API 枚举子窗口/控件，提供元素级操作。

解决现有 exe_click 纯坐标点击的脆弱问题：学习软件/桌面应用的按钮、输入框、菜单等
可以通过控件文本/类名/层级精确定位，不依赖屏幕坐标。

能力（全部经 ctypes 调 user32，零第三方依赖）：
- enum_child_windows(hwnd)     枚举指定窗口的所有子窗口/控件（递归）
- find_control(hwnd, text, cls) 按文本或类名在子窗口树中定位控件
- get_control_info(hwnd)        获取单个控件的详细信息（文本/类名/位置/可见性）
- control_click(hwnd)           点击控件中心（更可靠：先 SetCursorPos 再 mouse_event）
- control_set_text(hwnd, text)  向控件发送 WM_SETTEXT（适用于 Edit/RichEdit）
- control_get_text(hwnd)        读取控件文本（GetWindowTextW）
- get_window_tree(hwnd)         获取完整的窗口树结构（JSON 可序列化）

安全：
- 本模块只提供机械动作原语，不判断"该不该点"；调用方必须先过审批门。
- 不访问网络、不读写文件，只操作窗口句柄。
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from typing import Optional

if os.name == "nt":
    import ctypes
    from ctypes import wintypes
else:
    ctypes = None  # type: ignore
    wintypes = None  # type: ignore

# Win32 常量
_WM_GETTEXT = 0x000D
_WM_GETTEXTLENGTH = 0x000E
_WM_SETTEXT = 0x000C
_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_WS_VISIBLE = 0x10000000
_WS_DISABLED = 0x08000000
_SW_SHOW = 5

# 常见 Windows 控件类名
_COMMON_CLASSES = (
    "Button", "Edit", "Static", "ListBox", "ComboBox",
    "SysListView32", "SysTreeView32", "SysTabControl32",
    "ScrollBar", "ToolBarWindow32", "StatusBarWindow32",
    "RichEdit20A", "RichEdit20W", "RICHEDIT50W",
    "msctls_progress32", "msctls_trackbar32", "msctls_updown32",
    "SysDateTimePick32", "SysMonthCal32",
    "Custom", "DirectUIHWND", "NotifyIconOverflowWindow",
)


def _u32():
    u32 = ctypes.windll.user32
    # 64 位下必须声明 SendMessageW 的指针级参数：未声明时 ctypes 默认按 C int
    # 转换 64 位 lParam，WM_SETTEXT 必然 OverflowError
    if not getattr(u32.SendMessageW, "argtypes", None):
        u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM]
        u32.SendMessageW.restype = wintypes.LPARAM
    return u32


def _is_available() -> bool:
    return os.name == "nt" and ctypes is not None


# ---------------------------------------------------------------- 枚举子窗口
def enum_child_windows(parent_hwnd: int, recursive: bool = True, max_depth: int = 8) -> dict:
    """枚举指定窗口的所有子窗口/控件。

    返回 {ok, controls:[{hwnd,title,class_name,rect:{x,y,w,h},visible,enabled,children:[]}], count}。
    recursive=True 时递归枚举所有层级。max_depth 限制递归深度（默认 8，防栈溢出）。
    """
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    parent_hwnd = int(parent_hwnd or 0)
    if not parent_hwnd:
        return {"ok": False, "output": "无效父窗口句柄。"}

    u32 = _u32()
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _enum_level(hwnd: int, level: int = 0) -> Optional[dict]:
        if level > max_depth:
            return None
        info = get_control_info(hwnd)
        if not info.get("ok"):
            return None
        ctrl = {
            "hwnd": int(hwnd),
            "title": info.get("title", ""),
            "class_name": info.get("class_name", ""),
            "rect": info.get("rect", {}),
            "visible": info.get("visible", False),
            "enabled": info.get("enabled", False),
            "level": level,
        }
        if recursive and level < max_depth:
            children = []
            child_list = []

            def _cb(child_hwnd, _lparam):
                child_list.append(child_hwnd)
                return True

            # 保存回调引用防 GC
            _cb_proc = WNDENUMPROC(_cb)
            try:
                u32.EnumChildWindows(hwnd, _cb_proc, 0)
            except Exception:
                pass

            for child_hwnd in child_list:
                child_info = _enum_level(child_hwnd, level + 1)
                if child_info:
                    children.append(child_info)
            ctrl["children"] = children
        return ctrl

    try:
        result = _enum_level(parent_hwnd, 0)
    except Exception as e:
        return {"ok": False, "output": f"枚举失败: {e}"}

    if result is None:
        return {"ok": False, "output": "无法获取窗口信息。"}

    # 展平计数
    def _count(node):
        c = 1
        for ch in node.get("children", []):
            c += _count(ch)
        return c

    total = _count(result) - 1  # 减去根节点自身
    return {"ok": True, "controls": result, "count": total}


# ---------------------------------------------------------------- 控件信息
def get_control_info(hwnd: int) -> dict:
    """获取单个控件的详细信息。"""
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    hwnd = int(hwnd or 0)
    if not hwnd:
        return {"ok": False, "output": "无效句柄。"}

    u32 = _u32()
    try:
        # 窗口文本
        length = u32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            u32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
        else:
            title = ""

        # 类名（512 字符缓冲区，防长类名截断）
        cls_buf = ctypes.create_unicode_buffer(512)
        u32.GetClassNameW(hwnd, cls_buf, 512)
        class_name = cls_buf.value

        # 矩形
        rect = wintypes.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(rect))
        rect_info = {
            "x": rect.left, "y": rect.top,
            "w": rect.right - rect.left, "h": rect.bottom - rect.top,
        }

        # 可见性/启用状态
        style = u32.GetWindowLongW(hwnd, _GWL_STYLE)
        visible = bool(style & _WS_VISIBLE)
        enabled = not bool(style & _WS_DISABLED)

        return {
            "ok": True,
            "hwnd": int(hwnd),
            "title": title,
            "class_name": class_name,
            "rect": rect_info,
            "visible": visible,
            "enabled": enabled,
        }
    except Exception as e:
        return {"ok": False, "output": f"获取控件信息失败: {e}"}


# ---------------------------------------------------------------- 查找控件
def find_control(parent_hwnd: int, text: str = "", class_name: str = "",
                 recursive: bool = True) -> dict:
    """在子窗口树中按文本或类名定位控件。

    返回 {ok, found, hwnd, title, class_name, rect, path}。
    text 和 class_name 至少提供一个，都提供时AND匹配。
    """
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    parent_hwnd = int(parent_hwnd or 0)
    if not parent_hwnd:
        return {"ok": False, "output": "无效父窗口句柄。"}

    text = (text or "").strip().lower()
    class_name = (class_name or "").strip().lower()

    if not text and not class_name:
        return {"ok": False, "output": "text 和 class_name 至少提供一个。"}

    u32 = _u32()
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    found_hwnd = 0
    found_title = ""
    found_class = ""

    def _match(hwnd):
        nonlocal found_hwnd, found_title, found_class
        try:
            # 文本匹配
            length = u32.GetWindowTextLengthW(hwnd)
            win_title = ""
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                u32.GetWindowTextW(hwnd, buf, length + 1)
                win_title = buf.value

            # 类名匹配（512 字符缓冲区，防长类名截断）
            cls_buf = ctypes.create_unicode_buffer(512)
            u32.GetClassNameW(hwnd, cls_buf, 512)
            win_class = cls_buf.value

            match_text = (text in win_title.lower()) if text else True
            match_cls = (class_name in win_class.lower()) if class_name else True

            if match_text and match_cls:
                found_hwnd = int(hwnd)
                found_title = win_title
                found_class = win_class
                return False  # 停止枚举
        except Exception:
            pass
        return True

    # 保存回调引用防 GC
    _match_proc = WNDENUMPROC(_match)
    try:
        if recursive:
            # 递归枚举所有子窗口
            _enum_all(parent_hwnd, _match)
        else:
            u32.EnumChildWindows(parent_hwnd, _match_proc, 0)
    except Exception as e:
        return {"ok": False, "output": f"查找失败: {e}"}

    if not found_hwnd:
        hint = []
        if text:
            hint.append(f"文本含「{text}」")
        if class_name:
            hint.append(f"类名含「{class_name}」")
        return {"ok": True, "found": False, "output": f"未找到控件：{' 且 '.join(hint)}"}

    # 获取矩形
    rect = wintypes.RECT()
    try:
        u32.GetWindowRect(found_hwnd, ctypes.byref(rect))
        rect_info = {"x": rect.left, "y": rect.top, "w": rect.right - rect.left, "h": rect.bottom - rect.top}
    except Exception:
        rect_info = {}

    return {
        "ok": True,
        "found": True,
        "hwnd": found_hwnd,
        "title": found_title,
        "class_name": found_class,
        "rect": rect_info,
    }


def _enum_all(parent_hwnd: int, callback, max_depth: int = 20, depth: int = 0) -> None:
    """递归枚举所有子窗口并对每个调用 callback。callback 返回 False 时停止。max_depth 防循环父子关系导致栈溢出。"""
    if depth >= max_depth:
        return
    u32 = _u32()
    child_list = []

    def _cb(hwnd, _lparam):
        child_list.append(hwnd)
        return True

    # 保存回调引用防 GC（局部变量 _cb_proc 在整个 EnumChildWindows 调用期间存活）
    _cb_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(_cb)
    try:
        u32.EnumChildWindows(parent_hwnd, _cb_proc, 0)
    except Exception:
        return

    for child_hwnd in child_list:
        if not callback(child_hwnd):
            return
        _enum_all(child_hwnd, callback, max_depth, depth + 1)


# ---------------------------------------------------------------- 控件操作
def control_click(ctrl_hwnd: int) -> dict:
    """点击控件中心（比坐标点击更可靠）。"""
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    ctrl_hwnd = int(ctrl_hwnd or 0)
    if not ctrl_hwnd:
        return {"ok": False, "output": "无效控件句柄。"}

    try:
        u32 = _u32()
        # 先置前父窗口
        parent = u32.GetParent(ctrl_hwnd)
        if parent:
            u32.ShowWindow(parent, _SW_SHOW)
            u32.SetForegroundWindow(parent)
            time.sleep(0.1)

        # 获取控件中心坐标
        rect = wintypes.RECT()
        u32.GetWindowRect(ctrl_hwnd, ctypes.byref(rect))
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2

        # 点击
        u32.SetCursorPos(cx, cy)
        time.sleep(0.05)
        u32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        u32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        return {"ok": True, "output": f"已点击控件中心 ({cx}, {cy})"}
    except Exception as e:
        return {"ok": False, "output": f"点击失败: {e}"}


def control_set_text(ctrl_hwnd: int, text: str) -> dict:
    """向控件发送 WM_SETTEXT（适用于 Edit/RichEdit/ComboBox 等输入控件）。"""
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    ctrl_hwnd = int(ctrl_hwnd or 0)
    if not ctrl_hwnd:
        return {"ok": False, "output": "无效控件句柄。"}
    text = str(text or "")
    if not text:
        return {"ok": False, "output": "缺少要设置的文本。"}

    try:
        u32 = _u32()
        # 先点击控件获取焦点
        rect = wintypes.RECT()
        u32.GetWindowRect(ctrl_hwnd, ctypes.byref(rect))
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        u32.SetCursorPos(cx, cy)
        time.sleep(0.05)
        u32.mouse_event(0x0002, 0, 0, 0, 0)
        u32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.1)

        # 发送 WM_SETTEXT（64 位下 LPARAM 是 c_longlong，不能直接 cast 指针类型）
        c_text = ctypes.c_wchar_p(text)
        lparam = ctypes.cast(c_text, ctypes.c_void_p).value
        u32.SendMessageW(ctrl_hwnd, _WM_SETTEXT, 0, lparam)
        return {"ok": True, "output": f"已设置控件文本（{len(text)} 字符）"}
    except Exception as e:
        return {"ok": False, "output": f"设置文本失败: {e}"}


def control_get_text(ctrl_hwnd: int) -> dict:
    """读取控件文本（GetWindowTextW）。"""
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    ctrl_hwnd = int(ctrl_hwnd or 0)
    if not ctrl_hwnd:
        return {"ok": False, "output": "无效控件句柄。"}

    try:
        u32 = _u32()
        length = u32.GetWindowTextLengthW(ctrl_hwnd)
        if length <= 0:
            return {"ok": True, "text": "", "output": "控件无文本。"}
        buf = ctypes.create_unicode_buffer(length + 1)
        u32.GetWindowTextW(ctrl_hwnd, buf, length + 1)
        return {"ok": True, "text": buf.value, "output": f"控件文本: {buf.value}"}
    except Exception as e:
        return {"ok": False, "output": f"读取文本失败: {e}"}


# ---------------------------------------------------------------- 窗口树
def get_window_tree(hwnd: int, max_depth: int = 5) -> dict:
    """获取完整的窗口树结构（JSON 可序列化）。

    返回嵌套的 {hwnd, title, class_name, rect, visible, enabled, children:[]}。
    max_depth 限制递归深度防止过大。
    """
    if not _is_available():
        return {"ok": False, "output": "仅支持 Windows。"}
    hwnd = int(hwnd or 0)
    if not hwnd:
        return {"ok": False, "output": "无效窗口句柄。"}

    def _build(node_hwnd: int, depth: int) -> Optional[dict]:
        if depth > max_depth:
            return None
        info = get_control_info(node_hwnd)
        if not info.get("ok"):
            return None
        result = {
            "hwnd": int(node_hwnd),
            "title": info.get("title", ""),
            "class_name": info.get("class_name", ""),
            "rect": info.get("rect", {}),
            "visible": info.get("visible", False),
            "enabled": info.get("enabled", False),
        }
        if depth < max_depth:
            children = []
            child_list = []

            def _cb(child_hwnd, _lparam):
                child_list.append(child_hwnd)
                return True

            # 保存回调引用防 GC
            _cb_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(_cb)
            try:
                ctypes.windll.user32.EnumChildWindows(node_hwnd, _cb_proc, 0)
            except Exception:
                pass

            for child_hwnd in child_list:
                child_node = _build(child_hwnd, depth + 1)
                if child_node:
                    children.append(child_node)
            result["children"] = children
        return result

    try:
        tree = _build(hwnd, 0)
    except Exception as e:
        return {"ok": False, "output": f"获取窗口树失败: {e}"}

    if tree is None:
        return {"ok": False, "output": "无法构建窗口树。"}
    return {"ok": True, "tree": tree}
