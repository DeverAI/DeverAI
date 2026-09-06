"""DeverAI 桌面应用（PyQt6）—— UI 即 Agent 运行地。

架构：
- 一切操作本地直连：subprocess 执行命令行、Path 直接读写文件、httpx 直连 LLM。
- Agent 在后台线程的 asyncio loop 中运行，事件经 Queue 泵到 UI 线程。
- 审批门：命令执行前弹对话框，用户选择后经 loop.call_soon_threadsafe 唤醒。
"""
import asyncio
import os
import re
import sys
import time
from pathlib import Path
from queue import Queue, Empty

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPoint, QEvent
from PyQt6.QtGui import QColor, QFont, QTextCursor, QKeySequence, QTextCharFormat, QIcon, QPixmap, QPainter, QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QPlainTextEdit, QTextEdit, QTextBrowser, QLineEdit, QPushButton, QLabel,
    QDockWidget, QMessageBox, QMenu, QDialog, QFileDialog, QFrame,
    QStatusBar, QToolBar, QComboBox, QDialogButtonBox, QFormLayout,
    QSystemTrayIcon, QListWidget, QListWidgetItem, QProgressBar, QStackedWidget,
    QTabBar, QInputDialog,
)

from . import sync as sync_mod
from . import vault as vault_mod
from . import modes as modes_mod
from . import themes as themes_mod
from . import ctx_expert
from . import session_snap as snap_mod      # v8.3 任务级快照
from . import dep_tree as deps_mod          # v8.3 依赖树
from . import suggest as suggest_mod        # v8.3 建议系统
from . import drift as drift_mod            # v8.6 算力漂移状态机
from . import sessions as sessions_mod      # v8.16 多会话标签存储
from .agent import Agent
from .icons import icon as svg_icon, set_theme_colors as icons_set_theme
from .locks import get_locks
from .models import get_model
from .ai_complete import AI_ACTIONS, SelectionDialog, request_completion, stream_completion_lines
from .config import get_config
from .errors import log_error
from .highlight import BaseHighlighter, lang_for
from .md import md_to_html
from .panels import FileTree, VaultPanel, TerminalPanel, HealthDashboard
from .quest_panels import (HeroWidget, ModelPickerPopup, StatusRow, SummaryPanel,
                           GuardBanner, TaskManagerPanel, QuestSidebar, TracePanel)
from .ide_extras import (FileMentionPopup, CommandPalette, build_checkpoint_menu,
                         DiffPreviewDialog, VersionRestoreDialog)
from .settings_dialog import SettingsDialog
from .sync_queue import SyncQueue
from .tools import ApprovalGate
from .storage import load_json, save_json, save_text
from .remote_cmd import RemoteCmdClient

APP_NAME = "DeverAI"
HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "desktop_history.json"

# 关闭窗口后仍在运行的补全线程保活集合，防止活销毁崩溃
_ORPHAN_WORKERS = set()
# v6 算力漂移：后台推送/拉取线程保活（防 QThread 被 GC 时线程仍在跑）
_DRIFT_THREADS = set()


# ---------------------------------------------------------------------------
# 编辑器
# ---------------------------------------------------------------------------
class EditorTab:
    def __init__(self, path: str):
        self.path = path
        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        self.editor.setFont(font)
        self.editor.setTabStopDistance(4 * 16)
        self.hl = BaseHighlighter(self.editor.document(), lang_for(path))
        self.dirty = False
        self.ghost_start = -1   # 补全幽灵文本标记位置（\x1f 包裹）
        self.ghost_end = -1


class CompletionWorker(QThread):
    """Inline 补全请求线程。"""

    line_ready = pyqtSignal(str, object)  # (一行/一段补全文本, EditorTab)
    all_done = pyqtSignal(object)

    def __init__(self, cfg, lang, prefix, tab, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.lang = lang
        self.prefix = prefix
        self.tab = tab
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if getattr(self.cfg, "ENABLE_STREAM_COMPLETE", True):
                asyncio.run(self._stream())
            else:
                text = request_completion(self.cfg, self.lang, self.prefix)
                if text and not self._cancelled:
                    self.line_ready.emit(text + "\n", self.tab)
        except Exception as e:
            # v8.5.x 审查修复：补全失败不再静默吞掉，记 Err.log 便于排查
            log_error("AI 补全失败", e)
        finally:
            self.all_done.emit(self.tab)

    async def _stream(self):
        async for line in stream_completion_lines(self.cfg, self.lang, self.prefix):
            if self._cancelled:
                return
            self.line_ready.emit(line, self.tab)


class _SuggestThread(QThread):
    """v8.3 建议系统后台线程：文档驱动 → AI 生成 → 筛选 → result_ready(list)。"""

    result_ready = pyqtSignal(list)

    def __init__(self, cfg, user_input: str, mode: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.user_input = user_input
        self.mode = mode

    def run(self):
        try:
            import asyncio
            ctx = suggest_mod.build_context(self.cfg, current_input=self.user_input,
                                            current_mode=self.mode)
            items = asyncio.run(
                suggest_mod.generate_suggestions(self.cfg, ctx,
                                                 model_id=(self.cfg.suggest_models or [""])[0] or ""))
            self.result_ready.emit(items or [])
        except Exception:
            self.result_ready.emit([])


class EditorWidget(QTabWidget):
    path_activated = pyqtSignal(str)
    ai_status = pyqtSignal(str)  # 补全状态 → 状态栏
    quote_requested = pyqtSignal(str)  # v4: 引用选区到对话

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabs: list[EditorTab] = []
        self.cfg = None
        self.setDocumentMode(True)
        self.setTabsClosable(True)
        self.tabCloseRequested.connect(self._close_at)
        self.currentChanged.connect(self._sync_active)
        self._active = None
        self._applying = False        # 幽灵文本插入标志
        self._last_pos = -1           # 触发补全时的光标位置
        self._worker = None
        self._completion_timer = QTimer(self)
        self._completion_timer.setSingleShot(True)
        self._completion_timer.setInterval(700)
        self._completion_timer.timeout.connect(self._do_completion)

    # ---------------- 补全 ----------------
    def _sync_active(self, idx):
        if 0 <= idx < len(self.tabs):
            self._active = self.tabs[idx]
        else:
            self._active = None

    def _bind_completion(self, tab):
        tab.editor.textChanged.connect(lambda: self._on_text_changed(tab))
        tab.editor.keyPressEvent = self._make_key_handler(tab)
        tab.editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab.editor.customContextMenuRequested.connect(
            lambda pos: self._editor_context_menu(tab, pos))

    def _make_key_handler(self, tab):
        def handler(e):
            # 1) Tab 接受整段 ghost
            if e.key() == Qt.Key.Key_Tab and self._accept_ghost(tab):
                return
            # 2) v4：键入补全首字符时消费该字符（ghost 前进，灰字不残留）
            if tab.ghost_start >= 0 and e.text():
                body = self._ghost_body(tab)
                if body and body[0] == e.text()[0]:
                    self._consume_ghost_char(tab, e.text()[0])
                    return
            # 有 ghost 时的其他按键：先清除 ghost 再默认处理
            if tab.ghost_start >= 0 and e.text():
                self._clear_ghost(tab)
            # 3) v4 智能键：自动缩进 / Tab 空格 / 成对符号
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._smart_enter(tab)
                return
            if e.key() == Qt.Key.Key_Tab:
                tab.editor.textCursor().insertText("    ")
                return
            if e.key() == Qt.Key.Key_Backtab:
                self._smart_dedent(tab)
                return
            if e.key() == Qt.Key.Key_Backspace and self._smart_backspace(tab):
                return
            if e.text() and self._smart_pair(tab, e.text()):
                return
            QPlainTextEdit.keyPressEvent(tab.editor, e)
        return handler

    # ---- v4 智能键实现 ----
    def _smart_enter(self, tab):
        cur = tab.editor.textCursor()
        if cur.hasSelection():
            cur.insertText("")  # 先删除选区，再走统一缩进逻辑
        block_text = cur.block().text()
        indent = block_text[: len(block_text) - len(block_text.lstrip())]
        before = block_text[: cur.positionInBlock()].rstrip()
        extra = "    " if before.endswith((":", "{", "(", "[")) else ""
        cur.insertText("\n" + indent + extra)
        tab.editor.setTextCursor(cur)  # 回写光标，否则可见光标滞留在旧行

    def _smart_dedent(self, tab):
        cur = tab.editor.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        block_text = cur.block().text()
        n = 0
        while n < 4 and n < len(block_text) and block_text[n] == " ":
            n += 1
        if n:
            cur.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, n)
            cur.insertText("")
            tab.editor.setTextCursor(cur)

    def _smart_backspace(self, tab) -> bool:
        cur = tab.editor.textCursor()
        if cur.hasSelection():
            return False
        block_text = cur.block().text()
        pos = cur.positionInBlock()
        if pos >= 4 and block_text[:pos].strip() == "":
            cur.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, 4)
            cur.insertText("")
            tab.editor.setTextCursor(cur)
            return True
        return False

    _PAIRS = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}

    def _smart_pair(self, tab, ch) -> bool:
        if ch not in self._PAIRS:
            return False
        cur = tab.editor.textCursor()
        close = self._PAIRS[ch]
        sel = cur.selectedText()
        if sel:  # 包裹选区
            cur.insertText(ch + sel.replace("\u2029", "\n") + close)
            return True
        cur.insertText(ch + close)
        cur.movePosition(QTextCursor.MoveOperation.Left)
        tab.editor.setTextCursor(cur)
        return True

    def _ghost_body(self, tab) -> str:
        if tab.ghost_start < 0 or tab.ghost_end < 0:
            return ""
        doc = tab.editor.toPlainText()
        marker = "\x1f"
        ok = (0 <= tab.ghost_start < len(doc) and tab.ghost_end <= len(doc)
              and doc[tab.ghost_start] == marker and doc[tab.ghost_end - 1] == marker)
        if not ok:
            return ""
        return doc[tab.ghost_start + 1 : tab.ghost_end - 1]

    def _consume_ghost_char(self, tab, ch):
        """键入补全首字符：接受首字符，剩余补全继续作为 ghost 显示（ghost 前进）。"""
        body = self._ghost_body(tab)
        self._applying = True
        try:
            cur = tab.editor.textCursor()
            cur.setPosition(tab.ghost_start)
            cur.setPosition(tab.ghost_end, QTextCursor.MoveMode.KeepAnchor)
            cur.insertText(ch)
            tab.editor.setTextCursor(cur)
            tab.ghost_start = tab.ghost_end = -1  # 旧 ghost 已清除
        finally:
            self._applying = False
        rest = body[1:] if body.startswith(ch) else body
        if rest:
            self._insert_ghost(tab, rest)

    def _on_text_changed(self, tab):
        if self._applying:
            return
        self._completion_timer.stop()
        self._stop_worker()          # v4：用户真实编辑即取消在途补全（流式不打断的前提是无变化）
        self._clear_ghost(tab)
        self._last_pos = tab.editor.textCursor().position()
        self._last_tab = tab         # v8.5.x 审查修复：记录触发 tab，防止切 tab 错位补全
        self._completion_timer.start()

    def _stop_worker(self):
        w = self._worker
        if w is not None and w.isRunning():
            try:
                w.cancel()
            except Exception:
                pass

    def _do_completion(self):
        tab = self._active
        if tab is None or not self.cfg or not self.cfg.api_key or not self.cfg.model:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        # v8.5.x 审查修复：仅当触发编辑的 tab 仍是当前 tab 才补全，避免切 tab 错位触发
        if getattr(self, "_last_tab", None) is not tab:
            return
        cur = tab.editor.textCursor()
        pos = cur.position()
        if self._last_pos != -1 and pos != self._last_pos:
            return
        block = cur.block().text()
        if not block.strip():
            return
        prefix = tab.editor.toPlainText()[:pos][-4000:]
        self.ai_status.emit("AI 补全中…")
        # worker 不设 parent：避免窗口关闭时被父对象"活销毁"
        self._worker = CompletionWorker(self.cfg, lang_for(tab.path), prefix, tab)
        # v8.15 检修：deleteLater 后 self._worker 变悬挂 C++ 指针，下次按键
        # isRunning() 抛 RuntimeError（PyQt6 槽内未捕获 → qFatal 整应用崩溃）
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.line_ready.connect(self._on_completion_line)
        self._worker.all_done.connect(self._on_completion_done)
        self._worker.start()

    def _on_worker_finished(self):
        """v8.15：先清引用再 deleteLater，杜绝悬挂访问。"""
        w = self.sender()
        if w is not None:
            if self._worker is w:
                self._worker = None
            w.deleteLater()

    def _on_completion_line(self, chunk, tab):
        """逐行流式展示：首段插入 ghost，后续段追加到 ghost 尾部（文档无变化不打断）。"""
        if tab not in self.tabs or self._active is not tab:
            return
        # ghost 已存在即说明无用户真实编辑（任何编辑都会触发 _on_text_changed 清除 ghost），
        # 仅在尚无 ghost 时才校验光标位置，避免 ghost 自身移动光标导致自我熔断
        if tab.ghost_start < 0:
            cur = tab.editor.textCursor()
            if cur.position() != self._last_pos:
                self._stop_worker()
                return
        if not chunk:
            return
        if tab.ghost_start < 0:
            self._insert_ghost(tab, chunk)
        else:
            self._append_ghost(tab, chunk)

    def _on_completion_done(self, tab):
        self.ai_status.emit("就绪")

    def _append_ghost(self, tab, chunk):
        """在 ghost 结束标记前追加文本并重新应用灰色斜体。"""
        body = self._ghost_body(tab)
        if body == "":
            # 标记已损坏或尚未插入：损坏时复位绝不乱插，未插入时走首段路径
            if tab.ghost_start >= 0:
                tab.ghost_start = tab.ghost_end = -1
                return
            self._insert_ghost(tab, chunk)
            return
        self._applying = True
        try:
            insert_at = tab.ghost_end - 1  # 结束标记前
            cur = tab.editor.textCursor()
            cur.setPosition(insert_at)
            cur.insertText(chunk)
            new_end = tab.ghost_end + len(chunk)
            sel = tab.editor.textCursor()
            sel.setPosition(insert_at)
            sel.setPosition(insert_at + len(chunk), QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self._ghost_color()))
            fmt.setFontItalic(True)
            sel.mergeCharFormat(fmt)
            tab.ghost_end = new_end
            # 光标回到 ghost 起点（保持用户视角不变）
            cur2 = tab.editor.textCursor()
            cur2.setPosition(tab.ghost_start + 1)
            tab.editor.setTextCursor(cur2)
        finally:
            self._applying = False

    def _ghost_color(self) -> str:
        try:
            from .themes import get_palette
            return get_palette(get_config().theme).get("ghost", "#5c667a")
        except Exception:
            return "#5c667a"

    def _insert_ghost(self, tab, text):
        self._applying = True
        try:
            cur = tab.editor.textCursor()
            start = cur.position()
            marker = "\x1f"
            cur.insertText(marker + text + marker)
            end = cur.position()
            # 设置幽灵样式：灰色斜体
            sel = tab.editor.textCursor()
            sel.setPosition(start + 1)
            sel.setPosition(end - 1, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self._ghost_color()))
            fmt.setFontItalic(True)
            sel.mergeCharFormat(fmt)
            tab.ghost_start = start
            tab.ghost_end = end
            cur.setPosition(start + 1)
            tab.editor.setTextCursor(cur)
        finally:
            self._applying = False

    def _accept_ghost(self, tab) -> bool:
        if tab.ghost_start < 0 or tab.ghost_end < 0:
            return False
        doc = tab.editor.toPlainText()
        marker = "\x1f"
        ok = (0 <= tab.ghost_start < len(doc) and tab.ghost_end <= len(doc)
              and doc[tab.ghost_start] == marker and doc[tab.ghost_end - 1] == marker)
        if not ok:
            self._clear_ghost(tab)
            return False
        self._applying = True
        try:
            body = doc[tab.ghost_start + 1 : tab.ghost_end - 1]
            cur = tab.editor.textCursor()
            cur.setPosition(tab.ghost_start)
            cur.setPosition(tab.ghost_end, QTextCursor.MoveMode.KeepAnchor)
            cur.insertText(body)
            cur.setPosition(tab.ghost_start + len(body))
            tab.editor.setTextCursor(cur)
        finally:
            tab.ghost_start = tab.ghost_end = -1
            self._applying = False
        return True

    def _clear_ghost(self, tab):
        if tab.ghost_start < 0:
            return
        doc = tab.editor.toPlainText()
        marker = "\x1f"
        start = tab.ghost_start
        # v8.5.x 审查修复：粘贴/撤销/输入法编辑会让 ghost_end 右移（绝对位置失效），
        # 结束标记改用搜索定位，避免只复位标记却把 \x1f 与幽灵文本残留写回文件。
        if not (0 <= start < len(doc) and doc[start] == marker):
            tab.ghost_start = tab.ghost_end = -1
            return
        end = doc.find(marker, start + 1)
        if end < 0:
            # 结束标记已丢失（外部编辑），只复位位置，绝不删除用户文本
            tab.ghost_start = tab.ghost_end = -1
            return
        end += 1  # 含结束标记
        self._applying = True
        try:
            cur = tab.editor.textCursor()
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cur.insertText("")
        finally:
            tab.ghost_start = tab.ghost_end = -1
            self._applying = False

    def _editor_context_menu(self, tab, pos):
        menu = QMenu(self)
        cur = tab.editor.textCursor()
        has_selection = cur.hasSelection()
        if has_selection:
            sub = menu.addMenu("AI 处理选区")
            sub.addAction("解释代码", lambda: self._request_ai_action(tab, "解释"))
            sub.addAction("优化代码", lambda: self._request_ai_action(tab, "优化"))
            sub.addAction("找 Bug", lambda: self._request_ai_action(tab, "找Bug"))
            sub.addAction("写注释", lambda: self._request_ai_action(tab, "写注释"))
            sub.addAction("生成测试", lambda: self._request_ai_action(tab, "生成测试"))
            menu.addAction("引用选区到对话",
                           lambda: self.quote_requested.emit(
                               tab.editor.textCursor().selectedText().replace("\u2029", "\n")))
        else:
            menu.addAction("AI 生成代码…", lambda: self._request_ai_action(tab, "改写成…"))
        menu.addSeparator()
        menu.addAction("版本历史…", self.versions_requested.emit)
        menu.addAction("剪切", lambda: tab.editor.cut())
        menu.addAction("复制", lambda: tab.editor.copy())
        menu.addAction("粘贴", lambda: tab.editor.paste())
        menu.addSeparator()
        menu.addAction("全选", lambda: tab.editor.selectAll())
        menu.exec(tab.editor.viewport().mapToGlobal(pos))

    def _request_ai_action(self, tab, action_name):
        if self.ai_action_requested:
            self.ai_action_requested.emit(tab, action_name)

    ai_action_requested = pyqtSignal(object, str)
    versions_requested = pyqtSignal()   # v8.26：编辑器右键「版本历史…」→ 主窗 VersionRestoreDialog

    # ---------------- 基础 ----------------
    def open_path(self, rel: str, content: str):
        for t in self.tabs:
            if t.path == rel:
                self.setCurrentWidget(t.editor)
                return t
        tab = EditorTab(rel)
        tab.editor.setPlainText(content)
        tab.editor.document().modificationChanged.connect(lambda d: self._mark(tab, d))
        self._bind_completion(tab)
        idx = self.addTab(tab.editor, rel.split("/")[-1])
        self.setTabToolTip(idx, rel)
        self.tabs.append(tab)
        self.setCurrentIndex(idx)
        return tab

    def reload_if_open(self, rel: str, content: str):
        try:
            target = str(Path(rel).resolve())
        except OSError:
            target = rel
        for t in self.tabs:
            try:
                same = Path(t.path).resolve() == Path(target)
            except OSError:
                same = os.path.normcase(os.path.abspath(t.path)) == os.path.normcase(target)
            if same and not t.dirty:
                self._clear_ghost(t)
                # 程序化刷新不应触发伪自动补全：_applying 抑制 _on_text_changed 的补全计时器
                self._applying = True
                try:
                    t.editor.setPlainText(content)
                finally:
                    self._applying = False

    def is_open(self, rel: str) -> bool:
        try:
            target = str(Path(rel).resolve())
        except OSError:
            target = rel
        for t in self.tabs:
            try:
                same = Path(t.path).resolve() == Path(target)
            except OSError:
                same = os.path.normcase(os.path.abspath(t.path)) == os.path.normcase(target)
            if same:
                return True
        return False

    def _mark(self, tab, dirty):
        if tab.dirty != dirty:
            tab.dirty = dirty
            idx = self.tabs.index(tab)
            self.setTabText(idx, ("* " if dirty else "") + tab.path.split("/")[-1])

    def _close_at(self, idx):
        tab = self.tabs[idx]
        if tab.dirty:
            # v8.14：默认按钮改为 No（回车不再等于丢弃修改）
            ret = QMessageBox.question(
                self, "未保存", f"「{tab.path}」未保存，确定关闭？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
        if self._active is tab:
            self._completion_timer.stop()
            self._clear_ghost(tab)
        tab.editor.deleteLater()
        # v8.15 检修：必须先 pop 再 removeTab——removeTab 同步触发 currentChanged，
        # 此时若 tabs 仍含旧项，_sync_active 会用新索引映射旧列表，_active 指向
        # 正在关闭的标签；其 editor 已 deleteLater，后续保存即 RuntimeError
        self.tabs.pop(idx)
        self.removeTab(idx)

    def _to_rel(self, path: str) -> str:
        """绝对路径 → 相对工作区路径（斜杠分隔，供快照存储）；不在工作区内返回空串。"""
        try:
            return str(Path(path).resolve().relative_to(Path(self.cfg.workspace).resolve())
                       ).replace("\\", "/")
        except (ValueError, TypeError):
            return ""

    def save_current(self) -> str:
        tab = self._active
        if not tab:
            return ""
        self._clear_ghost(tab)
        # v8.2 人类编辑快照：仅内容被改过（dirty）时保存前快照原文件内容
        if tab.dirty and self.cfg is not None and getattr(self.cfg, "ENABLE_CHECKPOINT", True):
            try:
                from . import checkpoint as _ckpt
                rel = self._to_rel(tab.path)
                if rel and Path(tab.path).exists():
                    cur = Path(tab.path).read_text(encoding="utf-8", errors="replace")
                    _ckpt.save_checkpoint(rel, cur, task_id="editor", source="human")
            except Exception:
                pass  # 快照失败不阻塞保存
        try:
            # v8.14：保持原文件行尾风格（编辑器内部一律 \n，保存时按原文件还原）
            from .storage import sniff_crlf
            save_text(tab.path, tab.editor.toPlainText(), eol=sniff_crlf(Path(tab.path)))
        except OSError as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return ""
        tab.dirty = False
        self.setTabText(self.tabs.index(tab), tab.path.split("/")[-1])
        return tab.path

    def save_as(self):
        tab = self._active
        if not tab:
            return
        self._clear_ghost(tab)
        path, _ = QFileDialog.getSaveFileName(self, "另存为", tab.path)
        if path:
            try:
                save_text(path, tab.editor.toPlainText())
            except OSError as e:
                QMessageBox.warning(self, "保存失败", str(e))
                return
            # v8.15 检修：另存成功后同步 tab 状态——否则 Ctrl+S 会把内容覆盖回旧文件
            tab.path = path
            tab.dirty = False
            self.setTabText(self.tabs.index(tab), path.replace("\\", "/").split("/")[-1])

    def current_path(self) -> str:
        return self._active.path if self._active else ""

    def current_selection(self) -> str:
        if self._active:
            return self._active.editor.textCursor().selectedText()
        return ""


# ---------------------------------------------------------------------------
# 聊天面板
# ---------------------------------------------------------------------------
class ChatPanel(QWidget):
    expert_detail_requested = pyqtSignal(str)  # v5: 单击专家卡片 → 详情对话框

    attach_requested = pyqtSignal()  # v8：输入卡 + 按钮 → 主窗口弹文件选择加引用
    open_file_requested = pyqtSignal(str)  # v8.29：变更栏点击文件引用 → 编辑器打开

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # v8.16：会话标签条（ENABLE_MULTI_SESSION 开启且存储可用时由主窗口填充显示）
        sess_host = QWidget()
        sess_host.setObjectName("sessionbarhost")
        sb = QHBoxLayout(sess_host)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(4)
        self.session_tabs = QTabBar()
        self.session_tabs.setObjectName("sessiontabs")
        self.session_tabs.setExpanding(False)
        self.session_tabs.setDrawBase(False)
        self.session_tabs.setMovable(False)
        self.session_tabs.setUsesScrollButtons(True)
        self.session_tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        sb.addWidget(self.session_tabs, 1)
        self.sess_new_btn = QPushButton()
        self.sess_new_btn.setObjectName("flatbtn")
        self.sess_new_btn.setIcon(svg_icon("plus", 14))
        self.sess_new_btn.setFixedSize(24, 24)
        self.sess_new_btn.setToolTip("新建会话")
        sb.addWidget(self.sess_new_btn)
        self.session_bar = sess_host
        self.session_bar.hide()

        # v8：英雄欢迎区（首条消息后自动隐藏）
        self.hero = HeroWidget()
        layout.addWidget(self.session_bar)   # v8.16：标签条置于聊天区最顶部
        layout.addWidget(self.hero, 1)
        # v8.3：建议条（TRAE CUE 式，消息发送时展示 AI 精选建议）
        self.suggest_box = QFrame()
        self.suggest_box.setObjectName("suggestbox")
        self.suggest_lay = QVBoxLayout(self.suggest_box)
        self.suggest_lay.setContentsMargins(8, 6, 8, 6)
        self.suggest_lay.setSpacing(2)
        self.suggest_box.hide()
        layout.addWidget(self.suggest_box, 0, Qt.AlignmentFlag.AlignTop)

        # v4：Agent 形态选择（v8 移入输入卡工具条）
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("flatcombo")
        self.mode_combo.addItems(["Chat", "Builder", "Experts"])
        self.mode_combo.setToolTip("Chat=对话助手（只读）；Builder=全能构建；Experts=专家团（总司令规划→专家并行→反馈表单）")

        # v5/v8：状态行（API 兼容旧 chip，v8 移入模型弹窗）
        self.qa_chip = StatusRow("问答模式", "on", "off",
                                 "开启后可直接回复专家团反馈表单，回复交给总司令（默认开）")
        self.sleep_chip = StatusRow("肝完睡觉", "on", "off",
                                    "目标达成后倒计时关机/休眠（需在设置中授权，注意保存工作）")
        self.serial_chip = StatusRow("专家调度", "serial", "parallel",
                                     "仅本轮专家团串行执行，发送后自动复位")

        # v6.1 修复：链接拦截（setOpenLinks/anchorClicked）属 QTextBrowser API，
        # QTextEdit 无此方法（启动即崩）；QTextBrowser 继承 QTextEdit，append 等全兼容
        self.view = QTextBrowser()
        self.view.setObjectName("conversationview")
        self.view.setReadOnly(True)
        self.view.setAcceptRichText(True)
        self.view.document().setDefaultStyleSheet(_CHAT_CSS)
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._on_anchor)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._view_ctx)
        self.view.hide()
        layout.addWidget(self.view, 1)

        # v4：引用条（发送前可逐条双击移除）
        self.quote_list = QListWidget()
        self.quote_list.setFixedHeight(60)
        self.quote_list.setVisible(False)
        self.quote_list.setToolTip("双击移除该引用")
        self.quote_list.itemDoubleClicked.connect(self._remove_quote)
        layout.addWidget(self.quote_list)

        # v8：卡片式输入框（圆角卡 + 底部工具条）
        self.card = QFrame()
        self.card.setObjectName("inputcard")
        self.card.setMinimumWidth(360)
        self.card.setMaximumWidth(880)
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(6)
        self.inp = QPlainTextEdit()
        self.inp.setPlaceholderText("Plan and build, @ 引用文件… (Ctrl+Enter 发送)")
        self.inp.setFixedHeight(64)
        cl.addWidget(self.inp)
        tb = QHBoxLayout()
        tb.setSpacing(4)
        self.plus_btn = QPushButton()
        self.plus_btn.setObjectName("flatbtn")
        self.plus_btn.setIcon(svg_icon("plus", 16))
        self.plus_btn.setFixedSize(28, 28)
        self.plus_btn.setToolTip("添加文件引用")
        self.plus_btn.clicked.connect(self.attach_requested.emit)
        tb.addWidget(self.plus_btn)
        tb.addWidget(self.mode_combo)
        self.model_btn = QPushButton("模型 ∨")
        self.model_btn.setObjectName("flatbtn")
        self.model_btn.setToolTip("选择模型 / 查看功能状态")
        tb.addWidget(self.model_btn)
        tb.addStretch(1)
        self.stop_btn = QPushButton()
        self.stop_btn.setObjectName("flatbtn")
        self.stop_btn.setIcon(svg_icon("close", 16))
        self.stop_btn.setFixedSize(28, 28)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setToolTip("停止")
        self.send_btn = QPushButton()
        self.send_btn.setObjectName("primary")
        self.send_btn.setIcon(svg_icon("send", 16))
        self.send_btn.setFixedSize(32, 32)
        self.send_btn.setToolTip("发送 (Ctrl+Enter)")
        tb.addWidget(self.stop_btn)
        tb.addWidget(self.send_btn)
        cl.addLayout(tb)
        layout.addWidget(self.card, 0, Qt.AlignmentFlag.AlignHCenter)

        # v8：模型选择弹窗（状态行 + Pilot/Copilot 分段 + 模型列表）
        self.model_popup = ModelPickerPopup(self)
        for r in (self.qa_chip, self.sleep_chip, self.serial_chip):
            self.model_popup.add_status_row(r)
        self.model_btn.clicked.connect(self._open_model_popup)

        self._ai_id = 0
        self._mid = 0
        self._msgs = {}            # mid -> {raw,start,end,uid,source_mode,has_tools}
        self._quotes = []          # [{label, content}]
        self._pending_uid = ""     # 下一条 AI 消息所属对话块 uid（供禁止自动引用）
        self._tool_cards = {}      # call_id -> {view_ref}
        self._ai_buf = ""
        self._ai_block = None
        self._ai_start = -1
        self._ai_end = -1
        self._ai_has_tools = False  # 本条 AI 消息期间是否插入过工具卡片
        self._streaming = False     # new_ai → finish_ai 之间为 True
        self._ai_notes = []         # 流式期间插入的提示（finish_ai 重渲染时保留）
        self._experts = {}          # v5: expert_id -> 卡片数据（供详情对话框）
        self._palette = {}         # v7: 当前主题色板（供内联样式取色）

    def update_palette(self, p: dict):
        """v7：主题切换时更新色板，供内联 HTML 样式取色。"""
        self._palette = p
        self.hero.update_palette(p)

    # ---- v8：英雄区 / 模型弹窗 ----
    def set_workspace_label(self, txt: str):
        self.hero.set_sub(txt)

    def set_model_label(self, name: str):
        self.model_btn.setText(f"{name} ∨")

    def _open_model_popup(self):
        self.model_popup.prepare()
        self.model_popup.adjustSize()
        g = self.model_btn.mapToGlobal(QPoint(0, 0))
        h = self.model_popup.sizeHint().height()
        x, y = g.x(), g.y() - h - 4
        scr = QApplication.primaryScreen()
        if scr is not None:
            geo = scr.availableGeometry()
            x = min(max(geo.x(), x), geo.x() + geo.width() - self.model_popup.width())
            if y < geo.y():  # 上方放不下则落到按钮下方
                y = g.y() + self.model_btn.height() + 4
            y = min(max(geo.y(), y), geo.y() + geo.height() - h)
        self.model_popup.move(x, y)
        self.model_popup.show()

    def _hide_hero(self):
        if self.hero.isVisible():
            self.hero.hide()
        self.view.show()

    # ---- v8.3：建议展示 ----
    def show_suggestions(self, items: list):
        """展示 AI 精选建议（功能/技术/美术三类）。"""
        while self.suggest_lay.count():
            it = self.suggest_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        if not items:
            return
        head = QLabel("AI 精选建议（点击采纳进对话）")
        head.setObjectName("suggesthead")
        self.suggest_lay.addWidget(head)
        for it in items:
            b = QPushButton(f"[{it.get('type', '')}] {it.get('title', '')}")
            b.setObjectName("flatbtn")
            b.setToolTip(str(it.get("detail", ""))[:200])
            b.clicked.connect(lambda _=False, d=it.get("detail", ""):
                              self.ai_note("采纳建议：" + str(d)))
            self.suggest_lay.addWidget(b)
        self.suggest_box.show()

    def _c(self, key: str, fallback: str = "") -> str:
        """从色板取色，缺失时回落。"""
        return self._palette.get(key, fallback)

    # ---- v5 专家团展示 ----
    def add_expert_plan(self, ev: dict):
        tasks = ev.get("tasks") or []
        mode_txt = "串行" if ev.get("serial") else "并行"
        lines = [f"总司令规划（{mode_txt}）：{_esc(ev.get('summary',''))}"]
        for t in tasks:
            files = "、".join(t.get("files") or []) or "无申报文件"
            lines.append(f"&nbsp;&nbsp;• {_esc(t.get('title',''))} → {_esc(t.get('model',''))}"
                         f"（思考 {t.get('thinking','medium')}）文件: {_esc(files)}")
        html = ('<div style="border:1px solid ' + self._c('border', '#252530') + '; border-radius:8px; padding:6px 10px;'
                'margin:4px 0; background:' + self._c('tool_bg', '#16161c') + '; font-size:12px;">' + "<br>".join(lines) + "</div>")
        self.view.append(html)
        if self._streaming:
            self._ai_notes.append(html)
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    def add_expert_card(self, ev: dict):
        """专家卡片：任务摘要 | 模型与负载（默认参数不展示）| 当前状态；单击开详情。"""
        eid = ev.get("expert_id", "")
        title = ev.get("title", eid)
        model = ev.get("model", "")
        thinking = ev.get("thinking", "medium")
        senior = bool(ev.get("senior"))
        m = get_model(model)
        level = (m.display_level if m else "full") if senior else "full"
        self._experts[eid] = {
            "title": title, "model": model, "thinking": thinking, "senior": senior,
            "files": ev.get("files") or [], "status": "执行中", "cost": "",
            "todos": [], "display_level": level,
        }
        # 展示级别过滤（高级专家）：minimal=仅状态；partial=摘要+状态；full=全部
        load_txt = f"{model} · {thinking}" if thinking != "medium" or senior else model
        if level == "minimal":
            body = f"{_esc(title[:10])} ｜ 执行中"
        elif level == "partial":
            body = f"{_esc(title[:14])} ｜ 执行中"
        else:
            body = f"{_esc(title[:14])} ｜ {_esc(load_txt[:22])} ｜ 执行中"
        html = ('<div style="border:1px solid ' + self._c('accent_dim', 'rgba(59,130,186,0.15)') + '; border-radius:8px; padding:5px 10px;'
                'margin:3px 0; background:' + self._c('tool_bg', '#16161c') + '; font-size:12px;">'
                f'<a href="act:xcard:{eid}">{_esc(body)}</a>'
                f'{"（高级专家）" if senior else ""}</div>')
        self.view.append(html)
        if self._streaming:
            self._ai_notes.append(html)
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    def note_expert_status(self, ev: dict):
        eid = ev.get("expert_id", "")
        st = ev.get("status", "")
        cost = ev.get("cost", "")
        e = self._experts.get(eid)
        if e is not None:
            e["status"] = st
            if cost:
                e["cost"] = cost
        mark = {"已完成": "✓", "实现不了": "[!]", "资源等待失败": "■", "已掐断": "■"}.get(st, "·")
        self.ai_note(f"  {mark} 专家 {_esc(eid)}：{_esc(st)}" + (f"（{_esc(cost)}）" if cost else ""))

    def set_expert_todos(self, ev: dict):
        e = self._experts.get(ev.get("expert_id", ""))
        if e is not None:
            e["todos"] = list(ev.get("todos") or [])
            done_n = sum(1 for t in e["todos"] if t.get("done"))
            e["status"] = f"进行中 {done_n}/{len(e['todos'])}"

    def add_expert_form(self, items: list, final: bool = True):
        """总司令反馈表单（含成本估测）；问答模式开启时直接回复即可。"""
        lines = ["<b>专家团反馈表单</b>"]
        for it in items:
            kind = _esc(it.get("kind", "需要提问"))
            expert = _esc(it.get("expert", ""))
            q = _esc(it.get("question", ""))[:300]
            cost = _esc(it.get("cost_estimate", "") or "")
            senior = " (需高级专家)" if it.get("need_senior") else ""
            lines.append(f"&nbsp;&nbsp;• [{kind}{senior}] {expert}：{q}"
                         + (f"（成本：{cost}）" if cost else ""))
        if self.qa_chip.isChecked() and final:
            lines.append("<i>问答模式已开启：直接在输入框回复表单内容即可交给总司令。</i>")
        html = ('<div style="border:1px solid ' + self._c('warn', '#f59e0b') + '; border-radius:8px; padding:6px 10px;'
                'margin:4px 0; background:' + self._c('tool_bg', '#16161c') + '; font-size:12px;">' + "<br>".join(lines) + "</div>")
        self.view.append(html)
        if self._streaming:
            self._ai_notes.append(html)
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    def add_copilot_card(self, note: str, kind: str = "block"):
        _border = self._c('err', '#ef4444')
        _bg = self._c('tool_bg', '#16161c')
        html = (f'<div style="border:1px solid {_border}; border-radius:8px; padding:6px 10px;'
                f'margin:4px 0; background:{_bg}; font-size:12px;">副驾驶：{_esc(note or "已掐断危险操作")}</div>')
        self.view.append(html)
        if self._streaming:
            self._ai_notes.append(html)
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    # ---- v4 引用 ----
    def add_quote(self, label: str, content: str):
        content = str(content or "").strip()
        if not content:
            return
        self._quotes.append({"label": str(label), "content": content})
        self._refresh_quotes()

    def _refresh_quotes(self):
        self.quote_list.clear()
        for i, q in enumerate(self._quotes):
            preview = q["content"][:60].replace("\n", " ⏎ ")
            it = QListWidgetItem(f"[{q['label']}] {preview}  （双击移除）")
            it.setData(Qt.ItemDataRole.UserRole, i)
            self.quote_list.addItem(it)
        self.quote_list.setVisible(bool(self._quotes))

    def _remove_quote(self, item):
        idx = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(idx, int) and 0 <= idx < len(self._quotes):
            self._quotes.pop(idx)
            self._refresh_quotes()

    def take_quotes(self):
        qs = list(self._quotes)
        self._quotes = []
        self._refresh_quotes()
        return qs

    def set_pending_uid(self, uid: str):
        self._pending_uid = str(uid or "")

    # ---- v4 消息按钮（锺点链接）----
    def _on_anchor(self, url):
        s = url.toString()
        if not s.startswith("act:"):
            return
        parts = s.split(":")
        act = parts[1] if len(parts) > 1 else ""
        if act == "xcard":  # v5: 专家卡片 → 详情对话框
            self.expert_detail_requested.emit(parts[2] if len(parts) > 2 else "")
            return
        if act == "openfile":  # v8.29 变更栏：点击文件引用 → 编辑器打开（相对路径，无冒号）
            self.open_file_requested.emit(":".join(parts[2:]) if len(parts) > 2 else "")
            return
        try:
            mid = int(parts[2]) if len(parts) > 2 else -1
        except ValueError:
            mid = -1
        m = self._msgs.get(mid)
        if m is None:
            return
        if act == "quote":
            self.add_quote("对话消息", m["raw"])
        elif act == "ban":
            uid = m.get("uid") or ctx_expert.block_uid(m["raw"])
            ctx_expert.ban_block(uid, preview=m["raw"][:120], by="user")
            self.ai_note("该对话块已永久禁止自动引用（需要时可点「引用」找回）")
        elif act == "toggle":
            self._toggle_render(mid)

    def _toggle_render(self, mid):
        m = self._msgs.get(mid)
        if m is None or not m["raw"]:
            return
        if m.get("has_tools"):
            # 有工具卡片时不做区间替换（避免抹掉卡片），在末尾追加副本
            body = _esc(m["raw"]) if m.get("source_mode") else md_to_html(m["raw"])
            m["source_mode"] = not m.get("source_mode")
            self.view.append(f'<div class="aitext">{body}</div>')
            self.view.moveCursor(QTextCursor.MoveOperation.End)
            return
        c = self.view.textCursor()
        c.setPosition(m["start"])
        c.setPosition(m["end"], QTextCursor.MoveMode.KeepAnchor)
        if m.get("source_mode"):
            c.insertHtml(md_to_html(m["raw"]))
            m["source_mode"] = False
        else:
            c.insertHtml(
                '<pre style="white-space:pre-wrap;font-family:Consolas,monospace;'
                f'font-size:12px;">{_esc(m["raw"])}</pre>'
            )
            m["source_mode"] = True
        m["end"] = c.position()
        self.view.ensureCursorVisible()

    def _view_ctx(self, pos):
        menu = QMenu(self)
        sel = self.view.textCursor().selectedText()
        if sel:
            menu.addAction("引用选中内容",
                           lambda: self.add_quote("对话选区", sel.replace("\u2029", "\n")))
        else:
            menu.addAction("（先选中内容再引用）").setEnabled(False)
        menu.exec(self.view.mapToGlobal(pos))

    # ---- v4 守护/守门卡片 ----
    def add_guard(self, ok: bool, note: str):
        mark = "✓ 合规" if ok else "[!] 异常"
        self.view.append(
            f'<div class="guard">规则守护 {mark}：{_esc(note or "对话正常，未绕过限制")}</div>'
        )
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    def add_gate_note(self, stats: dict, reason: str, banned: list):
        parts = [f"上下文守门：保留 {stats.get('kept', 0)}/{stats.get('total', 0)} 块"]
        if stats.get("dropped"):
            parts.append(f"省略古早 {stats['dropped']} 块")
        if stats.get("banned"):
            parts.append(f"已禁用 {stats['banned']} 块")
        if banned:
            parts.append(f"本轮新禁用 {len(banned)} 块")
        if reason:
            parts.append(f"（{_esc(reason)[:80]}）")
        html = f'<div class="note">{"，".join(parts)}</div>'
        self.view.append(html)
        if self._streaming:  # finish_ai 区间重渲染时会保留这些提示
            self._ai_notes.append(html)

    # ---- 消息渲染 ----
    def add_user(self, text: str, quote_count: int = 0):
        self._hide_hero()
        quote_line = ""
        if quote_count:
            quote_line = f'<div class="quote">携带 {quote_count} 条引用</div>'
        self.view.append(
            f'<div class="user">{quote_line}<div class="bubble">{_esc(text)}</div></div>'
        )

    def new_ai(self, title="DeverAI"):
        self._hide_hero()
        self._ai_id += 1
        self._mid += 1
        self._ai_buf = ""
        self._ai_block = None
        self._ai_has_tools = False
        self._streaming = True
        self._ai_notes = []
        self._msgs[self._mid] = {
            "raw": "", "start": -1, "end": -1, "uid": self._pending_uid,
            "source_mode": False, "has_tools": False,
        }
        c = self.view.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        c.insertHtml(f'<div class="ai"><div class="aihead">● {_esc(title)}</div></div>')
        # 用插入用的副本光标记录边界，不依赖用户可见光标（流式期间点击聊天区不影响）
        self._ai_start = c.position()
        self._ai_end = self._ai_start
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    def append_ai(self, text: str):
        if self._ai_block is None and self._ai_start < 0:
            self.new_ai()
        self._ai_buf += str(text or "")
        c = self.view.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        c.insertHtml(_esc(text).replace("\n", "<br>"))
        self._ai_end = c.position()
        self.view.moveCursor(QTextCursor.MoveOperation.End)
        self.view.ensureCursorVisible()

    def finish_ai(self):
        """流式结束后把整条 AI 消息重渲染为 Markdown，并附消息操作按钮（v4）。"""
        if not self._ai_buf or self._ai_start < 0:
            return
        mid = self._mid
        m = self._msgs.get(mid)
        if m is not None:
            m["raw"] = self._ai_buf
            m["has_tools"] = self._ai_has_tools
        if not self._ai_has_tools:
            # 有工具卡片时区间替换会抹掉卡片，保留流式显示的原样文本
            html = "".join(self._ai_notes) + md_to_html(self._ai_buf)
            c = self.view.textCursor()
            c.setPosition(self._ai_start)
            c.setPosition(self._ai_end, QTextCursor.MoveMode.KeepAnchor)
            c.insertHtml(html)
            if m is not None:
                m["start"] = self._ai_start
                m["end"] = c.position()
        self.view.append(
            f'<div class="msgbtns"><a href="act:quote:{mid}">引用</a> · '
            f'<a href="act:ban:{mid}">禁止自动引用</a> · '
            f'<a href="act:toggle:{mid}">源码/预览</a></div>'
        )
        self._streaming = False
        self.view.moveCursor(QTextCursor.MoveOperation.End)
        self.view.ensureCursorVisible()

    def ai_note(self, text: str):
        html = f'<div class="note">{_esc(text)}</div>'
        self.view.append(html)
        if self._streaming:
            self._ai_notes.append(html)

    def add_tool(self, call_id, name, args):
        self._ai_has_tools = True
        m = self._msgs.get(self._mid)
        if m is not None:
            m["has_tools"] = True
        arg_str = ", ".join(f"{k}={_short(str(v))}" for k, v in (args or {}).items() if k != "content")
        self.view.append(
            f'<div class="tool running" id="t{call_id}"><div class="toolhead">'
            f'<b>{_esc(name)}</b> <span class="st">运行中…</span></div>'
            f'<div class="toolargs">{_esc(arg_str)}</div>'
            f'<div class="toolout"></div></div>'
        )
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    def tool_output(self, call_id, line):
        c = self.view.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        c.insertHtml(f'<div class="tooloutline">{_esc(line)}</div>')
        self.view.moveCursor(QTextCursor.MoveOperation.End)
        self.view.ensureCursorVisible()

    def finish_tool(self, call_id, ok, output, meta=None):
        c = self.view.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        status = "✓ 完成" if ok else "× 失败"
        cls = "done" if ok else "err"
        c.insertHtml(
            f'<div class="tool {cls}"><b>{status}</b></div>'
            + (f'<div class="toolout">{_esc(output)[:6000]}</div>' if output else "")
        )
        self.view.moveCursor(QTextCursor.MoveOperation.End)
        self.view.ensureCursorVisible()

    def render_change_log(self, changes):
        """v8.29 桌面变更栏：任务结束的文件引用卡片，点击文件名在编辑器打开。

        与网页版引用卡片同语义（桌面本地可直接打开文件，无需预览/源码切换）；
        相邻重复 (path, action) 去重；渲染失败不影响主流程。"""
        try:
            items, seen = [], set()
            for c in changes or []:
                p = str((c or {}).get("path") or "").replace("\\", "/")
                a = str((c or {}).get("action") or "")
                if not p or (p, a) in seen:
                    continue
                seen.add((p, a))
                items.append((p, a))
            if not items:
                return
            rows = "".join(
                '<div style="margin:2px 0;">'
                f'<a href="act:openfile:{_esc(p)}" style="color:#7fb3ff;text-decoration:underline;">{_esc(p.rsplit("/", 1)[-1])}</a>'
                f'<span style="color:gray;font-size:11px;">　{_esc(a)}</span></div>'
                for p, a in items)
            self.view.append(
                '<div style="margin-top:6px;padding:6px 10px;border:1px solid rgba(128,128,128,.4);'
                'border-radius:6px;font-size:12px;">'
                f'<b>变更栏 · {len(items)} 个文件</b>（点击文件名在编辑器打开）{rows}</div>')
            self.view.moveCursor(QTextCursor.MoveOperation.End)
            self.view.ensureCursorVisible()
        except Exception:
            pass

    def sub_event(self, label, ev):
        t = ev.get("type")
        if t == "run_start":
            self.ai_note(f"子Agent「{label}」启动…")
        elif t == "text_delta":
            self.append_ai(ev.get("content", ""))
        elif t == "run_done":
            self.ai_note(f"✓ 子Agent「{label}」完成")
        elif t == "run_error":
            self.ai_note(f"× 子Agent「{label}」失败: {ev.get('message','')}")
        elif t == "tool_start":
            self.ai_note(f"  [{ev.get('name')}] …")

    def clear(self):
        self.view.clear()
        self.view.hide()
        self.hero.show()  # v8：空会话复位欢迎区
        self._ai_id = 0
        self._mid = 0
        self._msgs = {}
        self._tool_cards = {}
        self._ai_buf = ""
        self._ai_block = None
        self._ai_start = -1
        self._ai_end = -1
        self._ai_has_tools = False
        self._streaming = False
        self._ai_notes = []
        self._experts = {}

    def set_input_text(self, text: str):
        """将文本写入输入框并聚焦（用于采纳建议）。"""
        self.inp.setPlainText(str(text or ""))
        cur = self.inp.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self.inp.setTextCursor(cur)
        self.inp.setFocus()

    def replay_history(self, msgs):
        """v8.16：按持久化协议消息重建聊天视图（会话切换用）。

        只回放 user/assistant 文本条目；tool/system 等协议内容由工作轨迹面板呈现
        （trace_panel.set_history 与本方法由主窗口成对调用）。
        """
        self.clear()
        for m in msgs or []:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "")
            content = m.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            if role == "user":
                self.add_user(content)
            elif role == "assistant":
                self.new_ai()
                self.append_ai(content)
                self.finish_ai()
        if msgs:
            # 历史非空即视为有内容（即使全部是 tool/system 条目）——隐藏欢迎区
            self._hide_hero()
            self.view.moveCursor(QTextCursor.MoveOperation.End)


_CHAT_CSS = ""  # v7：由 _apply_theme() 从 themes_mod.chat_css() 动态设置


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _short(s, n=80):
    s = str(s)
    return s[:n] + "…" if len(s) > n else s


# ---------------------------------------------------------------------------
# Agent 后台线程
# ---------------------------------------------------------------------------
class AgentThread(QThread):
    def __init__(self, cfg, vault, ui_queue: Queue, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.vault = vault
        self.ui_queue = ui_queue
        self.task_queue = Queue()
        self.loop = None
        self.agent = None
        self._current_task = None
        self.approval = ApprovalGate()

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._consume())
        except asyncio.CancelledError:
            raise  # 必须重抛：让 wait_for 熔断 / 取消真正生效
        except Exception as e:
            try:
                self.ui_queue.put({"type": "run_error", "message": f"Agent 线程异常退出: {e}"})
            except Exception:
                pass
        finally:
            try:
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            except Exception:
                pass
            try:
                self.loop.close()
            except Exception:
                pass
            self.loop = None

    async def _emit(self, ev: dict):
        self.ui_queue.put(ev)

    async def _consume(self):
        try:
            while True:
                item = await self.loop.run_in_executor(None, self.task_queue.get)
                if item is None:
                    break
                user_msg, current_file, serial, qa_reply = item
                try:
                    # 工作区设置覆盖：pilot/copilot/suggest 模型在工作区内单独调整
                    from .workspace_config import apply_ws_overrides
                    ws_cfg = apply_ws_overrides(self.cfg, getattr(self.cfg, "workspace", ""))
                    self.agent = Agent(
                        ws_cfg, emit=self._emit, history=list(self.history),
                        vault=self.vault, approval=self.approval,
                    )
                except Exception as e:
                    log_error("Agent 初始化失败", e)
                    self.ui_queue.put({"type": "run_error", "message": f"Agent 初始化失败: {e}"})
                    continue
                try:
                    self._current_task = asyncio.ensure_future(
                        self.agent.run(user_msg, current_file=current_file,
                                       serial=serial, qa_reply=qa_reply))
                    result = await self._current_task
                    self.ui_queue.put({
                        "type": "run_final", "result": result,
                        "history": list(self.agent.history),
                    })
                    self.history = list(self.agent.history)
                except asyncio.CancelledError:
                    # 取消时也会保存历史，避免本轮已产生内容丢失
                    if self.agent is not None:
                        self.history = list(self.agent.history)
                    self.ui_queue.put({"type": "run_cancelled"})
                except Exception as e:
                    log_error("Agent 运行异常", e)
                    self.ui_queue.put({"type": "run_error", "message": str(e)})
                finally:
                    self._current_task = None
                    self.agent = None
        except asyncio.CancelledError:
            # stop() 触发的任务取消：静默退出循环
            pass
        finally:
            # 退出时拒绝所有悬挂的审批请求，避免 future 泄漏
            for call_id in list(self.approval._pending.keys()):
                try:
                    self.approval.resolve(call_id, False)
                except Exception:
                    pass

    # 从 UI 线程调用
    def submit(self, user_msg, current_file, history, serial=None, qa_reply=False):
        self.history = list(history)
        self.task_queue.put((user_msg, current_file, serial, qa_reply))

    def run_copilot_event(self, payload: dict):
        """v5: 在 Agent 线程 loop 中异步调取副驾驶（事件触发：锁异常等），结果经 ui_queue 回传。"""
        if self.loop is None or not self.loop.is_running():
            return

        async def _job():
            try:
                from .experts import copilot_check
                v = await copilot_check(self.cfg, payload)
            except Exception as e:
                v = {"kill": False, "uncertain": True, "note": str(e)}
            self.ui_queue.put({"type": "copilot_note", "verdict": v, "payload": payload})

        try:
            self.loop.call_soon_threadsafe(lambda: self.loop.create_task(_job()))
        except RuntimeError:
            pass

    def cancel_current(self):
        """只取消当前正在运行的任务，线程继续等待下一条消息。"""
        if self.loop is not None and self.loop.is_running():
            try:
                self.loop.call_soon_threadsafe(self._cancel_tasks)
            except RuntimeError:
                pass

    def stop(self):
        """结束线程：正常退出信号 + 取消进行中的任务（触发工具层 BaseException 兜底杀子进程）。"""
        self.task_queue.put(None)
        self.cancel_current()

    def _cancel_tasks(self):
        # 只取消当前 agent 运行任务，绝不误伤 _consume 消费循环本身
        t = self._current_task
        if t is not None and not t.done():
            t.cancel()

    def resolve_approval(self, call_id, approved):
        if self.loop is not None and self.loop.is_running():
            try:
                self.loop.call_soon_threadsafe(lambda: self.approval.resolve(call_id, approved))
            except RuntimeError:
                pass


# ---------------------------------------------------------------------------
# v5 专家详情对话框（左上角面包屑层级导航，文件管理器风格）
# ---------------------------------------------------------------------------
class ExpertDetailDialog(QDialog):
    def __init__(self, experts: dict, focus_eid: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("专家详情")
        self.resize(680, 460)
        layout = QVBoxLayout(self)
        self.crumb = QLabel("专家团")
        layout.addWidget(self.crumb)
        from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        root = QTreeWidgetItem(self.tree, ["专家团"])
        self._items = {}
        for eid, e in experts.items():
            label = f"[专家] {_short(e.get('title', eid), 18)}（{_short(e.get('model',''), 14)}）"
            node = QTreeWidgetItem(root, [label])
            self._fill(node, e)
            self._items[eid] = node
        self.tree.expandAll()
        self.tree.currentItemChanged.connect(self._on_sel)
        layout.addWidget(self.tree, 1)
        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(self.reject)
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)
        if focus_eid in self._items:
            self.tree.setCurrentItem(self._items[focus_eid])

    def _fill(self, node, e: dict):
        from PyQt6.QtWidgets import QTreeWidgetItem
        lvl = {"full": "全展示", "partial": "部分展示", "minimal": "少量展示"}.get(
            e.get("display_level", "full"), "全展示")
        rows = [
            f"状态：{e.get('status', '')}",
            f"模型与负载：{e.get('model', '')} · 思考 {e.get('thinking', 'medium')}"
            + (f"（展示级别：{lvl}）" if e.get("senior") else ""),
            f"申报文件：{'、'.join(e.get('files') or []) or '无（全只读）'}",
            f"成本：{e.get('cost', '') or '待实测'}",
        ]
        todos = e.get("todos") or []
        if todos:
            done_n = sum(1 for t in todos if t.get("done"))
            rows.append(f"待办（{done_n}/{len(todos)}）：" + "；".join(
                ("✓ " if t.get("done") else "○ ") + t.get("text", "")[:30] for t in todos[:12]))
        for r in rows:
            QTreeWidgetItem(node, ["  " + r])

    def _on_sel(self, cur, prev):
        if cur is None:
            return
        # 面包屑：从根到当前项的层级路径
        path = []
        node = cur
        while node is not None:
            path.append(node.text(0).strip())
            node = node.parent()
        self.crumb.setText(" ▸ ".join(reversed(path)))


# ---------------------------------------------------------------------------
# v5 锁状态对话框（含强制解锁：锁卡 2 分钟才可点）
# ---------------------------------------------------------------------------
class LockDialog(QDialog):
    def __init__(self, chat=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("租约锁状态")
        self.resize(620, 320)
        self.chat = chat
        layout = QVBoxLayout(self)
        from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["资源", "持有者", "已持锁(s)", "剩余(s)"])
        self._rows = {}
        self.tree.itemSelectionChanged.connect(self._on_sel)  # 仅连一次，refresh 不重连
        layout.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.unlock_btn = QPushButton("强制解锁（仅锁卡 ≥2 分钟）")
        self.unlock_btn.clicked.connect(self._force_unlock)
        row.addWidget(self.unlock_btn)
        row.addStretch(1)
        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.clicked.connect(self.accept)
        row.addWidget(btn)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        self.tree.clear()
        from PyQt6.QtWidgets import QTreeWidgetItem
        self._rows = {}
        for s in get_locks().snapshot():
            it = QTreeWidgetItem(self.tree, [
                s["resource"], s["owner"], str(s["age_s"]), str(s["expires_in_s"])])
            # v8.14：用 setData 存资源名——id() 作键在对象回收后可能被复用而误映射
            it.setData(0, Qt.ItemDataRole.UserRole, (s["resource"], s["age_s"]))
        self._on_sel()

    def _on_sel(self):
        sel = self.tree.selectedItems()
        ok = False
        if sel:
            res, age = sel[0].data(0, Qt.ItemDataRole.UserRole) or ("", 0)
            ok = age >= 120
        self.unlock_btn.setEnabled(ok)

    def _force_unlock(self):
        sel = self.tree.selectedItems()
        if not sel:
            return
        res, age = sel[0].data(0, Qt.ItemDataRole.UserRole) or ("", 0)
        if age < 120:
            return
        e = get_locks().force_release(res)
        owner = e.get("owner", "")
        if self.chat is not None:
            self.chat.ai_note(f"用户强制中断了 {owner} 的资源锁 {res}")
        self.refresh()


# ---------------------------------------------------------------------------
# 肝完睡觉倒计时
# ---------------------------------------------------------------------------
class SleepDialog(QDialog):
    def __init__(self, goal: str, action: str, parent=None, on_before_power=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint)
        self.setWindowTitle("肝完睡觉模式")
        self.setFixedSize(420, 240)
        self.action = action
        self.remaining = 300
        # v6 算力漂移：执行电源动作前先推送工作状态到用户服务器
        self.on_before_power = on_before_power
        layout = QVBoxLayout(self)
        title = QLabel("任务已完成，5 分钟后将关机/休眠")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _pal = {}
        if parent is not None and hasattr(parent, "cfg"):
            _pal = themes_mod.get_palette(getattr(parent.cfg, "theme", "obsidian"))
        else:
            _pal = themes_mod.get_palette("obsidian")
        _text_c = _pal.get("text", "#e2e8f0")
        _accent_c = _pal.get("accent", "#3b82f6")
        _muted_c = _pal.get("muted", "#94a3b8")
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {_text_c};")
        self.time = QLabel("05:00")
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time.setStyleSheet(f"font-size: 60px; font-weight: bold; color: {_accent_c};")
        goal_lbl = QLabel(f"目标：{goal}")
        goal_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        goal_lbl.setStyleSheet(f"color: {_muted_c};")
        row = QHBoxLayout()
        cancel = QPushButton("我还在，取消")
        cancel.clicked.connect(self._cancel)
        now = QPushButton("立即执行")
        now.clicked.connect(self._now)
        row.addWidget(cancel)
        row.addWidget(now)
        layout.addWidget(title)
        layout.addWidget(self.time)
        layout.addWidget(goal_lbl)
        layout.addLayout(row)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

    def _tick(self):
        self.remaining -= 1
        m, s = divmod(self.remaining, 60)
        self.time.setText(f"{m:02d}:{s:02d}")
        if self.remaining <= 0:
            self.timer.stop()
            self._exec()

    def _cancel(self):
        self.timer.stop()
        self.reject()

    def _now(self):
        self.timer.stop()
        self._exec()

    def done(self, r):  # v8.15 检修：Esc/系统关闭走 reject 不经过 _cancel——
        self.timer.stop()  # 定时器随对话框存活于主窗口下，不停会在到点后照常执行关机/休眠
        super().done(r)

    def _exec(self):
        # v6 算力漂移：关机/休眠前先推送完整工作状态（阻塞等待，确保漂移成功）
        if callable(self.on_before_power):
            try:
                self.on_before_power(self.action)
            except Exception:
                pass
        try:
            modes_mod.do_power_action(self.action)
        except Exception as e:
            QMessageBox.warning(self, "执行失败", str(e))
        self.accept()


# ---------------------------------------------------------------------------
# v6 算力漂移：后台推送/拉取线程（不阻塞 UI；关机前可 wait）
# ---------------------------------------------------------------------------
class DriftThread(QThread):
    def __init__(self, cfg, mode: str, payload=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.mode = mode          # "push" | "pull"
        self.payload = payload
        self.res = {"ok": False, "message": "未执行"}

    def run(self):
        try:
            if self.mode == "push":
                self.res = asyncio.run(asyncio.wait_for(
                    sync_mod.push_to_server(self.cfg, self.payload,
                                            include_cold=True, upload="auto"), 90.0))
            elif self.mode == "pull":
                self.res = asyncio.run(asyncio.wait_for(
                    sync_mod.pull_from_server(self.cfg), 15.0))
            elif self.mode == "begin":
                self.res = asyncio.run(asyncio.wait_for(
                    sync_mod.drift_begin_to_server(self.cfg, self.payload or ""), 10.0))
            elif self.mode == "status":
                self.res = asyncio.run(asyncio.wait_for(
                    sync_mod.drift_status_from_server(self.cfg, self.payload or ""), 10.0))
            elif self.mode == "finish":
                self.res = asyncio.run(asyncio.wait_for(
                    sync_mod.drift_finish_to_server(self.cfg, self.payload or ""), 10.0))
        except Exception as e:
            # v8.13：漂移线程失败必须落 Err.log（状态栏此前提示“详见 Err.log”却无记录）
            log_error(f"算力漂移{self.mode}失败", e)
            self.res = {"ok": False, "message": str(e)[:200]}


# ---------------------------------------------------------------------------
# 选区 AI 操作（Cursor 式 Ctrl+K / 右键 AI）
# ---------------------------------------------------------------------------
def _extract_code(text: str) -> str:
    m = re.search(r"```[^\n]*\n(.*?)```", text or "", re.S)
    return m.group(1).rstrip("\n") if m else ""


class SelectionActionDialog(QDialog):
    """选中代码后调用 AI 处理：解释/优化/找Bug/写注释/生成测试/自定义改写。"""

    def __init__(self, cfg, tab, action_name: str, code: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.tab = tab
        self.code = code
        self.result_text = ""
        self.setWindowTitle("AI 处理选区")
        self.resize(600, 480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.combo = QComboBox()
        self.combo.addItems(list(AI_ACTIONS.keys()))
        if action_name in AI_ACTIONS:
            self.combo.setCurrentText(action_name)
        self.instruction = QLineEdit()
        self.instruction.setPlaceholderText("自定义指令（选「改写成…」时必填）")
        form.addRow("操作", self.combo)
        form.addRow("指令", self.instruction)
        layout.addLayout(form)
        self.out = QTextEdit()
        self.out.setReadOnly(True)
        layout.addWidget(self.out, 1)
        btns = QDialogButtonBox()
        self.btn_run = btns.addButton("运行", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_apply = btns.addButton("应用到代码", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_close = btns.addButton("关闭", QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(btns)
        self.btn_run.clicked.connect(self._run)
        self.btn_apply.clicked.connect(self._apply)
        self.btn_close.clicked.connect(self.reject)
        self.combo.currentTextChanged.connect(self._on_action_changed)
        self._on_action_changed(action_name)
        self.btn_apply.setEnabled(False)

    def _on_action_changed(self, name):
        self.instruction.setEnabled(name == "改写成…")

    def _run(self):
        name = self.combo.currentText()
        custom = self.instruction.text().strip()
        if name == "改写成…" and not custom:
            QMessageBox.information(self, "提示", "请填写改写指令")
            return
        if getattr(self, "runner", None) is not None and self.runner.runner.isRunning():
            return
        instruction = custom if custom else AI_ACTIONS.get(name, "")
        self.out.clear()
        self.result_text = ""
        self._had_error = False
        self.btn_run.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.runner = SelectionDialog(self.cfg, instruction, self.code, self)
        self.runner.start(
            on_delta=self._on_delta, on_done=self._on_done, on_err=self._on_err)

    def closeEvent(self, e):
        r = getattr(self, "runner", None)
        if r is not None:
            r.stop()
        super().closeEvent(e)

    def _on_delta(self, text):
        self.result_text += text
        c = self.out.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        c.insertText(text)
        self.out.moveCursor(QTextCursor.MoveOperation.End)
        self.out.ensureCursorVisible()

    def _on_done(self):
        self.btn_run.setEnabled(True)
        if getattr(self, "_had_error", False):
            return  # 出错时不覆盖错误显示、不启用应用
        self.btn_apply.setEnabled(True)
        if self.result_text:
            self.out.setHtml(md_to_html(self.result_text))

    def _on_err(self, msg):
        self._had_error = True
        self.btn_run.setEnabled(True)
        self.out.setPlainText("错误: " + str(msg))

    def _apply(self):
        editor = self.tab.editor
        cur = editor.textCursor()
        body = _extract_code(self.result_text) or self.result_text
        if cur.hasSelection():
            cur.insertText(body)
        else:
            cur.insertText(body)
            editor.setTextCursor(cur)
        self.accept()


def _draw_tray_icon() -> QIcon:
    """自绘托盘图标（无外部资源依赖）：圆角方块 + 字母 D。"""
    # v7：从当前主题取强调色（回退蓝）
    try:
        _pal = themes_mod.get_palette(getattr(get_config(), "theme", "obsidian"))
        _accent = QColor(_pal.get("accent", "#3b82f6"))
    except Exception:
        _accent = QColor("#3b82f6")
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(_accent)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(QColor("#ffffff"))
    f = QFont("Segoe UI")
    f.setPointSize(30)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "D")
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class DeverAIApp(QMainWindow):
    _MODE_BY_IDX = {0: "chat", 1: "builder", 2: "experts"}

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        # v6.1：默认尺寸不超出可用屏（低分屏 1400x880 会出屏且不自缩）
        _scr = QApplication.primaryScreen()
        if _scr is not None:
            avail = _scr.availableSize()
            self.resize(min(1400, int(avail.width() * 0.92)),
                        min(880, int(avail.height() * 0.92)))
        else:
            self.resize(1400, 880)
        self.cfg = get_config()
        self.vault = vault_mod.Vault()
        # v8.16：多会话存储先于历史加载——开关关闭（或初始化失败）时保持 None，
        # _load_history/_save_history 走旧 desktop_history.json 单会话路径
        self.sessions = None
        if getattr(self.cfg, "ENABLE_MULTI_SESSION", True):
            try:
                self.sessions = sessions_mod.SessionStore()
                self.sessions.ensure()
            except Exception as e:
                log_error("多会话模块初始化失败，退回单会话", e)
                self.sessions = None
        self.history: list = []
        self._load_history()
        self._force_close = False   # v4: 托盘驻留时区分"隐藏"与"真退出"

        self.ui_queue = Queue()
        self.agent_thread = AgentThread(self.cfg, self.vault, self.ui_queue)
        self._busy = False
        # v8.5.6：全局建议计数
        self._suggest_total = 0
        self._suggest_processed = 0

        self._build_ui()
        self._apply_theme()
        self._build_menu()
        self._build_tray()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._pump)
        # v5 低内存模式：降低事件泵频率（少渲染少动画少进程）
        self.timer.start(120 if getattr(self.cfg, "low_memory_mode", False) else 50)

        # v5 Reaper（刽子手）：10s 纯数据结构扫描，零 token；扫出僵尸锁才触发副驾驶
        # v6：定时器无条件启动——后台长任务事件排空也挂在 _reap_locks，不受锁开关影响
        self.reaper_timer = QTimer(self)
        self.reaper_timer.timeout.connect(self._reap_locks)
        self.reaper_timer.start(10000)

        # v8.9 自动算力漂移：工作期间定时把完整状态推送到服务器（自动上漂移）
        self._last_auto_drift = time.monotonic()  # 首次触发从启动时刻起算一个完整周期
        self._auto_drift_timer = QTimer(self)
        self._auto_drift_timer.timeout.connect(self._auto_drift_tick)
        self._auto_drift_timer.start(60 * 1000)  # 每分钟检查一次是否到点

        self.agent_thread.start()
        self._init_workspace()

        # v6.5 远程指挥协调：后台线程长轮询 sync_server 接收远程命令
        self._remote_cmd: RemoteCmdClient | None = None
        if getattr(self.cfg, "ENABLE_REMOTE_CMD", False) and getattr(self.cfg, "sync_server_url", ""):
            try:
                self._remote_cmd = RemoteCmdClient(self.cfg)
                self._remote_cmd.start()
            except Exception as e:
                log_error(f"远程指挥模块启动失败: {e}")

    # ------------------------------------------------------------------
    def _build_ui(self):
        self._panel_hidden_by_user = False
        self._panel_hidden_responsive = False
        # v8：右侧图标条（Quest 风格面板管理，替代旧左活动栏）
        self.activitybar = QToolBar("面板栏", self)
        self.activitybar.setObjectName("rightstrip")
        self.activitybar.setOrientation(Qt.Orientation.Vertical)
        self.activitybar.setMovable(False)
        self.activitybar.setFixedWidth(40)
        self.activitybar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.addToolBar(Qt.ToolBarArea.RightToolBarArea, self.activitybar)
        self.act_sum = self.activitybar.addAction(svg_icon("chart"), "")
        self.act_files = self.activitybar.addAction(svg_icon("folder"), "")
        self.act_term = self.activitybar.addAction(svg_icon("terminal"), "")
        self.act_vault = self.activitybar.addAction(svg_icon("atom"), "")
        self.act_sum.setToolTip("概览（Progress/Artifacts/References）")
        self.act_files.setToolTip("文件")
        self.act_term.setToolTip("终端")
        self.act_vault.setToolTip("资产银行")
        for a in (self.act_sum, self.act_files, self.act_term, self.act_vault):
            a.setCheckable(True)
        self.act_sum.triggered.connect(lambda: self._toggle_panel("summary"))
        self.act_files.triggered.connect(lambda: self._toggle_panel("files"))
        self.act_term.triggered.connect(lambda: self._toggle_panel("terminal"))
        self.act_vault.triggered.connect(lambda: self._toggle_panel("vault"))
        self.activitybar.addSeparator()
        self.act_set = self.activitybar.addAction(svg_icon("cog"), "")
        self.act_set.setToolTip("设置")
        self.act_set.triggered.connect(lambda: self._toggle_panel("settings"))

        # v8：Pannel 右栏部件（概览/文件/终端/资产），dock 在聊天 dock 之后挂载
        self.file_tree = FileTree()
        self.file_tree.file_activated.connect(self._open_file)
        self.vault_panel = VaultPanel(self.vault)
        self.health_panel = HealthDashboard()
        self.health_panel.p0_alert.connect(lambda msg: self._guard_alert("健康仪表盘", msg))
        self.summary_panel = SummaryPanel(self.health_panel, self.vault)
        # v8.5.6：全局建议面板采纳/忽略信号
        self.summary_panel.suggest_panel.suggest_adopted.connect(self._on_suggest_adopted)
        self.summary_panel.suggest_panel.suggest_dismissed.connect(self._on_suggest_dismissed)
        self.terminal = TerminalPanel()

        # 中央工作区页：编辑器与聊天稍后装入 QStackedWidget。
        self.editors = EditorWidget()
        self.editors.cfg = self.cfg
        self.editors.ai_action_requested.connect(self._open_ai_action)
        self.editors.versions_requested.connect(self._open_version_restore)
        self.editors.ai_status.connect(self._set_ai_status)
        self.editors.quote_requested.connect(
            lambda text: self.chat.add_quote("编辑器选区", text))

        # 主聊天页（v8.5.4：不再塞进狭窄右 Dock）。
        self.chat = ChatPanel()
        self.chat.send_btn.clicked.connect(self._send)
        self.chat.stop_btn.clicked.connect(self._stop)
        self.chat.inp.keyPressEvent = self._chat_key
        # v6.3 @-mention：输入框 textChanged 检测 @ 触发文件选择 popup
        self._mention_popup = None
        if getattr(self.cfg, "ENABLE_FILE_MENTION", True):
            self._mention_popup = FileMentionPopup(
                self.chat.inp,
                file_provider=lambda: self.file_tree.list_all_files(),
                on_picked=self._on_mention_picked,
            )
            self.chat.inp.textChanged.connect(self._on_chat_input_changed)
        self.chat.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_names = list(self._MODE_BY_IDX.values())
        self.chat.mode_combo.setCurrentIndex(
            mode_names.index(getattr(self.cfg, "agent_mode", "builder"))
            if getattr(self.cfg, "agent_mode", "builder") in mode_names else 1)
        # v5 chips 联动
        self.chat.sleep_chip.setChecked(bool(getattr(self.cfg, "sleep_enabled", False)))
        self.chat.sleep_chip.toggled.connect(self._on_sleep_chip)
        self.chat.serial_chip.setChecked(bool(getattr(self.cfg, "experts_default_serial", False)))
        self.chat.serial_chip.toggled.connect(self._on_serial_chip)
        self.chat.expert_detail_requested.connect(self._open_expert_detail)
        # v8：模型弹窗 / 状态行 / 引用挂载 接线
        mp = self.chat.model_popup
        mp.row_traffic.setChecked(bool(getattr(self.cfg, "traffic_mode", False)))
        mp.row_traffic.toggled.connect(self._on_traffic_row)
        mp.row_lowram.setChecked(bool(getattr(self.cfg, "low_memory_mode", False)))
        mp.row_lowram.toggled.connect(self._on_lowram_row)
        mp.model_picked.connect(self._on_model_picked)
        mp.manage_requested.connect(self._open_settings)
        self.chat.attach_requested.connect(self._attach_file)
        self.chat.open_file_requested.connect(self._open_file)  # v8.29 变更栏引用打开
        # v8.30 方案1：窗口级文件拖放入库 + 输入框粘贴文件/剪贴板图片（Future.md 用户裁决）
        self.setAcceptDrops(True)
        self.chat.inp.setAcceptDrops(False)      # 拖放统一上浮到窗口级入库
        self.chat.inp.installEventFilter(self)
        self._refresh_model_label()
        # v8.16：多会话标签条接线（存储不可用=开关关闭时保持隐藏）
        if self.sessions is not None:
            self.chat.session_tabs.currentChanged.connect(self._on_session_tab_changed)
            self.chat.session_tabs.customContextMenuRequested.connect(self._on_session_tab_menu)
            self.chat.sess_new_btn.clicked.connect(self._new_session)
            self.chat.session_bar.show()
            self._refresh_session_tabs()
        # v8：Pannel 右栏（可左右拖动改宽，dock 分割条原生支持）
        self.dock_panel = QDockWidget("Summary", self)
        self.dock_panel.setObjectName("questpaneldock")
        self.dock_panel.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.panel_tabs = QTabWidget()
        self.panel_tabs.addTab(self.summary_panel, "概览")
        self.panel_tabs.addTab(self.file_tree, "文件")
        self.panel_tabs.addTab(self.terminal, "终端")
        self.panel_tabs.addTab(self.vault_panel, "资产")
        # v8.3：任务管理器（四层视图）
        self.task_panel = TaskManagerPanel()
        self.task_panel.rollback_requested.connect(self._on_task_rollback)
        self.panel_tabs.addTab(self.task_panel, "任务")
        # v8.3：报警只读横幅（停清理/只读/供用户选择恢复）
        panel_host = QWidget()
        pl = QVBoxLayout(panel_host)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        self._suggest_threads = set()  # v8.3 建议线程保活集合
        self._expert_runtime = {}      # v8.3 缺口3：专家运行时状态（四层树填充）
        self._expert_eid_map = {}      # v8.3 缺口3：eid → title（expert_status 无 title 时匹配）
        self.guard_banner = GuardBanner()
        self.guard_banner.restore_clicked.connect(self._guard_restore)
        self.guard_banner.dismiss_clicked.connect(self._guard_dismiss)
        pl.addWidget(self.guard_banner)
        pl.addWidget(self.panel_tabs, 1)
        self.dock_panel.setWidget(panel_host)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_panel)
        self.dock_panel.setMinimumWidth(300)
        self.panel_tabs.currentChanged.connect(self._on_panel_tab)
        # v4：终端选段引用（信号由 panels T10 提供，缺失时安全跳过）
        if hasattr(self.terminal, "quote_requested"):
            self.terminal.quote_requested.connect(
                lambda text: self.chat.add_quote("终端选段", text))
        # v8.5.4：左 Quest 导航 + 中央 Chat/Editor 状态页。
        # v8.8：新增工作轨迹页。
        self.quest_sidebar = QuestSidebar()
        self.quest_sidebar.new_quest_requested.connect(self._new_quest)
        self.quest_sidebar.chat_requested.connect(lambda: self._show_workspace_page("chat"))
        self.quest_sidebar.trace_requested.connect(lambda: self._show_workspace_page("trace"))
        self.quest_sidebar.editor_requested.connect(lambda: self._show_workspace_page("editor"))
        self.quest_sidebar.files_requested.connect(lambda: self._toggle_panel("files"))
        self.quest_sidebar.settings_requested.connect(self._open_settings)
        self.trace_panel = TracePanel()
        # v8.8：__init__ 中 _load_history 早于 _build_ui，此处补重建轨迹
        self.trace_panel.set_history(self.history)
        # v8.10：详情预览对话框里的「引用」信号透传进主对话
        self.trace_panel.quote_requested.connect(
            lambda text: self.chat.add_quote("轨迹预览", text))
        self.workspace_stack = QStackedWidget()
        self.workspace_stack.setObjectName("workspacestack")
        self.workspace_stack.addWidget(self.chat)
        self.workspace_stack.addWidget(self.editors)
        self.workspace_stack.addWidget(self.trace_panel)
        central = QSplitter(Qt.Orientation.Horizontal)
        central.setObjectName("workspaceSplitter")
        central.addWidget(self.quest_sidebar)
        central.addWidget(self.workspace_stack)
        central.setCollapsible(0, False)
        central.setCollapsible(1, False)
        central.setStretchFactor(0, 0)
        central.setStretchFactor(1, 1)
        central.setSizes([248, 800])
        self.setCentralWidget(central)
        self.quest_sidebar.set_page("chat")

        # 参考图默认展示 Summary；action、tab 与 dock 同步。
        self.panel_tabs.setCurrentIndex(0)
        self.act_sum.setChecked(True)
        self.resizeDocks([self.dock_panel], [350], Qt.Orientation.Horizontal)

        self.setStatusBar(QStatusBar())
        # v4：加载进度条（busy 态不定长滚动）
        self.progress = QProgressBar()
        self.progress.setFixedWidth(140)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress)
        # v5：租约锁状态入口（点开锁表，可强制解锁）
        self.lock_btn = QPushButton(" 0")
        self.lock_btn.setIcon(svg_icon("lock"))
        self.lock_btn.setFlat(True)
        self.lock_btn.setToolTip("租约锁状态（点开查看/强制解锁）")
        self.lock_btn.clicked.connect(lambda: LockDialog(self.chat, self).exec())
        self.statusBar().addPermanentWidget(self.lock_btn)
        # v4：同步队列状态常驻
        self.sync_lbl = QLabel("同步队列 空闲")
        self.statusBar().addPermanentWidget(self.sync_lbl)
        self.ai_status_lbl = QLabel("就绪")
        self.statusBar().addPermanentWidget(self.ai_status_lbl)
        # v8.5.6：系统状态标签（就绪/运行中/只读/建议待处理）
        self.system_status_lbl = QLabel("系统状态: 就绪")
        self.statusBar().addPermanentWidget(self.system_status_lbl)

        # v4：信息同步队列（流量模式挂起 / 关闭时冲刷）
        self.sync_q = SyncQueue(
            snapshot_provider=lambda: sync_mod.build_snapshot(self.history, self.vault),
            parent=self)
        self.sync_q.status_changed.connect(self.sync_lbl.setText)
        self.sync_q.flushed.connect(self._on_sync_flushed)
        self._apply_size_limits()
        self._update_statusbar()
        # v6 算力漂移：开机拉取服务器冷备 Agent 期间追加的消息并合并
        self._drift_pull()
        # v8.6 算力漂移状态机：若上次是漂移退出，检查服务器是否跑完，未跑完则锁定项目
        self._drift_check_startup()

    def _chat_key(self, e):
        if e.key() == Qt.Key.Key_Return and (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._send()
            return
        QPlainTextEdit.keyPressEvent(self.chat.inp, e)

    # ---- v6.3 @-mention 引用文件 ----
    def _on_chat_input_changed(self):
        """检测输入框内最近一个 @ 触发文件选择 popup。"""
        if self._mention_popup is None:
            return
        inp = self.chat.inp
        cur = inp.textCursor()
        pos = cur.position()
        text = inp.toPlainText()
        # 找光标前最近的 @
        at_idx = text.rfind("@", 0, pos)
        if at_idx == -1:
            self._mention_popup.hide()
            return
        # @ 后到光标的查询串（不含空格/换行，空格表示用户已停止输入文件名）
        query = text[at_idx + 1:pos]
        if "\n" in query or " " in query:
            self._mention_popup.hide()
            return
        # @ 必须在行首或前一个字符是空白（避免邮箱地址误触发）
        if at_idx > 0:
            prev = text[at_idx - 1]
            if prev not in " \t\n":
                self._mention_popup.hide()
                return
        # 查询串过长（超过 60 字符）视为非文件名，关闭
        if len(query) > 60:
            self._mention_popup.hide()
            return
        self._mention_popup.trigger(at_idx, query)

    def _on_mention_picked(self, rel_path: str):
        """选中文件后：读取内容插入为 quote chip，输入框补一个空格。"""
        from pathlib import Path
        try:
            ws = self.cfg.workspace
            p = Path(ws) / rel_path if ws else Path(rel_path)
            if not p.exists() or not p.is_file():
                return
            # 限制 50KB，避免超大文件撑爆引用
            content = p.read_text(encoding="utf-8", errors="replace")[:50000]
        except Exception as e:
            self.chat.ai_note(f"@ 引用 {rel_path} 失败: {e}")
            return
        self.chat.add_quote(f"@{rel_path}", content)
        # 输入框补一个空格（用户继续输入）
        inp = self.chat.inp
        cur = inp.textCursor()
        cur.insertText(" ")
        inp.setTextCursor(cur)
        inp.setFocus()

    # ---- v6.3 命令面板 Ctrl+Shift+P ----
    def _open_command_palette(self):
        if not getattr(self.cfg, "ENABLE_COMMAND_PALETTE", True):
            return
        cmds = []
        # 文件类：工作区所有文件
        try:
            for f in self.file_tree.list_all_files():
                cmds.append({"type": "file", "label": f, "data": f})
        except Exception:
            pass
        # 动作类
        cmds.append({"type": "action", "label": "切换主题: harness", "data": lambda: self._set_theme("harness")})
        cmds.append({"type": "action", "label": "切换主题: obsidian", "data": lambda: self._set_theme("obsidian")})
        cmds.append({"type": "action", "label": "切换主题: paper", "data": lambda: self._set_theme("paper")})
        cmds.append({"type": "action", "label": "切换主题: sand", "data": lambda: self._set_theme("sand")})
        cmds.append({"type": "action", "label": "切换主题: midnight", "data": lambda: self._set_theme("midnight")})
        cmds.append({"type": "action", "label": "切换模式: chat", "data": lambda: self._set_mode("chat")})
        cmds.append({"type": "action", "label": "切换模式: builder", "data": lambda: self._set_mode("builder")})
        cmds.append({"type": "action", "label": "切换模式: experts", "data": lambda: self._set_mode("experts")})
        cmds.append({"type": "action", "label": "全局协调看板（跨工作区 Agent 闸口）",
                     "data": self._open_coordination_board})
        cmds.append({"type": "action", "label": "打开设置", "data": self._open_settings})
        cmds.append({"type": "action", "label": "清空会话", "data": self._clear_history})
        cmds.append({"type": "action", "label": "选择工作区目录", "data": self._pick_workspace})
        cmds.append({"type": "action", "label": "恢复到 AI 改动前（Checkpoint）",
                     "data": self._open_checkpoint_restore})
        cmds.append({"type": "action", "label": "版本回退（快照对话框）",
                     "data": self._open_version_restore})
        pal = CommandPalette(self, cmds)
        if pal.exec() != QDialog.DialogCode.Accepted:
            return
        sel = pal.selected()
        if sel and sel.get("type") == "file":
            self._open_file(sel["data"])

    def _set_theme(self, name: str):
        self.cfg.theme = name
        self.cfg.save()
        self._apply_theme()

    def _set_mode(self, mode: str):
        self.cfg.agent_mode = mode
        self.cfg.save()
        mode_names = list(self._MODE_BY_IDX.values())
        if mode in mode_names:
            self.chat.mode_combo.setCurrentIndex(mode_names.index(mode))

    def _open_checkpoint_restore(self):
        """弹出 checkpoint 恢复菜单。"""
        menu = build_checkpoint_menu(self, self._restore_checkpoint_cb)
        menu.exec(self.chat.view.viewport().mapToGlobal(self.chat.view.rect().center()))

    def _open_version_restore(self):
        """v8.2：版本回退对话框（按文件选择历史版本恢复）。"""
        from .ide_extras import VersionRestoreDialog
        dlg = VersionRestoreDialog(self, self._restore_checkpoint_cb)
        dlg.exec()

    def _open_coordination_board(self):
        """v8.33 全局协调看板：跨工作区 Agent 闸口（人类可在损害发生前直接叫停）。"""
        from .ide_extras import CoordinationBoardDialog
        dlg = CoordinationBoardDialog(self, self.cfg.workspace if self.cfg else "")
        dlg.exec()

    def _restore_checkpoint_cb(self, bak_path: str, rel_path: str):
        """实际恢复：读 bak 内容写回原文件（恢复前再做一次快照防后悔）。"""
        from pathlib import Path
        from . import checkpoint as ckpt
        ws = self.cfg.workspace if self.cfg else ""
        root = Path(ws).resolve() if ws else Path(".").resolve()
        # P1：路径越界校验（与 tools.resolve_ws 对称），防 meta 被篡改写工作区外
        if rel_path:
            p = (root / rel_path).resolve()
            if p != root and root not in p.parents:
                raise RuntimeError(f"路径越界（仅允许工作区内）：{rel_path}")
        else:
            raise RuntimeError("缺少目标文件路径")
        # 恢复前先快照当前内容（防后悔，source=restore 便于识别回退链）
        if p.exists():
            try:
                cur = p.read_text(encoding="utf-8", errors="replace")
                ckpt.save_checkpoint(rel_path, cur, task_id="restore", source="restore")
            except Exception:
                pass
        ok, content = ckpt.restore_checkpoint(bak_path, rel_path)
        if not ok:
            raise RuntimeError(content)
        # v8.14：按当前文件行尾风格写回（快照存 canonical LF，恢复时还原原风格）
        from .storage import sniff_crlf as _sniff
        save_text(p, content, eol=_sniff(p) if p.exists() else False)
        # v8.9 回退审核：用户手动恢复 checkpoint 必须留审计记录
        try:
            from . import audit as _audit
            _audit.audit_log("checkpoint_restore", rel_path,
                             f"从 checkpoint 恢复文件（bak={bak_path}），恢复前已快照当前内容", actor="user")
        except Exception:
            pass
        # P1：恢复后同步已打开的编辑器 tab，防 stale 内容被 Ctrl+S 覆盖
        self._reload_changed(str(p))

    def _build_menu(self):
        m = self.menuBar()
        fm = m.addMenu("文件")
        act_open = QAction("打开文件…", self)
        act_open.triggered.connect(lambda: self._open_dialog())
        act_save = QAction("保存 (Ctrl+S)", self)
        act_save.setShortcut(QKeySequence.StandardKey.Save)
        act_save.triggered.connect(lambda: self.editors.save_current())
        # v6.3 命令面板 Ctrl+Shift+P
        act_palette = QAction("命令面板… (Ctrl+Shift+P)", self)
        act_palette.setShortcut("Ctrl+Shift+P")
        act_palette.triggered.connect(self._open_command_palette)
        fm.addAction(act_open)
        fm.addAction(act_save)
        fm.addAction(act_palette)
        # v8.14：PyQt6 不支持构造时以信号关键字连接，改为显式 connect
        act_saveas = QAction("另存为…", self)
        act_saveas.triggered.connect(lambda: self.editors.save_as())
        fm.addAction(act_saveas)
        fm.addSeparator()
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self._quit)
        fm.addAction(act_quit)

        am = m.addMenu("AI")
        act_ai = QAction("AI 处理选区… (Ctrl+K)", self)
        act_ai.setShortcut("Ctrl+K")
        act_ai.triggered.connect(self._ctrl_k)
        act_set = QAction("设置…", self)
        act_set.triggered.connect(self._open_settings)
        act_clear = QAction("清空会话", self)
        act_clear.triggered.connect(self._clear_history)
        am.addAction(act_ai)
        am.addSeparator()
        act_ver = QAction("版本回退（快照）…", self)
        act_ver.triggered.connect(self._open_version_restore)
        am.addAction(act_ver)
        am.addAction(act_set)
        act_reg = QAction("模型注册表…", self)
        act_reg.triggered.connect(self._open_settings)
        am.addAction(act_reg)
        am.addAction(act_clear)

        vm = m.addMenu("工作区")
        act_ws = QAction("选择工作区目录…", self)
        act_ws.triggered.connect(self._pick_workspace)
        vm.addAction(act_ws)

    def _apply_theme(self):
        """v7：按主题色板生成全局 QSS + 聊天区样式 + 图标着色。"""
        palette = themes_mod.get_palette(getattr(self.cfg, "theme", "obsidian"))
        self.setStyleSheet(themes_mod.build_qss(palette))
        self.chat.view.document().setDefaultStyleSheet(themes_mod.chat_css(palette))
        self.chat.update_palette(palette)
        # v7：图标着色随主题切换（深色=浅色图标 / 浅色=深色图标）
        icons_set_theme(
            fg=palette.get("text", "#e2e8f0"),
            accent=palette.get("accent", "#3b82f6"),
            muted=palette.get("muted", "#94a3b8"),
        )
        # P0 修复：缓存已清，重建所有已有 UI 图标使其取新色
        self.act_sum.setIcon(svg_icon("chart"))
        self.act_files.setIcon(svg_icon("folder"))
        self.act_vault.setIcon(svg_icon("atom"))
        self.act_term.setIcon(svg_icon("terminal"))
        self.act_set.setIcon(svg_icon("cog"))
        self.lock_btn.setIcon(svg_icon("lock"))
        # v8：输入卡/弹窗新增图标按钮同步取新色
        self.chat.plus_btn.setIcon(svg_icon("plus", 16))
        self.chat.stop_btn.setIcon(svg_icon("close", 16))
        self.chat.send_btn.setIcon(svg_icon("send", 16))
        self.chat.model_popup.btn_plus.setIcon(svg_icon("plus", 14))
        if self.file_tree.workspace:
            self.file_tree.set_workspace(self.file_tree.workspace)
        self.vault_panel.refresh()
        self.health_panel.refresh()
        if getattr(self, "tray", None) is not None:
            self.tray.setIcon(_draw_tray_icon())
        # v8.8：主题切换后重绘轨迹面板
        if hasattr(self, "trace_panel") and self.trace_panel is not None:
            self.trace_panel.update()

    def _apply_size_limits(self):
        """v4：窗口大小/宽窄限制（0 = 不限制）。"""
        self.setMinimumSize(max(400, int(getattr(self.cfg, "win_min_w", 900))),
                            max(300, int(getattr(self.cfg, "win_min_h", 560))))
        max_w = int(getattr(self.cfg, "win_max_w", 0))
        max_h = int(getattr(self.cfg, "win_max_h", 0))
        self.setMaximumSize(max_w if max_w > 0 else 16777215,
                            max_h if max_h > 0 else 16777215)

    # ------------------------------------------------------------------
    # v4：系统托盘
    # ------------------------------------------------------------------
    def _build_tray(self):
        self.tray = None
        if not (getattr(self.cfg, "ENABLE_TRAY", True)
                and QSystemTrayIcon.isSystemTrayAvailable()):
            return
        self.tray = QSystemTrayIcon(_draw_tray_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu(self)
        menu.addAction("显示主窗口", self._show_from_tray)
        menu.addSeparator()
        menu.addAction("退出", self._quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _show_from_tray(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # 单击：切换显示
            if self.isVisible():
                self.hide()
            else:
                self._show_from_tray()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _quit(self):
        self._force_close = True
        self.close()

    def _on_mode_changed(self, idx):
        mode = self._MODE_BY_IDX.get(idx, "builder")
        if getattr(self.cfg, "agent_mode", "") != mode:
            self.cfg.agent_mode = mode
            self.cfg.save()
            # v8.5.x 审查修复：__init__ 早期 mode_combo 触发 currentIndexChanged 时 sync_q 尚未创建
            sq = getattr(self, "sync_q", None)
            if sq is not None:
                sq.mark_dirty("settings")
                sq.flush()

    def _on_sleep_chip(self, checked):
        # v5: 肝完睡觉开关与配置同步（授权仍需设置中完成，安全红线不变）
        if bool(getattr(self.cfg, "sleep_enabled", False)) != checked:
            self.cfg.sleep_enabled = checked
            self.cfg.save()
            if checked and not (self.cfg.sleep_goal and self.cfg.sleep_authorized):
                self.chat.ai_note("肝完睡觉已开启：请在「AI → 设置」中填写目标并授权关机/休眠。")

    def _reap_locks(self):
        """v5 Reaper：纯扫描清理僵尸锁；有异常才调副驾驶（事件触发，非后台常驻）。
        v6：顺带排空后台长任务事件（线程安全 queue）。"""
        try:
            from .background import get_bg_jobs
            for ev in get_bg_jobs().drain_events():
                if ev.get("type") == "bg_started":
                    self.chat.ai_note(f"后台构建已启动（{ev.get('job_id')}）：{str(ev.get('label'))[:80]}")
                elif ev.get("type") == "bg_done":
                    mark = "✓" if ev.get("ok") else "×"
                    self.chat.ai_note(f"{mark} 后台构建完成（{ev.get('job_id')}）\n{str(ev.get('output'))[:300]}")
        except Exception:
            pass
        if not getattr(self.cfg, "ENABLE_LOCKS", True):
            self.lock_btn.setText(" 0")
            return
        try:
            dead = get_locks().reap()
        except Exception:
            return
        snap = get_locks().snapshot()
        self.lock_btn.setText(f" {len(snap)}")
        if dead:
            names = "、".join(r for r, _ in dead[:4])
            self.chat.ai_note(f"Reaper 清理僵尸锁：{names}")
            if getattr(self.cfg, "ENABLE_LOCKS", True):
                self.agent_thread.run_copilot_event({
                    "kind": "lock_anomaly",
                    "locks": [{"resource": r, "owner": e.get("owner", "")} for r, e in dead],
                })

    def _open_expert_detail(self, eid: str):
        ExpertDetailDialog(self.chat._experts, focus_eid=eid, parent=self).exec()

    def _on_sync_flushed(self, ok, message):
        self.statusBar().showMessage(("✓ 同步成功" if ok else f"同步未成功: {message}"), 5000)

    # ------------------------------------------------------------------
    def _init_workspace(self):
        if self.cfg.workspace:
            self.file_tree.set_workspace(self.cfg.workspace)
            self.health_panel.set_workspace(self.cfg.workspace)
            self.terminal.set_cwd(self.cfg.workspace)
            self._update_statusbar()
        if hasattr(self, "quest_sidebar"):
            name = Path(self.cfg.workspace).name if self.cfg.workspace else "not set"
            self.quest_sidebar.set_workspace(name)

    def _update_statusbar(self):
        model = self.cfg.model or "模型未配置"
        ws = Path(self.cfg.workspace).name if self.cfg.workspace else "未设置"
        self.statusBar().showMessage(f"模型: {model}   |   工作区: {ws}")
        # v8：英雄区副标题同步工作区名
        self.chat.set_workspace_label(f"Start in · {ws}")
        # v8.5.6：系统状态标签
        if self._busy:
            sys_status = "运行中"
        elif snap_mod.is_readonly():
            sys_status = "只读"
        elif self._suggest_total > self._suggest_processed:
            pending = self._suggest_total - self._suggest_processed
            sys_status = f"建议待处理 ({pending})"
        else:
            sys_status = "就绪"
        self.system_status_lbl.setText(f"系统状态: {sys_status}")

    def _pick_workspace(self):
        path = QFileDialog.getExistingDirectory(self, "选择工作区目录", self.cfg.workspace or os.getcwd())
        if path:
            self.cfg.workspace = path
            self.cfg.save()
            self._init_workspace()

    def _show_workspace_page(self, page: str):
        """切换中央 Chat/Editor/Trace 页，并同步左侧导航的选中态。"""
        idx = {"chat": 0, "editor": 1, "trace": 2}.get(page, 0)
        self.workspace_stack.setCurrentIndex(idx)
        self.quest_sidebar.set_page(page)
        if page == "chat":
            self.chat.inp.setFocus()

    def resizeEvent(self, event):
        """窄窗口自动收敛，避免左栏/右栏把主输入区挤成不可用窄条。"""
        super().resizeEvent(event)
        if not hasattr(self, "quest_sidebar"):
            return
        narrow = self.width() < 1080
        if narrow:
            self.quest_sidebar.setVisible(self.width() >= 720)
            if self.dock_panel.isVisible():
                self.dock_panel.hide()
                self._panel_hidden_responsive = True
                for action in (self.act_sum, self.act_files, self.act_term, self.act_vault):
                    action.setChecked(False)
        elif self._panel_hidden_responsive and not self._panel_hidden_by_user:
            self.dock_panel.show()
            self._panel_hidden_responsive = False
            self._on_panel_tab(self.panel_tabs.currentIndex())

    def _new_quest(self):
        """New Task 是明确的新会话动作：清空当前历史并回到聊天输入。"""
        self._clear_history()
        self._show_workspace_page("chat")

    def _toggle_panel(self, which):
        """v8：右栏 Pannel 面板管理（概览/文件/终端/资产 + 设置）。"""
        if which == "settings":
            self._open_settings()
            return
        idx = {"summary": 0, "files": 1, "terminal": 2, "vault": 3}[which]
        act = {"summary": self.act_sum, "files": self.act_files,
               "terminal": self.act_term, "vault": self.act_vault}[which]
        if self.dock_panel.isVisible() and self.panel_tabs.currentIndex() == idx:
            self.dock_panel.hide()
            self._panel_hidden_by_user = True
            act.setChecked(False)
        else:
            self.dock_panel.show()
            self._panel_hidden_by_user = False
            self._panel_hidden_responsive = False
            for a in (self.act_sum, self.act_files, self.act_term, self.act_vault):
                a.setChecked(a is act)
            # setCurrentIndex 会经 currentChanged 触发刷新；同页重开则手动刷一次
            if self.panel_tabs.currentIndex() == idx:
                self._on_panel_tab(idx)
            else:
                self.panel_tabs.setCurrentIndex(idx)

    def _on_panel_tab(self, idx):
        names = ("Summary", "Files", "Terminal", "Vault", "Tasks")
        if 0 <= idx < len(names):
            self.dock_panel.setWindowTitle(names[idx])
        actions = (self.act_sum, self.act_files, self.act_term, self.act_vault)
        for i, action in enumerate(actions):
            action.setChecked(i == idx and self.dock_panel.isVisible())
        if idx == 0:
            self.summary_panel.refresh_artifacts()
            self.summary_panel.refresh_refs(self.chat._quotes)

    # ---- v8：模型弹窗状态行 / 模型选择 ----
    def _on_serial_chip(self, checked):
        self.cfg.experts_default_serial = bool(checked)
        self.cfg.save()

    def _on_traffic_row(self, checked):
        self.cfg.traffic_mode = bool(checked)
        self.cfg.save()
        if not checked:
            self.sync_q.flush(force=True)

    def _on_lowram_row(self, checked):
        self.cfg.low_memory_mode = bool(checked)
        self.cfg.save()
        self._apply_size_limits()

    def _on_model_picked(self, role, mid):
        if role == "copilot":
            self.cfg.copilot_model = mid
        else:
            self.cfg.model = mid
        self.cfg.save()
        self.sync_q.mark_dirty("settings")
        self.sync_q.flush()
        self._refresh_model_label()
        self._update_statusbar()

    def _refresh_model_label(self):
        m = get_model(self.cfg.model) if self.cfg.model else None
        name = m.display_name() if m else (self.cfg.model or "模型未配置")
        self.chat.set_model_label(name)

    # ---------------- v8.30 上传入库（方案1：拖拽/粘贴，Future.md 用户裁决） ----------------
    def dragEnterEvent(self, ev):
        md = ev.mimeData()
        if md.hasUrls() and any(u.isLocalFile() for u in md.urls()):
            ev.acceptProposedAction()
        else:
            super().dragEnterEvent(ev)

    def dropEvent(self, ev):
        md = ev.mimeData()
        if md.hasUrls():
            local = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if local:
                ev.acceptProposedAction()
                self._ingest_paths(local)
                return
        super().dropEvent(ev)

    def eventFilter(self, obj, ev):
        # 输入框：Ctrl+V 粘贴文件/剪贴板图片 → 入库并挂引用条；纯文本粘贴走默认
        if obj is getattr(self.chat, "inp", None):
            if ev.type() == QEvent.Type.KeyPress and ev.matches(QKeySequence.StandardKey.Paste):
                md = QApplication.clipboard().mimeData()
                if md.hasUrls():
                    local = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
                    if local:
                        self._ingest_paths(local)
                        return True
                if md.hasImage() and self._ingest_clipboard_image():
                    return True
        return super().eventFilter(obj, ev)

    def _ingest_paths(self, paths):
        """本机文件批量入库 uploads/ 并挂引用条（指针式引用，AI 用 read_file 读取相对路径）。"""
        from .uploads_ingest import ingest_file
        if not paths:
            return
        ws = self.cfg.workspace
        if not ws:
            QMessageBox.information(self, "提示", "请先授权工作区，再入库文件。")
            return
        ok_n = 0
        for p in paths:
            ok, info = ingest_file(ws, p)
            if ok:
                ok_n += 1
                self.chat.add_quote(
                    info,
                    f"[文件已入库工作区：{info}]（AI 可用 read_file 读取该相对路径；"
                    "二进制用户资产请走 copy_user_asset 工作副本语义，原文件语义不变）")
            else:
                self.statusBar().showMessage(f"入库失败 {Path(p).name}: {info}", 6000)
        if ok_n:
            self.statusBar().showMessage(
                f"已入库 {ok_n} 个文件到 uploads/（引用条随下一条消息发送给 AI）", 6000)

    def _ingest_clipboard_image(self):
        """剪贴板图片 → uploads/pasted-<ts>.png。成功返回 True。"""
        from .uploads_ingest import alloc_dest
        img = QApplication.clipboard().image()
        if img is None or img.isNull():
            return False
        dest, info = alloc_dest(self.cfg.workspace,
                                f"pasted-{time.strftime('%Y%m%d-%H%M%S')}.png")
        if dest is None:
            self.statusBar().showMessage(f"图片入库失败: {info}", 6000)
            return False
        if not img.save(str(dest), "PNG"):
            self.statusBar().showMessage("图片落盘失败", 6000)
            return False
        self.chat.add_quote(info, f"[图片已入库工作区：{info}]（AI 可用图像工具读取该相对路径）")
        self.statusBar().showMessage(f"剪贴板图片已入库 {info}", 6000)
        return True

    def _attach_file(self):
        start = self.cfg.workspace or os.getcwd()
        path, _ = QFileDialog.getOpenFileName(self, "添加文件引用", start)
        if not path:
            return
        try:
            # 与 @-mention 对齐：引用截断 50KB，防 GB 级文件打爆内存/请求
            content = Path(path).read_text(encoding="utf-8", errors="replace")[:50000]
        except OSError:
            return
        self.chat.add_quote(Path(path).name, content)

    def _set_ai_status(self, text):
        self.ai_status_lbl.setText(text)

    def _ctrl_k(self):
        tab = self.editors._active
        if tab is None:
            QMessageBox.information(self, "提示", "请先打开一个文件")
            return
        self._open_ai_action(tab, "改写成…")

    def _open_ai_action(self, tab, action_name):
        if tab is None:
            return
        code = tab.editor.textCursor().selectedText().replace("\u2029", "\n")
        if not code and action_name != "改写成…":
            QMessageBox.information(self, "提示", "请先在编辑器中选中代码")
            return
        dlg = SelectionActionDialog(self.cfg, tab, action_name, code, self)
        dlg.exec()

    def _open_settings(self):
        was_traffic = bool(self.cfg.traffic_mode)
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            self._init_workspace()
            self._update_statusbar()
            # v8：设置对话框可能改写 model/traffic/lowram/sleep/serial，回同步状态载体
            mp = self.chat.model_popup
            mp.row_traffic.setChecked(bool(self.cfg.traffic_mode))
            mp.row_lowram.setChecked(bool(self.cfg.low_memory_mode))
            self.chat.sleep_chip.setChecked(bool(getattr(self.cfg, "sleep_enabled", False)))
            self.chat.serial_chip.setChecked(bool(getattr(self.cfg, "experts_default_serial", False)))
            self._refresh_model_label()
            # 同步新配置到 Agent 线程
            self.agent_thread.cfg = self.cfg
            # v4：主题/窗口限制/托盘可能变更
            self._apply_theme()
            self._apply_size_limits()
            self._apply_multi_session_switch()   # v8.16.1：多会话开关即时生效（决策 118）
            self.sync_q.mark_dirty("settings")
            if was_traffic and not self.cfg.traffic_mode:
                # 关闭流量模式：立即冲刷同步队列
                self.sync_q.flush(force=True)
            else:
                self.sync_q.flush()

    # ------------------------------------------------------------------
    # 文件
    # ------------------------------------------------------------------
    def _open_file(self, rel: str):
        ws = Path(self.cfg.workspace)
        p = ws / rel if ws else Path(rel)
        try:
            if p.stat().st_size > 2 * 1024 * 1024:
                QMessageBox.warning(self, "无法打开", f"文件超过 2MB，编辑器不支持打开：{rel}")
                return
            content = p.read_text(encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "无法打开", str(e))
            return
        self.editors.open_path(str(p), content)
        self._show_workspace_page("editor")

    def _open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开文件", self.cfg.workspace)
        if path:
            try:
                if Path(path).stat().st_size > 2 * 1024 * 1024:
                    QMessageBox.warning(self, "无法打开", "文件超过 2MB，编辑器不支持打开")
                    return
                content = Path(path).read_text(encoding="utf-8")
            except Exception as e:
                QMessageBox.warning(self, "无法打开", str(e))
                return
            self.editors.open_path(path, content)
            self._show_workspace_page("editor")

    def _reload_changed(self, abs_path: str):
        self.file_tree.set_workspace(self.cfg.workspace)
        if not self.editors.is_open(abs_path):
            return
        try:
            content = Path(abs_path).read_text(encoding="utf-8")
        except OSError:
            return
        self.editors.reload_if_open(abs_path, content)

    def _reload_workspace_after_restore(self):
        """快照/回退恢复后：刷新文件树并重载全部已打开 tab，防止旧内容再次保存覆盖。"""
        try:
            self.file_tree.set_workspace(self.cfg.workspace)
        except Exception:
            pass
        for tab in list(getattr(self.editors, "tabs", []) or []):
            path = getattr(tab, "path", "")
            if not path:
                continue
            try:
                content = Path(path).read_text(encoding="utf-8")
                self.editors.reload_if_open(path, content)
            except OSError:
                continue

    # ------------------------------------------------------------------
    # 聊天
    # ------------------------------------------------------------------
    def _send(self):
        text = self.chat.inp.toPlainText().strip()
        if not text or self._busy:
            return
        if not self.cfg.api_key or not self.cfg.model:
            QMessageBox.information(self, "提示", "请先在「AI → 设置」中配置 API Key 与模型。")
            return
        if not self.agent_thread.isRunning():
            # 线程异常退出后自愈重建，避免聊天永久卡死
            # v8.14：清理旧线程对象，防 QThread 泄漏
            old = self.agent_thread
            if old is not None:
                try:
                    old.wait(1000)
                except Exception:
                    pass
            self.agent_thread = AgentThread(self.cfg, self.vault, self.ui_queue)
            self.agent_thread.start()
        # v4：引用条拼装——古早内容回归入口
        quotes = self.chat.take_quotes()
        parts = []
        for i, q in enumerate(quotes, 1):
            parts.append(f"【引用内容 {i} · {q['label']}】\n{q['content']}")
        parts.append("【正文】\n" + text)
        composed = "\n\n".join(parts)
        self.chat.inp.clear()
        self.chat.add_user(text, quote_count=len(quotes))
        # v8.16：空白名「新会话」且尚无历史时，用首条消息摘要自动命名并刷新标签
        if self.sessions is not None and self.sessions.autotitle_if_blank(
                self.sessions.active_id, text):
            self._refresh_session_tabs()
        # 本条用户消息所属对话块 uid：供消息按钮「禁止自动引用」使用
        uid = ctx_expert.block_uid(composed)
        self.chat.set_pending_uid(uid)
        self._busy = True
        self._update_statusbar()
        self.chat.send_btn.setEnabled(False)
        self.chat.stop_btn.setEnabled(True)
        self.progress.setVisible(True)
        # 注意：不要在此处 append 到 self.history——
        # Agent.run 会自行追加用户消息，run_final 回传完整 history 并落盘，避免消息重复
        cur = self.editors.current_path()
        # v8.3：新一轮对话——先开启任务级快照再 submit（P3-29：防首个工具调用抢先无回退）
        if getattr(self.cfg, "ENABLE_SESSION_SNAP", True):
            self._current_round = snap_mod.begin_round(self.cfg)
            snap_mod.set_current_round(self._current_round)
            snap_mod.set_round_input(self._current_round, text)
            self.task_panel.set_round(self._current_round)
            self.task_panel.set_status("建立备份中")
        # v5: 专家团单次串行开关（发送后自动复位，除非配置默认串行）
        serial = None
        if self.chat.serial_chip.isChecked():
            serial = True
            if not getattr(self.cfg, "experts_default_serial", False):
                self.chat.serial_chip.setChecked(False)
        # v5: 问答模式——上轮留有反馈表单时，回复直接交总司令（路由判据在 agent 侧）
        qa_reply = bool(self.chat.qa_chip.isChecked())
        self.agent_thread.submit(composed, cur, self.history, serial=serial, qa_reply=qa_reply)
        # v8.3：建议系统——消息发送完成时启动后台建议生成（TRAE CUE 式）
        self._maybe_start_suggest(text)

    def _stop(self):
        self.agent_thread.cancel_current()

    # ------------------------------------------------------------------
    # v8.3：报警只读 / 快照提交 / 建议系统
    # ------------------------------------------------------------------
    def _inject_tree_issues(self, rid: str, issues: list):
        """v8.3 缺口5：tree 校验失败 → 按问题文件分组注入下一轮上下文。

        按 rel 维度存储（专家读取时用自己申报的 files 过滤），
        避免依赖专家锁表时序与跨轮 id 变化。"""
        try:
            from . import session_snap as snap2
            by_rel: dict = {}
            for i in issues[:20]:
                rel = i.get("rel", "") or "unknown"
                note = f"{rel} {i.get('detail', '')}"
                by_rel.setdefault(rel, []).append(note)
            for rel, probs in by_rel.items():
                snap2.inject_problems(rid, rel, probs)
        except Exception:
            pass

    def _refresh_task_panel(self):
        """v8.3 缺口3：用专家运行时状态填充任务管理器四层树。"""
        try:
            experts = []
            for e in (self._expert_runtime or {}).values():
                experts.append({
                    "id": e.get("title", ""), "title": e.get("title", ""),
                    "model": e.get("model", ""), "params": e.get("params", ""),
                    "status": e.get("status", ""),
                })
            self.task_panel.set_state(
                commander={"task": getattr(self, "_task_summary", "规划中"),
                           "status": "执行中"},
                experts=experts)
        except Exception:
            pass

    def _guard_alert(self, source: str, reason: str):
        """报警：停止快照清理 + AI 只读 + 事件泵展示 pannel 横幅。"""
        full = f"{source}: {reason}"
        was_readonly = snap_mod.is_readonly()
        snap_mod.set_readonly(True, full)
        # P2-13：幂等——已处于只读且同因不再重复弹横幅（防健康仪表盘 60s 刷屏）
        if was_readonly and snap_mod.alert_reason() == full:
            return
        try:
            self.ui_queue.put({"type": "guard_alert", "reason": full})
        except Exception:
            pass

    def _guard_dismiss(self):
        """用户解除只读。"""
        snap_mod.set_readonly(False)
        self.guard_banner.clear_alert()
        self.task_panel.set_status("空闲")
        self.chat.ai_note("已解除只读，AI 可继续操作。")
        self._update_statusbar()

    def _guard_restore(self):
        """打开快照恢复对话框（任务级回退点 + 历史会话 + 文件版本回退）。"""
        # 优先任务级：回退点/会话（v8.3 P1-10 接线）
        try:
            from .ide_extras import RoundRestoreDialog
            dlg = RoundRestoreDialog(self, workspace=getattr(self.cfg, "workspace", "") or "",
                                     on_restored=self._reload_workspace_after_restore)
            dlg.exec()
        except Exception:
            pass
        # 文件级版本回退
        try:
            dlg2 = VersionRestoreDialog(self, self._restore_checkpoint_cb)
            dlg2.exec()
        except Exception:
            pass

    def _on_task_rollback(self, round_id: str):
        """任务管理器「回退到本轮快照」按钮回调：恢复到该轮最近一次工具调用前。"""
        if not round_id:
            return
        try:
            from . import session_snap as _snap
            points = _snap.rollback_points(round_id)
            if not points:
                QMessageBox.information(self, "回退", "该轮没有可用的回退点。")
                return
            target = points[0]
            ans = QMessageBox.question(
                self, "回退确认",
                f"将工作区文件恢复到「{target.get('tool') or '工具调用'}」"
                f"（第 {target.get('round_no')} 次调用，{target.get('ts')}）之前？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                return
            ok, note = _snap.restore_rollback_point(
                round_id, int(target.get("round_no") or 0),
                getattr(self.cfg, "workspace", "") or "")
            if not ok:
                QMessageBox.warning(self, "回退失败", note)
                return
            QMessageBox.information(self, "已回退", note)
            # 恢复后刷新文件树与已打开编辑器，防止旧 tab 再次保存覆盖恢复结果
            for rel in (target.get("files") or []):
                try:
                    abs_path = str((Path(getattr(self.cfg, "workspace", "")) / str(rel)).resolve())
                    self._reload_changed(abs_path)
                except Exception:
                    pass
        except Exception as e:
            log_error("任务回退点恢复失败", e)
            QMessageBox.warning(self, "回退失败", str(e))

    def _finish_round(self, keep_rollback: bool = False):
        """v8.3 回复完成：后台线程提交任务级快照（树扫描/校验/zip/标题/清理）。

        keep_rollback=True（出错/取消）：保留本轮回退点供用户恢复。"""
        if not getattr(self.cfg, "ENABLE_SESSION_SNAP", True):
            return
        rid = getattr(self, "_current_round", "") or snap_mod.current_round()
        if not rid:
            return
        try:
            import threading
            th = threading.Thread(target=self._finish_round_worker,
                                  args=(rid, keep_rollback), daemon=True)
            self._finish_thread = th
            self.task_panel.set_status("建立快照中")
            th.start()
        except Exception:
            pass

    def _finish_round_worker(self, rid: str, keep_rollback: bool = False):
        """后台：依赖树扫描+校验（问题则报警）、提交快照（zip+标题+清理旧轮）。"""
        # P2-6：workspace 有效性判断（空/不存在时跳过树扫描与打包，避免扫 cwd）
        ws = getattr(self.cfg, "workspace", "") or ""
        ws_valid = bool(ws) and Path(ws).is_dir()
        tree = {}
        try:
            from pathlib import Path
            todo_text = ""
            dev_text = ""
            try:
                ws_root = Path(ws) if ws_valid else Path(".")
                tp = ws_root / "todo.md"
                if tp.exists():
                    todo_text = tp.read_text(encoding="utf-8", errors="ignore")
                dp = ws_root / "dev_log"
                if dp.exists():
                    logs = sorted(dp.glob("*.md"))
                    if logs:
                        dev_text = logs[-1].read_text(encoding="utf-8", errors="ignore")[:2000]
            except Exception:
                pass
            # P2-4：树扫描/校验单独 try——校验异常不得吞掉 commit（快照不可丢）
            if ws_valid and getattr(self.cfg, "ENABLE_DEP_TREE", True):
                try:
                    tree = deps_mod.scan_workspace(ws, snap_mod.get_tree(rid))
                    issues = deps_mod.check_tree(tree, ws)
                    if issues:
                        self._guard_alert("依赖树校验失败",
                                          "; ".join(f"{i['rel']} {i['detail']}" for i in issues[:5]))
                        # v8.3 缺口5：按问题文件分组注入下一轮上下文
                        self._inject_tree_issues(rid, issues)
                except Exception as e:
                    try:
                        log_error("依赖树校验异常", e)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            # P1-8：后台线程内跑 AI 标题生成（asyncio.run，工作线程无 running loop）
            def _ai_title(cfg, msgs):
                try:
                    import asyncio
                    from .llm import chat_complete
                    resp = asyncio.run(chat_complete(cfg, msgs, max_tokens=60, timeout=25.0))
                    return (resp or {}).get("content", "").strip()
                except Exception:
                    return ""
            snap_mod.commit_round(
                rid, ws, self.cfg,
                title=snap_mod.generate_title(rid, self.cfg, llm_fn=_ai_title),
                todo_text=todo_text, devlog_text=dev_text, tree=tree,
                keep_rollback=keep_rollback)
            try:
                self.ui_queue.put({"type": "note", "note": "本轮快照已提交"})
            except Exception:
                pass
        except Exception as e:
            try:
                log_error("快照提交失败", e)
            except Exception:
                pass
        finally:
            # P1-6/P3-25：确保清空当前轮（防下一轮二次 commit / 状态栏卡"建立快照中"）
            try:
                if snap_mod.current_round() == rid:
                    snap_mod.set_current_round("")
                self.ui_queue.put({"type": "note", "note": "本轮快照处理结束"})
                # P2-1：复位交给 UI 线程（round_done 事件分支），防后台线程改 UI 属性
                self.ui_queue.put({"type": "round_done"})
            except Exception:
                pass

    def _maybe_start_suggest(self, user_input: str):
        """v8.3 建议系统：消息发送完成时后台生成建议（TRAE CUE 式）。"""
        if not getattr(self.cfg, "ENABLE_SUGGEST", True):
            return
        if not getattr(self.cfg, "api_key", ""):
            return
        try:
            th = _SuggestThread(self.cfg, user_input,
                                self.chat.mode_combo.currentText())
            th.result_ready.connect(self._show_suggestions)
            th.finished.connect(lambda t=th: self._suggest_threads.discard(t))
            self._suggest_threads.add(th)  # 保活集合（FreqErr：QThread 无引用被 GC）
            th.start()
        except Exception:
            pass

    def _show_suggestions(self, items: list):
        """展示 AI 精选建议（功能/技术/美术三类，限量）。"""
        self.chat.show_suggestions(items)
        # v8.5.6：同步刷新全局建议面板与计数
        self._suggest_total = len(items or [])
        self._suggest_processed = 0
        self.summary_panel.suggest_panel.set_suggestions(items or [])
        self._update_statusbar()

    def _on_suggest_adopted(self, item: dict, detail: str):
        """用户从全局建议面板采纳建议：写入输入框、标记已处理并移除该卡片。"""
        panel = self.summary_panel.suggest_panel
        self.chat.set_input_text(detail)
        self._suggest_processed = panel.mark_processed()
        panel.remove_suggestion(item)
        self._suggest_processed, self._suggest_total = panel.counts
        self.chat.ai_note("已采纳建议，文本已填入输入框。")
        self._update_statusbar()

    def _on_suggest_dismissed(self, item: dict):
        """用户忽略某条建议：从面板精确移除被点击的卡片。"""
        panel = self.summary_panel.suggest_panel
        if panel.remove_suggestion(item):
            self._suggest_processed, self._suggest_total = panel.counts
            self._update_statusbar()

    # ------------------ v8.16 多会话标签 ------------------
    def _apply_multi_session_switch(self):
        """v8.16.1：设置接受后即时重评 ENABLE_MULTI_SESSION（此前需重启才生效）。

        AI 回合进行中不切换存储形态（replay 会冲掉流式视图），提示用户稍后再开设置；
        关闭方向依赖决策 116 的镜像写（活跃会话已实时镜像旧档），回落零丢失。
        """
        want = bool(getattr(self.cfg, "ENABLE_MULTI_SESSION", True))
        if self._busy:
            self.statusBar().showMessage("AI 回合进行中：多会话开关请稍后重新打开设置以生效", 4000)
            return
        if want:
            if self.sessions is None:
                try:
                    store = sessions_mod.SessionStore()
                    store.ensure()
                    self.sessions = store
                    self._load_history()
                    self.chat.replay_history(self.history)
                    self._refresh_session_tabs()
                except Exception as e:
                    log_error("多会话存储初始化失败，保持单会话", e)
                    self.sessions = None
            self.chat.session_bar.setVisible(self.sessions is not None)
        elif self.sessions is not None:
            self._save_history()          # 当前内存态补一次落盘（槽位+镜像）
            self.sessions = None          # 回落后 _load_history 读旧档（即刚才的镜像）
            self._load_history()
            self.chat.replay_history(self.history)
            self.chat.session_bar.hide()

    def _refresh_session_tabs(self):
        """全量重绘标签条（会话数量为个位~十位，无需增量优化）。"""
        if self.sessions is None:
            return
        bar = self.chat.session_tabs
        bar.blockSignals(True)
        while bar.count():
            bar.removeTab(0)
        active = self.sessions.active_id
        for s in self.sessions.sessions():
            idx = bar.addTab(s["name"])
            bar.setTabData(idx, s["id"])
            bar.setTabToolTip(idx, f"{s['name']}\n创建 {s['created_at']} · 更新 {s['updated_at']}")
            if s["id"] == active:
                bar.setCurrentIndex(idx)
        bar.blockSignals(False)

    def _on_session_tab_changed(self, idx: int):
        if self.sessions is None or idx < 0:
            return
        sid = self.chat.session_tabs.tabData(idx)
        if not sid or sid == self.sessions.active_id:
            return
        if not self._switch_session(sid):
            self._refresh_session_tabs()   # 切换被拒绝 → 标签视觉复位到真实活跃项

    def _on_session_tab_menu(self, pos):
        if self.sessions is None:
            return
        bar = self.chat.session_tabs
        idx = bar.tabAt(pos)
        if idx < 0:
            return
        sid = bar.tabData(idx)
        menu = QMenu(self)
        menu.addAction("重命名", lambda: self._rename_session(sid))
        menu.addAction("关闭会话", lambda: self._close_session(sid))
        menu.exec(bar.mapToGlobal(pos))

    def _new_session(self):
        if self.sessions is None:
            return
        if self._busy:
            self.statusBar().showMessage("AI 回合进行中，请先停止再新建会话", 4000)
            return
        self._save_history()
        self.sessions.create("")
        self._load_history()                    # 活跃已指向新会话 → 载入空历史并重建轨迹
        self.chat.replay_history(self.history)  # 空列表 → 复位欢迎区
        self._refresh_session_tabs()

    def _rename_session(self, sid: str):
        if self.sessions is None:
            return
        s = self.sessions.find(sid)
        if s is None:
            return
        name, ok = QInputDialog.getText(self, "重命名会话", "名称：", text=s["name"])
        if ok and self.sessions.rename(sid, name):
            self._refresh_session_tabs()

    def _close_session(self, sid: str):
        """关闭标签=移除会话条目（不删历史文件，可手动从 data/chat_history 找回）。"""
        if self.sessions is None:
            return
        if len(self.sessions.sessions()) <= 1:
            # 最后一个会话不可移除——等价「清空对话」保留会话本身（Design 决策 116）
            self._clear_history()
            self.statusBar().showMessage("最后一个会话已清空内容（会话保留）", 4000)
            return
        closing_active = sid == self.sessions.active_id
        if closing_active:
            if self._busy:
                self.statusBar().showMessage("AI 回合进行中，请先停止再关闭该会话", 4000)
                return
            self._save_history()
        self.sessions.remove(sid)               # 活跃被关时 Store 自动切到相邻会话
        if closing_active:
            self._load_history()
            self.chat.replay_history(self.history)
        self._refresh_session_tabs()

    def _switch_session(self, sid: str) -> bool:
        """切换活跃会话：先落盘当前，再载入目标（聊天区回放 + 轨迹重建）。"""
        if self.sessions is None or sid == self.sessions.active_id:
            return False
        if self._busy:
            # Design 决策 115：同一时刻全局仅一个 AI 回合；运行中切换会让流式事件
            # 写错会话视图、审批上下文错绑——与网页版 v8.13 守卫同策略
            self.statusBar().showMessage("AI 回合进行中，请先停止再切换会话", 4000)
            return False
        self._save_history()
        self.sessions.set_active(sid)
        self._load_history()
        self.chat.replay_history(self.history)
        s = self.sessions.find(sid)
        self.statusBar().showMessage(f"已切换到会话：{s['name'] if s else sid}", 3000)
        return True

    def _clear_history(self):
        self.history = []
        self._save_history()
        self.chat.clear()
        # v8.5.6：清空会话时重置建议计数
        self._suggest_total = 0
        self._suggest_processed = 0
        self.summary_panel.suggest_panel.clear()
        # v8.8：清空轨迹
        if hasattr(self, "trace_panel") and self.trace_panel is not None:
            self.trace_panel.clear()
        self._update_statusbar()

    def _load_history(self):
        try:
            if self.sessions is not None:
                # v8.16：从活跃会话的历史文件加载（首启迁移旧单会话数据由 Store.ensure 完成）
                self.history = self.sessions.load_history(self.sessions.active_id)
            else:
                self.history = load_json(HISTORY_PATH, []) or []
        except Exception:
            self.history = []
        # v8.8：加载历史后重建轨迹
        if hasattr(self, "trace_panel") and self.trace_panel is not None:
            self.trace_panel.set_history(self.history)

    def _save_history(self):
        try:
            if self.sessions is not None:
                # v8.16：写当前活跃会话文件 + 镜像一份到旧 desktop_history.json——
                # 用户之后关闭多会话开关时，仍能看到最近活跃会话的内容（回退安全方向）
                sid = self.sessions.active_id
                self.sessions.save_history(sid, self.history)
                self.sessions.touch(sid)
                try:
                    save_json(sessions_mod.LEGACY_HISTORY_PATH, self.history)
                except Exception:
                    pass
            else:
                save_json(HISTORY_PATH, self.history)
        except Exception:
            pass
        self.sync_q.mark_dirty("history")
        self.sync_q.flush()

    # ------------------------------------------------------------------
    # 事件泵（UI 线程）
    # ------------------------------------------------------------------
    def _pump(self):
        # 每 tick 最多处理 200 条，避免大输出一次排空导致 UI 长时间冻结
        n = 0
        try:
            while n < 200:
                ev = self.ui_queue.get_nowait()
                self._handle_event(ev)
                n += 1
        except Empty:
            pass
        except Exception as e:
            log_error("UI 事件泵异常", e)

    def _handle_event(self, ev):
        ev = ev or {}
        t = ev.get("type")
        try:
            # v8.8：同步到工作轨迹面板（只读，异常自吞避免污染主错误日志）
            try:
                if hasattr(self, "trace_panel") and self.trace_panel is not None:
                    self.trace_panel.add_event(ev)
            except Exception:
                pass
            if t == "run_start":
                self.progress.setVisible(True)
                self.chat.new_ai()
                # v8.3 缺口4：任务流程状态流（建立备份中→建立连接中→执行中）
                try:
                    self.task_panel.set_status("建立连接中")
                except Exception:
                    pass
            elif t == "gate_start":
                self.progress.setVisible(True)
                self.statusBar().showMessage("上下文守门专家检查中…", 5000)
            elif t == "ctx_gate":
                self.chat.add_gate_note(ev.get("stats") or {}, ev.get("reason", ""),
                                        ev.get("banned") or [])
            elif t == "guard":
                self.chat.add_guard(bool(ev.get("ok")), ev.get("note", ""))
            elif t == "text_delta":
                self.chat.append_ai(ev.get("content", ""))
            elif t == "tool_start":
                self.chat.add_tool(ev.get("call_id"), ev.get("name"), ev.get("args"))
                if ev.get("name") == "run_command" and ev.get("args", {}).get("command"):
                    self.terminal.append("> " + ev["args"]["command"])
            elif t == "tool_result":
                self.chat.finish_tool(ev.get("call_id"), ev.get("ok"), ev.get("output"), ev.get("meta"))
            elif t == "file_changes":  # v8.29 变更栏
                self.chat.render_change_log(ev.get("changes") or [])
            elif t == "cmd_output":
                self.chat.tool_output(ev.get("call_id"), ev.get("line", ""))
                self.terminal.append(ev.get("line", ""))
            elif t == "cmd_done":
                self.chat.ai_note(f"■ 退出码 {ev.get('rc')}")
            elif t == "approval_needed":
                self._ask_approval(ev)
            elif t == "diff_preview_needed":
                self._show_diff_preview(ev)
            elif t == "subagent":
                self.chat.sub_event(ev.get("label"), ev.get("event") or {})
            # ---- v5 专家团 / 副驾驶 事件 ----
            elif t == "expert_plan_start":
                self.progress.setVisible(True)
                self.statusBar().showMessage("总司令规划中…", 5000)
            elif t == "expert_plan":
                self.chat.add_expert_plan(ev)
                # v8.3 P1-10：填充任务管理器四层树（L1 总司令 → L2 专家）
                try:
                    self._task_summary = ev.get("summary", "规划中")
                    rt = {}
                    for i, e in enumerate(ev.get("tasks") or []):
                        title = e.get("title", f"任务{i}")
                        rt[title] = {"title": title, "model": e.get("model", ""),
                                     "params": "", "status": "规划中"}
                    self._expert_runtime = rt
                    self._refresh_task_panel()
                    # v8.3 缺口4：进入执行阶段
                    self.task_panel.set_status("执行中")
                except Exception:
                    pass
            elif t == "expert_start":
                self.chat.add_expert_card(ev)
                # v8.3 缺口3：专家开工 → 更新运行时（模型+状态）
                try:
                    eid = ev.get("expert_id", "")
                    title = ev.get("title", eid)
                    self._expert_eid_map[eid] = title
                    e = self._expert_runtime.setdefault(title, {"title": title})
                    e["model"] = ev.get("model", "") or e.get("model", "")
                    e["params"] = ev.get("thinking", "") or e.get("params", "")
                    e["status"] = "执行中"
                    self._refresh_task_panel()
                except Exception:
                    pass
            elif t == "expert_status":
                self.chat.note_expert_status(ev)
                # v8.3 缺口3：专家状态变更 → 刷新四层树
                try:
                    eid = ev.get("expert_id", "")
                    title = self._expert_eid_map.get(eid, eid)
                    e = self._expert_runtime.get(title)
                    if e is None:
                        e = self._expert_runtime.setdefault(title, {"title": title})
                    e["status"] = ev.get("status", "") or e.get("status", "")
                    self._refresh_task_panel()
                except Exception:
                    pass
            elif t == "expert_todos":
                self.chat.set_expert_todos(ev)
            elif t == "expert_form":
                self.chat.add_expert_form(ev.get("items") or [], bool(ev.get("final", True)))
            elif t == "expert_note":
                self.chat.ai_note(str(ev.get("note", "")))
            elif t == "copilot_block":
                self.chat.add_copilot_card(ev.get("note", ""))
                # v8.3 P2-21：copilot 拦截终止统一报警只读（写内容被掐断同样触发）
                self._guard_alert("copilot 拦截", str(ev.get("note", "")))
            elif t == "copilot_note":
                v = ev.get("verdict") or {}
                if v.get("kill"):
                    self.chat.add_copilot_card(v.get("note", ""))
                    # v8.3：copilot 拦截终止 → 报警只读（停止快照清理）
                    self._guard_alert("copilot 拦截", str(v.get("note", "")))
                elif v.get("note"):
                    self.chat.ai_note("副驾驶：" + str(v.get("note")))
            elif t == "compress":
                self.chat.ai_note(f"已压缩上下文（移除 {ev.get('removed')} 条旧消息）")
            elif t == "plan":
                plan = ev.get("plan") or {}
                nodes = plan.get("nodes") or []
                self.chat.ai_note(f"AOE 规划：{plan.get('goal','')}（{len(nodes)} 节点）")
                for n in nodes:
                    self.chat.ai_note(f"  {n.get('id')}: {n.get('name')} deps={n.get('deps',[])}")
            elif t == "plan_node":
                st = ev.get("status")
                mark = {"running": "→", "done": "✓", "timeout": "■"}.get(st, "·")
                self.chat.ai_note(f"  {mark} {ev.get('id')} {ev.get('name','')} ({ev.get('cost_s','')}s)")
            elif t == "plan_done":
                self.chat.ai_note("AOE 完成")
                self.chat.append_ai(str(ev.get("summary", "")))
            elif t == "plan_error":
                self.chat.ai_note("[!] " + str(ev.get("message", "")))
            elif t == "drift_merged":
                self.chat.ai_note(f"算力漂移：已合并服务器冷备 Agent 期间追加的 {ev.get('added', 0)} 条消息")
            elif t == "tool_registered":
                tool = ev.get("tool") or {}
                self.chat.ai_note(f"自研工具已入库：{tool.get('name', '')}（{tool.get('description', '')[:60]}）")
            elif t == "vault_stored":
                self.vault_panel.refresh()
                if self.dock_panel.isVisible() and self.panel_tabs.currentIndex() == 0:
                    self.summary_panel.refresh_artifacts()
                self.sync_q.mark_dirty("vault")
                self.sync_q.flush()
            elif t == "file_changed":
                self._reload_changed(ev.get("path", ""))
            elif t == "run_final":
                result = ev.get("result")
                self._busy = False
                self._update_statusbar()
                self.chat.send_btn.setEnabled(True)
                self.chat.stop_btn.setEnabled(False)
                self.progress.setVisible(False)
                self.chat.finish_ai()
                if ev.get("history") is not None:
                    self.history = ev["history"]
                    # v6.1 P1：Agent 线程基于提交时快照跑，可能早于漂移拉取合并；
                    # 覆盖存盘前把已合并的服务器消息再并回（去重键防翻倍）
                    if getattr(self, "_drift_added", None):
                        try:
                            self.history = sync_mod.merge_drift_history(
                                self.history, {"history": self._drift_added})
                        except Exception:
                            pass
                    self._save_history()
                if result:
                    tools = getattr(result, "tools_used", 0)
                    rounds = getattr(result, "rounds", 0)
                    usage = getattr(result, "usage", {}) or {}
                    self.chat.ai_note(
                        f"✓ 完成 · {tools} 次工具调用 · {rounds} 轮"
                        + (f" · [{usage.get('prompt_tokens','?')}→{usage.get('completion_tokens','?')} tokens]" if usage else "")
                    )
                self._check_sleep_goal(result)
                # v8.3：回复完成 → 提交任务级快照（后台打包+标题生成）
                self._finish_round()
            elif t == "run_error":
                self._busy = False
                self._update_statusbar()
                self.chat.send_btn.setEnabled(True)
                self.chat.stop_btn.setEnabled(False)
                self.progress.setVisible(False)
                self.chat.ai_note("× 运行出错: " + str(ev.get("message", "")))
                # v8.3：出错/取消时保留回退点供用户恢复（P0-3）
                self._finish_round(keep_rollback=True)
            elif t == "run_cancelled":
                self._busy = False
                self._update_statusbar()
                self.chat.send_btn.setEnabled(True)
                self.chat.stop_btn.setEnabled(False)
                self.progress.setVisible(False)
                self.chat.ai_note("■ 已停止")
                self._finish_round(keep_rollback=True)
            elif t == "guard_alert":
                # v8.3：报警 → 停止清理 + 只读 + pannel 横幅展示
                self.guard_banner.show_alert(ev.get("reason", ""))
                self.task_panel.set_status("只读（报警）")
                self.chat.ai_note("[!] AI 报警：" + str(ev.get("reason", "")) + "（已停止快照清理，进入只读模式）")
                self._update_statusbar()
            elif t == "round_done":
                # v8.3 P2-1：快照提交完成 → 状态复位 + 清专家运行时（UI 线程）
                try:
                    self._expert_runtime = {}
                    self._expert_eid_map = {}
                    if not snap_mod.is_readonly():
                        self.task_panel.set_status("空闲")
                        self._refresh_task_panel()
                except Exception:
                    pass
            # v8.15 检修：以下事件类型此前已入队但事件泵无分支，被静默丢弃
            # （快照提交提示不显示 / 专家文件冲突用户不可见 / UI 自动化被拦截无告警）
            elif t == "note":
                note = str(ev.get("note", "")).strip()
                if note:
                    self.chat.ai_note(note)
            elif t == "expert_conflict":
                self.chat.ai_note("[!] 专家文件冲突：" + str(ev.get("note", "")))
                try:
                    self.statusBar().showMessage(
                        f"专家 {ev.get('expert_id', '')} 文件冲突，已转只读", 5000)
                except Exception:
                    pass
            elif t == "ui_auto_block":
                self.chat.ai_note("[!] UI 自动化已拦截：" + str(ev.get("note", "")))
        except Exception as e:
            log_error("UI 事件处理异常", e)

    def _schedule_auto_decision(self, close_fn):
        """v8.3 P1-9：ai_decision_delay_s>0 时，用户超时不回答则由 AI 自己决策（默认放行）。

        v8.14：timer 与对话框成对返回（单槽位此前会被连续弹窗覆盖，
        第一个 timer 失去停止入口并对已关闭 dialog 调 done()）。
        """
        delay = int(getattr(self.cfg, "ai_decision_delay_s", 0))
        if delay <= 0:
            return None
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(close_fn)
        timer.start(delay * 1000)
        return timer

    @staticmethod
    def _cancel_auto_decision(timer):
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass  # C++ 对象可能已随父级销毁

    def _ask_approval(self, ev):
        payload = ev.get("payload") or {}
        call_id = ev.get("call_id")
        # v8.7 审查修复：非命令类工具（exe_*/浏览器自动化）payload 无 command 字段，
        # 原实现弹窗空白=盲批。通用摘要：有 command 展示命令；否则列出工具名+关键参数+说明。
        tool = str(ev.get("tool", "") or "")
        note = str(ev.get("note", "") or "")
        cmd = str(payload.get("command", "") or "")
        if cmd:
            summary = f"命令：\n{cmd}"
        else:
            parts = []
            for k in ("path", "url", "title", "text", "keys", "hwnd", "pid",
                      "x", "y", "args", "cwd"):
                v = payload.get(k)
                if v is None or v == "" or v == [] or v == {}:
                    continue
                parts.append(f"{k}: {str(v)[:200]}")
            body = "\n".join(parts) if parts else "(无参数)"
            summary = f"工具：{tool or '(未知)'}\n参数：\n{body}"
        if note:
            summary += f"\n\n说明：{note}"
        box = QMessageBox(self)
        box.setWindowTitle("需要确认操作")
        box.setText(f"AI 请求执行操作：\n\n{summary}\n\n是否允许？")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        timer = self._schedule_auto_decision(lambda: box.done(QMessageBox.StandardButton.Yes))
        ret = box.exec()
        self._cancel_auto_decision(timer)
        self.agent_thread.resolve_approval(call_id, ret == QMessageBox.StandardButton.Yes)

    def _show_diff_preview(self, ev):
        """v6.4 内联差异预览：弹 DiffPreviewDialog，用户接受才允许写入。"""
        call_id = ev.get("call_id")
        rel = ev.get("rel", "")
        old = ev.get("old", "")
        new = ev.get("new", "")
        try:
            dlg = DiffPreviewDialog(self, rel, old, new)
            timer = self._schedule_auto_decision(lambda: dlg.accept())
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
            self._cancel_auto_decision(timer)
        except Exception as e:
            log_error("差异预览对话框异常", e)
            self.chat.ai_note(f"差异预览异常（为安全起见已拒绝）: {e}")
            accepted = False
        self.agent_thread.resolve_approval(call_id, accepted)

    def _check_sleep_goal(self, result):
        if not (getattr(self.cfg, "ENABLE_MODES", True) and self.cfg.sleep_enabled
                and self.cfg.sleep_goal and self.cfg.sleep_authorized):  # v8.32（F7）：总开关门控
            return
        final_text = getattr(result, "text", "") or ""
        if modes_mod.goal_reached(self.cfg.sleep_goal, final_text):
            SleepDialog(self.cfg.sleep_goal, self.cfg.sleep_action, self,
                        on_before_power=lambda reason: self._drift_push(reason, wait_ms=10000)).exec()

    def set_history(self, history):
        self.history = list(history)
        self._save_history()
        self.chat.clear()
        # v8.8：set_history 也要同步轨迹面板
        if hasattr(self, "trace_panel") and self.trace_panel is not None:
            self.trace_panel.set_history(self.history)

    # ------------------------------------------------------------------
    # v6 算力漂移（第四章 4.2）：关机/休眠前推送 → 服务器冷备 Agent 续聊 → 开机拉取合并
    # ------------------------------------------------------------------
    def _drift_runtime(self, reason: str) -> dict:
        """快照附带的运行时状态：Agent 忙闲 + 在途后台任务，供冷备 Agent 续跑参考。"""
        try:
            from .background import get_bg_jobs
            jobs = [{"label": j.get("label"), "status": j.get("status"),
                     "started_at": j.get("started_at")}
                    for j in get_bg_jobs().snapshot() if j.get("status") == "running"]
        except Exception:
            jobs = []
        return {"reason": reason, "busy": bool(getattr(self, "_busy", False)), "bg_jobs": jobs}

    def _drift_push(self, reason: str, wait_ms: int = 0):
        """推送完整工作状态（含冷备副本）到用户自有服务器。wait_ms>0 时阻塞等待（关机/休眠前）。"""
        if not (getattr(self.cfg, "ENABLE_DRIFT", True)
                and getattr(self.cfg, "ENABLE_SYNC", True)
                and self.cfg.sync_server_url):
            return None
        try:
            payload = sync_mod.build_snapshot(
                self.history, self.vault, runtime=self._drift_runtime(reason))
        except Exception:
            return None
        t = DriftThread(self.cfg, "push", payload)
        _DRIFT_THREADS.add(t)
        t.finished.connect(lambda: _DRIFT_THREADS.discard(t))
        t.start()
        if wait_ms > 0:
            t.wait(wait_ms)
            if t.res.get("ok"):
                self.statusBar().showMessage("✓ 算力漂移：工作状态已推送到服务器", 5000)
        return t

    def _auto_drift_tick(self):
        """v8.9 自动上漂移：每 auto_drift_interval_min 分钟把完整状态推送到服务器。

        只在启用自动漂移、配置了同步服务器、且 Agent 空闲时触发（避免与在途 LLM 调用/快照打包争抢）。
        失败不弹窗不打断用户，只更新状态栏。
        """
        try:
            interval = int(getattr(self.cfg, "auto_drift_interval_min", 0) or 0)
        except (TypeError, ValueError):
            interval = 0
        if not (getattr(self.cfg, "ENABLE_AUTO_DRIFT", True)
                and getattr(self.cfg, "ENABLE_DRIFT", True)
                and getattr(self.cfg, "ENABLE_SYNC", True)
                and self.cfg.sync_server_url
                and interval > 0
                and not getattr(self, "_busy", False)):
            return
        now = time.monotonic()
        if now - getattr(self, "_last_auto_drift", 0.0) < interval * 60:
            return
        self._last_auto_drift = now
        t = self._drift_push("auto")
        if t is not None:
            # v8.15 检修：lambda 无接收者 QObject，QThread.finished 在工作线程 emit 时
            # 直连执行——statusBar 跨线程操作 QWidget。改绑定方法走事件队列。
            t.finished.connect(self._on_auto_drift_done)

    def _on_auto_drift_done(self):
        t = self.sender()
        if getattr(t, "res", {}).get("ok"):
            self.statusBar().showMessage("✓ 自动算力漂移：工作状态已推送到服务器", 5000)
        else:
            self.statusBar().showMessage("自动算力漂移失败（详见 Err.log）", 5000)

    def _drift_pull(self):
        """开机拉取：合并服务器冷备 Agent 期间追加的消息（from_server 标记，去重）。"""
        if not (getattr(self.cfg, "ENABLE_DRIFT", True)
                and getattr(self.cfg, "ENABLE_SYNC", True)
                and self.cfg.sync_server_url):
            return
        t = DriftThread(self.cfg, "pull")
        _DRIFT_THREADS.add(t)
        t.finished.connect(lambda: _DRIFT_THREADS.discard(t))
        # v8.15 检修：_drift_merge 改写 history 并落盘，此前经 lambda 在工作线程执行，
        # 与 UI 线程并发读写同一列表——绑定方法自动队列化到 GUI 线程
        t.finished.connect(self._on_pull_done)
        t.start()

    def _on_pull_done(self):
        self._drift_merge(self.sender())

    def _drift_merge(self, t) -> bool:
        """合并服务器冷备产出。返回 True=成功（含无新增）；False=拉取失败或本地合并异常。"""
        res = getattr(t, "res", {}) or {}
        if not res.get("ok"):
            return False
        try:
            before = len(self.history)
            merged = sync_mod.merge_drift_history(self.history, res.get("payload") or {})
            if len(merged) > before:
                added = merged[before:]
                self.history = merged
                # v6.1 P1：留存本批漂移消息；若后续 run_final 用旧快照覆盖历史，需重新并回
                self._drift_added = getattr(self, "_drift_added", []) + added
                self._save_history()
                self.ui_queue.put({"type": "drift_merged", "added": len(added)})
            return True
        except Exception as e:
            log_error("算力漂移合并异常", e)
            return False

    def _drift_control(self, mode: str, project_id: str = ""):
        """发起服务器漂移状态控制（begin/status/finish），返回 DriftThread。"""
        t = DriftThread(self.cfg, mode, project_id)
        _DRIFT_THREADS.add(t)
        t.finished.connect(lambda: _DRIFT_THREADS.discard(t))
        t.start()
        return t

    def _should_drift_exit(self) -> bool:
        """退出时是否「算力漂移退出」。v8.9：auto_drift_on_exit=True 时默认自动漂移，
        不再弹三选一问询；False 时恢复手动弹窗选择。"""
        can = (getattr(self.cfg, "ENABLE_DRIFT", True)
               and getattr(self.cfg, "ENABLE_SYNC", True)
               and bool(self.cfg.sync_server_url))
        if not can:
            return False
        if getattr(self.cfg, "auto_drift_on_exit", True):
            self._ensure_drift_upload_mode()
            self.statusBar().showMessage("自动算力漂移：退出前推送工作状态到服务器…", 3000)
            return True
        box = QMessageBox(self)
        box.setWindowTitle("算力漂移退出")
        box.setText("是否「算力漂移退出」？")
        box.setInformativeText(
            "漂移退出：把当前工作状态推送到服务器，之后可在云端/手机继续运算、编辑；\n"
            "普通退出：仅保存并退出，不进入云端继续。")
        drift_btn = box.addButton("算力漂移退出", QMessageBox.ButtonRole.AcceptRole)
        normal_btn = box.addButton("普通退出", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(normal_btn)
        box.exec()
        drift = box.clickedButton() is drift_btn
        if drift:
            self._ensure_drift_upload_mode()
        return drift

    def _ensure_drift_upload_mode(self):
        """v8.27：漂移上传模式只问一次（最简=WorkTree 即时变更增量 / 完整=所有工作区文件）。

        勾选「记住选择」即写入 config 永久生效，此后不再询问；不勾选仅本轮生效（下次再问）。
        """
        if getattr(self.cfg, "drift_upload_mode", "") in ("minimal", "full"):
            return
        try:
            from PyQt6.QtWidgets import QCheckBox
            box = QMessageBox(self)
            box.setWindowTitle("漂移上传模式")
            box.setText("选择算力漂移的上传模式（仅询问这一次）：")
            btn_min = box.addButton("最简（WorkTree 即时变更增量）",
                                    QMessageBox.ButtonRole.AcceptRole)
            btn_full = box.addButton("完整（所有工作区文件）",
                                     QMessageBox.ButtonRole.AcceptRole)
            box.setDefaultButton(btn_min)
            chk = QCheckBox("记住选择，以后不再询问（可改配置文件 drift_upload_mode）", box)
            chk.setChecked(True)
            box.setCheckBox(chk)
            box.exec()
            mode = "full" if box.clickedButton() is btn_full else "minimal"
            self.cfg.drift_upload_mode = mode
            if chk.isChecked():
                from dataclasses import asdict
                from .config import CONFIG_PATH
                from .storage import save_json
                save_json(CONFIG_PATH, asdict(self.cfg))
        except Exception:
            pass

    def _drift_check_startup(self):
        """开机检查：若上次是「算力漂移退出」，查询服务器是否仍有未回传产出。
        有则先锁定项目（只读），提示用户把算力漂移回本地；漂移回本地后解除只读。"""
        if not drift_mod.is_drifting():
            return
        if not (getattr(self.cfg, "ENABLE_DRIFT", True)
                and getattr(self.cfg, "ENABLE_SYNC", True)
                and self.cfg.sync_server_url):
            return
        pid = drift_mod.read_drift_state().get("project_id", "")
        t = DriftThread(self.cfg, "status", pid)
        _DRIFT_THREADS.add(t)
        t.finished.connect(lambda: _DRIFT_THREADS.discard(t))
        # v8.15 检修：回调含 statusBar/模态框，必须队列化到 GUI 线程
        t.finished.connect(self._on_startup_status)
        t.start()

    def _on_startup_status(self):
        self._on_drift_status(self.sender())

    def _on_drift_status(self, t):
        res = getattr(t, "res", {}) or {}
        if not res.get("ok"):
            return
        unacked = int(res.get("unacked_count", 0) or 0)
        if not unacked:
            # v8.7 审查修复：服务器已跑完且已复位时，同步清除本地漂移标记，
            # 避免 active 标记永久残留（每次开机空查询、误用旧状态锁项目）
            if not bool(res.get("active", False)) and drift_mod.is_drifting():
                drift_mod.end_drift()
                self.statusBar().showMessage("算力漂移：云端产出已全部回传，漂移状态已复位", 5000)
            return  # 服务器已跑完，无需锁定
        reason = f"上次算力漂移尚未完成：服务器有 {unacked} 条云端产出未回传，项目已锁定为只读。"
        snap_mod.set_readonly(True, reason)
        self._update_statusbar()
        self._prompt_drift_back(unacked)

    def _prompt_drift_back(self, unacked: int):
        box = QMessageBox(self)
        box.setWindowTitle("算力漂移回本地")
        box.setText(f"检测到服务器有 {unacked} 条云端产出未回传，项目已暂时锁定（只读）。")
        box.setInformativeText(
            "是否将服务器上的算力漂移回本地？\n"
            "「漂移回本地」会合并云端产出并解除只读；\n"
            "「保持锁定」则继续只读，稍后手动处理。")
        back_btn = box.addButton("漂移回本地", QMessageBox.ButtonRole.AcceptRole)
        lock_btn = box.addButton("保持锁定", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(back_btn)
        box.exec()
        if box.clickedButton() is back_btn:
            self._do_drift_back()

    def _do_drift_back(self):
        """把服务器上的算力漂移回本地：重新拉取合并（幂等）→ 解除只读 + 复位服务器状态。"""
        t = DriftThread(self.cfg, "pull")
        _DRIFT_THREADS.add(t)
        t.finished.connect(lambda: _DRIFT_THREADS.discard(t))
        # v8.15 检修：同上，队列化到 GUI 线程
        t.finished.connect(self._on_back_pull_done)
        t.start()

    def _on_back_pull_done(self):
        self._drift_back_after_pull(self.sender())

    def _drift_back_after_pull(self, t):
        # v8.7 审查修复：仅合并成功才复位漂移状态并解除只读；合并失败保持锁定，云端产出不丢
        if not self._drift_merge(t):
            self.statusBar().showMessage(
                "漂移回本地失败：无法合并云端产出，项目保持锁定（请检查服务器后重试）", 8000)
            self._update_statusbar()
            return
        drift_mod.end_drift()
        self._drift_control("finish", drift_mod.read_drift_state().get("project_id", ""))
        snap_mod.set_readonly(False)
        self._update_statusbar()
        try:
            from . import audit as _audit
            _audit.audit_log("drift_back", drift_mod.read_drift_state().get("project_id", ""),
                             "算力漂移回本地：云端产出已合并，服务器漂移状态已复位，项目解除只读", actor="user")
        except Exception:
            pass
        self.statusBar().showMessage("✓ 算力已漂移回本地，项目解除只读", 5000)

    def closeEvent(self, e):
        # v4：托盘驻留——普通关闭只隐藏窗口，真退出走托盘菜单/菜单「退出」
        if (self.tray is not None and self.tray.isVisible()
                and getattr(self.cfg, "minimize_to_tray", False)
                and not self._force_close):
            e.ignore()
            self.hide()
            return
        dirty = [t.path for t in self.editors.tabs if t.dirty]
        if dirty:
            ret = QMessageBox.question(
                self, "未保存的文件",
                f"{len(dirty)} 个文件未保存，关闭将丢失修改：\n" + "\n".join(dirty[:5]),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            )
            if ret == QMessageBox.StandardButton.Cancel:
                e.ignore()
                return
            if ret == QMessageBox.StandardButton.Save:
                for t in list(self.editors.tabs):
                    if t.dirty:
                        self.editors.setCurrentWidget(t.editor)
                        self.editors.save_current()
        self.timer.stop()
        self.reaper_timer.stop()
        if hasattr(self, "_auto_drift_timer"):
            self._auto_drift_timer.stop()
        # v8.9 退出前关闭受控浏览器并清理临时 profile（避免浏览器进程与临时目录残留）
        try:
            from . import browser_ctl as _bctl
            _bctl.close()
        except Exception:
            pass
        # v8.6 算力漂移：退出前询问是否「算力漂移退出」（有界等待，不卡关闭流程）
        drift_exit = self._should_drift_exit()
        try:
            if drift_exit:
                state = drift_mod.begin_drift()
                # 登记到服务器（复位云端未回传计数），随后推送含冷备副本的完整状态
                t_begin = self._drift_control("begin", state.get("project_id", ""))
                if t_begin is not None:
                    # v8.7 审查修复：begin 登记线程有界等待，避免解释器退出时 QThread 活销毁、
                    # 且登记请求未发出（服务器未复位计数）就关窗
                    t_begin.wait(2500)
            t = self._drift_push("quit")
            if t is not None:
                # v8.27 阶段3：漂移退出可能上传全工作区（客户端预算 144MB/服务器 192MB），
                # 等待上限对齐上传窗口 90s——2.5s 会杀掉上传线程使退出上传形同虚设
                t.wait(90_000)
        except Exception:
            pass
        w = self.editors._worker
        if w is not None and w.isRunning():
            # 转交保活集合，线程结束时自动清理，绝不阻塞 UI / 活销毁
            _ORPHAN_WORKERS.add(w)
            w.finished.connect(lambda: _ORPHAN_WORKERS.discard(w))
        # v8.3 建议线程/快照提交线程：有界等待回收（防 QThread 活销毁）
        for th in list(getattr(self, "_suggest_threads", set()) or set()):
            try:
                th.wait(2500)
            except Exception:
                pass
        # v8.5.x 审查修复：显式停止终端在途命令（TerminalPanel.closeEvent 是死代码）
        try:
            self.terminal.shutdown()
        except Exception:
            pass
        self.agent_thread.stop()
        if not self.agent_thread.wait(5000):
            # v8.5.x 审查修复：超时未退出时转交保活集合，避免解释器退出时活销毁
            _ORPHAN_WORKERS.add(self.agent_thread)
            self.agent_thread.finished.connect(lambda: _ORPHAN_WORKERS.discard(self.agent_thread))
        # v8.5.x 审查修复：快照提交线程（daemon threading.Thread）有界 join，防残缺快照
        ft = getattr(self, "_finish_thread", None)
        if ft is not None and ft.is_alive():
            try:
                ft.join(3.0)
            except Exception:
                pass
        # v6.5 远程指挥：停止后台线程（有界等待 3s，不卡关闭流程）
        if self._remote_cmd is not None:
            try:
                self._remote_cmd.stop(timeout=3.0)
            except Exception:
                pass
        # 统一收尾剩余临时 QThread：漂移/健康扫描等全部有界 wait（防活销毁）
        for th in list(_DRIFT_THREADS):
            try:
                th.wait(2500)
            except Exception:
                pass
        try:
            from . import panels as _panels_mod
            for th in list(getattr(_panels_mod, "_SCAN_THREADS", set()) or set()):
                try:
                    th.wait(2500)
                except Exception:
                    pass
        except Exception:
            pass
        e.accept()


# ---------------------------------------------------------------------------
def main():
    # v6.1 修复：高 DPI 缩放。不开启时 Windows 高分屏按物理 96dpi 渲染，
    # 全局字体/控件偏小、布局显窄（必须在 QApplication 创建前设置）
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # 全局基础字体（QSS 未覆盖的控件也随此尺寸，10pt ≈ 13px@96dpi）
    app.setFont(QFont("Microsoft YaHei UI", 10))
    win = DeverAIApp()
    win.show()
    # v4：托盘驻留且「关闭最小化到托盘」时，关窗不退出应用
    if (getattr(win.cfg, "ENABLE_TRAY", True) and win.tray is not None
            and getattr(win.cfg, "minimize_to_tray", False)):
        app.setQuitOnLastWindowClosed(False)
    sys.exit(app.exec())
