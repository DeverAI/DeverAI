"""DeverAI v8 Quest 风格面板管理组件。

复现现代 AI 编程工具的面板管理模式：
- HeroWidget：聊天区欢迎英雄区（圆形 logo + 大标题 + 工作区副标题）
- Section：可折叠分区（概览面板 Progress/Artifacts/References）
- SummaryPanel：概览面板（嵌入健康仪表盘 + 资产产物 + 引用列表）
- StatusRow：状态开关行（左名称 / 右状态，点击切换，NO EMOJI）
- ModelPickerPopup：模型选择弹窗（状态行 + Pilot/Copilot 分段 + 带价比徽章的模型列表）

全部颜色由主题 QSS 驱动（objectName/动态属性选择器），无硬编码。
"""
from __future__ import annotations

import html

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QPointF, QRect
from PyQt6.QtGui import QColor, QPainter, QPixmap, QMouseEvent, QFont, QPolygon
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QTextBrowser, QToolButton, QMenu,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QApplication,
)

from . import models as models_mod
from .config import get_config
from .icons import icon as svg_icon


def _esc(s) -> str:
    return html.escape(str(s or ""))


class QuestSidebar(QWidget):
    """参考图式左侧 Quest 导航。

    这里只负责导航状态，不直接清空历史或修改文件；具体动作由主窗口接线，
    避免一个视觉按钮隐式触发不可逆操作。
    """

    new_quest_requested = pyqtSignal()
    chat_requested = pyqtSignal()
    trace_requested = pyqtSignal()   # v8.8 工作轨迹
    editor_requested = pyqtSignal()
    files_requested = pyqtSignal()
    settings_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("questsidebar")
        self.setMinimumWidth(220)
        self.setMaximumWidth(286)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(5)

        brand = QHBoxLayout()
        mark = QLabel("D")
        mark.setObjectName("brandmark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(26, 26)
        name = QLabel("DEVERAI")
        name.setObjectName("brandname")
        brand.addWidget(mark)
        brand.addWidget(name)
        brand.addStretch(1)
        root.addLayout(brand)
        root.addSpacing(8)

        self.new_btn = QPushButton("＋  New Task")
        self.new_btn.setObjectName("newquest")
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.clicked.connect(self.new_quest_requested.emit)
        root.addWidget(self.new_btn)

        root.addSpacing(12)
        root.addWidget(self._section_label("TASKS"))
        self.quest_btn = self._nav_button("Hands-off workspace", True)
        self.quest_btn.clicked.connect(self.chat_requested.emit)
        root.addWidget(self.quest_btn)

        root.addSpacing(10)
        root.addWidget(self._section_label("WORKSPACE"))
        self.editor_btn = self._nav_button("Editor")
        self.editor_btn.clicked.connect(self.editor_requested.emit)
        self.files_btn = self._nav_button("Files")
        self.files_btn.clicked.connect(self.files_requested.emit)
        root.addWidget(self.editor_btn)
        root.addWidget(self.files_btn)

        root.addSpacing(10)
        root.addWidget(self._section_label("CHATS"))
        self.recent_btn = self._nav_button("Current conversation")
        self.recent_btn.clicked.connect(self.chat_requested.emit)
        root.addWidget(self.recent_btn)

        root.addSpacing(10)
        root.addWidget(self._section_label("TRACE"))
        self.trace_btn = self._nav_button("工作轨迹")
        self.trace_btn.clicked.connect(self.trace_requested.emit)
        root.addWidget(self.trace_btn)
        root.addStretch(1)

        for label in ("Schedule", "Harness", "Knowledge", "Marketplace"):
            b = self._nav_button(label)
            b.setEnabled(False)
            root.addWidget(b)

        line = QFrame()
        line.setObjectName("sidebarline")
        line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(line)
        self.settings_btn = self._nav_button("Settings")
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        root.addWidget(self.settings_btn)
        self.workspace_lbl = QLabel("Workspace · not set")
        self.workspace_lbl.setObjectName("sidebarworkspace")
        self.workspace_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.workspace_lbl)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sidebarsection")
        return label

    @staticmethod
    def _nav_button(text: str, active: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("navitem")
        button.setProperty("active", "true" if active else "false")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def set_workspace(self, name: str):
        self.workspace_lbl.setText(f"Workspace · {name or 'not set'}")

    def set_page(self, page: str):
        for button, active in (
            (self.quest_btn, page == "task"),
            (self.editor_btn, page == "editor"),
            (self.recent_btn, page == "chat"),
            (self.trace_btn, page == "trace"),
        ):
            button.setProperty("active", "true" if active else "false")
            style = button.style()
            if style is not None:
                style.unpolish(button)
                style.polish(button)


# ---------------------------------------------------------------------------
# 英雄欢迎区
# ---------------------------------------------------------------------------
class HeroWidget(QWidget):
    """聊天区空态欢迎区：圆形 logo + 大标题 + 工作区副标题。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 18, 0, 18)
        v.setSpacing(8)
        v.addStretch(1)
        self.logo = QLabel()
        self.logo.setFixedSize(72, 72)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        self.title = QLabel("Hands-off workspace")
        self.title.setObjectName("herotitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.title)
        self.sub = QLabel("工作区: 未设置")
        self.sub.setObjectName("herosub")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.sub)
        v.addStretch(1)
        self._p: dict = {}

    def set_sub(self, txt: str):
        self.sub.setText(txt)

    def update_palette(self, p: dict):
        """主题切换时重绘 logo（圆底 + atom 图标）。"""
        self._p = p
        pm = QPixmap(72, 72)
        pm.fill(Qt.GlobalColor.transparent)
        pa = QPainter(pm)
        pa.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pa.setPen(Qt.PenStyle.NoPen)
        # QColor 不解析 CSS 的 rgba(...) 字符串；使用色板中的十六进制 field，
        # 否则无效 QColor 会退化成实心黑圆。
        circle = QColor(p.get("field", p.get("border", "#eef1f5")))
        if not circle.isValid():
            circle = QColor("#eef1f5")
        pa.setBrush(circle)
        pa.drawEllipse(2, 2, 68, 68)
        pa.end()
        ic = svg_icon("atom", 34, QColor(p.get("faint", "#64748b"))).pixmap(34, 34)
        pa = QPainter(pm)
        pa.drawPixmap(19, 19, ic)
        pa.end()
        self.logo.setPixmap(pm)


# ---------------------------------------------------------------------------
# 可折叠分区
# ---------------------------------------------------------------------------
class Section(QWidget):
    """标题头（▾/▸ + 名称）+ 内容部件，点击标题折叠。"""

    def __init__(self, title: str, content: QWidget, parent=None, expanded: bool = True):
        super().__init__(parent)
        self._title = title
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self.head = QPushButton()
        self.head.setObjectName("sechead")
        self.head.setCursor(Qt.CursorShape.PointingHandCursor)
        self.head.clicked.connect(lambda: self.set_expanded(not self.content.isVisible()))
        v.addWidget(self.head)
        self.content = content
        v.addWidget(content)
        self.set_expanded(expanded)

    def set_expanded(self, on: bool):
        self.content.setVisible(on)
        self.head.setText(("▾ " if on else "▸ ") + self._title)


# ---------------------------------------------------------------------------
# 状态开关行
# ---------------------------------------------------------------------------
class StatusRow(QWidget):
    """左名称 / 右状态的开关行。API 兼容 QPushButton 的 setChecked/isChecked/toggled。"""

    toggled = pyqtSignal(bool)

    def __init__(self, name: str, on_txt: str = "on", off_txt: str = "off",
                 tip: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("statusrow")
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._on_txt, self._off_txt = on_txt, off_txt
        self._checked = False
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 5, 8, 5)
        h.setSpacing(6)
        self.name_lbl = QLabel(name)
        self.state_lbl = QLabel(off_txt)
        self.state_lbl.setProperty("on", "false")
        h.addWidget(self.name_lbl)
        h.addStretch(1)
        h.addWidget(self.state_lbl)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, v):
        self._checked = bool(v)
        self.state_lbl.setText(self._on_txt if self._checked else self._off_txt)
        self.state_lbl.setProperty("on", "true" if self._checked else "false")
        st = self.state_lbl.style()
        if st is not None:
            st.unpolish(self.state_lbl)
            st.polish(self.state_lbl)

    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(ev)
            return
        self.setChecked(not self._checked)
        self.toggled.emit(self._checked)


# ---------------------------------------------------------------------------
# 模型选择弹窗
# ---------------------------------------------------------------------------
class ModelPickerPopup(QWidget):
    """状态行 + Pilot/Copilot 分段 + 模型列表（价比徽章）。

    信号：model_picked(role, model_id) / manage_requested()
    """

    model_picked = pyqtSignal(str, str)
    manage_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("modelpopup")
        self.setFixedWidth(330)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 8, 6, 8)
        v.setSpacing(4)

        # 状态开关行区（外部行经 add_status_row 注入到顶部）
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(0)
        self._rows_box.setContentsMargins(0, 0, 0, 4)
        self.row_traffic = StatusRow("流量节省", "on", "off",
                                     "开启后同步队列挂起，减少网络流量")
        self.row_lowram = StatusRow("低内存", "on", "off",
                                    "少渲染少动画少进程，强制串行")
        self._rows_box.addWidget(self.row_traffic)
        self._rows_box.addWidget(self.row_lowram)
        v.addLayout(self._rows_box)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("popsep")
        v.addWidget(sep)

        # Pilot / Copilot 分段 + 管理按钮
        seg = QHBoxLayout()
        seg.setSpacing(2)
        self.btn_pilot = QPushButton("Pilot 主模型")
        self.btn_copilot = QPushButton("Copilot")
        for b in (self.btn_pilot, self.btn_copilot):
            b.setObjectName("seg")
            b.setCheckable(True)
        self.btn_pilot.setChecked(True)
        self.btn_pilot.clicked.connect(lambda: self._set_role("pilot"))
        self.btn_copilot.clicked.connect(lambda: self._set_role("copilot"))
        self.btn_plus = QPushButton()
        self.btn_plus.setObjectName("segplus")
        self.btn_plus.setIcon(svg_icon("plus", 14))
        self.btn_plus.setFixedSize(26, 26)
        self.btn_plus.setToolTip("模型注册表管理")
        # 先关弹窗再开模态设置框，避免 Qt.WindowType.Popup 压住对话框
        self.btn_plus.clicked.connect(lambda: (self.hide(), self.manage_requested.emit()))
        seg.addWidget(self.btn_pilot)
        seg.addWidget(self.btn_copilot)
        seg.addStretch(1)
        seg.addWidget(self.btn_plus)
        v.addLayout(seg)

        # 模型列表
        self.listw = QListWidget()
        self.listw.setFixedHeight(240)
        self.listw.itemClicked.connect(self._on_pick)
        v.addWidget(self.listw)
        self._role = "pilot"

    def add_status_row(self, row: StatusRow):
        """外部状态行注入到状态区顶部。"""
        self._rows_box.insertWidget(0, row)

    def _set_role(self, role: str):
        self._role = role
        self.btn_pilot.setChecked(role == "pilot")
        self.btn_copilot.setChecked(role == "copilot")
        self._highlight_current()

    def prepare(self):
        """显示前重建模型列表与选中态。"""
        cfg = get_config()
        models = [m for m in models_mod.load_models() if m.kind != "embedding"]
        ids = {m.id for m in models}
        if cfg.model and cfg.model not in ids:
            models.insert(0, models_mod.ModelInfo(id=cfg.model, name=cfg.model))
        if cfg.copilot_model and cfg.copilot_model not in ids:
            models.append(models_mod.ModelInfo(id=cfg.copilot_model, name=cfg.copilot_model))
        self.listw.clear()
        for m in models:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(6)
            nm = QLabel(m.display_name())
            nm.setToolTip(m.display_name())
            h.addWidget(nm)
            h.addStretch(1)
            pin = float((m.prices or {}).get("in") or 0)
            pout = float((m.prices or {}).get("out") or 0)
            if pin > 0 or pout > 0:
                badge = QLabel("$")
                badge.setObjectName("moneybadge")
                h.addWidget(badge)
                ratio = QLabel(f"{pin:g}:{pout:g}")
                ratio.setObjectName("ratio")
                h.addWidget(ratio)
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, m.id)
            self.listw.addItem(it)
            self.listw.setItemWidget(it, w)
        self._highlight_current()

    def _highlight_current(self):
        cfg = get_config()
        cur = cfg.model if self._role == "pilot" else (cfg.copilot_model or cfg.model)
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == cur:
                self.listw.setCurrentItem(it)
                break

    def _on_pick(self, item):
        mid = item.data(Qt.ItemDataRole.UserRole)
        if mid:
            self.model_picked.emit(self._role, str(mid))
        self.hide()


# ---------------------------------------------------------------------------
# 全局建议提出系统（TRAE CUE-Pro 式）
# ---------------------------------------------------------------------------
_TYPE_LABELS = {
    "functional": "功能",
    "technical": "技术",
    "art": "视觉",
}


class GlobalSuggestPanel(QWidget):
    """TRAE CUE-Pro 式全局建议面板。

    空态显示“暂无编辑建议，请先进行编码操作…”；
    有建议时展示卡片列表；底部显示“已处理 N/M 个变更点”。
    """

    suggest_adopted = pyqtSignal(dict, str)
    suggest_dismissed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("globalsuggest")
        self._items: list = []
        self._processed = 0
        self._total = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        head = QHBoxLayout()
        head.setContentsMargins(8, 6, 6, 6)
        head.setSpacing(6)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(svg_icon("bot", 16).pixmap(16, 16))
        icon_lbl.setToolTip("Suggestions")
        title_lbl = QLabel("Suggestions")
        title_lbl.setObjectName("gs_title")
        menu_btn = QPushButton("···")
        menu_btn.setObjectName("gs_menu")
        menu_btn.setFixedSize(22, 22)
        menu_btn.setToolTip("更多")
        menu_btn.clicked.connect(self.clear)
        head.addWidget(icon_lbl)
        head.addWidget(title_lbl)
        head.addStretch(1)
        head.addWidget(menu_btn)
        root.addLayout(head)

        sep = QFrame()
        sep.setObjectName("gs_sep")
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # 内容区
        self.stack = QWidget()
        self.stack_lay = QVBoxLayout(self.stack)
        self.stack_lay.setContentsMargins(8, 8, 8, 8)
        self.stack_lay.setSpacing(6)
        self.empty_lbl = QLabel("暂无编辑建议，请先进行编码操作…")
        self.empty_lbl.setObjectName("gs_empty")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setWordWrap(True)
        self.stack_lay.addWidget(self.empty_lbl)
        self.stack_lay.addStretch(1)
        root.addWidget(self.stack, 1)

        # 底部计数条
        foot = QHBoxLayout()
        foot.setContentsMargins(8, 6, 8, 6)
        foot.setSpacing(6)
        self.count_icon = QLabel()
        self.count_icon.setPixmap(svg_icon("chart", 14).pixmap(14, 14))
        self.count_lbl = QLabel("已处理 0/0 个变更点")
        self.count_lbl.setObjectName("gs_count")
        foot.addWidget(self.count_icon)
        foot.addWidget(self.count_lbl)
        foot.addStretch(1)
        root.addLayout(foot)

    def clear(self):
        """清空建议列表并重置为空态。"""
        self._items = []
        self._processed = 0
        self._total = 0
        self._rebuild()
        self._update_count()

    def set_counts(self, processed: int, total: int):
        """设置已处理/总数计数。"""
        self._processed = max(0, int(processed))
        self._total = max(0, int(total))
        self._update_count()

    def set_suggestions(self, items: list):
        """刷新建议列表。新一轮建议计数归零。"""
        self._items = list(items or [])
        self._total = len(self._items)
        self._processed = 0
        self._rebuild()
        self._update_count()

    @property
    def counts(self) -> tuple[int, int]:
        """返回 (processed, total) 计数。"""
        return self._processed, self._total

    def remove_suggestion(self, item: dict) -> bool:
        """按对象身份移除指定建议，返回是否成功移除。"""
        try:
            idx = next(i for i, x in enumerate(self._items) if x is item)
        except StopIteration:
            return False
        self._items.pop(idx)
        self._total = len(self._items)
        self._processed = min(self._processed, self._total)
        self._rebuild()
        self._update_count()
        return True

    def _update_count(self):
        self.count_lbl.setText(f"已处理 {self._processed}/{self._total} 个变更点")

    def _rebuild(self):
        """重建内容区：空态或卡片列表。"""
        self.empty_lbl = None
        # 清空现有内容（widget 与 spacer 均释放）
        while self.stack_lay.count():
            it = self.stack_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
            sp = it.spacerItem()
            if sp is not None:
                del sp
        if not self._items:
            self.empty_lbl = QLabel("暂无编辑建议，请先进行编码操作…")
            self.empty_lbl.setObjectName("gs_empty")
            self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.empty_lbl.setWordWrap(True)
            self.stack_lay.addWidget(self.empty_lbl)
            self.stack_lay.addStretch(1)
            return

        for it in self._items:
            card = self._build_card(it)
            self.stack_lay.addWidget(card)
        self.stack_lay.addStretch(1)

    def _build_card(self, item: dict) -> QWidget:
        """单个建议卡片。"""
        card = QFrame()
        card.setObjectName("gs_card")
        v = QVBoxLayout(card)
        v.setContentsMargins(8, 7, 8, 7)
        v.setSpacing(5)

        # 类型标签 + 标题
        top = QHBoxLayout()
        top.setSpacing(6)
        typ = str(item.get("type", "functional")).lower()
        tag = QLabel(_TYPE_LABELS.get(typ, typ[:1].upper() + typ[1:]))
        tag.setObjectName("gs_tag")
        tag.setProperty("suggest_type", typ)
        self._refresh_tag_style(tag)
        title = QLabel(str(item.get("title", "")))
        title.setObjectName("gs_card_title")
        title.setWordWrap(True)
        top.addWidget(tag)
        top.addWidget(title, 1)
        v.addLayout(top)

        # 详情
        detail = str(item.get("detail", ""))
        if detail:
            body = QLabel(detail[:120] + ("…" if len(detail) > 120 else ""))
            body.setObjectName("gs_card_body")
            body.setWordWrap(True)
            v.addWidget(body)

        # 操作按钮
        row = QHBoxLayout()
        row.setSpacing(6)
        adopt = QPushButton("采纳")
        adopt.setObjectName("gs_adopt")
        adopt.setCursor(Qt.CursorShape.PointingHandCursor)
        adopt.setProperty("item", item)
        adopt.clicked.connect(lambda _=False, it=item, d=detail:
                              self.suggest_adopted.emit(it, d))
        dismiss = QPushButton("忽略")
        dismiss.setObjectName("gs_dismiss")
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setProperty("item", item)
        dismiss.clicked.connect(lambda _=False, i=item: self.suggest_dismissed.emit(i))
        row.addWidget(adopt)
        row.addWidget(dismiss)
        row.addStretch(1)
        v.addLayout(row)
        return card

    @staticmethod
    def _refresh_tag_style(tag: QLabel):
        """设置类型标签动态属性后刷新样式。"""
        st = tag.style()
        if st is not None:
            st.unpolish(tag)
            st.polish(tag)

    def mark_processed(self) -> int:
        """标记一条建议为已处理（由外部在采纳后调用），返回新的 processed 值。"""
        self._processed = min(self._total, self._processed + 1)
        self._update_count()
        return self._processed


# ---------------------------------------------------------------------------
# 概览面板（Summary）
# ---------------------------------------------------------------------------
class SummaryPanel(QWidget):
    """右栏概览：Suggestions（全局建议）/ Progress（健康仪表盘）/ Artifacts / References。"""

    def __init__(self, health_widget: QWidget, vault, parent=None):
        super().__init__(parent)
        self.vault = vault
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("sumscroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        self.suggest_panel = GlobalSuggestPanel()
        self.sec_suggest = Section("Suggestions", self.suggest_panel, expanded=True)
        v.addWidget(self.sec_suggest)
        self.sec_progress = Section("Progress 进度", health_widget)
        v.addWidget(self.sec_progress)
        self.art_list = QListWidget()
        self.art_list.setMaximumHeight(170)
        self.sec_art = Section("Artifacts 产物", self.art_list)
        v.addWidget(self.sec_art)
        self.ref_list = QListWidget()
        self.ref_list.setMaximumHeight(170)
        self.sec_ref = Section("References 引用", self.ref_list)
        v.addWidget(self.sec_ref)
        v.addStretch(1)
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def refresh_artifacts(self):
        self.art_list.clear()
        try:
            items = self.vault.list()[:50]
        except Exception:
            items = []
        for a in items:
            self.art_list.addItem(f"{a.get('title', '未命名')} ({a.get('kind', 'code')})")
        if not items:
            self.art_list.addItem("No Artifacts yet")

    def refresh_refs(self, quotes: list):
        self.ref_list.clear()
        for q in quotes or []:
            preview = str(q.get("content", ""))[:60].replace("\n", " ⏎ ")
            self.ref_list.addItem(f"[{q.get('label', '')}] {preview}")
        if not quotes:
            self.ref_list.addItem("No references yet")


# ==========================================================================
# v8.3 报警只读横幅 + 任务管理器四层视图
# ==========================================================================
class GuardBanner(QWidget):
    """报警/只读状态横幅：AI 主动报警（copilot 拦截 / tree 校验失败 / 健康 P0）时，
    停止快照清理、AI 进入只读模式，本横幅出现在 pannel 供用户选择。"""

    restore_clicked = pyqtSignal()   # 用户点"查看快照并恢复"
    dismiss_clicked = pyqtSignal()   # 用户解除只读

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("guardbanner")
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)
        self.title = QLabel("AI 主动报警 — 已停止快照清理，进入只读模式")
        self.title.setObjectName("guardtitle")
        v.addWidget(self.title)
        self.reason = QLabel("")
        self.reason.setObjectName("guardreason")
        self.reason.setWordWrap(True)
        v.addWidget(self.reason)
        row = QHBoxLayout()
        self.btn_restore = QPushButton("查看快照并恢复")
        self.btn_restore.setObjectName("primary")
        self.btn_restore.clicked.connect(self.restore_clicked.emit)
        self.btn_dismiss = QPushButton("解除只读")
        self.btn_dismiss.clicked.connect(self.dismiss_clicked.emit)
        row.addWidget(self.btn_restore)
        row.addStretch(1)
        row.addWidget(self.btn_dismiss)
        v.addLayout(row)
        self.hide()

    def show_alert(self, reason: str):
        self.reason.setText(reason)
        self.show()

    def clear_alert(self):
        self.hide()


class TaskManagerPanel(QWidget):
    """任务管理器四层视图（对话压缩）：
    L1 总司令在干的事情 → L2 分派的专家 → L3 专家的小只读 Agent/工具 → L4 思考过程。
    默认只展开当前状态（活跃专家+任务+模型参数+审批表单），其余点击展开。"""

    rollback_requested = pyqtSignal(str)   # round_id

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)
        self.status_lbl = QLabel("状态: 空闲")
        self.status_lbl.setObjectName("herosub")
        v.addWidget(self.status_lbl)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["层级", "内容", "状态"])
        self.tree.setColumnWidth(0, 90)
        self.tree.setColumnWidth(1, 300)
        v.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.btn_rollback = QPushButton("回退到本轮快照")
        self.btn_rollback.clicked.connect(lambda: self.rollback_requested.emit(
            self._current_round))
        row.addWidget(self.btn_rollback)
        row.addStretch(1)
        v.addLayout(row)
        self._current_round = ""

    def set_status(self, text: str):
        self.status_lbl.setText(f"状态: {text}")

    def set_round(self, round_id: str):
        self._current_round = round_id or ""

    def set_state(self, commander: dict = None, experts: list = None,
                  agents: dict = None, thinking: list = None,
                  readonly: bool = False):
        """刷新四层树。默认展开 L1；其他折叠（点击展开）。"""
        self.tree.clear()
        # L1 总司令
        cmdr = commander or {}
        l1 = QTreeWidgetItem(["L1 总司令", cmdr.get("task", "规划中"), cmdr.get("status", "执行中")])
        self.tree.addTopLevelItem(l1)
        # L2 专家
        for e in experts or []:
            l2 = QTreeWidgetItem(l1, [
                "L2 专家", f"{e.get('title','')} · {e.get('model','')}",
                f"{e.get('params','')} {e.get('status','')}"])
            # L3 只读 Agent/工具
            for a in (agents or {}).get(e.get("id", ""), []) or []:
                l3 = QTreeWidgetItem(l2, ["L3 工具", a.get("name", ""), a.get("status", "")])
                for t in (a.get("tools", []) or [])[:8]:
                    QTreeWidgetItem(l3, ["L3 子Agent", str(t), ""])
        # L4 思考过程
        for th in (thinking or [])[:10]:
            QTreeWidgetItem(l1, ["L4 思考", str(th)[:60], ""])
        l1.setExpanded(True)
        if not experts:
            QTreeWidgetItem(l1, ["L2 专家", "（暂无活跃专家）", ""])
        self.tree.expandToDepth(1)


# ==========================================================================
# v8.8 工作轨迹面板（参考 DeepSeek HARNESS 轨迹页）
# ==========================================================================
_TRACE_ROLE_META = {
    "system":    {"label": "SYSTEM",    "color": "#7c3aed", "bg": "rgba(124,58,237,0.10)", "icon": "shield"},
    "context":   {"label": "CONTEXT",   "color": "#0891b2", "bg": "rgba(8,145,178,0.10)",  "icon": "memo"},
    "user":      {"label": "USER",      "color": "#2563eb", "bg": "rgba(37,99,235,0.10)",  "icon": "user"},
    "assistant": {"label": "ASSISTANT", "color": "#111827", "bg": "rgba(17,24,39,0.06)",  "icon": "atom"},
    "tool":      {"label": "TOOL",      "color": "#ca8a04", "bg": "rgba(202,138,4,0.10)",  "icon": "cog"},
}


class TraceTimeline(QWidget):
    """顶部时间线：横向条形图，按角色分色，展示 Duration/Turns/Calls 概览。

    v8.10 增强：
    - 支持手动标记点（marker）+ 区间查询（range）。
    - 鼠标左键单击空白处新增标记点（snap 到最近事件）；右键标记点弹菜单（删除）。
    - 按住左键拖拽时间线划选区间，松手后发出 range_changed 信号。
    - markers 与 range 不持久化，set_items / clear 时一并清空。
    """

    marker_clicked = pyqtSignal(dict)    # marker 被点击（detail 预览用）
    range_changed = pyqtSignal(tuple)    # (t0, t1) 或 (0, 0) 表示清除

    BAR_TOP = 38

    def __init__(self, parent=None, advanced: bool = True):
        super().__init__(parent)
        self.setObjectName("tracetime")
        self.setMinimumHeight(90)
        self.setMaximumHeight(140)
        self._items: list[dict] = []
        self._duration_s = 0
        self._turns = 0
        self._calls = 0
        self._markers: list[dict] = []   # [{time, idx, note}]
        self._range: tuple = (0.0, 0.0)  # (t0, t1)；(0,0) 表示无区间
        self._drag_start: float | None = None
        self._drag_end: float | None = None
        self._advanced: bool = advanced   # False 时禁用标记/区间交互（v8.8 形态）
        self.setMouseTracking(True)

    def set_items(self, items: list[dict], reset_extras: bool = True):
        """更新事件列表。reset_extras=True 时清空标记点与区间（clear/set_history 用），
        reset_extras=False 时保留标记与区间（add_event 流式追加用）。"""
        self._items = list(items or [])
        if reset_extras:
            self._markers = []
            self._range = (0.0, 0.0)
        self._recompute()
        self.update()

    def markers(self) -> list[dict]:
        return list(self._markers)

    def range_(self) -> tuple:
        return self._range

    def set_advanced(self, on: bool):
        """切换高级开关：False 时禁用标记/区间鼠标交互（保持 v8.8 形态）。"""
        self._advanced = bool(on)
        if not self._advanced:
            # 清空既有标记/区间，保持只读
            self._markers = []
            self._range = (0.0, 0.0)
            self._drag_start = None
            self._drag_end = None
            self.update()

    def remove_marker(self, idx: int):
        """移除指定索引的标记点。"""
        if 0 <= idx < len(self._markers):
            self._markers.pop(idx)
            self.update()

    def clear_range(self):
        self._range = (0.0, 0.0)
        self.range_changed.emit((0.0, 0.0))
        self.update()

    def _recompute(self):
        times = [it.get("time", 0) for it in self._items if it.get("time")]
        self._duration_s = int((max(times) - min(times)) if len(times) > 1 else 0)
        self._turns = sum(1 for it in self._items if it.get("role") == "user")
        self._calls = sum(1 for it in self._items if it.get("role") == "tool")

    def _x_to_time(self, x: int) -> float:
        """屏幕 x 坐标 → 时间戳。"""
        w = self.width()
        bar_top = self.BAR_TOP
        usable_w = max(w - 24, 1)
        if not self._items:
            return 0.0
        times = [it.get("time", 0) for it in self._items]
        t0, t1 = min(times), max(times)
        span = max(t1 - t0, 1.0)
        if x <= 12:
            return t0
        if x >= 12 + usable_w:
            return t1
        return t0 + (x - 12) / usable_w * span

    def _nearest_item_index(self, t: float) -> int:
        if not self._items:
            return -1
        best_i, best_dt = -1, float("inf")
        for i, it in enumerate(self._items):
            dt = abs((it.get("time", 0) or 0) - t)
            if dt < best_dt:
                best_dt = dt
                best_i = i
        return best_i

    def mousePressEvent(self, ev: QMouseEvent):
        if not self._advanced:
            return
        if not self._items:
            return
        if ev.button() == Qt.MouseButton.LeftButton:
            t = self._x_to_time(ev.position().x())
            idx = self._nearest_item_index(t)
            if idx < 0:
                return
            # 检测是否点击了已有标记点（容差 6 像素）
            for m_i, m in enumerate(self._markers):
                if abs(m.get("idx", -1) - idx) < 2:
                    self.marker_clicked.emit({"marker": m, "index": m_i, "item": self._items[idx]})
                    ev.accept()
                    return
            # 划选区间 + 记录 mousedown 信息（P2-1：单击延时判定）
            self._mouse_down = {"x": ev.position().x(), "y": ev.position().y(), "t": t, "idx": idx}
            self._drag_start = t
            self._drag_end = t
            self.update()
            ev.accept()
        elif ev.button() == Qt.MouseButton.RightButton:
            t = self._x_to_time(ev.position().x())
            # 右键点击 marker → 菜单
            for m_i, m in enumerate(self._markers):
                if abs(m.get("idx", -1) - self._nearest_item_index(t)) < 2:
                    self._show_marker_menu(ev.globalPosition().toPoint(), m_i)
                    ev.accept()
                    return

    def mouseMoveEvent(self, ev: QMouseEvent):
        if not self._advanced:
            return
        if self._drag_start is not None and (ev.buttons() & Qt.MouseButton.LeftButton):
            self._drag_end = self._x_to_time(ev.position().x())
            self.update()

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if not self._advanced:
            return
        if ev.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            t0 = self._drag_start
            t1 = self._drag_end if self._drag_end is not None else t0
            info = self._mouse_down
            self._drag_start = None
            self._drag_end = None
            self._mouse_down = None
            if abs(t1 - t0) < 0.001:
                # P2-1 修复：单击位移 < 6px 才视为有效点击，延时 250ms 后新增标记
                # 双击会抢先 cancel 掉这个 timer，避免重复添加
                if not info or abs(ev.position().x() - info["x"]) > 6 or abs(ev.position().y() - info["y"]) > 6:
                    return
                idx = info["idx"]
                self._pending_click_idx = idx
                try:
                    self._pending_click_timer.stop()
                except Exception:
                    pass
                from PyQt6.QtCore import QTimer
                self._pending_click_timer = QTimer(self)
                self._pending_click_timer.setSingleShot(True)
                self._pending_click_timer.timeout.connect(self._commit_pending_click)
                self._pending_click_timer.start(250)
            else:
                lo, hi = (t0, t1) if t0 < t1 else (t1, t0)
                self._range = (lo, hi)
                self.range_changed.emit(self._range)
                self.update()

    def _commit_pending_click(self):
        """单击延时到期：真正新增标记点（避免双击重复添加）。"""
        idx = getattr(self, "_pending_click_idx", None)
        if idx is None or idx < 0 or idx >= len(self._items):
            return
        # 检查是否已存在同一 item 的标记（去重）
        if any(m.get("idx") == idx for m in self._markers):
            return
        self._markers.append({"time": self._items[idx].get("time", 0), "idx": idx, "note": ""})
        self._pending_click_idx = None
        self.update()

    def mouseDoubleClickEvent(self, ev: QMouseEvent):
        # P2-1 修复：双击时取消 click delay timer，避免重复添加
        if not self._advanced:
            return
        if hasattr(self, "_pending_click_timer") and self._pending_click_timer:
            try:
                self._pending_click_timer.stop()
            except Exception:
                pass
        if not self._items:
            return
        t = self._x_to_time(ev.position().x())
        idx = self._nearest_item_index(t)
        if idx >= 0:
            # 去重
            if any(m.get("idx") == idx for m in self._markers):
                return
            self._markers.append({"time": self._items[idx].get("time", t), "idx": idx, "note": ""})
            self.update()

    def _show_marker_menu(self, global_pos: QPoint, marker_idx: int):
        menu = QMenu(self)
        act_del = menu.addAction("删除该标记")
        act_clear = menu.addAction("清除全部标记")
        chosen = menu.exec(global_pos)
        if chosen is act_del:
            self.remove_marker(marker_idx)
        elif chosen is act_clear:
            self._markers = []
            self.update()

    def paintEvent(self, ev):
        pa = QPainter(self)
        pa.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        pal = QApplication.palette()
        bg = pal.color(pal.Base)
        fg = pal.color(pal.Text)
        faint = pal.color(pal.PlaceholderText)
        if not faint.isValid():
            faint = QColor("#9ca3af")
        pa.fillRect(self.rect(), bg)
        pa.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        pa.setPen(fg)
        dur = self._duration_s
        dur_txt = f"Duration {dur//60:02d}:{dur%60:02d}" if dur >= 60 else f"Duration {dur}s"
        stat = f"{dur_txt}  ·  Turns {self._turns}  ·  Calls {self._calls}"
        pa.drawText(12, 22, stat)
        bar_top, bar_h = self.BAR_TOP, max(16, h - 54)
        if not self._items:
            pa.setPen(faint)
            pa.setFont(QFont("Microsoft YaHei", 9))
            pa.drawText(12, bar_top + bar_h // 2 + 4, "暂无轨迹数据（单/双击时间线加标记 · 拖拽划选区间）")
            self._paint_legend(pa, w, h, faint)
            pa.end()
            return
        times = [it.get("time", 0) for it in self._items]
        t0, t1 = min(times), max(times)
        span = max(t1 - t0, 1.0)
        usable_w = max(w - 24, 1)
        sampled = self._sample(self._items)
        # 区间高亮（先绘制底色）
        if self._drag_start is not None and self._drag_end is not None:
            x0 = 12 + int(((min(self._drag_start, self._drag_end) - t0) / span) * usable_w)
            x1 = 12 + int(((max(self._drag_start, self._drag_end) - t0) / span) * usable_w)
            pa.fillRect(QRect(x0, bar_top - 2, max(2, x1 - x0), bar_h + 4), QColor(0, 0, 0, 28))
        elif self._range[0] != self._range[1]:
            x0 = 12 + int(((self._range[0] - t0) / span) * usable_w)
            x1 = 12 + int(((self._range[1] - t0) / span) * usable_w)
            pa.fillRect(QRect(x0, bar_top - 2, max(2, x1 - x0), bar_h + 4), QColor(255, 213, 79, 70))
        for it in sampled:
            t = it.get("time", t0)
            role = it.get("role", "assistant")
            meta = _TRACE_ROLE_META.get(role, _TRACE_ROLE_META["assistant"])
            x = 12 + int(((t - t0) / span) * usable_w)
            color = QColor(meta["color"])
            pa.setBrush(color)
            pa.setPen(Qt.PenStyle.NoPen)
            pa.drawRoundedRect(x, bar_top, max(4, int(usable_w * 0.015)), bar_h, 2, 2)
        # 标记点（黄色倒三角 ▼ 落在条形顶部）
        for m in self._markers:
            t = m.get("time", t0)
            x = 12 + int(((t - t0) / span) * usable_w)
            pa.setBrush(QColor("#f59e0b"))
            pa.setPen(QColor("#b45309"))
            tri = [
                QPoint(x - 5, bar_top - 1),
                QPoint(x + 5, bar_top - 1),
                QPoint(x, bar_top + 9),
            ]
            pa.drawPolygon(QPolygon(tri))
        self._paint_legend(pa, w, h, faint)
        pa.end()

    def _paint_legend(self, pa, w, h, faint):
        pa.setFont(QFont("Microsoft YaHei", 8))
        legend_x = 12
        for role, meta in _TRACE_ROLE_META.items():
            pa.setBrush(QColor(meta["color"]))
            pa.drawRoundedRect(legend_x, h - 14, 8, 8, 2, 2)
            pa.setPen(faint)
            pa.drawText(legend_x + 12, h - 6, meta["label"])
            legend_x += 56

    @staticmethod
    def _sample(items: list[dict], max_bars: int = 160) -> list[dict]:
        if len(items) <= max_bars:
            return items
        step = len(items) / max_bars
        out = []
        for i in range(max_bars):
            idx = min(int(i * step), len(items) - 1)
            out.append(items[idx])
        out[-1] = items[-1]
        return out


class TraceFlow(QWidget):
    """轨迹消息流：按角色分类的卡片列表，支持展开详情。

    增量渲染 + 2000 条上限，防止长会话 O(n^2) 重建与内存无限增长。

    v8.10 增强：
    - 多关键词 AND 匹配（空格分隔）+ 区分大小写
    - 操作筛选（按 role 集合）+ 区间筛选（与 TraceTimeline 联动）
    - 卡片双击 → 弹出详情预览对话框
    """

    MAX_ITEMS = 2000

    item_activated = pyqtSignal(dict)   # 双击卡片时发出（detail 预览用）

    def __init__(self, parent=None, advanced: bool = True):
        super().__init__(parent)
        self.setObjectName("traceflow")
        self._advanced = advanced
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.listw = QListWidget()
        self.listw.setSpacing(4)
        self.listw.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if advanced:
            self.listw.itemDoubleClicked.connect(self._on_item_double_clicked)
        v.addWidget(self.listw, 1)
        self._all_items: list[dict] = []
        # 筛选条件
        self._keyword_tokens: list[str] = []   # 原始文本（按 case_sensitive 决定是否 lower）
        self._case_sensitive: bool = False
        self._role_filter: set[str] = set()   # 空集表示全部
        self._range_filter: tuple = (0.0, 0.0)  # (t0, t1)，(0,0) 表示无区间

    # ---------- 数据入口 ----------
    def set_items(self, items: list[dict]):
        items = list(items or [])[-self.MAX_ITEMS:]
        # P1-1 修复：写入真实 __idx，供双击/详情按钮定位
        for _i, _it in enumerate(items):
            _it["__idx"] = _i
        self._all_items = items
        self._rebuild()

    def add_item(self, item: dict):
        item = dict(item)  # 浅拷贝，避免篡改外部传入的 dict
        self._all_items.append(item)
        if len(self._all_items) > self.MAX_ITEMS:
            self._all_items.pop(0)
        # 重新同步 __idx（pop 后下标可能漂移）
        for _i, _it in enumerate(self._all_items):
            _it["__idx"] = _i
        if not self._keyword_tokens and not self._role_filter and self._range_filter == (0.0, 0.0):
            widget = self._build_card(item)
            li = QListWidgetItem()
            li.setSizeHint(widget.sizeHint())
            li.setData(Qt.ItemDataRole.UserRole, item.get("__idx", len(self._all_items) - 1))
            self.listw.addItem(li)
            self.listw.setItemWidget(li, widget)
            self.listw.scrollToBottom()
        else:
            self._rebuild()

    def clear(self):
        self._all_items = []
        self._keyword_tokens = []
        self._role_filter = set()
        self._range_filter = (0.0, 0.0)
        self._rebuild()

    def scroll_to_item(self, idx: int):
        """滚动到指定「真实 __idx」的卡片（用于 marker 点击联动）。"""
        # 遍历 listw 反查：找 data == idx 的 QListWidgetItem
        for i in range(self.listw.count()):
            li = self.listw.item(i)
            if li is not None and li.data(Qt.ItemDataRole.UserRole) == idx:
                self.listw.scrollToItem(li, self.listw.PositionAtCenter)
                return

    # ---------- 筛选条件 ----------
    def set_keywords(self, text: str):
        """多关键词 AND，空格分隔。保存原始 token（不分大小写），匹配时再 lower。"""
        raw = str(text or "").strip()
        self._keyword_tokens = [t for t in raw.split() if t] if raw else []
        self._rebuild()

    def set_case_sensitive(self, on: bool):
        """P1-5 修复：只改标志，不重写 token（与 Web 端对齐）。"""
        self._case_sensitive = bool(on)
        self._rebuild()

    def set_role_filter(self, roles: set[str]):
        self._role_filter = set(roles or [])
        self._rebuild()

    def set_range(self, rng: tuple):
        self._range_filter = tuple(rng or (0.0, 0.0))
        self._rebuild()

    # ---------- 事件 ----------
    def _on_item_double_clicked(self, li: QListWidgetItem):
        if li is None:
            return
        idx = li.data(Qt.ItemDataRole.UserRole)
        if idx is None:
            return
        if 0 <= idx < len(self._all_items):
            self.item_activated.emit(self._all_items[idx])

    # ---------- 渲染 ----------
    def _matches(self, item: dict) -> bool:
        # 角色筛选
        if self._role_filter:
            role = item.get("role", "assistant")
            if role not in self._role_filter:
                return False
        # 区间筛选
        if self._range_filter != (0.0, 0.0):
            t = item.get("time", 0) or 0
            t0, t1 = self._range_filter
            if not (t0 <= t <= t1):
                return False
        # 关键词筛选（AND）
        if self._keyword_tokens:
            role = item.get("role", "")
            summary = item.get("summary", "")
            detail = item.get("detail", "")
            haystack = f"{role}\n{summary}\n{detail}"
            if not self._case_sensitive:
                haystack = haystack.lower()
            for tok in self._keyword_tokens:
                target = tok if self._case_sensitive else tok.lower()
                if target not in haystack:
                    return False
        return True

    def _rebuild(self):
        self.listw.clear()
        items = self._all_items
        if self._role_filter or self._keyword_tokens or self._range_filter != (0.0, 0.0):
            items = [it for it in items if self._matches(it)]
        if not items:
            li = QListWidgetItem("暂无轨迹（请检查筛选条件）" if self._all_items else "暂无轨迹")
            li.setFlags(Qt.ItemFlag.NoItemFlags)
            self.listw.addItem(li)
            return
        for idx, it in enumerate(items):
            widget = self._build_card(it)
            li = QListWidgetItem()
            li.setSizeHint(widget.sizeHint())
            # P1-1 修复：存真实 __idx 而非过滤后下标，避免筛选激活时点错条目
            li.setData(Qt.ItemDataRole.UserRole, it.get("__idx", idx))
            self.listw.addItem(li)
            self.listw.setItemWidget(li, widget)

    def _build_card(self, item: dict) -> QWidget:
        role = item.get("role", "assistant")
        meta = _TRACE_ROLE_META.get(role, _TRACE_ROLE_META["assistant"])
        card = QFrame()
        card.setObjectName("tracecard")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(5)
        # 头部：角色徽章 + 时间 + 摘要 + 详情按钮
        head = QHBoxLayout()
        head.setSpacing(8)
        badge = QLabel(meta["label"])
        badge.setObjectName("tracebadge")
        badge.setStyleSheet(f"color:{meta['color']};background:{meta['bg']};"
                            f"padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600;")
        tm = item.get("time")
        time_lbl = QLabel(self._fmt_time(tm) if tm else "")
        time_lbl.setObjectName("tracetime")
        summary = QLabel(str(item.get("summary", ""))[:120])
        summary.setObjectName("tracesummary")
        summary.setWordWrap(True)
        head.addWidget(badge)
        head.addWidget(time_lbl)
        head.addWidget(summary, 1)
        # 详情按钮（仅高级模式显示）
        if self._advanced:
            detail_btn = QPushButton("详情")
            detail_btn.setObjectName("flatbtn")
            detail_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            detail_btn.setToolTip("查看完整详情（可缩放/可引用文字）")
            detail_btn.clicked.connect(lambda _=False, it=item: self.item_activated.emit(it))
            head.addWidget(detail_btn)
        v.addLayout(head)
        # 详情（默认折叠——限制高度 80，点击展开由详情对话框全显）
        detail = str(item.get("detail", "")).strip()
        if detail:
            body = QTextBrowser()
            body.setObjectName("tracedetail")
            body.setMaximumHeight(80)
            body.setHtml(f"<pre style='font-family:Consolas,monospace;font-size:12px;"
                         f"white-space:pre-wrap;word-break:break-word;margin:0;'>{_esc(detail[:600])}</pre>")
            v.addWidget(body)
        card.setObjectName("tracecard")
        return card

    @staticmethod
    def _fmt_time(ts) -> str:
        from datetime import datetime
        try:
            dt = datetime.fromtimestamp(float(ts))
            return dt.strftime("%H:%M:%S")
        except Exception:
            return ""


class TracePanel(QWidget):
    """工作轨迹主面板：搜索 + 时间线 + 消息流。

    只读展示运行历史；事件由主窗口事件泵实时同步。

    v8.10 增强：
    - 操作筛选下拉（多选 system/context/user/assistant/tool）
    - 区分大小写开关
    - 时间线手动标记点 + 区间联动
    - 卡片「详情」按钮或双击 → 弹出 TracePreviewDialog（详情预览）
    - 模块开关 ENABLE_TRACE_ADVANCED（关闭时隐藏高级入口）
    """

    quote_requested = pyqtSignal(str)   # 透传预览对话框的引用信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tracepanel")
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        # 标题
        title = QHBoxLayout()
        title_lbl = QLabel("工作轨迹")
        title_lbl.setObjectName("trace_title")
        title.addWidget(title_lbl)
        title.addStretch(1)
        # 区间统计
        self.range_stat = QLabel("")
        self.range_stat.setObjectName("trace_range_stat")
        self.range_stat.setStyleSheet("color: gray; font-size: 11px;")
        self.range_stat.setVisible(False)
        title.addWidget(self.range_stat)
        v.addLayout(title)

        # 时间线
        self.timeline = TraceTimeline()
        self.timeline.range_changed.connect(self._on_range_changed)
        self.timeline.marker_clicked.connect(self._on_marker_clicked)
        v.addWidget(self.timeline)

        # 高级筛选行（操作下拉 + 关键词 + 大小写 + 清空）
        try:
            advanced = bool(get_config().ENABLE_TRACE_ADVANCED)
        except Exception:
            advanced = True
        self._advanced = advanced

        # P1-3：高级开关关闭时禁用标记/区间交互（保持纯展示）
        if not self._advanced:
            self.timeline.set_advanced(False)

        # 先创建消息流（被 case_btn 的 toggled 引用，必须先于控件行）
        # P1-3：高级开关控制详情按钮/双击预览是否挂接
        self.flow = TraceFlow(advanced=self._advanced)
        if self._advanced:
            self.flow.item_activated.connect(self._open_preview)

        if advanced:
            ctrl_row = QHBoxLayout()
            ctrl_row.setSpacing(6)
            # 操作下拉
            self.ops_btn = QToolButton()
            self.ops_btn.setObjectName("trace_ops")
            self.ops_btn.setText("操作 ▾")
            self.ops_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            self.ops_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.ops_btn.setToolTip("按角色多选（system/context/user/assistant/tool）")
            self._build_ops_menu()
            ctrl_row.addWidget(self.ops_btn)
            # 关键词
            self.search_inp = QLineEdit()
            self.search_inp.setObjectName("tracesearch")
            self.search_inp.setPlaceholderText("搜索关键词（空格分隔 = AND）")
            self.search_inp.textChanged.connect(self._on_keyword)
            ctrl_row.addWidget(self.search_inp, 1)
            # 大小写
            self.case_btn = QToolButton()
            self.case_btn.setObjectName("trace_case")
            self.case_btn.setText("Aa")
            self.case_btn.setCheckable(True)
            self.case_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.case_btn.setToolTip("区分大小写")
            self.case_btn.toggled.connect(self.flow.set_case_sensitive)
            ctrl_row.addWidget(self.case_btn)
            # 清空
            clear_btn = QPushButton("清空")
            clear_btn.setObjectName("flatbtn")
            clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            clear_btn.clicked.connect(self.clear)
            ctrl_row.addWidget(clear_btn)
            v.addLayout(ctrl_row)
        else:
            # 简化模式（v8.8 形态）
            search_row = QHBoxLayout()
            search_row.setSpacing(6)
            self.search_inp = QLineEdit()
            self.search_inp.setObjectName("tracesearch")
            self.search_inp.setPlaceholderText("搜索角色或内容…")
            self.search_inp.textChanged.connect(self._on_keyword)
            search_row.addWidget(self.search_inp)
            clear_btn = QPushButton("清空")
            clear_btn.setObjectName("flatbtn")
            clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            clear_btn.clicked.connect(self.clear)
            search_row.addWidget(clear_btn)
            v.addLayout(search_row)

        v.addWidget(self.flow, 1)

        # 预览对话框实例集合（避免 GC + 多次打开对比）
        self._previews = []

    # ---------- 操作下拉菜单 ----------
    def _build_ops_menu(self):
        menu = QMenu(self.ops_btn)
        self._ops_actions = {}
        for role in ("system", "context", "user", "assistant", "tool"):
            act = menu.addAction(_TRACE_ROLE_META[role]["label"])
            act.setCheckable(True)
            act.toggled.connect(lambda checked, r=role: self._on_ops_toggled(r, checked))
            self._ops_actions[role] = act
        menu.addSeparator()
        clear_act = menu.addAction("清除筛选")
        clear_act.triggered.connect(self._clear_role_filter)
        self.ops_btn.setMenu(menu)

    def _on_ops_toggled(self, role: str, checked: bool):
        roles = set(self.flow._role_filter)
        if checked:
            roles.add(role)
        else:
            roles.discard(role)
        self.flow.set_role_filter(roles)
        # 更新按钮文本（显示已选数量）
        if hasattr(self, "ops_btn"):
            n = len(self.flow._role_filter)
            self.ops_btn.setText(f"操作 ({n}) ▾" if n else "操作 ▾")

    def _clear_role_filter(self):
        for act in getattr(self, "_ops_actions", {}).values():
            act.setChecked(False)
        self.flow.set_role_filter(set())
        if hasattr(self, "ops_btn"):
            self.ops_btn.setText("操作 ▾")

    # ---------- 时间线区间 / 标记联动 ----------
    def _on_range_changed(self, rng: tuple):
        self.flow.set_range(rng)
        # 统计区间内事件
        t0, t1 = rng
        if t0 == 0 and t1 == 0:
            self.range_stat.setVisible(False)
            self.range_stat.setText("")
            return
        items = [it for it in self.flow._all_items
                 if (it.get("time") is not None and t0 <= it["time"] <= t1)]
        turns = sum(1 for it in items if it.get("role") == "user")
        calls = sum(1 for it in items if it.get("role") == "tool")
        errors = sum(1 for it in items
                     if (it.get("kind") == "run_error"
                         or (it.get("role") == "system" and "出错" in (it.get("summary") or ""))))
        # Top 工具
        from collections import Counter
        tool_names = [it.get("summary", "") for it in items if it.get("role") == "tool" and it.get("summary")]
        top3 = ", ".join(f"{n}({c})" for n, c in Counter(tool_names).most_common(3)) or "-"
        self.range_stat.setText(
            f"区间：{len(items)} 条 · Turns {turns} · Calls {calls} · Errors {errors} · Top {top3}"
        )
        self.range_stat.setVisible(True)

    def _on_marker_clicked(self, payload: dict):
        """标记点被点击：滚动到对应卡片并打开详情预览。"""
        item = payload.get("item")
        idx = payload.get("index", 0)
        if item is None:
            return
        # 找到该 item 在 _all_items 中的索引并滚动
        try:
            target_idx = self.flow._all_items.index(item)
        except ValueError:
            target_idx = idx
        self.flow.scroll_to_item(target_idx)
        self._open_preview(item)

    def _open_preview(self, item: dict):
        """弹出详情预览对话框（非模态，可同时开多个）。"""
        try:
            from .trace_preview import TracePreviewDialog
        except Exception:
            return
        # 关闭已开数量超过 5 的最旧对话框，防泄漏
        if len(self._previews) >= 5:
            old = self._previews.pop(0)
            try:
                old.close()
                old.deleteLater()
            except Exception:
                pass
        dlg = TracePreviewDialog(item, self)
        dlg.quote_requested.connect(self.quote_requested.emit)
        self._previews.append(dlg)
        dlg.destroyed.connect(lambda _=None, d=dlg: self._previews.remove(d) if d in self._previews else None)
        dlg.show()
        dlg.raise_()

    # ---------- 主窗口事件入口 ----------
    def add_event(self, ev: dict):
        """主窗口事件泵调用：把事件转为轨迹条目。"""
        item = self._event_to_item(ev)
        if item is None:
            return
        # 增加 kind 字段便于区间统计识别 run_error
        item["kind"] = ev.get("type", "")
        self.flow.add_item(item)
        # P1-2：流式追加时不重置 marker/range（避免每条事件抹掉用户标记）
        self.timeline.set_items(self.flow._all_items, reset_extras=False)

    def set_history(self, history: list[dict]):
        """从历史消息重建轨迹。"""
        items = []
        for msg in history or []:
            role = msg.get("role", "")
            if role == "system":
                items.append({"role": "system", "time": 0, "summary": "Initial System Prompt",
                              "detail": str(msg.get("content", ""))[:2000], "kind": "system"})
            elif role == "user":
                items.append({"role": "user", "time": 0,
                              "summary": str(msg.get("content", ""))[:120],
                              "detail": str(msg.get("content", ""))[:2000], "kind": "user"})
            elif role == "assistant":
                content = str(msg.get("content", ""))
                items.append({"role": "assistant", "time": 0,
                              "summary": content[:120] or "AI 回复",
                              "detail": content[:2000], "kind": "assistant"})
                # tool_calls 拆为 TOOL
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    name = fn.get("name", "tool") if isinstance(fn, dict) else "tool"
                    args = fn.get("arguments", "") if isinstance(fn, dict) else ""
                    items.append({"role": "tool", "time": 0,
                                  "summary": name,
                                  "detail": str(args)[:2000], "kind": "tool"})
        # 没有时间戳时按顺序均匀分布，保证时间线不空
        base = __import__("time").time() - max(len(items), 1)
        for i, it in enumerate(items):
            if not it.get("time"):
                it["time"] = base + i
        self.flow.set_items(items)
        self.timeline.set_items(items)

    def clear(self):
        self.flow.clear()
        self.timeline.set_items([])
        if hasattr(self, "range_stat"):
            self.range_stat.setVisible(False)
            self.range_stat.setText("")
        if self._advanced:
            self._clear_role_filter()
            # P2-2 修复：清空时同步复位 UI（搜索框/大小写按钮）
            if hasattr(self, "search_inp"):
                self.search_inp.blockSignals(True)
                self.search_inp.clear()
                self.search_inp.blockSignals(False)
            if hasattr(self, "case_btn") and self.case_btn.isChecked():
                self.case_btn.setChecked(False)

    def _on_keyword(self, text: str):
        self.flow.set_keywords(text)

    @staticmethod
    def _event_to_item(ev: dict) -> dict | None:
        t = ev.get("type")
        if t == "run_start":
            return {"role": "system", "time": __import__("time").time(),
                    "summary": "会话开始", "detail": "", "kind": "run_start"}
        if t == "text_delta":
            return {"role": "assistant", "time": __import__("time").time(),
                    "summary": "AI 输出", "detail": str(ev.get("content", ""))[:2000], "kind": "text_delta"}
        if t == "tool_start":
            return {"role": "tool", "time": __import__("time").time(),
                    "summary": ev.get("name", "tool"),
                    "detail": str(ev.get("args", ""))[:2000], "kind": "tool_start"}
        if t == "tool_result":
            return {"role": "tool", "time": __import__("time").time(),
                    "summary": (ev.get("name", "tool") + " 完成"),
                    "detail": str(ev.get("output", ""))[:2000], "kind": "tool_result"}
        if t in ("expert_plan_start", "expert_plan"):
            return {"role": "context", "time": __import__("time").time(),
                    "summary": "总司令规划", "detail": str(ev.get("summary", ""))[:2000], "kind": "expert_plan"}
        if t == "guard_alert":
            return {"role": "system", "time": __import__("time").time(),
                    "summary": "报警", "detail": str(ev.get("reason", ""))[:2000], "kind": "guard_alert"}
        if t == "drift_merged":
            return {"role": "system", "time": __import__("time").time(),
                    "summary": "算力漂移合并", "detail": f"追加 {ev.get('added', 0)} 条消息",
                    "kind": "drift_merged"}
        if t in ("run_done", "run_final", "run_error", "run_cancelled"):
            return {"role": "system", "time": __import__("time").time(),
                    "summary": {"run_done": "会话完成", "run_final": "会话完成",
                                "run_error": "运行出错", "run_cancelled": "已停止"}.get(t, t),
                    "detail": str(ev.get("message", ""))[:2000], "kind": t}
        return None
