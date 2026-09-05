"""桌面面板：文件树 / 资产银行 / 终端。PyQt6。
文件树懒加载（展开时读取子目录），双击打开文件（回调交给主窗口）。
"""
import html
import os
import re
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QTimer, QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QListWidget, QListWidgetItem, QPlainTextEdit, QLineEdit, QPushButton,
    QMenu, QMessageBox, QInputDialog, QLabel, QSplitter, QTextBrowser,
)

from . import vault as vault_mod
from .icons import icon as svg_icon

SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".idea", ".vscode", "dist", "build"}
# v7：文件类型→SVG图标名映射（替代旧 [py] 文本标记）
_FILE_ICON_MAP = {
    "py": "atom", "js": "pen-line", "ts": "pen-line", "jsx": "pen-line", "tsx": "pen-line",
    "html": "pen-line", "css": "pen-line", "json": "memo", "md": "memo", "txt": "memo",
    "yaml": "cog", "yml": "cog", "toml": "cog", "ini": "cog",
    "sh": "terminal", "bat": "terminal", "ps1": "terminal", "sql": "memo", "csv": "chart-bar-alt-square",
    "lock": "lock-closed", "pyc": "cog",
}


def _ext(p):
    n = Path(p).name
    i = n.rfind(".")
    return n[i + 1:].lower() if i > 0 else ""


def _file_icon_name(name):
    """返回文件类型对应的 SVG 图标名。"""
    return _FILE_ICON_MAP.get(_ext(name), "memo")


# ---------------------------------------------------------------------------
# 文件树
# ---------------------------------------------------------------------------
class FileTree(QTreeWidget):
    file_activated = pyqtSignal(str)  # 相对路径

    def __init__(self, parent=None):
        super().__init__(parent)
        self.workspace = ""
        self.setHeaderHidden(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx_menu)
        self.itemDoubleClicked.connect(self._on_double_click)
        self.itemExpanded.connect(self._on_expand)

    def set_workspace(self, path: str):
        self.workspace = str(path)
        self.clear()
        if not path:
            # v4：加载提示——未选择工作区时给出引导
            self.addTopLevelItem(QTreeWidgetItem(
                ["尚未选择工作区（菜单「工作区 → 选择工作区目录…」）"]))
            return
        root_item = QTreeWidgetItem(["[目录] " + (Path(path).name or path)])
        root_item.setData(0, Qt.ItemDataRole.UserRole, "")
        root_item.setData(0, Qt.ItemDataRole.UserRole + 1, True)  # is_dir
        root_item.setIcon(0, svg_icon("folder-arrow-down", 14))
        self.addTopLevelItem(root_item)
        loading = QTreeWidgetItem(["加载中…"])
        root_item.addChild(loading)
        self._load_children(root_item, "")
        root_item.setExpanded(True)

    def _abs(self, rel: str) -> Path:
        return Path(self.workspace) / rel

    def _load_children(self, item: QTreeWidgetItem, rel: str):
        item.takeChildren()
        try:
            entries = sorted(self._abs(rel).iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            item.addChild(QTreeWidgetItem(["[!] 无法读取目录"]))
            return
        MAX_PER_DIR = 1500  # 单层条目上限，防超大目录卡死 UI
        if len(entries) > MAX_PER_DIR:
            item.addChild(QTreeWidgetItem(["…(条目过多，仅显示前 {} 项)".format(MAX_PER_DIR)]))
        shown = 0
        for p in entries[:MAX_PER_DIR]:
            if p.is_dir() and p.name in SKIP_DIRS:
                continue
            rel_child = f"{rel}/{p.name}" if rel else p.name
            label = ("[目录] " if p.is_dir() else "") + p.name
            node = QTreeWidgetItem([label])
            node.setData(0, Qt.ItemDataRole.UserRole, rel_child)
            node.setData(0, Qt.ItemDataRole.UserRole + 1, p.is_dir())
            # v7：SVG图标替代文本标记
            if p.is_dir():
                node.setIcon(0, svg_icon("folder-arrow-down", 14))
            else:
                node.setIcon(0, svg_icon(_file_icon_name(p.name), 14))
            if p.is_dir():
                node.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            item.addChild(node)
            shown += 1
        if shown == 0:
            item.addChild(QTreeWidgetItem(["（空目录）"]))

    def _on_expand(self, item: QTreeWidgetItem):
        # 展开时无条件重载子目录（懒加载深层目录）
        if item.data(0, Qt.ItemDataRole.UserRole + 1):
            rel = item.data(0, Qt.ItemDataRole.UserRole) or ""
            self._load_children(item, rel)

    def _on_double_click(self, item: QTreeWidgetItem, _col):
        rel = item.data(0, Qt.ItemDataRole.UserRole)
        is_dir = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if rel is not None and not is_dir:
            self.file_activated.emit(rel)

    # v6.3 @-mention / 命令面板用：列出工作区所有文件相对路径（带上限，缓存 30s）
    _all_files_cache: list = []
    _all_files_ts: float = 0.0
    _all_files_ws: str = ""  # v8.5.x 审查修复：缓存对应的工作区（切工作区需失效）

    def list_all_files(self, limit: int = 2000) -> list:
        """返回工作区内所有文件的相对路径列表（按字母序，上限 limit）。"""
        import time as _time
        if not self.workspace:
            return []
        # 30s 缓存（@-mention 频繁触发）；v8.5.x 审查修复：工作区变化时缓存失效
        if (self._all_files_cache and self._all_files_ws == self.workspace
                and (_time.time() - self._all_files_ts) < 30):
            return self._all_files_cache
        out = []
        try:
            base = Path(self.workspace)
            for p in base.rglob("*"):
                if not p.is_file():
                    continue
                # 跳过忽略目录
                if any(part in SKIP_DIRS for part in p.relative_to(base).parts):
                    continue
                out.append(str(p.relative_to(base)).replace("\\", "/"))
                if len(out) >= limit:
                    break
        except Exception:
            pass
        out.sort(key=str.lower)
        type(self)._all_files_cache = out
        type(self)._all_files_ts = _time.time()
        type(self)._all_files_ws = self.workspace
        return out

    def _ctx_menu(self, pos):
        item = self.itemAt(pos)
        rel = item.data(0, Qt.ItemDataRole.UserRole) if item else ""
        is_dir = item.data(0, Qt.ItemDataRole.UserRole + 1) if item else False
        menu = QMenu(self)
        # v8.15 检修：占位行（尚未选择工作区/加载中/空目录标记）无 UserRole 数据
        # （rel=None），此前"新建/重命名/删除"照常入菜单，点击即 Path(None) TypeError——
        # PyQt6 槽内未捕获异常默认 qFatal 直接整个应用 abort。占位行只留刷新。
        if item is not None and rel is None:
            menu.addAction("刷新", lambda: self.set_workspace(self.workspace))
            menu.exec(self.viewport().mapToGlobal(pos))
            return
        if item and not is_dir:
            menu.addAction("打开", lambda: self.file_activated.emit(rel))
        if rel is not None:
            menu.addAction("新建文件", lambda: self._new_file(rel, is_dir))
            menu.addAction("新建文件夹", lambda: self._new_folder(rel, is_dir))
            menu.addAction("重命名", lambda: self._rename(rel))
        if item and rel is not None:
            # v8.14：恢复手动删除入口（此前恒 disabled 成死功能）；确认框默认 No 防误删
            menu.addAction("删除", lambda r=rel: self._delete(r))
        menu.addAction("刷新", lambda: self.set_workspace(self.workspace))
        menu.exec(self.viewport().mapToGlobal(pos))

    def _new_file(self, rel, is_dir):
        base = rel if is_dir else str(Path(rel).parent)
        name, ok = QInputDialog.getText(self, "新建文件", "文件名（相对路径）:", text=(base + "/" if base else ""))
        if not ok or not name.strip():
            return
        p = self._abs(name.strip().replace("\\", "/"))
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        except OSError as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        self.file_activated.emit(str(p.relative_to(self.workspace)).replace("\\", "/"))
        self.set_workspace(self.workspace)

    def _new_folder(self, rel, is_dir):
        base = rel if is_dir else str(Path(rel).parent)
        name, ok = QInputDialog.getText(self, "新建文件夹", "名称:", text=(base + "/" if base else ""))
        if not ok or not name.strip():
            return
        try:
            self._abs(name.strip().replace("\\", "/")).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        self.set_workspace(self.workspace)

    def _rename(self, rel):
        name, ok = QInputDialog.getText(self, "重命名", "新名称:", text=Path(rel).name)
        if not ok or not name.strip():
            return
        old = self._abs(rel)
        new = self._abs(str(Path(rel).parent / name.strip()))
        try:
            old.rename(new)
        except OSError as e:
            QMessageBox.warning(self, "重命名失败", str(e))
            return
        self.set_workspace(self.workspace)

    def _delete(self, rel):
        ret = QMessageBox.question(
            self, "删除", f"确定删除 {rel}？\n（AI 删除未启用时，可在此手动删除）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            try:
                p = self._abs(rel)
                if p.is_dir():
                    import shutil
                    shutil.rmtree(p)
                else:
                    p.unlink()
            except OSError as e:
                QMessageBox.warning(self, "删除失败", str(e))
            self.set_workspace(self.workspace)


# ---------------------------------------------------------------------------
# 资产银行
# ---------------------------------------------------------------------------
class VaultPanel(QWidget):
    def __init__(self, vault: vault_mod.Vault, parent=None):
        super().__init__(parent)
        self.vault = vault
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("检索资产…")
        self.search.returnPressed.connect(self._search)
        btn = QPushButton("搜索")
        btn.clicked.connect(self._search)
        row.addWidget(self.search)
        row.addWidget(btn)
        layout.addLayout(row)
        self.listw = QListWidget()
        self.listw.itemDoubleClicked.connect(self._show_detail)
        layout.addWidget(self.listw)
        self.refresh()

    def refresh(self):
        self.listw.clear()
        for a in self.vault.list()[:200]:
            title = a.get("title", "未命名")
            tags = ",".join(a.get("tags", []))
            item = QListWidgetItem(f"  {title}\n   {a.get('kind','code')} {tags}")
            item.setIcon(svg_icon("atom", 14))
            item.setData(Qt.ItemDataRole.UserRole, a)
            self.listw.addItem(item)

    def _search(self):
        q = self.search.text().strip()
        if not q:
            self.refresh()
            return
        # v8.14：embedding_level=api 时 vault.search 走同步网络 IO（分批、每批可达 8s），
        # 此前在 GUI 线程直接调用会冻结整个界面数十秒——移入 QThread
        if getattr(self, "_search_thread", None) and self._search_thread.isRunning():
            return  # 上一次搜索仍在进行，忽略重复回车
        self._search_thread = _VaultSearchThread(self.vault, q, self)
        self._search_thread.ready.connect(self._fill_results)
        # v8.15 检修：lambda 无 QObject 接收者 → 工作线程内直连执行，
        # 会在非 GUI 线程创建 QMessageBox（Qt 硬性违例，可崩溃）——改绑定方法走队列
        self._search_thread.fail.connect(self._on_search_fail)
        self._search_thread.finished.connect(self._on_search_done)
        self._search_thread.finished.connect(self._search_thread.deleteLater)
        self.search.setPlaceholderText("搜索中…")
        self._search_thread.start()

    def _on_search_fail(self, msg: str):
        QMessageBox.warning(self, "搜索失败", msg)

    def _on_search_done(self):
        self.search.setPlaceholderText("检索资产…")

    def _fill_results(self, hits):
        self.listw.clear()
        for h in hits or []:
            a = h if isinstance(h, dict) and "score" not in h else h.get("asset", h)
            label = f"  {a.get('title','')}"
            if isinstance(h, dict) and "score" in h:
                label += f" (相似 {h.get('score',0):.2f})"
            item = QListWidgetItem(label)
            item.setIcon(svg_icon("atom", 14))
            item.setData(Qt.ItemDataRole.UserRole, a)
            self.listw.addItem(item)

    def _show_detail(self, item):
        a = item.data(Qt.ItemDataRole.UserRole)
        QMessageBox.information(
            self, a.get("title", "资产"),
            f"类型: {a.get('kind')}\n场景: {a.get('scene','')}\n标签: {','.join(a.get('tags',[]))}\n\n{a.get('description','')}\n\n---内容---\n{a.get('content','')[:2000]}",
        )


class _VaultSearchThread(QThread):
    """v8.14：资产检索后台线程（api 级 embedding 为同步网络 IO，不得阻塞 GUI）。"""
    ready = pyqtSignal(object)
    fail = pyqtSignal(str)

    def __init__(self, vault, query: str, parent=None):
        super().__init__(parent)
        self._vault = vault
        self._query = query

    def run(self):
        try:
            hits = self._vault.search(self._query, limit=20, threshold=0.0)
            self.ready.emit(hits)
        except Exception as e:
            self.fail.emit(str(e))


# ---------------------------------------------------------------------------
# 终端
# ---------------------------------------------------------------------------
class _CmdThread(QThread):
    line_ready = pyqtSignal(str)
    done = pyqtSignal(int)

    def __init__(self, command: str, cwd: str, parent=None):
        super().__init__(parent)
        self.command = command
        self.cwd = cwd

    def run(self):
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.proc = subprocess.Popen(
                self.command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, cwd=self.cwd, creationflags=creationflags,
            )
        except OSError as e:
            self.proc = None
            self.line_ready.emit(f"启动失败: {e}")
            self.done.emit(-1)
            return
        proc = self.proc
        for raw in proc.stdout:
            self.line_ready.emit(raw.decode("utf-8", errors="replace").rstrip("\n"))
        proc.wait()
        self.done.emit(proc.returncode)

    def kill_tree(self):
        """终结整棵子进程树（Windows），让 stdout 管道关闭、线程自然退出。"""
        proc = getattr(self, "proc", None)
        if proc is None or proc.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass
        else:
            # v8.5.x 审查修复：非 Windows 用 terminate 兜底（taskkill 不存在，否则 wait 必超时）
            try:
                proc.terminate()
            except Exception:
                pass


class TerminalPanel(QWidget):
    MAX_CONCURRENT = 3
    quote_requested = pyqtSignal(str)  # v4：选段终端输出引用到对话

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cwd = os.getcwd()
        self._threads = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumBlockCount(5000)
        self.out.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.out.customContextMenuRequested.connect(self._out_ctx)
        layout.addWidget(self.out, 1)
        row = QHBoxLayout()
        self.prompt = QLabel(">")
        self.inp = QLineEdit()
        self.inp.returnPressed.connect(self._run)
        row.addWidget(self.prompt)
        row.addWidget(self.inp)
        layout.addLayout(row)

    def set_cwd(self, path: str):
        self._cwd = str(path)
        self.out.appendPlainText(f"$ cd {self._cwd}")

    def _run(self):
        cmd = self.inp.text().strip()
        if not cmd:
            return
        self.inp.clear()
        self.out.appendPlainText(f"> {cmd}")
        if cmd.lower() in ("cls", "clear"):
            self.out.clear()
            return
        self._threads = [t for t in self._threads if t.isRunning()]
        if len(self._threads) >= self.MAX_CONCURRENT:
            self.out.appendPlainText(f"[已达最大并发 {self.MAX_CONCURRENT} 个终端命令，请等待完成]")
            return
        t = _CmdThread(cmd, self._cwd, self)
        t.line_ready.connect(self.out.appendPlainText)
        # v8.15 检修：done 在工作线程 emit，lambda 直连会在非 GUI 线程操作 QPlainTextEdit
        t.done.connect(self._on_cmd_done)
        t.finished.connect(t.deleteLater)
        self._threads.append(t)
        t.start()

    def _on_cmd_done(self, rc: int):
        self.out.appendPlainText(f"[退出码 {rc}]")

    def append(self, text: str):
        self.out.appendPlainText(text)

    def _out_ctx(self, pos):
        """v4：终端右键——选段发送给 Agent。"""
        menu = QMenu(self)
        sel = self.out.textCursor().selectedText()
        if sel:
            menu.addAction("引用选段到对话",
                           lambda: self.quote_requested.emit(sel.replace("\u2029", "\n")))
        else:
            menu.addAction("（先选中输出内容再引用）").setEnabled(False)
        menu.addSeparator()
        menu.addAction("复制", self.out.copy)
        menu.addAction("全选", self.out.selectAll)
        menu.exec(self.out.viewport().mapToGlobal(pos))

    def shutdown(self):
        """应用退出时停止所有在途终端命令。

        v8.5.x 审查修复：closeEvent 对嵌入 QTabWidget 的子控件永不触发（死代码），
        改为显式 shutdown()，由主窗口 closeEvent 调用，避免在途 _CmdThread 被活销毁。
        """
        for t in self._threads:
            try:
                t.kill_tree()
                t.wait(3000)
            except Exception:
                pass

    def closeEvent(self, e):
        self.shutdown()
        e.accept()


# ---------------------------------------------------------------------------
# v6.4 项目健康仪表盘（Health Dashboard）
# ---------------------------------------------------------------------------
class HealthDashboard(QWidget):
    """项目健康仪表盘：实时展示文件数、TODO、近1小时报错率、err_mirror 热点词云。

    每 60s 自动刷新（也可手动刷新）。所有指标在后台线程计算避免阻塞 UI。
    v8.3：健康度跌破 P0 阈值时发出 p0_alert 信号，由主窗口进入只读报警态。
    """

    p0_alert = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workspace = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        # 顶部刷新按钮
        top = QHBoxLayout()
        self._title = QLabel("项目健康仪表盘")
        self._title.setStyleSheet("font-weight: bold; font-size: 13px;")
        top.addWidget(self._title)
        top.addStretch()
        self._btn = QPushButton("刷新")
        self._btn.clicked.connect(self.refresh)
        top.addWidget(self._btn)
        layout.addLayout(top)
        # 主内容区
        self._view = QTextBrowser()
        self._view.setOpenExternalLinks(False)
        self._view.setHtml("<i>点击「刷新」或选择工作区后查看项目健康度。</i>")
        layout.addWidget(self._view, 1)
        # 定时刷新（60s）
        self._timer = QTimer(self)
        self._timer.setInterval(60000)
        self._timer.timeout.connect(self.refresh)

    def set_workspace(self, path: str):
        self._workspace = str(path or "")
        # v8.15 检修：切换工作区立即作废在途扫描（其结果属于旧目录，不得渲染到新视图）
        self._gen = getattr(self, "_gen", 0) + 1
        if self._workspace:
            self._timer.start()
            self.refresh()
        else:
            self._timer.stop()
            self._view.setHtml("<i>未选择工作区。</i>")

    def refresh(self):
        """触发后台计算（避免大目录扫描阻塞 UI）。

        v8.15 检修：①在途扫描时直接跳过（此前 60s 定时器可叠加并发多个扫描线程，
        大目录/网络盘上无限增殖）；②代次校验——旧扫描后完成不得覆盖新数据。
        """
        if not self._workspace:
            self._view.setHtml("<i>未选择工作区。</i>")
            return
        if getattr(self, "_scan_thread", None) and self._scan_thread.isRunning():
            return
        self._btn.setEnabled(False)
        self._btn.setText("统计中…")
        self._gen = getattr(self, "_gen", 0) + 1
        # 无 parent + 模块级保活集合：防主窗口关闭时子控件销毁连带 QThread 活销毁
        t = _HealthScanThread(self._workspace)
        t.expected_gen = self._gen          # v8.15：渲染时代次比对用（经 sender() 读取）
        t.scan_ws = self._workspace         # v8.15：记录扫描目标，供切工作区后补扫判断
        self._scan_thread = t
        _SCAN_THREADS.add(t)
        t.finished_scan.connect(self._render)   # 绑定方法 → AutoConnection 队列化到 GUI 线程
        t.finished.connect(lambda th=t: _SCAN_THREADS.discard(th))
        t.finished.connect(t.deleteLater)
        t.start()

    def _render(self, data: dict):
        st = self.sender()
        # v8.15 检修：扫描目标与当前工作区不符（切换发生在在途期间）→ 丢弃并立即补扫当前目录。
        # 注意必须先于代次判断——set_workspace 已抬升代次，若先因代次早退，B 工作区的
        # 补扫将永远不触发（只能等 60s 定时器）。
        if self._workspace and getattr(st, "scan_ws", "") != self._workspace:
            self.refresh()
            return
        if st is not None and getattr(st, "expected_gen", None) != getattr(self, "_gen", None):
            return  # 旧扫描晚到，不得覆盖新数据
        self._btn.setEnabled(True)
        self._btn.setText("刷新")
        # v8.3 P0 报警：健康度极低或报错率爆表 → 通知主窗口只读
        health = 100
        err_1h = data.get("err_1h", 0)
        todos = data.get("todos", 0)
        if err_1h > 0:
            health -= min(40, err_1h * 5)
        if todos > 50:
            health -= min(20, (todos - 50) // 5)
        health = max(0, min(100, health))
        if health < 40 or err_1h >= 10:
            self.p0_alert.emit(
                f"健康度 {health}/100，近1小时报错 {err_1h} 次，"
                f"TODO 负债 {todos} 处"
            )
        html = self._build_html(data)
        self._view.setHtml(html)

    def _build_html(self, d: dict) -> str:
        """v7：从当前主题取色，无硬编码颜色。"""
        from . import themes as themes_mod
        from .config import get_config
        try:
            _pal = themes_mod.get_palette(getattr(get_config(), "theme", "obsidian"))
        except Exception:
            _pal = themes_mod.get_palette("obsidian")
        _bg = _pal.get("bg", "#0e0e12")
        _panel = _pal.get("panel", "#16161c")
        _text = _pal.get("text", "#e2e8f0")
        _muted = _pal.get("muted", "#94a3b8")
        _border = _pal.get("border", "#252530")
        _ok = _pal.get("ok", "#22c55e")
        _warn = _pal.get("warn", "#f59e0b")
        _err = _pal.get("err", "#ef4444")
        _accent = _pal.get("accent", "#3b82f6")

        files = d.get("files", 0)
        lines = d.get("lines", 0)
        todos = d.get("todos", 0)
        todo_list = d.get("todo_list", [])
        err_1h = d.get("err_1h", 0)
        err_total = d.get("err_total", 0)
        hotspots = d.get("hotspots", [])
        health = 100
        if err_1h > 0:
            health -= min(40, err_1h * 5)
        if todos > 50:
            health -= min(20, (todos - 50) // 5)
        health = max(0, min(100, health))
        health_color = _ok if health >= 80 else (_warn if health >= 60 else _err)
        parts = [
            f"<html><body style='font-family: sans-serif; font-size: 12px; color: {_text};'>",
            f"<div style='background:{_panel};padding:8px;border-radius:6px;margin-bottom:8px;'>",
            f"<b>健康度</b>: <span style='color:{health_color};font-size:18px;font-weight:bold;'>{health}</span>/100"
            f" &nbsp; <span style='color:{_muted};'>(报错率+TODO 负债综合)</span></div>",
            f"<b>项目规模</b>: {files} 个文件，约 {lines} 行代码<br/>",
            f"<b>未完成 TODO</b>: <span style='color:{_err if todos > 20 else _text};font-weight:bold;'>{todos}</span> 处<br/>",
            f"<b>近1小时报错</b>: {err_1h} 次（总计 {err_total} 次）<br/>",
        ]
        if todo_list:
            parts.append(f"<hr style='border-color:{_border};'/><b>TODO 清单（前 10 条）:</b><ul>")
            for item in todo_list[:10]:
                parts.append(f"<li><small style='color:{_muted};'>{html.escape(str(item))}</small></li>")
            parts.append("</ul>")
        if hotspots:
            parts.append(f"<hr style='border-color:{_border};'/><b>报错热点词:</b><div style='line-height:1.8;'>")
            for word, cnt in hotspots[:15]:
                size = 11 + min(10, int(cnt or 0))
                parts.append(f"<span style='font-size:{size}px;color:{_err};margin-right:8px;'>"
                             f"{html.escape(str(word))}({int(cnt or 0)})</span>")
            parts.append("</div>")
        parts.append("</body></html>")
        return "".join(parts)


# 健康扫描线程保活集合（无 parent，防关窗时活销毁）
_SCAN_THREADS: set = set()


class _HealthScanThread(QThread):
    """后台扫描线程：统计文件/行数/TODO/报错热点，避免阻塞 UI。"""
    finished_scan = pyqtSignal(dict)

    def __init__(self, workspace: str, parent=None):
        super().__init__(parent)
        self._workspace = workspace

    def run(self):
        try:
            data = self._scan()
        except Exception:
            data = {"files": 0, "lines": 0, "todos": 0, "todo_list": [],
                    "err_1h": 0, "err_total": 0, "hotspots": []}
        self.finished_scan.emit(data)

    def _scan(self) -> dict:
        import datetime
        base = Path(self._workspace)
        files = 0
        lines = 0
        todos = 0
        todo_list = []
        todo_re = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)
        code_exts = {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json",
                     ".md", ".txt", ".yaml", ".yml", ".toml", ".ini", ".sh", ".bat", ".sql"}
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(base).parts):
                continue
            files += 1
            if p.suffix.lower() not in code_exts:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            lines += text.count("\n") + 1
            # TODO 扫描
            for m in todo_re.finditer(text):
                todos += 1
                if len(todo_list) < 20:
                    line_no = text[:m.start()].count("\n") + 1
                    rel = str(p.relative_to(base)).replace("\\", "/")
                    line_content = text.splitlines()[line_no - 1][:80] if line_no <= len(text.splitlines()) else ""
                    todo_list.append(f"{rel}:{line_no} {line_content.strip()}")
        # err_mirror 热点
        err_1h = 0
        err_total = 0
        hotspots = []
        try:
            from . import err_mirror
            errs = err_mirror.list_all()
            now = datetime.datetime.now()
            words = []
            for e in errs:
                ts_str = e.get("ts", "")
                try:
                    et = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    if (now - et).total_seconds() < 3600:
                        err_1h += 1
                except Exception:
                    pass
                err_total += 1
                # 提取错误特征词
                msg = str(e.get("message", "")) + " " + str(e.get("kind", ""))
                words.extend([w for w in re.findall(r"[A-Za-z_]{3,}", msg) if len(w) > 3])
            hotspots = Counter(words).most_common(15)
        except Exception:
            pass
        return {
            "files": files, "lines": lines,
            "todos": todos, "todo_list": todo_list,
            "err_1h": err_1h, "err_total": err_total,
            "hotspots": hotspots,
        }
