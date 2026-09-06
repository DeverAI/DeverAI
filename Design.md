# DeverAI — 核心设计文档（当前最新状态）

项目代号：DeverAI
核心理念：本地记忆即资产，复用优先于重建；**UI 即 Agent 运行地**——UI 用普通 Python（PyQt6 桌面程序），Agent 直接运行在本机 Python 进程内，操作工作区内的真实文件与真实命令行。

> 本文档只描述项目**当前**的样子（架构、模块、设计决策、开关、安全红线）。
> 版本演进统一记录在 `dev_log/`；实现方法与技术选型见 `Techniques.md`；用户运行指引见 `README.md`；用户偏好与冲突裁决见 `Fact.md`；常见错误类型见 `FreqErr.md`。

---

## 1. 项目定位与核心特点

DeverAI 是一款「UI 即 Agent 运行地」的 AI 开发工作台：AI Agent 直接运行在本机 Python 进程内，以 **PyQt6 桌面应用**为主形态（另有完整网页版 / 超轻量远控 Lite / 命令行 CLI / 同步服务器四个入口），通过 OpenAI 兼容接口直连大模型，无需浏览器权限桥接即可操作工作区内的真实文件与真实命令行。

当前核心特点：

| 特点 | 说明 |
|------|------|
| 1. 算力漂移（自动上下 + 关机续算） | 工作期间按周期自动推送完整工作状态（含冷备副本）到用户自有服务器；退出默认自动漂移；服务器冷备 Agent 续聊；本机开机自动拉取合并云端产出并去重。服务器端有 HTML 版状态页：手机/平板/浏览器打开服务器地址即可查看漂移状态、查看冷备会话并从 HTML 版本状态继续任务。漂移状态机：漂移退出登记项目编号 → 开机查询未回传计数 → 未跑完自动锁定项目（只读）→ 一键「漂移回本地」复位。 |
| 2. 总司令调度文件分区并发 | 专家团（总司令/普通专家/高级专家/副驾驶）DAG 分层并行；文件分区调度器把同层任务按申报文件冲突自动分批——写不同文件的任务并行、写同一文件的任务自动串行；租约锁 + 文件所有权申报（同文件冲突申报直接拒绝，防并发写丢更新）；写文件强制 CoW 原子替换；串行/并行可切换。 |
| 3. Worktree 独立安全备份审核 | 三层防线：文件级版本快照（改前自动存原内容，可回退）、任务级会话快照（每轮 zip 打包工作区 + 轮内回退点，删除目录前自动整目录 zip 备份、可还原）、依赖树校验（缺文件/过小报警 → 只读锁定）。快照删除前由 AI 独立审查，路径全部防 `../` 与 zip-slip 越界。回退审核日志（`data/audit.jsonl`）记录所有回退/删除/恢复/漂移回本地动作，AI 可用 `list_audits` 自查。 |
| 4. 自动化设计·搜索·工具池 | 工具设计专家（接需求→查重→构建→审核→入库，桌面+网页双端桥接）、自研工具库（prompt 型/script 型 + 工具医生自动修 bug）、资产银行资料检修（inspect/repair）、互联网搜索（DDG/付费 API）、三级文件匹配（char/bm25/embedding）、浏览器控制、直接操控浏览器（CDP 启动/跳转/点击/输入/按键/关闭）与外部 exe 自动化（开关默认关闭 + 副驾驶全程监督 + 操作记录 journal）。 |
| 5. 全量端口检修 | 所有 API 端口有真实实现、前端全部接线；危险命令四端同源 + `danger_ok` 严格确认；跨端快照互通（网页 AES-GCM 信封 / web-cold / 桌面 zlib 冷备）；远程指挥控制台在 sync_server 首页即可下发 shell/读写文件/ping 并轮询结果；网页版多任务会话、附件引用、知识文档直达等控件全部真实可用。 |
| 6. 记忆·出关·外部 API 治理（v8.22） | Agent 跨会话长期记忆（桌面 memory.py + 网页 memory.js，四类条目、自动召回注入、漂移合体）；外发内容必须经 outbound_deliver 逐字复述原始要求 + 审核卡批准才出关（邮件仅存草稿）；安全中心维护外部 API 清单，AI 经 api_request → `/api/llm/ext_proxy`（SSRF 防护）调用；一键 heartbeat 泄露检查（8 类密钥 + API 厂商存活）+ 可选 SMTP 报告。 |

---

## 2. 总体架构

### 2.1 桌面版架构（主形态）

```
+---------------------------------------------------------------------+
| PyQt6 桌面程序（python pyqt/main.py）—— UI 即 Agent 运行地           |
| 【界面层】                                                            |
|   gui.py           主窗口：Quest 导航|聊天/编辑器|右缘面板；事件泵/审批 |
|   quest_panels.py  Summary/Trace/任务管理器/建议/GuardBanner 等面板   |
|   panels.py        文件树/资产银行/终端/健康仪表盘                     |
|   settings_dialog  设置页    ide_extras.py 版本回退/命令面板等对话框   |
|   themes.py 主题    icons.py SVG 图标    md.py Markdown 渲染          |
|   highlight.py 语法高亮    trace_preview.py 轨迹详情预览              |
| 【Agent 核心】                                                        |
|   agent.py         Agent 循环（流式+工具+压缩+产物入库+安全规则注入）   |
|   experts.py       专家团：总司令规划/DAG 分层/工具审核/副驾驶          |
|   planner.py       AOE 规划器（DAG 并行 + 20s 熔断 + 资产复用拦截）    |
|   context.py       子Agent 摘要压缩    ctx_expert.py 上下文守门专家   |
|   tools.py         工具集：文件/命令/子Agent/资产/AOE/端口/自研工具     |
|   toolsmith.py     自研工具库（查重→构建→审核→入库）+ 工具医生        |
|   background.py    后台长任务登记表（>3min 转后台）                   |
| 【记忆与生态】                                                        |
|   vault.py         资产银行（bigram 相似度+多维说明书+资料检修）       |
|   err_mirror.py    防呆数据库（报错特征注入提示词）                   |
|   models.py        模型注册表与打分（a/b.1/b.2）    locks.py 租约锁   |
|   matcher.py       三级匹配（char/bm25/embedding）                   |
|   search_tools.py  互联网搜索与文件搜索    auto_score.py 自动打分      |
| 【工作树与同步】                                                      |
|   checkpoint.py    文件级版本快照    session_snap.py 任务级会话快照    |
|   dep_tree.py      依赖树校验        suggest.py 建议系统              |
|   audit.py         回退审核日志      drift.py 漂移状态机              |
|   sync.py/sync_queue.py 快照加密与同步队列    remote_cmd.py 远程指挥  |
|   browser.py/browser_ctl.py 无头与控制浏览器    win_automate.py Win32 |
|   app_shot.py      Python 应用截图    tool_journal.py 自动化操作记录  |
| 【基础设施】                                                          |
|   llm.py/ai_complete.py  LLM 客户端与补全    config.py/storage.py    |
|   modes.py         三大模式（含关机/休眠电源动作） errors.py 错误日志 |
| 数据：data/*.json（见第 5 节）；API Key 仅本机                        |
+---------------------------------------------------------------------+
| 本地直接能力（Python 原生，无任何浏览器权限限制）                     |
|   · 命令行：subprocess 直接执行，操作工作区内已有文件                 |
|   · 文件：Path.read_text / write_text 直接读写（写强制 CoW 原子替换） |
+---------------------------------------------------------------------+
```

### 2.2 网页版架构（第二入口）

- `webui/web_main.py` 启动 FastAPI + uvicorn 并自动打开浏览器；`webui/app/` 严格保持三件事：下发 UI（`webui/static/`）、验证身份（HMAC Cookie + Bearer 会话令牌）、无状态转发（LLM 代理 + 本地资源桥）。
- Agent 决策、工具编排、规划、压缩、资产、模式全部在浏览器端 JS 执行；API Key 只存在于浏览器 localStorage；会话历史存 IndexedDB（多任务会话）。
- 桥接层复用同机 `pyqt/desktop/` 模块（模型注册表、匹配器、快照、依赖树、搜索、浏览器、截图、toolsmith 等），保证与桌面版能力对等。
- 完整版前端通过公共 CDN 加载 `marked` / `dompurify` / `highlight.js` / `monaco-editor`，运行时需可访问 jsdelivr 等 CDN；Lite 版零 CDN 依赖。

### 2.3 Lite / CLI / 同步服务器

- `lite/lite_main.py` + `lite/app/lite_server.py` + `lite/static/lite.html`：超轻量远控（鉴权 + LLM 代理 + 文件桥 + 命令桥），零 CDN 单文件前端，完全自包含（`lite/app/` 为独立副本，不依赖 `webui/app/`），刻意精简（写上限 5MB、无邮箱注册/项目下载/快照桥、文件树无 modified 等）。
- `pyqt/cli_main.py`：交互式命令行 Agent，复用桌面 `Agent` 内核（工具/上下文压缩/专家团/审批门全通用），不依赖 GUI。
- `sync_server.py`：部署在用户自有服务器，提供快照存取（`/push`、`/pull`）、冷备 Agent 续聊（`/chat`）、漂移状态（`/drift/*`）、远程指挥（`/cmd/*`）与 HTML 状态页（`/`、`/chat/page`）；v8.24 起随仓库分发（零 CDN、FastAPI 单文件），数据目录默认 `./sync_data`。

### 2.4 DSH 插件版架构（第六入口，Cordis 静态插件）

- `@deverai/hub`（Cordis 静态 npm 插件，前身为动态插件 `dehub-11/pkg-34`「DeverAI Hub」）：将 DeverAI 的五大核心能力移植到 DeepSeek Harness Web GUI，使 DSH 成为一个接近 DeverAI 体验的 AI 开发工作台。
- **插件代码不在本仓库分发**：静态插件源码与 `node_modules` 符号链接落盘于 DSH 宿主机的 `~/.dsh/profiles/web/deverai-hub/`（含 `package.json`/`lib/index.js`/`lib/client.js`），由 `cordis.patch.yml` 注册 host/client 两个 plugin id（`deverai-hub-host` / `deverai-hub-client`）加载；本仓库仅保留参考图（`参考图/`）与档案记录（`Fact.md` 2026-08-22 条目）。
- Host 半边（Node.js 进程）：通过 `webServer` 注册 HTTP 路由，通过 `harness.handle` 注册 Client→Host RPC 处理器，通过 `fs`/`shell` 服务操作工作区文件与命令。
- Client 半边（浏览器）：通过 `slots.inject` + `slots.register` 在 `shell.overlay` 注册浮层状态面板，在 `settings.section` 注册设置页面，通过 `host.call` 调用 Host RPC。
- 数据存储：DSH 宿主机 `$DEVERAI_DIR/dsh-plugin-storage/` 下的 JSON 文件（`assets.json`、`tools.json`、`drift_state.json`、`experts_state.json`、`checkpoints/`）。
- 本仓库无 `ENABLE_DSH_PLUGIN` 开关（插件开关在 DSH 宿主侧；v8.24 复检修正此前文档失实声明）。

五大核心模块：

| 模块 | Host 路由 | Client UI | 说明 |
|------|-----------|-----------|------|
| 文件树 + 版本快照 | `GET /deverai/fs/tree`、`GET /deverai/fs/read`、`POST /deverai/fs/checkpoint`、`GET /deverai/fs/checkpoints`、`POST /deverai/fs/restore` | 设置页文件树面板 | 浏览工作区目录、读取文件、创建/列出/恢复 checkpoint |
| 资产银行 | `GET /deverai/assets/list`、`GET /deverai/assets/search`、`POST /deverai/assets/store`、`GET /deverai/assets/inspect`、`POST /deverai/assets/repair` | 可扩展 | 资产检索（名称/描述/标签）、入库、资料检修 |
| 算力漂移 + 同步 | `GET /deverai/drift/status`、`POST /deverai/drift/push`、`POST /deverai/drift/pull` | 浮层漂移状态指示 | 漂移状态查看、推送/拉取同步 |
| 自动化工具池 | `GET /deverai/tools/list`、`POST /deverai/tools/create`、`GET /deverai/tools/bugs`、`POST /deverai/tools/fix` | 可扩展 | 自研工具注册、bug 追踪、工具医生 |
| 专家团 + 并发调度 | `GET /deverai/experts/status`、`POST /deverai/experts/start`、`POST /deverai/experts/stop` | 浮层专家状态指示 | 专家团状态查看、启动/停止 |

RPC 处理器（`harness.handle`）：`deverai.fs.tree`、`deverai.assets.search`、`deverai.drift.status`、`deverai.experts.status`。

---

## 3. 入口与运行方式

| 入口 | 命令 | 默认地址 | 说明 |
|------|------|----------|------|
| 桌面版 | `python pyqt/main.py` | 本机 GUI | PyQt6 主形态，功能最全 |
| 完整网页版 | `python webui/web_main.py` | http://127.0.0.1:8765 | FastAPI + 浏览器端 Agent；`--port`、`--no-browser`、`--reload` |
| 超轻量远控 Lite | `python lite/lite_main.py` | http://127.0.0.1:8733 | 极简后端 + 零 CDN 单文件前端；`--port`、`--no-browser`、`--reload` |
| 命令行 CLI | `python pyqt/cli_main.py` | 无 | 交互式 Agent（复用桌面内核，不依赖 GUI，需 httpx）；`--model/--workspace/--mode/--no-color`；会话内 `/help /mode /clear /history /quit` |
| 同步服务器 | `python sync_server.py` | 0.0.0.0:8765 | 部署在用户自有服务器；环境变量 `SYNC_TOKEN`、`DRIFT_API_BASE`、`DRIFT_API_KEY`、`DRIFT_ALLOW_LOOPBACK`、`DRIFT_MODEL`、`SYNC_DATA_DIR`（默认 `./sync_data`）。HTML 状态页 `/`（漂移状态 + 冷备续聊 + 远程指挥控制台）与 `/chat/page`（续聊页）。v8.24 起随仓库分发。 |
| DSH 插件版 | Cordis 静态插件 `@deverai/hub`（宿主机 `~/.dsh/profiles/web/deverai-hub/`） | http://127.0.0.1:3080 | DeepSeek Harness Web GUI 套壳插件；代码在 DSH 宿主机，不在本仓库 |

> 完整网页版与 sync_server 默认端口同为 8765；同一台机器同时运行需用 `python webui/web_main.py --port 8766` 或给 sync_server 改端口错开。

---

## 4. 模块地图

### 4.1 pyqt/desktop/（桌面版核心包，全部模块）

| 模块 | 职责 |
|------|------|
| `agent.py` | Agent 循环：流式对话、工具调用循环、上下文压缩、子Agent 委派、产物入库、安全规则注入 |
| `tools.py` | 工具注册中心：文件/命令/资产/AOE/端口/自研工具/浏览器/exe/截图等 87 个工具处理器、审批门、路径保护、工具裁剪 |
| `experts.py` | 专家团编排：总司令规划、DAG 分层执行、反馈回收、副驾驶、工具审核 |
| `planner.py` | AOE 规划器：DAG 规划、拓扑分层、并行执行、超时熔断、资产复用拦截 |
| `context.py` | 子Agent 上下文压缩（超阈值压缩为 system 摘要） |
| `ctx_expert.py` | 上下文守门专家（pre-round 分块裁决 / post-round 规则守护 / 引用回退） |
| `llm.py` | OpenAI 兼容 LLM 客户端（流式 chat/completions、工具调用、embedding；网络异常转友好文案） |
| `ai_complete.py` | 逐行流式补全与选区 AI 操作（解释/优化/修 Bug/自定义） |
| `config.py` | 全局配置 dataclass（API、开关、阈值、模式、同步），`to_public()` 掩码敏感字段 |
| `storage.py` | JSON 原子持久化（唯一临时名 + `os.replace`） |
| `errors.py` | 错误日志：运行时错误统一写根目录 `Err.log`；读取/清空接口 |
| `err_mirror.py` | 防呆数据库：记录工具失败特征，生成代码前注入提示词 |
| `memory.py` | Agent 长期记忆库（v8.18）：四类条目（fault/lesson/preference/knowledge）、bigram 检索注入、memory_read/memory_record 工具、漂移合体按 id+相似度去重 |
| `codename.py` | 大代号/相对路径安全映射（内存不落盘） |
| `modes.py` | 三大模式：流量模式、肝完睡觉（目标达成→倒计时→关机/休眠）、Token 计费 |
| `locks.py` | 租约锁与文件所有权（TTL+heartbeat+Reaper；`_norm` 路径规范化；过期自愈） |
| `partition.py` | 文件分区调度：同层任务按申报文件冲突分批并行 |
| `background.py` | 后台长任务登记表（>3min 转后台，事件渲染） |
| `vault.py` | 资产银行：bigram 相似度检索、自动评估入库、资料检修 |
| `models.py` | 模型注册表：读写、加权排名 b.1、性价比排名 b.2、视觉模型选择、进程级互斥 |
| `matcher.py` | 三级匹配引擎：char / bm25 / api（OpenAI 兼容 embeddings） |
| `search_tools.py` | 互联网搜索（DDG/付费 API 三级回退）与文件搜索、端口文档工具 |
| `toolsmith.py` | 自研工具库：查重→构建→审核→入库；工具医生（bug 收集/诊断/修复/回归） |
| `auto_score.py` | 模型自动打分与画像更新（judge_model + web_search，时间戳与互斥） |
| `checkpoint.py` | 文件级版本快照（写前备份、按文件聚合、保留策略、越界防护） |
| `session_snap.py` | 任务级会话快照（每轮 zip 打包、轮内回退点、只读状态机、zip-slip 防护、总上限淘汰） |
| `file_protect.py` | 用户文件保护（v8.25）：用户资产后缀 AI 禁写禁命令、用户外部修改感知（data/file_protect.json）、一键全量备份、重名/命名不清治理与隔离；v8.26 起含工作副本（workcopy/）生成与豁免 |
| `dep_tree.py` | 依赖树：正则扫描 import/require、大小下限、缺文件/过小报警 |
| `suggest.py` | 建议系统（TRAE CUE 式，消息发送后生成精选建议） |
| `audit.py` | 回退审核日志：回退/删除/恢复/漂移回本地动作写 `data/audit.jsonl` |
| `drift.py` | 漂移状态机：project_id、退出登记、开机检查、漂移回本地复位 |
| `sync.py` | 快照加密（Fernet/zlib 冷备）、推送/拉取/漂移接口、密钥排除 |
| `sync_queue.py` | 同步队列：变更标记、流量模式挂起/关闭时冲刷 |
| `sessions.py` | 多会话标签（v8.16）：会话索引 + 每会话历史文件的切换/持久化（切换前保存、切换后回放） |
| `terms.py` | 终端环境池（v8.18）：持久 cwd+env 轻量会话（`data/term_envs.json` 原子写） |
| `remote_cmd.py` | 远程指挥客户端：后台长轮询、命令执行、有界结果队列 |
| `browser.py` | 无头浏览器控制（open/read/screenshot/elements；SSRF 校验、有界流式读取） |
| `browser_ctl.py` | 直接操控浏览器：纯标准库 CDP 客户端（launch/navigate/click/type/press_keys/close） |
| `browser_devtools.py` | F12 开发者工具（v8.14）：CDP Networks/Storage/Console/Sources 面板 |
| `win_automate.py` | Win32 外部程序自动化原语（ctypes，非 Windows 安全降级） |
| `app_shot.py` | Python 应用截图：运行脚本→找窗口→QScreen 截图→杀进程（跨线程 Qt 抓图） |
| `tool_journal.py` | 外部 exe 操作记录（`data/ui_automation_journal.jsonl`） |
| `gui.py` | 主窗口：Quest 导航、中央工作区（聊天/编辑器/轨迹）、右缘面板、事件泵、审批弹窗、漂移/建议/快照接线 |
| `panels.py` | 文件树 / 资产银行 / 终端 / 健康仪表盘 |
| `quest_panels.py` | Summary/Section/StatusRow/ModelPickerPopup/TracePanel/TraceTimeline/TraceFlow/TaskManagerPanel/GlobalSuggestPanel/GuardBanner |
| `settings_dialog.py` | 设置对话框：模型/开关/阈值/外观/同步/工作区等分组 |
| `themes.py` | 主题引擎：内置主题（obsidian/paper/sand/midnight/harness）+ 自定义 JSON + QSS 生成 |
| `icons.py` | 桌面 SVG 图标系统（QSvgRenderer，颜色随主题） |
| `md.py` | Markdown→HTML 渲染（转义防注入，Mermaid/HTML 仅样式化源码呈现） |
| `highlight.py` | 语法高亮（QSyntaxHighlighter，多语言规则表） |
| `ide_extras.py` | IDE 对标：@-mention、命令面板、Checkpoint 恢复菜单/对话框 |
| `trace_preview.py` | 轨迹详情预览对话框（缩放、拖动、置顶、元素选择引用） |
| `notepad.py` | 跨轮暂存（`data/notepad.json`，多 key） |
| `workspace_config.py` | 工作区级设置（`data/workspaces/{hash}.json`） |
| `deverai_integrity.py` | DeveraiIntegrityService：HKDF + HMAC-SHA256 防篡改签名服务 |
| `dashscope.py` | DashScope（通义万相）API 客户端：文生图/图生图/视频生成（httpx 直调 REST，零 SDK 依赖） |
| `ui_inspect.py` | UI 元素检视与可靠交互：枚举子窗口/控件、按文本类名定位控件、控件级点击/输入/读取（ctypes Win32，替代脆弱坐标点击） |

### 4.2 webui/app/（完整版网页版后端）

| 模块 | 职责 |
|------|------|
| `__init__.py` | 包标记（空文件） |
| `server.py` | Host Server：下发 UI、身份验证、邮箱注册/账户/项目下载、路由汇总 |
| `auth.py` | HMAC 会话 Cookie + Bearer 会话令牌、`current_user` 鉴权 |
| `users.py` | 用户存储：PBKDF2-SHA256 密码哈希、验证码、邮箱注册 |
| `mailer.py` | SMTP SSL 邮件发送（凭证从环境变量读取） |
| `security.py` | 安全强化：登录/验证码限速、异常 IP、审计日志、危险命令模式 |
| `proxy.py` | 无状态 LLM 转发代理（SSRF 校验、参数白名单、体限） |
| `bridge.py` | 本地资源桥：文件树/读写/删除/重命名、grep/glob、命令执行、浏览器、toolsmith、资产检修、截图等 |
| `lite_server.py` | Lite 极简后端（鉴权 + LLM 代理 + 文件桥 + 命令桥） |
| `meta_bridge.py` | 网页版模型注册表与三级匹配桥（复用 desktop/models.py、matcher.py） |
| `snap_bridge.py` | 网页版可靠性桥（guard/checkpoint/sessions/tree，复用 desktop 模块） |
| `snap_util.py` | 快照桥辅助（专家报告应用到轮 meta） |
| `codename.py` | 网页版大代号/相对路径映射 |
| `config.py` | 服务端配置（host/port/secret/allow_register/bridge_workspace 等） |
| `storage.py` | JSON 原子持久化 |
| `errors.py` | 错误日志（统一写根目录 `Err.log`） |
| `voice_pet_host.py` | 语音助手「小龙」2.0 宿主端：对话历史 + 增强任务管理 + 进度查询 + 配置持久化 |

### 4.3 webui/static/（完整版网页版前端，零构建）

| 文件 | 职责 |
|------|------|
| `index.html` | 完整版单页骨架（登录页 + 三栏主界面 + 各面板/对话框 DOM） |
| `lite.html` | Lite 单文件前端（内联 CSS/JS，零 CDN） |
| `css/style.css` | 全局样式（CSS 变量主题、组件、响应式） |
| `js/core.js` | 配置默认值、IndexedDB 会话、工具函数、字符相似度 |
| `js/main.js` | 启动流程、事件绑定、视图切换、引用条 |
| `js/agent.js` | 浏览器端 Agent 循环：工具调用、上下文守门、快照轮次、工具裁剪 |
| `js/tools.js` | 网页版工具定义与执行分发（38 个工具）、危险命令前端判定、审批 |
| `js/chat.js` | 聊天渲染：消息卡片、工具卡片、审批卡、GuardBanner、快照对话框 |
| `js/panels.js` | 右侧面板/设置/模型注册表/资产银行/快照推送/安全中心/外部 API 清单/Git 分支实验面板 |
| `js/memory.js` | Agent 长期记忆（v8.22）：IndexedDB 四类记忆、本地打分零 API 成本召回 top-5、memory_save/search/list/delete 工具 |
| `js/sessions.js` | 多会话标签条（v8.16）：聊天面板顶部标签的打开/切换/关闭（会话数据在 IndexedDB，按 taskId 隔离） |
| `js/fs.js` | 文件系统访问（FSS 直连 + 桥模式，含前端路径保护） |
| `js/trace.js` | 工作轨迹视图（时间线/标记/区间/筛选/详情预览） |
| `js/devtools.js` | API 控制台：请求记录、curl 构造与打码 |
| `js/editor.js` | 编辑器（CodeMirror/Monaco 之外的轻量实现） |
| `js/tree.js` | 文件树渲染 |
| `js/icons.js` | 内联 SVG 图标系统 |
| `js/voice-pet.js` | 语音助手「小龙」2.0 客户端核心：浮层宠物 + 对话面板 + 双模输入 + 状态机 + 任务进度 |
| `js/voice.js` | 语音助手「小龙」STT/TTS/意图解析/双模式/数据持久化（增强版：多轮记忆 + 13 种意图） |

### 4.4 lite/（超轻量远控版，自包含）

| 路径 | 职责 |
|------|------|
| `lite/lite_main.py` | Lite 版入口（极简后端 + 零 CDN 单文件前端） |
| `lite/app/lite_server.py` | Lite 极简后端（鉴权 + LLM 代理 + 文件桥 + 命令桥） |
| `lite/app/codename.py` | Lite 大代号/相对路径映射（独立副本） |
| `lite/app/config.py` | Lite 服务端配置（独立副本，APP_DIR 指向项目根） |
| `lite/app/security.py` | Lite 安全模块（危险命令检测，独立副本） |
| `lite/app/storage.py` | Lite JSON 持久化（独立副本） |
| `lite/app/errors.py` | Lite 错误日志（独立副本，ERR_LOG 指向项目根） |
| `lite/app/auth.py` | Lite 身份验证（独立副本） |
| `lite/app/users.py` | Lite 用户存储（独立副本） |
| `lite/static/lite.html` | Lite 零 CDN 单文件前端 |

### 4.5 其他源码与共享入口

| 路径 | 职责 |
|------|------|
| `pyqt/main.py` / `pyqt/cli_main.py` | 桌面版与 CLI 入口（见第 3 节） |
| `webui/web_main.py` | 完整版网页版入口（见第 3 节） |
| `lite/lite_main.py` | Lite 版入口（见第 3 节） |
| `sync_server.py` | 同步服务器入口（快照存取/冷备续聊/漂移状态/远程指挥 + HTML 状态页，见第 3 节；v8.24 补建） |
| `tests/test_desktop_offscreen.py` | 桌面版离屏回归（模块导入/主窗口/多会话/终端环境池/安全中心） |
| `tests/test_smoke_servers.py` | 服务端真实冒烟（lite + webui + sync_server 起进程端到端） |
| `_audit_tmp.py` | 一键回归入口（gui/server/all 子命令顺序跑两套测试） |

> 历史注记：早期文档曾列出 `tests/test_smoke.py` 等四个旧测试文件与 `tools/svg_picker.py`，以上文件当前均不在本仓库中。
> `sync_server.py` 曾因跨机器工作区差异在本副本缺失（v8.19 按「文档加注记而非补文件」处理），v8.24 按用户指令补建并随仓库分发（裁决见 Fact.md）。

---

## 5. 数据文件清单

### 5.1 data/（本机持久化，均原子写入；API Key 仅本机）

| 文件/目录 | 内容 |
|------|------|
| `config.json` | 桌面全局配置（含敏感字段，仅本机；`to_public()` 掩码） |
| `server_config.json` | 网页版服务端配置（host/port/secret/allow_register/bridge_workspace 等） |
| `users.json` | 网页版用户账户（PBKDF2-SHA256 哈希，不含明文） |
| `desktop_history.json` | 桌面版主对话历史（含漂移合并的 from_server 消息） |
| `cli_history.json` | CLI 独立历史（与 GUI 分离） |
| `vault/assets.json` | 资产银行（含 kind=tool 逻辑资产） |
| `tools.json` | 自研工具注册表 |
| `tool_bugs.jsonl` | 工具医生 bug 记录 |
| `tool_fail_count.json` | 工具失败计数（工具医生用） |
| `models.json` | 模型注册表与打分 |
| `err_mirror.json` | 防呆数据库（报错特征） |
| `agent_memory.json` | Agent 长期记忆库（v8.22 memory.py，四类条目，上限 300 条） |
| `term_envs.json` | 终端环境池持久化（v8.18 terms.py，cwd+env 轻量会话） |
| `ctx_bans.json` | 守门专家永久禁引块 |
| `notepad.json` | Agent 跨轮暂存 |
| `theme_custom.json` | 用户自定义主题 |
| `workspaces/{hash}.json` | 工作区级设置（集中本机，不进工作区） |
| `checkpoints/` | 文件级版本快照（`.bak` + `.meta`，每文件 20 版/全局 600 版） |
| `sessions/` | 任务级会话快照（`meta.json` + `snapshot.zip` + `rollback/snap_N.json`） |
| `audit.jsonl` | 回退/删除/恢复/漂移回本地审计日志 |
| `ui_automation_journal.jsonl` | 外部 exe 自动化操作记录 |
| `drift_state.json` | 漂移状态机本地标记（不进工作区） |
| `history/` | 兼容目录（服务端启动时创建；当前网页版历史存浏览器 IndexedDB） |
| `voice_tasks.json` | 语音助手任务列表（id/title/status/detail/priority/tags/progress/createdAt/updatedAt） |
| `voice_constraints.json` | 语音助手限制条件列表（id/text/createdAt） |
| `voice_conversations.json` | 语音助手对话历史（id/role/content/ts/mood） |
| `voice_pet_config.json` | 语音助手浮层配置（name/size/visible/conv_mode/mood_state/builtin_sprite） |
| `integrity_salt.bin` | HKDF 熵源盐值（首次运行生成，32 字节） |
| `integrity_nonce.bin` | HKDF 熵源随机数（首次运行生成，16 字节） |

### 5.2 同步服务器工作目录（部署在用户自有服务器时）

默认目录 `./sync_data`（环境变量 `SYNC_DATA_DIR` 可改），节点注册表与命令队列/结果为进程内存态（不落盘）。

| 文件 | 内容 |
|------|------|
| `snapshots.json` | 主快照容器 `{data, cold, updated_at}`（保持客户端入参格式原样写回，加密或无口令取决于客户端配置） |
| `cold.json` | 无口令冷备副本（冷备 Agent 续聊用，恒为历史超集；/chat 续聊消息追加于此） |
| `drift_state.json` | 服务器端漂移状态（project_id/active/unacked_count 等） |

---

## 6. 设计决策

> 决策编号沿用既有编号（1–111），按主题重新分组；每一条都描述当前状态。

### 6.1 核心架构与运行模型

1. **UI 用普通 Python**：PyQt6 桌面应用为主形态，Agent（对话、工具编排、规划、资产、模式）全部在 Python 进程内运行；命令行与文件操作走 Python 原生能力，不依赖浏览器 File System Access 或 HTTP 桥。
2. **命令行操作已有文件**：命令工具用 `asyncio.create_subprocess_shell` 在本地进程直接执行，`cwd` 限定工作区，支持超时熔断、逐行流式回传，操作的是工作区内的真实文件。
3. **LLM 接入**：OpenAI 兼容 `/chat/completions`（tools + streaming，httpx），兼容 DeepSeek/Kimi/智谱/Ollama。API Key 仅存本机 `data/config.json`。用户已批准引入全局 AI Agent（记录于 `Fact.md`）。
4. **Agent 线程模型**：Agent 在后台线程的 asyncio loop 运行，事件经 `Queue` 泵到 UI 线程（QTimer 50ms 轮询）；审批门由 UI 弹对话框、经 `loop.call_soon_threadsafe` 唤醒。
5. **工作区保护**：文件路径 `resolve()` 强制解析到工作区内防越界；工作区=项目根时保护 `app/ desktop/ data/ backups/ dev_log/ updates/ static/` 与 `config.json`、`Err.log` 等系统目录/文件；AI 删除默认禁用（`ALLOW_AI_DELETE=False`）。`delete_file` 工具仅在 `ALLOW_AI_DELETE=True` 且非只读时暴露；系统保护目录永远禁删；删除工作区根一律拒绝。
6. **审批门**：`run_command` 默认需用户确认；副作用后置——cwd 目录创建等落盘动作移到审批通过之后；副驾驶代批异常时兜底升级用户审批。
7. **子Agent 委派**：独立上下文循环（全新 messages），只回传紧凑摘要；嵌套深度限 3 防递归。
8. **上下文压缩**：历史超阈值（默认 12000 token）时，一次独立 LLM 调用将旧消息压缩为 system 摘要，保留最近 N 条（默认 6）。
9. **AOE 并行规划**：规划器产出 DAG → 拓扑分层 → 同层 `asyncio.gather` 并行（每节点=独立子Agent），分支默认 20s 超时熔断 → LLM 汇总；规划前强制查资产银行做复用拦截。
10. **资产银行**：本地 JSON 存储，检索统一走 matcher 三级引擎（char/bm25/api），阈值默认 0.45；`search_vault`/`store_asset`/`inspect_asset`/`repair_assets` 工具形成「创建→入库→检索→检修」闭环。
11. **三大模式**：流量模式（精简输出）、肝完睡觉模式（目标达成 → 全屏倒计时 → 授权后关机/休眠）、Token 计费模式（减少调用路径+工具裁剪+激进压缩）。
12. **同步（用户自有服务器）**：快照（配置/会话/资产）Fernet 加密导出/导入；可选推送/拉取到自建 `sync_server.py`。
13. **（可选）服务端**：`app/` FastAPI 保留身份验证(HMAC Cookie)+无状态 LLM 代理+命令桥，供"服务端验证身份/多人/远程"场景；桌面版默认本地直连不依赖。
16. **展示方式选择**：AI 消息支持预览/源码切换（Markdown 渲染 vs 原始文本）；Mermaid/HTML 代码块以专属卡片呈现，预览模式为样式化源码框（本地无 JS 引擎，不做栅格化，安全且零依赖）。
17. **主题系统**：内置主题（全黑·曜石 / 全白·纸感 / 米色·羊皮 / 蓝调·深海 / 深空 Harness）+ 自定义主题；主题为语义色板 JSON，可导入/导出/在设置内逐色修改；全局 QSS 由色板生成。
18. **系统托盘**：应用常驻系统托盘；可选"关闭时最小化到托盘"。
19. **同步队列**：设置保存/会话落盘后进入待同步队列；流量模式下挂起不同步，关闭流量模式时立即冲刷。
20. **逐行流式补全**：补全流式输出，出一行展示一行灰字；文档无变化则不打断；用户键入补全首字符时消费该字符；补全模型可单独配置。
39. **配置开关三层贯通**：任何功能开关必须同时打通三层——`config.py` 字段定义 → `tools.build_tool_defs` 工具暴露 → `settings_dialog` 设置面板展示；缺一层即静默失效。
40. **线程退出与竞态红线**：临时 QThread 必须注册进模块级保活集合防 GC，退出路径有界 `wait(2500ms)`；Agent 线程用提交时快照跑；`run_final` 覆盖历史前必须把本会话已合并的漂移消息重新并回（去重键防翻倍）。
41. **密钥排除穷举**：快照/导出/对外暴露的密钥排除必须逐字段穷举（api_key/sync_password/sync_token/drift_api_key/search_api_key），`to_public()` 统一掩码。
42. **双入口并存架构**：桌面版与网页版作为独立入口并存，功能对等但代码独立；`main.py` 只启动 PyQt6 桌面应用，`web_main.py` 只启动 FastAPI + uvicorn 并自动打开浏览器。
43. **网页版架构边界**：服务端 `app/` 严格保持"三件事"——下发 UI、验证身份、无状态转发（LLM 代理 + 本地资源桥）；Agent 逻辑全部在浏览器端 JS 执行；API Key 只存在于浏览器 localStorage。
44. **SVG 图标系统**：全 UI 禁用 emoji。网页版用 `static/icons/` + `static/js/icons.js` 内联 SVG；桌面版用 `desktop/icons.py` 的 `QSvgRenderer` 渲染 SVG。
45. **相对路径安全（大代号机制）**：AI 视角下工作区文件只显示大代号（codename），AI 写文件/命令只能用相对路径，绝对路径由系统层翻译；代号映射存内存不落盘。
46. **网页版 Agent 修复点**：以当前 `static/js/` 为基础修复跑通；重点：AOE 节点超时后取消实际子任务、runSession 错误时回滚 user 消息、sleep 目标判定为关键词+用户确认双因子、运算符优先级显式加括号。
57. **超轻量网页远控（Lite 版）**：与完整版并存，极简（鉴权+LLM 代理+文件读写+命令执行）、低内存（SSE finally 清理、子进程超时即 kill）、零 CDN 依赖；默认端口 8733。
58. **远程指挥协同架构**：本地端 `desktop/remote_cmd.py::RemoteCmdClient` 后台长轮询，部署端 `sync_server.py` 的 `/cmd/*` 端点；命令类型 shell/read_file/write_file/ping。
59. **远程指挥内存安全**：部署端节点注册表 TTL 2 分钟、命令队列 maxlen=100、结果表 TTL 10 分钟，惰性 GC；本地端结果队列有界、httpx client 用完即关。
60. **SSE 资源清理加固**：命令桥 SSE 生成器 `try/finally` 确保子进程被 kill、管道被 close、`proc.wait()` 被调用；前端 `sseFetch` 的 reader 在 finally 中 cancel。
61. **Lite 版前端内存安全**：`AbortController` 管理可中止 fetch，finally 清理状态；DOM 随会话清空回收；sessionStorage 历史最多 50 条。
62. **四入口并存架构**：`main.py`（桌面）、`web_main.py`（完整网页）、`lite_main.py`（轻量远控）、`cli_main.py`（命令行）互不干扰，功能对等但资源消耗递减；`sync_server.py` 作为第四端（部署在用户自有服务器）同时服务同步与远程指挥。
63. **v8 面板管理**：面板统一收归右侧 Pannel dock；右缘图标条与 tab 一一映射，同页再点即收起；概览=折叠分区聚合（健康仪表盘嵌入 Progress）；模型弹窗为 Qt.Popup 状态载体。
64. **Embedding 三级匹配**：`matcher.py` 统一入口 rank/similarity；三级分数归一化 [0,1] 阈值语义一致；api 用注册表 kind=embedding 独立条目，失败静默降级不中断链路。
72. **CLI（cli_main.py）**：复用桌面 Agent 内核，无 GUI；会话内命令 `/mode /clear /history /quit`；历史独立存 `data/cli_history.json`；vault 损坏时降级 None 不崩启动；审批交互串行锁 + 取消=拒绝；异常/取消路径 finally 统一保存历史。
73. **网页版参考图精修与可用性闭环**：完整版网页端以参考图为视觉基线（浅色低对比三栏工作台、紧凑侧栏、居中 Quest 输入卡、右侧 Summary/Terminal/Files dock）；空会话只显示 Hero；右侧 dock 标签页同步维护选中态与内容页 active；小窗口分级收敛。
74. **本地 Python/PyQt 参考图对齐**：桌面入口采用左侧 Quest 导航、中央 Chat/Editor 工作区与默认可见右侧 Summary/Files/Terminal 面板的三栏结构；聊天区是默认主视图。
75. **品牌中性化**：参考图中的第三方产品名与专有类型名不进入用户界面；内部 DOM id、Python 类名和兼容函数名保留，仅作为实现细节。
76. **桌面版全局建议系统 + 系统状态优化**：右栏 Summary 新增 Suggestions 可折叠分区；采纳建议自动注入输入区并计数；状态栏右侧展示系统状态（就绪/运行中/只读/建议待处理）。
77. **网页版工具补齐**：对照桌面版补齐非专家/非 toolsmith 工具（search_tool/file_search/web_search/browser_open/read/screenshot/notepad_*/list_checkpoints），开关三层贯通。
78. **模型系统完整化**：模型自动打分/画像更新（judge_model + web_search，时间戳机制）；设置拆分为系统级与工作区级（`desktop/workspace_config.py`）；默认模型回退链；依赖树工具调用语义审查。
96. **「端口没编码」清零**：项目下载端口打包真实源码 ZIP（相对路径排除 data/backups/pyc/Err.log、符号链接/30MB 上限、HMAC 签名 15 分钟时效）；全项目无 stub/占位端口。
97. **「前端缺少对应端口」清零**：模型注册表补齐打分+榜单；快照对话框补齐轮内回退点三端口；`#model-select` 从注册表填充；`/checkpoint/restore` 相对 bak_path 修复后恢复功能真正可用。
98. **DevTools curl 可复现**：登录/注册响应回传 HMAC 会话令牌，`current_user` 支持 `Authorization: Bearer`（Cookie 失败回退）；API 控制台 curl 携带 Bearer 头；预览打码、复制取真实值。权衡：Bearer 令牌即会话本身（无状态 HMAC、随 session_ttl 过期），登出仅清 Cookie 与本地令牌、不吊销已签发令牌（记录于 `Fact.md`）。
102. **孤儿端口清零**：轮内回退点全生命周期接线；`/checkpoint/versions` 端口接线；`POST /models/auto_score` 对等；Lite 接线 server/info 与手动工作区授权；`/tree/expert_report`、`/tree/inject` 保留为桌面专家体系专用端口（网页版从 `/tree/scan` 重建注入文本，记录于 `Fact.md`）。
107. **多任务会话与前端死控件清零**：web 左栏 Tasks/Chats 为真会话列表（IndexedDB 按 taskId 隔离，旧 default 会话兼容）；mode-select 真实接线；附件引用、Knowledge 文档、Assets/Experts/Schedule/Marketplace/Editor 标签、New Task 全部真实可用；无实现的设置页/占位按钮按模块开关隐藏；Commands 面板可创建执行。

### 6.2 Agent 形态、上下文与提示词

14. **上下文守门专家**：每轮对话前后调用一次轻量模型（`expert_model`，可独立配置，默认用主模型）——pre-round 分块裁决保留/永久禁引（禁块写 `data/ctx_bans.json`）；post-round 规则守护（检查本轮是否合规，发守护卡片）；引用回退（每条消息的「引用」按钮把该块完整内容强制注入下一次调用）。
15. **三形态 Agent**：`agent_mode` ∈ chat / builder / experts。Chat=对话助手（只读工具优先，不主动改文件）；Builder=全能构建；Experts=专家团（桌面版由编排器接管；开关关闭时以多领域顾问口吻工作）。网页版当前未开放 experts 形态（入口隐藏，配置可设但 UI 不展示）。系统提示词按模式注入。
52. **AGENT.txt 安全规则注入**：把 AGENT.txt 的工作流核心提取为 `AGENT_SAFETY_RULES` 常量，在 builder/experts/专家实例的所有 system prompt 末尾追加；网页版同步注入精简版。
53. **@-mention 引用文件**：输入框检测 `@` 触发文件选择器，选中后插入为引用 chip；AI 收到的引用内容带文件相对路径与内容。
54. **命令面板 Ctrl+Shift+P**：全局快捷键弹命令面板，支持模糊匹配（跳转文件/切主题/切模式/切工作区/打开设置/清空会话）。
56. **Notepad 暂存工具**：`notepad_save/read/list/clear`，存 `data/notepad.json`，Agent 跨轮暂存中间结果。

### 6.3 安全与权限

47. **三体架构**：官方服务器（邮箱注册/账户/项目下载，不接触用户代码）、用户云服务（同步/云接管/网页端 Agent 远程操控，用户自有）、本地电脑（桌面版/网页版本地 Agent，API Key 仅存本机）。
48. **邮箱注册与账户管理**：邮箱+密码注册（PBKDF2-SHA256 哈希），官方服务器通过 POP3/SMTP（SSL 993/465）发送验证码；邮箱凭证存环境变量不落盘；登录限速、异常 IP 检测、验证码 10 分钟过期。
49. **项目下载功能**：`GET /api/projects` + `GET /api/projects/{id}/download`；打包真实源码 ZIP，需登录鉴权，链接带 HMAC 签名时效 15 分钟。
50. **数据安全实践**：传输加密、存储加密（AES-256-GCM 快照、PBKDF2-SHA256 200k）、零信任（每请求验证身份、路径强制 resolve）、最小权限（AI 只见代号/相对路径、命令审批门、AI 删除默认禁用）、数据最小化（官方服务器不存代码/会话）、审计日志、限速防护、密钥轮换、多租户隔离、备份策略、二次审批。
100. **命令桥后端危险命令拦截（danger_ok 协议）**：`app/security.is_dangerous_cmd` 与桌面 `DANGEROUS_PATTERNS` 同源，在 bridge/lite 的 run_command 强制生效——未带 `danger_ok:true` 的危险命令 403；前端全链路接线（web 审批卡通过后置 danger_ok、lite 本地预判+后端 403 二次确认重试、终端手动 confirm）。定位是「防误触/防裸调 API 误操作」，不是对已登录用户的安全边界（记录于 `Fact.md`）。
103. **确认协议严格布尔化**：所有「确认/开关」类字段（danger_ok、guard on、sessions begin enabled）统一严格布尔解析——bool 直接取，字符串仅 `1/true/yes/on` 为真；`"false"`/`"0"` 一律视为未确认/关闭。
104. **危险命令四端同源**：desktop/tools.py、app/security.py、static/js/tools.js、static/lite.html 各自维护同一套危险模式语义（`rd /s`、taskkill 顺序无关、`--force-with-lease` 排除、代码执行等价形式）；Python 侧统一 `re.DOTALL` 防换行拆词；远程指挥 `/cmd/dispatch` 的 shell 同样执行该协议。新增危险模式必须四端同步并加同组危险/安全样例断言。
105. **工作区根与系统目录双闸**：桌面 delete_file 与 web bridge `/fs/delete` 都拒绝删除工作区根；bridge/lite 补齐与 desktop 同源的系统目录保护（工作区=项目根时 app/desktop/data/backups/dev_log/updates/static 与 config.json/Err.log 禁止读/写/删/改名）；只读保护检查异常 fail-closed。
108. **sync_token 语义与密钥边界**：`sync_token` 是唯一远程鉴权令牌，remote_cmd 绝不把 `sync_password` 当令牌发送；设置导出/导入与快照排除同用六字段清单。
110. **网络面板对 Agent 只读开放**：API 控制台内存记录（DevTools.entries）新增 Agent 工具 `network_list` / `network_curl`；打码沿用 DevTools.mask/maskCurl——Bearer 令牌与 api_key/password/token 不出现在 Agent 上下文；用户仍可在面板手动复制真实 curl。
111. **只读保护与写保护分离**：为满足「Agent 可以看网页源码/JS」，bridge/lite 与桌面 read_file 只禁止读取 `data/`、`backups/`、`config.json`、`Err.log` 等密钥/运行数据；`static/`、`app/`、`desktop/` 等源码目录允许 Agent 读取。写/删/改名继续沿用完整系统目录保护。Cookie 维持 HttpOnly，JS/Agent 不可读；需要复现鉴权请求时使用 API 控制台 Bearer 会话令牌。

### 6.4 专家团、并发与文件所有权

21. **专家团四角色**：总司令（用户指定模型，唯一可写重要文档，负责总结/规划/发命令/核查汇总）、普通专家（Builder 内核，由总司令按任务选模型与参数唤醒）、高级专家（普通专家上报"不擅长/修不好"时由总司令规划后转入）、副驾驶（事件触发型监察：危险指令/危险代码片段/锁异常/总司令放开读写权限时才调取）。专家内部禁止委派复杂 Agent，仅允许基础只读/搜索子Agent。
22. **反馈汇总节奏**：命令发布后专家并行开工，空闲专家达到 ceil(2/5 × 本轮专家数)（比例可配）时将其反馈交总司令核查；总司令以表单回给用户（实现不了/需要提问/需求过模糊），含成本估测。
26. **租约锁与并发**：内存锁表：acquire 带 TTL 与 timeout，超时抛 `AcquireLockTimeout`；专家每完成一个待办项自动 heartbeat 续约；Reaper 为纯数据结构扫描（10s 定时器，零 token）；锁卡 2 分钟 UI 可强制解锁。文件所有权：专家唤醒时申报管理文件清单，清单外一律只读；可全局切串行。写文件强制 CoW（临时文件+原子 rename）。
27. **端口文档**：每工作区一份 `ports.md`；AI 需要/变更/使用端口时读写该文档；ports.md 写权归总司令调度。改端口禁止全局搜索+文本替换，正确流程 = port_read 查表 → `port_refs` 精确定位引用 → AI 逐处人工语义确认后再改。
28. **审批门四模式**：全审批 / 危险内容审批 / 帮我审批（副驾驶代批：恶意危险直接掐断并提示，非恶意放行）/ 全放行。专家动外部文件/危险命令需向总司令请示，总司令拿不准才转用户。
29. **Builder 工具裁剪**：每工具带匹配序列；仅 Token 计费模式下才按当前 todo 做轻量匹配裁剪，另保留基础工具与 search_tool 元工具；非计费模式不裁剪；低内存模式下纯子串匹配且强制串行。匹配器单轨——低内存纯子串、否则纯 bigram 余弦（或 api），不混用。
30. **专家私有待办**：专家用内存待办（`todo_update` 工具，事件上报 UI）；总司令才读写根目录 todo.md。
31. **专家卡片展示**：聊天区卡片（任务摘要|模型与负载|当前状态），单击开独立详情对话框（面包屑层级导航），悬停展示完整参数与状态；高级专家按展示级别过滤展示内容。
32. **重要文档保护**：重要文档 = Design.md/Techniques.md/Fact.md/Future.md/FreqErr.md/dev_log/updates（AGENT.txt 文档清单）；仅总司令可写，其余专家写这些路径在工具层直接拒绝。
33. **副驾驶生命周期**：副驾驶不是后台常驻进程，仅在 4 个事件钩子按需调用（危险指令/危险代码/锁异常/总司令放开写权），调完即退；Reaper 的 10s 定时器是零 token 纯数据结构扫描。
37. **专家团 DAG**：计划 JSON 增 deps 字段 → 拓扑分层执行；同层无依赖任务并行（串行模式退化为顺序），依赖任务结果直注下游专家 prompt；单任务失败不阻塞其余分支。
87. **文件分区规划并发执行加速**：把同一 DAG 层内的任务按申报文件（`files`，经 locks._norm 规范化）分组：无文件交集的任务并行，有交集的按组串行，文件为空的只读任务与任意任务并行；波内任务再经租约锁 `declare_files` 硬闸防漏。AOE 规划提示词要求每个节点声明 `files` 字段。

### 6.5 模型系统

23. **模型注册表**：`data/models.json`。每模型：展示名/URL/独立 Key/上下文大小/输出上限/能力/介绍/分数（Benchmark 复杂列表，定/未定）/输入输出价/计费系数/操作说明/作为高级专家时的上下文限制与展示级别。
24. **模型调取三法**：a=总司令按任务读模型介绍自选；b.1 性能优先（AI 搜 Benchmark 给分，总司令赋权加权排名）；b.2 性价比优先（得分÷价格，输入输出比默认 9:1，可自动调整）。"定"的模型定期复查；增删指标后手动/自动间隔重搜生效。
25. **助手模型泛化**：搜索/多模态/上下文压缩/摘要统一走助手模型，解决"部分模型商不支持搜索"的问题。
70. **Python 应用截图 + UI 自截图确认**：`app_screenshot` 工具运行脚本→EnumWindows 找窗口→QScreen.grabWindow→杀进程；`ui_review` 工具 base64 多模态调视觉模型审查。视觉专家模型选择遵循"默认不调、主模型不能用视觉时再调"。app_screenshot 走审批门，Base64 前限图 ≤3MB。
71. **网页版落地（web_main/app+static）**：桥接新增 `app_screenshot` 与 `fs/image`（魔数判定 PNG/JPEG，≤3MB）；前端新增 app_screenshot/ui_review 工具（开关裁剪）；llmChat 支持 model 参数覆盖 + 非流式 content 无条件累加；设置面板加视觉专家模型输入。

### 6.6 资产、自研工具与工具医生

34. **工具设计专家**：用户提"造工具/写脚本/做转换器"类需求 → 总司令派工具设计专家：构建前强制查重（search_vault + 查自研工具库 + 问 AI 是否重复劳动）；做完经审核（默认副驾驶判安全与重复，可配总司令），过了才入库（`data/tools.json` + 资产银行逻辑资产），不过则打回。
35. **主动复用入库**：任何 Agent（含专家）完成生成物后自动轻量评估，通过则入资产银行并强制撰写多维说明书；专家产物同样自动评估。长耗时构建（>3min）转后台，完成后代码本体+参数范例作为逻辑资产入库。
36. **防呆库增强**：自研工具/任何工具报错（异常或 ok=False）都记录特征到 `data/err_mirror.json`；下次生成代码前注入系统提示主动规避历史 Bug。
51. **工具医生（Tool Doctor）Agent**：`use_tool` 失败时自动追加 bug 记录到 `data/tool_bugs.jsonl`；工具 `list_tool_bugs` / `fix_tool_bugs` / `clear_tool_bug`；修复走"先读 bug→读原 impl→LLM 诊断→生成新 impl→审核→沙箱跑 example 回归→通过才覆盖入库"，失败不覆盖原版。开关 `ENABLE_TOOL_DOCTOR`。
84. **自动化工具与资料创建检修全链路**：工具设计专家与资产银行打通桌面/网页双端；资料检修：`inspect_asset`（完整性检查）、`repair_assets`（批量修复），与 search_vault/store_asset 形成闭环。

### 6.7 快照、依赖树与建议系统

55. **AI Checkpoint**：Agent 调用 write_file/edit_file 前，工具层把原文件备份到 `data/checkpoints/{task_id}/{rel_path}`；ChatPanel 右键菜单"恢复到 AI 改动前"列出最近 checkpoint 供回滚。开关 `ENABLE_CHECKPOINT`。
65. **版本快照回退**：checkpoint.py 统一快照存储（`.bak` + `.meta` 含 source）；source 语义 ai/human/model/restore；保留策略每文件 20 版 + 全局 600 版；恢复链路做路径越界校验 + 恢复后同步已打开编辑器 tab。
66. **任务级快照与只读报警（大架构）**：每轮对话生命周期=begin_round→（写工具前 tool_rollback 记录旧内容+add_tool_call 记工具调用）→commit_round（zip 打包工作区不含 backups/data/sessions/.git + 标题 + 完整输入 + todo + devlog + 依赖树）。报警三源（copilot kill / 依赖树校验失败 / 健康仪表盘 P0）统一进只读状态机→GuardBanner 展示供用户选择。轮内保留 `round_keep_rollback` 次回退点；任务完成 commit 后清除全部轮内快照。删除旧任务快照前由 AI 后台独立对话审查无 P0 才允许；总上限 `session_snapshot_max_mb` 超限淘汰最旧。
67. **依赖树（dep_tree.py）**：正则扫描 .py/.js/.ts import/from/require；节点 {deps, size, min_size}；大小下限自动=0.3×当前大小（容差防误报），expert_report 可覆盖并锁定；check_tree 校验被依赖文件缺失 + 大小低于下限；写/删/大减后增量更新。
68. **建议系统（suggest.py，TRAE CUE 式）**：时机=用户消息发送完成时；读 Design/Techniques/Fact/Future/FreqErr 生成功能性/技术性/美术性建议，filter_dedupe 按类均衡限量 6 条；桌面 + 网页端同步展示；Lite 排除。
69. **快照第二轮完整化**：快照展开显示完整输入+todo+devlog+tree 摘要；删除审查 P0 时向前遍历找更早快照、用快照自身树做自洽校验；任务管理器实时四层树；tree 校验问题按 rel 维度注入；专家按申报 files 过滤读取注入警告；owner_of "*" 通配最低优先级。
86. **完整安全工作树 + 回退审核机制**：`desktop/audit.py` 审计日志（`data/audit.jsonl`）：所有回退/恢复/删除/漂移回本地/快照清理动作统一记录 `{ts, actor, action, target, detail}`；`list_audits` 工具供 AI 自查；开关 `ENABLE_AUDIT_LOG`（关闭只停止追加、不删历史）。Err.log 记错误，audit 记回退动作，两者分离。

### 6.8 算力漂移与同步

38. **算力漂移**：本地关机/休眠/退出前将完整工作状态加密压缩增量推送用户服务器（同时附无口令冷备副本）；服务器冷备 Agent（sync_server /chat）基于冷备副本续聊；本地开机拉取回传，服务器端产生的对话按标记去重合并回本地历史。所有数据流经用户自有服务器，开发者不持有任何副本。
79. **算力漂移退出状态机**：`desktop/drift.py` 维护 `data/drift_state.json`——project_id（工作区路径 sha256 前 12 位）/ active / target_server / drifted_at。流程：begin_drift 本地留标记 → 服务器登记并复位未回传计数 → 推送含冷备副本的完整状态。服务器 `/chat` 全程 `_CHAT_LOCK`（读→LLM→写三段式防 lost update），每轮 `unacked_count+1`；文件写与 /push 互斥。本地开机查 `/drift/status`：unacked>0 → 锁定项目 + 弹「漂移回本地/保持锁定」；「漂移回本地」重新拉取合并，仅合并成功才复位解锁。加密主快照永不降级：`/chat` 只在主快照本就为无口令冷备格式时才回写；`/pull` 附带 `drift_cold` 冷备副本。
85. **算力自动上下漂移 + 云端 HTML 继续任务**：`ENABLE_AUTO_DRIFT` + `auto_drift_interval_min`（默认 30 分钟）工作期间定时推送；退出时默认自动漂移（`auto_drift_on_exit`）。服务器端 `/` 与 `/chat/page` 提供 HTML 版状态页：直接展示漂移状态、冷备历史消息流、续聊输入框，手机/平板/浏览器打开即可继续任务。
99. **同步链路鉴权贯通**：desktop/config 新增 `sync_token`（to_public 掩码、快照排除），push/pull/drift 五接口携带 `X-Sync-Token`；sync_server 未设 SYNC_TOKEN 时仅环回客户端可访问（反代 XFF 存在时强制要求令牌）、CORS 收紧为环回源；`/cmd/result` 有 TTL 清理与 1MB 上限。
106. **跨端快照互通**：网页版推送 `{"v":2,"kind":"web-cold","data":标准base64(UTF-8 JSON)}` 冷备信封；sync_server 双格式识别并保持入参格式写回；桌面导入识别网页 AES-GCM 信封与 web-cold；网页 pull 兼容桌面 zlib 冷备与无口令主快照。密钥排除六字段跨端穷举。

### 6.9 浏览器与外部程序自动化

80. **外部程序/浏览器自动化**：`desktop/win_automate.py` 提供 ctypes Win32 原语（零第三方依赖，非 Windows 安全降级）；`desktop/browser.py::browser_elements` 无头抽取可交互元素（限 300 条、单元素截断 500、有界流式读取、SSRF 校验复用）。工具层 8 个 `exe_*` + `browser_elements`；守卫 `_ui_automation_guard`：开关 → 硬规则（唯一 exe、文本限长）→ 副驾驶全程监督 → 审批模式路由；exe_close 只允许关闭由 exe_launch 启动过的 pid/窗口；click 坐标限屏幕内。
81. **四大特点边界修复**：① 分区并发——`declare_files` 返回冲突列表，`*` 通配同样报冲突；`_norm` 折叠 `a/../b` 与内部 `./`；过期锁自愈。② worktree 备份——`_safe_name` 拦截 `.`/`..`；被删目录整目录 zip 备份（相对路径存盘、不跟随符号链接、时间戳防碰撞），恢复时 zip-slip 双重校验。③ 同步队列 `flush(include_cold=True)` 冷备副本与主快照一致。④ 自动化设计/搜索/工具池审计确认真实实现。
82. **错误与网络健壮性**：LLM 网络层异常（httpx.HTTPError）统一转为友好中文 LLMError；同步三接口同样转友好文案；漂移状态写失败落根目录 Err.log；工具 call_id 统一加 Agent 实例前缀；`tools/svg_picker.py` 日志统一指向根目录 Err.log。
88. **浏览器 F12/curl + 直接操控浏览器 + 选定软件探索与记录**：网页版 API 控制台（devtools.js）记录每次请求并提供「复制为 curl」；桌面版 `browser_ctl.py` 最小 CDP 客户端（纯标准库 WebSocket + Chrome/Edge `--remote-debugging-port`，只绑 127.0.0.1）；`exe_*` 操作写入 `data/ui_automation_journal.jsonl`（`exe_journal` 工具回读）。
101. **健壮性批量修复**：fs/grep pattern≤200+30s 超时（bridge+lite 对齐）、/power action 白名单、fs/read 二进制 415、LLM 代理消息形状/数量/总长校验、SSRF 环回判定 any 语义、meta_bridge 全锁覆盖、501/500 错误回显泛化（详情落 Err.log）、Err.log 对外脱敏补盘符路径、审计日志 5MB 轮转、验证码超限改 LRU 驱逐、/api/users 邮箱打码。
112. **DashScope API 工具集**：`desktop/dashscope.py` 使用 httpx 直调 DashScope REST API（零 SDK 依赖），提供文生图/图生图/视频生成/任务查询/模型列表 5 个工具。API Key 存 `cfg.dashscope_api_key`，`to_public()` 掩码。prompt 限 2000 字符、n 限 4、同步超时 120s、异步轮询 3s*120 次。模型数据源：model-library/models.json。
113. **UI 元素检视与可靠交互**：`desktop/ui_inspect.py` 基于 ctypes Win32 API（EnumChildWindows + GetWindowTextW + GetClassNameW）实现控件级操作，替代脆弱的坐标点击。提供枚举/定位/点击/输入/读取/树形结构 7 个工具。写操作走 `_ui_automation_guard`（开关+硬规则+副驾驶+审批门）。典型工作流：exe_list_windows → ui_get_tree → ui_find_control → ui_control_click/ui_control_set_text。
109. **资源与竞态上限**：Err.log 5MB 轮转 + 对外只读末尾 1MB；auto_score 互斥标记先占位再 await；remote_cmd 线程防双实例、输出 2000 行有界、唯一临时文件；grep 拒绝典型嵌套量词、glob 30s 超时；`_STATE`/`_FILE_CODENAMES` 5000 上限；快照链路失败一律 note/Err.log 呈现；Diff 预览异常 fail-closed、超大 diff 改走审批门。

### 6.10 工作轨迹

83. **工作轨迹页面**：双端同步新增「工作轨迹」视图——顶部 Duration/Turns/Calls 时间线可视化，下方按 SYSTEM/CONTEXT/USER/ASSISTANT/TOOL 角色分类的消息流；支持搜索过滤与从当前会话历史重建。轨迹页只读展示运行历史。
89. **手动标记点 + 区间查询**：时间线支持单击新增标记点（黄色三角形，落到最近事件，联动滚动）；按住拖拽划选区间，区间内事件即时高亮并统计「区间内 Turns/Calls/Errors/Top 工具」；标记点与区间存内存不持久化。
90. **操作搜索 + 关键词增强**：顶部「操作」下拉按角色/事件类型多选；关键词支持空格分隔 AND 匹配 + 区分大小写开关；与时间线筛选同时满足。
91. **详情预览对话框（TracePreviewDialog）**：QDialog + QTextBrowser（零第三方依赖；HTML 渲染走 md_to_html，转义严格防注入）+ 工具条（缩放 80/100/120/150%、复制全部、引用到对话）；窗口可拖拽缩放，最大屏幕 95%；非模态可同时开多个对比；网页版 modal 同样实现。
92. **模块开关 `ENABLE_TRACE_ADVANCED`**：默认 True，关闭后隐藏全部高级入口回到基础轨迹形态。
93. **拖动移动 + 置顶悬浮**：桌面 `_DragFilter` 事件过滤器（客户区坐标 + 屏幕边界 clamp + Leave/Hide 复位防粘住）；「置顶」切换 `WindowStaysOnTopHint`。网页版 `_initModalDrag`（clamp + `e.buttons&1` 兜底释放丢失）；置顶 `.trace-modal-pinned`（z-index 9999 + mask `pointer-events:none` 透明，真悬浮）。
94. **元素选择引用**：详情按「角色/时间/类型/摘要/详情」五元素分块，每块带「引用」按钮逐元素引用；引用文本统一 `[标签] 内容`；与「引用选区」「引用全部」并存。
95. **缩放与健壮性双端对齐**：网页缩放档位统一 [80,100,120,150]；非法时间戳过滤、纯空白字段跳过、openPreview 空指针保护。

### 6.11 多会话标签

114. **多会话标签（产品形态）**：三版本均支持多会话并行——每个标签是一条独立命名、独立持久化的对话上下文；新建、切换、关闭三端齐备。能力梯度（v8.16.1 复检收敛措辞）：重命名——桌面（右键菜单）与 Lite（双击）支持，网页版标签即任务条目，管理走左栏列表；自动命名（首条用户消息摘要作标题）——桌面与 Lite 落地，网页版新建时强制 prompt 命名。开启功能时不破坏存量数据：旧的单会话历史首启自动迁入「默认会话」，用户无感升级。
115. **隔离边界**：会话只隔离「对话历史」与跟随历史的视图（聊天区回放、工作轨迹重建）；工作区授权、模型注册表、主题、工具集、其他面板状态均为全局共享，不做按会话副本。运行模型不变：同一时刻全局只有一个 AI 回合在执行；AI 回合进行中禁止切换会话（网页版 v8.13 已有该守卫，桌面版补齐同样提示），防止流式输出写入错误会话视图。
116. **桌面版形态**：ChatPanel 顶部横向标签条 + 尾部「新会话」按钮；右键菜单重命名/关闭（关闭最后一个标签=清空并保留空会话）；会话索引与会话历史分别落盘（索引单文件 + 每会话一个历史文件），沿用原子写与既有序列化约定；切换会话时以当前会话历史重建聊天区与轨迹面板。同步队列的 history 脏标记跟随切换后的活跃会话。
117. **网页版与 Lite 形态**：网页版完整版复用既有任务存储（localStorage 任务索引 + IndexedDB 分会话消息），增量加「聊天面板顶部横向标签条」——列出已打开的会话（打开集 ∩ 现存任务，空集仅显活跃；v8.16.1 复检收敛措辞：当前实现为首启只列活跃一个标签），点选即已有的 switchTask 流程，标签上的关闭仅移除打开态不删数据（当前版本主界面不提供任务删除入口，「删除仍在左栏管理入口」为后续增强方向）。Lite 在浏览器本地存储中把单一历史键改为分槽（每会话一槽）+ 索引槽，界面同样加标签条与新建/重命名/关闭。
118. **模块开关 `ENABLE_MULTI_SESSION`**：三版本各设同名开关（桌面 desktop/config.py 持久化字段、网页 core.js CFG_DEFAULT、Lite State.cfg），默认 True；关闭时隐藏全部标签入口并保持单会话行为与旧版一致（见 §7.1 / §7.3）。

### 6.12 安全中心（v8.17 轻档聚合）

<!-- 6.12 body unchanged below -->

119. **产品定位**：把散落各处的安全能力聚合成可视化安全中心——状态卡片（命令/文件/网络/身份）+ 审计日志流水（含导出/清空）+ 核心开关聚合；轻档原则——不改底层删除语义、不加黑白名单/前缀放行/配额管理，纯 UI 聚合。
120. **三版本形态**：桌面版设置对话框新增「Security」Tab（状态卡片 + 审计只读列表 + 导出/清空）；网页版设置面板新增安全中心分区（状态卡片 + 审计流水 + 导出/清空按钮）；Lite 新增只读审计日志查看区（顶部按钮触发，含清空）。审计数据源：webui/lite 的 `data/audit.jsonl`（`security.audit()` 追加写，P2-7 大小轮转），桌面版 `data/audit.jsonl`（同源 `audit.audit_log()` 接口）。
121. **后端最小改动**：security.py 新增 `read_audit(tail)` 与 `clear_audit()` 两个纯函数（twin 同步）；webui/server.py 新增 `GET /api/security/audit` 与 `POST /api/security/audit/clear`；lite/lite_server.py 同步新增（孪生端点）；桌面版直接调用 `audit.list_audits()` 读本地文件。无新模块开关——安全中心始终可见（只读审计 + 强制开启的能力展示）。
### 6.13 终端环境池 + 悬浮小助手 Agent 循环 + 系统专家（v8.18）

122. **终端环境池（持久 cwd+env 轻量会话）**：命令行执行新增三可选参数——`env`（持久环境 id/name，继承 cwd 与环境变量）、`update_interval`（静默期到点心跳，K210 刷写场景）、`kill_after`（超时/停止后是否杀进程树）。未显式指定时按默认值执行并在结果中返回 `[WARN] 使用默认值：...`（AI 可见，下次可显式传参）。环境=轻量上下文（cwd+env），每条命令继承后新起进程（Windows 友好、无孤儿 shell）；持久化 `data/term_envs.json`（原子写）。后端 `/api/bridge/term/create|list|delete` + `run_command` 参数扩展；前端新增 `term_create/term_list/term_delete` 工具。桌面端独立 `desktop/terms.py` + `tool_run_command/_exec_command` 扩展。

123. **悬浮小助手 Agent 循环（一次 LLM 调用自判模式）**：语音助手「小龙」升级为 Agent 循环入口——一次 LLM 调用输出 `{mode: chat|agent_loop|system_expert}` 头部标记：`chat`=单轮跑腿（直接答）；`agent_loop`=暂停主循环（软暂停·工具边界安全停靠）→ 小助手独立 `runPetAgent` 循环（独立 abort 控制器 / 独立 messages / 不污染主历史）→ 自动恢复；`system_expert`=只读查阅 Design/Techniques/Fact 回答系统问题。主循环软暂停原语（`agentPauseMain/agentResumeMain/agentWaitIfPaused`）+ 工具循环两处停靠点。

124. **用户/Agent 共享系统专家**：系统专家是 experts 体系的一个角色，拥有系统知识（Design/Techniques/Fact 关键决策自动检索）+ 可读源码上下文，用户（悬浮小助手入口）和 Agent（`runPetAgent` 的 system_expert 模式）均可调用，大幅节省主循环上下文。

### 6.14 Agent 记忆、出关审核、外部 API 与同步服务器落地（v8.22/v8.24）

125. **Agent 长期记忆双端**：桌面 `desktop/memory.py`（文件式 `data/agent_memory.json`，四类条目 fault/lesson/preference/knowledge、scope global/local、bigram 相似度检索注入 system prompt、memory_read/memory_record 工具、漂移合体按 id+相似度去重，上限 300 条）；网页 `static/js/memory.js`（IndexedDB 存储四类记忆，本地打分零 API 成本，每轮自动召回 top-5 相关记忆注入，memory_save/search/list/delete 四工具，踩坑修复后沉淀 lesson）。开关：桌面 `ENABLE_AGENT_MEMORY`、网页 `ENABLE_MEMORY`。
126. **出关审核（outbound gate）**：AI 向外传递最终内容（交付物/对外说明/邮件正文/发布文本）禁止直接写在聊天回复里出关，必须先调 `outbound_deliver` 工具——逐字复述用户原始要求（requirement）+ 给出完整内容（content），经聊天审核卡片批准后才出关；目标为 email 的内容仅存草稿，发送由用户在安全中心手动触发（AI 不能直发邮件）。开关 `ENABLE_OUTBOUND_GATE`。
127. **外部 API 清单治理**：安全中心清单编辑器维护外部 API（名称/URL/Key/用途，密钥仅存浏览器本地），AI 经 `api_request` 工具调用（首次调用弹用户授权卡）；实际请求统一经服务端 `POST /api/llm/ext_proxy` 转发，带 SSRF 防护（环回/内网地址拒绝，`llm_allow_loopback` 可配）与 base_url 校验。
128. **安全中心 heartbeat 泄露检查**：`POST /api/security/leak_scan` 一键检查 8 类本地工作区密钥泄露（OpenAI/AWS/GitHub/私钥等）+ 外部 API 清单厂商存活心跳；发现问题且配置 SMTP（服务端环境变量 `SMTP_HOST/SMTP_USER/SMTP_PASS`）且填了收件邮箱时可发送报告邮件；`GET /api/security/smtp_status` 探测发件配置状态。
129. **同步服务器落地（v8.24）**：`sync_server.py` 补建随仓库分发——`/push`/`/pull` 双格式原样写回（决策106）；`/chat` 冷备 Agent 续聊（`_CHAT_LOCK` 读→LLM→写三段式防 lost update、每轮 `unacked_count+1`、与 `/push` 文件写互斥、加密主快照永不降级只在无口令格式时回写）；`/drift/begin|status|finish` 状态机；`/cmd/register|poll|heartbeat|result|dispatch|nodes|results` 远程指挥（节点 TTL 2 分钟、命令队列 maxlen=100、结果 TTL 10 分钟且体限 1MB，惰性 GC）；HTML `/`（漂移状态 + 冷备会话续聊 + 远程指挥控制台，危险命令勾选 danger_ok）与 `/chat/page`（手机/平板续聊页），零 CDN 无 emoji。鉴权同决策 99：`SYNC_TOKEN`/`X-Sync-Token`（HTML 页可用 `?token=`）、未设令牌仅环回、XFF 存在强制令牌、CORS 收紧环回源、请求体按端点分级限 64MB/1MB/2MB。
130. **Git 分支实验（v8.21 引入，v8.24 更名）**：git worktree 多工作树能力——bridge.py 四端点（list 只读；create/switch/remove 需 `danger_ok` 严格审批，路径限定 `<workspace>/.worktrees/<name>`，`_safe_name` 拦 `.`/`..`）+ 网页「分支实验」面板 + worktree_list/worktree_switch 工具 + 系统提示词感知。更名原因：本项目术语 **WorkTree = 独立安全备份审核**（特点3，checkpoint/session_snap/dep_tree/audit 三层防线），与 git worktree 不是同一概念，避免混淆（内部标识符/端点路径保留 worktree 字样，仅 UI 文案与提示词更名）。

### 6.15 用户文件保护、工作副本与编辑器统一（v8.25/v8.26）

131. **用户文件保护（v8.25）**：`file_protect.py`——USER_SUFFIXES（PPT/Excel/Word/PDF/设计源文件/视频/压缩包等）视为用户手工资产：AI 文本工具（write/edit/delete）一律拒绝、run_command 触碰即拦截；`data/file_protect.json` 记录 {rel: {size, mtime, actor}}，AI 写后记录 actor=ai，scan 发现与记录不符即判「人类在外部改过」并要求用户一键备份+授权；一键全量备份 `backups/<ts>_full.zip`（不含 backups/.git/缓存）；重名/命名不清治理（detect_ambiguous 归一化词干分组 + UNCLEAR_PATTERNS，quarantine 移入 `backups/quarantine/<ts>/`）。四端接线：桌面 tools/checkpoint（二进制快照 save_checkpoint_bytes）、webui bridge/snap_bridge、lite_server；网页版前端 tools.js 同源 USER_ASSET_SUFFIXES。开关：ENABLE_USER_FILE_PROTECT / ENABLE_FULL_BACKUP / ENABLE_AMBIGUOUS_GUARD。
132. **禁碰语义裁决（v8.26，用户原话「AI禁止碰不是不能碰，而是拷贝，原内容不修改」）**：对用户资产从「硬拦截」升级为「拦截+引导拷贝」——新工具 `copy_user_asset`（桌面 tools.py + webui `/api/bridge/fs/workcopy` + tools.js，经审批门）把原文件按「时间-作者-内容」规则拷贝为 `workcopy/` 下工作副本（如 `20260905-AI-报告.pptx`），原文件永不修改；workcopy/ 目录内的 AI 创建副本豁免用户资产保护，AI 可自由读写处理；阻断文案统一引导 AI 先申请工作副本。开关：桌面 `ENABLE_WORK_COPY`（config/build_tool_defs/settings 三层），网页端点后端强制 `enable_work_copy`（server_config，前端 CFG_DEFAULT 同步）。阶段3 复检加固：`_is_workcopy` 拒绝 `..` 穿越与盘符（防 `workcopy/../` 前缀绕过豁免）；`make_workcopy` 拒绝 data/backups/sessions 等敏感目录与 config.json/Err.log/api.txt/api_keys.py/.env 敏感名（防密钥/运行数据经工作副本外带）；webui 审批 dangerous=true 与桌面对齐。Lite/DSH 插件不接（用户裁决）。
133. **编辑器两端统一 WorkTree 保护（v8.26）**：桌面编辑器保存前快照（source=human）为既有行为；网页版编辑器补齐同语义（saveActiveTab 保存前 POST /checkpoint/save source=human，不设 dirty 条件——外部改盘而标签未 dirty 时同样兜底，失败 toast 提示不阻塞保存）；两端编辑器统一「版本历史」入口（网页编辑器标签栏「历史」按钮复用 chat.js openVersionDialog 的 /checkpoint/versions；桌面编辑器右键菜单「版本历史…」接 VersionRestoreDialog）；网页版 fs/write 行尾保持对齐桌面（写前嗅探原文件 CRLF 风格，save_text 增加 crlf 参数），消除 CRLF 字节漂移跨端差异。
134. **新建文件命名规则（v8.26，用户裁决）**：AI 创建新文件时优先沿用工作区已有命名惯例（先看同目录与资料）；无惯例时用「时间-作者-内容」格式（YYYYMMDD-作者-描述；用户交付物作者=用户，AI 中间产物作者=AI）。规则注入 AGENT_SAFETY_RULES / AGENT_SAFETY_RULES_WEB。

### 6.16 真·算力漂移：服务器端续算（v8.27）

135. **密钥加密随漂移上服务器（v8.27，用户裁决「密钥同样传上服务器但必须加密；MQTT 不采用」）**：api_key/api_base_url/model/search_api_key/dashscope_api_key/drift_api_key 六字段打包为 AES-GCM 信封（复用 sync.py PBKDF2(sync_password, 每安装盐) 派生密钥，v:2 格式），作为不透明串 `keys_enc` 随 /push 上传用户自有服务器；服务器仅存密文、永不解密落盘，服务器端普通端续算时凭用户输入的 sync_password 解密到**内存**（临时供 LLM 客户端使用，进程退出即失）。sync_token/sync_password 自身永不上传。安全红线「API Key 仅本机」修订为：**本机明文（data/config.json）；自有服务器仅密文信封；解密只发生在续算进程内存**。未设 sync_password 时不加密钥，服务器端改用既有 `drift_api_key/drift_api_base` 直连字段。
136. **不看守模式（v8.27，用户裁决「不使用提问工具、审批工具，能做的做完，没做完的保留进度」）**：`agent_unattended`（CLI `--unattended` / 服务器续算强制开启）——审批门改自动策略：非危险动作自动放行，危险动作不执行并记入保留进度台账；提示词注入「不看守纪律」（禁用提问、自行判断、完成能完成的、结尾给出未完成清单）。台账持久化为工作区内 `unattended_progress.json`（随快照往返，服务器续算接得上、回本机也接得上）。
137. **会话迁移与服务器落点（v8.27，用户裁决「在合适位置断掉会话上传，然后继续会话；同步端把工作区放到 APPDATA；WorkTree 等 AI 不可乱动的东西同步到对应位置」）**：断点沿用既有轮边界（退出漂移 / 周期自动推送），上传会话历史 + 工作区 + 数据子集；服务器端续算 = 在服务器上以普通端（CLI `--workspace <工作区>`）继续同一会话。同步端素材化落点：`%LOCALAPPDATA%\DeverAI\drift\<项目号>\workspace\`（可用 `SYNC_WS_DIR` 环境变量覆盖），WorkTree/记忆/工作区设置/保护状态等放对应 `data/` 位（遵守「work tree/设置/记忆绝不放工作区内」既有约束）。回传沿用 /pull 合并去重。
138. **退出上传模式（v8.27，用户裁决「退出时选一次并以后不再询问」）**：`drift_upload_mode`——`minimal`（最简：WorkTree 即时变更 + 相关数据文件增量上传）/ `full`（完整：全工作区 + data 子集 checkpoints/audit/记忆/设置）。漂移退出对话框首次询问并记住选择（写入 config），此后不再询问；设置页可改。

### 6.17 备份范围、引用交付与 Agent+ 预设（v8.28）

139. **备份范围控制（v8.28，用户裁决「C 盘爆红 → 允许备份每个文件的上两个版本」）**：`checkpoint_keep_per_file`（默认 2，可配 1-50）——checkpoint 每文件保留版本数由固定 20 改为可配，`_gc` 按配置裁剪；session_snap 总上限与删除前 AI 独立审查、Copilot 拦截危险操作等 WorkTree 安全语义不变。
140. **换机环境提醒（v8.28）**：漂移续算状态（`drift.is_drifting()`）下自动注入【换机环境提醒】——依赖/路径/服务/外部程序可能需重配，动手先验证环境，做不了如实说明保留进度；本地与服务器续算端同源生效（服务器 data 位含 drift_state.json）。
141. **变更栏 + 引用卡片（v8.28，网页端）**：Agent 循环采集本轮文件类工具改动（write/edit/delete/mkdir/rename/copy_user_asset），`run_done` 事件携带 `changes` 渲染「变更栏」卡片——HTML 文件给「预览」（iframe sandbox 独立渲染）与「源码」（默认折叠、懒加载、可复制）两种引用；文本/代码给「源码+复制」；其余「仅文件」不展开。多图引用 = 微信式扑克牌叠放（悬停距离驱动排斥位移、点击 lightbox 放大），单图缩略图+放大；图片走 `/fs/image` 端点。
142. **云端交付纪律（v8.28）**：客户端配置了 `sync_server_url`（自动上云漂移）时，系统提示注入——用户端可能无法直接预览工作区文件，任务结束必须在变更栏用引用形式交付产出，不要只报文件路径。
143. **Agent+ 模式预设（v8.28）**：`AGENT_PRESETS`（unattended=无人值守——禁提问、非危险自动放行、把能干的活先干完；daily=日常值守）+ `apply_agent_preset`（dataclasses.replace 不污染原对象）+ CLI `--preset`；不看守模式下 diff 预览自动放行（不阻塞）。

### 6.18 全局协调协议：跨工作区 Agent 闸口（v8.33）

144. **全局协调协议（v8.33，用户裁决「加一个全局闸口：检查每个并发 Agent 在做什么；冲突就让两个 Agent 互通信息；信息人类也要能看到，在损害发生前直接阻止」）**：工作区内并发已有租约锁/文件分区调度，但**跨工作区的外部资源**（典型：两个不同工作区的 Agent 同时 SSH 同一服务器，一个重启、一个上传中掉线）无任何协调。新增 `coordination.py`：
- **注册表** `data/coordination.json`（本机所有 DeverAI 进程共享 DATA_DIR）：agents（workspace/pid/status/task/resources/next_action/last_seen）+ inbox（逐 Agent 收件箱）；心跳 TTL（轮开始/工具调用节流刷新，超时 180s 自动回收，GUI 关闭自然过期；v8.34：轮开始的纯心跳也按节流窗口落盘、回收结果回写注册表——此前两者只改内存，看板看不到"未声明资源"的 Agent，且 coordination.json 只增不减）。
- **冲突检测**：两个存活 Agent 声明同一归一化资源键（如 `ssh:host`；run_command 自动嗅探 ssh/scp/rsync/sshpass/plink 主机，AI 也可 `coordination_declare` 显式声明任务/资源/下一步）→ 双向投递协议消息（你在干什么/我要干什么/下一步是什么）。
- **AI 协议工具**：`coordination_board`（只读看板）/ `coordination_declare`（声明并即时返回冲突+收件箱）；轮开始把收件箱+冲突快照注入 system prompt（协调顺序：等待/错峰/经用户确认）。
- **人类看板**：桌面「协调看板」**非模态**对话框（菜单栏「工作区 → 全局协调看板…」+ 命令面板 Ctrl+Shift+P 双入口；表格 + 5s 自动刷新 + 冲突行高亮 + Agent 列 tooltip 显示完整工作区路径；关闭即销毁、单实例复用。v8.34 由模态 exec 改非模态 show——监控面板不能挡住本窗口的「停止」按钮）+ webui 设置「安全中心 → 全局协调看板」（v8.34 补：此前网页端只有 AI 工具能读，人类看不到，与"信息人类也要能看到"的裁决不符）+ 只读端点 `GET /api/bridge/coordination`。叫停方式=按工作区路径切到对应窗口点「停止」（看板本身不跨进程发指令）。
- 开关 `ENABLE_COORDINATION`（默认开，三层贯通）。

---


---

## 7. 模块开关与阈值

### 7.1 桌面版（desktop/config.py，本地持久化 data/config.json）

| 字段 | 默认 | 说明 |
|------|------|------|
| `api_base_url` / `api_key` / `model` | `https://api.openai.com/v1` / 空 / `gpt-4o-mini` | LLM API 配置 |
| `temperature` / `max_tokens` | 0.3 / 4096 | 生成参数 |
| `workspace` | 项目根 | 工作区路径 |
| `ENABLE_VAULT` | True | 资产银行 |
| `ENABLE_AOE` | True | AOE 规划器/并行调度 |
| `ENABLE_SUBAGENT` | True | 子Agent（压缩/委派） |
| `ENABLE_SYNC` | True | 快照导出导入/远端同步 |
| `ENABLE_MODES` | True | 三大模式 |
| `ENABLE_ERR_MIRROR` | True | 防呆数据库 |
| `ENABLE_AGENT_MEMORY` | True | Agent 长期记忆库（memory.py：教训沉淀+检索注入+漂移合体） |
| `ENABLE_APPROVAL` | True | 命令审批门 |
| `ALLOW_AI_DELETE` | False | 允许 AI 删除文件（危险；开启后 delete_file 才暴露给 LLM） |
| `ENABLE_CTX_EXPERT` | True | 上下文守门专家 |
| `ENABLE_TRAY` | True | 系统托盘 |
| `ENABLE_MULTI_SESSION` | True | 多会话标签（v8.16，聊天区顶部 QTabBar；关闭回单会话） |
| `minimize_to_tray` | False | 关闭时最小化到托盘 |
| `ENABLE_STREAM_COMPLETE` | True | 逐行流式自动补全 |
| `ENABLE_EXPERTS` | True | 专家团 |
| `ENABLE_LOCKS` | True | 租约锁/文件所有权 |
| `ENABLE_WEB_SEARCH` | True | 互联网搜索工具 |
| `approval_mode` | danger | 审批模式：all/danger/copilot/free |
| `expert_collect_ratio` | 0.4 | 反馈回收比例（2/5，上取整） |
| `experts_default_serial` | False | 专家默认串行 |
| `low_memory_mode` | False | 低内存模式（串行/少渲染/不 embedding） |
| `copilot_model` / `helper_model` / `judge_model` / `commander_model` | 空 | 副驾驶/助手/打分/总司令模型（空=主模型） |
| `model_pick_mode` | a | 模型调取三法 a / b.1 / b.2 |
| `score_metrics` | 六项核心指标 | 模型打分指标（可增删） |
| `score_refresh_hours` | 168 | 自动重搜间隔（小时，0=关闭） |
| `cost_ratio_in_out` | 9.0 | 性价比折算输入:输出比 |
| `search_api_key` / `search_api_url` | 空 | 付费搜索 API（Tavily/Serper 兼容） |
| `ENABLE_TOOLSMITH` | True | 自研工具库/工具设计专家 |
| `tool_review_mode` | copilot | 工具审核者：copilot/commander |
| `ENABLE_DRIFT` | True | 算力漂移：退出前推送+开机回传 |
| `drift_api_base` / `drift_api_key` | 空 | 冷备 Agent 直连 LLM 预留配置 |
| `ENABLE_AUTO_DRIFT` | True | 自动算力漂移（周期推送） |
| `auto_drift_interval_min` | 30 | 自动漂移周期（分钟，0=关闭周期推送） |
| `auto_drift_on_exit` | True | 退出时默认自动漂移（False=弹三选一问询） |
| `ENABLE_TOOL_DOCTOR` | True | 工具医生 |
| `ENABLE_CHECKPOINT` | True | AI 改动前文件级快照 |
| `ENABLE_NOTEPAD` | True | Agent 跨轮暂存 |
| `ENABLE_COMMAND_PALETTE` | True | Ctrl+Shift+P 命令面板 |
| `ENABLE_FILE_MENTION` | True | @-mention 引用文件 |
| `ENABLE_EMBEDDING_API` | True | 外部 Embedding API 开关 |
| `embedding_level` | char | 三级匹配级别：char/bm25/api |
| `embedding_model` | 空 | 注册表 kind=embedding 的模型 id |
| `ENABLE_SESSION_SNAP` | True | 任务级会话快照 |
| `session_snapshot_max_mb` | 3072 | 快照总上限 MB（默认 3G） |
| `round_keep_rollback` | 2 | 轮内保留工具调用前回退次数 |
| `tree_update_at_round_end` | True | 依赖树&完整快照在回合结束时更新 |
| `ai_decision_delay_s` | 0 | 默认问答延迟（秒） |
| `ENABLE_DEP_TREE` | True | 依赖树自动扫描+校验 |
| `ENABLE_SUGGEST` | True | 建议系统 |
| `suggest_models` | [] | 建议生成模型（空=主模型） |
| `ENABLE_SAFETY_RULES` | True | AGENT.txt 安全规则注入 system prompt |
| `ENABLE_CASE_FEEDBACK` | True | 用例反哺（连续失败2次→副驾驶生成修复钩子） |
| `ENABLE_DIFF_PREVIEW` | True | 内联差异预览（write/edit 前弹 diff） |
| `ENABLE_REMOTE_CMD` | False | 远程指挥客户端（后台长轮询） |
| `ENABLE_BROWSER` | True | 浏览器控制（无头） |
| `ENABLE_APP_SHOT` | True | Python 应用截图工具 |
| `ENABLE_UI_REVIEW` | True | 截图视觉审查工具 |
| `visual_expert_model` | 空 | 视觉审查专家模型（空=自动选注册表第一个 image 模型） |
| `ENABLE_UI_AUTOMATION` | False | 外部 exe/浏览器自动化（开启后 AI 可操作配置的唯一 exe） |
| `ui_automation_exe` | 空 | 唯一允许 AI 启动的外部程序路径 |
| `ENABLE_EXE_JOURNAL` | True | 外部 exe 自动化操作记录 |
| `ENABLE_BROWSER_CTL` | True | 直接操控浏览器（CDP） |
| `ENABLE_BROWSER_DEVTOOLS` | True | F12 开发者工具（CDP Networks/Storage/Console/Sources） |
| `ENABLE_AUDIT_LOG` | True | 回退审核日志 |
| `ENABLE_FILE_PARTITION` | True | 文件分区规划并发调度 |
| `ENABLE_USER_FILE_PROTECT` | True | 用户文件保护（v8.25：PPT/Excel/Word/PDF 等用户资产 AI 禁写禁命令） |
| `ENABLE_FULL_BACKUP` | True | 一键备份完整工作区（backups/&lt;ts&gt;_full.zip） |
| `ENABLE_AMBIGUOUS_GUARD` | True | 重名/命名不清治理（识别/备份/转移） |
| `ENABLE_WORK_COPY` | True | 工作副本（v8.26：copy_user_asset 拷贝用户资产到 workcopy/，原文件不动） |
| `checkpoint_keep_per_file` | 2 | v8.28: 每文件保留 checkpoint 版本数（上两版默认，C 盘友好，可配 1-50） |
| `agent_preset` / `AGENT_PRESETS` | 空 | v8.28: Agent+ 模式预设（unattended/daily，CLI --preset 应用） |
| `ENABLE_COORDINATION` | True | 全局协调协议（v8.33：跨工作区 Agent 闸口+互通+协调看板，防互相踩外部资源） |
| `ENABLE_UNATTENDED` | False | 不看守模式（v8.27：禁提问；非危险自动放行，危险跳过记 unattended_progress.json 台账） |
| `drift_upload_mode` | 空 | 漂移上传模式（v8.27：空=漂移退出时询问一次；minimal=WorkTree 即时变更增量 / full=全工作区，记住后不再问） |
| `ENABLE_TRACE_ADVANCED` | True | 工作轨迹高级能力 |
| `ENABLE_VOICE_ASSISTANT` | False | 语音助手「小龙」2.0（浮层宠物化身 + 多轮对话 + 双模输入 + 进度查询） |
| `ENABLE_INTEGRITY` | True | DeveraiIntegrityService 完整性校验（HKDF + HMAC-SHA256） |
| `dashscope_api_key` | 空 | DashScope API Key（通义万相图像/视频生成） |
| `agent_mode` | builder | 形态：chat/builder/experts |
| `expert_model` | 空 | 守门/守护轻量模型（空=主模型） |
| `complete_model` | 空 | 补全专用模型 |
| `compress_threshold_tokens` | 12000 | 压缩触发 token 阈值 |
| `aoe_timeout_s` | 20 | AOE 分支熔断秒数 |
| `vault_threshold` | 0.45 | 资产复用阈值 |
| `context_keep_recent` | 6 | 压缩后保留最近条数 |
| `traffic_mode` / `token_mode` | False | 流量模式 / Token 计费模式 |
| `sleep_enabled` / `sleep_goal` / `sleep_action` / `sleep_authorized` | False/空/shutdown/False | 肝完睡觉模式 |
| `sync_server_url` / `sync_password` / `sync_token` | 空 | 同步服务器地址/快照口令/接口令牌 |
| `theme` | obsidian | 主题：obsidian/paper/sand/midnight/harness/custom |
| `win_min_w` / `win_min_h` / `win_max_w` / `win_max_h` | 900/560/0/0 | 窗口尺寸限制 |

### 7.2 网页版服务端（app/config.py，data/server_config.json）

| 字段 | 默认 | 说明 |
|------|------|------|
| `host` / `port` | 127.0.0.1 / 8732 | 服务端配置默认（实际端口由启动参数注入） |
| `DEVERAI_DATA_DIR`（环境变量） | 未设置 | v8.16：数据目录隔离（冒烟测试/多实例），仅进程级，不落盘 |
| `secret` | 自动生成 | 会话签名密钥（首次启动生成） |
| `session_ttl_hours` | 72 | 会话有效期 |
| `allow_register` | True | 是否开放注册 |
| `bridge_workspace` | 空 | 本地命令桥工作区（设置中授权） |
| `allow_ai_delete` | False | 命令桥是否允许删除文件 |
| `llm_allow_loopback` | True | LLM 代理是否允许环回地址（本地 LLM；生产多租户设 False） |
| `power_authorized` | False | 电源操作是否已授权 |
| `enable_work_copy` | True | v8.26：工作副本端点后端开关（/api/bridge/fs/workcopy） |

### 7.3 网页版前端（static/js/core.js，localStorage）

| 字段 | 默认 | 说明 |
|------|------|------|
| `base_url` / `api_key` / `model` | OpenAI 默认 / 空 / 空 | LLM 配置（仅浏览器 localStorage） |
| `theme` | auto | 外观：auto/light/dark |
| `ENABLE_VAULT/AOE/SUBAGENT/SYNC/MODES/ERR_MIRROR/APPROVAL` | True | 与桌面同义 |
| `ALLOW_AI_DELETE` | False | 允许 AI 删除 |
| `approval_mode` | danger | 审批四模式 |
| `embedding_level` / `ENABLE_EMBEDDING_API` / `embedding_model` | char / True / 空 | 三级匹配 |
| `low_memory_mode` | False | 低内存模式 |
| `ENABLE_MULTI_SESSION` | True | 多会话标签（v8.16；关闭隐藏聊天区顶部标签条） |
| `ENABLE_SUGGEST` | True | 建议系统 |
| `ENABLE_MEMORY` | True | Agent 长期记忆（memory.js，IndexedDB 四类记忆 + 自动召回） |
| `ENABLE_OUTBOUND_GATE` | True | 出关审核（outbound_deliver：复述要求 + 审核卡批准后出关） |
| `ENABLE_APP_SHOT` / `ENABLE_UI_REVIEW` / `visual_expert_model` | True / True / 空 | 应用截图与视觉审查 |
| `ENABLE_CHECKPOINT/SESSION_SNAP/DEP_TREE/CTX_EXPERT` | True | 可靠性四件套 |
| `ENABLE_WEB_SEARCH` / `ENABLE_BROWSER` / `ENABLE_NOTEPAD` | True | 搜索/浏览器/暂存 |
| `ENABLE_TOOLSMITH` / `ENABLE_TOOL_DOCTOR` | True | 自研工具/工具医生 |
| `ENABLE_AUTO_DRIFT` / `auto_drift_interval_min` / `auto_drift_on_exit` | True / 30 / True | 自动漂移（前端仅保存配置；实际推送由桌面端 `gui.py` 周期任务执行） |
| `ENABLE_AUDIT_LOG` / `ENABLE_FILE_PARTITION` / `ENABLE_BROWSER_CTL` / `ENABLE_EXE_JOURNAL` | True | 审计/分区/CDP/自动化记录（前端保存配置；实际执行在桌面端，网页版工具 defs 无对应工具） |
| `ENABLE_USER_FILE_PROTECT` / `ENABLE_WORK_COPY` | True | 用户资产禁写（前端同源后缀表）+ 工作副本工具（v8.26，经 /api/bridge/fs/workcopy） |
| `ENABLE_TRACE_ADVANCED` | True | 轨迹高级能力 |
| `ENABLE_VOICE_ASSISTANT` | False | 语音助手「小龙」2.0 |
| `builtin_sprite` | blob | 浮层形象：blob/cat/robot/axolotl |
| `compress_threshold_tokens` / `aoe_timeout_s` / `vault_threshold` / `context_keep_recent` | 12000/20/0.45/6 | 阈值 |
| `traffic_mode` / `sleep_*` / `token_mode` | False | 三大模式 |
| `agent_mode` | builder | 形态（experts 网页版未开放，入口隐藏） |

> 注：网页版 `CFG_DEFAULT` 是桌面版配置的**子集**，且设置面板 General 页模块开关区未列出全部字段（`ENABLE_ERR_MIRROR`、`ENABLE_EMBEDDING_API`、`low_memory_mode` 在 Advanced 页或未展示）；桌面版独占字段（专家团、同步、自动化白名单等）见 §7.1。

---

## 8. 安全红线

- API Key 仅存本机 `data/config.json`；快照/导出不含明文密钥（`to_public()` 统一掩码，排除六字段：api_key/sync_password/sync_token/drift_api_key/search_api_key/dashscope_api_key）。
- 命令/文件仅限工作区内（`resolve()` 防 `../` 越界）；工作区=项目根时保护系统目录与 `config.json`、`Err.log`；删除工作区根一律拒绝。
- AI 删除文件默认禁止；命令执行默认需审批；关机/休眠需显式授权。
- 用户资产（PPT/Excel/Word/PDF 等）AI 禁改原件：拒绝写/删/命令触碰，并引导经审批用 copy_user_asset 生成 workcopy/ 工作副本后处理副本（v8.26 语义：禁碰=拷贝出去改，原内容永不修改）。
- 错误统一写根目录 `Err.log`（自动存错机制，5MB 轮转 + 对外只读末尾 1MB）；防呆库自动记录工具失败镜像。
- 聊天区 HTML 一律转义渲染；Mermaid/HTML 预览仅样式化源码呈现，不执行脚本。
- QThread 保活与有界等待：临时 QThread 注册模块级集合防 GC，退出路径 `wait()` 有上限（2.5s）。
- 外部 exe 自动化：开关默认关 + 硬规则只允许配置的唯一 exe + 副驾驶全程监督 + 操作记录 journal；exe_close 只能关闭由 exe_launch 启动过的进程/窗口；click 坐标限屏幕内。
- 主快照加密不降级：服务器 `/chat` 只在主快照本就为无口令冷备格式时才回写；加密主快照保持不动，云端续聊消息经 `/pull` 的 `drift_cold` 冷备副本回传。
- 副作用后置：一切落盘/建目录副作用必须在审批通过之后；审批异常一律升级用户，不静默放行。
- 历史覆盖竞态防护：`run_final` 覆盖历史存盘前重并本会话已拉取的漂移消息，去重键保证不丢不翻倍。
- 无头浏览器/网页抓取默认拒绝内网/环回/链路本地地址（SSRF 数值 IP 先归一化再判定、DNS 任一命中即拒绝）；LLM 代理参数白名单/体限/工具形状校验。
- 危险命令四端同源 + `danger_ok` 严格布尔解析；命令桥后端对未经确认的危险命令直接 403。
- 网页版 `--host 0.0.0.0` 会暴露本地资源桥，仅限可信网络并打印警告；sync_server 未设 `SYNC_TOKEN` 时仅环回可访问，非环回部署必须设置令牌；`/api/users` 邮箱打码、错误回显脱敏。
- AI 只看到工作区文件的大代号（codename），写文件/命令只能用相对路径，绝对路径由系统层翻译；代号映射存内存不落盘。
- Cookie 维持 HttpOnly（JS/Agent 不可读）；curl 复现鉴权使用 API 控制台 Bearer 会话令牌（Agent 可见内容全部打码）。
- 只读保护检查异常 fail-closed：保护模块故障时拒绝执行，不 fail-open。

---

## 9. 文档体系与开发流程

- **文档单套制**：所有项目文档（README/Design/Techniques/Fact/Future/FreqErr/Err.log/todo/done 等）只允许存在于项目根目录一份；`backups/` 内同名文件是历史备份，仅供恢复参考。
- **README.md**：首要入口文档，保存用户运行系统所需的一切（简介、环境、安装、启动、使用、目录结构、常见问题）。
- **Design.md**：本文档，只描述项目当前最新状态；只允许追加/修改/删除章节，不写版本化口吻；版本演进统一写 `dev_log/`。
- **Techniques.md**：技术方案文档，记录实现方法、关键技巧、技术选型（按版本倒序组织）。
- **Fact.md**：用户偏好约束与冲突裁决记录；**Future.md**：未来需求记录；**FreqErr.md**：常见错误类型与正确做法。
- **Err.log**：运行时错误日志，每次修复前先读取，修复完成后清空内容（非删除文件）。
- **todo.md / done.md**：本轮任务清单；todo 最终为空，done 归档到 `updates/`。
- **dev_log/**：版本更新文档（按日期命名），记录每个版本干了什么、造了什么、改了什么、修了什么。
- **backups/**：阶段性备份（时间命名，只读参考）；**updates/**：归档的 done.md。
- **禁止占位文件**：文件要么不创建，要么一次写到位；编辑任何文件前必须读取核对内容与版本。
- **模块开关机制**：项目内功能用 `ENABLE_XXX` 开关控制，关闭时隐藏所有入口和接口调用。
- **错误记录与自动存错**：错误不得被吞没，RE 必须捕获并记录到根目录 `Err.log`。
- **全量检修纪律**：全量检修必须覆盖文档单套制、全部入口与全部源码、全部测试与冒烟脚本；每个修复点先读 `Err.log` 与 `FreqErr.md`，修复后清空 `Err.log` 并复跑验证；整轮以「故障检测子AGENT 全局复检」作为最后一个操作收口，未清零的问题不得停机结束。
