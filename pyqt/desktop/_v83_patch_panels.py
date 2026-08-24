
# ==========================================================================
# v8.3 报警只读横幅 + 任务管理器四层视图
# ==========================================================================
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
)

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
