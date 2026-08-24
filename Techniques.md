# DeverAI — 技术方案文档

> 本文件按「版本倒序」组织：最新轮次在最上方，历史实现技巧与基础技术选型在下方。用户运行指引见 `README.md`；当前最新状态设计见 `Design.md`；版本演进归档见 `dev_log/`。

## 目录（章节导航）

- [全项目检查与深度加强（全量级别，2026-08-22）](#全项目检查与深度加强全量级别2026-08-22)
- [DSH 插件版 — Cordis 动态插件套壳（2026-08-22）](#dsh-插件版--cordis-动态插件套壳2026-08-22)
- [DashScope API 工具 + UI 元素检视（2026-08-21）](#dashscope-api-工具--ui-元素检视2026-08-21)
- [全量检修（2026-08-20）](#全量检修2026-08-20)
- [语音助手「小龙」2.0 — 浮层伙伴 + 多轮对话 + 双模输入](#语音助手小龙20--浮层伙伴--多轮对话--双模输入2026-08-18)
- [2026-08-16 文档与源码全量盘点整理（本轮）](#2026-08-16-文档与源码全量盘点整理本轮)
- [v8.13.1 AGENT 可见开发者网络 / 网页源码 / JS](#v8131-agent-可见开发者网络--网页源码--js2026-08-16)
- [v8.13.0 全量端口检修（第二轮）](#v8130-全量端口检修第二轮2026-08-16)
- [v8.12.0 全量端口检修](#v8120-全量端口检修2026-08-16)
- [v8.11.0 轨迹详情预览窗口增强](#v8110-轨迹详情预览窗口增强拖动--置顶悬浮--元素选择引用2026-08-15)
- [v8.10.0 工作轨迹深度增强 + 详情预览对话框](#v8100-工作轨迹深度增强--详情预览对话框2026-08-15)
- [v8.9.0 五大核心能力深度优化](#v890-五大核心能力深度优化2026-08-16)
- [v8.8.0 工作轨迹页面 + 双版本 UI 交互体验优化](#v880-工作轨迹页面--双版本-ui-交互体验优化2026-08-15)
- [v8.7.0 算力漂移退出状态机 + 外部程序/浏览器自动化 + 四大特点边界修复](#v870-算力漂移退出状态机--外部程序浏览器自动化--四大特点边界修复2026-08-15)
- [v8.6.0 前端主题升级：双主题工作台](#v860-前端主题升级双主题工作台参考-3080-deepseek-harness2026-08-15)
- [v8.5.7 网页版工具补齐](#v857-网页版工具补齐搜索--浏览器--暂存--元工具2026-08-15)
- [v8.5.6 Python 桌面版全局建议系统 + 状态优化](#v856-python-桌面版全局建议系统--状态优化2026-08-15)
- [v8.5.3 前端精修重构](#v853-前端精修重构2026-08-13)
- [全量完整项目查修（4 子代理并行审查）](#全量完整项目查修2026-08-134-子代理并行审查--逐项修复)
- [v8.5 网页版对齐批次2](#v85-网页版对齐批次2模型注册表三级匹配审批四模式资产升级建议去重工具裁剪2026-08-13)
- [v8.5 网页版对齐批次1](#v85-网页版对齐批次1快照只读报警依赖树上下文守门2026-08-13)
- [v8.4 Python 应用截图 + UI 自截图确认](#v84-python-应用截图--ui-自截图确认2026-08-13)
- [v8.3 任务级快照与只读报警大架构](#v83-任务级快照与只读报警大架构2026-08-12)
- [v8.2 版本快照回退](#v82-版本快照回退2026-08-12)
- [v8.1 Embedding 级别匹配](#v81-embedding-级别匹配2026-08-12)
- [技术选型](#技术选型)
- [关键实现技巧](#关键实现技巧)
- [v4 新增关键实现技巧](#v4-新增关键实现技巧)
- [v5 新增关键实现技巧](#v5-新增关键实现技巧)
- [v6 新增关键实现技巧](#v6-新增关键实现技巧)
- [UI 布局（PyQt5）](#ui-布局pyqt5)
- [v6.2 / v6.3 / v8 新增关键实现技巧](#v62-新增关键实现技巧双入口--svg--相对路径安全)

---

## 全项目检查与深度加强（全量级别，2026-08-22）

### 需求背景

用户要求「全项目检查并深度加强，全量级别，最后一次必须是检查，除非问题已经可以忽略或者没有任何问题否则不要停」。本轮对全部入口（main/web_main/lite_main/cli_main/sync_server）、`desktop/`、`app/`、`static/`、`tests/`、`tools/` 及全部项目文档做全量检查与深度加强。

### 检修方法

1. **文档单套制与前置检查**：枚举全项目文档，确认根目录一份；读取 README/Design/Fact/FreqErr/Future/Err.log；将用户核心诉求写入 Design 章节与本文档。
2. **静态全量扫描**：Python 全文件内存 `compile()`（不落盘 pyc）+ Node `node --check` 全量 JS 语法检查 + 危险模式/吞异常/死代码等定向 grep。
3. **测试全量回归**：`tests/test_smoke.py`、`tests/test_lite.py`、`tests/test_desktop.py`、`tests/test_gui.py` 四套全跑，另运行既有 `_smoke_*.py` 冒烟脚本。
4. **多线并行子AGENT深度审查**：后端（app + sync_server）、桌面核心（agent/tools/experts/planner/llm/config/storage/errors/codename/modes/locks/partition/background/vault/models/matcher/search_tools/toolsmith/auto_score）、桌面可靠性与 UI（checkpoint/session_snap/dep_tree/suggest/audit/drift/sync/sync_queue/remote_cmd/browser/browser_ctl/win_automate/app_shot/tool_journal/deverai_integrity/dashscope/ui_inspect/gui/panels/quest_panels/settings_dialog/themes/icons/md/highlight/ide_extras/trace_preview/notepad/workspace_config）、前端（static 全量）、入口与测试（五入口 + tests + tools）。
5. **分级修复与复检**：按 P0（崩溃/安全/数据丢失）→ P1（功能错误/静默失效）→ P2（健壮性/资源）→ P3（一致性/文档）修复；每项修复后重跑受影响测试与静态扫描。
6. **最终收口**：以「故障检测子AGENT 全局复检」作为最后一个操作，未清零问题不结束。
7. **归档备份**：done 归档 updates/、dev_log 累加本轮章节、整项目备份。

### 关键检查维度

- 与 `FreqErr.md` 全部已知错误类型逐条对照（审批门全覆盖、SSRF 数值 IP 归一化、danger_ok 严格布尔、fail-closed、QThread 保活、SSE finally、互斥先占位再 await、路径防越界、读保护全端口一致、四端危险模式同源、模块开关三层贯通、非流式 content 无条件累加等）。
- 跨模块 import 名称与 def 完全一致；新增/修改后立即 `compile()` + 实际调用。
- 测试真实退出码（禁止假绿）；GUI offscreen 实例化回归。
- 文档一致性：README/Design/Techniques/Fact/FreqErr 与源码实际状态同步。

### 实际发现与修复摘要（多线子AGENT 审查 + 人工复查）

P0：
- `dashscope_api_key` 漏出快照/设置导出/网页快照/DevTools 打码：六处清单统一补齐（sync.py、settings_dialog.py、panels.js、devtools.js），测试加断言。
- `app/snap_bridge.py` checkpoint 保存/恢复可外带 `data/config.json`；rollback_point/restore 可覆写系统保护文件：补同源 `_is_read_protected/_is_protected` 校验，ck_files/versions 过滤受保护路径。

P1：
- `desktop/dashscope.py` `_MODES_JSON` 笔误 → 模型列表恒失败，已修并补冒烟断言。
- `desktop/locks.py` 混用 `time.time()`/`time.monotonic()` 导致 try_acquire 误放行，统一 monotonic。
- `desktop/ui_inspect.py` `ctypes.cast(c_wchar_p, LPARAM)` 在 64 位必 TypeError，改 `c_void_p.value`。
- `desktop/gui.py` 任务回退按钮缺 2 个参数静默失效，补齐确认 + 恢复后刷新编辑器。
- `desktop/session_snap.py` `delete_session("..")` 可删整个 SESSION_DIR，`_round_dir` 增加 id 净化校验。
- `desktop/browser.py` DNS 多结果 `_is_loopback` 用 all 可被公网+环回混合解析绕过，改 any。
- `app/proxy.py`/`lite_server.py` 将环回解析误判为私有（localhost 被拒），any 分支排除 loopback，并补 100.64/10 等 `not is_global` 拦截。
- 语音助手浮层：DOMContentLoaded 早于 boot 导致永不初始化 + `String.strip` 不存在的 DashScope task_status 必挂 + NO EMOJI ❤ 违规，全部修复。
- 网页 `ALLOW_AI_DELETE`/语音/完整性/电源授权开关只写 localStorage 后端不生效：新增 `/allow_ai_delete`、`/feature_flags`、`/power_authorized` 桥端点并接线。
- 后端写/命令端口不联动 Guard 只读态（fail-open）：run_command/fs/write/mkdir/delete/rename/app_screenshot 统一 423 拦截。
- 无 UI 审批 future 600s 悬挂、审批 future 在 `_emit` 阶段被取消不清理、系统提示注入绝对路径、UI/DashScope 工具在 chat 形态泄露，均已修复。
- 工具医生覆盖自研工具无审批：补审批门。

P2/P3（节选）：
- 数值参数统一 `_safe_int/_safe_float/_strict_bool/_as_str_list`；read_file 先 stat 再读；list_dir 500 上限；命令输出单行/总字节上限且 cwd 回显相对化；delete 大目录丢线程；embedding 检索丢线程；auto_score 互斥先占位；CLI 历史/漂移状态/app.storage/sync_server 固定 .tmp 全改唯一临时名；checkpoint 只读态不 GC；commit 清理目录 zip；restore 先全量校验再落盘；remote_cmd 读写套保护 + stop 即杀命令；voice_pet_host NaN/limit；lite 危险正则四端对齐 + history 校验；mascot innerHTML XSS 改 DOM 属性；HealthDashboard HTML 转义；QSS letter-spacing 移除；SVG 重着色排除 white 并兼容单引号；Err.log 桌面/网页轮转对齐；入口/README/Design 文档一致化。

---

## DSH 插件版 — Cordis 动态插件套壳（2026-08-22）

### 需求背景

用户要求将 DeverAI 的核心能力移植到 DeepSeek Harness (DSH) Web GUI，使 DSH 成为一个接近 DeverAI 体验的 AI 开发工作台。实现方式为 Cordis 动态插件（Host + Client 双面插件）。

### 技术选型

| 方面 | 选择 | 原因 |
|------|------|------|
| 插件框架 | Cordis 动态插件（`cordis_define` + `cordis_run`） | DSH 原生扩展机制，Host+Client 双面 |
| Host 运行时 | Node.js 沙箱（`ctx.get('webServer')` / `ctx.get('fs')` / `ctx.get('shell')`） | 直接操作文件系统、注册 HTTP 路由 |
| Client 运行时 | React + `slots.inject`/`slots.register` | DSH Web GUI 原生 UI 扩展 |
| Host→Client 通信 | `harness.handle` + `host.call` | 包私有 JSON RPC，Client 不能用 fetch |
| 定时器 | `ctx.interval`（timer 服务） | Client 沙箱禁止 `setInterval` |
| 持久化 | `$DEVERAI_DIR/dsh-plugin-storage/*.json` | 文件形式持久化，进程重启保留 |
| UI 插槽 | `shell.overlay` + `settings.section` | 浮层状态面板 + 设置页面 |

### 关键实现约束

1. **沙箱限制**：Host 沙箱不暴露 `logger`、`process`、`fetch` 等全局；只暴露注入的服务（`webServer`、`fs`、`shell`、`timer`）+ `ctx.tools.register` / `ctx.on` / `ctx.provide`
2. **Client 沙箱限制**：Client 禁止 `fetch`、`setInterval`/`setTimeout`、`window`/`document` 直接访问；网络请求必须通过 `host.call` 走 Host `harness.handle`；定时器必须用 `ctx.interval`
3. **生命周期管理**：所有路由注册、RPC 处理器必须用 `ctx.effect()` 包裹，确保插件停止/更新时自动清理

### 五大模块实现

#### 1. 文件树 + 版本快照
- Host：`fs.resolve()` + `fs.listDir()` + `fs.readText()` + `fs.writeText()` 实现目录浏览、文件读取、checkpoint 创建/恢复
- Client：设置页显示文件树根目录条目
- API：`GET /deverai/fs/tree?path=.`、`GET /deverai/fs/read?path=<rel>`、`POST /deverai/fs/checkpoint`、`GET /deverai/fs/checkpoints`、`POST /deverai/fs/restore`

#### 2. 资产银行
- Host：JSON 文件存储（`assets.json`），支持按名称/描述/标签搜索
- API：list/search/store/inspect/repair

#### 3. 算力漂移 + 同步
- Host：JSON 文件存储漂移状态（`drift_state.json`）
- Client：浮层显示漂移状态（Active/Idle 指示灯）
- API：status/push/pull

#### 4. 自动化工具池
- Host：JSON 文件存储自定义工具（`tools.json`）
- API：list/create/bugs/fix

#### 5. 专家团 + 并发调度
- Host：JSON 文件存储专家团状态（`experts_state.json`）
- Client：浮层显示专家团状态（Running/Idle 指示灯）
- API：status/start/stop

### 错误修复记录

| 错误 | 原因 | 修复 |
|------|------|------|
| `sandbox ctx does not expose "logger"` | Host 沙箱不暴露 logger | 删除 `const logger = ctx.logger` 及所有 logger 调用 |
| `fetch is not available in a dynamic client half` | Client 沙箱禁止 fetch | 改用 `host.call(method, args)` 调用 Host `harness.handle` |
| `setInterval is not available in a dynamic client half` | Client 沙箱禁止定时器全局 | 注入 `timer` 服务，改用 `ctx.interval(fn, delay)` |

---

## DashScope API 工具 + UI 元素检视（2026-08-21）

### 需求背景
用户要求基于 Documents/model-library 中收录的 DashScope（通义万相）API 文档创建 DeverAI 工具，并检查项目对具体计算机软件的操控学习能力，不足则补充。

### 实现内容

#### 1. DashScope API 工具集（desktop/dashscope.py + tools.py 注册）
- **设计原则**：轻量化，直接使用 httpx 调用 DashScope REST API，不引入 dashscope SDK（避免新增依赖）
- **API Key 管理**：新增 `cfg.dashscope_api_key` 字段，`to_public()` 统一掩码
- **5 个工具**：
  - `dashscope_image_generate`：文生图（同步），支持 prompt/model/size/n/style/negative_prompt
  - `dashscope_image_edit`：图生图（同步），输入图片 URL + 编辑描述
  - `dashscope_video_generate`：文生视频/图生视频（异步，自动轮询），image_url 非空走 i2v
  - `dashscope_task_status`：查询异步任务状态
  - `dashscope_list_models`：读 model-library/models.json 列出可用模型
- **安全**：prompt 限 2000 字符、negative_prompt 限 1000、n 限 4、网络异常转友好中文
- **模型数据来源**：model-library/docs/api-reference.md + model-library/models.json

#### 2. UI 元素检视与可靠交互（desktop/ui_inspect.py + tools.py 注册）
- **解决的问题**：现有 exe_click 纯坐标点击脆弱（分辨率变化/DPI/窗口移动即失效），学习软件需要控件级可靠交互
- **设计原则**：ctypes Win32 API（零依赖），EnumChildWindows + GetWindowTextW + GetClassNameW 实现控件枚举与定位
- **7 个工具**：
  - `ui_enum_controls`：枚举子控件层级结构
  - `ui_find_control`：按文本/类名定位控件（返回 hwnd）
  - `ui_control_click`：点击控件中心（自动置前父窗口）
  - `ui_control_set_text`：WM_SETTEXT 输入（适用于 Edit/输入框）
  - `ui_control_get_text`：读取控件文本
  - `ui_get_tree`：完整窗口树（JSON，max_depth 限制 10）
  - `ui_get_control_info`：单个控件详细信息
- **典型工作流**：exe_list_windows 获取顶层 hwnd → ui_get_tree 分析界面 → ui_find_control 定位目标控件 → ui_control_click/ui_control_set_text 操作
- **安全**：写操作（click/set_text）走 _ui_automation_guard（开关+硬规则+副驾驶+审批门）

#### 3. 配置文件更新
- `config.py`：新增 `dashscope_api_key: str = ""`，`to_public()` 添加掩码

#### 3. 网页版 DashScope 工具（app/bridge.py + static/js/tools.js）
- **服务端代理模式**：API Key 仅存 `data/config.json`，浏览器端无法获取；所有 DashScope API 调用经 `/api/bridge/dashscope/*` 端点代理
- **5 个代理端点**：image_generate / image_edit / video_generate / task_status / list_models
- **前端工具定义**：`static/js/tools.js` 新增 5 个工具定义 + 5 个 `execDashscope*` 处理器
- **处理器模式**：`_dashscopeProxy()` 统一封装，POST 调用桥接端点；task_status / list_models 走 GET

### 关键检查维度
- 所有新工具在 TOOL_HANDLERS、TOOL_MATCH、build_tool_defs 三处同步注册
- 网页版工具在 bridge.py 和 tools.js 两处同步注册
- 新工具在 Design.md 模块地图中记录
- API Key 字段在 to_public() 中掩码，源码中无硬编码 Key
- 网络超时设置合理（同步 120s，异步轮询 3s*120=6min）
- 输入参数限长防 LLM 注入
- Win32 回调引用保存到局部变量防 GC
- 递归枚举带 max_depth 防栈溢出

---

## 全量检修（2026-08-20）

### 需求背景
用户再次要求「全量检修」——对项目进行全面检查，包括文档单套制合规、源码健壮性、测试通过率、文档一致性，修复所有发现的问题，并完成归档备份。

### 检修方法
1. **文档单套制检查**：枚举全项目所有文档文件（README.md、Design.md、Techniques.md、Fact.md、Future.md、FreqErr.md、Err.log、todo.md、done.md、voice_assistant.md、AGENT.txt），确认仅根目录一份，backups/ 内的为只读备份。
2. **全量源码并行审查**：派出 6 路子 AGENT 并行审查：
   - 后端：app/*.py（16 个）+ sync_server.py（1 个）= 17 个文件
   - 桌面核心：agent.py、tools.py、experts.py、planner.py、context.py、ctx_expert.py、llm.py、ai_complete.py、config.py、storage.py、errors.py、err_mirror.py、codename.py、modes.py、locks.py = 15 个文件
   - 桌面可靠性：checkpoint.py、session_snap.py、dep_tree.py、suggest.py、audit.py、drift.py、sync.py、sync_queue.py、remote_cmd.py、browser.py、browser_ctl.py、win_automate.py、app_shot.py、tool_journal.py、deverai_integrity.py = 15 个文件
   - 桌面 UI：gui.py、panels.py、quest_panels.py、settings_dialog.py、themes.py、icons.py、md.py、highlight.py、ide_extras.py、trace_preview.py、notepad.py、workspace_config.py、_v83_patch_panels.py、partition.py、background.py、vault.py、models.py、matcher.py、search_tools.py、toolsmith.py、auto_score.py = 21 个文件
   - 前端：static/index.html、static/lite.html、static/css/style.css、static/js/*.js（12 个）= 15 个文件
   - 入口 + 测试 + 剩余：main.py、web_main.py、lite_main.py、cli_main.py、tests/*.py（4 个）、tools/svg_picker.py、_desktop_ui_inventory.md = 10 个文件
3. **测试验证**：运行全部 4 个测试 + 全部冒烟脚本。
4. **文档一致性核对**：交叉核对 README/Design/Techniques/Fact/FreqErr 与源码实际状态。
5. **问题修复与复检**：按 P0-P3 分级修复，多轮刁难复检。
6. **归档备份**：done.md 归档 updates/、写入 dev_log/、执行备份。

### 关键检查维度
- 导入正确性：所有跨模块 import 名称在被导入模块中存在
- 线程安全：QThread 保活集合、有界 wait、无跨线程 GUI 访问
- 安全：SSRF 归一化、danger_ok 严格布尔、路径遍历防护、审批门全覆盖
- 资源清理：subprocess kill、HTTP client close、SSE reader cancel、DOM 回收
- 竞态：锁顺序、先占位再 await、互斥标记原子性
- 模块开关：三层贯通（config → build_tool_defs → settings_dialog）
- NO EMOJI：所有 UI 文本零 emoji
- XSS：动态内容走 textContent，innerHTML 仅静态文案

---

## 语音助手「小龙」2.0 — 浮层伙伴 + 多轮对话 + 双模输入（2026-08-18）

### 需求背景
用户要求基于 dsh-plugin-pet（MIT License, https://github.com/c-ling/dsh-plugin-pet）核心架构优化「小龙」语音助手设计，实现：随时对话询问进度、插入消息、插入新任务等能力。

### 方法
1. **架构借鉴**：分析 dsh-plugin-pet 的双面插件架构（宿主端 HTTP 路由 + 客户端 React 浮层），将其核心概念（浮层宠物、状态驱动心情、配置持久化、精灵图动画）移植到 DeverAI 的 FastAPI + 原生 JS 技术栈。
2. **适配改造**：将 React 组件改为原生 JS 实现（DeverAI 不使用 React），将 Cordis 插件注册改为 FastAPI APIRouter，将 `$DSH_HOME/storages/` 改为 `data/` 目录。
3. **能力扩展**：在原有 STT/TTS/意图解析基础上，新增多轮对话记忆、13 种意图、任务 CRUD、进度查询、消息注入、文本双模输入。

### 关键技术点
- **浮层状态机**：idle → listening → thinking → responding → talking → idle，与 Agent 状态联动
- **多轮记忆**：`_contextMemory` 数组保存最近 20 轮对话，反馈模式注入 LLM 上下文
- **双模输入**：语音（Web Speech API STT）+ 文本（输入框 fallback），STT 不可用时自动降级
- **任务增强**：新增 progress（0-100）、priority（low/mid/high）、tags 字段
- **进度查询**：`/api/bridge/voice-pet/progress` 返回任务统计 + 平均进度 + 下一个优先任务
- **消息注入**：`injectToMainChat()` 向主对话输入框注入文本并触发发送
- **MIT 合规**：所有衍生文件头部保留 dsh-plugin-pet 版权声明

### 文件变更
| 文件 | 操作 | 说明 |
|------|------|------|
| `app/voice_pet_host.py` | 新增 | 宿主端 API 路由（配置/对话/任务/进度） |
| `static/js/voice-pet.js` | 新增 | 客户端核心（浮层 + 面板 + 双模输入 + 状态机） |
| `static/js/voice.js` | 修改 | 多轮记忆 + 13 种意图 + 进度查询 |
| `static/index.html` | 修改 | 添加 voice-pet.js 脚本引用 |
| `static/js/main.js` | 修改 | wireVoiceAssistant 集成 VoicePet 浮层 |
| `static/js/panels.js` | 修改 | 设置面板改用内置精灵选择器 |
| `static/js/core.js` | 修改 | 配置默认值改用 builtin_sprite |
| `voice_assistant.md` | 新增 | 功能升级跟踪文档 |

---

## 2026-08-16 文档与源码全量盘点整理（本轮）

### 需求背景
用户要求「把 Design 等文档按照章节整理一遍，把源码都翻一遍，别漏了什么东西」。本轮不新增产品功能，只做文档整理与源码盘点。

### 方法
1. 枚举全部源码文件：根入口 5 个 + `app/` 16 个 + `desktop/` 50 个 + `static/` 15 个（12 个 JS + index.html + lite.html + style.css）+ `tests/` 4 个 + `tools/svg_picker.py` + 根目录 8 个冒烟脚本；排除 `backups/`、`__pycache__/`、`.bak`、图片/网页存档。
2. 并行派出 5 个只读盘点子代理，分别覆盖：入口 + app 后端 + sync_server；desktop Agent 核心；desktop 生态与基建；desktop UI；static 前端 + tests + 冒烟脚本。
3. 交叉核对源码与 README/Design/Techniques/Fact/FreqErr，形成不一致清单。

### 主要发现与处置
- **Design.md 重构**：由「版本记录 + 编号决策混排」改为按章节组织（定位/架构/入口/模块地图/数据文件/设计决策/开关阈值/安全红线/文档流程），版本记录迁出到 `dev_log/`；Design.md 现只描述当前状态。
- **数据文件清单修正**：源码实际读写 `audit.jsonl`、`ui_automation_journal.jsonl`、`tool_bugs.jsonl`、`tool_fail_count.json`、`notepad.json`、`drift_state.json`、`workspaces/`、`checkpoints/`、`sessions/` 等此前 Design 未列；`exchanges/*.json`、`themes/*.json` 为过时表述，已按源码改为 `history/`（兼容目录）与 `theme_custom.json`。
- **模块开关表补全**：Design 开关表原缺 `ENABLE_TOOL_DOCTOR/CHECKPOINT/NOTEPAD/COMMAND_PALETTE/FILE_MENTION/EMBEDDING_API/SAFETY_RULES/CASE_FEEDBACK/DIFF_PREVIEW/REMOTE_CMD/BROWSER` 等，本轮按 `desktop/config.py` 全字段补齐，并新增 `app/config.py` 与网页版 `CFG_DEFAULT` 两张表。
- **入口/端口/环境变量核对**：补齐 web/lite CLI 参数、`SYNC_TOKEN/DRIFT_API_BASE/DRIFT_API_KEY/DRIFT_ALLOW_LOOPBACK/SMTP_*` 环境变量说明；确认 sync_server 与网页版默认端口同为 8765 需错开。
- **前端 JS 数量**：`static/js/` 现有 12 个 JS 文件（历史 dev_log 中「9 个 JS」「7 个 JS」为旧批次表述），README/Design 已按 12 个文件列明职责。
- **网页版配置边界**：网页版 `CFG_DEFAULT` 是桌面版配置子集；`auto_drift_*`、`ENABLE_AUDIT_LOG/FILE_PARTITION/BROWSER_CTL/EXE_JOURNAL` 等前端只保存配置、实际执行在桌面端，已在 Design 注明。
- **只读保护与写保护分离**（v8.13.1）：`data/`、`backups/`、`config.json`、`Err.log` 仍禁读；`static/app/desktop` 源码允许 Agent 读；FSS 直连路径同源保护，已在 Design/README 对齐。
- **测试清单**：README 测试命令补 `tests/test_lite.py`；冒烟脚本与测试覆盖点已在 Techniques 各版本节记录。

### 验证
- 源码盘点未修改任何产品代码；文档经只读交叉核对。
- 本文件与 README/Design/Fact 同步更新，`todo.md` 清空、`done.md` 归档 `updates/`、本轮记录写入 `dev_log/20260816_docs_reorg.md`。

---

## v8.13.1 AGENT 可见开发者网络 / 网页源码 / JS（2026-08-16）

### 需求背景
用户询问 Agent 现在是否可以看到「开发者 networks 一栏、网页源码、JS 代码、Cookie」。本轮把可开放部分做成 Agent 只读工具，并明确 Cookie 安全边界。

### 模块划分
- **static/js/tools.js**：新增 `network_list`（最近 N 条 API 控制台请求：状态/方法/URL/耗时/打码请求体）与 `network_curl`（打码 curl 预览）；executeTool 补对应分发；工具 defs 注册。
- **static/js/devtools.js**：`DevTools.mask/maskCurl/curlOf/entries` 已具备，无需新增；Agent 调用时只经 mask 输出。
- **app/bridge.py / app/lite_server.py**：`_is_protected` 拆为 `_is_write_protected`（写/删/改名保护：app/desktop/static/data/backups/dev_log/updates + config.json/Err.log）与 `_is_read_protected`（只读保护：data/backups + config.json/Err.log）。fs/read 只查只读保护，源码目录可看；fs/tree/grep/glob/image 与截图保存端点同步补保护。
- **desktop/tools.py / desktop/search_tools.py**：`_is_protected` 保留为写保护；新增 `_is_read_protected`；read_file/list_dir/grep/glob/file_search/ui_review 应用只读保护，app_screenshot/browser_screenshot/exe_screenshot/store_asset(source_path) 补写/读保护。
- **static/js/fs.js（FSS 直连路径）**：新增同源 FSS 保护常量与 `_assertFsPath`，listDir/readFile/writeFile/mkdir/delete/rename/walkFs 全链路套用——防浏览器直连绕过桥端保护。
- **Cookie 边界**：会话 Cookie 为 HttpOnly + SameSite=Strict，JS/Agent 永远不可读；API 控制台 curl 以 `Authorization: Bearer <会话令牌>` 复现请求，Agent 只拿到打码后的 curl 预览。

### 验证
- `node --check` 全部 JS；`_smoke_js85.py` 补 network_list/network_curl 与 FSS 保护断言；`_smoke_audit_v813.py` 补 3b/第 10 节保护矩阵；全量回归退出码 0。
- 故障检测子 AGENT 三轮复检：第一轮 P2×3（mkdir 漏保护、mask fail-open、目录列举泄露）→ 修复；第二轮 P1×1（FSS 无保护）+P2×2（fs/image、browser_screenshot 绕过）→ 修复；第三轮桌面 P1×2（file_search、ui_review 泄露）+P2×4（截图/资产工具缺口）→ 修复；最终结论「最终验收：零错误」。

## v8.13.0 全量端口检修（第二轮，2026-08-16）

### 需求背景
用户再次要求「全量检修，多轮自检刁难极端测试：任何有端口没编码、实现简陋、前端缺少对应端口的，都实现得死死的」，并强调最后一轮必须是零错误检查（只检查不修复）。

### 检修方法
1. 基线对账：重新枚举 FastAPI 路由 99 处、前端 API 字面量 100 处、桌面 TOOL_HANDLERS 54 个、网页工具 defs/dispatch 各 36 个；跑全部既有测试与冒烟，先暴露真实回归（test_lite 127.1 误判、test_desktop sync_server 鉴权测试未适配）。
2. 按「后端 / 前端 / 桌面跨端」三路子 AGENT 刁难审查 + 自身逐文件巡检，发现一批上轮漏网的深层问题（数值 host 被 DNS 前置误判、三端危险正则不同源、跨端快照不互通、sync_token 无设置入口、自动打分互斥竞态、bridge 缺系统目录保护、多任务会话/死控件等）。
3. 极端测试新增 `_smoke_audit_v813.py`（11 节）锁住全部修复边界；第二/三轮刁难复检发现的问题全部修复后，最终验收子 AGENT 独立复跑全部检查并给出「最终验收：零错误」。

### 关键修复与实现
- **SSRF 数值 host 归一化**：proxy/lite/sync_server/bridge 四处统一「先数值归一化（127.1/0x7f…）再 DNS」，归一化成功即 IP 字面量、跳过 DNS；非 IP 域名 DNS 全部 to_thread；空解析直接 400。
- **LLM 代理参数白名单化**：proxy/lite 都增加 5MB 请求体前置、api_key/model 长度、消息 content 形状、temperature 范围、max_tokens 范围、tools 数组/数量/大小/每条 shape 校验。
- **危险命令三端同源**：desktop/tools.py、static/js/tools.js 补齐 `rd /s`、taskkill 顺序无关、`--force` 负向排除 `--force-with-lease`，与 app/security.py 完全一致；JS requestApproval 支持 dangerous 标记（delete_file 在 danger 模式也弹审批）。
- **跨端快照互通**：网页版新增 `{"v":2,"kind":"web-cold","data":标准b64}` 冷备信封随 /push 上传；sync_server `_unpack_cold` 兼容 web-cold 与桌面 zlib 两种格式；桌面 `import_snapshot_bytes` 识别网页 AES-GCM 信封（PBKDF2-SHA256 200k + AES-256-GCM）与 web-cold；网页 pull 兼容桌面 zlib 冷备/无口令主快照（DecompressionStream）。
- **工作区系统目录保护补回桥端**：app/bridge、app/lite_server 对齐 desktop/tools.py `_is_protected`——工作区=项目根时 app/desktop/data/backups/dev_log/updates/static 与 config.json/Err.log 禁止 read/write/delete/rename；bridge /fs/delete 同时禁止删除工作区根。
- **sync_server 完整化**：/chat、/cmd/dispatch、/cmd/result 改手动读体限流；push 的 data/cold 格式深度校验（base64 可解码、信封字段可解码）；冷备读写异常落 Err.log；LLM 失败回显脱敏；HTML 控制台新增远程指挥面板（节点列表 / 下发 / 15 秒轮询结果）；token 常量时间比较。
- **前端死控件清零**：mode-select 真实接线（chat 只读工具裁剪，experts 网页未开放则隐藏）；btn-attach 文件引用选择器；Knowledge 文档直接打开；Assets/Experts/Schedule/Marketplace/Editor 标签全部接到真实功能；New Task 与左栏 Tasks/Chats 实现多任务会话（IndexedDB 按 taskId 隔离，旧 default 会话兼容）；启动即从 /models 填充 #model-select；无实现设置页/占位按钮按模块开关原则隐藏（web 与 desktop 同步清理）。
- **快照与错误不吞**：SnapRound setInput/toolCall/commit 失败以 note 事件呈现；snap_bridge owner 写失败、sync_server 冷备读失败落 Err.log；Err.log 读接口只回末尾 1MB，写入口 5MB 轮转。
- **竞态与上限**：/models/auto_score 互斥标记改为「先占位再 await」，消除并发双跑；meta_bridge 打分 NaN/Inf 拒绝；snap_bridge guard bool 歧义/round_no/元数据/rid 长度上限；fs 路径长度与 rename 冲突 409；lite 鉴权补齐与完整版同源限速。
- **桌面同步闭环**：settings_dialog 新增「同步令牌」输入并保存加载；设置导出/导入密钥排除穷举（api_key/sync_password/sync_token/drift_api_key/search_api_key）；remote_cmd 令牌改 sync_token（回退 sync_password）。

### 第二/三轮刁难修复（后端 P0-P3 / 前端 P1-P3 / 集成 P0-P2）
- **八进制 SSRF 旁路**：`_normalize_numeric_ip` 对前导零分量按八进制解析（0177.0.0.1 → 127.0.0.1），proxy/lite/sync_server 三份同步修正并加回归。
- **灾难性回溯前置拒绝**：grep 增加交替型/嵌套量词检测（`(a|aa)+$`、`(a+)+` 直接 400），桌面 grep 补齐 pattern 上限 + wait_for 30s。
- **danger_ok 全链路严格布尔**：同步 `/cmd/dispatch` 与 `sess_begin.enabled` 的 `"false"` 歧义。
- **push/chunked 体限**：`/push` 改为手动读体限流；`/chat` 非预期类型 400；config 顶层 JSON 类型校验（数组/null 回退默认）。
- **前端正确性**：工具执行中被停止发 run_cancelled；dirty 文件重新打开/关闭需确认；AOE 取消节点不再标 done；桥授权后刷新文件树/状态栏；FSS 目录重命名走句柄 move；trace 空态/args JSON/error 样式；模型选择持久化；write_file 缺 content 显式报错；lite 停止不再误导 toast；导航互斥不吞任务高亮。
- **测试污染治理**：tests/test_desktop.py 资产库测试改临时 VAULT_PATH（P0 数据破坏修复）；新增 users.delete_user/delete_users_by_prefix，各测试/冒烟 finally 清理测试账号；set_runtime_port 提供 reset；`_smoke_audit_v811.py` 不再吞 TypeError 跳过安全分支；`_smoke_audit_v813.py` ImportError 视为失败。
- **桌面路径最小化**：list_dir/read_file/write/edit 的 meta.path 与审批 cwd 改相对路径，AI 上下文不再携带工作区绝对路径。
- **文档契约修正**：CLI 依赖表述、Lite 精简差异、完整版与 sync_server 同端口冲突提示、冷备明文语义、run_command background 桌面专属、plan_and_execute task 统一。

### 验证
- 全量回归：tests/test_smoke.py / test_lite.py / test_desktop.py / test_gui.py + 8 个 Python 冒烟脚本 + node --check 全部 JS/HTML 内联脚本 + node _smoke_devtools_curl.js 全部退出码 0。
- 新增 `_smoke_audit_v813.py`（11 节）覆盖：127.1 归一化、八进制 SSRF、LLM 边界、fs 根保护/rename/grep/交替回溯拒绝、meta NaN/SSRF、snap 上限、sync web-cold+远程指挥+danger_ok 严格布尔、跨端快照导入、危险命令三端同源、桌面 chat 只读与根目录闸。
- 最终零错误验收（只检查不修复）：验收子 AGENT 独立复跑全部检查并输出「最终验收：零错误」；Err.log 为空。

## v8.12.0 全量端口检修（2026-08-16）

### 需求背景
用户要求「全量检修，多轮自检刁难极端测试：任何有端口没编码、实现简陋、前端缺少对应端口的，都实现得死死的」，并确认「开发者模式获取网络请求如 curl」必须做到（应用中的自动化请求依赖这个），且最后一轮必须是零错误检查。

### 检修方法
1. 全量端口清单：枚举 app/*.py、sync_server.py 的路由（82 个端点）与 static/js/*.js 的 68 处 api/fetch/sseFetch 调用，三路并行子 AGENT（后端健壮性 / 前后端接线 / Lite+同步+CLI）交叉审计。
2. 分类：端口没编码（stub）→ 去骨架化；后端有端口前端没接 → 补 UI 接线；实现简陋 → 加校验/上限/异常处理。
3. 多轮刁难：两轮子 AGENT 刁难审查（轮 1 报 P1×2+P2×13，轮 2 复检报 P2×1+P3×3），逐条修复；最后一轮零错误验收（py_compile + 8 个 Python 冒烟 + 1 个 node 冒烟全部退出码 0）。

### 关键实现
- **curl 可复现（Bearer 会话令牌）**：auth.py 新增 `session_token()`/`set_session` 返回令牌，登录/注册响应带 `token`；`current_user` Cookie→Bearer 回退；main.js 存 localStorage（登出清除）；devtools.js curlOf 携带 `Authorization: Bearer`，预览 `maskCurl`（Bearer 打码 + KEY_RE 打码 body 内 api_key/password/token）。
- **项目下载真实打包**：`_PROJECT_INCLUDE` 清单 + `_path_excluded`（**相对**路径 parts，避免检出目录名撞排除名整包变空）+ `_zip_ok`（符号链接/30MB 跳过）+ `_project_size_mb` 与 zip 同规则。
- **模型打分/榜单前端**：/models/ranks 响应改 `{"model","score","free"}`（inf 归一化防 JSON 500）；panels.js 榜单三列（性能榜 b.1/性价比榜 b.2/未定队列）+ openScoreDialog（value null=未定清除）+ populateModelSelect（#model-select 注册表填充）。
- **轮内回退点前端**：chat.js toggleRollbackPoints（rollback_points 列表 + /rollback/{round_no} + /sessions/{rid} 详情）。
- **checkpoint/restore P1-2**：`Path(raw).resolve()` 对相对路径按 CWD 解析致 is_absolute 恒真 → 改 `os.path.isabs` 判相对后拼 CHECKPOINT_DIR。
- **同步令牌贯通**：desktop/config `sync_token`；desktop/sync `_sync_headers` 五接口带 X-Sync-Token；build_snapshot 排除 sync_token；sync_server `_check_auth` 无 TOKEN 时仅环回（XFF 存在→403 强制令牌）+ CORS `allow_origin_regex` 环回源。
- **危险命令 danger_ok 协议**：security.DANGEROUS_PATTERNS（修正 --force-with-lease/rd /s/taskkill 顺序无关）；bridge+lite run_command 403；web 审批卡置 danger_ok、lite 本地预判 + 后端 403 二次确认重试、终端 confirm。
- **健壮性批量**：grep ≤200+30s、power 白名单、fs/read 二进制 415、代理消息形状/500 条/2MB、SSRF any 语义、meta_bridge 全锁、错误泛化落 Err.log、Err.log 盘符脱敏、审计 5MB 轮转、验证码 LRU、邮箱打码、二次 wait 限时。

### 验证
- `_smoke_audit_v811.py`（12 节极端测试：Bearer 鉴权/邮箱打码/真实 ZIP/相对 bak_path 恢复/榜单 inf/危险命令边界/XFF 403 等）退出码 0。
- `_smoke_devtools_curl.js`（curl 真实值可复现 + 预览打码）退出码 0。
- 回归全绿：`_smoke_meta85.py`/`_smoke_snap85.py`/`_smoke_js85.py`/`_smoke_trace_v810.py`/`_smoke_trace_web.py`/`tests/test_smoke.py`/`tests/test_gui.py` 全部退出码 0；全项目 py_compile 与 node --check 全绿。
- 两轮子 AGENT 刁难审查全部修复；最后一轮为零错误验收检查（只检查不修复）。

### 补充批次（孤儿端口清零 + 第三轮刁难修复）
- **回退点全生命周期**：`SnapRound.toolCall` 返回 count（round_no）→ ctx.rollbackNo 传入执行器 → 写工具调 `/rollback_point`（后端直读旧内容 + 50MB/二进制跳过防清空 + existed 后端判定）；commit(meta, keepRollback)（出错/取消轮保留，对齐桌面「正常轮提交即弃」）；快照链路失败 emit note（错误不得吞没）。
- **单文件版本全览**：`openVersionDialog`（/checkpoint/versions + /checkpoint/restore 只读预览 + 恢复补 snapTreeAfter）；恢复前快照 source 由非法 'ui' 改为 'restore'。
- **重搜打分 web 对等**：`POST /models/auto_score`（`_AUTOSCORE_RUNNING` 互斥 + `asyncio.run` 于后台线程新事件循环 + 请求体凭据透传覆盖 config.json + 无凭据 400）；`desktop/models.py` upsert/delete/set_score 加 `threading.Lock`（跨 GUI/web/auto_score 上下文防 lost update）；打分对话框补 fixed checkbox。
- **Lite 接线**：server/info 补 allow_register/bridge_authorized（注册按钮显隐 + 首次使用提示）；设置弹窗手动工作区路径 → POST /workspace。
- **SSE/网络规范**：sseFetch 空行复位事件类型；proxy/lite `_validate_base` 异步化（DNS to_thread，失败 400，事件循环零阻塞）；sync_server /push 双格式校验（urlsafe base64 或 web JSON 信封）、/pull 读异常明确错误、/cmd/result 节点校验+心跳刷新。
- **第三轮刁难修复**：P1-1 push 校验误杀 web 信封；P2-1 回退点空内容清空文件→跳过；P2-2 恢复分支漏树更新；P2-3 guardBlocked 漏 ctx；P2-4 models.json 并发锁；P2-5 纯 web 无凭据；P2-6 DNS 失败二次阻塞；P2-7 proxy 同步 DNS。tests/test_smoke.py 补 allow_ai_delete 状态隔离（防残留配置误报）。

## v8.11.0 轨迹详情预览窗口增强：拖动 + 置顶悬浮 + 元素选择引用（2026-08-15）

### 需求背景
用户补充要求「打开预览窗口后的状态，可以放大缩小拖动悬浮的对话窗口，可以选择元素或文字引用」。v8.10 已实现缩放与文字引用，本轮补齐：**拖动移动、置顶悬浮、元素选择引用**，桌面 + 网页双端对等。

### 模块划分
- **desktop/trace_preview.py（增强）**：
  - `_DragFilter(QObject)` 事件过滤器：按住顶部标题区（QFrame，cursor=SizeAllCursor）左键拖动移动窗口；偏移用 `pos()`（客户区坐标，与 `move()` 一致，避免带原生标题栏时 frame 偏移跳变）；`_move_within()` 按 `availableGeometry` clamp（至少保留 80px 宽 / 48px 高在可视区，防拖出丢失）；`Leave/Hide/FocusOut` 与无按键 MouseMove 时复位 `_offset` 防"粘住"。
  - 「置顶」QAction（checkable）→ `_toggle_pin()`：切换 `WindowStaysOnTopHint`，可见时重新 `show()/raise_()`；`is_pinned()` 供测试。
  - `_elements()`/`_element_text()`：详情按「角色/时间/类型/摘要/详情」五元素分块（仅非空字段；时间以 `_fmt_time` 格式化成功为准）。
  - `_render()`：每块 `<a href="quote:key">引用</a>` + `QTextBrowser.setOpenLinks(False)` + `anchorClicked`→`_on_anchor()` 解析 `quote:` 前缀发 `quote_requested`；detail 走 md_to_html、其余 html.escape；正文 `white-space:pre-wrap` 保留换行。
- **static/js/trace.js（增强）**：`_zoomLevels=[80,100,120,150]`（与桌面一致，消除旧步进 20 产生的 140% 档）；`_applyZoom` 同时缩放 `.trace-modal-elem-body` 与 `.trace-modal-elem-label`；`_initModalDrag`/`_endModalDrag`（mousedown→window mousemove clamp→mouseup/blur 复位，`e.buttons&1` 兜底释放丢失）；`togglePin`/`_syncPin`、`quoteElement`/`_elementList`/`_renderElements`/`_buildFullText`/`_fmtTime`（NaN 保护）；`openPreview` 对 role/summary 判空保护；`_elementList` 先 trim 再判空（与桌面一致）。
- **static/index.html**：`#trace-modal` 结构改为 `#trace-modal-card`（absolute 居中可拖）+ `#trace-modal-head`（拖动柄）+ `#trace-modal-body`（元素容器）+ `#trace-modal-pin`（置顶按钮）。
- **static/css/style.css**：`.trace-modal-card` absolute `left/top 50% + translate(-50%,-50%)`；`.trace-modal-head` cursor:move；`.trace-modal-pinned` z-index 9999 + mask `pointer-events:none` 透明（真悬浮，可与背后页面交互）；新增 `.trace-modal-body/.trace-modal-elem*` 分块样式。
- **static/js/main.js::initTraceModal**：接线置顶按钮；遮罩点击 `if(!TraceView._pinned)` 才关闭。

### 关键技巧
1. **拖动偏移统一用 pos()（客户区坐标）**：带原生标题栏时 `frameGeometry().topLeft()` 与 `move()` 相差一个 frame margin，混用会导致首动跳变；两端统一用客户区坐标，拖动零跳变。
2. **拖动 clamp 防丢失**：桌面按 `availableGeometry` 保底 80×48 可见；网页 `Math.min(Math.max(x, keepW-w), innerWidth-keepW)` 保底 80×48 可见，避免拖出视口后无法拖回/无法关闭。
3. **释放丢失兜底复位**：网页 `e.buttons&1` 判断 + `window.blur` 复位 `_endModalDrag`；桌面 `Leave/Hide/FocusOut` + 无按键 MouseMove 复位 `_offset`，避免 `_dragging`/`_offset` 卡死导致全页文本选择被禁用或事件被吞。
4. **置顶即真悬浮**：桌面 `WindowStaysOnTopHint`；网页 `.trace-modal-pinned` 让 mask `pointer-events:none` 透明（而非仅抬 z-index），置顶后可边看边操作背后页面，遮罩点击不关闭。
5. **元素引用走 XSS 安全路径**：桌面 detail 走 md_to_html、其余 html.escape；网页动态内容 `textContent`（el() 第三参为 innerHTML，仅静态文案"引用"使用）；引用文本统一 `[标签] 内容`，与「引用选区」「引用全部」并存。
6. **双端档位/字段严格对齐**：缩放档位统一 [80,100,120,150]；元素标签集合（角色/时间/类型/摘要/详情）与引用格式一致；纯空白字段两端均 trim 后跳过；非法时间戳两端均过滤。

### 验证
- `python _smoke_trace_v810.py`（扩展至 12 节：新增 Test10 元素选择+引用+anchor+full_text / Test11 置顶 toggle+stay-on-top flag / Test12 拖动过滤器捕获 press）通过，退出码 0。
- `python _smoke_trace_web.py`（方法数 26→35，新增 _endModalDrag 等；HTML 元素 15→19；CSS 规则 15→21；新增 v8.11 元素 textContent XSS 断言）通过，退出码 0。
- `node --check static/js/trace.js` / `main.js` 通过。
- `python tests/test_gui.py`（offscreen GUI 实例化回归）通过，退出码 0。
- 子 AGENT「上下文故障检测专员」两轮审查：第一轮报 P1×2（双端拖动无边界 clamp 会拖丢窗口）+P2×10（pos/frame 偏移跳变、释放丢失粘住、置顶非真悬浮、时间 NaN、缩放档位/作用域不一致、空白处理不一致、openPreview 空指针、死代码）全部修复；第二轮全局复检 A-E 全通过（桌面接线/网页 boot 顺序/死链/XSS/双端元素一致性）。

## v8.10.0 工作轨迹深度增强 + 详情预览对话框（2026-08-15）

### 需求背景
用户要求「DeepSeek Harness 的轨迹能力也得有」——手动标记点、区间查询调用详情、操作搜索、关键词搜索；同时参考图展示了「打开预览窗口后」的另一种状态——可放大缩小、可选择元素或文字引用。本轮把这四项能力 + 详情预览对话框（带文字引用）补到 DeverAI 桌面 + 网页双端。

### 模块划分
- **desktop/trace_preview.py（新）**：`TracePreviewDialog`（QDialog + QTextBrowser，零第三方依赖，HTML 渲染走 md_to_html 严格防注入），含工具条（复制全部 / 引用到对话 / Ctrl+/-/Ctrl+0 缩放 80/100/120/150% / Esc 关闭 / Ctrl+W 关闭）；窗口默认 800×640，最小 600×400，最大屏幕 95%；`setAttribute(Qt.WA_DeleteOnClose, True)` 关闭即销毁，避免滞留隐藏实例；选区右键菜单「引用选区到对话 / 复制选区 / 引用全部」三选项。
- **desktop/quest_panels.py::TraceTimeline**：升级为可交互 widget——左键单击空白处新增标记点（snap 到最近事件）、双击（经 250ms 单击延时判定避免重复添加 P2-1）、左键拖拽划选区间（区间内事件即时高亮 + 顶部实时统计条数/Turns/Calls/Errors/Top 工具）、右键 marker 弹菜单（删除/清除全部）；marker/range 不持久化，set_items(reset_extras=True) 时清空（clear/set_history 用），reset_extras=False 时保留（add_event 流式追加用，P1-2）。
- **desktop/quest_panels.py::TraceFlow**：升级筛选逻辑——`_matches()` 同时检查 role_filter / range_filter / 关键词（多关键词 AND，token 存原始文本，匹配时按 case_sensitive 决定是否 lower，P1-5）；卡片 `__idx` 写入真实 _all_items 索引（P1-1），双击/详情按钮准确定位；advanced=False 时不挂 item_activated、不显示「详情」按钮（P1-3）。
- **desktop/quest_panels.py::TracePanel**：新增「操作」QToolButton 多选下拉（角色 system/context/user/assistant/tool 多选）+ 关键词 QLineEdit（空格分隔 AND）+ 区分大小写 toggle 按钮 + 「+标记」按钮；区间统计 QLabel；模块开关 `ENABLE_TRACE_ADVANCED`（关闭时退回 v8.8 形态）；`quote_requested` 信号透传至 `chat.add_quote`；clear() 同步复位搜索框/大小写按钮/操作勾选 UI。
- **desktop/gui.py**：`trace_panel.quote_requested.connect(lambda text: self.chat.add_quote("轨迹预览", text))`。
- **desktop/config.py**：新增 `ENABLE_TRACE_ADVANCED: bool = True`。
- **static/js/trace.js（重写）**：`TraceView` 暴露 26 个方法——init/addEvent/setHistory/clear/setKeyword/toggleCase/toggleOpRole/clearOpFilter/addMarkerAtEnd/openPreview/closePreview/zoomIn/zoomOut/zoomReset/quoteSelection 等；时间线 canvas mousedown/mousemove/mouseup/dblclick/contextmenu 全套交互；详情 modal：`.trace-modal-card`（默认 70vw / 70vh，可拖拽边界缩放，min 480×320，max-width 900px）；ESC/Ctrl+=/Ctrl+-/Ctrl+0 全局快捷键；`_inited` 守卫防重复 init（P2-3）；`ENABLE_TRACE_ADVANCED=false` 时隐藏 ops/case/+标记/详情按钮并跳过时间线交互（P1-4）。
- **static/index.html**：trace-view 顶部新增 `#trace-range-stat`；新增 `.trace-controls` 行（操作下拉 + 关键词 + 大小写 + +标记 + 清空）；新增 `#trace-modal`（含 role badge、summary、toolbar、detail）；新增 quote-chip CSS。
- **static/css/style.css**：新增 `.trace-range-stat` / `.trace-controls*` / `.trace-ops-menu` / `.trace-case-btn` / `.trace-modal*` / `.quote-bar*` 等样式（含深色主题适配）。
- **static/js/main.js**：`App.addQuote(label, content)` / `App.buildQuoteMessage()` / `App.clearQuotes()` / `renderQuoteBar()`；新增 `initTraceModal`（接线 modal 关闭/缩放/引用按钮）与 `initTraceOps`（接线 ops 下拉 toggle + 外部点击关闭）；boot 尾部统一初始化 TraceView.init() + initTraceModal() + initTraceOps()。
- **static/js/chat.js::sendMessage**：在 addUserMessage 前用 `App.buildQuoteMessage()` 拼接引用内容到用户消息，发送后 `App.clearQuotes()` 清空引用条。
- **static/js/core.js::CFG_DEFAULT**：新增 `ENABLE_TRACE_ADVANCED: true`。

### 关键技巧
1. **手动标记/区间不持久化**：marker 和 range 存内存，每次 clear/set_history 才清空；add_event 流式追加走 `set_items(items, reset_extras=False)` 保留用户标记（P1-2）。
2. **真实 __idx 而非过滤下标**：`TraceFlow._rebuild` 给每个 QListWidgetItem 存 `__idx`（_all_items 真实下标），双击时反查——筛选激活时点第 N 张卡片弹出真实第 N 张卡片对应条目（P1-1）。
3. **单击延时消歧双击**：mousedown 时记位移阈值（6px / 5px），mouseup 时若位移超阈不算点击；若距离近，启动 250ms QTimer 延时新增标记，dblclick 抢先 `cancel` 掉 timer 避免重复添加（P2-1 桌面+Web 对等修复）。
4. **case_sensitive 不重写 token**：保存原始关键词，匹配时按标志决定是否 lower——切换大小写开关后历史 token 不被破坏（P1-5，与 Web 端对齐）。
5. **模块开关三层贯通**：ENABLE_TRACE_ADVANCED 在 config.py 定义 + TraceTimeline/TraceFlow/TraceView 消费 + init 决定是否挂交互——关闭时全部新入口隐藏（P1-3/P1-4）。
6. **XSS 注入防御**：引用条 `.quote-chip-text` 走 `textContent` 而非 innerHTML（FreqErr #149）；md_to_html 已经过滤脚本，QTextBrowser 不执行外部资源。
7. **WA_DeleteOnClose + _previews 列表**：TracePreviewDialog 关闭即销毁，self._previews 列表最多 5 个，超出淘汰最旧；用户可同时开多个对比。
8. **新增事件映射 `run_done`**：与 Web 端对齐（桌面 _event_to_item 此前只映射 run_final，P3-5 修复）。
9. **Ctrl+=/Ctrl+-/Ctrl+0 全局快捷键（仅 modal 可见时拦截）**：避免污染聊天输入。

### 验证
- `python -m py_compile desktop/trace_preview.py desktop/quest_panels.py desktop/gui.py desktop/config.py` 通过。
- `python _smoke_trace_v810.py`（新增 9 节冒烟）：TraceTimeline markers / range / TraceFlow 关键词 + 角色筛选 / TracePanel 创建 / TracePreviewDialog 创建 + 缩放 + 复制 / 非模态 + WA_DeleteOnClose / item_activated 信号 / range stat / **P1-1 P1-2 真实 __idx 激活 + 标记存活事件** / ENABLE_TRACE_ADVANCED 切换 时间线高级态 全部通过。
- `python _smoke_trace_web.py`（新增 P0-1 引用条 XSS 转义断言）：trace.js 26 方法 / index.html 15 元素 / style.css 15 规则 / main.js 5 函数 / chat.js sendMessage 集成引用 / **引用条 label/text 走 textContent（无 XSS 注入路径）** 全部通过。
- 子 AGENT「上下文故障检测专员」两轮审查：第一轮 P0/P1/P2/P3 多条问题；第二轮全部修复完毕（P0-1 Web XSS / P1-1 双击错条目 / P1-2 marker 被事件冲掉 / P1-3 P1-4 模块开关贯通 / P1-5 case_sensitive 不重写 token / P2-1 双击去重复 / P2-2 clear 复位 UI / P2-3 init 守卫 / P2-4 真实 idx 滚动 / P2-5 Web 缩放快捷键 / P3-2 WA_DeleteOnClose / P3-3 死代码清理 / P3-5 run_done 映射）。

## v8.9.0 五大核心能力深度优化（2026-08-16）

### 需求背景
用户要求深度优化并检验五大核心优势：①自动化工具/资料创建检修；②算力自动上下漂移且云端服务器打开后可直接从 HTML 版本状态继续任务；③完整安全工作树+回退审核机制；④文件分区规划并发执行加速；⑤浏览器 F12 获得 curl 命令、直接操控浏览器、选定软件探索与记录。

### 模块划分
- **P1 工具/资料检修**：`app/bridge.py` 新增 `/api/bridge/toolsmith/{tools,build,use,bugs,fix,clear}` 与 `/api/bridge/assets/{inspect,repair}` 端点（复用 desktop/toolsmith.py 与 vault 检修逻辑）；`static/js/tools.js` 补齐 `build_tool/list_tools/use_tool/list_tool_bugs/fix_tool_bugs/clear_tool_bug/inspect_asset/repair_assets` 工具定义与执行分发，开关裁剪与桌面版语义一致。
- **P2 自动漂移 + 云端 HTML**：`desktop/config.py` 新增 `ENABLE_AUTO_DRIFT/auto_drift_interval_min/auto_drift_on_exit`；`desktop/gui.py` 新增 QTimer 周期推送（只在有同步服务器且开关开时启动，推送失败静默不打扰）与退出自动漂移（auto_drift_on_exit=True 时跳过三选一问询直接走漂移退出）；`sync_server.py` 新增 `GET /` 返回零 CDN 单文件 HTML（内联 CSS/JS，展示 /drift/status 与 /pull 冷备历史、可 POST /chat 续聊），`GET /chat/page` 兼容别名。
- **P3 审计**：`desktop/audit.py`（新）——`audit_log(action, target, detail, actor)` 原子追加 `data/audit.jsonl`，`list_audits(tail)` 读取；`checkpoint.py`/`session_snap.py`/`tools.py` 的恢复与删除路径接入；`desktop/tools.py` 新增只读工具 `list_audits`；settings_dialog 加开关。
- **P4 文件分区调度**：`desktop/partition.py`（新）——`wave_partition(tasks)` 按 `t.files`（locks._norm 规范化）把同层任务分成多波，波内文件无交集；`run_expert_team` 与 `execute_aoe` 的层执行改为波执行；AOE `PLAN_SYSTEM` 增加 `files` 字段要求。
- **P5 浏览器/curl/记录**：
  - `desktop/browser_ctl.py`（新）——最小 CDP 客户端：`_ws_connect/_send_frame/_recv_frame`（RFC6455，纯 socket/struct/hashlib/base64）；`_spawn_browser` 用 Edge/Chrome `--remote-debugging-port=0` 拿临时端口 + `--user-data-dir` 临时 profile，只绑 127.0.0.1；`browser_launch/navigate/click/type/press_keys/close`；所有 AI 可给 URL 过 `_valid_url` 与 `_diplomatic_guard`，关闭时 `_kill_tree` 清理。
  - `desktop/tool_journal.py`（新）——`record(tool, args, result)` 原子追加 `data/ui_automation_journal.jsonl`（含摘要截断），`read_journal(tail)`；tools.py 的 `exe_*` 工具执行后记录；新增 `exe_journal` 只读工具。
  - `static/js/devtools.js`（新）——包装 `window.fetch`（记录 method/url/body/status/耗时，派发 `devtools:request`），`DevToolsView` 渲染请求列表 + `curl` 命令构造（bash/Windows 两版）+ 复制按钮；`static/index.html` 左栏新增 API Console 入口，`static/js/main.js` 接线；`static/css/style.css` 样式。

### 关键技巧
1. **CDP 零依赖 WebSocket**：握手用 `Sec-WebSocket-Key` + 魔数 `258EAFA5-E914-47DA-95CA-C5AB0DC85B11`；客户端帧必须 mask，服务端帧无 mask；`struct` 处理 7bit/16bit/64bit 长度；`--remote-debugging-port=0` 让浏览器自选空闲端口，从 `DevToolsActivePort` 文件读实际端口（并发安全）。
2. **自动漂移不打扰**：周期推送用 `DriftThread` 后台线程，失败只写状态栏不弹窗；仅在 `ENABLE_AUTO_DRIFT && ENABLE_DRIFT && ENABLE_SYNC && sync_server_url` 全真且 Agent 非运行中时触发（避免与在途 LLM 调用争抢）。
3. **审计与日志分离**：Err.log 只记错误；audit.jsonl 记回退/删除/恢复等「动作」，结构化 JSONL 便于后续查询与前端展示。
4. **文件分区波次调度**：`files=[]`（只读任务）与任何任务并行；有交集任务放后续波，既保住并发加速，又不依赖 LLM 计划质量——即使计划未声明 files，仍退化为原同层并行。
5. **前端 fetch 包装兼容性**：只记录不拦截，保留 `sseFetch` 的流式读取语义；curl 构造对 GET 省略 `-d`，POST 带 `-H "Content-Type: application/json"` 与 `--data-raw`，对 body 中 `api_key` 等敏感键做脱敏显示（不影响复制原始命令）。

### 验证
- `python -m py_compile` 全后端改动文件。
- 新增测试断言：toolsmith 桥端点、审计日志追加/读取、partition 波次、browser_ctl 模块导入与无浏览器降级、journal 记录。
- `python tests/test_desktop.py`、`python tests/test_smoke.py`、`python tests/test_lite.py`、`python tests/test_gui.py` 全绿。
- 子 AGENT 故障检测专员两轮审查。

## v8.8.0 工作轨迹页面 + 双版本 UI 交互体验优化（2026-08-15）

### 需求背景
用户要求根据参考图文件夹优化 UI 交互体验，并新增「工作轨迹」页面；需完整覆盖 Python（PyQt5 桌面版）与 HTML（网页版）双版本，不可遗漏。参考图核心特征：DeepSeek HARNESS 式轨迹页（顶部 Duration/Turns/Calls 时间线 + 按 SYSTEM/CONTEXT/USER/ASSISTANT/TOOL 分类的消息流）、左侧 Quest 导航、中央聊天/编辑器/轨迹工作区、右侧 Summary/Terminal/Files dock、卡片式工具调用与折叠 Thought/To-dos、底部输入卡带 Agent/模型选择器。

### 模块划分
- **desktop/quest_panels.py（新增 TracePanel）**：`TraceTimeline`（顶部横向条形时间线，按 role 分色：system/context/user/assistant/tool）、`TraceFlow`（QListWidget 消息流，带 role 徽章/时间/摘要/展开详情）、`TracePanel`（搜索框 + 时间线 + 消息流容器）。`add_event(ev)` 接收事件字典，`set_history(history)` 从历史消息重建，`clear()` 清空，`filter(text)` 按角色/内容过滤。
- **desktop/quest_panels.py（QuestSidebar）**：新增 `trace_requested` 信号与「Trace」导航按钮；`set_page(page)` 支持 `trace`。
- **desktop/gui.py**：导入 `TracePanel`；`_build_ui` 中实例化并加入 `workspace_stack`；连接 `quest_sidebar.trace_requested → _show_workspace_page("trace")`；扩展 `_show_workspace_page` 支持三页；`_handle_event` 中所有事件经 `self.trace_panel.add_event(ev)` 同步；`_load_history` / `set_history` 末尾调用 `self.trace_panel.set_history(self.history)`。
- **static/index.html**：左侧导航增加「Trace」入口；主工作区 `#main-body` 内新增 `#trace-view`（时间线、搜索框、消息流容器）；右侧 Summary 增加 Suggestions 折叠区占位（与桌面版对齐）。
- **static/js/trace.js（新）**：`TraceView` 对象——`init()` 绑定 DOM，`renderTimeline(items)` 绘制横向条形图，`renderFlow(items)` 渲染消息流，`addEvent(ev)` 实时追加，`setHistory(history)` 从会话历史重建，`filter(q)` 搜索过滤；角色映射与桌面版一致。
- **static/js/main.js**：左侧导航点击切换 `chat/trace` 视图；`enterApp` 中初始化 `TraceView`；Agent 事件总线 `onAgentEvent` 末尾把事件透传给 `TraceView.addEvent`。
- **static/js/agent.js / chat.js**：事件命名统一（`run_start/tool_start/tool_result/text_delta` 等已存在），chat.js 的 `onAgentEvent` 经 `App.onAgentEvent` 透传后由 trace.js 消费。
- **static/css/style.css**：新增 `.trace-view / .trace-timeline / .trace-bar / .trace-flow / .trace-item / .trace-role-* / .trace-search` 等类；统一卡片式消息、折叠 Thought/To-do 卡片、运行中脉冲、Progress 状态徽章；补齐右侧 Summary Suggestions 分区样式与空态。

### 关键技巧
1. **事件总线双消费**：桌面版 `_handle_event` 与网页版 `onAgentEvent` 都把事件同时发给聊天区和轨迹页；轨迹页只读，不阻塞主流程，异常自身捕获不写 `Err.log`（避免轨迹 UI bug 污染主错误日志）。
2. **历史重建只取结构化消息**：`set_history` 过滤 `role ∈ {system, context, user, assistant}`，assistant 消息中的 tool_calls/tool_results 拆分为独立 TOOL 条目；duration 用首末消息时间差，turns 数 user 消息数，calls 数 tool 调用数。
3. **角色分类与色板绑定**：SYSTEM=#7c3aed（紫）、CONTEXT=#0891b2（青）、USER=#2563eb（蓝）、ASSISTANT=#111827（灰/文本色）、TOOL=#ca8a04（琥珀），与现有 CSS `--accent/--cyan/--ok/--warn` 变量对齐，桌面版用 QSS 动态属性或内联样式。
4. **时间线限量渲染**：超过 120 条时按对数采样抽 120 个代表点，避免超长会话 DOM/QWidget 卡顿；搜索过滤时仍按完整数据集过滤。
5. **NO EMOJI 贯穿**：轨迹角色徽章、完成标记、运行中状态全部使用 SVG 图标或 Unicode 几何符号（● ■ ▶ ✓），不引入 emoji。
6. **响应式收敛复用现有逻辑**：桌面版沿用 `resizeEvent` 的窄窗口隐藏侧栏逻辑；网页版沿用 CSS media query 与小窗口隐藏右 dock 逻辑，轨迹视图在窄屏下占满中央区。

### 验证
- `python -m py_compile desktop/quest_panels.py desktop/gui.py` 通过。
- `python tests/test_gui.py` 通过（退出码 0）。
- 网页版 `tests/test_smoke.py` 通过；手动检查 index.html 加载 trace.js 且无 404。
- 子 AGENT 故障检测专员全局复检重点：新增事件同步路径、DOM/CSS 类名一致性、PyQt 线程安全、轨迹面板清空/重建不泄露引用。

## v8.7.0 算力漂移退出状态机 + 外部程序/浏览器自动化 + 四大特点边界修复（2026-08-15）

### 需求背景
用户要求：①算力漂移补全「漂移退出 → 服务器续算 → 漂移回本地」的完整状态机（此前只有续聊，无项目编号/状态标记/开机检查/锁定/回传复位）；②AI 操作外部 exe 与浏览器交互（此前 browser 只有 open/read/screenshot 无 click/type）；③四大特点的边界/安全隐患修复（审计结论：目录穿越、过期锁、冷备不同步、目录删除无回退、同文件并发写）。

### 模块划分
- **desktop/drift.py（新）**：本地漂移状态机。`project_id()`=工作区绝对路径 sha256 前 12 位；`data/drift_state.json` 原子写（os.replace），绝不进工作区；写失败落根目录 Err.log（不静默吞）。
- **desktop/gui.py**：`_should_drift_exit`（退出三选一弹窗）→ `begin_drift` + `/drift/begin`（begin 线程有界 wait 2.5s，防 QThread 活销毁/登记未发出）→ `_drift_push("quit")`；`__init__` 尾 `_drift_pull()` + `_drift_check_startup()`（独立 DriftThread，finished 信号槽在 GUI 线程串行，合并幂等去重）；`_on_drift_status`：unacked>0 → `snap_mod.set_readonly(True)` + 弹「漂移回本地/保持锁定」，unacked=0 且服务器已复位 → 自动 `end_drift()` 清残留标记；`_drift_merge` 返回 bool，`_drift_back_after_pull` **仅合并成功才** end_drift+finish+解锁。
- **sync_server.py**：`/drift/begin|status|finish` + `_load/_save_drift_state`（写失败落 Err.log）；`/chat` 全程 `_CHAT_LOCK` + 文件写 `_FILES_LOCK` + 漂移状态 `_DRIFT_LOCK`；`/chat` 尾基于锁内最新冷备**追加**而非覆盖（防覆盖并发 /push）；`_data_is_cold()` 判定主快照是否明文——加密快照绝不降级覆盖；`/pull` 附带 `drift_cold`；`/chat` 消息 ≤8000 字符。
- **desktop/sync.py**：`drift_begin/status/finish_to_server` + `pull_from_server` 用 `drift_cold` 冷备历史覆盖主快照历史（冷备恒为超集）；push/pull/drift 网络异常统一转友好中文。
- **desktop/win_automate.py（新）**：ctypes user32 原语（SendInput/keybd_event/mouse_event/EnumWindows/GetWindowThreadProcessId/GetSystemMetrics），非 Windows `ctypes=None` 安全降级；`window_pid()`/`screen_size()` 供上层白名单与边界校验。
- **desktop/browser.py**：`browser_elements`（--dump-dom + `_ClickableExtractor` 限 300 条/单元素文本与属性截断 500）；`_run_browser` 有界流式读取（线程 drain + 超限 kill + 非 Windows 常量兼容）；`_normalize_numeric_ip` 归一化 hex/混合数值 host（防 SSRF 旁路）。
- **desktop/tools.py**：`_EXE_LAUNCHED` pid 白名单；`_ui_auto_hard_check`（唯一 exe / 文本限长）；`_ui_automation_guard`（开关→硬规则→copilot(kind=ui_automation)→审批模式路由 all/danger/free/copilot）；`_ui_auto_approval`（future 三路径清理不悬挂）；8 个 `exe_*` 工具（写类挂 `_readonly_block`，close 白名单硬闸，click 屏幕边界，launch 带参标危险）；`_zip_dir_backup`（os.walk followlinks=False + to_thread + 时间戳命名）。
- **desktop/session_snap.py**：`tool_rollback_dir` 存相对 rollback 目录的 zip 路径（`dirs/x.zip`），恢复时按快照目录解析 + 越界双重校验。
- **desktop/locks.py**：`_norm` 折叠内部 `..`/`./`；`declare_files` 对 `*` 通配持有者报冲突；acquire/try_acquire 过期锁自愈。
- **desktop/agent.py**：call_id 统一 `f"{id(self):x}:{raw_id}"`（LLM 原始 id 跨专家相同时审批 Future 不互覆）；非 chat 形态注入自动化安全铁律。
- **desktop/llm.py**：`httpx.HTTPError` → 友好 LLMError（stream_chat/chat_complete/embed_texts 三处）。
- **app/proxy.py / app/lite_server.py**：`_ip_of` 前置 `_normalize_numeric_ip`（同款 SSRF 修复）。
- **cli_main.py**：Agent 异常落根目录 Err.log。
- **tools/svg_picker.py**：ERR_LOG 指向项目根（单套制）。
- **tests/test_desktop.py**：新增第 22 节 v8.7 回归断言（hex IP / 锁规范化+通配冲突 / 目录回退相对路径 / 加密快照不降级）。

### 关键技巧
1. **漂移状态机三锁分层**：`_CHAT_LOCK`（chat 三段式串行）→ `_FILES_LOCK`（push/chat 文件写互斥）→ `_DRIFT_LOCK`（漂移状态 RMW），锁顺序固定无死锁环；`/chat` 开头读冷备在锁外（LLM 长调用不持文件锁），结尾在锁内重读最新冷备并**追加**两条 from_server 消息——并发 /push 的新快照永不丢失。
2. **加密不降级 + 冷备兜底**：`_data_is_cold()` 用 `_unpack_cold` 尝试解包判定格式，加密快照（Fernet）解包失败 → 不回写 DATA_FILE；云端消息全部进 COLD_FILE，`/pull` 同时返回 `drift_cold`，桌面端历史以冷备为准——设口令与不设口令用户漂移功能一致，加密承诺不破。
3. **hex IP 归一化**：`re.fullmatch(r"[0-9a-fx.]+")` 只拦截「疑似数值 host」，`int(p,16) if "x" in p else int(p,10)` 按现代浏览器十进制语义解析（避免 octal 误判），1/2/3 段形式按 GURL 右对齐展开；`dead.beef` 等合法域名解析失败自然回落 getaddrinfo，不误伤。
4. **exe 自动化纵深防御**：开关（默认关）→ 硬规则（唯一 exe/长度）→ copilot 语义审查 → 审批模式路由 → 运行时硬闸（pid 白名单/屏幕边界），五层全部在工具层完成，LLM 可控参数无处旁路。
5. **有界流式读取**：Windows 管道不可 select，用独立线程 drain + 64KB 块累加，超 8MB 即 kill 浏览器，`join(5)` 有界收尾——`--dump-dom` 超大页面不再撑爆内存。
6. **审批 future 三路径清理**：`_ui_auto_approval` 超时 resolve(False)、`except BaseException`（含 CancelledError）resolve 后重抛、无审批门直接 False——与 `_request_approval` 同构。

### 验证
- `python -m py_compile` 全部改动文件通过。
- `python tests/test_smoke.py`（网页版）通过。
- `python tests/test_desktop.py` 通过（含新增第 22 节 v8.7 回归断言）。
- `python tests/test_gui.py` 通过（退出码 0）。
- 两轮「上下文故障检测专员」子 AGENT 审查：第一轮 0 P0 / 4 P1 / 12 P2 / 14 P3；修复后第二轮复查确认全部闭环。

## v8.6.0 前端主题升级：双主题工作台（参考 3080 DeepSeek Harness）（2026-08-15）

### 需求背景
用户要求"把 Python & HTML 的前端都升级一下，参考文件夹内的其他 AI 或 3080 端口的 DeepSeek Harness"。经调研 Harness 深色工作台（深空灰底 + 深求蓝强调 + 无边框卡片 + 细腻动效），对网页版与桌面版做视觉与主题能力双升级。

### 模块划分
- **static/css/style.css**：CSS 变量体系新增 `html[data-theme="dark"]` 深色主题（`--bg:#1e1f23` / `--accent:#4d6bfe` 等全套变量）；`color-scheme` 联动原生控件；组件精修（输入卡聚焦光环、工具卡运行中 pulse-ring 脉冲、hover 过渡统一）。
- **static/index.html**：顶栏新增主题切换按钮 `#btn-theme`；引入 Highlight.js 深色样式表 `hljs-dark`（默认 disabled）。
- **static/js/main.js**：`applyTheme(pref)` 三态（auto/light/dark），auto 态用 `matchMedia('(prefers-color-scheme: dark)')` 跟随系统；按钮图标 sun/moon/contrast 轮换；切换时启停 hljs-dark。
- **static/js/core.js**：默认配置新增 `theme: 'auto'`。
- **static/js/panels.js**：设置弹窗"外观"区新增主题下拉（auto/浅色/深色），保存后即时 `applyTheme`。
- **static/js/icons.js**：新增 `sun` 图标；`moon` 移除硬编码白底 rect；`icon()` 统一 `fill/stroke → currentColor`（保留 none/white 例外：轮廓透明与深色主题背景层）。
- **static/js/chat.js**：AI 消息"思考中"三点 typing-dots 动画；流式输出尾部 stream-cursor 打字光标（结束后自动消失）。
- **desktop/themes.py**：新增 `harness` 色板（深空 Harness，逐值对齐网页版深色主题），THEME_LABELS 注册"深空 Harness"；`build_qss`/`chat_css` 无硬编码色全色板取值。
- **desktop/gui.py**：命令面板补"切换主题: harness"快捷项。

### 关键技巧
1. **主题状态机三态而非二态**：auto → matchMedia 解析为 light/dark 实际态写入 `data-theme`；localStorage 存的是偏好（auto/light/dark）而非解析结果，避免系统切换后被缓存值锁死。
2. **深浅双 hljs 样式表**：两个 `<link>` 常驻 DOM，用 `disabled` 属性切换，避免运行时插拔 link 引起 FOUC。
3. **SVG 图标 currentColor 继承**：fill/stroke 替换为 currentColor 后图标颜色随文字色走，一套 SVG 适配双主题；`fill="none"`（轮廓透明）与 `fill="white"`（部分图标背景层）保留不替换，否则轮廓图标变实心色块/深色主题丢背景。
4. **桌面/网页色值逐项对齐**：harness 色板 26 键与 CSS 变量一一对应（accent/ai_head/tool_bg/activity_* 全套），保证两端观感一致。
5. **NO EMOJI 不变**：主题按钮图标用 SVG（sun/moon/contrast）。

### 验证
- JS 括号平衡状态机（含正则字面量跳过，正则内引号不再误报）：main/core/panels/chat/icons 五文件全部 BALANCED。
- `python -m py_compile desktop/themes.py desktop/gui.py desktop/settings_dialog.py` 通过（PYEXIT=0）。
- 设置对话框主题下拉经 `THEME_LABELS.items()` 动态生成，harness 自动出现，无需改 settings_dialog.py。

## v8.5.7 网页版工具补齐：搜索 / 浏览器 / 暂存 / 元工具（2026-08-15）

### 需求背景
用户要求“检查 python+webUI 的缺漏点并补齐”。经对照桌面版 `desktop/tools.py::build_tool_defs` 与网页版 `static/js/tools.js::getToolDefs`，网页版缺以下非专家、非 toolsmith 的可移植工具：`search_tool`（元工具）、`file_search`（文件名相似度）、`web_search`（联网搜索）、`browser_open/read/screenshot`（浏览器控制）、`notepad_save/read/list/clear`（跨轮暂存）、`list_checkpoints`（版本快照查询）。

### 模块划分
- **app/bridge.py（新增 3 端点）**：`POST /api/bridge/web_search`（DuckDuckGo 零 key，复用 `desktop.search_tools._search_duckduckgo`）、`POST /api/bridge/browser_read`、`POST /api/bridge/browser_screenshot`（复用 `desktop.browser` 无头 Edge/Chrome）；URL 白名单校验 + 泛化错误防路径泄露。
- **static/js/tools.js**：
  - 新增工具定义与执行分发：`search_tool`、`file_search`、`web_search`、`browser_open`、`browser_read`、`browser_screenshot`、`notepad_save`、`notepad_read`、`notepad_list`、`notepad_clear`、`list_checkpoints`。
  - `browser_open` 客户端 `window.open(url)`（web 环境用户浏览器即宿主）；`browser_read/screenshot` 走桥；`notepad_*` 用 `localStorage`（key `deverai.notepad.v1`）；`file_search` 用 `walkFs` + 已存在的 `charSimilarity`；`list_checkpoints` 复用 `/checkpoint/files`。
- **static/js/core.js**：`CFG_DEFAULT` 新增 `ENABLE_WEB_SEARCH / ENABLE_BROWSER / ENABLE_NOTEPAD`。
- **static/js/panels.js**：`SWITCHES` 新增三项开关展示。

### 关键技巧
1. **工具开关三层贯通（FreqErr #67/#94）**：新工具 defs 与执行分发都按开关裁剪，开关关闭时从 defs 移除（对齐桌面 build_tool_defs 语义）。
2. **不重复造轮子**：`file_search` 复用 `charSimilarity`（core.js 已有）；`list_checkpoints` 复用已有 `/checkpoint/files` 端点；`browser_*` 复用 `desktop/browser.py`；`web_search` 复用 `desktop/search_tools.py` 的 DDG 抓取。
3. **NO EMOJI**：全部文案用纯文本/SVG 图标。
4. **跳过 expert/toolsmith**：`todo_update`、`port_declare/read`、`request_write_permission`（专家团写权）与 `build_tool/list_tools/use_tool`、`list/fix/clear_tool_bugs`（自研工具库/工具医生）依赖网页版尚未具备的专家团/工具设计 Agent 编排，不在本批补齐范围，留待后续批次。

### 验证
- `python -m py_compile app/bridge.py` 通过。
- `tests/test_smoke.py` 冒烟通过。
- 子 AGENT 故障检测专员全局复检。

## v8.5.6 Python 桌面版全局建议系统 + 状态优化（2026-08-15）

### 需求背景
用户要求“优化系统状态，保证 PYTHON 版本在非标识性内容上已经对齐了参考图列表，并且有全局建议提出系统（TRAE 的样式）”。TRAE CUE-Pro 参考图呈现：面板标题“Cue-Pro”、空态文案“暂无编辑建议，请先进行编码操作…”、底部状态“已处理 0/0 个变更点”。本次在 PyQt5 桌面版实现同风格全局建议面板，并优化状态显示。

### 模块划分
- **desktop/quest_panels.py（新增 GlobalSuggestPanel）**：
  - 标题栏：左图标 + “Suggestions” + 右“···”菜单按钮（NO EMOJI）。
  - 空态区：居中显示“暂无编辑建议，请先进行编码操作…”，参考 TRAE 默认态。
  - 建议卡片：类型标签（functional/technical/art 映射为“功能/技术/视觉”）、标题、详情摘要、采纳/忽略按钮；点击采纳把建议文本写入输入框并 emit 信号。
  - 底部计数条：左图标 + “已处理 {processed}/{total} 个变更点”；total 为当前会话建议总数，processed 为用户已采纳数。
  - 提供 `set_suggestions(items)` / `set_counts(processed, total)` / `clear()` API；emit `suggest_adopted(str)` / `suggest_dismissed(dict)` 信号。
- **desktop/quest_panels.py（SummaryPanel 改造）**：
  - 在 Progress/Artifacts/References 之前新增 `sec_suggest = Section("Suggestions", global_suggest_panel, expanded=True)`，让建议面板成为右栏首要可见分区；保持其余 tab 不变。
- **desktop/gui.py（状态与接线）**：
  - `DeverAIApp` 维护 `_suggest_total` / `_suggest_processed` 计数；`_show_suggestions` 同时更新 `chat.show_suggestions` 与 `summary_panel.suggest_panel.set_suggestions`。
  - 采纳建议时调用 `chat.set_input_text(detail)` 并 `_suggest_processed += 1`，刷新计数条；同时保留旧聊天区建议按钮作为兼容。
  - 状态栏右侧新增 `system_status_lbl`：展示“就绪 / 运行中 / 只读 / 建议待处理 ({pending})”，随 `_busy`、只读状态、建议数更新。
  - `_update_statusbar` 左侧保持模型/工作区，右侧刷新系统状态，实现系统状态优化。
- **desktop/themes.py**：新增 `#globalsuggest` 面板 QSS（标题栏背景、空态文字、类型标签底色、计数条背景、采纳/忽略按钮样式），全部从色板取色。
- **tests/test_gui.py**：新增 `GlobalSuggestPanel` offscreen 构造、set_suggestions/set_counts/clear 调用、采纳信号发射的断言。

### 关键技巧
1. **不破坏现有建议流**：旧的 `_SuggestThread` 与 `ChatPanel.show_suggestions` 继续工作；新面板只是额外消费者，用户可在右栏全局看到建议。
2. **计数语义**：total = 本轮生成后累计建议数；processed = 用户点击采纳次数。清空会话时归零。
3. **状态栏优先级**：运行中 > 只读 > 建议待处理 > 就绪；运行结束后若仍有未采纳建议则显示“建议待处理 (N)”。
4. **NO EMOJI**：标题/按钮/状态全部使用 SVG 图标或纯文本；类型标签用中文“功能/技术/视觉”而非 emoji 色块。
5. **右栏首屏可见**：将 Suggestions 放在 Summary 最上方，符合参考图“全局建议提出系统”的首要位置。

### 验证
- py_compile 全后端文件。
- `tests/test_gui.py` offscreen 验证 GlobalSuggestPanel 构造、计数、采纳信号。
- 子 AGENT 故障检测专员全局复检。

## v8.5.3 前端精修重构（2026-08-13）

### 需求背景
用户要求"前端设计请精修，允许重构"，并提供了两张参考图：第一张为顶部模型/模式选择器（ShutDone/Less-online/LowRAM/Agentalty 开关 + Pilot/Copilot 分段 + 模型比例徽章列表）；第二张为完整三栏 Quest 布局（左侧 Quests/Chats/Schedule/Better Harness/Knowledge/Marketplace 导航、中间 Quest on, hands off 主交互区、右侧 Summary/Terminal/Files 折叠面板）。目标是让网页版拥有现代化、类 Quest/Agent 的视觉与交互体验，同时保持与现有后端 API 和浏览器端事件系统完全兼容。

### 模块划分
- **static/index.html**：整体 DOM 骨架改为左-中-右三栏。保留登录页 `#auth-view`；主应用 `#app` 内改为 `#left-nav`（Quest 导航）、`#quest-main`（Hero + 输入）、`#right-panel`（Summary/Terminal/Files）。移除旧的活动栏/侧边栏/聊天面板独立结构，将聊天流、输入框、工具卡片迁入中间主区。
- **static/css/style.css**：全新浅色主题色板（`--bg #ffffff`、 `--panel #f8f9fb`、 `--border #e8eaed`、 `--text #1f2328`、 `--accent #4f6af6`、 `--accent-2 #5b8cff`）。统一圆角 12px/10px/8px、柔和阴影、1px 细边框、层级用背景色区分。所有组件按新布局重写：`.qn-nav`、`.qn-main`、`.qn-right`、`.hero`、`.input-card`、`.status-row`、`.model-picker`、`.tool-card`、`.approval-card`、`.msg` 等。NO EMOJI：全部使用 SVG 图标或 Unicode 几何符号。
- **static/js/panels.js**：右侧 Summary/Terminal/Files 面板接管原 Terminal 输出与 Vault 概览；新增 `SummaryPanel`（Progress/Artifacts/References 折叠区）、`RightPanel.switchTab(tab)`；设置面板改为模态卡片内分组（模型/API、模型注册表、工作区、模块开关、审批与匹配、阈值、同步、诊断）。模型注册表 UI 改为列表卡片 + 比例徽章 + 打分/删除按钮。
- **static/js/chat.js**：聊天消息迁入中间主区 `#chat-messages`；用户气泡改为圆角 pill，AI 卡片改为扁平化卡片；工具卡片重绘为带头图标的折叠卡片；审批卡片强化命令展示与允许/拒绝按钮；子 Agent/AOE 节点使用新色彩与层级；GuardBanner 集成到主区顶部。
- **static/js/main.js**：事件绑定迁移到新 DOM（`#left-nav` 导航、`#right-panel` tab 切换、输入框发送、停止按钮、清空会话、终端输入等）。保留现有鉴权、文件树、编辑器初始化流程。
- **static/js/agent.js / core.js / tools.js**：事件键名与 API 调用不变；仅调整部分 DOM 查询选择器以适配新结构；`core.js` 中新增/补齐 Quest 布局所需的辅助函数（如 `setChatSub` 兼容新位置）。
- **static/js/icons.js**：补充新 UI 所需图标（quest/chat/schedule/harness/knowledge/marketplace/summary/terminal/files/send/stop/expand/collapse/check/warning/error/more 等），全部使用 SVG。

### 关键技巧
1. **DOM 零破坏迁移**：保留 `<script>` 加载顺序与全局对象（`App`、`Agent`、`Chat`、`Vault`、`Terminal`、`Approval`、`IDB` 等）的 API 签名，仅改内部选择器。后端 `/api/*` 调用路径不变。
2. **三栏响应式**：`#app { display: grid; grid-template-columns: 240px 1fr 320px; }`，左/右面板 `min-width` 限制，中间主区自适应；小屏时允许水平滚动。
3. **右侧可折叠分区**：Summary 页使用 `<details>` 或 JS 折叠，Progress/Artifacts/References 默认 Progress 展开，其余折叠，与参考图一致。
4. **顶部模型选择器**：设置面板中的模型选择改为弹层（`.model-picker`），但在当前重构中先以设置面板内嵌 + 主输入区 Agent/Model 选择器形式落地；后续可扩展为独立弹层。
5. **状态开关行**：在主输入区上方或设置面板暴露 `traffic_mode`/`sleep_enabled`/`low_memory_mode` 等开关的可视化切换，命名对齐参考图语义。
6. **颜色变量继承**：所有组件颜色从 CSS 变量读取，方便未来一键切换暗色主题；当前先落地浅色主题。

### 验证
- JS 括号/引号配平（`_smoke_js85.py` 风格词法检查）。
- `py_compile` 全后端文件。
- `python web_main.py` 启动不报错，首页渲染结构正确。
- 登录后主界面三栏可见，左侧导航、中间 Hero、右侧 Summary 可交互。
- 子 Agent 故障检测专员全局复检通过。

## 全量完整项目查修（2026-08-13，4 子代理并行审查 + 逐项修复）

### 审查范围
四路并行"刁难"审查：①网页版后端 app/（server/auth/bridge/meta_bridge/snap_bridge/proxy/security/users/mailer/config/storage/errors/codename/snap_util + web_main/lite_main/sync_server）；②网页版前端 static/js/ 全 9 文件 + index.html/style.css；③桌面端 desktop/ 全 43 模块 + main.py；④入口（main/web/lite/cli）+ tests/ 四套 + 冒烟脚本 + 跨端一致性 + 文档。

### 修复的 P1（必须修，8 项）
1. **前端上下文压缩 tool 消息错位**（agent.js compressMessages）：压缩切分使 recent 首条落在 role='tool' → LLM 400 长会话破坏 → 丢弃孤立 tool 前缀 + 压缩调用接 abortCtrl。
2. **审批/副驾驶期间停止失效**（tools.js/agent.js）：Approval.request 加 120s 超时自动拒绝；requestApproval 的 llmChat 接 signal（abort 按拒绝）；stopSession 中止挂起审批（Approval.respond(false)）。
3. **运行中清空会话卡死**（main.js）：Agent.running 时禁用清空 + 清空时强制 resolve 挂起审批。
4. **CLI diff_preview_needed 悬挂**（cli_main.py + desktop/tools.py）：CLI 加 diff 渲染+审批分支；tools.py 判定补 `ctx.emit is None` 放行（防无消费者悬挂）。
5. **snap_bridge 绝对路径泄露**（含用户名）：checkpoint/sessions 相关端点返回路径相对 CHECKPOINT_DIR/SESSION_DIR（_rel_to helper）；restore 兼容相对 bak_path；output 脱敏。
6. **快照会话未按用户隔离**（snap_bridge + session_snap）：begin 写 meta.owner；所有 sessions/{rid} 端点 _check_owner（owner 空=旧数据放行）；list 过滤 owner；list_sessions 输出加 owner 字段。
7. **sync_server /chat 无鉴权+SSRF**：_validate_llm_base（http(s)+禁内网/环回/链路本地，域名解析检查）；/push 64MB 上限+原子写；/cmd/register 1000 上限；history 截断 500；cmd_type 白名单。
8. **桌面 AOE 并发回退点丢失**（session_snap.add_tool_call）：读改写持 _META_LOCK。

### 修复的 P2（高价值）
- 后端：bridge timeout NaN 防护、run_command 启动失败脱敏、fs_write 目录 400；server.py 全局异常落 Err.log（app/errors.log_error）+ register/login Cookie Secure（传 request）；meta_bridge threading.Lock→asyncio.Lock；snap_bridge _TREE_CACHE 容量上限。
- 前端：ENABLE_SUBAGENT 空 if 补实现（降级 LLM 单次调用）。
- 测试：test_smoke 保存/恢复 llm_allow_loopback（防机器状态误报）。

### 验证
py_compile（9 文件）+ _smoke_js85.py + _smoke_snap85.py + _smoke_meta85.py + 四套回归（test_smoke/test_lite/test_desktop/test_gui）全部通过。路径泄露回归确认：rollback point 返回相对路径。

## v8.5 网页版对齐批次2：模型注册表/三级匹配/审批四模式/资产升级/建议去重/工具裁剪（2026-08-13）

### 模块划分
- **app/meta_bridge.py（新）**：FastAPI router（/api/bridge 前缀），复用 desktop 模块：
  - GET /models（`_to_dict` 序列化 ModelInfo）、POST /models（upsert：按 id 更新或 model_from_dict 新建+append；`_MODELS_LOCK` 读-改-写原子化防并发丢更新）、DELETE /models/{mid}、POST /models/score（打分 clamp 0-10）
  - GET /models/ranks?mode=b1|b2：weighted_rank/value_rank（**weights 键必须来自 desktop.config.score_metrics**——app ServerConfig 无该字段会榜单恒空 P1-1；返回 `[(ModelInfo,score)]` 需解包）+ unscored_queue
  - POST /matcher/rank：`SimpleNamespace` cfg 需含 api_base_url/api_key（api 级 embedding 模型 url 空时用全局兜底，都空则 matcher.api_embed 返回 None 走 bm25 降级链而非 AttributeError 500 P1-2）；输入长度校验 query≤2000、docs 总长≤1M
- **static/js/core.js**：CFG_DEFAULT 补 approval_mode:'danger'、embedding_level:'char'、ENABLE_EMBEDDING_API、embedding_model、low_memory_mode；新增 `charSimilarity(doc,query)`（bigram 余弦×0.7+查询包含度×0.3，与 desktop matcher 同构，空/单字符安全返回 0）
- **static/js/tools.js**：DANGEROUS_PATTERNS（18 条，与桌面逐条一致）+ `isDangerousCommand` + `requestApproval(payload,cfg)`（free 放行/danger 危险才弹/copilot llmChat 代判 kill→拒绝+`copilot_block` 事件、uncertain 或副驾驶不可用→保守升级用户弹窗/all 全弹；ENABLE_APPROVAL=false 全放行）；execRunCommand/execAppScreenshot 接入；**getToolDefs 的 defs 改 let**（const 在开关裁剪 filter 时 TypeError 崩溃 P1-3）
- **static/js/agent.js**：`selectToolsWeb(cfg,defs,todoText)` token_mode 才裁剪：BASE_TOOL_NAMES_WEB 基础集保留 + charSimilarity≥0.08 保留 top8；runSession llmChat 处接入
- **static/js/chat.js**：`copilot_block` 事件；SUGGEST_DOCS 补 AGENT.txt；`filterDedupeSuggestions`（标题去重+value 降序+三类均衡+max6，对齐桌面 suggest.filter_dedupe）；renderSuggestions type 补 esc（P2-4 防注入）
- **static/js/panels.js**：Vault.search 升级（bridge 模式 POST /matcher/rank 后端三级引擎 + min_score=vault_threshold，FSS/失败降级前端 charSimilarity）；设置面板"审批与匹配"区（approval_mode/embedding_level/embedding_model/vault_threshold 收集）+ 模型注册表区 loadModelRegistry（列表/新增/打分 prompt/删除，esc 防注入）

### 关键技巧
1. **weights 同源**：榜单权重的 score_metrics 键必须取自 desktop.config（与 models.json 的 scores 键同源），否则榜单恒空。
2. **api 级降级链路**：matcher/api_embed 无 base/key 时返回 None → rank 自动降级 bm25→char；桥必须把 api_base_url/api_key 传给 SimpleNamespace cfg 才能触发该降级而非 500。
3. **copilot 代批保守方向**：副驾驶不可用/解析失败一律升级用户弹窗（不放行危险命令）；kill 直接拒绝并发 copilot_block 事件。
4. **Vault.search 双通道**：bridge 后端三级引擎（index 对应 IDB.getAll 顺序稳定），FSS/离线前端 bigram 降级，阈值语义与桌面 vault_threshold 一致。

### 冒烟（_smoke_meta85.py 保留）
- 注册表：列表/upsert（含新增后读回）/score/ranks b1 含已打分模型 + 未定队列非空/b2/delete。
- 匹配：相似文档排前、空 docs 空结果、超大输入 400、**api 级无配置自动降级不 500**（P1-2 回归）。
- 回归：py_compile + _smoke_js85.py + test_smoke/test_lite/test_desktop/test_gui 全绿。

### v8.5 批次2 故障检测修复
- P1-1 榜单恒空（weights 用 app ServerConfig 无 score_metrics）→ 改 desktop.config 同源
- P1-2 api 级 500（SimpleNamespace cfg 缺 api_base_url/api_key）→ 补全局兜底触发降级链
- P1-3 getToolDefs const→let（关闭 app_shot/ui_review 开关即崩溃）
- P2：upsert 并发锁、打分 clamp 0-10、建议 type 转义、冒烟断言补强

## v8.5 网页版对齐批次1：快照/只读报警/依赖树/上下文守门（2026-08-13）

### 需求背景
用户："网页版功能必须和普通版完全对齐"。批次1 先把桌面版四类可靠性能力对等搬到浏览器端 Agent（v8.3 任务级快照+只读报警、v8.2 版本快照、依赖树校验、上下文守门）。

### 模块划分
- **app/snap_bridge.py（新）**：FastAPI router（prefix `/api/bridge`），复用 desktop 模块（同机进程内）暴露浏览器端可调端点：
  - guard 只读报警：GET/POST `/guard`；**状态按用户名隔离** `_STATE={username:{readonly,reason,round_id}}`（不与桌面 `session_snap._READONLY` 互相干扰）；守卫锁 `_STATE_LOCK` 防并发
  - checkpoint 文件级版本：POST `/checkpoint/save`（content 空串时后端直读原文件兜底；`_CK_MAX_BYTES=50MB` 超限跳过；`\x00` 前 4096 字节判定二进制跳过防损坏备份；新文件 bak=None）、GET `/checkpoint/files`/`/checkpoint/versions`、POST `/checkpoint/restore`（bak_path 必须位于 CHECKPOINT_DIR 内，越界 403）
  - sessions 任务级：POST `/sessions/begin`（置激活轮）、`/input`、`/tool_call`、`/rollback_point`、`/rollback_points`、`/rollback/{round_no}`、POST `/sessions/{rid}/commit`（guard 置位期间 commit 自动 `keep_rollback=True` 保留回退点；**finally 中仅当 `_active_round(user)==rid` 才清空**，防交错 commit 误清他轮）、GET/DELETE `/sessions`、POST `/sessions/{rid}/restore`
  - tree 依赖树：GET `/tree/scan`（**60s 短时缓存** `_TREE_CACHE` 按 工作区|用户 键控，防大工作区连续对话全量重扫）、POST `/tree/update`（size 由后端 `_resolve` 读真实字节数）、POST `/tree/expert_report`（写激活轮 meta.tree）、GET `/tree/inject`
- **app/snap_util.py（新）**：`apply_expert_report(rid, rel, deps, min_size)` 应用专家报告到激活轮 meta（locked 由 dep_tree.expert_report 内部强制置 True，无外部参数）。
- **static/js/agent.js**：
  - `SnapRound` 轮次生命周期（begin/setInput/toolCall/commit 全走桥，fire-and-forget 失败静默）
  - `gateHistory(history,userText,signal)` 上下文守门：每 2 条一块→llmChat 非流式裁决 keep uid→装配，保留 messages[0] system；**signal 接入 abortCtrl**（停止按钮对守门阶段生效）；失败回落不阻塞
  - `treeProblemsText(signal)` 依赖树注入：有 problems→emit guard_alert + POST guard on=true + 追加 system 警告段；**无 problems 且 guard 已置位→自动 POST on=false 解除 + emit guard_clear**（防 AI 修复后反复空转）；signal.aborted 提前返回
  - `runSession` 接线：begin→setInput→（ENABLE_CTX_EXPERT 时 gateHistory 重建 messages）→（ENABLE_DEP_TREE 时 treeProblemsText 追加 system）→工具循环每步 SnapRound.toolCall→finally SnapRound.commit；**try 前检查 Agent.aborted 走取消语义**（防守门/扫描阶段停止误报 run_done）
- **static/js/tools.js**：
  - `snapCheckpointBefore(rel,cfg)`：**不再用 exists() 前置门**（bridge 模式 exists() 走 /fs/read，>2MB 413 误判不存在导致大文件永远无备份）→ 直接 POST save 由后端兜底；返回 bak（空=新文件）
  - `snapTreeAfter(rel,op,cfg)`：写后/删后增量更新树（size 后端读）
  - `guardBlocked()/ensureWritable()`：只读拦截（查 GET /guard）+ 写前快照
  - 写工具全接：write/edit（ensureWritable→snapTreeAfter）、delete（guardBlocked→snapCheckpointBefore 删前快照→snapTreeAfter 'delete'）、mkdir/rename/run_command/app_screenshot（guardBlocked）
- **static/js/chat.js**：
  - 事件：`guard_alert`→showGuardBanner（**去重 `_guardBanner` 单例**）、`guard_clear`→hideGuardBanner、`ctx_gate`→note-chip
  - `showGuardBanner(reason)`：横幅（查看快照并恢复 / 解除只读=dismiss POST guard on=false）；`restoreGuardState()` 页面加载/刷新后恢复横幅（后端按用户持久）
  - `openSnapshotDialog`：版本回退（checkpoint files→选版本→**恢复前先 checkpoint/save 备份当前内容**（P1-3，content 空串走后端直读）→restore→writeFile→snapTreeAfter 刷树）+ 轮次回退（sessions→restore）
- **core.js/panels.js**：CFG_DEFAULT + SWITCHES 加 ENABLE_CHECKPOINT/ENABLE_SESSION_SNAP/ENABLE_DEP_TREE/ENABLE_CTX_EXPERT（默认 true）。
- **style.css**：.guard-banner/.snap-row/#snap-files/#snap-sessions 样式。

### 关键技巧
1. **后端直读备份**：前端不读内容（省大文件回传 + 绕 2MB 前端限制），POST content='' 由后端 `_resolve` 后 `read_bytes` 判二进制再 `decode`；新文件返回 bak=None 静默。
2. **按用户隔离状态**：`_STATE` 以 username 为键，`_user_state(user)` 惰性建项；async handler 单线程 + GIL 下 dict 操作原子，`_STATE_LOCK` 防御性串行。
3. **guard 自动解除**：校验通过（problems 空）时若 guard 仍置位自动 POST on=false + emit guard_clear——问题修复后 AI 不空转；commit 时 guard 置位则自动 keep_rollback 保留回退点。
4. **commit 清空条件**：`if _active_round(user)==rid: _set_active_round("")` 放 finally——异常路径也清理、防并发交错 commit 误清他轮。
5. **tree/scan 缓存**：`_TREE_CACHE[(ws|user)]=(ts,tree,problems)` 60s TTL，减少连续对话全量扫描。

### 冒烟验证（_smoke_js85.py / _smoke_snap85.py 保留）
- JS 括号配平（状态机词法正确处理字符串/注释/正则不误伤 URL）+ 关键函数存在性。
- 后端快照桥：guard 置位/读取/解除 + 多用户隔离、checkpoint 空 content 兜底/新文件跳过/越界 403、sessions begin→input→tool_call→rollback_point→commit（guard 置位期间保留回退点）→active 清空、tree scan/update/inject。
- 四套回归全绿：test_smoke / test_lite / test_desktop / test_gui。

### v8.5 批次1 故障检测修复（子代理审查两轮）
- 第一轮 P1×4：写工具补 guardBlocked（mkdir/rename/run_command/app_screenshot）、按用户隔离状态、恢复前先备份当前内容、guard 置位后永不自动解除。
- 第二轮发现"宣称已修复但实际未落盘"（SearchReplace 并行编辑部分丢失的教训）→ 重新串行应用：treeProblemsText 自动解除+signal+guard_clear、chat.js guard_clear case + 恢复前备份；另修 P2-1 commit 清空条件、P2-3 去 exists() 前置门（>2MB 误判）、P2-6 commit 异常路径清理、P2-7 二进制/超大文件跳过、P2-9 删 locked 死参数、P2-10 停止阶段不误报 run_done、P2-8 tree/scan 60s 缓存。

## v8.4 Python 应用截图 + UI 自截图确认（2026-08-13）

### 需求背景
- 网页/HTML 已可 browser_screenshot 截图自检，但 Python 应用（PyQt/Tkinter 等本地 GUI）无截图能力。
- 要求：AI 制造本地 UI 后自己截图查看并确认效果（必要时找视觉专家）；Builder/Experts 形态都要有；视觉专家默认不调用，仅当主模型无视觉能力（无 caps.image）时才调。

### 模块划分
- **app_shot.py（新）**：`app_screenshot(script, out_path, timeout, title_hint, python, extra_args)`。Windows-only（`os.name != "nt"` 直接明确报错）。`subprocess.Popen`（cwd=脚本目录，CREATE_NO_WINDOW 隐藏控制台，stdout=DEVNULL，stderr=PIPE 供失败回吐尾部诊断，stdin=DEVNULL 防 input() 阻塞）；`find_window_pid(pid, title_hint)` 用 ctypes EnumWindows + GetWindowThreadProcessId 找可见窗口（可选标题过滤）；`_grab` 用 Qt QScreen.grabWindow 截图——**跨线程安全**：通过 `_GrabBridge(QObject)` + `QMetaObject.invokeMethod(bridge, "_do", BlockingQueuedConnection, Q_ARG("long long", hwnd), Q_ARG("str", out))` 把抓图投递回主线程事件循环执行（Agent 跑在 QThread，直接在工作线程访问 QScreen 属未定义行为，可能黑屏/崩溃）；`bridge.moveToThread(app.thread())` 必须设置否则 invokeMethod 不会投递；无 QApplication 时不硬造实例（非主线程构造 QApplication 可能 qFatal）直接返回 False。超时轮询 `_wait_and_shoot`（截图失败不直接放弃，稍等重试）；finally 用 `taskkill /PID /T /F` 连子进程树一起杀避免孙进程孤儿。`MAX_IMG_BYTES=3MB` 截图大小上限。
- **tools.py 新增两工具**：
  - `app_screenshot`（ENABLE_APP_SHOT 开关）：参数 script/path(.png/.jpg)/title/timeout/extra_args；resolve_ws 校验路径 + 脚本必须存在；**走审批门**（`_request_approval`，与 run_command 一致：danger 模式纯脚本路径默认放行、all 模式需用户确认）；timeout int() 容错。
  - `ui_review`（ENABLE_UI_REVIEW 开关）：参数 path/prompt；读截图文件 base64（**先校验 ≤MAX_IMG_BYTES** 防打爆多模态 API）→ 构造 OpenAI 多模态 messages（content 数组 text+image_url data:image/png;base64）→ `chat_complete(llm_cfg, max_tokens=800, timeout=45.0)`。
  - **视觉专家模型选择（用户要求默认不调）**：`get_model(cfg.model)` 主模型 caps.image=True → 直接用主模型；否则 `get_visual_expert_model(cfg)`（配置优先→注册表第一个 image 模型）；两者都无 → 明确报错"请在设置中配置支持图像的模型"（不让 AI 误以为审查成功）。content 非 str 时 str() 兜底（推理型模型可能返回 None/list）。
  - build_tool_defs：开关 + readonly（chat/experts 非专家形态）都不暴露；TOOL_MATCH 别名扩充（应用截图/窗口截图/GUI截图/视觉审查/审查截图等）；两工具加入 **BASE_TOOL_NAMES** 保证 token 计费裁剪下不丢（与 UI 自检纪律配套）。
- **models.py 新增**：`list_vision_models()`（caps.image=True 条目）；`get_visual_expert_model(cfg)`（cfg.visual_expert_model 优先→注册表第一个 image 模型，异常兜底 None）。
- **config.py 新增**：ENABLE_APP_SHOT=True / ENABLE_UI_REVIEW=True / visual_expert_model=""。
- **agent.py 提示词注入**：`_build_system_prompt` 末尾注入【UI 自截图确认纪律】——"制造本地 GUI 后必须 app_screenshot 截图自检；截图后主模型能看图直接看，不能看图则 ui_review 调视觉专家审查，按意见改进后复验，未截图确认不得宣称 UI 完成"。条件：`(ENABLE_APP_SHOT or ENABLE_UI_REVIEW) and mode != "chat"`。mode 上提到函数开头（避免 expert_id 分支 NameError）。
- **settings_dialog.py 接线**：专家团 tab 新增"Python 应用截图 / UI 自截图确认"组——cb_app_shot/cb_ui_review 开关 + cb_visual_expert 下拉（list_vision_models，空项=自动兜底）+ 刷新按钮 `_vision_reload`；_load/_save 双向。

### 关键技巧
1. **跨线程 Qt 抓图**：Agent 在 QThread 内，asyncio.to_thread 又进线程池 → 工作线程直接调 QScreen.grabWindow 是跨线程访问 GUI 对象（未定义行为）。用 `_GrabBridge(QObject)` 槽 `_do` + `invokeMethod(BlockingQueuedConnection)` 同步投递主线程执行；`moveToThread(app.thread())` 是投递生效的关键。
2. **审批门复用**：app_screenshot 执行任意脚本等同 run_command，走 `_request_approval`（approval_mode=danger 对纯脚本路径 is_dangerous 为 False 自动放行，all 模式需用户确认）——既尊重用户"运行脚本截图 OK"的授权，又不绕过全局审批模型。
3. **多模态图片传参**：`chat_complete` 通过 httpx json=payload 原样序列化，content 数组（text+image_url）逐字传给 OpenAI 兼容端点；`get_llm_cfg` 正确替换 model/url/api_key。
4. **只读/形态双层过滤**：`_readonly_block`（快照报警态，app_screenshot 有、ui_review 纯只读不设）与 build_tool_defs 的形态只读（chat/experts）互补。

### v8.4 冒烟验证（_smoke_v84.py / _verify_v84.py 跑全绿后删除）
- app_shot 模块导入+参数校验（脚本不存在报错、非 Windows 报错）。
- 工具注册三层贯通：ENABLE_APP_SHOT/ENABLE_UI_REVIEW 开关、readonly（chat 形态）、TOOL_HANDLERS/TOOL_MATCH 别名、参数键与 handler 解析一致。
- 视觉专家模型选择（配置优先/自动兜底/幽灵配置兜底）。
- agent 提示词注入三形态（builder 注入/chat 不注入/专家实例不崩）。
- settings_dialog offscreen 构造 + load/save 双向（config 落盘重定向临时路径防污染）。

### v8.4 故障检测修复（子代理审查）
- P1-1 跨线程 Qt：_grab 直接在工作线程 grabWindow → _GrabBridge 投递主线程。
- P1-2 免审批执行脚本：tool_app_screenshot 补 _request_approval 审批门。
- P1-3 孙进程孤儿：taskkill /T /F 连树杀。
- P1-4 base64 打爆 API：MAX_IMG_BYTES 3MB 前置校验。
- P2 顺带修复：ctypes argtypes 补全（GetWindowThreadProcessId/ShowWindow）、非 Windows 明确报错、截图失败重试、stderr 尾部诊断回吐、stdin=DEVNULL、timeout 容错、content 非 str 兜底、TOOL_MATCH 别名扩充、BASE_TOOL_NAMES 收录新工具、docstring 与实际平台一致。

### v8.4 网页版落地（web_main/app+static，2026-08-13）
- **app/bridge.py**：POST /api/bridge/app_screenshot（复用 desktop/app_shot.py 延迟导入——ImportError 归 501"当前环境不支持应用截图"、模块自身 bug 归 500；wait_for(timeout+60) 防永久悬挂；extra_args 必须 list、≤32 项、每项 str≤512、title≤200）+ GET /api/bridge/fs/image（文件头魔数判定 PNG(\x89PNG)/JPEG(\xff\xd8\xff)，非图片 415，≤3MB 413）。
- **static/js/tools.js**：app_screenshot（命令桥未授权报错、路径后缀正则、审批门带 command 展示字段防盲审、透传 signal）/ ui_review（读图 base64→多模态 messages→llmChat 指定 model）；getToolDefs 按 ENABLE_APP_SHOT/ENABLE_UI_REVIEW=false 裁剪。
- **static/js/agent.js**：llmChat 增加 model 参数覆盖（body.model = model || cfg.model，requireLlmConfig(model) 校验生效模型）；**修复 P0**：非流式分支 content 无条件累加（原 `if (... && onDelta)` 门控导致 ui_review/compressMessages/AOE 全部拿不到内容）；提示词纪律改为"必须调用 ui_review 让视觉专家审查"（不诱导"直接看图"）。
- **static/js/core.js + panels.js**：CFG_DEFAULT 加 ENABLE_APP_SHOT/ENABLE_UI_REVIEW/visual_expert_model；SWITCHES 两项 + 设置面板视觉专家模型输入框（提示与主模型共用 API Base/Key）+ collectSettings 保存。
- 网页版桥冒烟（_smoke_web84.py/_verify_web84.py 跑全绿后删）：未授权 409/401、参数 400、脚本不存在 404、越界 403、extra_args/title 校验、魔数 415、PNG/JPEG 正常。

### v8.4.1 命令行 Agent（cli_main.py，2026-08-13）
- **复用内核**：`Agent(cfg, emit, history, vault, approval)` 直接 async 调用，无 QThread/GUI；工具/上下文压缩/专家团/审批门全部复用桌面版，功能天然对齐。
- **事件渲染**：`_emit` 处理 subagent 嵌套事件展开；text_delta 流式写 stdout；tool_start 预截断大字段再 dumps（防 content 数十 KB 全量序列化）；approval_needed 在 emit 内 resolve（future 于 emit 之前创建，时序安全）。
- **审批交互**：`asyncio.to_thread(input)` + 会话级 `asyncio.Lock` 串行化（专家并行审批抢 stdin）；EOF/KeyboardInterrupt/CancelledError 均按拒绝，绝不悬挂 future（内层 try + tools.py BaseException 双保险）。
- **取消**：Ctrl+C → asyncio.CancelledError → run_round 捕获设置 `_cancel`；finally 统一同步历史（取消/异常路径也保存已产生部分），内存截断 200 条。
- **健壮性**：vault 构造失败降级 None 不崩启动；`sys.stdout.reconfigure(errors="replace")` 防 GBK 下特殊字符 UnicodeEncodeError；历史原子写（tmp + os.replace）；no_color 用 `_COLOR_KEYS` 显式白名单替换颜色类。
- **冒烟**（_smoke_cli.py/_verify_cli.py 跑全绿后删）：工具闭环（write_file）/ 历史落盘原子写 / 审批解析 / vault 降级 / 特殊字符渲染。

## v8.3 任务级快照与只读报警大架构（2026-08-12）

### 模块划分
- **config.py 新字段**：ENABLE_SESSION_SNAP=True / session_snapshot_max_mb=3072（总上限 MB）/ round_keep_rollback=2（轮内回退次数）/ tree_update_at_round_end=True（tree&完整快照更新时机，False=回合开始时）/ ai_decision_delay_s=0（默认问答延迟）/ ENABLE_DEP_TREE=True / ENABLE_SUGGEST=True。
- **session_snap.py（新）**：模块级只读状态机 `_READONLY/_ALERT_REASON/_CURRENT_ROUND`；`set_readonly/is_readonly/alert_reason/set_current_round/current_round`；round 生命周期 begin_round→set_round_input→add_tool_call（记工具调用）→tool_rollback（写前快照文件旧内容，按 round_no 编号）→commit_round（zip 打包+标题+todo+devlog+tree+清理轮内+_gc_sessions 总上限淘汰最旧）；`rollback_points/restore_rollback_point` 轮内最多 round_keep_rollback 次回退点；`pack_workspace` 用 zipfile 压缩整个工作区（EXCLUDE_TOP={backups,data,sessions,.git,__pycache__,node_modules,dev_log,updates,static} + EXCLUDE_SUFFIX={.pyc,.pyo,.log,.tmp,.bak}）；`list_sessions/get_session/delete_session/restore_session`（zip-slip 防护：条目名 `..` 直接跳过 + `(root/name).resolve()` 越界校验）；`generate_title(rid,cfg,llm_fn)` AI 生成标题（兜底=工具名+时间）；`update_tree(rid,rel,size,op)` 轮内增量更新树。
- **dep_tree.py（新）**：正则 `_IMPORT_RE`（import-from/裸 import/from import/require）扫描 .py/.js/.ts/.jsx/.tsx/.mjs/.cjs；`_resolve_rel(spec, base_rel, root, require_exists)`——相对导入（./x）缺失由 check_tree 报；裸模块名 require_exists=True 时仅在 root 下候选文件真实存在才记依赖（避免第三方库误报）；候选 = 原路径 + with_suffix(.py/.js/.ts/.pyw/.mjs) + index.py/js/ts；`check_tree` 校验"被依赖文件缺失 + 大小<min_size"；min_size 自动=0.3×当前大小（容差防误报），`expert_report` 可覆盖并锁 lock；`update_on_file_op` 写=更新 size/min_size、delete=移除节点并清引用；`serialize` JSON 序列化（随快照 meta 保存）。
- **suggest.py（新）**：SUGGEST_SYSTEM 提示词（功能性/技术性/美术性 + value 0-10）；`build_context(cfg, workspace, current_input)` 读 Design/Techniques/Fact/Future/FreqErr（每文件截断）；`filter_dedupe(items, max_items=6)` 按类均衡限量；`_strip_json` 剥离 JSON 代码块；`generate_suggestions` 异步生成、llm_fn 可注入。
- **quest_panels.py 追加**：GuardBanner（报警横幅：title/reason + 查看快照并恢复 / 解除只读，restore_clicked/dismiss_clicked 信号）；TaskManagerPanel（四层树 L1 总司令→L2 专家（title·model·params·status）→L3 只读 Agent/工具→L4 思考；`set_round/set_status/set_state`；默认只展开 L1，其余点击展开）。
- **themes.py 追加**：guardbanner QSS（背景用 `p['hover']`，色板无 err_dark 键）+ suggestbox QSS。
- **panels.py 追加**：HealthDashboard 增 `p0_alert = pyqtSignal(str)`，`_render` 中健康度<40 或 err_1h≥10 时 emit（P0 报警源之一）。
- **tools.py 钩子**：`_readonly_block(tool_name)` 只读时拒绝写工具（write/edit/delete/run_command 全检查）；write/edit 前 `snap_mod.tool_rollback(rel, old_content)` + `add_tool_call`（轮内回退点）；write/edit 成功、delete 后 `snap_mod.update_tree` 增量更新。
- **gui.py 接线**：`_SuggestThread(QThread)` 后台 asyncio 生成建议（result_ready 信号）；`_send` 时 begin_round→set_current_round→set_round_input→task_panel.set_round("建立备份中")→_maybe_start_suggest；事件泵新增 `guard_alert` 分支（copilot kill→_guard_alert、run_final/run_error/run_cancelled→_finish_round）；`_guard_alert/_guard_dismiss/_guard_restore/_finish_round`；`_finish_round_worker` 后台线程扫描树→校验报警→commit_round→清 current_round；GuardBanner restore_clicked→VersionRestoreDialog（文件版本回退 + 轮次快照）；health_panel.p0_alert→lambda 适配→_guard_alert。

### 关键技巧
1. **只读状态机放模块级**：`session_snap._READONLY` 模块级标志，任何线程可直接读写，写工具入口 `_readonly_block` 统一拒绝——报警→只读不依赖 UI 线程，后台 agent 线程同样被拦截。
2. **轮内回退点（最多 round_keep_rollback 次）**：`tool_rollback` 把"某次工具调用前的文件内容"写入轮目录 `data/sessions/{rid}/rollback/snap_N.json`；`restore_rollback_point(rid, round_no, ws)` 恢复指定调用点并校验路径；commit 后整轮 rollback 目录删除。
3. **zip-slip 防护**：解压前逐条检查条目名（含 `..` 直接跳过）+ 解压路径 `(root/name).resolve().is_relative_to(root)` 校验，与 tools.resolve_ws 对称，防恶意快照逃逸工作区。
4. **健康 P0 双参数信号适配**：p0_alert 单参数信号用 `lambda msg: self._guard_alert("健康仪表盘", msg)` 适配双参数 _guard_alert(source, reason)。
5. **建议时机**：在 `_send` 用户消息发送完成时启动后台线程，展示在消息输入区上方——用户刚输入新想法、且此刻不能发消息，正是建议展示窗口。
6. **GUI 后台线程只发事件**：_finish_round_worker / _SuggestThread 都不直接操作控件，只 `ui_queue.put` 或 pyqtSignal，规避 QThread 跨线程访问崩溃。

### v8.3 冒烟验证（smoke_v83.py 已跑全绿后删除）
- GuardBanner show_alert/clear_alert；TaskManagerPanel 四层树 set_state；HealthDashboard P0（err_1h=12 触发）；SettingsDialog g_snap 组 load/save 往返（cb_snap/sp_snap_max 等）；session_snap+dep_tree 全流程（begin→rollback→commit→list_sessions→tree 缺失检测→只读）。

### v8.3 第二轮完整化（缺口补齐，2026-08-12）
- **缺口1 快照展开详情**：RoundRestoreDialog._on_sess_click 用 get_session 取完整 meta，detail 展示完整输入+todo+devlog+依赖树摘要（deps/size/min_size，isinstance 防御损坏条目）；历史会话列表只列 committed。
- **缺口2 删除前向前遍历推荐**：_do_delete_sess 的 P0 分支只找比当前会话 ts 更早的快照，用快照自身文件集合做自洽校验（不依赖当前工作区，防误判），推荐"文件正常无缺失"的最近一个；sess 不存在（外部删除）时提示刷新。
- **缺口3 任务管理器实时四层树**：gui 维护 _expert_runtime（title→{title,model,params,status}）+ _expert_eid_map（eid→title）；_refresh_task_panel 从 runtime 构造 experts 调 set_state；expert_plan 初始化、expert_start 更新 model/params/status、expert_status 更新 status。
- **缺口4 状态流**：_send"建立备份中"→ run_start"建立连接中"→ expert_plan"执行中"→ round_done"空闲"（报警只读时保持只读态）。
- **缺口5 问题注入专家上下文**：inject_problems 按 rel 维度存储（meta.inject[rel]），begin_round 把上一轮 committed 会话的 inject 迁移到新轮（跨轮透传，解决"写读轮次错位"）；专家执行时按自己申报 files 过滤注入（get_inject(current_round()) 中 rel∈own 或 own 前缀匹配），追加【依赖树警告】段；locks.owner_of "*" 通配降为最低优先级（先精确后通配）。
- **第二轮故障检测修复**：P1-1（注入跨轮透传+按 rel 存储）、P1-2（owner_of * 优先级）、P2-1（round_done 事件 UI 线程复位状态+清 runtime）、P2-2（推荐只取更早+自洽校验）、P2-3（损坏 meta isinstance 防御）、P2-4（tree 校验与 commit 分 try，快照不可丢）、P2-5（sess None 防御）、P2-6（workspace 有效性判断，防扫 cwd）。
- **第二轮冒烟**（smoke_v83d.py 全绿后删）：注入跨轮透传/按专家过滤、owner * 优先级、删除审查自洽+推荐、round_done 复位、worker ws_valid；GUI offscreen 全实例 + _refresh_task_panel + round_done 事件。

## v8.2 版本快照回退（2026-08-12）
- **快照存储（desktop/checkpoint.py）**：`data/checkpoints/{task_id}/{safe_name}.{ts}.{hex}.bak` + `.meta`（rel_path/ts/task_id/source）；`save_checkpoint(rel, content, task_id, source)`；source ∈ ai（工具写入）/human（编辑器保存）/model/restore（回退）；rel_path 入口统一 `replace("\\","/")` 保证人类/AI 同组。
- **聚合接口**：`_scan()` 读全部（按 (ts, bak_path) 升序稳定）；`list_files()` 按文件聚合（供对话框左列）；`list_versions(rel)` 单文件倒序；`source_name()` 中文映射；旧 meta 无 source→ai、无 rel_path→跳过（脏数据防聚合到 "?"）。
- **保留策略**：每文件 `_MAX_PER_FILE=20` 版 + 全局 `_MAX_TOTAL=600` 版，`_gc()` 删最旧（.bak+.meta 配对）；meta 写失败回滚 .bak 防孤儿。
- **接入**：tools.py write/edit 写前快照（source=ai，空文件也快照 `p.exists()`）；gui.py EditorWidget.save_current 仅 dirty 时快照（source=human）；恢复前快照（source=restore）可二次回滚。
- **恢复链路安全**：`_restore_checkpoint_cb` 路径越界校验（`(root/rel).resolve()` 的 parents 检查，与 tools.resolve_ws 对称）防 meta 篡改逃逸工作区；恢复后 `_reload_changed` 同步已打开编辑器 tab（防 stale 内容被 Ctrl+S 覆盖）。
- **UI**：VersionRestoreDialog（ide_extras.py）左文件列表（含版本数）/ 右版本列表（时间+来源）+ 只读预览 / "恢复此版本"（确认→回调→`_load_files(select_rel)` 回跳）；入口：AI 菜单 + Ctrl+Shift+P 命令面板。

## v8.1 Embedding 级别匹配（2026-08-12）
- **三级引擎（desktop/matcher.py）**：`rank(query, docs, cfg, min_score, limit)` 批量 / `similarity(query, doc, cfg)` 单条；级别 `cfg.embedding_level`：char（字符 bigram 余弦×0.7+包含度×0.3，原 vault._similarity 移入）/ bm25 / api。
- **归一化 BM25（关键）**：`score = Σ_q idf(q)·qtf·tf_norm(q) / Σ_q idf(q)·qtf`，分子分母同为 query 词项加权 → 分数天然 ∈[0,1]，与 char 阈值（0.35/0.45/0.5/0.75）语义兼容；tf_norm = tf(k1+1)/(tf+k1(1-b+b·dl/avgdl))，k1=1.5 b=0.75；单文档退化 char。
- **api 级别**：注册表 `kind=embedding` 独立条目（`models.get_embedding_model(cfg)` 优先 cfg.embedding_model）；`api_embed` 同步 httpx.post {base}/embeddings（OpenAI 兼容），分批 128、缓存 OrderedDict 上限 2000（LRU）、超时 8s、按响应 item["index"] 对齐；rank 中 docs 超 512 自动降级 bm25；失败/未配置静默降级，链路不中断。
- **接入 5 点**：vault.search（复用拦截）、tools.select_tools（裁剪 0.35）、tools.tool_search_tool（0.25）、toolsmith.find_duplicates（DUP_THRESHOLD）与修复钩子、search_tools 文件名打分（0.30）——全部从单条 _similarity 改为 matcher 批量。
- **UI**：设置对话框"Embedding 级别匹配"组（开关+级别下拉+Embedding 模型下拉+刷新）；模型注册表表单加"类型 chat/embedding"下拉、列表 [C]/[E] 标记、_models_add 重置 chat；模型弹窗过滤 embedding 条目。
- **config**：ENABLE_EMBEDDING_API=True / embedding_level="char" / embedding_model=""。
- **llm.embed_texts**：异步版 /embeddings（供未来异步调用点；同步走 matcher.api_embed）。

## 技术选型
- UI：Python 3.13 + PyQt5（5.15，已装）—— 普通 Python 桌面程序，无浏览器
- Agent：`desktop/` 包（from 备份恢复的 v1 引擎 + 新写 GUI），后台线程 asyncio loop
- LLM：httpx（OpenAI 兼容 /chat/completions，流式 + function calling）
- 命令：asyncio.create_subprocess_shell（本地直接执行）
- 文件：pathlib.Path 直接读写
- 持久化：JSON（data/config.json + data/desktop_history.json + vault）
- 快照加密：cryptography.Fernet（口令派生）

## 关键实现技巧
1. **命令行直接操作已有文件**：`tool_run_command` 用 `asyncio.create_subprocess_shell(cmd, cwd=工作区, CREATE_NO_WINDOW)`；逐行 `readline` 流式回传 `cmd_output` 事件；EOF 后 `wait_for(proc.wait(),15)` 防假卡死；超时 kill；`except BaseException` 兜底杀子进程（CancelledError 继承 BaseException）。
2. **Agent 线程模型（PyQt5 集成）**：AgentThread(QThread) 内 `asyncio.run_until_complete(consume)`；`_emit` 把事件丢进 `queue.Queue`；UI 线程 QTimer(50ms) 轮询泵出；`submit()` 用 `loop.run_in_executor(None, task_queue.get)` 从 UI 线程投递任务。
3. **审批门线程安全**：ApprovalGate 的 future 属于 Agent loop；UI 弹 QMessageBox 后 `loop.call_soon_threadsafe(lambda: gate.resolve(call_id, approved))` 唤醒，避免跨线程 set_result。
4. **停止**：`Agent.cancel()` 设置 asyncio.Event（线程安全）；Agent loop 每轮检查。
5. **流式渲染**：事件 `text_delta` 追加到 QTextEdit（HTML 转义），工具卡片为富文本 div（运行中→✓/✗），命令输出追加 `<div class=tooloutline>`。
6. **语法高亮**：QSyntaxHighlighter 规则表（python/js/json/yaml/html/css/markdown/shell），按扩展名映射，关键词加粗 + 主题色。
7. **上下文压缩**：`estimate_tokens ≈ len//3`；超阈值时独立 LLM 调用压缩旧消息为 system 摘要，保留最近 N 条；失败不阻塞主流程。
8. **AOE 并行**：规划 prompt 约束输出 JSON（extract_json 容忍 ``` 包裹）；拓扑分层 `_topo_levels`；同层 `asyncio.gather`；每节点 `asyncio.wait_for(timeout=aoe_timeout_s)` 熔断；强制查库复用拦截（命中"复用节点"）。
9. **资产银行**：字符 bigram 余弦(0.7) + 包含度(0.3) 综合相似度；`Vault.search(query, limit, threshold)` 低于阈值不返回。
10. **路径安全**：`resolve_ws` 强制 resolve + 前缀校验；工作区=项目根时 PROTECTED_NAMES={app,data,backups,dev_log,updates,desktop,static}。
11. **肝完睡觉**：`modes.goal_reached` 关键词判定 → SleepDialog 全屏倒计时（QTimer 1s）→ 授权后 `do_power_action(shutdown/hibernate)`。
12. **同步**：build_snapshot → Fernet 加密 → 导出文件 / 推送到 sync_server（X-Sync-Token 可选）。

## v4 新增关键实现技巧
13. **上下文分块与守门（ctx_expert.py）**：`split_blocks(history)` 按 role==user 切块，块 uid = 用户消息 md5 前 16 位；守门调用轻量模型（`expert_model` 或主模型）输出严格 JSON：`{"keep": 保留块数 N（从最新往回数）, "ban": [uid...], "reason": str}`；未保留且未被引用的古早块不进本轮调用（仅压缩进摘要），ban 写入 `data/ctx_bans.json` 永久生效；用户引用（quote）的块无条件强制注入。守门失败不阻塞主流程（FreqErr：协程/异常均不得吞没主对话）。
14. **规则守护**：run 结束后同模型一次非流式调用，检查最后一轮 user+assistant 是否合规，输出 `{"ok": bool, "note": str}`，发 `guard` 事件渲染守护卡片；异常时静默跳过不阻塞。
15. **引用注入**：引用以结构化前缀拼入用户消息：`【引用内容 1】\n...\n【正文】...`；ChatPanel 输入框上方引用条（quote chips）可逐条删除；引用块同时作为强制保留块传入守门。
16. **三形态提示词（agent.py::build_system_for_mode）**：chat 模式禁用主动写文件/命令（系统提示约束 + 只读工具倾向），builder 用现有 BASE_SYSTEM，experts 占位提示（待用户方案）。工具定义在 chat 模式下隐藏 write/edit/delete/run_command。
17. **消息渲染与展示切换**：ChatPanel 每条 AI 消息结束后可切换 预览/源码；实现上不做区间替换（避免夹带工具卡片被抹掉，P1-2 教训），而是追加独立渲染卡：`finish_ai` 记录原始文本，源码/预览切换时以「重新插入整条消息副本」的方式在消息尾部重渲染（有工具卡片时仅切本条文本卡）。
18. **Mermaid/HTML 卡片（md.py）**：`_fence_to_html` 对 lang ∈ {mermaid, html} 输出带标签的样式化源码框 + 提示（本地无 JS 引擎不渲染图表），其余语言不变；全部转义防注入。
19. **主题引擎（themes.py）**：色板 = 语义键字典（bg/panel/editor/text/muted/accent/border/btn/hover/sel/user_bubble/ai_head/ghost…）；`build_qss(palette)` 生成全局 QSS；四内置主题 + `data/theme_custom.json`；导入导出即读写该 JSON；md.py 代码块色使用内联样式（保持可读）。
20. **系统托盘**：`QSystemTrayIcon` + 自绘 QPixmap 图标（QPainter 画圆角方块+字母 D，零资源文件）；closeEvent 若 `minimize_to_tray` 则 `hide()` 并 `e.ignore()`；托盘菜单 显示/隐藏/退出（退出走真实关闭流程 `_real_quit`）。
21. **逐行流式补全**：`CompletionWorker` 改用 `stream_chat` 逐 delta 累积，遇 `\n` 发 `line_ready` 信号；EditorWidget 收到首行后插入 ghost，后续行以 `_applying` 模式追加到 ghost 区（更新标记位置与样式）；用户任何真实编辑（textChanged 且非 applying）即取消 worker 并清除 ghost；键入字符 == ghost 首字符时消费：删 ghost、插入该字符并继续等待下一段。
22. **编辑器智能键（EditorWidget 键处理）**：Enter 自动缩进（取当前行前导空白，上行以 `:` `{` `(` `[` 结尾 +4 空格）；Tab 插 4 空格 / Shift+Tab 减 4；成对符号自动补全 `()[]{}""''`（选区非空时包裹选区）；Backspace 在纯缩进行一次删 4 空格。
23. **同步队列（sync_queue.py）**：`SyncQueue` 维护 pending 原因集合（settings/history）；`mark_dirty(reason)` 入队；`flush()` 在流量模式或非 ENABLE_SYNC/无服务器时挂起/跳过；GUI 在关闭流量模式、保存设置、会话落盘后调用；状态栏显示「同步队列 N」。
24. **设置导入导出**：`Config` 全字段序列化 JSON（含 api_key，仅用户本机文件，UI 提示）；导入时逐 hasattr 字段覆盖并 `save()`。
25. **加载进度**：状态栏 QProgressBar（busy 时 busy 动画）+ 聊天「思考中」提示；文件树刷新显示「加载中…」。防卡死继续遵守：事件泵限流、扫描进 executor（FreqErr 已有条目）。

## v5 新增关键实现技巧
26. **模型注册表（models.py）**：`data/models.json` 存模型列表（含能力/分数/价格/操作说明/高级专家展示配置）；`get_llm_cfg(model_id)` 返回可直接传给 llm.py 的调用配置（空 key 回退全局）；`weighted_rank(models, weights)` 加权排名（b.1），`value_rank` 得分÷价格（b.2，价格用输入输出比折算）；分数项为 `{metric: {value, fixed, updated}}`，未定值 value=None。
27. **租约锁（locks.py）**：内存表 `{resource: {owner, ttl, heartbeat_at}}` + 全局 threading.Lock；`acquire(resource, owner, ttl, timeout)` 轮询等待（asyncio.sleep 0.1 短轮询），超时抛 `AcquireLockTimeout`；`heartbeat(resource, owner)` 刷新时间并重置 ttl；`reap(now)` 纯扫描返回被清理的资源列表（零 LLM）；`force_release(resource)` 供 UI 强制解锁；文件所有权 = 专家级声明清单，写工具在执行前校验 `owner_of(path)`，未申报→拒绝，报错入反馈表单。
28. **写文件强制 CoW**：所有写路径走 `atomic_write(path, text)`：写同目录 `.tmp` → `os.replace` 原子替换；专家工具层禁止其他方式写文件。
29. **工具裁剪（tools.py）**：每个工具定义带 `_match` 关键词列表（手写，含同义词）；`select_tools(cfg, defs, todo_text)`：低内存模式→纯子串包含匹配，否则纯字符 bigram 余弦（复用 vault 相似度函数）取阈值 0.35 以上；**单轨不混用（v6 隐患 4）**：余弦分支不再 OR 子串，避免两策略互相干扰；结果并上 BASE_TOOLS（read_file/search 类）+ search_tool 元工具；裁剪只发生在 token 计费模式下。
30. **专家团编排（experts.py）**：`ExpertTeam.run(user_msg, history)` 总司令首轮产出 JSON 计划（任务列表，每项：摘要/文件清单/模型 id/参数/思考负载）；每专家一个 `Agent` 实例（独立 messages、独立模型配置、`readonly_ctx` 视申报文件而定）；并行用 `asyncio.gather`，串行模式逐个 await；事件统一打 expert_id 前缀供 UI 路由；专家工具集 = builder 裁剪集 + todo_update + 禁止复杂委派（subagent 工具不给专家，仅给只读搜索子Agent）。
31. **反馈汇总**：用 `asyncio.Queue` 收集专家完成事件，空闲数达 `math.ceil(ratio*n)` 时触发总司令核查调用（非流式 JSON），产出反馈表单事件 `expert_form`（含每条：类型/问题/成本估测）；用户回复经 `expert_reply` 工具回注总司令上下文。
32. **副驾驶（事件触发）**：`copilot_check(cfg, event_payload)` 在四个钩子点调用：危险命令审批、工具内容校验命中危险模式、reaper 扫出僵尸锁、总司令申请放开只读；输出 `{kill: bool, note}`，kill=True 则中断对应专家并插系统卡片；模型用 `copilot_model`（空→主模型）。
33. **互联网搜索（search_tools.py）**：三级回退：① 模型自带 web_search（caps.web_search=True 时请求体加开关）② DuckDuckGo HTML 接口（httpx GET，零 key，解析 `<a class=result__a>`，UA 伪装，失败降级）③ 配置了搜索 API key 时走付费接口；结果交助手模型压缩到 ≤1500 token 摘要。`fetch_url` 不预置工具（留给 AI 用 delegate_task 自造，用户确认不急）。
34. **专家卡片与详情（gui.py）**：卡片为 QTextEdit 富文本（三列：摘要|模型负载|状态，省略号+悬停 tooltip 全文）；单击开 `ExpertDetailDialog`（QTreeWidget 面包屑导航，文件管理器风格：根=专家团 > 专家 > 工具调用/todo 层级）；高级专家按展示级别过滤卡片内容（全/部分/少量 = 保留全部/仅状态与摘要/仅状态）。
35. **审批门四模式**：`approval_mode` ∈ all（全弹）/danger（仅危险词命中弹）/copilot（副驾驶代批：恶意危险掐断+系统提示，非恶意放行）/free（全放）；在 ApprovalGate.resolve 前新增路由层，copilot 模式下先 `copilot_check` 再决定是否弹 UI。
36. **端口文档**：`ports.md` 位于工作区根，Markdown 表格（端口/状态/类型/位置/调取方式/调用数）；工具 `port_declare`（声明/更新端口，写前先 acquire(ports.md) 锁）+ `port_read`；改端口定位引用用 `port_refs`（v6：词边界正则扫描工作区返回 文件:行号+片段，明确提示逐处语义确认、禁盲替），不用全局文本替换；ports.md 写锁归总司令调度。

## v6 新增关键实现技巧
37. **自研工具库（toolsmith.py）**：`data/tools.json` 存自研工具（name/desc/kind=prompt|script/impl/params/status/reviewed_at）；`find_duplicates(query)` 用 vault 同款 `_similarity`（阈值 0.5）查重；`build_tool_via_llm` 让模型产出 prompt 模板型工具定义（严格 JSON，双写大括号）；注册前必须 review 通过（status=active）；use_tool 执行 prompt 型（chat_complete 填充模板）/script 型（提示走 run_command）。
38. **工具设计专家编排（experts.py）**：规划提示词增规则——含"造工具"类任务时自动前置"工具查重"任务（deps 挂接，撞名时改"工具查重(前置)"）；parse_plan 后检测任务文本含 造工具/写工具/做工具/建工具 且 ENABLE_TOOLSMITH → 自动注入查重任务并重排 deps；审核发生在专家调用 build_tool 工具内部的 `finalize_tool_build`（tool_review_mode：copilot 用 copilot_check / commander 用总司令模型 JSON 审核，拿不准一律不过审），通过 → toolsmith.register + vault.add（kind=tool 逻辑资产）+ tool_registered 事件卡片，不过 → 不入库 + copilot_block 卡片。
39. **专家团 DAG 分层（experts.py）**：ExpertTask 增 deps；`_expert_levels(tasks)` 拓扑分层（非法依赖丢弃防环）；并行模式按层 gather，串行模式逐任务；下游任务 prompt 注入「依赖成果」段（上游 feedback 截 600 字）；上游失败不阻塞下游（标注缺失）。
40. **专家产物自动入库（agent.py）**：专家 Agent run_task 结束后收集 `_artifacts`（write_file/edit_file 产物）→ 非空则 `vault.evaluate_and_store`（专家自己的 cfg），失败不阻塞；主对话路径维持原有 _maybe_vault_eval。
41. **后台长任务（background.py）**：`BackgroundJobs` 登记表（线程安全 dict + queue 事件）；run_command 预估 >180s 时 `asyncio.create_task` 转后台并发 bg_started 事件，完成发 bg_done（尾部 800 字）；GUI Reaper 定时器（10s，无条件启动）排空事件渲染聊天卡片；低内存模式下不起后台任务（直接串行）。
42. **算力漂移（sync.py/sync_server.py）**：`build_snapshot` 增 runtime 段（在途任务/忙闲/时间戳），config 排除 api_key/sync_password/drift_api_key/search_api_key；GUI 退出/关机/休眠前（ENABLE_DRIFT 且配了服务器）自动 push（含 cold 无口令压缩副本供冷备读取，服务器端免 cryptography）；sync_server 新增 `POST /chat`：读 cold 副本→拼历史→httpx 直连 LLM（凭据来自服务器环境变量 DRIFT_API_BASE/DRIFT_API_KEY 或请求体；桌面端 drift_api_base/key 为预留配置）→回复追加到 cold 副本（标 from_server）并同步回主快照；本地开机 `GET /pull` 回传（解密失败降级按无口令流解包），历史中 from_server 消息按 (role, content[:200], drift_ts) 去重合并入本地会话。
43. **防呆库增强**：`_execute_tools` 中工具返回 ok=False（非仅异常）也调 `err_mirror.record_error`（取 output 前 300 字），使历史报错镜像覆盖审批拒绝/越界/权限类失败。

## UI 布局（PyQt5，v5）
- 中央：QTabWidget 编辑器（多标签，Ctrl+S 保存，dirty 标记）
- 左侧 dock：文件树（懒加载，双击打开，右键新建/重命名）| 资产银行（搜索+详情）
- 右侧 dock：聊天（模式选择 Chat/Builder/Experts + 引用条 + 消息卡片 + 专家卡片 + toggle chips：问答模式/肝完睡觉/单次串行 + 锁状态区含强制解锁）
- 底部 dock：终端（QThread 执行，实时回显，选段可引用给 Agent）
- 菜单：文件 / AI(设置·模型注册表·清空会话) / 工作区；状态栏显示模型、工作区、同步队列与进度条
- 系统托盘常驻；主题四内置 + 自定义 JSON 导入导出；低内存模式少动画少渲染

## v6.2 新增关键实现技巧（双入口 + SVG + 相对路径安全）
44. **网页版启动入口（web_main.py）**：独立于 main.py；`uvicorn.run("app.server:app", host, port)` 启动 FastAPI；`threading.Timer(1.2, open_browser)` 在新线程打开 `http://host:port/`；读 `app.config.get_config()` 的 host/port；不导入任何 desktop/ 模块，保证桌面版 PyQt5 不被加载。
45. **网页版 SVG 图标系统（static/icons/ + static/js/icons.js）**：
    - 把 `tools/export_svgs/` 的 29 个 SVG 复制到 `static/icons/`（内置到分发物）；
    - `icons.js` 提供 `icon(name, size=16, cls='')` 函数：用内置 SVG 字符串映射表（避免运行时 fetch），返回 `<svg ...>` 字符串；`ICONS` 对象按语义键（home/notebook/memo/pen/atom/chart/cog/contrast/folder/upload/trash/lock/unlock/plus/search/user/bell/bot/calendar/download/link/undo/redo/refresh/cloud-ok/cloud-fail/cloud-up/info/close/play/stop）映射到对应 SVG path；
    - HTML 中用 `data-icon="home"` 属性标记需要图标的元素，`icons.js` 在 DOMContentLoaded 时统一注入；JS 动态创建的元素用 `icon('xxx')` 直接拼字符串；
    - 文件类型图标：`fileIcon(rel)` 改为返回 `icon('file-py')` 等 SVG（不再用 emoji）。
46. **桌面版 SVG 图标系统（desktop/icons.py）**：
    - `QSvgRenderer` 渲染 SVG 为 `QPixmap`，缓存到模块级 dict（`_CACHE`）；
    - `svg_icon(name, size=16, color=None)` 返回 `QIcon`；`svg_pixmap(name, size=16, color=None)` 返回 `QPixmap`；
    - SVG 源从 `static/icons/` 读取（与网页版共用资源），桌面版启动时把 `static/icons/` 路径注入；
    - 颜色用 `QPainter` + `QPixmap.fill(color)` 或 SVG 内 `fill="currentColor"` 替换；
    - gui.py 活动栏 QAction 用 `setIcon(svg_icon('files'))`；状态栏 QLabel 用 `setPixmap(svg_pixmap('lock', 14))`；富文本卡片中的 emoji 改为内联 SVG（QTextDocument 的 resource 机制）或纯文本+SVG 图标按钮。
47. **大代号/相对路径安全机制**：
    - 代号生成：`CodenameMap` 类，`assign(path) → "WS-" + 4位hex`（基于路径 hash，稳定）；`resolve(codename) → path`；`to_display(path) → codename 或 相对路径`；
    - 网页版 bridge.py：fs_tree/fs_read 返回的 path 字段在 `ENABLE_CODENAME` 时替换为代号；run_command 输出做路径脱敏（正则替换工作区绝对路径为代号）；AI 请求体中的 path/cwd 接受相对路径，`_resolve` 翻译为绝对路径（已有）；
    - 桌面版 tools.py：工具返回结果在 emit 前做路径脱敏；系统提示词追加"你只能看到文件代号与相对路径，写文件时只能用相对路径，绝对路径由系统翻译"；
    - 系统提示词注入：`buildSystemPrompt()`（agent.js）与 `desktop/agent.py` 都追加相对路径约束段。
48. **网页版 Agent 修复（agent.js）**：
    - AOE 节点超时取消：`runAoeNode` 用 `AbortController`，超时后 `abort()` 触发 runSubagent 内部 SSE 中止；`Promise.race` 改为显式 `clearTimeout` + `controller.abort()`；
    - runSession 错误回滚：错误发生时若已 push user 消息，从 `App.history` pop 出去（保存快照便于恢复），emit `run_error` 后 `saveHistory()`；
    - sleep 判定双因子：`checkSleepGoal` 命中关键词后 emit `sleep_confirm` 事件，UI 弹确认框，用户确认才执行 `sleepExecute`；不再自动触发；
    - 运算符优先级：`if (e && e.name === 'AbortError' || Agent.aborted)` 显式写为 `if ((e && e.name === 'AbortError') || Agent.aborted)`（语义不变，可读性提升）。
49. **双入口启动互斥检查**：`web_main.py` 启动前检测 `desktop` 是否被意外导入（sys.modules 检查），若已导入则警告（避免在桌面版进程内嵌套启动网页版）；`main.py` 不导入 `app.server`，保证桌面版进程纯净。
50. **邮箱注册验证码流程（app/mailer.py + app/security.py + app/users.py）**：
    - SMTP SSL 发送：`smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15)`；凭证从环境变量 `SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS` 读取，不落盘；`send_verification_code(to, code)` 失败返回泛化原因（不泄露主机/用户名）；
    - 验证码生成与存储：`security.generate_code()` 用 `secrets.randbelow(1000000)` 生成 6 位；`users.store_code(email, code)` 存内存 dict（不落盘），TTL 10 分钟；`verify_code` 用 `secrets.compare_digest` 防时序攻击，成功后一次性删除；
    - 限速：`security.check_code_rate(email)` 滑动窗口——同邮箱 1 分钟 1 次、10 分钟 3 次；`check_login_rate(key)` 5 分钟 10 次；`is_ip_suspicious(ip)` 同 IP 5 分钟 6 次失败即标记异常；
    - 审计：`security.audit(action, user, ip, detail)` 追加 JSONL 到 `data/audit.jsonl`，字段截断（user 32 字符、ip 45 字符、detail 200 字符），不记密码/Token；
    - 前端：index.html 三 tab（登录/用户名注册/邮箱注册），邮箱表单含验证码输入+发送按钮（60s 倒计时），main.js `wireEmailRegister()` 编排 send_code → register_email 流程。
51. **项目下载签名链接（app/server.py）**：
    - `GET /api/projects` 返回项目列表 + 每项附带 `download_url`（含 `expire` 时间戳 + `sig` HMAC-SHA256 签名），签名密钥从 `config.secret` 派生；
    - `GET /api/projects/{id}/download?expire=&sig=` 校验签名（`hmac.compare_digest` 防时序）+ 时效（15 分钟）+ 登录鉴权；通过后返回 StreamingResponse ZIP（当前为骨架占位包，后期填充真实项目）；
    - 篡改签名或过期 → 403；未登录 → 401；审计日志记录下载操作；
    - 前端：panels.js `openAccount()` 弹模态展示账户列表（表格，不含 hash/salt）+ 项目列表（卡片 + 下载按钮 `window.open(url)`）+ 数据安全实践清单。
52. **大厂数据安全实践整合**：传输加密（HTTPS/TLS + SMTP SSL 465/993）、存储加密（AES-256-GCM 快照 + PBKDF2-SHA256 200k 迭代密码哈希）、零信任（每请求验证身份 + 路径强制 resolve）、最小权限（AI 代号/相对路径 + 命令审批门 + AI 删除默认禁用）、数据最小化（官方服务器不存代码/会话）、审计日志（JSONL 留痕，不记敏感字段）、限速防护（登录/验证码/异常 IP 三级）、密钥轮换（HMAC secret 自动生成 + 快照口令可换 + 下载签名 15 分钟时效）、多租户隔离（每用户独立账户记录）、备份策略（backups/ + dev_log/）、二次审批（命令审批门四模式 + 副驾驶异常兜底升级）。

## v6.3 新增关键实现技巧（工具医生 + 安全规则注入 + IDE 对标补全）
53. **工具医生（desktop/toolsmith.py + tools.py）**：
    - bug 收集：`use_tool` 失败（ok=False）时 `record_tool_bug(name, reason, inputs)` 追加 JSONL 到 `data/tool_bugs.jsonl`，字段含 `bug_id`（时间戳+随机4位）、`tool_name`、`reason`（前 500 字）、`inputs`（前 200 字）、`status=pending`、`ts`；
    - `list_tool_bugs` 工具：列 pending bug，按工具名聚合显示计数；
    - `fix_tool_bugs` 工具：核心修复链路——读 bug 列表→对每个 bug 取原 tool impl→LLM 诊断（`DIAGNOSE_PROMPT` 给 bug reason+原 impl+example，输出新 impl JSON）→沙箱跑 example 回归（`_regression_test` 用原 example 输入跑新 impl，校验输出非空且无异常）→通过则 `register` 覆盖入库 + `clear_tool_bug` 标 fixed；失败保留原版不覆盖，bug 留 pending；
    - `clear_tool_bug` 工具：标记 bug 为 fixed（写回 jsonl）；
    - 回归沙箱：`_regression_test` 用 `chat_complete` 跑 example，超时 60s，捕获异常即判失败，不阻塞主流程；
    - 开关 `ENABLE_TOOL_DOCTOR`，关闭时 fix_tool_bugs 直接返回未开启提示。
54. **AGENT.txt 安全规则注入（desktop/agent.py + static/js/agent.js）**：
    - `AGENT_SAFETY_RULES` 常量：精简提取 AGENT.txt 核心——相对路径约束（写文件只写相对路径，绝对路径由系统翻译）、文件操作不用命令行（全部内置工具）、错误记录（异常写 Err.log 不吞）、模块开关意识（关闭的模块不调用）、不臆造文件（不确定先 list_dir/glob）、修改前先读（read_file 确认原文）、edit_file 精确改（不整文件覆盖）、重要文档（Design/Techniques/Fact 等）只读；
    - `desktop/agent.py::_build_system_prompt` 末尾追加 `AGENT_SAFETY_RULES`，对 builder/experts/专家实例统一生效；
    - `static/js/agent.js::buildSystemPrompt` 末尾追加精简版（去掉桌面版特有项），网页版 Agent 同步受约束。
55. **@-mention 引用文件（desktop/gui.py ChatPanel）**：
    - ChatPanel 输入框 `textChanged` 检测光标前最近 `@`，触发 `FileMentionPopup`（QListWidget popup）模糊匹配工作区文件（复用文件树缓存）；
    - 选中后在输入框替换 `@xxx` 为 quote chip（复用现有 quote chips 机制），AI 收到的引用内容含文件相对路径与内容；
    - `@` 后无输入时显示最近打开文件；Esc 关闭 popup。
56. **命令面板 Ctrl+Shift+P（desktop/gui.py）**：
    - 全局 QShortcut(`Ctrl+Shift+P`) 弹 `CommandPalette`（QDialog + QLineEdit + QListWidget）；
    - 命令清单：跳转文件（输入文件名模糊匹配，回车打开）、切换主题（obsidian/paper/beige/blue/custom）、切换 Agent 模式（chat/builder/experts）、切换工作区（弹目录选择）、打开设置、清空会话、切换流量/Token 模式；
    - QLineEdit `textChanged` 实时过滤 QListWidget，回车执行；轻量实现不引新依赖。
57. **AI Checkpoint（desktop/tools.py + desktop/checkpoint.py）**：
    - `desktop/checkpoint.py`：`save_checkpoint(rel_path, content, task_id)` 把原文件内容写到 `data/checkpoints/{task_id}/{rel_path}.{ts}.bak`；`list_checkpoints()` 返回最近 N 条；`restore_checkpoint(bak_path)` 恢复；
    - `tools.py` 的 `tool_write_file`/`tool_edit_file` 在写入前调用 `save_checkpoint`（开关 `ENABLE_CHECKPOINT`）；
    - ChatPanel 右键菜单"恢复到 AI 改动前"列出最近 20 条 checkpoint，选中即恢复（恢复本身也走 checkpoint，可二次回滚）。
58. **Notepad 暂存（desktop/notepad.py + tools.py）**：
    - `desktop/notepad.py`：`save(key, content)`/`read(key)`/`list_keys()`/`clear(key)`，存 `data/notepad.json`（单文件多 key）；
    - 工具 `notepad_save`/`notepad_read`/`notepad_list`/`notepad_clear`，Agent 跨轮暂存中间结果；
    - 低成本：纯 JSON 读写，无新依赖。

## v8 新增关键实现技巧（Quest 风格面板管理，2026-08-12）
59. **右缘图标条 + Pannel dock（desktop/gui.py + quest_panels.py）**：QToolBar(RightToolBarArea, objectName=rightstrip, 40px) 的 checkable QAction（概览/文件/终端/资产）与 QDockWidget(dock_panel) 内 QTabWidget 页一一映射；`_toggle_panel` 同页再点收起、异页切换并同步勾选态；dock 分割条原生左右拖动改宽（resizeDocks 初始 [430,340]）；移除旧左活动栏/dock_left/dock_bottom，部件（FileTree/VaultPanel/HealthDashboard/TerminalPanel）全部迁入 Pannel 页。
60. **概览折叠分区（quest_panels.SummaryPanel/Section）**：QScrollArea 内 3 个 Section（▾/▸ 头按钮切换 content.setVisible）；Progress 直接嵌入 HealthDashboard 实例（零复制）；Artifacts 读 `vault.list()[:50]`（.get 容错）；References 读 `chat._quotes`；刷新时机=切页(currentChanged)/同页重开/vault_stored 事件且 dock 可见。
61. **StatusRow 兼容层（quest_panels.py）**：QWidget 行（左名 QLabel + 右状态 QLabel），左键 mousePressEvent 翻转并发 `toggled(bool)`；暴露 `setChecked/isChecked`（setChecked 不 emit，与 QPushButton 语义一致）→ 旧 sleep_chip/serial_chip/qa_chip 全部接线零改动；状态色用动态属性 `QLabel[on="true"/"false"]` + `style().unpolish/polish` 即时刷新 QSS。
62. **模型选择弹窗（quest_panels.ModelPickerPopup）**：Qt.Popup 无边框窗（点击外部自动关）；状态行区（外部 StatusRow 经 `add_status_row` 注入顶部 + 内置流量节省/低内存行）；Pilot/Copilot 分段按钮（Pilot→cfg.model，Copilot→cfg.copilot_model）；模型列表 `load_models()` 行内 $ 徽章 + `in:out` 价比（仅付费模型显示）；`+` 按钮先 hide 再 emit manage_requested（防 Qt.Popup 压住模态设置框）；定位按 `primaryScreen().availableGeometry()` 四向钳制，上方空间不足落按钮下方。
63. **ChatPanel v8（gui.py）**：hero 欢迎区 HeroWidget（QPainter 圆底 + atom 图标，`update_palette` 随主题重绘；`add_user/new_ai` 隐藏、`clear()` 复位）；卡片输入框 `#inputcard`（inp 无边框 64px + 工具条：plus_btn 引用挂载/ mode_combo 形态/ model_btn ∨/ stop_btn/ send_btn 图标按钮）；`attach_requested` 信号 → 主窗口 QFileDialog + 50KB 截断 add_quote（与 @-mention 上限对齐）。
64. **v8 QSS 块（themes.py build_qss）**：rightstrip/inputcard/flatbtn/flatcombo/statusrow/seg(:checked)/segplus/moneybadge/ratio/sechead/herotitle(26px 700)/herosub(等宽 12px)/modelpopup/sumscroll/popsep，全部从色板取色，f-string 大括号双写转义。
65. **状态回同步**：`_open_settings` 接受后按 cfg 回设 row_traffic/row_lowram/sleep_chip/serial_chip + `_refresh_model_label()`；`_apply_theme` 图标重建清单补 v8 新增按钮（plus/stop/send/btn_plus）。

## v8.5.3 网页版参考图精修与查错（2026-08-14）
66. **Hero 生命周期**：`restoreChat()` 根据历史是否为空切换 `#hero.hidden`；空历史不再额外插入欢迎消息，首条用户消息在 `addUserMessage()` 中立即隐藏 Hero，清空会话后恢复 Hero。
67. **右侧 dock 状态机**：标签点击同时切换 `.rh-tab.active`、`.right-tab.active` 与 `.hidden`。仅移除 `.hidden` 不足以显示页面，因为基础 CSS 以 `.right-tab.active { display:flex }` 控制可见性。
68. **Summary 状态镜像**：`todos` 事件既渲染聊天卡片，也更新 `#summary-progress` 的任务行；`progress` 事件更新浮层与侧栏计数。Add To-dos 默认展开、Updated To-dos 默认折叠，与参考交互一致。
69. **响应式三栏**：桌面保持 260px 左栏与 320px 右 dock；中等宽度缩窄右 dock，≤900px 隐藏右 dock，≤640px 隐藏左栏并压缩输入工具条。聊天与输入水平留白改为约 6%，接近参考图的内容密度。
70. **装饰图标可访问性**：`icon()` 注入 `aria-hidden="true" focusable="false"`，避免内嵌 SVG `<title>` 覆盖按钮自身的 `title`/`aria-label`，使图标按钮获得正确可访问名称。
71. **同步服务器本地 LLM 开关**：`DRIFT_ALLOW_LOOPBACK` 环境变量默认关闭；仅部署者显式设为 true 时允许冷备 Agent 连接 127.0.0.1/::1，本地 MockLLM 回归在测试范围内临时开启并在 finally 恢复，私网与链路本地地址始终拒绝。

## v8.5.4 本地 Python/PyQt 参考图精修与查错（2026-08-14）
72. **桌面工作区状态机**：主窗口中央用 `QSplitter` 固定左侧 Quest 导航，右侧用 `QStackedWidget` 管理 Chat/Editor 两页；文件激活统一切换 Editor，New Quest/Quest 导航统一切回 Chat，避免聊天继续困在窄 Dock。
73. **右面板默认态**：Summary 面板首次启动即显示，图标 action、tab index、dock 显隐保持同步；重复点击当前 action 收起 dock，切换其他 action 自动展开。
74. **GUI 自截图回归**：使用 `QT_QPA_PLATFORM=offscreen` 启动 `DeverAIApp` 与 `SettingsDialog`，通过 `QWidget.grab()` 输出 PNG，视觉核对三栏比例、Hero/输入卡、设置卡片和小窗口约束；关闭前设置 `_force_close=True`，避免托盘驻留干扰测试。

75. **品牌中性化**：只替换用户可见文案（任务/工作台/当前对话），保留 `quest-*` DOM id 与 `QuestSidebar` Python 符号以避免破坏现有 CSS、事件绑定和外部兼容调用；静态扫描确认可见文案不再出现第三方品牌或专有类型名。
