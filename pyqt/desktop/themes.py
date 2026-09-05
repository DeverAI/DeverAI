"""DeverAI 主题引擎（v7 全新设计语言）。

设计理念：
- 现代AI编程工具风格（Cursor/Trae/VS Code 暗色系）
- 品牌强调色贯穿全局：按钮激活态、光标、选中、图标
- 语义色板 JSON → 全局 QSS，主题切换全 UI 联动
- 深浅色自适应：is_dark() 判定，图标着色自动适配
"""
from typing import Optional

from .config import DATA_DIR
from .storage import load_json, save_json

CUSTOM_PATH = DATA_DIR / "theme_custom.json"

PALETTE_KEYS = [
    ("bg", "窗口背景"),
    ("panel", "面板/侧栏"),
    ("editor", "编辑器背景"),
    ("field", "输入框背景"),
    ("text", "主文字"),
    ("muted", "次要文字"),
    ("faint", "极淡文字"),
    ("accent", "强调色"),
    ("accent_dim", "强调色半透明背景"),
    ("border", "边框"),
    ("btn", "按钮背景"),
    ("btn_hover", "按钮悬停"),
    ("sel", "选中项背景"),
    ("hover", "悬停背景"),
    ("user_bubble", "用户气泡"),
    ("user_bubble_text", "用户气泡文字"),
    ("ai_head", "AI 标题"),
    ("tool_bg", "工具卡片背景"),
    ("tool_border", "工具卡片边框"),
    ("ghost", "补全幽灵文字"),
    ("scrollbar", "滚动条"),
    ("ok", "成功色"),
    ("warn", "警告色"),
    ("err", "错误色"),
    ("activity_bg", "活动栏背景"),
    ("activity_hover", "活动栏悬停"),
    ("activity_active", "活动栏激活指示"),
]

THEMES = {
    # 曜石暗 — 深炭灰 + 电光蓝
    "obsidian": {
        "bg": "#0e0e12", "panel": "#16161c", "editor": "#121218", "field": "#1c1c24",
        "text": "#e2e8f0", "muted": "#94a3b8", "faint": "#64748b",
        "accent": "#3b82f6", "accent_dim": "rgba(59,130,186,0.15)",
        "border": "#252530", "btn": "#1e1e28", "btn_hover": "#2a2a38", "sel": "rgba(59,130,186,0.25)",
        "hover": "rgba(255,255,255,0.05)",
        "user_bubble": "#3b82f6", "user_bubble_text": "#ffffff", "ai_head": "#60a5fa",
        "tool_bg": "#16161c", "tool_border": "#252530",
        "ghost": "#4a5568", "scrollbar": "#2a2a38",
        "ok": "#22c55e", "warn": "#f59e0b", "err": "#ef4444",
        "activity_bg": "#0a0a0e", "activity_hover": "#1a1a24", "activity_active": "#3b82f6",
    },
    # 深空 Harness — v8.6 对齐网页版深色主题（DeepSeek Harness 风格：深空灰 + 深求蓝）
    "harness": {
        "bg": "#1e1f23", "panel": "#232428", "editor": "#1b1c20", "field": "#2a2b30",
        "text": "#e8e9ed", "muted": "#9b9da6", "faint": "#6c6e78",
        "accent": "#4d6bfe", "accent_dim": "rgba(77,107,254,0.16)",
        "border": "#2e2f34", "btn": "#2a2b30", "btn_hover": "#33343a", "sel": "rgba(77,107,254,0.28)",
        "hover": "rgba(255,255,255,0.05)",
        "user_bubble": "#4d6bfe", "user_bubble_text": "#ffffff", "ai_head": "#7c93ff",
        "tool_bg": "#232428", "tool_border": "#2e2f34",
        "ghost": "#5a5c66", "scrollbar": "#33343a",
        "ok": "#4ade80", "warn": "#fbbf24", "err": "#f87171",
        "activity_bg": "#1a1b1f", "activity_hover": "#26272c", "activity_active": "#4d6bfe",
    },
    # 深海蓝 — 深海军蓝 + 青蓝
    "midnight": {
        "bg": "#0b1626", "panel": "#11203a", "editor": "#0d1a2e", "field": "#152a48",
        "text": "#dce8ff", "muted": "#7f95bd", "faint": "#4a6080",
        "accent": "#06b6d4", "accent_dim": "rgba(6,182,212,0.15)",
        "border": "#1e3454", "btn": "#173052", "btn_hover": "#1f3f6e", "sel": "rgba(6,182,212,0.25)",
        "hover": "rgba(255,255,255,0.04)",
        "user_bubble": "#0e7490", "user_bubble_text": "#ffffff", "ai_head": "#22d3ee",
        "tool_bg": "#0e1e36", "tool_border": "#1e3454",
        "ghost": "#3b5070", "scrollbar": "#2b4a78",
        "ok": "#22c55e", "warn": "#f59e0b", "err": "#ef4444",
        "activity_bg": "#081522", "activity_hover": "#0f1d35", "activity_active": "#06b6d4",
    },
    # 纸感白 — 纯净白 + 钴蓝
    "paper": {
        "bg": "#ffffff", "panel": "#f8f9fb", "editor": "#ffffff", "field": "#f1f3f5",
        "text": "#1e293b", "muted": "#64748b", "faint": "#94a3b8",
        "accent": "#2563eb", "accent_dim": "rgba(37,99,235,0.08)",
        "border": "#e2e8f0", "btn": "#f1f5f9", "btn_hover": "#e2e8f0", "sel": "rgba(37,99,235,0.12)",
        "hover": "rgba(0,0,0,0.03)",
        "user_bubble": "#2563eb", "user_bubble_text": "#ffffff", "ai_head": "#2563eb",
        "tool_bg": "#f8f9fb", "tool_border": "#e2e8f0",
        "ghost": "#cbd5e1", "scrollbar": "#cbd5e1",
        "ok": "#16a34a", "warn": "#d97706", "err": "#dc2626",
        "activity_bg": "#f1f3f5", "activity_hover": "#e2e8f0", "activity_active": "#2563eb",
    },
    # 羊皮米 — 暖米色 + 琥珀
    "sand": {
        "bg": "#faf6f0", "panel": "#f0e9dc", "editor": "#fffcf5", "field": "#ede5d2",
        "text": "#44403c", "muted": "#8b8170", "faint": "#a8a090",
        "accent": "#d97706", "accent_dim": "rgba(217,119,6,0.10)",
        "border": "#ddd3bd", "btn": "#ede5d2", "btn_hover": "#e0d6c0", "sel": "rgba(217,119,6,0.15)",
        "hover": "rgba(0,0,0,0.02)",
        "user_bubble": "#d97706", "user_bubble_text": "#fff8ec", "ai_head": "#b45309",
        "tool_bg": "#f0e9dc", "tool_border": "#ddd3bd",
        "ghost": "#a8a090", "scrollbar": "#c9bfa5",
        "ok": "#16a34a", "warn": "#ca8a04", "err": "#dc2626",
        "activity_bg": "#ebe4d4", "activity_hover": "#ddd3bd", "activity_active": "#d97706",
    },
}

THEME_LABELS = {
    "obsidian": "曜石暗",
    "harness": "深空 Harness",
    "midnight": "深海蓝",
    "paper": "纸感白",
    "sand": "羊皮米",
    "custom": "自定义",
}


def get_palette(name: str) -> dict:
    if name == "custom":
        data = load_json(CUSTOM_PATH, {})
        if isinstance(data, dict) and data.get("bg"):
            base = dict(THEMES["obsidian"])
            base.update({k: v for k, v in data.items() if k in dict(PALETTE_KEYS)})
            return base
        return dict(THEMES["obsidian"])
    return dict(THEMES.get(name, THEMES["obsidian"]))


def save_custom(palette: dict) -> None:
    save_json(CUSTOM_PATH, {k: palette.get(k, "") for k, _ in PALETTE_KEYS})


def load_custom() -> dict:
    data = load_json(CUSTOM_PATH, {})
    return data if isinstance(data, dict) else {}


def is_dark(palette: dict) -> bool:
    try:
        h = str(palette.get("bg", "#000000")).lstrip("#")
        if len(h) < 6:
            return True
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (0.299 * r + 0.587 * g + 0.114 * b) < 128
    except ValueError:
        return True


def _hex_to_rgba(hex_color: str, alpha: float = 1.0) -> str:
    """将十六进制颜色（支持 #rgb / #rrggbb）转换为 rgba() 字符串。"""
    try:
        alpha = float(alpha)
    except (TypeError, ValueError):
        alpha = 1.0
    alpha = max(0.0, min(1.0, alpha))
    h = (hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c + c for c in h)
    if len(h) == 6:
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r},{g},{b},{alpha:.2f})"
        except ValueError:
            pass
    return hex_color or "rgba(0,0,0,1)"


def build_qss(p: dict) -> str:
    """v7 全新设计语言 QSS：现代间距 + 圆角 + 层次感 + 品牌色贯穿。"""
    return f"""
    QMainWindow, QDialog {{ background: {p['bg']}; color: {p['text']};
      font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 14px; }}
    QWidget {{ background: {p['bg']}; color: {p['text']};
      font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 14px; }}
    /* 通用背景命中后，纯文本标签保持透明以透出所在卡片的底色 */
    QLabel {{ background: transparent; }}
    /* 右栏 Pannel 宿主（标签条上方区域）与 Dock 标题同色 */
    QDockWidget#questpaneldock > QWidget {{ background: {p['panel']}; }}
    /* v8.16：多会话标签条 */
    QWidget#sessionbarhost {{ background: transparent; }}
    QTabBar#sessiontabs {{ background: transparent; }}
    QTabBar#sessiontabs::tab {{ background: {p['panel']}; color: {p['muted']};
      padding: 4px 12px; border: 1px solid {p['border']}; border-bottom: none;
      border-top-left-radius: 6px; border-top-right-radius: 6px;
      margin-right: 2px; max-width: 170px; }}
    QTabBar#sessiontabs::tab:hover {{ color: {p['text']}; background: {p['hover']}; }}
    QTabBar#sessiontabs::tab:selected {{ color: {p['text']};
      border-bottom: 2px solid {p['accent']}; }}
    QDockWidget {{ color: {p['muted']}; }}
    QDockWidget::title {{ background: {p['panel']}; padding: 6px 12px; color: {p['muted']};
      border-bottom: 1px solid {p['border']}; }}

    QTextEdit, QPlainTextEdit {{ background: {p['editor']}; color: {p['text']};
      border: 1px solid {p['border']}; border-radius: 8px; padding: 6px;
      selection-background-color: {p['sel']}; }}
    QLineEdit {{ background: {p['field']}; color: {p['text']};
      border: 1px solid {p['border']}; border-radius: 8px; padding: 6px 10px; }}
    QLineEdit:focus {{ border-color: {p['accent']}; }}
    QSpinBox, QDoubleSpinBox, QComboBox {{ background: {p['field']}; color: {p['text']};
      border: 1px solid {p['border']}; border-radius: 6px; padding: 4px 8px; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{ background: {p['panel']}; color: {p['text']};
      border: 1px solid {p['border']}; selection-background-color: {p['sel']}; border-radius: 4px; }}

    QTreeWidget, QListWidget {{ background: {p['panel']}; color: {p['text']}; border: none;
      outline: none; }}
    QTreeWidget::item, QListWidget::item {{ padding: 4px 6px; border-radius: 4px; }}
    QTreeWidget::item:hover, QListWidget::item:hover {{ background: {p['hover']}; }}
    QTreeWidget::item:selected, QListWidget::item:selected {{ background: {p['sel']}; }}

    QTabWidget::pane {{ border: none; border-top: 1px solid {p['border']}; background: {p['bg']}; }}
    QTabBar::tab {{ background: transparent; color: {p['muted']}; padding: 8px 16px;
      border: none; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {p['text']}; border-bottom-color: {p['accent']}; }}
    QTabBar::tab:hover:!selected {{ color: {p['text']}; background: {p['hover']}; }}

    QPushButton {{ background: {p['btn']}; color: {p['text']}; border: 1px solid {p['border']};
      border-radius: 8px; padding: 7px 16px; }}
    QPushButton:hover {{ background: {p['btn_hover']}; border-color: {p['accent']}; }}
    QPushButton:pressed {{ background: {p['sel']}; }}
    QPushButton:disabled {{ color: {p['faint']}; }}
    QPushButton#primary {{ background: {p['accent']}; color: #ffffff; border: none;
      font-weight: 600; border-radius: 8px; }}
    QPushButton#primary:hover {{ background: {p['accent']}; }}
    QPushButton#primary:disabled {{ background: {p['btn']}; color: {p['faint']}; }}

    QToolButton {{ background: transparent; color: {p['muted']}; border: none;
      border-radius: 6px; padding: 6px; font-size: 16px; }}
    QToolButton:hover {{ background: {p['activity_hover']}; color: {p['text']}; }}
    QToolButton:checked {{ background: {p['accent_dim']}; color: {p['accent']}; }}

    QMenuBar {{ background: {p['panel']}; color: {p['text']};
      border-bottom: 1px solid {p['border']}; }}
    QMenuBar::item {{ padding: 6px 12px; background: transparent; border-radius: 4px; }}
    QMenuBar::item:selected {{ background: {p['hover']}; }}
    QMenu {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']};
      border-radius: 8px; padding: 4px; }}
    QMenu::item {{ padding: 6px 22px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {p['sel']}; }}
    QMenu::separator {{ height: 1px; background: {p['border']}; margin: 4px 8px; }}

    QStatusBar {{ background: {p['panel']}; color: {p['muted']};
      border-top: 1px solid {p['border']}; }}
    QStatusBar::item {{ border: none; }}
    QToolBar {{ background: {p['activity_bg']}; border: none; border-right: 1px solid {p['border']};
      spacing: 2px; padding: 4px 0; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {p['scrollbar']}; border-radius: 5px;
      min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {p['accent']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {p['scrollbar']}; border-radius: 5px;
      min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QHeaderView::section {{ background: {p['panel']}; color: {p['muted']};
      border: none; border-bottom: 1px solid {p['border']}; padding: 6px 8px;
      font-size: 12px; }}
    QProgressBar {{ background: {p['field']}; border: none; border-radius: 4px;
      text-align: center; color: {p['text']}; max-height: 6px; }}
    QProgressBar::chunk {{ background: {p['accent']}; border-radius: 4px; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {p['border']};
      border-radius: 4px; background: {p['field']}; }}
    QCheckBox::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']}; }}
    QGroupBox {{ border: 1px solid {p['border']}; border-radius: 8px; margin-top: 10px;
      padding-top: 10px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {p['accent']}; }}
    QToolTip {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']};
      border-radius: 6px; padding: 6px 10px; }}
    QMessageBox {{ background: {p['bg']}; }}
    QSplitter::handle {{ background: {p['border']}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QSplitter::handle:vertical {{ height: 1px; }}

    /* ---- v8 Quest 风格面板管理 ---- */
    QToolBar#rightstrip {{ background: {p['activity_bg']}; border-right: none;
      border-left: 1px solid {p['border']}; }}
    #inputcard {{ background: {p['field']}; border: 1px solid {p['border']};
      border-radius: 12px; }}
    #inputcard QPlainTextEdit {{ background: transparent; border: none; padding: 2px; }}
    QPushButton#flatbtn {{ background: transparent; border: none; color: {p['muted']};
      padding: 4px 8px; border-radius: 6px; text-align: left; }}
    QPushButton#flatbtn:hover {{ background: {p['hover']}; color: {p['text']}; }}
    QPushButton#flatbtn:disabled {{ color: {p['faint']}; }}
    QComboBox#flatcombo {{ background: transparent; border: none; color: {p['muted']};
      padding: 4px 6px; border-radius: 6px; }}
    QComboBox#flatcombo:hover {{ background: {p['hover']}; color: {p['text']}; }}
    QWidget#statusrow {{ background: transparent; border-radius: 6px; }}
    QWidget#statusrow:hover {{ background: {p['hover']}; }}
    QLabel[on="true"] {{ color: {p['accent']}; font-weight: 600; }}
    QLabel[on="false"] {{ color: {p['muted']}; }}
    QPushButton#seg {{ background: transparent; border: none; border-radius: 6px;
      padding: 6px 14px; color: {p['muted']}; }}
    QPushButton#seg:checked {{ background: {p['btn_hover']}; color: {p['text']}; }}
    QPushButton#segplus {{ background: {p['btn_hover']}; border: none; border-radius: 13px; }}
    QLabel#moneybadge {{ background: {p['ok']}; color: #ffffff; border-radius: 4px;
      padding: 1px 5px; font-weight: 600; }}
    QLabel#ratio {{ color: {p['muted']}; }}
    QPushButton#sechead {{ background: transparent; border: none; color: {p['muted']};
      text-align: left; padding: 6px 4px; font-weight: 600; }}
    QPushButton#sechead:hover {{ color: {p['text']}; }}
    QLabel#herotitle {{ font-size: 26px; font-weight: 700; color: {p['text']}; }}
    QLabel#herosub {{ color: {p['faint']}; font-family: Consolas, "Cascadia Code", monospace;
      font-size: 12px; }}
    QWidget#modelpopup {{ background: {p['panel']}; border: 1px solid {p['border']};
      border-radius: 10px; }}
    QScrollArea#sumscroll {{ background: transparent; border: none; }}
    QFrame#popsep {{ color: {p['border']}; }}

    /* ---- v8.3 报警只读横幅 ---- */
    QWidget#guardbanner {{ background: {p['hover']}; border: 1px solid {p['err']}; border-radius: 8px; }}
    QLabel#guardtitle {{ color: {p['err']}; font-weight: 700; font-size: 13px; }}
    QLabel#guardreason {{ color: {p['text']}; }}

    /* ---- v8.3 建议条（TRAE CUE 式）---- */
    QWidget#suggestbox {{ background: {p['hover']}; border: 1px solid {p['border']}; border-radius: 8px; }}
    QLabel#suggesthead {{ color: {p['muted']}; font-size: 12px; font-weight: 600; }}

    /* ---- v8.5.6 全局建议面板（TRAE CUE-Pro 式）---- */
    QWidget#globalsuggest {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 10px; }}
    /* v8.15：通用 QWidget{{background}} 会把裸容器刷成窗口底色方块——
       卡片内容区/列表行/轨迹卡必须显式置透明，透出各自容器的底色与高亮 */
    QWidget#gs_stack {{ background: transparent; }}
    QWidget#modelrow {{ background: transparent; }}
    QFrame#tracecard {{ background: transparent; }}
    QLabel#gs_title {{ color: {p['text']}; font-size: 13px; font-weight: 700; }}
    QPushButton#gs_menu {{ background: transparent; border: none; color: {p['muted']}; font-weight: 700; }}
    QPushButton#gs_menu:hover {{ color: {p['text']}; background: {p['hover']}; border-radius: 5px; }}
    QFrame#gs_sep {{ color: {p['border']}; }}
    QLabel#gs_empty {{ color: {p['faint']}; font-size: 12px; padding: 18px 8px; }}
    QLabel#gs_count {{ color: {p['muted']}; font-size: 11px; }}
    QFrame#gs_card {{ background: {p['bg']}; border: 1px solid {p['border']}; border-radius: 8px; }}
    QLabel#gs_tag {{ background: {p['accent_dim']}; color: {p['accent']}; border-radius: 4px;
      padding: 1px 5px; font-size: 10px; font-weight: 600; }}
    QLabel#gs_tag[suggest_type="functional"] {{ background: {p['accent_dim']}; color: {p['accent']}; }}
    QLabel#gs_tag[suggest_type="technical"] {{ background: rgba(34,197,94,0.12); color: {p['ok']}; }}
    QLabel#gs_tag[suggest_type="art"] {{ background: rgba(217,119,6,0.12); color: {p['warn']}; }}
    QLabel#gs_card_title {{ color: {p['text']}; font-size: 12px; font-weight: 600; }}
    QLabel#gs_card_body {{ color: {p['muted']}; font-size: 11px; }}
    QPushButton#gs_adopt {{ background: {p['accent']}; color: #ffffff; border: none;
      border-radius: 5px; padding: 3px 10px; font-size: 11px; font-weight: 600; }}
    QPushButton#gs_adopt:hover {{ background: {_hex_to_rgba(p['accent'], 0.85)}; }}
    QPushButton#gs_dismiss {{ background: transparent; color: {p['muted']}; border: 1px solid {p['border']};
      border-radius: 5px; padding: 3px 10px; font-size: 11px; }}
    QPushButton#gs_dismiss:hover {{ color: {p['text']}; background: {p['hover']}; }}

    /* ---- v8.5.4 参考图三栏工作台 ---- */
    QWidget#questsidebar {{ background: {p['panel']}; border-right: 1px solid {p['border']}; }}
    QLabel#brandmark {{ background: {p['text']}; color: {p['bg']}; border-radius: 7px;
      font-size: 13px; font-weight: 700; }}
    QLabel#brandname {{ color: {p['text']}; font-size: 12px; font-weight: 700; }}
    QLabel#sidebarsection {{ color: {p['faint']}; font-size: 10px; font-weight: 600;
      padding: 4px 8px 2px 8px; }}
    QLabel#sidebarworkspace {{ color: {p['faint']}; font-size: 10px; padding: 4px 8px; }}
    QPushButton#newquest {{ background: {p['bg']}; border: 1px solid {p['border']};
      color: {p['text']}; text-align: left; padding: 9px 11px; border-radius: 8px;
      font-weight: 600; }}
    QPushButton#newquest:hover {{ background: {p['btn_hover']}; border-color: {p['border']}; }}
    QPushButton#navitem {{ background: transparent; border: none; color: {p['muted']};
      text-align: left; padding: 7px 9px; border-radius: 7px; font-size: 12px; }}
    QPushButton#navitem:hover {{ background: {p['hover']}; color: {p['text']}; }}
    QPushButton#navitem[active="true"] {{ background: {p['sel']}; color: {p['text']};
      font-weight: 600; }}
    QPushButton#navitem:disabled {{ color: {p['faint']}; }}
    QFrame#sidebarline {{ color: {p['border']}; }}
    QStackedWidget#workspacestack {{ background: {p['bg']}; }}
    QTextBrowser#conversationview {{ background: {p['bg']}; border: none; padding: 8px; }}
    QDockWidget#questpaneldock {{ background: {p['panel']}; }}
    QDockWidget#questpaneldock::title {{ background: {p['panel']}; color: {p['text']};
      padding: 9px 12px; font-size: 12px; font-weight: 600;
      border-bottom: 1px solid {p['border']}; }}
    QDockWidget#questpaneldock QTabBar::tab {{ padding: 7px 10px; font-size: 11px; }}

    /* ---- v8.5.4 设置页：左导航 + 右侧卡片 ---- */
    QDialog#settingsDialog {{ background: {p['bg']}; }}
    QLabel#settingsTitle {{ color: {p['text']}; font-size: 22px; font-weight: 700; }}
    QLabel#settingsSubtitle {{ color: {p['muted']}; font-size: 12px; }}
    QTabWidget#settingsTabs::pane {{ border: 1px solid {p['border']};
      border-radius: 10px; background: {p['bg']}; }}
    QTabWidget#settingsTabs QTabBar::tab {{ min-width: 150px; text-align: left;
      padding: 10px 14px; border: none; border-left: 2px solid transparent;
      color: {p['muted']}; background: {p['panel']}; }}
    QTabWidget#settingsTabs QTabBar::tab:selected {{ color: {p['text']};
      background: {p['sel']}; border-left-color: {p['accent']}; }}
    QTabWidget#settingsTabs QTabBar::tab:hover:!selected {{ color: {p['text']};
      background: {p['hover']}; }}
    QDialog#settingsDialog QGroupBox {{ background: {p['panel']}; border: 1px solid {p['border']};
      border-radius: 9px; margin-top: 14px; padding: 12px 10px 10px 10px; }}
    """


def chat_css(p: dict) -> str:
    """v7 聊天区富文本样式：全部从色板取色，无硬编码。"""
    return f"""
    body {{ color: {p['text']}; }}
    .user {{ text-align: right; margin: 8px 0; }}
    .user .bubble {{ display: inline-block; background: {p['user_bubble']};
      color: {p['user_bubble_text']}; padding: 9px 14px;
      border-radius: 14px 14px 3px 14px; max-width: 90%; text-align: left; }}
    .ai {{ margin: 10px 0 4px; }}
    .aihead {{ color: {p['ai_head']}; font-weight: 600; margin-bottom: 3px;
      font-size: 13px; }}
    .aitext {{ color: {p['text']}; line-height: 1.65; }}
    .note {{ color: {p['muted']}; font-size: 12px; margin: 4px 0;
      padding: 2px 0; }}
    .tool {{ border: 1px solid {p['tool_border']}; border-radius: 8px;
      padding: 8px 12px; margin: 6px 0; background: {p['tool_bg']};
      font-family: Consolas, "Cascadia Code", monospace; font-size: 12px;
      color: {p['muted']}; }}
    .tool .st {{ color: {p['warn']}; }}
    .tool.done {{ border-color: {p['ok']}; }}
    .tool.err {{ border-color: {p['err']}; }}
    .toolargs {{ color: {p['faint']}; font-size: 11px; }}
    .toolout {{ color: {p['text']}; white-space: pre-wrap; }}
    .tooloutline {{ color: {p['text']}; white-space: pre-wrap; }}
    .guard {{ border: 1px solid {p['border']}; border-left: 3px solid {p['accent']};
      border-radius: 8px; padding: 6px 10px; margin: 6px 0; background: {p['tool_bg']};
      color: {p['muted']}; font-size: 12px; }}
    .quote {{ border-left: 3px solid {p['accent']}; background: {p['tool_bg']};
      padding: 4px 10px; margin: 4px 0; color: {p['muted']}; font-size: 12px;
      border-radius: 0 6px 6px 0; }}
    .msgbtns {{ color: {p['faint']}; font-size: 11px; margin-top: 2px; }}
    .msgbtns a {{ color: {p['muted']}; text-decoration: none; }}
    .msgbtns a:hover {{ color: {p['accent']}; }}
    a {{ color: {p['accent']}; }}
    pre {{ background: {p['editor']}; border: 1px solid {p['border']};
      border-radius: 6px; padding: 8px; overflow-x: auto; }}
    code {{ font-family: Consolas, "Cascadia Code", monospace; }}
    """
