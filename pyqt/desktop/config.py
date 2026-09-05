"""全局配置：加载/保存 data/config.json，含 API 配置、模块开关、阈值、模式、同步。"""
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# 将工作区根目录加入 path，以便导入统一 api_keys 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from api_keys import get_api_key

from .storage import load_json, save_json

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"


@dataclass
class Config:
    # ---- LLM API（OpenAI 兼容）----
    api_base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.3
    max_tokens: int = 4096

    # ---- 工作区 ----
    workspace: str = str(APP_DIR)

    # ---- 模块开关 ----
    ENABLE_VAULT: bool = True
    ENABLE_AOE: bool = True
    ENABLE_SUBAGENT: bool = True
    ENABLE_SYNC: bool = True
    ENABLE_MODES: bool = True
    ENABLE_ERR_MIRROR: bool = True
    ENABLE_AGENT_MEMORY: bool = True    # v8.18: Agent 长期记忆库（教训沉淀+检索注入+漂移合体）
    ENABLE_APPROVAL: bool = True
    ALLOW_AI_DELETE: bool = False
    ENABLE_CTX_EXPERT: bool = True      # v4: 上下文守门专家
    ENABLE_TRAY: bool = True            # v4: 系统托盘
    minimize_to_tray: bool = False      # v4: 关闭时最小化到托盘
    ENABLE_STREAM_COMPLETE: bool = True  # v4: 逐行流式补全
    # ---- 多会话标签（v8.16）----
    ENABLE_MULTI_SESSION: bool = True    # v8.16: 聊天区顶部标签条（关闭=旧单会话行为）

    # ---- 专家团（v5）----
    ENABLE_EXPERTS: bool = True          # v5: 专家团模式
    ENABLE_LOCKS: bool = True            # v5: 租约锁/文件所有权
    ENABLE_WEB_SEARCH: bool = True       # v5: 互联网搜索工具
    approval_mode: str = "danger"        # v5: all | danger | copilot | free
    expert_collect_ratio: float = 0.4    # v5: 反馈回收比例（2/5，上取整）
    experts_default_serial: bool = False # v5: 专家默认串行
    low_memory_mode: bool = False        # v5: 低内存模式（串行/少渲染/不 embedding）
    copilot_model: str = ""              # v5: 副驾驶模型（空=主模型，models.json 中 id）
    helper_model: str = ""               # v5: 助手模型（搜索/摘要/压缩，空=主模型）
    judge_model: str = ""                # v5: 打分执行模型（空=主模型）
    commander_model: str = ""            # v5: 总司令模型（空=主模型）
    model_pick_mode: str = "a"           # v5: 模型调取三法 a | b.1 | b.2
    score_metrics: list = field(default_factory=lambda: [
        "推理能力", "幻觉率", "指令遵循", "工具调用准确率", "长上下文保持", "速度"])
    score_refresh_hours: int = 168       # v5: 自动重搜间隔（小时，0=关闭）
    cost_ratio_in_out: float = 9.0       # v5: 性价比折算输入:输出比（9:1）
    search_api_key: str = ""             # v5: 付费搜索 API key（空=仅 DDG）
    search_api_url: str = ""             # v5: 付费搜索接口 URL（Tavily/Serper 兼容）

    # ---- 自我成长与算力漂移（v6）----
    ENABLE_TOOLSMITH: bool = True        # v6: 自研工具库/工具设计专家
    tool_review_mode: str = "copilot"    # v6: 工具审核者 copilot | commander
    ENABLE_DRIFT: bool = True            # v6: 算力漂移（退出前推送+开机回传）
    drift_api_base: str = ""             # v6: 冷备 Agent 直连 LLM base_url（空=服务器环境变量）
    drift_api_key: str = ""              # v6: 冷备 Agent 直连 LLM key（空=服务器环境变量）
    # ---- 自动算力漂移（v8.9）----
    ENABLE_AUTO_DRIFT: bool = True       # v8.9: 工作期间定时向上推送快照（自动上漂移）
    auto_drift_interval_min: int = 30    # v8.9: 自动漂移周期（分钟，0=关闭周期推送）
    auto_drift_on_exit: bool = True      # v8.9: 退出时默认自动漂移（False=弹三选一问询）

    # ---- 工具医生与 IDE 对标（v6.3）----
    ENABLE_TOOL_DOCTOR: bool = True      # v6.3: 工具 bug 收集与修复 Agent
    ENABLE_CHECKPOINT: bool = True       # v6.3: AI 改动前快照（write/edit 前备份原文件）
    ENABLE_NOTEPAD: bool = True          # v6.3: Agent 跨轮暂存中间结果
    ENABLE_COMMAND_PALETTE: bool = True  # v6.3: Ctrl+Shift+P 命令面板
    ENABLE_FILE_MENTION: bool = True     # v6.3: @-mention 引用文件

    # ---- Embedding 级别匹配（v8.1）----
    ENABLE_EMBEDDING_API: bool = True    # v8.1: 外部 Embedding API 开关
    embedding_level: str = "char"        # v8.1: char | bm25 | api（三级匹配方案）
    embedding_model: str = ""            # v8.1: 注册表 kind=embedding 的模型 id

    # ---- 任务级快照 / 依赖树 / 建议系统（v8.3）----
    ENABLE_SESSION_SNAP: bool = True     # v8.3: 一轮对话任务级快照（标题+工作区压缩）
    session_snapshot_max_mb: int = 3072  # v8.3: 快照总上限 MB（默认 3G），超限淘汰最旧
    round_keep_rollback: int = 2         # v8.3: 轮内保留工具调用前回退次数
    checkpoint_keep_per_file: int = 2    # v8.28: 每文件保留 checkpoint 版本数（C盘友好，默认上两版）
    agent_preset: str = ""               # v8.28: Agent+ 模式预设名（空=自定义；unattended/daily）
    tree_update_at_round_end: bool = True  # v8.3: 依赖树&完整快照在回合结束时更新（False=开始时）
    ai_decision_delay_s: int = 0         # v8.3: 默认问答延迟（秒，0=AI 立即决策）
    ENABLE_DEP_TREE: bool = True         # v8.3: 依赖树自动扫描+校验
    ENABLE_SUGGEST: bool = True          # v8.3: 建议系统（TRAE CUE 式，消息发送时展示精选建议）
    suggest_models: list = field(default_factory=lambda: [])  # v8.3: 建议生成模型（空=主模型）
    ENABLE_SAFETY_RULES: bool = True     # v6.3: AGENT.txt 安全规则注入 system prompt
    ENABLE_CASE_FEEDBACK: bool = True    # v6.4: 用例反哺（连续失败2次→副驾驶生成修复钩子→资产库→同类上下文注入）
    ENABLE_DIFF_PREVIEW: bool = True     # v6.4: 内联差异预览（write/edit 前弹 diff 对比，接受才落盘）
    ENABLE_REMOTE_CMD: bool = False      # v6.5: 远程指挥协调（后台长轮询 sync_server，接收远程命令）
    ENABLE_BROWSER: bool = True          # v6.6: 浏览器控制（超轻量：系统 Edge/Chrome headless）

    # ---- Python 应用截图 / UI 自截图确认（v8.4）----
    ENABLE_APP_SHOT: bool = True         # v8.4: 运行 Python UI 脚本并截图（app_screenshot）
    ENABLE_UI_REVIEW: bool = True        # v8.4: 截图视觉审查（ui_review，调视觉专家模型）
    visual_expert_model: str = ""        # v8.4: 视觉审查专家模型 id（空=自动选注册表第一个 image 模型）

    # ---- 外部程序/浏览器自动化（v8.6）----
    ENABLE_UI_AUTOMATION: bool = False   # v8.6: 允许 AI 操作外部 exe/浏览器（点击/输入/按键）
    ui_automation_exe: str = ""          # v8.6: 允许 AI 启动的唯一外部 exe 绝对路径（空=禁用 exe_launch）
    ENABLE_EXE_JOURNAL: bool = True      # v8.9: 外部 exe 自动化操作记录（data/ui_automation_journal.jsonl）
    ENABLE_BROWSER_CTL: bool = True      # v8.9: 直接操控浏览器（CDP：launch/navigate/click/type/press_keys/close）
    ENABLE_BROWSER_DEVTOOLS: bool = True  # v8.14: F12 开发者工具（Networks/Storage/Console/Sources 面板）
    # ---- 工作树审计与文件分区并发（v8.9）----
    ENABLE_AUDIT_LOG: bool = True        # v8.9: 回退/删除/恢复/漂移回本地审计日志（data/audit.jsonl）
    ENABLE_FILE_PARTITION: bool = True   # v8.9: 文件分区规划并发调度（同层任务按文件冲突分批）

    # ---- 工作轨迹深度增强（v8.10）----
    ENABLE_TRACE_ADVANCED: bool = True  # v8.10: 工作轨迹高级能力（手动标记点/区间查询/操作筛选/详情预览）

    # ---- 语音助手「小龙」（v1.0.0）----
    ENABLE_VOICE_ASSISTANT: bool = False  # v1.0.0: 语音助手「小龙」（关闭即隐藏入口、不注册 RPC）
    ENABLE_INTEGRITY: bool = True        # v1.1.0: DeveraiIntegrityService 完整性校验（HKDF + HMAC-SHA256）

    # ---- 用户文件保护 + 非Git自动备份Worktree增强（v8.25）----
    ENABLE_USER_FILE_PROTECT: bool = True  # v8.25: PPT/Excel/Word/PDF等用户资产：AI禁写禁命令触碰
    ENABLE_FULL_BACKUP: bool = True        # v8.25: 一键备份完整工作区（backups/<ts>_full.zip）
    ENABLE_AMBIGUOUS_GUARD: bool = True    # v8.25: 重名/命名不清自动要求识别、备份/转移
    ENABLE_WORK_COPY: bool = True          # v8.26: 工作副本（copy_user_asset，禁碰=拷贝出去改，原文件不动）
    # ---- 真·算力漂移（v8.27）----
    ENABLE_UNATTENDED: bool = False        # v8.27: 不看守模式（禁提问；非危险自动放行，危险跳过记保留进度台账）
    drift_upload_mode: str = ""            # v8.27: 漂移上传模式（空=退出时询问；minimal/full 记住后不再问）
    # ---- Agent 形态（v4）----
    agent_mode: str = "builder"         # chat | builder | experts
    expert_model: str = ""              # 守门/守护用的轻量模型（空=主模型）
    complete_model: str = ""            # 补全专用模型（空=主模型）

    # ---- 阈值 ----
    compress_threshold_tokens: int = 12000
    aoe_timeout_s: int = 20
    vault_threshold: float = 0.45
    context_keep_recent: int = 6

    # ---- 三大环境硬开关 ----
    traffic_mode: bool = False
    token_mode: bool = False
    sleep_enabled: bool = False
    sleep_goal: str = ""
    sleep_action: str = "shutdown"  # shutdown | hibernate
    sleep_authorized: bool = False

    # ---- 同步（用户自有服务器）----
    sync_server_url: str = ""
    sync_password: str = ""
    sync_token: str = ""          # v8.11：服务器 SYNC_TOKEN 令牌（推送/拉取/漂移请求头 X-Sync-Token）

    # ---- DashScope API（通义万相：文生图/图生图/视频生成）----
    dashscope_api_key: str = ""             # DashScope API Key（通义万相图像/视频生成）

    # ---- 外观（v4）----
    theme: str = "obsidian"             # obsidian | paper | sand | midnight | harness | custom
    win_min_w: int = 900                # 窗口宽窄/大小限制（0 = 不限制）
    win_min_h: int = 560
    win_max_w: int = 0
    win_max_h: int = 0

    def save(self) -> None:
        save_json(CONFIG_PATH, asdict(self))

    def to_public(self) -> dict:
        """对外暴露时隐藏敏感字段。"""
        d = asdict(self)
        d["api_key"] = "***" if d.get("api_key") else ""
        d["sync_password"] = "***" if d.get("sync_password") else ""
        d["sync_token"] = "***" if d.get("sync_token") else ""
        d["search_api_key"] = "***" if d.get("search_api_key") else ""
        d["drift_api_key"] = "***" if d.get("drift_api_key") else ""
        d["dashscope_api_key"] = "***" if d.get("dashscope_api_key") else ""
        return d


# ---- v8.28 Agent+ 模式预设（一组开关的组合；目的：让 AI 把能干的活先干完）----
AGENT_PRESETS = {
    "unattended": {
        "desc": "无人值守：禁提问，非危险动作自动放行，危险跳过记保留进度台账——把能干的活先干完",
        "flags": {"ENABLE_UNATTENDED": True, "agent_mode": "builder"},
    },
    "daily": {
        "desc": "日常值守：恢复默认审批与 diff 预览行为",
        "flags": {"ENABLE_UNATTENDED": False},
    },
}


def apply_agent_preset(cfg, name: str):
    """v8.28：应用 Agent+ 模式预设（返回新 cfg，不改原对象）。未知预设原样返回。"""
    from dataclasses import replace as _replace
    p = AGENT_PRESETS.get(str(name or "").strip())
    if not p:
        return cfg
    kwargs = {k: v for k, v in p.get("flags", {}).items() if hasattr(cfg, k)}
    return _replace(cfg, **kwargs)


_cfg: Config = None


def init_config() -> Config:
    global _cfg
    data = load_json(CONFIG_PATH, {})
    cfg = Config()
    for k, v in data.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    cfg.workspace = cfg.workspace or str(APP_DIR)
    # 统一 api.txt 中的 key 优先级更高
    _api_mappings = {
        "api_key": "deverai_api_key",
        "search_api_key": "deverai_search",
        "drift_api_key": "deverai_drift",
        "dashscope_api_key": "deverai_dashscope",
    }
    for cfg_field, api_name in _api_mappings.items():
        api_val = get_api_key(api_name, fallback="")
        if api_val:
            setattr(cfg, cfg_field, api_val)
    _cfg = cfg
    return cfg


def get_config() -> Config:
    if _cfg is None:
        return init_config()
    return _cfg


def update_config(**fields) -> Config:
    """部分更新配置并落盘。返回最新配置。"""
    cfg = get_config()
    for k, v in fields.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    cfg.save()
    return cfg
