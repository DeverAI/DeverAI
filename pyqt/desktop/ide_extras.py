"""v6.3 IDE 对标补全组件：@-mention 文件引用 + 命令面板 + Checkpoint 恢复菜单。

设计目标（对标 Cursor/Trae/VSCode）：
- @-mention：ChatPanel 输入框检测 @ 触发文件选择 popup，选中插入为 quote chip
- 命令面板：Ctrl+Shift+P 弹出，模糊匹配文件/主题/模式/动作
- Checkpoint 恢复：右键菜单列出最近 AI 改动快照，一键恢复

轻量化原则：纯 PyQt5 内置组件，不引入新依赖。
"""
from __future__ import annotations
from typing import List

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QTextCursor, QTextCharFormat, QColor, QFont
from PyQt5.QtWidgets import (
    QListWidget, QListWidgetItem, QDialog, QLineEdit, QVBoxLayout, QLabel,
    QMenu, QMessageBox, QWidget, QHBoxLayout, QPlainTextEdit, QPushButton,
    QDialogButtonBox, QSplitter,
)


# ==========================================================================
# @-mention 文件选择 popup
# ==========================================================================
class FileMentionPopup(QListWidget):
    """输入框内 @ 触发的文件选择 popup。

    用法：ChatPanel 输入框 textChanged 时调 trigger(text, cursor_pos)；
    选中后 emit picked(rel_path)；Esc/失焦关闭。
    """

    def __init__(self, parent_input: QWidget, file_provider, on_picked):
        """
        parent_input: 宿主输入框（QPlainTextEdit），popup 定位在其下方
        file_provider: () -> List[str]，返回工作区所有相对路径
        on_picked: (rel_path: str) -> None，选中后回调（ChatPanel 注入 quote chip）
        """
        super().__init__(parent_input)
        self._provider = file_provider
        self._on_picked = on_picked
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMaximumHeight(220)
        self.itemClicked.connect(self._pick)
        self._anchor = ""   # @ 在文本中的位置
        self._query = ""    # @ 后的查询串

    def trigger(self, anchor_pos: int, query: str) -> None:
        """显示 popup 并按 query 过滤文件列表。anchor_pos 是 @ 的位置。"""
        self._anchor = anchor_pos
        self._query = query.lower()
        all_files = []
        try:
            all_files = self._provider() or []
        except Exception:
            pass
        if not all_files:
            self.hide()
            return
        # 过滤：文件名或路径包含 query
        if self._query:
            matched = [f for f in all_files if self._query in f.lower()]
        else:
            matched = all_files[:30]
        if not matched:
            self.hide()
            return
        self.clear()
        for f in matched[:30]:
            it = QListWidgetItem(f)
            self.addItem(it)
        # 定位到输入框光标下方
        gp = self.parent().mapToGlobal(self.parent().rect().bottomLeft())
        self.move(gp.x(), gp.y() + 2)
        self.resize(max(300, self.parent().width()), min(220, len(matched) * 22 + 10))
        self.show()
        self.setCurrentRow(0)

    def _pick(self, item: QListWidgetItem) -> None:
        rel = item.text()
        self.hide()
        # 从输入框删除 @query 串（从 anchor 到光标）
        inp = self.parent()
        if hasattr(inp, "textCursor"):
            cur = inp.textCursor()
            cur_pos = cur.position()
            if cur_pos > self._anchor:
                cur.setPosition(self._anchor, QTextCursor.KeepAnchor)
                cur.removeSelectedText()
        try:
            self._on_picked(rel)
        except Exception:
            pass

    def keyPressEvent(self, e):
        """Up/Down 导航，Enter 选中，Esc 关闭，其他键回传输入框。"""
        from PyQt5.QtCore import QEvent
        k = e.key()
        if k in (Qt.Key_Up, Qt.Key_Down):
            return super().keyPressEvent(e)
        if k in (Qt.Key_Return, Qt.Key_Enter):
            it = self.currentItem()
            if it:
                self._pick(it)
            return
        if k == Qt.Key_Escape:
            self.hide()
            return
        # 其他键转发给输入框（让用户继续输入过滤）
        inp = self.parent()
        if inp is not None:
            inp.setFocus()
            # v8.5.x 审查修复：原先 `QApplication_send_event(...) if False else None` 是死代码，
            # 按键被吞、过滤不更新；改为同步 sendEvent 正确转发。
            from PyQt5.QtWidgets import QApplication
            QApplication.sendEvent(inp, e)


# ==========================================================================
# 命令面板（Ctrl+Shift+P）
# ==========================================================================
class CommandPalette(QDialog):
    """命令面板：模糊匹配文件/主题/模式/动作。回车执行选中项。"""

    def __init__(self, parent, commands: List[dict]):
        """
        commands: [{"type": "file"|"action", "label": str, "data": any}, ...]
                  file 类：data=相对路径，回车打开
                  action 类：data=回调函数，回车执行
        """
        super().__init__(parent)
        self.setWindowTitle("命令面板")
        self.setModal(True)
        self.resize(520, 360)
        self._commands = commands
        self._filtered = list(commands)

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("输入文件名或命令（如：gui.py / 切换主题 / chat 模式）…")
        self.edit.textChanged.connect(self._filter)
        v.addWidget(self.edit)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._exec)
        v.addWidget(self.list, 1)
        hint = QLabel("↑↓ 导航 · Enter 执行 · Esc 关闭")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        v.addWidget(hint)
        self._populate()
        self.edit.setFocus()

    def _populate(self) -> None:
        self.list.clear()
        for c in self._filtered[:200]:
            tag = "[文件] " if c.get("type") == "file" else ""
            it = QListWidgetItem(tag + c.get("label", ""))
            it.setData(Qt.UserRole, c)
            self.list.addItem(it)
        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def _filter(self, text: str) -> None:
        t = text.strip().lower()
        if not t:
            self._filtered = list(self._commands)
        else:
            # 子串匹配（不分词，简单但够用）
            self._filtered = [c for c in self._commands if t in c.get("label", "").lower()]
        self._populate()

    def _exec(self, item: QListWidgetItem) -> None:
        c = item.data(Qt.UserRole)
        if not c:
            return
        self.accept()
        cb = c.get("data")
        if c.get("type") == "action" and callable(cb):
            try:
                cb()
            except Exception as e:
                QMessageBox.warning(self.parent(), "执行失败", str(e))
        # file 类由调用方在 accepted 后读 self.list.currentItem() 处理

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Up, Qt.Key_Down):
            self.list.keyPressEvent(e)
            return
        if k in (Qt.Key_Return, Qt.Key_Enter):
            it = self.list.currentItem()
            if it:
                self._exec(it)
            return
        if k == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(e)

    def selected(self) -> dict | None:
        """对话框关闭后取选中项（file 类用）。"""
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None


# ==========================================================================
# Checkpoint 恢复菜单
# ==========================================================================
def build_checkpoint_menu(parent: QWidget, restore_callback) -> QMenu:
    """构造"恢复到 AI 改动前"右键菜单。

    restore_callback: (bak_path: str, rel_path: str) -> None，由调用方实现实际写回。
    """
    menu = QMenu("恢复到 AI 改动前", parent)
    try:
        from . import checkpoint as ckpt
        items = ckpt.list_checkpoints(limit=20)
    except Exception:
        items = []
    if not items:
        act = menu.addAction("（无 checkpoint 记录）")
        act.setEnabled(False)
        return menu
    for it in items:
        label = f"[{it.get('ts', '')}] {it.get('rel_path', '?')}"
        act = menu.addAction(label)
        bak = it.get("bak_path", "")
        rel = it.get("rel_path", "")
        act.triggered.connect(lambda _=False, b=bak, r=rel: _do_restore(parent, b, r, restore_callback))
    return menu


def _do_restore(parent: QWidget, bak_path: str, rel_path: str, restore_callback) -> None:
    """执行恢复：先确认，再回调写回。"""
    from PyQt5.QtWidgets import QMessageBox
    ans = QMessageBox.question(
        parent, "恢复确认",
        f"将把「{rel_path}」恢复到 AI 改动前的版本。\n当前内容会被覆盖（恢复前会自动再做一次快照，可二次回滚）。\n\n确认恢复？",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
    )
    if ans != QMessageBox.Yes:
        return
    try:
        restore_callback(bak_path, rel_path)
        QMessageBox.information(parent, "已恢复", f"已恢复「{rel_path}」到 checkpoint 版本。")
    except Exception as e:
        QMessageBox.warning(parent, "恢复失败", str(e))


# ==========================================================================
# v6.4 内联差异预览（Diff Preview）：Agent 写入前弹左右分栏对比，接受才落盘
# ==========================================================================
def _diff_lines(old: str, new: str) -> tuple:
    """简易逐行 diff：返回 (left_lines, right_lines, line_marks)。
    line_marks: ' '='共同，'-'=仅旧，'+'=仅新。用 difflib 避免新依赖。"""
    import difflib
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    left, right, marks = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                left.append(old_lines[k]); right.append(old_lines[k]); marks.append("=")
        elif tag == "replace":
            for k in range(i1, i2):
                left.append(old_lines[k]); right.append(""); marks.append("-")
            for k in range(j1, j2):
                left.append(""); right.append(new_lines[k]); marks.append("+")
        elif tag == "delete":
            for k in range(i1, i2):
                left.append(old_lines[k]); right.append(""); marks.append("-")
        elif tag == "insert":
            for k in range(j1, j2):
                left.append(""); right.append(new_lines[k]); marks.append("+")
    return left, right, marks


class DiffPreviewDialog(QDialog):
    """左右分栏 diff 对比视图。用户点"接受"才落盘，点"拒绝"则取消写入。

    用法：dialog = DiffPreviewDialog(parent, rel_path, old_content, new_content)
         if dialog.exec_() == QDialog.Accepted: ...执行写入...
    """

    def __init__(self, parent, rel_path: str, old: str, new: str):
        super().__init__(parent)
        self.setWindowTitle(f"差异预览 — {rel_path}")
        self.resize(900, 600)
        self._old = old or ""
        self._new = new or ""

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        # 顶部统计
        stats = self._calc_stats()
        lbl = QLabel(f"文件: {rel_path}    行数: 旧 {stats['old_lines']} → 新 {stats['new_lines']}    "
                     f"变更: +{stats['added']} -{stats['removed']} 行")
        lbl.setStyleSheet("color: gray; font-size: 11px; padding: 2px;")
        v.addWidget(lbl)

        # 左右分栏
        splitter = QSplitter(Qt.Horizontal)
        left_edit = self._build_diff_view("原始（旧）", self._old, self._new, side="left")
        right_edit = self._build_diff_view("新内容（AI 生成）", self._old, self._new, side="right")
        splitter.addWidget(left_edit)
        splitter.addWidget(right_edit)
        splitter.setSizes([450, 450])
        v.addWidget(splitter, 1)

        # 底部按钮
        bb = QDialogButtonBox()
        self._btn_accept = bb.addButton("接受并写入", QDialogButtonBox.AcceptRole)
        self._btn_reject = bb.addButton("拒绝（取消写入）", QDialogButtonBox.RejectRole)
        self._btn_accept.setStyleSheet("background: #2d7d46; color: white; padding: 6px 16px; font-weight: bold;")
        self._btn_reject.setStyleSheet("padding: 6px 16px;")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._btn_accept.setFocus()

    def _calc_stats(self) -> dict:
        left, right, marks = _diff_lines(self._old, self._new)
        added = marks.count("+")
        removed = marks.count("-")
        return {
            "old_lines": len(self._old.splitlines()),
            "new_lines": len(self._new.splitlines()),
            "added": added, "removed": removed,
        }

    def _build_diff_view(self, title: str, old: str, new: str, side: str) -> QWidget:
        """构建单侧 diff 视图（标题 + 带行号高亮的 QPlainTextEdit）。"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight: bold; padding: 2px; background: #2a2a3a; color: #ddd;")
        lay.addWidget(lbl)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(10)
        edit.setFont(font)
        edit.setLineWrapMode(QPlainTextEdit.NoWrap)

        left, right, marks = _diff_lines(old, new)
        lines = left if side == "left" else right
        # 构造带颜色的文本
        fmt_normal = QTextCharFormat()
        fmt_add = QTextCharFormat(); fmt_add.setForeground(QColor("#2d7d46"))   # 绿
        fmt_del = QTextCharFormat(); fmt_del.setForeground(QColor("#b33a3a"))   # 红
        cur = edit.textCursor()
        for i, (line, mark) in enumerate(zip(lines, marks)):
            if i > 0:
                cur.insertText("\n", fmt_normal)
            prefix = "  "
            fmt = fmt_normal
            if side == "left" and mark == "-":
                prefix = "- "; fmt = fmt_del
            elif side == "right" and mark == "+":
                prefix = "+ "; fmt = fmt_add
            elif mark == "=":
                prefix = "  "
            cur.insertText(prefix + str(line), fmt)
        lay.addWidget(edit, 1)
        return w

# ==========================================================================
# v8.2 版本回退对话框（按文件选择历史版本恢复，防误编辑/rm -rf 救不回）
# ==========================================================================
class VersionRestoreDialog(QDialog):
    """左文件列表 + 右版本列表（时间/来源）+ 内容预览；选中后恢复。

    restore_callback: (bak_path, rel_path) -> None（实际写回，恢复前自动快照可二次回滚）。
    """

    def __init__(self, parent=None, restore_callback=None):
        super().__init__(parent)
        self.setWindowTitle("版本回退（快照）")
        self.resize(780, 500)
        self._restore_callback = restore_callback

        root = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        self.file_list = QListWidget()
        self.file_list.setMinimumWidth(250)
        self.file_list.currentRowChanged.connect(self._on_file)
        self.ver_list = QListWidget()
        self.ver_list.setMinimumWidth(210)
        self.ver_list.currentRowChanged.connect(self._on_version)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        right = QSplitter(Qt.Vertical)
        right.addWidget(self.ver_list)
        right.addWidget(self.preview)
        right.setSizes([150, 340])
        split.addWidget(self.file_list)
        split.addWidget(right)
        split.setSizes([280, 500])
        root.addWidget(split, 1)

        btns = QHBoxLayout()
        btn_refresh = QPushButton("刷新")
        btn_restore = QPushButton("恢复此版本")
        btn_close = QPushButton("关闭")
        btn_refresh.clicked.connect(self._load_files)
        btn_restore.clicked.connect(self._restore)
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_refresh)
        btns.addStretch(1)
        btns.addWidget(btn_restore)
        btns.addWidget(btn_close)
        root.addLayout(btns)
        self._load_files()

    def _load_files(self, select_rel: str = ""):
        from . import checkpoint as ckpt
        self.file_list.blockSignals(True)
        self.file_list.clear()
        self.ver_list.blockSignals(True)
        self.ver_list.clear()
        self.ver_list.blockSignals(False)
        self.preview.clear()
        try:
            files = ckpt.list_files()
        except Exception:
            files = []
        if not files:
            it = QListWidgetItem("（无快照记录）")
            it.setData(Qt.UserRole, None)
            self.file_list.addItem(it)
        else:
            sel_row = 0
            for n, f in enumerate(files):
                it = QListWidgetItem(f"{f['rel_path']}  ({f['count']}版)")
                it.setData(Qt.UserRole, f["rel_path"])
                self.file_list.addItem(it)
                if select_rel and f["rel_path"] == select_rel:
                    sel_row = n
            self.file_list.setCurrentRow(sel_row)
        self.file_list.blockSignals(False)
        if files:
            self.file_list.setCurrentRow(sel_row)

    def _on_file(self, row):
        it = self.file_list.item(row)
        rel = it.data(Qt.UserRole) if it else None
        if not rel:
            return
        from . import checkpoint as ckpt
        self.ver_list.blockSignals(True)
        self.ver_list.clear()
        try:
            vers = ckpt.list_versions(rel)
        except Exception:
            vers = []
        for v in vers:
            label = f"[{v.get('ts', '')}] {ckpt.source_name(v.get('source', 'ai'))}"
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, v)
            self.ver_list.addItem(it)
        self.ver_list.blockSignals(False)
        self.preview.clear()
        if vers:
            self.ver_list.setCurrentRow(0)

    def _on_version(self, row):
        it = self.ver_list.item(row)
        v = it.data(Qt.UserRole) if it else None
        if not v:
            return
        try:
            from pathlib import Path
            self.preview.setPlainText(
                Path(v["bak_path"]).read_text(encoding="utf-8", errors="replace"))
        except Exception:
            self.preview.setPlainText("（读取预览失败）")

    def _restore(self):
        from . import checkpoint as ckpt
        it = self.ver_list.currentItem()
        v = it.data(Qt.UserRole) if it else None
        if not v:
            QMessageBox.information(self, "版本回退", "请先选择一个版本。")
            return
        if not self._restore_callback:
            return
        ans = QMessageBox.question(
            self, "恢复确认",
            f"将把「{v.get('rel_path', '?')}」恢复到\n"
            f"[{v.get('ts', '')}] {ckpt.source_name(v.get('source', 'ai'))} 版本。\n"
            f"当前内容会被覆盖（恢复前会自动再做一次快照，可二次回滚）。\n\n确认恢复？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        try:
            self._restore_callback(v["bak_path"], v.get("rel_path", ""))
            QMessageBox.information(self, "已恢复", "已恢复到所选版本。")
            self._load_files(select_rel=v.get("rel_path", ""))
        except Exception as e:
            QMessageBox.warning(self, "恢复失败", str(e))


# ==========================================================================
# v8.3 轮次快照回退对话框（回退点 + 历史任务会话）
# ==========================================================================
class RoundRestoreDialog(QDialog):
    """任务级快照恢复：左列=当前轮回退点（某次工具调用前），右列=历史任务会话。

    恢复前先由上层触发审查（无 P0 才允许恢复）；本对话框只做选择与执行。
    """

    def __init__(self, parent=None, workspace: str = "", on_restored=None):
        super().__init__(parent)
        self.setWindowTitle("快照恢复（任务级）")
        self.resize(720, 460)
        self._workspace = workspace or ""
        self._round_no = None
        self._session_rid = None
        self._on_restored = on_restored

        root = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        self.rb_list = QListWidget()
        self.rb_list.setMinimumWidth(280)
        self.rb_list.itemClicked.connect(self._on_rb_click)
        self.sess_list = QListWidget()
        self.sess_list.setMinimumWidth(280)
        self.sess_list.itemClicked.connect(self._on_sess_click)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        right = QSplitter(Qt.Vertical)
        right.addWidget(self.sess_list)
        right.addWidget(self.detail)
        right.setSizes([160, 300])
        split.addWidget(self.rb_list)
        split.addWidget(right)
        split.setSizes([300, 420])
        root.addWidget(split, 1)

        btns = QHBoxLayout()
        lbl = QLabel("回退点=某次工具调用前 · 会话=整轮工作区压缩")
        lbl.setStyleSheet("color: #94a3b8; font-size: 11px;")
        btn_refresh = QPushButton("刷新")
        btn_rb = QPushButton("回退到所选调用点")
        btn_sess = QPushButton("恢复所选会话")
        btn_del = QPushButton("删除所选会话")
        btn_close = QPushButton("关闭")
        btn_refresh.clicked.connect(self._load)
        btn_rb.clicked.connect(self._do_rb)
        btn_sess.clicked.connect(self._do_sess)
        btn_del.clicked.connect(self._do_delete_sess)
        btn_close.clicked.connect(self.accept)
        btns.addWidget(lbl)
        btns.addStretch(1)
        btns.addWidget(btn_refresh)
        btns.addWidget(btn_rb)
        btns.addWidget(btn_sess)
        btns.addWidget(btn_del)
        btns.addWidget(btn_close)
        root.addLayout(btns)
        self._load()

    def _load(self):
        from . import session_snap as snap
        self.rb_list.blockSignals(True)
        self.rb_list.clear()
        self.sess_list.blockSignals(True)
        self.sess_list.clear()
        self.detail.clear()
        try:
            points = snap.rollback_points(snap.current_round())
        except Exception:
            points = []
        if not points:
            it = QListWidgetItem("（当前轮无回退点）")
            it.setData(Qt.UserRole, None)
            self.rb_list.addItem(it)
        else:
            for p in points:
                it = QListWidgetItem(f"#{p['round_no']} {p['tool']} ({p['ts']})")
                it.setData(Qt.UserRole, p)
                self.rb_list.addItem(it)
        try:
            sessions = [s for s in snap.list_sessions()
                        if s.get("status") == "committed"]
        except Exception:
            sessions = []
        if not sessions:
            it = QListWidgetItem("（无历史任务快照）")
            it.setData(Qt.UserRole, None)
            self.sess_list.addItem(it)
        else:
            for s in sessions[:30]:
                label = f"[{s['ts']}] {s['title'] or s['round_id']}"
                if s.get("size_mb"):
                    label += f" ({s['size_mb']}MB)"
                it = QListWidgetItem(label)
                it.setData(Qt.UserRole, s)
                self.sess_list.addItem(it)
        self.rb_list.blockSignals(False)
        self.sess_list.blockSignals(False)

    def _on_rb_click(self, item):
        p = item.data(Qt.UserRole)
        if not p:
            return
        self._round_no = int(p["round_no"])
        self._session_rid = None
        self.detail.setPlainText(
            f"回退点 #{p['round_no']}\n工具: {p['tool']}\n时间: {p['ts']}\n"
            f"涉及文件: {', '.join(p.get('files') or [])}")

    def _on_sess_click(self, item):
        s = item.data(Qt.UserRole)
        if not s:
            return
        try:
            from . import session_snap as snap
            self._session_rid = s["round_id"]
            self._round_no = None
            meta = snap.get_session(s["round_id"]) or {}
            calls = meta.get("tool_calls") or s.get("tool_calls") or []
            todo = meta.get("todo") or ""
            devlog = meta.get("devlog") or ""
            tree = meta.get("tree") or {}
            # 依赖树摘要：列出文件数与大小下限（P2-3：损坏条目防御 isinstance）
            tree_lines = []
            for k, v in list(tree.items())[:15]:
                if not isinstance(v, dict):
                    continue
                tree_lines.append(
                    f"   {k}: deps={len(v.get('deps') or [])} "
                    f"size={v.get('size') or 0}B min={v.get('min_size') or 0}B")
            if len(tree) > 15:
                tree_lines.append(f"   ... 共 {len(tree)} 个文件")
            self.detail.setPlainText(
                f"会话 {s['round_id']}\n标题: {s.get('title','') or meta.get('title','')}\n"
                f"时间: {s['ts']}\n状态: {s.get('status','')}\n大小: {s.get('size_mb','')}MB\n\n"
                f"完整输入:\n{(meta.get('input') or '')[:2000]}\n\n"
                f"当时 todo 表:\n{(todo[:1500] or '（无）')}\n\n"
                f"当时 devlog:\n{(devlog[:1200] or '（无）')}\n\n"
                f"依赖树摘要:\n" + ("\n".join(tree_lines) if tree_lines else "   （无）") + "\n\n"
                f"工具调用:\n" + "\n".join(f"  {c}" for c in calls[:40]))
        except Exception:
            self.detail.setPlainText("（快照元数据读取失败）")

    def _do_rb(self):
        from . import session_snap as snap
        if self._round_no is None:
            QMessageBox.information(self, "回退", "请先在左侧选择一个回退点。")
            return
        if not self._workspace:
            QMessageBox.warning(self, "回退", "未配置工作区，无法回退。")
            return
        ok, note = snap.restore_rollback_point(
            snap.current_round(), self._round_no, self._workspace)
        if ok:
            QMessageBox.information(self, "已回退", note)
            if callable(self._on_restored):
                try:
                    self._on_restored()
                except Exception:
                    pass
            self.accept()
        else:
            QMessageBox.warning(self, "回退失败", note)

    def _do_sess(self):
        from . import session_snap as snap
        if self._session_rid is None:
            QMessageBox.information(self, "恢复", "请先在右侧选择一个历史会话。")
            return
        if not self._workspace:
            QMessageBox.warning(self, "恢复", "未配置工作区，无法恢复。")
            return
        ans = QMessageBox.question(
            self, "恢复确认",
            "恢复将用该会话快照覆盖整个工作区当前状态（不含 backups/data 等）。\n\n"
            "确认恢复？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        ok, note = snap.restore_session(self._session_rid, self._workspace)
        if ok:
            QMessageBox.information(self, "已恢复", note)
            if callable(self._on_restored):
                try:
                    self._on_restored()
                except Exception:
                    pass
            self.accept()
        else:
            QMessageBox.warning(self, "恢复失败", note)

    def _do_delete_sess(self):
        """v8.3 需求5：删除前审查（无 P0 才允许删除）。

        审查指标=快照内依赖树自洽 + 输入完整性：快照内引用文件缺失、
        无标题无输入均视为 P0 级缺漏，拒绝删除；审查通过才执行删除。
        P2-2：推荐回退目标只取"更早"会话，且用快照自身树校验（不依赖当前工作区）。"""
        from . import session_snap as snap
        if self._session_rid is None:
            QMessageBox.information(self, "删除", "请先在右侧选择一个历史会话。")
            return
        sess = None
        try:
            for s in snap.list_sessions():
                if s["round_id"] == self._session_rid:
                    sess = s
                    break
        except Exception:
            sess = None
        if sess is None:
            # P2-5：会话已不存在（被外部删除）
            QMessageBox.information(self, "删除", "该会话已不存在，请刷新列表。")
            self._session_rid = None
            self._load()
            return
        meta = snap.get_session(self._session_rid) or {}
        issues = []
        tree = meta.get("tree") or {}
        # P2-2：用快照自身文件集合做自洽校验（不依赖当前工作区，防误判）
        if tree and isinstance(tree, dict):
            have = {k for k in tree.keys()}
            for rel, entry in tree.items():
                if not isinstance(entry, dict):
                    continue
                for d in entry.get("deps") or []:
                    if d not in have:
                        issues.append({"rel": rel, "kind": "missing_dep",
                                       "detail": f"快照内引用缺失: {d}"})
        p0 = [i for i in issues if i.get("kind") == "missing_dep"]
        if not meta.get("input") and not meta.get("title"):
            p0.append({"rel": "(meta)", "kind": "missing_dep", "detail": "快照缺输入与标题，疑似损坏"})
        if p0:
            detail = "; ".join(f"{i['rel']} {i['detail']}" for i in p0[:5])
            # 缺口2：只找比当前会话更早且快照自洽无缺失的最近一个推荐
            good = None
            try:
                for s2 in snap.list_sessions():
                    if s2["round_id"] == self._session_rid:
                        continue
                    if s2.get("ts", "") > sess.get("ts", ""):
                        continue  # P2-2：只推荐更早的快照
                    m2 = snap.get_session(s2["round_id"]) or {}
                    t2 = m2.get("tree") or {}
                    if not isinstance(t2, dict) or not t2:
                        continue
                    have2 = set(t2.keys())
                    bad = False
                    for rel2, entry2 in t2.items():
                        if not isinstance(entry2, dict):
                            continue
                        for d2 in entry2.get("deps") or []:
                            if d2 not in have2:
                                bad = True
                                break
                        if bad:
                            break
                    if bad:
                        continue
                    good = s2
                    break
            except Exception:
                good = None
            rec = ""
            if good:
                rec = (f"\n\n检测到更早快照正常可用，可尝试恢复：\n"
                       f"  [{good.get('ts','')}] {good.get('title') or good['round_id']}\n"
                       f"（在右侧选择该会话后点「恢复所选会话」）")
            QMessageBox.warning(
                self, "审查未通过（P0）",
                "该快照存在 P0 级缺漏，禁止删除：\n\n" + detail +
                rec +
                "\n\n建议先排查文件缺失/损坏问题，修复后再试。")
            return
        ans = QMessageBox.question(
            self, "删除确认",
            f"删除会话 {sess.get('title') or self._session_rid}？\n\n"
            "快照将被彻底删除（不留回收站）。此操作不可恢复。\n\n确认删除？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        if snap.delete_session(self._session_rid):
            QMessageBox.information(self, "已删除", "会话快照已删除。")
            self._session_rid = None
            self._load()
        else:
            QMessageBox.warning(self, "删除失败", "无法删除该会话。")
