"""设置对话框：模型 / 工作区 / 模块开关 / 阈值 / 三大模式 / 同步 / v4 外观。
"""
import asyncio
import json
import threading
from dataclasses import asdict
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPushButton,
    QCheckBox, QSpinBox, QDoubleSpinBox, QFileDialog, QLabel, QTabWidget,
    QWidget, QMessageBox, QGroupBox, QScrollArea, QComboBox, QColorDialog,
    QListWidget, QListWidgetItem, QPlainTextEdit,
)

from . import sync as sync_mod
from . import themes as themes_mod
from .config import Config, CONFIG_PATH
from .icons import icon as svg_icon
from .llm import test_connection
from .models import (ModelInfo, load_models, get_model, upsert_model,
                     delete_model, ensure_seed, model_from_dict, DISPLAY_LEVELS,
                     list_embedding_models, list_vision_models)

PROVIDERS = [
    ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
    ("Kimi", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    ("智谱GLM", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    ("Ollama本地", "http://127.0.0.1:11434/v1", "qwen2.5:7b"),
]

SWITCHES = [
    ("ENABLE_VAULT", "资产银行"),
    ("ENABLE_AOE", "AOE 并行规划"),
    ("ENABLE_SUBAGENT", "子Agent"),
    ("ENABLE_SYNC", "状态同步"),
    ("ENABLE_MODES", "三大模式"),
    ("ENABLE_ERR_MIRROR", "防呆数据库"),
    ("ENABLE_APPROVAL", "命令审批门"),
    ("ALLOW_AI_DELETE", "允许 AI 删除文件（危险）"),
    ("ENABLE_CTX_EXPERT", "上下文守门专家（自动省略古早上下文）"),
    ("ENABLE_STREAM_COMPLETE", "逐行流式自动补全"),
    ("ENABLE_EXPERTS", "专家团（总司令规划 + 专家并行）"),
    ("ENABLE_LOCKS", "租约锁 + 重要文档保护"),
    ("ENABLE_WEB_SEARCH", "联网搜索"),
    ("ENABLE_TOOLSMITH", "自研工具库 / 工具设计专家（查重→构建→审核→入库）"),
    ("ENABLE_DRIFT", "算力漂移（退出前推送 + 开机回传合并）"),
    ("ENABLE_REMOTE_CMD", "远程指挥协调（后台接收 sync_server 远程命令）"),
    ("ENABLE_BROWSER", "浏览器控制（超轻量：系统 Edge/Chrome，外交型任务自动合规拦截）"),
    ("ENABLE_UI_AUTOMATION", "外部程序/浏览器自动化（AI 操作 exe+浏览器点击/输入，copilot 全程监督）"),
    ("ENABLE_AUTO_DRIFT", "自动算力漂移（工作期间定时推送 + 退出自动漂移）"),
    ("auto_drift_on_exit", "退出默认自动漂移（关闭则每次退出时询问）"),
    ("ENABLE_AUDIT_LOG", "回退审计日志（回退/删除/恢复/漂移回本地全记录）"),
    ("ENABLE_FILE_PARTITION", "文件分区并发（同层任务按文件冲突分批并行）"),
    ("ENABLE_BROWSER_CTL", "直接操控浏览器（CDP：启动/跳转/点击/输入/按键/关闭）"),
    ("ENABLE_BROWSER_DEVTOOLS", "F12 开发者工具（Networks/Storage/Console/Sources 面板，查找 API 端点）"),
    ("ENABLE_EXE_JOURNAL", "外部软件探索记录（每次 exe 操作写入 journal）"),
    ("ENABLE_TOOL_DOCTOR", "工具医生（bug 收集与自动修复回归）"),
]

# v8.5.6：参考图内置专家与技能（卡片式展示）
BUILTIN_EXPERTS = [
    ("Researcher", "研究分析、代码定位、依赖映射、环境检查、报告生成。"),
    ("Full-Stack Engineer", "前后端代码实现与修改、跨栈与通用编码任务。"),
    ("QA", "运行测试与构建，收集验证证据。"),
    ("Code Reviewer", "审查代码、识别风险、给出改进建议。"),
    ("UI Operator", "浏览器与 UI 端到端验证、视觉缺陷复现。"),
    ("Debug Engineer", "复现故障、定位根因、诊断缺陷并给出修复建议。"),
]

BUILTIN_SKILLS = [
    ("better-harness", "审查外层编码 Agent 的生命周期控制、重复工作、项目反馈与代理资产。"),
    ("create-plugin", "从外部或本地源创建插件目录，转换 GitHub SKILL.md 与本地 SKILL.md。"),
]


# 连接测试线程保活集合（无 parent，防关窗时 QThread 活销毁）
_TEST_THREADS: set = set()


class _TestThread(QThread):
    finished_ok = pyqtSignal(str)
    finished_err = pyqtSignal(str)

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self):
        try:
            msg = asyncio.run(asyncio.wait_for(test_connection(self.cfg), timeout=20))
            self.finished_ok.emit(msg or "连接成功")
        except Exception as e:
            self.finished_err.emit(str(e))


class _ScoreThread(QThread):
    """自动打分后台线程：跑 auto_score_all(force=True)，结果经信号回 UI。"""
    finished_ok = pyqtSignal(str)
    finished_err = pyqtSignal(str)

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self):
        try:
            from .auto_score import auto_score_all
            res = asyncio.run(auto_score_all(self.cfg, force=True))
            self.finished_ok.emit(
                f"打分完成：处理 {res.get('processed', 0)}/{res.get('total', 0)} 个模型。")
        except Exception as e:
            self.finished_err.emit(str(e))


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._cfg_backup = dict(cfg.__dict__)  # 取消时回滚（含导入设置的暂存修改）
        self._accepted = False
        self.setWindowTitle("设置")
        self.setObjectName("settingsDialog")
        self.resize(980, 700)
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        title = QLabel("Settings")
        title.setObjectName("settingsTitle")
        layout.addWidget(title)
        subtitle = QLabel("Configure models, agents, workspace, skills and appearance")
        subtitle.setObjectName("settingsSubtitle")
        layout.addWidget(subtitle)
        tabs = QTabWidget()
        tabs.setObjectName("settingsTabs")
        tabs.setTabPosition(QTabWidget.West)
        tabs.setDocumentMode(True)
        self.tabs = tabs

        # v8.5.6：对齐参考图左侧导航；未实现模块按模块开关原则隐藏入口（v8.13）
        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_models_tab(), "Models")
        tabs.addTab(self._build_agents_tab(), "Agents")
        tabs.addTab(self._build_skills_tab(), "Skills & Commands")
        tabs.addTab(self._build_workspace_tab(), "Worktree")
        tabs.addTab(self._build_indexing_tab(), "Indexing")
        tabs.addTab(self._build_integrations_tab(), "Integrations")
        tabs.addTab(self._build_appearance_tab(), "Advanced")

        layout.addWidget(tabs)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        btns.addWidget(cancel)
        btns.addWidget(save)
        layout.addLayout(btns)

    # ------------------------------------------------------------------
    def _build_workspace_tab(self) -> QWidget:
        """工作区设置：只作用于当前工作区，覆盖全局对应项（留空则沿用全局）。"""
        page = QWidget()
        v = QVBoxLayout(page)
        title = QLabel("工作区设置")
        title.setObjectName("settingsTitle")
        v.addWidget(title)
        sub = QLabel("这些设置只作用于当前工作区，覆盖全局对应项；留空则沿用全局设置。")
        sub.setObjectName("settingsSubtitle")
        v.addWidget(sub)

        f = QFormLayout()
        self.ws_path_lbl = QLabel(str(getattr(self.cfg, "workspace", "") or "未设置"))
        self.ws_path_lbl.setWordWrap(True)
        f.addRow("工作区", self.ws_path_lbl)

        self.ws_model = QLineEdit()
        self.ws_model.setPlaceholderText("Pilot 主模型 id（留空=沿用全局）")
        self.ws_copilot = QLineEdit()
        self.ws_copilot.setPlaceholderText("Copilot 副驾驶模型 id（留空=沿用全局）")
        self.ws_suggest = QLineEdit()
        self.ws_suggest.setPlaceholderText("建议生成模型 id，逗号分隔（留空=沿用全局）")
        self.ws_mode = QComboBox()
        self.ws_mode.addItem("沿用全局", "")
        self.ws_mode.addItem("chat", "chat")
        self.ws_mode.addItem("builder", "builder")
        self.ws_mode.addItem("experts", "experts")
        f.addRow("Pilot 模型", self.ws_model)
        f.addRow("Copilot 模型", self.ws_copilot)
        f.addRow("建议模型", self.ws_suggest)
        f.addRow("Agent 形态", self.ws_mode)
        v.addLayout(f)

        btn = QPushButton("保存工作区设置")
        btn.setObjectName("primary")
        btn.clicked.connect(self._save_workspace)
        v.addWidget(btn)
        v.addStretch(1)

        self._load_workspace()
        return page

    def _load_workspace(self):
        from .workspace_config import load_ws
        ws = load_ws(getattr(self.cfg, "workspace", "")) or {}
        self.ws_model.setText(str(ws.get("model", "") or ""))
        self.ws_copilot.setText(str(ws.get("copilot_model", "") or ""))
        self.ws_suggest.setText(",".join(ws.get("suggest_models", []) or []))
        idx = self.ws_mode.findData(ws.get("agent_mode", "") or "")
        self.ws_mode.setCurrentIndex(idx if idx >= 0 else 0)

    def _save_workspace(self):
        from .workspace_config import save_ws
        data = {
            "model": self.ws_model.text().strip(),
            "copilot_model": self.ws_copilot.text().strip(),
            "suggest_models": [s.strip() for s in self.ws_suggest.text().split(",") if s.strip()],
            "agent_mode": self.ws_mode.currentData() or "",
        }
        save_ws(getattr(self.cfg, "workspace", ""), data)
        QMessageBox.information(self, "工作区设置", "已保存当前工作区设置。")

    # ------------------------------------------------------------------
    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        v = QVBoxLayout(body)

        # ---- 模型 ----
        g_model = QGroupBox("模型（OpenAI 兼容；API Key 仅存本机）")
        mf = QFormLayout(g_model)
        provider_row = QHBoxLayout()
        for name, base, model in PROVIDERS:
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, base=base, model=model: self._apply_provider(base, model))
            provider_row.addWidget(b)
        mf.addRow("快速选择:", provider_row)
        self.ed_base = QLineEdit()
        self.ed_key = QLineEdit()
        self.ed_key.setEchoMode(QLineEdit.Password)
        self.ed_model = QLineEdit()
        self.ed_temp = QDoubleSpinBox()
        self.ed_temp.setRange(0.0, 2.0)
        self.ed_temp.setSingleStep(0.1)
        self.ed_max = QSpinBox()
        self.ed_max.setRange(128, 128000)
        self.ed_max.setSingleStep(256)
        mf.addRow("API Base URL", self.ed_base)
        mf.addRow("API Key（仅存本机）", self.ed_key)
        mf.addRow("模型", self.ed_model)
        mf.addRow("Temperature", self.ed_temp)
        mf.addRow("Max Tokens", self.ed_max)
        self.ed_expert_model = QLineEdit()
        self.ed_expert_model.setPlaceholderText("留空 = 用主模型（建议便宜小模型）")
        self.ed_complete_model = QLineEdit()
        self.ed_complete_model.setPlaceholderText("留空 = 用主模型（建议快模型）")
        mf.addRow("守门/补全专家模型", self.ed_expert_model)
        mf.addRow("自动补全模型", self.ed_complete_model)
        test_row = QHBoxLayout()
        self.btn_test = QPushButton("测试连接")
        self.btn_test.clicked.connect(self._test)
        self.lbl_test = QLabel("")
        test_row.addWidget(self.btn_test)
        test_row.addWidget(self.lbl_test)
        mf.addRow("", test_row)
        v.addWidget(g_model)

        # ---- 工作区 ----
        g_ws = QGroupBox("工作区")
        wf = QFormLayout(g_ws)
        self.ed_ws = QLineEdit()
        ws_row = QHBoxLayout()
        ws_row.addWidget(self.ed_ws)
        btn = QPushButton("浏览…")
        btn.clicked.connect(self._pick_ws)
        ws_row.addWidget(btn)
        wf.addRow("工作区目录", ws_row)
        v.addWidget(g_ws)

        # ---- 模块开关 ----
        g_mod = QGroupBox("模块开关")
        mv = QVBoxLayout(g_mod)
        self.switches = {}
        for key, label in SWITCHES:
            cb = QCheckBox(label)
            self.switches[key] = cb
            mv.addWidget(cb)
        v.addWidget(g_mod)

        # ---- 阈值 ----
        g_thr = QGroupBox("阈值")
        tf = QFormLayout(g_thr)
        self.sp_compress = QSpinBox()
        self.sp_compress.setRange(1000, 200000)
        self.sp_compress.setSingleStep(500)
        self.sp_aoe = QSpinBox()
        self.sp_aoe.setRange(3, 300)
        self.sp_keep = QSpinBox()
        self.sp_keep.setRange(2, 30)
        tf.addRow("上下文压缩阈值 (tokens)", self.sp_compress)
        tf.addRow("AOE 分支超时 (秒)", self.sp_aoe)
        tf.addRow("保留最近消息数", self.sp_keep)
        v.addWidget(g_thr)

        # ---- 三大模式 ----
        g_mode = QGroupBox("三大模式")
        mo = QVBoxLayout(g_mode)
        self.cb_traffic = QCheckBox("流量模式：精简输出，停大规模同步")
        self.cb_token = QCheckBox("Token 计费模式：激进压缩、减少调用")
        self.cb_sleep = QCheckBox("肝完睡觉模式：任务完成倒计时后关机/休眠")
        self.ed_sleep_goal = QLineEdit()
        self.ed_sleep_goal.setPlaceholderText("最终目标（完成后触发倒计时）")
        self.ed_sleep_action = QLineEdit("shutdown")
        self.ed_sleep_action.setPlaceholderText("shutdown 或 hibernate")
        self.cb_sleep_auth = QCheckBox("已授权自动关机/休眠")
        mo.addWidget(self.cb_traffic)
        mo.addWidget(self.cb_token)
        mo.addWidget(self.cb_sleep)
        mo.addWidget(QLabel("肝完睡觉目标"))
        mo.addWidget(self.ed_sleep_goal)
        mo.addWidget(QLabel("倒计时结束动作 (shutdown/hibernate)"))
        mo.addWidget(self.ed_sleep_action)
        mo.addWidget(self.cb_sleep_auth)
        v.addWidget(g_mode)

        # ---- 同步 ----
        g_sync = QGroupBox("同步（部署在用户自有服务器）")
        sf = QFormLayout(g_sync)
        self.ed_sync_url = QLineEdit()
        self.ed_sync_url.setPlaceholderText("http://你的服务器:8765")
        self.ed_sync_pw = QLineEdit()
        self.ed_sync_pw.setEchoMode(QLineEdit.Password)
        self.ed_sync_token = QLineEdit()
        self.ed_sync_token.setEchoMode(QLineEdit.Password)
        self.ed_sync_token.setPlaceholderText("服务器环境变量 SYNC_TOKEN 一致（未设置留空）")
        sf.addRow("同步服务器", self.ed_sync_url)
        sf.addRow("加密口令", self.ed_sync_pw)
        sf.addRow("同步令牌", self.ed_sync_token)
        self.sp_auto_drift = QSpinBox()
        self.sp_auto_drift.setRange(0, 1440)
        self.sp_auto_drift.setSingleStep(5)
        self.sp_auto_drift.setSuffix(" 分钟（0=关闭周期推送）")
        sf.addRow("自动漂移周期", self.sp_auto_drift)
        sync_row = QHBoxLayout()
        self.btn_export = QPushButton("导出快照")
        self.btn_export.clicked.connect(self._export)
        self.btn_import = QPushButton("导入快照")
        self.btn_import.clicked.connect(self._import)
        sync_row.addWidget(self.btn_export)
        sync_row.addWidget(self.btn_import)
        sf.addRow(sync_row)
        sf.addRow(QLabel("—— 用户设置导入导出（v4）——"))
        cfg_row = QHBoxLayout()
        self.btn_cfg_export = QPushButton("导出用户设置 JSON")
        self.btn_cfg_export.clicked.connect(self._export_settings)
        self.btn_cfg_import = QPushButton("导入用户设置 JSON")
        self.btn_cfg_import.clicked.connect(self._import_settings)
        cfg_row.addWidget(self.btn_cfg_export)
        cfg_row.addWidget(self.btn_cfg_import)
        sf.addRow(cfg_row)
        v.addWidget(g_sync)

        v.addStretch(1)
        scroll.setWidget(body)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return page

    # ------------------------------------------------------------------
    def _info_card(self, title: str, desc: str) -> QGroupBox:
        """参考图式卡片：标题 + 描述。"""
        g = QGroupBox(title)
        gv = QVBoxLayout(g)
        lbl = QLabel(desc)
        lbl.setWordWrap(True)
        lbl.setObjectName("settingsSubtitle")
        gv.addWidget(lbl)
        return g

    def _build_skills_tab(self) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        v = QVBoxLayout(body)
        v.addWidget(QLabel("Skills（内置技能，Agent 默认可用）"))
        for name, desc in BUILTIN_SKILLS:
            v.addWidget(self._info_card(name, desc))
        v.addStretch(1)
        scroll.setWidget(body)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return page

    def _build_indexing_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        g = QGroupBox("代码索引与 Embedding 匹配")
        gv = QVBoxLayout(g)
        self.cb_emb_enable = QCheckBox("启用外部 Embedding API（api 级别）")
        gv.addWidget(self.cb_emb_enable)
        f = QFormLayout()
        self.cb_emb_level = QComboBox()
        self.cb_emb_level.addItem("字符级（离线零成本）", "char")
        self.cb_emb_level.addItem("本地语义增强 BM25（离线）", "bm25")
        self.cb_emb_level.addItem("外部 Embedding API（语义最强）", "api")
        self.cb_emb_level.setToolTip("匹配越准 → 复用拦截/工具裁剪/资产检索越准 → 喂给高级模型的冗余 token 越少")
        self.cb_emb_model = QComboBox()
        self.cb_emb_model.addItem("（自动选择第一个 Embedding 条目）", "")
        for m in list_embedding_models():
            self.cb_emb_model.addItem(m.display_name(), m.id)
        self.btn_emb_refresh = QPushButton("刷新条目")
        self.btn_emb_refresh.clicked.connect(self._emb_reload)
        hm = QHBoxLayout()
        hm.addWidget(self.cb_emb_model, 1)
        hm.addWidget(self.btn_emb_refresh)
        f.addRow("匹配级别", self.cb_emb_level)
        f.addRow("Embedding 模型", hm)
        gv.addLayout(f)
        v.addWidget(g)
        v.addStretch(1)
        return page

    def _build_integrations_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        g = QGroupBox("联网搜索服务")
        f = QFormLayout(g)
        self.ed_search_url = QLineEdit()
        self.ed_search_url.setPlaceholderText("付费搜索 API 地址（留空 = DuckDuckGo 免费通道）")
        self.ed_search_key = QLineEdit()
        self.ed_search_key.setEchoMode(QLineEdit.Password)
        f.addRow("搜索 API URL", self.ed_search_url)
        f.addRow("搜索 API Key", self.ed_search_key)
        v.addWidget(g)
        v.addStretch(1)
        return page

    # ------------------------------------------------------------------
    def _build_appearance_tab(self) -> QWidget:
        """v4：主题选择 + 逐色编辑 + JSON 导入导出 + 托盘/窗口限制。"""
        page = QWidget()
        outer = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        v = QVBoxLayout(body)

        g1 = QGroupBox("主题")
        f1 = QFormLayout(g1)
        self.cb_theme = QComboBox()
        for k, lbl in themes_mod.THEME_LABELS.items():
            self.cb_theme.addItem(lbl, k)
        self.cb_theme.currentIndexChanged.connect(lambda _: self._load_palette_editor())
        f1.addRow("内置主题", self.cb_theme)
        self.cb_tray = QCheckBox("启用系统托盘（应用驻留后台）")
        self.cb_min_tray = QCheckBox("关闭窗口时最小化到托盘（而非退出）")
        f1.addRow(self.cb_tray)
        f1.addRow(self.cb_min_tray)
        v.addWidget(g1)

        g2 = QGroupBox("窗口大小/宽窄限制（0 = 不限制）")
        f2 = QFormLayout(g2)
        self.sp_min_w = QSpinBox(); self.sp_min_w.setRange(0, 3000)
        self.sp_min_h = QSpinBox(); self.sp_min_h.setRange(0, 2000)
        self.sp_max_w = QSpinBox(); self.sp_max_w.setRange(0, 6000)
        self.sp_max_h = QSpinBox(); self.sp_max_h.setRange(0, 4000)
        f2.addRow("最小宽", self.sp_min_w)
        f2.addRow("最小高", self.sp_min_h)
        f2.addRow("最大宽（0=不限）", self.sp_max_w)
        f2.addRow("最大高（0=不限）", self.sp_max_h)
        v.addWidget(g2)

        g3 = QGroupBox("逐色编辑（基于当前主题修改 → 另存为自定义）")
        g3v = QVBoxLayout(g3)
        self._color_eds = {}
        for key, label in themes_mod.PALETTE_KEYS:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setMinimumWidth(110)
            ed = QLineEdit()
            btn = QPushButton("选色")
            btn.clicked.connect(lambda _=False, k=key: self._pick_color(k))
            row.addWidget(lbl)
            row.addWidget(ed, 1)
            row.addWidget(btn)
            g3v.addLayout(row)
            self._color_eds[key] = ed
        v.addWidget(g3)

        row = QHBoxLayout()
        btn_apply = QPushButton("另存为自定义主题并启用")
        btn_apply.clicked.connect(self._apply_custom_theme)
        btn_imp = QPushButton("导入主题 JSON")
        btn_imp.clicked.connect(self._import_theme)
        btn_exp = QPushButton("导出主题 JSON")
        btn_exp.clicked.connect(self._export_theme)
        row.addWidget(btn_apply)
        row.addWidget(btn_imp)
        row.addWidget(btn_exp)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll)
        return page

    def _current_palette(self) -> dict:
        return themes_mod.get_palette(self.cb_theme.currentData() or "obsidian")

    def _load_palette_editor(self):
        p = self._current_palette()
        for key, ed in self._color_eds.items():
            ed.setText(str(p.get(key, "")))

    def _pick_color(self, key):
        ed = self._color_eds.get(key)
        if ed is None:
            return
        c = QColorDialog.getColor(QColor(ed.text() or "#000000"), self, "选择颜色")
        if c.isValid():
            ed.setText(c.name())

    def _collect_palette(self) -> dict:
        return {k: ed.text().strip() for k, ed in self._color_eds.items()}

    def _apply_custom_theme(self):
        themes_mod.save_custom(self._collect_palette())
        idx = self.cb_theme.findData("custom")
        if idx >= 0:
            self.cb_theme.setCurrentIndex(idx)
        QMessageBox.information(self, "主题", "已保存为自定义主题并启用。")

    def _import_theme(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入主题 JSON", "", "*.json")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return
        if not isinstance(data, dict):
            QMessageBox.warning(self, "导入失败", "JSON 格式不正确（需为颜色键字典）")
            return
        for k, v in data.items():
            ed = self._color_eds.get(k)
            if ed is not None:
                ed.setText(str(v))
        QMessageBox.information(self, "导入", "已载入编辑区，点「另存为自定义主题并启用」生效。")

    def _export_theme(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出主题 JSON", "deverai_theme.json", "*.json")
        if not path:
            return
        try:
            Path(path).write_text(
                json.dumps(self._collect_palette(), ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出", "主题已导出。")

    # ------------------------------------------------------------------
    # v5：模型注册表
    # ------------------------------------------------------------------
    def _build_models_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        # v8.5.6：参考图标题 + Add 按钮
        head = QHBoxLayout()
        title = QLabel("Models")
        title.setStyleSheet("font-weight: 600; font-size: 16px;")
        head.addWidget(title)
        head.addStretch(1)
        btn_add = QPushButton("＋ Add")
        btn_add.setObjectName("primary")
        btn_add.clicked.connect(self._models_add)
        head.addWidget(btn_add)
        btn_score = QPushButton("重搜打分")
        btn_score.clicked.connect(self._models_auto_score)
        head.addWidget(btn_score)
        outer.addLayout(head)
        sub = QLabel("使用自己的 API Key 管理自定义 AI 模型。")
        sub.setObjectName("settingsSubtitle")
        outer.addWidget(sub)

        top = QHBoxLayout()
        self.models_list = QListWidget()
        self.models_list.currentItemChanged.connect(lambda *_: self._models_fill())
        top.addWidget(self.models_list, 1)
        btns = QVBoxLayout()
        for label, slot in (("删除", self._models_delete),
                            ("导出 JSON", self._models_export), ("导入 JSON", self._models_import)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)
        top.addLayout(btns)
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        f = QFormLayout(body)
        self.m_kind = QComboBox()
        self.m_kind.addItem("chat — 对话/工具模型", "chat")
        self.m_kind.addItem("embedding — 向量匹配", "embedding")
        self.m_id = QLineEdit()
        self.m_id.setPlaceholderText("模型 ID（注册表唯一键，如 deepseek-chat）")
        self.m_name = QLineEdit()
        self.m_name.setPlaceholderText("展示名（留空 = 用 ID）")
        self.m_url = QLineEdit()
        self.m_url.setPlaceholderText("留空 = 用全局 API Base")
        self.m_key = QLineEdit()
        self.m_key.setEchoMode(QLineEdit.Password)
        self.m_key.setPlaceholderText("留空 = 用全局 API Key")
        self.m_ctx = QSpinBox()
        self.m_ctx.setRange(1024, 2000000)
        self.m_ctx.setSingleStep(1024)
        self.m_out = QSpinBox()
        self.m_out.setRange(256, 200000)
        self.m_out.setSingleStep(256)
        f.addRow("类型", self.m_kind)
        f.addRow("模型 ID", self.m_id)
        f.addRow("展示名", self.m_name)
        f.addRow("API Base URL", self.m_url)
        f.addRow("API Key", self.m_key)
        f.addRow("上下文大小", self.m_ctx)
        f.addRow("最大输出 tokens", self.m_out)

        caps_row = QHBoxLayout()
        self.m_caps = {}
        for key, label in (("attachment", "附件输入"), ("image", "图片输入"),
                           ("web_search", "自主搜索"), ("crawl", "自主爬取")):
            cb = QCheckBox(label)
            self.m_caps[key] = cb
            caps_row.addWidget(cb)
        f.addRow("能力", caps_row)

        self.m_intro = QPlainTextEdit()
        self.m_intro.setFixedHeight(48)
        self.m_manual = QPlainTextEdit()
        self.m_manual.setFixedHeight(48)
        self.m_manual.setPlaceholderText("如：Kimi K 系 temperature 锁 1，不要传该参数")
        f.addRow("介绍", self.m_intro)
        f.addRow("操作说明", self.m_manual)

        price_row = QHBoxLayout()
        self.m_price_in = QDoubleSpinBox()
        self.m_price_in.setRange(0, 10000)
        self.m_price_in.setDecimals(2)
        self.m_price_out = QDoubleSpinBox()
        self.m_price_out.setRange(0, 10000)
        self.m_price_out.setDecimals(2)
        price_row.addWidget(QLabel("输入/百万"))
        price_row.addWidget(self.m_price_in)
        price_row.addWidget(QLabel("输出/百万"))
        price_row.addWidget(self.m_price_out)
        price_row.addWidget(QLabel("（0 = 免费/未知）"))
        f.addRow("价格", price_row)

        self.m_coef = QDoubleSpinBox()
        self.m_coef.setRange(0.1, 10.0)
        self.m_coef.setSingleStep(0.1)
        f.addRow("计费系数 n", self.m_coef)
        self.m_ctx_expert = QSpinBox()
        self.m_ctx_expert.setRange(0, 2000000)
        self.m_ctx_expert.setSingleStep(1024)
        f.addRow("高级专家上下文上限（0=自动）", self.m_ctx_expert)
        self.m_disp = QComboBox()
        self.m_disp.addItem("full — 全部信息", "full")
        self.m_disp.addItem("partial — 摘要+状态", "partial")
        self.m_disp.addItem("minimal — 仅状态", "minimal")
        f.addRow("高级专家展示级别", self.m_disp)
        self.m_disp_locked = QCheckBox("锁死展示级别（总司令不可改，规划时被告知）")
        f.addRow(self.m_disp_locked)

        grp = QGroupBox("Benchmark 分数（留空 = 未定）")
        self._score_box = QVBoxLayout(grp)
        self._score_rows = {}
        f.addRow(grp)

        save_row = QHBoxLayout()
        btn_save = QPushButton("保存当前模型")
        btn_save.clicked.connect(lambda: self._models_save_current())
        save_row.addWidget(btn_save)
        save_row.addStretch(1)
        f.addRow(save_row)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        return page

    def _models_refresh(self, select_id: str = ""):
        self.models_list.blockSignals(True)
        self.models_list.clear()
        for m in load_models():
            # v8.5.6：卡片式渲染（模型名 + kind + ID + 价格摘要）
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(2)
            name_row = QHBoxLayout()
            nm = QLabel(m.display_name())
            nm.setStyleSheet("font-weight: 600;")
            name_row.addWidget(nm)
            kind = QLabel("embedding" if m.kind == "embedding" else "chat")
            kind.setStyleSheet("font-size: 11px;")
            name_row.addWidget(kind)
            name_row.addStretch(1)
            v.addLayout(name_row)
            summary = m.score_summary()
            meta = QLabel(m.id + ((" · " + summary) if summary else ""))
            meta.setStyleSheet("font-size: 11px;")
            v.addWidget(meta)
            it = QListWidgetItem()
            it.setData(Qt.UserRole, m.id)
            it.setSizeHint(w.sizeHint())
            self.models_list.addItem(it)
            self.models_list.setItemWidget(it, w)
            if m.id == select_id:
                self.models_list.setCurrentItem(it)
        self.models_list.blockSignals(False)
        if self.models_list.count() and self.models_list.currentRow() < 0:
            self.models_list.setCurrentRow(0)
            self._models_fill()  # 选中信号被屏蔽，手动填充一次

    def _current_model_id(self) -> str:
        it = self.models_list.currentItem()
        return it.data(Qt.UserRole) if it is not None else ""

    def _models_fill(self):
        m = get_model(self._current_model_id())
        if m is None:
            return
        self.m_id.setText(m.id)
        self.m_name.setText(m.name)
        i = self.m_kind.findData(m.kind)
        self.m_kind.setCurrentIndex(i if i >= 0 else 0)
        self.m_url.setText(m.url)
        self.m_key.setText(m.api_key)
        self.m_ctx.setValue(m.context_size)
        self.m_out.setValue(m.max_output)
        for k, cb in self.m_caps.items():
            cb.setChecked(bool((m.caps or {}).get(k, False)))
        self.m_intro.setPlainText(m.intro)
        self.m_manual.setPlainText(m.manual)
        self.m_price_in.setValue(float((m.prices or {}).get("in", 0)))
        self.m_price_out.setValue(float((m.prices or {}).get("out", 0)))
        self.m_coef.setValue(m.billing_coef)
        self.m_ctx_expert.setValue(m.ctx_limit_expert)
        idx = self.m_disp.findData(m.display_level)
        self.m_disp.setCurrentIndex(idx if idx >= 0 else 0)
        self.m_disp_locked.setChecked(m.display_locked)
        self._models_build_scores(m.scores or {})

    def _models_build_scores(self, scores: dict):
        while self._score_box.count():
            it = self._score_box.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self._score_rows = {}
        metrics = list(getattr(self.cfg, "score_metrics", []) or [])
        for k in scores or {}:
            if k not in metrics:
                metrics.append(k)  # 模型已有但当前指标列表没有的分，不丢
        for metric in metrics:
            row = QHBoxLayout()
            lbl = QLabel(metric)
            lbl.setMinimumWidth(100)
            ed = QLineEdit()
            ed.setPlaceholderText("未定")
            cb = QCheckBox("定")
            upd = QLabel("")
            s = (scores or {}).get(metric) or {}
            if s.get("value") is not None:
                ed.setText(str(s.get("value")))
            cb.setChecked(bool(s.get("fixed")))
            upd.setText(str(s.get("updated") or ""))
            row.addWidget(lbl)
            row.addWidget(ed, 1)
            row.addWidget(cb)
            row.addWidget(upd)
            self._score_box.addLayout(row)
            self._score_rows[metric] = (ed, cb)

    def _models_collect_scores(self) -> dict:
        old = get_model(self.m_id.text().strip())
        existing = (old.scores or {}) if old is not None else {}
        out = {}
        for metric, (ed, cb) in self._score_rows.items():
            txt = ed.text().strip()
            value = None
            if txt:
                try:
                    value = float(txt)
                except ValueError:
                    value = None
            out[metric] = {"value": value, "fixed": bool(cb.isChecked()),
                           "updated": (existing.get(metric) or {}).get("updated", "")}
        return out

    def _emb_reload(self):
        """刷新 Embedding 模型条目下拉（新增/编辑 kind=embedding 后调用）。"""
        cur = self.cb_emb_model.currentData() or ""
        self.cb_emb_model.clear()
        self.cb_emb_model.addItem("（自动选择第一个 Embedding 条目）", "")
        for m in list_embedding_models():
            self.cb_emb_model.addItem(m.display_name(), m.id)
        i = self.cb_emb_model.findData(cur)
        self.cb_emb_model.setCurrentIndex(i if i >= 0 else 0)

    def _vision_reload(self):
        """刷新视觉专家模型下拉（新增支持图像的模型后调用）。"""
        cur = self.cb_visual_expert.currentData() or ""
        self.cb_visual_expert.clear()
        self.cb_visual_expert.addItem("（自动选择注册表第一个支持图像的模型）", "")
        for m in list_vision_models():
            self.cb_visual_expert.addItem(m.display_name(), m.id)
        i = self.cb_visual_expert.findData(cur)
        self.cb_visual_expert.setCurrentIndex(i if i >= 0 else 0)

    def _models_save_current(self, silent: bool = False) -> bool:
        mid = self.m_id.text().strip()
        if not mid:
            if not silent:
                QMessageBox.warning(self, "模型注册表", "模型 ID 不能为空。")
            return False
        info = ModelInfo(
            id=mid,
            name=self.m_name.text().strip(),
            kind=self.m_kind.currentData() or "chat",
            url=self.m_url.text().strip(),
            api_key=self.m_key.text().strip(),
            context_size=self.m_ctx.value(),
            max_output=self.m_out.value(),
            caps={k: cb.isChecked() for k, cb in self.m_caps.items()},
            intro=self.m_intro.toPlainText().strip(),
            manual=self.m_manual.toPlainText().strip(),
            scores=self._models_collect_scores(),
            prices={"in": self.m_price_in.value(), "out": self.m_price_out.value()},
            billing_coef=self.m_coef.value(),
            ctx_limit_expert=self.m_ctx_expert.value(),
            display_level=self.m_disp.currentData() or "full",
            display_locked=self.m_disp_locked.isChecked(),
        )
        upsert_model(info)
        self._models_refresh(select_id=mid)
        if not silent:
            QMessageBox.information(self, "模型注册表", f"已保存 {mid}。")
        return True

    def _models_auto_score(self):
        """手动触发：对所有 chat 模型重搜 benchmark 打分（后台线程，不阻塞 UI）。"""
        t = _ScoreThread(self.cfg)
        _TEST_THREADS.add(t)
        t.finished.connect(lambda th=t: _TEST_THREADS.discard(th))
        t.finished_ok.connect(lambda msg: (QMessageBox.information(self, "模型打分", msg),
                                           self._models_refresh()))
        t.finished_err.connect(lambda err: QMessageBox.warning(self, "模型打分", f"失败：{err}"))
        t.finished.connect(t.deleteLater)
        t.start()

    def _models_add(self):
        self.models_list.blockSignals(True)
        self.models_list.clearSelection()
        self.models_list.blockSignals(False)
        self.m_id.clear()
        self.m_name.clear()
        self.m_url.clear()
        self.m_key.clear()
        self.m_ctx.setValue(32000)
        self.m_out.setValue(4096)
        for cb in self.m_caps.values():
            cb.setChecked(False)
        self.m_intro.clear()
        self.m_manual.clear()
        self.m_price_in.setValue(0)
        self.m_price_out.setValue(0)
        self.m_coef.setValue(1.0)
        self.m_ctx_expert.setValue(0)
        self.m_disp.setCurrentIndex(0)
        self.m_disp_locked.setChecked(False)
        self.m_kind.setCurrentIndex(0)  # 新建默认 chat，避免残留 embedding 类型
        self._models_build_scores({})
        self.m_id.setFocus()

    def _models_delete(self):
        mid = self._current_model_id()
        if not mid:
            return
        if QMessageBox.question(self, "删除模型",
                                f"从注册表移除 {mid}？") != QMessageBox.Yes:
            return
        delete_model(mid)
        self._models_refresh()

    def _models_export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出模型注册表", "deverai_models.json", "*.json")
        if not path:
            return
        try:
            Path(path).write_text(
                json.dumps([asdict(m) for m in load_models()], ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出", "模型注册表已导出（含 API Key，请妥善保管）。")

    def _models_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入模型注册表", "", "*.json")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return
        if not isinstance(data, list):
            QMessageBox.warning(self, "导入失败", "JSON 应为模型条目列表")
            return
        n = 0
        for d in data:
            if not isinstance(d, dict):
                continue
            try:
                upsert_model(model_from_dict(d))
                n += 1
            except (KeyError, TypeError, ValueError):
                continue
        self._models_refresh()
        QMessageBox.information(self, "导入", f"已导入 {n} 个模型（同 ID 覆盖）。")

    # ------------------------------------------------------------------
    # v5：专家团设置
    # ------------------------------------------------------------------
    def _build_agents_tab(self) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        v = QVBoxLayout(body)

        # v8.5.6：参考图 Built-in Experts 卡片列表
        head = QLabel("Built-in (Experts)")
        head.setStyleSheet("font-weight: 600; font-size: 13px;")
        v.addWidget(head)
        tip = QLabel("可为每个子 Agent 配置模型、工具等；单独定制后，聊天中切换模型只影响主 Agent。")
        tip.setObjectName("settingsSubtitle")
        tip.setWordWrap(True)
        v.addWidget(tip)
        for name, desc in BUILTIN_EXPERTS:
            v.addWidget(self._info_card(name, desc))
        v.addWidget(QLabel("Custom"))
        v.addWidget(self._info_card("No Agent Available", "暂无自定义 Agent。"))
        v.addSpacing(8)

        g1 = QGroupBox("角色模型（留空 = 用主模型）")
        f1 = QFormLayout(g1)
        self.ed_commander = QLineEdit()
        self.ed_commander.setPlaceholderText("总司令：规划/反馈汇总（建议强模型）")
        self.ed_copilot = QLineEdit()
        self.ed_copilot.setPlaceholderText("副驾驶：危险/锁异常审查（事件触发）")
        self.ed_helper = QLineEdit()
        self.ed_helper.setPlaceholderText("助手：搜索结果摘要等杂活")
        self.ed_judge = QLineEdit()
        self.ed_judge.setPlaceholderText("打分执行者：Benchmark 打分")
        f1.addRow("总司令模型", self.ed_commander)
        f1.addRow("副驾驶模型", self.ed_copilot)
        f1.addRow("助手模型", self.ed_helper)
        f1.addRow("打分执行模型", self.ed_judge)
        v.addWidget(g1)

        g2 = QGroupBox("调度与审批")
        f2 = QFormLayout(g2)
        self.cb_pick = QComboBox()
        self.cb_pick.addItem("a — 总司令手动指派", "a")
        self.cb_pick.addItem("b.1 — 性能优先（加权排名）", "b.1")
        self.cb_pick.addItem("b.2 — 性价比优先（分数÷价格）", "b.2")
        self.cb_appr = QComboBox()
        self.cb_appr.addItem("danger — 仅危险内容审批", "danger")
        self.cb_appr.addItem("all — 全部审批", "all")
        self.cb_appr.addItem("copilot — 帮我审批（副驾驶）", "copilot")
        self.cb_appr.addItem("free — 全放", "free")
        self.cb_toolreview = QComboBox()
        self.cb_toolreview.addItem("copilot — 副驾驶审核自研工具", "copilot")
        self.cb_toolreview.addItem("commander — 总司令审核自研工具", "commander")
        self.sp_ratio = QDoubleSpinBox()
        self.sp_ratio.setRange(0.1, 1.0)
        self.sp_ratio.setSingleStep(0.05)
        self.cb_default_serial = QCheckBox("默认串行（单次串行 chip 仍可临时覆盖）")
        self.cb_lowmem = QCheckBox("低内存模式：强制串行 + 少渲染动画 + 关 embedding（子串匹配）")
        f2.addRow("专家模型选择", self.cb_pick)
        f2.addRow("审批门模式", self.cb_appr)
        f2.addRow("工具审核者", self.cb_toolreview)
        f2.addRow("反馈收集比例（上取整）", self.sp_ratio)
        f2.addRow(self.cb_default_serial)
        f2.addRow(self.cb_lowmem)
        v.addWidget(g2)

        # v8.3 任务级快照 / 依赖树 / 建议系统 组
        g_snap = QGroupBox("任务级快照 / 依赖树 / 建议系统")
        sv = QVBoxLayout(g_snap)
        self.cb_snap = QCheckBox("启用任务级快照（一轮对话=标题+工作区压缩+2次工具回退）")
        self.cb_tree = QCheckBox("启用依赖树（自动扫描 import/require + 校验缺漏）")
        self.cb_suggest = QCheckBox("启用建议系统（消息发送时展示 AI 精选建议）")
        self.cb_tree_end = QCheckBox("依赖树&完整快照在回合结束时更新（取消=开始时）")
        sv.addWidget(self.cb_snap)
        sv.addWidget(self.cb_tree)
        sv.addWidget(self.cb_suggest)
        sv.addWidget(self.cb_tree_end)
        f_snap = QFormLayout()
        self.sp_snap_max = QSpinBox()
        self.sp_snap_max.setRange(50, 100000)
        self.sp_snap_max.setSingleStep(512)
        self.sp_snap_max.setSuffix(" MB")
        self.sp_rollback = QSpinBox()
        self.sp_rollback.setRange(1, 10)
        self.sp_delay = QSpinBox()
        self.sp_delay.setRange(0, 120)
        self.sp_delay.setSuffix(" s")
        f_snap.addRow("快照总上限", self.sp_snap_max)
        f_snap.addRow("轮内保留工具回退次数", self.sp_rollback)
        f_snap.addRow("AI 默认问答延迟（0=立即决策）", self.sp_delay)
        sv.addLayout(f_snap)
        v.addWidget(g_snap)

        # v8.4 Python 应用截图 / UI 自截图确认组
        g_shot = QGroupBox("Python 应用截图 / UI 自截图确认")
        sv2 = QVBoxLayout(g_shot)
        self.cb_app_shot = QCheckBox("启用应用截图（app_screenshot：运行 Python UI 脚本并截图）")
        self.cb_ui_review = QCheckBox("启用截图视觉审查（ui_review：截图后调视觉专家确认效果）")
        sv2.addWidget(self.cb_app_shot)
        sv2.addWidget(self.cb_ui_review)
        f_shot = QFormLayout()
        self.cb_visual_expert = QComboBox()
        self.cb_visual_expert.addItem("（自动选择注册表第一个支持图像的模型）", "")
        for m in list_vision_models():
            self.cb_visual_expert.addItem(m.display_name(), m.id)
        self.btn_vision_refresh = QPushButton("刷新条目")
        self.btn_vision_refresh.clicked.connect(self._vision_reload)
        hv = QHBoxLayout()
        hv.addWidget(self.cb_visual_expert, 1)
        hv.addWidget(self.btn_vision_refresh)
        f_shot.addRow("视觉专家模型", hv)
        sv2.addLayout(f_shot)
        sv2.addWidget(QLabel(
            "自检流程：制造本地 UI → app_screenshot 截图 → 主模型能看图直接看，"
            "不能看图则 ui_review 调视觉专家审查后再改。"))
        v.addWidget(g_shot)

        g_auto = QGroupBox("外部程序/浏览器自动化")
        auto_v = QVBoxLayout(g_auto)
        auto_v.addWidget(QLabel(
            "启用后（ENABLE_UI_AUTOMATION）AI 可操作外部 exe 与浏览器；"
            "每个点击/输入/按键动作都经副驾驶监督与用户审批。"))
        f_auto = QFormLayout()
        self.ed_ui_auto_exe = QLineEdit()
        self.ed_ui_auto_exe.setPlaceholderText("允许 AI 启动的唯一外部 exe 绝对路径（留空则禁用 exe_launch）")
        f_auto.addRow("外部 exe 路径", self.ed_ui_auto_exe)
        auto_v.addLayout(f_auto)
        v.addWidget(g_auto)

        g3 = QGroupBox("Benchmark 打分体系")
        g3v = QVBoxLayout(g3)
        g3v.addWidget(QLabel("分数指标（增删后手动重搜 / 到间隔自动刷新）"))
        row = QHBoxLayout()
        self.metrics_list = QListWidget()
        self.metrics_list.setFixedHeight(110)
        row.addWidget(self.metrics_list, 1)
        col = QVBoxLayout()
        self.ed_metric = QLineEdit()
        self.ed_metric.setPlaceholderText("新指标名")
        b_add = QPushButton("添加")
        b_add.clicked.connect(self._metric_add)
        b_del = QPushButton("删除")
        b_del.clicked.connect(self._metric_del)
        col.addWidget(self.ed_metric)
        col.addWidget(b_add)
        col.addWidget(b_del)
        col.addStretch(1)
        row.addLayout(col)
        g3v.addLayout(row)
        f3 = QFormLayout()
        self.sp_refresh = QSpinBox()
        self.sp_refresh.setRange(1, 24 * 30)
        self.sp_costratio = QDoubleSpinBox()
        self.sp_costratio.setRange(0.1, 100.0)
        self.sp_costratio.setSingleStep(0.5)
        f3.addRow("自动刷新间隔（小时）", self.sp_refresh)
        f3.addRow("输入:输出用量比（b.2 折算）", self.sp_costratio)
        g3v.addLayout(f3)
        v.addWidget(g3)

        v.addStretch(1)
        scroll.setWidget(body)
        outer = QVBoxLayout(page)
        outer.addWidget(scroll)
        return page

    def _metric_add(self):
        t = self.ed_metric.text().strip()
        if not t:
            return
        if any(self.metrics_list.item(i).text() == t
               for i in range(self.metrics_list.count())):
            return
        self.metrics_list.addItem(t)
        self.ed_metric.clear()

    def _metric_del(self):
        row = self.metrics_list.currentRow()
        if row >= 0:
            self.metrics_list.takeItem(row)

    # ---- v4：用户设置导入导出 ----
    def _export_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出用户设置", "deverai_settings.json", "*.json")
        if not path:
            return
        # 密钥排除穷举（Design 决策 41）——sync_token/drift_api_key/search_api_key/dashscope_api_key 同样不外流
        secret_fields = ("api_key", "sync_password", "sync_token", "drift_api_key", "search_api_key", "dashscope_api_key")
        data = {k: v for k, v in self.cfg.__dict__.items() if k not in secret_fields}
        try:
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出", "用户设置已导出（不含 API Key、同步口令/令牌、搜索/漂移/DashScope 密钥）。")

    def _import_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入用户设置", "", "*.json")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return
        if not isinstance(data, dict):
            QMessageBox.warning(self, "导入失败", "JSON 格式不正确")
            return
        known = set(self.cfg.__dict__.keys())
        secret_fields = ("api_key", "sync_password", "sync_token", "drift_api_key", "search_api_key", "dashscope_api_key")
        skipped = []
        for k, v in data.items():
            if k not in known or k in secret_fields:
                continue
            # 类型校验：拒绝类型不匹配的字段（bool 与 int 不互认）
            cur = getattr(self.cfg, k)
            if cur is not None and not isinstance(v, type(cur)) and type(v) is not type(cur):
                skipped.append(k)
                continue
            if isinstance(cur, bool) != isinstance(v, bool):
                skipped.append(k)
                continue
            setattr(self.cfg, k, v)  # 暂存到 cfg，点「保存」才落盘，取消时自动回滚
        self._load()
        msg = "用户设置已导入，点「保存」生效、「取消」放弃。"
        if skipped:
            msg += f"\n已跳过类型不匹配字段：{', '.join(skipped)}"
        QMessageBox.information(self, "导入", msg)

    # ------------------------------------------------------------------
    def _apply_provider(self, base, model):
        self.ed_base.setText(base)
        self.ed_model.setText(model)

    def _load(self):
        c = self.cfg
        self.ed_base.setText(c.api_base_url)
        self.ed_key.setText(c.api_key)
        self.ed_model.setText(c.model)
        self.ed_temp.setValue(c.temperature)
        self.ed_max.setValue(c.max_tokens)
        self.ed_ws.setText(c.workspace)
        for key, cb in self.switches.items():
            cb.setChecked(bool(getattr(c, key)))
        self.sp_compress.setValue(c.compress_threshold_tokens)
        self.sp_aoe.setValue(c.aoe_timeout_s)
        self.sp_keep.setValue(c.context_keep_recent)
        self.cb_traffic.setChecked(c.traffic_mode)
        self.cb_token.setChecked(c.token_mode)
        self.cb_sleep.setChecked(c.sleep_enabled)
        self.ed_sleep_goal.setText(c.sleep_goal)
        self.ed_sleep_action.setText(c.sleep_action)
        self.cb_sleep_auth.setChecked(c.sleep_authorized)
        self.ed_sync_url.setText(c.sync_server_url)
        self.ed_sync_pw.setText(c.sync_password)
        self.ed_sync_token.setText(getattr(c, "sync_token", ""))
        self.sp_auto_drift.setValue(int(getattr(c, "auto_drift_interval_min", 30) or 0))
        # v4
        self.ed_expert_model.setText(getattr(c, "expert_model", ""))
        self.ed_complete_model.setText(getattr(c, "complete_model", ""))
        self.cb_tray.setChecked(bool(getattr(c, "ENABLE_TRAY", True)))
        self.cb_min_tray.setChecked(bool(getattr(c, "minimize_to_tray", False)))
        idx = self.cb_theme.findData(getattr(c, "theme", "obsidian"))
        self.cb_theme.setCurrentIndex(idx if idx >= 0 else 0)
        self.sp_min_w.setValue(int(getattr(c, "win_min_w", 900)))
        self.sp_min_h.setValue(int(getattr(c, "win_min_h", 560)))
        self.sp_max_w.setValue(int(getattr(c, "win_max_w", 0)))
        self.sp_max_h.setValue(int(getattr(c, "win_max_h", 0)))
        self._load_palette_editor()
        # v5 模型注册表 + 专家团
        if not load_models():  # 仅首次（空注册表）种入主模型；不复活用户删掉的条目
            ensure_seed(c)
        self._models_refresh()
        self.ed_commander.setText(getattr(c, "commander_model", ""))
        self.ed_copilot.setText(getattr(c, "copilot_model", ""))
        self.ed_helper.setText(getattr(c, "helper_model", ""))
        self.ed_judge.setText(getattr(c, "judge_model", ""))
        idx = self.cb_pick.findData(getattr(c, "model_pick_mode", "a"))
        self.cb_pick.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self.cb_appr.findData(getattr(c, "approval_mode", "danger"))
        self.cb_appr.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self.cb_toolreview.findData(getattr(c, "tool_review_mode", "copilot"))
        self.cb_toolreview.setCurrentIndex(idx if idx >= 0 else 0)
        self.sp_ratio.setValue(float(getattr(c, "expert_collect_ratio", 0.4)))
        self.cb_default_serial.setChecked(bool(getattr(c, "experts_default_serial", False)))
        self.cb_lowmem.setChecked(bool(getattr(c, "low_memory_mode", False)))
        # v8.3 任务级快照/依赖树/建议
        self.cb_snap.setChecked(bool(getattr(c, "ENABLE_SESSION_SNAP", True)))
        self.cb_tree.setChecked(bool(getattr(c, "ENABLE_DEP_TREE", True)))
        self.cb_suggest.setChecked(bool(getattr(c, "ENABLE_SUGGEST", True)))
        self.cb_tree_end.setChecked(bool(getattr(c, "tree_update_at_round_end", True)))
        self.sp_snap_max.setValue(int(getattr(c, "session_snapshot_max_mb", 3072)))
        self.sp_rollback.setValue(int(getattr(c, "round_keep_rollback", 2)))
        self.sp_delay.setValue(int(getattr(c, "ai_decision_delay_s", 0)))
        # v8.4 应用截图 / UI 自截图确认
        self.cb_app_shot.setChecked(bool(getattr(c, "ENABLE_APP_SHOT", True)))
        self.cb_ui_review.setChecked(bool(getattr(c, "ENABLE_UI_REVIEW", True)))
        i = self.cb_visual_expert.findData(getattr(c, "visual_expert_model", ""))
        self.cb_visual_expert.setCurrentIndex(i if i >= 0 else 0)
        self.ed_ui_auto_exe.setText(getattr(c, "ui_automation_exe", ""))
        # v8.1 Embedding 级别匹配
        self.cb_emb_enable.setChecked(bool(getattr(c, "ENABLE_EMBEDDING_API", True)))
        i = self.cb_emb_level.findData(getattr(c, "embedding_level", "char"))
        self.cb_emb_level.setCurrentIndex(i if i >= 0 else 0)
        i = self.cb_emb_model.findData(getattr(c, "embedding_model", ""))
        self.cb_emb_model.setCurrentIndex(i if i >= 0 else 0)
        self.metrics_list.clear()
        for metric in getattr(c, "score_metrics", []) or []:
            self.metrics_list.addItem(metric)
        self.sp_refresh.setValue(int(getattr(c, "score_refresh_hours", 168)))
        self.sp_costratio.setValue(float(getattr(c, "cost_ratio_in_out", 9.0)))
        self.ed_search_url.setText(getattr(c, "search_api_url", ""))
        self.ed_search_key.setText(getattr(c, "search_api_key", ""))

    def _save(self):
        c = self.cfg
        c.api_base_url = self.ed_base.text().strip()
        c.api_key = self.ed_key.text().strip()
        c.model = self.ed_model.text().strip()
        c.temperature = self.ed_temp.value()
        c.max_tokens = self.ed_max.value()
        c.workspace = self.ed_ws.text().strip()
        for key, cb in self.switches.items():
            setattr(c, key, cb.isChecked())
        c.compress_threshold_tokens = self.sp_compress.value()
        c.aoe_timeout_s = self.sp_aoe.value()
        c.context_keep_recent = self.sp_keep.value()
        c.traffic_mode = self.cb_traffic.isChecked()
        c.token_mode = self.cb_token.isChecked()
        c.sleep_enabled = self.cb_sleep.isChecked()
        c.sleep_goal = self.ed_sleep_goal.text().strip()
        c.sleep_action = self.ed_sleep_action.text().strip()
        c.sleep_authorized = self.cb_sleep_auth.isChecked()
        c.sync_server_url = self.ed_sync_url.text().strip()
        c.sync_password = self.ed_sync_pw.text().strip()
        c.sync_token = self.ed_sync_token.text().strip()
        c.auto_drift_interval_min = self.sp_auto_drift.value()
        # v4
        c.expert_model = self.ed_expert_model.text().strip()
        c.complete_model = self.ed_complete_model.text().strip()
        c.ENABLE_TRAY = self.cb_tray.isChecked()
        c.minimize_to_tray = self.cb_min_tray.isChecked()
        c.theme = self.cb_theme.currentData() or "obsidian"
        c.win_min_w = self.sp_min_w.value()
        c.win_min_h = self.sp_min_h.value()
        c.win_max_w = self.sp_max_w.value()
        c.win_max_h = self.sp_max_h.value()
        # v5 专家团
        c.commander_model = self.ed_commander.text().strip()
        c.copilot_model = self.ed_copilot.text().strip()
        c.helper_model = self.ed_helper.text().strip()
        c.judge_model = self.ed_judge.text().strip()
        c.model_pick_mode = self.cb_pick.currentData() or "a"
        c.approval_mode = self.cb_appr.currentData() or "danger"
        c.tool_review_mode = self.cb_toolreview.currentData() or "copilot"
        c.expert_collect_ratio = self.sp_ratio.value()
        c.experts_default_serial = self.cb_default_serial.isChecked()
        c.low_memory_mode = self.cb_lowmem.isChecked()
        # v8.3 任务级快照/依赖树/建议
        c.ENABLE_SESSION_SNAP = self.cb_snap.isChecked()
        c.ENABLE_DEP_TREE = self.cb_tree.isChecked()
        c.ENABLE_SUGGEST = self.cb_suggest.isChecked()
        c.tree_update_at_round_end = self.cb_tree_end.isChecked()
        c.session_snapshot_max_mb = self.sp_snap_max.value()
        c.round_keep_rollback = self.sp_rollback.value()
        c.ai_decision_delay_s = self.sp_delay.value()
        # v8.4 应用截图 / UI 自截图确认
        c.ENABLE_APP_SHOT = self.cb_app_shot.isChecked()
        c.ENABLE_UI_REVIEW = self.cb_ui_review.isChecked()
        c.visual_expert_model = self.cb_visual_expert.currentData() or ""
        c.ui_automation_exe = self.ed_ui_auto_exe.text().strip()
        # v8.1 Embedding 级别匹配
        c.ENABLE_EMBEDDING_API = self.cb_emb_enable.isChecked()
        c.embedding_level = self.cb_emb_level.currentData() or "char"
        c.embedding_model = self.cb_emb_model.currentData() or ""
        c.score_metrics = [self.metrics_list.item(i).text()
                           for i in range(self.metrics_list.count())]
        c.score_refresh_hours = self.sp_refresh.value()
        c.cost_ratio_in_out = self.sp_costratio.value()
        c.search_api_url = self.ed_search_url.text().strip()
        c.search_api_key = self.ed_search_key.text().strip()
        # v5 模型注册表：当前编辑条目随整体保存一起落盘
        if self.m_id.text().strip():
            self._models_save_current(silent=True)
        c.save()
        self._accepted = True
        self.accept()

    def _pick_ws(self):
        path = QFileDialog.getExistingDirectory(self, "选择工作区目录")
        if path:
            self.ed_ws.setText(path)

    def _test(self):
        self.lbl_test.setText("测试中…")
        self.btn_test.setEnabled(False)
        # v8.14：基于当前 self.cfg 创建副本，保留所有配置字段（而非新建默认 Config）
        from dataclasses import replace as _replace
        cfg = _replace(self.cfg)
        cfg.api_base_url = self.ed_base.text().strip()
        cfg.api_key = self.ed_key.text().strip()
        cfg.model = self.ed_model.text().strip()
        self._test_thread = _TestThread(cfg)
        _TEST_THREADS.add(self._test_thread)
        self._test_thread.finished.connect(lambda th=self._test_thread: _TEST_THREADS.discard(th))
        self._test_thread.finished_ok.connect(self._test_done_ok)
        self._test_thread.finished_err.connect(self._test_done_err)
        self._test_thread.finished.connect(self._test_thread.deleteLater)  # v8.14：防 QThread 对象泄漏
        self._test_thread.start()

    def _test_done_ok(self, msg):
        self.btn_test.setEnabled(True)
        self.lbl_test.setText("✓ " + msg)

    def _test_done_err(self, err):
        self.btn_test.setEnabled(True)
        self.lbl_test.setText("✗ " + err)

    def closeEvent(self, e):
        if not self._accepted:
            # 取消/关窗：回滚暂存到 cfg 的修改（含导入设置）
            for k, v in self._cfg_backup.items():
                setattr(self.cfg, k, v)
        t = getattr(self, "_test_thread", None)
        if t is not None and t.isRunning():
            # 断开信号避免到达已销毁对象，再有界等待（run 内 wait_for 20s，不无限卡 UI）
            try:
                t.finished_ok.disconnect()
                t.finished_err.disconnect()
            except TypeError:
                pass
            t.wait(3000)
        e.accept()

    # ---- 同步 ----
    def _export(self):
        p = self.parent()
        history = list(getattr(p, "history", [])) if p is not None else []
        vault = getattr(p, "vault", None) if p is not None else None
        payload = sync_mod.build_snapshot(history, vault)
        text = sync_mod.export_snapshot_bytes(payload, self.ed_sync_pw.text())
        path, _ = QFileDialog.getSaveFileName(self, "导出快照", "deverai_snapshot.deverai")
        if path:
            from .storage import save_text
            save_text(path, text)
            QMessageBox.information(self, "导出", "快照已导出")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入快照", "", "*.deverai")
        if not path:
            return
        try:
            payload = sync_mod.import_snapshot_bytes(Path(path).read_text(encoding="utf-8"), self.ed_sync_pw.text())
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return
        # 应用会话历史
        history = payload.get("history", [])
        p = self.parent()
        if history and p is not None and hasattr(p, "set_history"):
            p.set_history(history)
        QMessageBox.information(self, "导入", f"已导入（历史 {len(history)} 条）")
