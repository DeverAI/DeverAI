"""DeverAI 桌面版 SVG 图标系统（v7 重构）。

核心修复：SVG 图标颜色随主题自动适配（深色=浅色图标 / 浅色=深色图标）。

原理：
1. 读取 SVG 原文 → 正则去除 fill="white" 背景层 → 将所有 fill/stroke 色替换为目标色
2. 用修改后的 SVG 渲染 QPixmap，天然支持 currentColor 语义
3. 模块级 _theme_fg/_theme_accent 在主题切换时由 gui._apply_theme() 更新
4. 缓存键含 color.rgb()，主题切换时清缓存

遵守 NO EMOJI 约束，所有 UI 图标用 SVG。
"""
from __future__ import annotations
import re
from pathlib import Path
from PyQt5.QtCore import QByteArray, QSize, Qt
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt5.QtSvg import QSvgRenderer

_ICONS_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"

# 主题色（由 set_theme_colors 更新，默认深色主题前景色）
_theme_fg = "#c8d3e6"       # 前景色（图标默认色）
_theme_accent = "#3b82f6"   # 强调色（活动栏激活态）
_theme_muted = "#6b7280"    # 次要色（禁用态）

# 缓存
_pixmap_cache: dict[tuple[str, int, str], QPixmap] = {}


def set_theme_colors(fg: str | None = None, accent: str | None = None, muted: str | None = None):
    """主题切换时调用：更新图标着色用的语义色，并清缓存。

    传 None 表示不更新该颜色（默认）。传空字符串会被视为有效值（虽无意义）。
    """
    global _theme_fg, _theme_accent, _theme_muted
    changed = False
    if fg is not None and fg != _theme_fg:
        _theme_fg = fg
        changed = True
    if accent is not None and accent != _theme_accent:
        _theme_accent = accent
        changed = True
    if muted is not None and muted != _theme_muted:
        _theme_muted = muted
        changed = True
    if changed:
        _pixmap_cache.clear()


def _recolor_svg(svg_text: str, color: str) -> QByteArray:
    """将 SVG 内所有 fill/stroke 颜色替换为目标色（保留 none）。

    - 去除 fill="white" 的 <rect> 背景层（svgrepo 图标常见）
    - fill="none" 保留（透明背景）
    - stroke="none" 保留
    - 其余 fill/stroke 色值统一替换为 color
    """
    # 去除白色背景 rect 元素（svgrepo 图标常见，深色主题下会变实心方块）
    # 匹配自闭合 <rect .../> 和显式闭合 <rect ...></rect> 两种形式
    # 白色匹配：white / #fff / #ffffff / White（大小写不敏感）
    svg_text = re.sub(
        r'<rect\b[^>]*\bfill="(?:white|#fff(?:fff)?|White)"[^>]*/\s*>', '', svg_text,
        flags=re.IGNORECASE)
    svg_text = re.sub(
        r'<rect\b[^>]*\bfill="(?:white|#fff(?:fff)?|White)"[^>]*>\s*</rect>', '', svg_text,
        flags=re.IGNORECASE)
    # 替换 fill 属性（排除 none / transparent / white 背景层；兼容单双引号与大写 White）
    svg_text = re.sub(
        r'''fill="(?!none\b|transparent\b|white\b)[^"]*"''', f'fill="{color}"', svg_text,
        flags=re.IGNORECASE)
    svg_text = re.sub(
        r"fill='(?!none\b|transparent\b|white\b)[^']*'", f"fill='{color}'", svg_text,
        flags=re.IGNORECASE)
    # 替换 stroke 属性（排除 none / transparent）
    svg_text = re.sub(
        r'stroke="(?!none\b|transparent\b)[^"]*"', f'stroke="{color}"', svg_text)
    svg_text = re.sub(
        r"stroke='(?!none\b|transparent\b)[^']*'", f"stroke='{color}'", svg_text)
    return QByteArray(svg_text.encode("utf-8"))


def _load_svg_text(name: str) -> str | None:
    """根据图标名加载 SVG 原始文本。"""
    candidates = [name, name + "-svgrepo-com.svg", name + ".svg"]
    for c in list(candidates):
        candidates.append(c.replace(".svg", " (1).svg"))
    for c in candidates:
        p = _ICONS_DIR / c
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8")
            except OSError:
                return None
    # 模糊匹配
    try:
        for f in _ICONS_DIR.glob(f"*{name}*.svg"):
            return f.read_text(encoding="utf-8")
    except OSError:
        pass
    return None


def render_pixmap(name: str, size: int = 16, color: QColor | None = None) -> QPixmap:
    """渲染 SVG 到 QPixmap。

    color 为 None 时使用主题前景色（_theme_fg）。
    传 QColor 时用指定色着色（如活动栏激活态用 accent）。
    """
    color_hex = color.name() if color is not None else _theme_fg
    key = (name, size, color_hex)
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached

    svg_text = _load_svg_text(name)
    if svg_text is None:
        # 兜底：透明空 pixmap
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        _pixmap_cache[key] = pm
        return pm

    # 重着色后渲染
    recolored = _recolor_svg(svg_text, color_hex)
    renderer = QSvgRenderer(recolored)
    if not renderer.isValid():
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        _pixmap_cache[key] = pm
        return pm

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(p)
    p.end()
    _pixmap_cache[key] = pm
    return pm


def make_icon(name: str, size: int = 16, color: QColor | None = None) -> QIcon:
    """生成 QIcon。"""
    return QIcon(render_pixmap(name, size, color))


def themed_icon(name: str, size: int = 16) -> QIcon:
    """用当前主题前景色上色。"""
    return make_icon(name, size)


def accent_icon(name: str, size: int = 16) -> QIcon:
    """用当前主题强调色上色（活动栏激活态等）。"""
    return make_icon(name, size, QColor(_theme_accent))


def muted_icon(name: str, size: int = 16) -> QIcon:
    """用当前主题次要色上色（禁用态等）。"""
    return make_icon(name, size, QColor(_theme_muted))


# 常用图标别名
ALIAS = {
    "files": "folder-arrow-down",
    "vault": "atom",
    "terminal": "terminal",
    "settings": "cog",
    "search": "circle-information",
    "home": "house-floor",
    "ai": "atom",
    "edit": "pen-line",
    "memo": "memo",
    "delete": "alt-tag",
    "rename": "link-alt",
    "lock": "lock-closed",
    "unlock": "lock-open",
    "user": "circle-user",
    "info": "circle-information",
    "warn": "circle-information",
    "err": "close",
    "ok": "circle-information",
    "running": "arrow-repeat-235",
    "done": "circle-information",
    "fail": "close",
    "stop": "close",
    "send": "cloud-upload",
    "save": "folder-arrow-down",
    "open": "folder-arrow-down",
    "close": "close",
    "plus": "card-add",
    "minus": "close",
    "moon": "moon",
    "bell": "bell",
    "bell-off": "bell-slash",
    "bot": "artificial-bot-intelligence",
    "chart": "chart-bar-alt-square",
    "calendar": "calendar-lines",
    "cloud-ok": "cloud-check",
    "cloud-fail": "cloud-xmark",
    "cloud-up": "cloud-upload",
    "cloud-down": "cloud-arrow-down-alt",
    "refresh": "arrow-repeat-235",
    "undo": "arrow-u-up-left",
    "redo": "arrow-u-up-right",
    "tool": "cog",
    "expert": "circle-user",
    "commander": "atom",
    "copilot": "artificial-bot-intelligence",
    "guard": "circle-information",
    "drift": "arrow-repeat-235",
    "sleep": "moon",
    "lock-status": "lock-closed",
    "skill": "pen-swirl",
    "list": "notebook",
    "tree": "folder-arrow-down",
    "ctx": "circle-information",
    "note": "memo",
    "quote": "link-alt",
    "ban": "close",
    "toggle": "arrow-repeat-235",
    "background": "arrow-repeat-235",
    "sync": "arrow-repeat-235",
    "folder": "folder-arrow-down",
    "atom": "atom",
    "cog": "cog",
    "pen": "pen-line",
    "pen-swirl": "pen-swirl",
    "notebook": "notebook",
    "moon": "moon",
    "terminal": "terminal",
    "close": "close",
    "memo": "memo",
    "bell-slash": "bell-slash",
    "artificial-bot-intelligence": "artificial-bot-intelligence",
    "calendar-lines": "calendar-lines",
    "card-add": "card-add",
    "chart-bar-alt-square": "chart-bar-alt-square",
    "circle-information": "circle-information",
    "circle-user": "circle-user",
    "cloud-arrow-down-alt": "cloud-arrow-down-alt",
    "cloud-check": "cloud-check",
    "cloud-upload": "cloud-upload",
    "cloud-xmark": "cloud-xmark",
    "contrast-1428": "contrast-1428",
    "house-floor": "house-floor",
    "link-alt": "link-alt",
    "lock-closed": "lock-closed",
    "lock-open": "lock-open",
    "pen-line": "pen-line",
}


def icon(name: str, size: int = 16, color: QColor | None = None) -> QIcon:
    """统一入口：支持别名。color=None 时用主题前景色。"""
    real = ALIAS.get(name, name)
    return make_icon(real, size, color)
