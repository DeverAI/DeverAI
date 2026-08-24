"""v8.10 轨迹详情预览对话框（v8.11 增强：置顶悬浮 + 拖动 + 元素选择引用）。

参考 DeepSeek Harness「详情预览」形态：可自由缩放、可拖动、可置顶悬浮、可选中文字引用，
并可选择单个「元素」（角色/时间/类型/摘要/详情）逐条引用到对话。

零第三方依赖；HTML 渲染走 QTextBrowser + md_to_html，转义严格防注入。

设计要点：
- 非模态（setModal False），允许同时打开多个对比
- 窗口默认 800×640，可拖拽边界缩放，最小 600×400，最大屏幕 95%
- 字号缩放 80/100/120/150%，Ctrl+=/Ctrl+-/Ctrl+0 快捷键
- 标题区可按住拖动移动窗口（_DragFilter 事件过滤器）
- 「置顶」按钮切换 WindowStaysOnTopHint（悬浮在其它窗口之上）
- 内容按「元素」分块，每块带「引用」锚点，逐元素引用（anchorClicked 信号）
- 选区右键菜单「引用选区到对话」走 quote_requested 信号
- 关闭时自动销毁（WA_DeleteOnClose），不留滞留实例
"""
from __future__ import annotations

import html

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QObject, QEvent, QPoint
from PyQt6.QtGui import QKeySequence, QAction
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser,
    QToolBar, QMenu, QApplication, QFrame,
)

from .md import md_to_html


_ROLE_META = {
    "system":    {"label": "SYSTEM",    "color": "#7c3aed", "bg": "rgba(124,58,237,0.10)"},
    "context":   {"label": "CONTEXT",   "color": "#0891b2", "bg": "rgba(8,145,178,0.10)"},
    "user":      {"label": "USER",      "color": "#2563eb", "bg": "rgba(37,99,235,0.10)"},
    "assistant": {"label": "ASSISTANT", "color": "#111827", "bg": "rgba(17,24,39,0.06)"},
    "tool":      {"label": "TOOL",      "color": "#ca8a04", "bg": "rgba(202,138,4,0.10)"},
}

_ZOOM_LEVELS = [80, 100, 120, 150]
_DEFAULT_ZOOM = 100


def _esc(s) -> str:
    return html.escape(str(s or ""))


def _fmt_time(ts) -> str:
    from datetime import datetime
    try:
        dt = datetime.fromtimestamp(float(ts))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


class _DragFilter(QObject):
    """让指定控件区域按住左键拖动整个对话框窗口（悬浮窗拖动）。

    - 偏移用 pos()（客户区左上角），与 move() 坐标一致，避免带原生标题栏时首动跳变；
    - 拖动位置做屏幕 clamp，保证标题/关闭按钮至少部分留在可视区（防拖出丢失）；
    - Leave/Hide/FocusOut/无按键时复位，防"粘住"。
    """

    def __init__(self, win: QDialog):
        super().__init__()
        self._win = win
        self._offset = None

    def _move_within(self, p: QPoint):
        win = self._win
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            keep_w = min(win.width(), 80)
            keep_h = min(win.height(), 48)
            x = max(geo.left() - win.width() + keep_w, min(p.x(), geo.right() - keep_w))
            y = max(geo.top() - win.height() + keep_h, min(p.y(), geo.bottom() - keep_h))
        else:
            x, y = p.x(), p.y()
        win.move(x, y)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
            self._offset = ev.globalPosition().toPoint() - self._win.pos()
            return True
        if t == QEvent.Type.MouseMove and self._offset is not None:
            if ev.buttons() & Qt.MouseButton.LeftButton:
                self._move_within(ev.globalPosition().toPoint() - self._offset)
            else:
                self._offset = None
            return True
        if t == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.LeftButton:
            self._offset = None
            return True
        if t in (QEvent.Type.Leave, QEvent.Type.Hide, QEvent.Type.FocusOut):
            self._offset = None
            return False
        return False


class TracePreviewDialog(QDialog):
    """轨迹详情预览对话框。

    信号：
      quote_requested(str)  选中文字/元素点击「引用」时发出（已带「[标签] 内容」前缀）
    """

    quote_requested = pyqtSignal(str)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("tracepreview")
        self.setWindowTitle(f"轨迹详情 — {_ROLE_META.get(item.get('role', 'assistant'), _ROLE_META['assistant'])['label']}")
        self.setModal(False)
        # P3-2 修复：关闭即销毁，避免 _previews 列表堆积隐藏实例
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        # 窗口尺寸：默认 800×640，限制范围
        self.setMinimumSize(QSize(600, 400))
        screen = QApplication.primaryScreen()
        if screen is not None:
            max_w = int(screen.size().width() * 0.95)
            max_h = int(screen.size().height() * 0.95)
            self.setMaximumSize(QSize(max_w, max_h))
        self.resize(800, 640)
        self._item = item
        self._pinned = False
        self._zoom_idx = _ZOOM_LEVELS.index(_DEFAULT_ZOOM) if _DEFAULT_ZOOM in _ZOOM_LEVELS else 1

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # 顶部标题区（可拖动移动窗口）
        self._head = QFrame()
        self._head.setObjectName("tracepreview_head")
        self._head.setCursor(Qt.CursorShape.SizeAllCursor)
        self._head.setToolTip("按住此处拖动移动窗口")
        meta = _ROLE_META.get(item.get("role", "assistant"), _ROLE_META["assistant"])
        head_lay = QHBoxLayout(self._head)
        head_lay.setContentsMargins(6, 4, 6, 4)
        head_lay.setSpacing(10)
        role_lbl = QLabel(meta["label"])
        role_lbl.setStyleSheet(
            f"color:{meta['color']};background:{meta['bg']};"
            f"padding:2px 9px;border-radius:10px;font-size:11px;font-weight:700;"
        )
        head_lay.addWidget(role_lbl)
        tm = item.get("time")
        if tm:
            time_lbl = QLabel(_fmt_time(tm))
            time_lbl.setStyleSheet("color: gray; font-family: Consolas, monospace; font-size: 11px;")
            head_lay.addWidget(time_lbl)
        head_lay.addStretch(1)
        drag_hint = QLabel("拖动移动")
        drag_hint.setStyleSheet("color: #9ca3af; font-size: 11px;")
        head_lay.addWidget(drag_hint)
        zoom_lbl = QLabel(f"{_ZOOM_LEVELS[self._zoom_idx]}%")
        zoom_lbl.setObjectName("tracepreview_zoom")
        zoom_lbl.setStyleSheet("color: gray; font-size: 11px; min-width: 44px;")
        head_lay.addWidget(zoom_lbl)
        v.addWidget(self._head)

        # 安装拖动过滤器（标题区各子控件均可拖动）
        self._drag_filter = _DragFilter(self)
        for w in (self._head, role_lbl, zoom_lbl, drag_hint):
            w.installEventFilter(self._drag_filter)
        if tm:
            time_lbl.installEventFilter(self._drag_filter)

        # 工具条
        tb = QToolBar()
        tb.setIconSize(QSize(14, 14))
        tb.setMovable(False)
        tb.setObjectName("tracepreview_tb")
        self._act_copy = QAction("复制全部", self)
        self._act_copy.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self._act_copy.triggered.connect(self._copy_all)
        tb.addAction(self._act_copy)
        self._act_quote = QAction("引用全部到对话", self)
        self._act_quote.triggered.connect(self._quote_all)
        tb.addAction(self._act_quote)
        tb.addSeparator()
        self._act_pin = QAction("置顶", self)
        self._act_pin.setCheckable(True)
        self._act_pin.setToolTip("悬浮在其它窗口之上")
        self._act_pin.toggled.connect(self._toggle_pin)
        tb.addAction(self._act_pin)
        tb.addSeparator()
        self._act_zoom_out = QAction("缩小", self)
        self._act_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        self._act_zoom_out.triggered.connect(self._zoom_out)
        tb.addAction(self._act_zoom_out)
        self._act_zoom_in = QAction("放大", self)
        self._act_zoom_in.setShortcut(QKeySequence("Ctrl+="))
        self._act_zoom_in.triggered.connect(self._zoom_in)
        tb.addAction(self._act_zoom_in)
        self._act_zoom_reset = QAction("重置", self)
        self._act_zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        self._act_zoom_reset.triggered.connect(self._zoom_reset)
        tb.addAction(self._act_zoom_reset)
        tb.addSeparator()
        self._act_close = QAction("关闭", self)
        self._act_close.setShortcut(QKeySequence("Ctrl+W"))
        self._act_close.triggered.connect(self.close)
        tb.addAction(self._act_close)
        v.addWidget(tb)

        # 内容区：元素分块（角色/时间/类型/摘要/详情）
        self._view = QTextBrowser()
        self._view.setObjectName("tracepreview_view")
        self._view.setOpenExternalLinks(False)
        # 阻止浏览器默认导航，仅触发 anchorClicked（元素「引用」锚点）
        self._view.setOpenLinks(False)
        # 给 view 设置 contextMenuPolicy，自定义右键菜单
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._on_context_menu)
        self._view.anchorClicked.connect(self._on_anchor)
        v.addWidget(self._view, 1)

        self._render()

    # ---------- 元素模型 ----------
    def _elements(self) -> list[tuple[str, str, str]]:
        """返回 [(key, 标签, 内容)]，只保留有内容的元素。"""
        item = self._item
        meta = _ROLE_META.get(item.get("role", "assistant"), _ROLE_META["assistant"])
        els: list[tuple[str, str, str]] = []
        els.append(("role", "角色", meta["label"]))
        tm = item.get("time")
        if tm:
            fmt = _fmt_time(tm)
            if fmt:
                els.append(("time", "时间", fmt))
        kind = str(item.get("kind", "") or "").strip()
        if kind:
            els.append(("kind", "类型", kind))
        summary = str(item.get("summary", "") or "").strip()
        if summary:
            els.append(("summary", "摘要", summary))
        detail = str(item.get("detail", "") or "").strip()
        if detail:
            els.append(("detail", "详情", detail))
        return els

    def _element_text(self, key: str) -> str:
        """返回某元素的「[标签] 内容」引用文本。"""
        for k, label, text in self._elements():
            if k == key:
                return f"[{label}] {text}"
        return ""

    # ---------- 渲染 ----------
    def _render(self):
        item = self._item
        meta = _ROLE_META.get(item.get("role", "assistant"), _ROLE_META["assistant"])
        zoom = _ZOOM_LEVELS[self._zoom_idx]
        zoom_lbl = self.findChild(QLabel, "tracepreview_zoom")
        if zoom_lbl:
            zoom_lbl.setText(f"{zoom}%")
        font_size = max(10, int(13 * zoom / 100))

        blocks = []
        for key, label, text in self._elements():
            # 详情走 markdown 渲染（md_to_html 已转义/清洗，不执行脚本）；其余纯文本转义
            body = md_to_html(text) if key == "detail" else _esc(text)
            blocks.append(f"""
              <div style='margin-bottom:12px;'>
                <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;'>
                  <span style='font-weight:600; color:{meta['color']}; font-size:{int(font_size*0.95)}px;'>{_esc(label)}</span>
                  <a href='quote:{key}' style='color:#2563eb; text-decoration:none; font-size:11px; font-family: "Microsoft YaHei","Segoe UI",sans-serif;'>引用</a>
                </div>
                <div style='background:{meta['bg']}; padding:8px 12px; border-radius:8px; border:1px solid #e5e7eb; border-left:3px solid {meta['color']}; white-space:pre-wrap; word-break:break-word;'>
                  {body}
                </div>
              </div>
            """)

        html_doc = f"""
        <html><body style='font-family: "Microsoft YaHei", "Segoe UI", sans-serif; font-size: {font_size}px; color: #1f2937;'>
          <div style='padding: 6px 4px;'>
            {''.join(blocks)}
          </div>
        </body></html>
        """
        self._view.setHtml(html_doc)

    # ---------- 置顶 / 拖动 ----------
    def _toggle_pin(self, on: bool):
        """切换置顶（悬浮）状态：WindowStaysOnTopHint。"""
        self._pinned = bool(on)
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._pinned)
        if was_visible:
            self.show()
            self.raise_()
        if hasattr(self, "_act_pin"):
            self._act_pin.setText("已置顶" if self._pinned else "置顶")

    def is_pinned(self) -> bool:
        return self._pinned

    # ---------- 缩放 ----------
    def _zoom_in(self):
        if self._zoom_idx < len(_ZOOM_LEVELS) - 1:
            self._zoom_idx += 1
            self._render()

    def _zoom_out(self):
        if self._zoom_idx > 0:
            self._zoom_idx -= 1
            self._render()

    def _zoom_reset(self):
        self._zoom_idx = _ZOOM_LEVELS.index(_DEFAULT_ZOOM) if _DEFAULT_ZOOM in _ZOOM_LEVELS else 1
        self._render()

    # ---------- 文本操作 ----------
    def _selected_text(self) -> str:
        cursor = self._view.textCursor()
        return cursor.selectedText().replace("\u2029", "\n").strip()

    def _copy_all(self):
        # 优先复制选区，没有选区复制全部
        sel = self._selected_text()
        text = sel or self._build_full_text()
        QApplication.clipboard().setText(text)

    def _quote_all(self):
        sel = self._selected_text()
        text = sel or self._build_full_text()
        if text:
            self.quote_requested.emit(text)

    def _build_full_text(self) -> str:
        parts = [f"[{label}] {text}" for _, label, text in self._elements()]
        return "\n".join(parts).strip()

    # ---------- 元素引用锚点 ----------
    def _on_anchor(self, url):
        s = url.toString()
        if s.startswith("quote:"):
            key = s[len("quote:"):]
            text = self._element_text(key)
            if text:
                self.quote_requested.emit(text)

    # ---------- 右键菜单 ----------
    def _on_context_menu(self, pos):
        menu = QMenu(self._view)
        sel = self._selected_text()
        act_quote_sel = menu.addAction("引用选区到对话")
        act_quote_sel.setEnabled(bool(sel))
        act_quote_sel.triggered.connect(lambda: self.quote_requested.emit(sel) if sel else None)
        menu.addSeparator()
        act_copy_sel = menu.addAction("复制选区")
        act_copy_sel.setEnabled(bool(sel))
        act_copy_sel.triggered.connect(lambda: QApplication.clipboard().setText(sel) if sel else None)
        act_copy_all = menu.addAction("复制全部")
        act_copy_all.triggered.connect(self._copy_all)
        menu.addSeparator()
        act_quote_all = menu.addAction("引用全部到对话")
        act_quote_all.triggered.connect(self._quote_all)
        menu.exec(self._view.viewport().mapToGlobal(pos))
