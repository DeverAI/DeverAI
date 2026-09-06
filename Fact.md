# Fact.md — 用户偏好与事实约束

- [AI 集成批准] 用户明确要求 DeverAI 具备 AI 对话与 AI 调用工具（读写文件/执行命令行）能力，
  已批准引入全局 AI Agent。记录于 Design.md。
- [UI 偏好] 前端要好看，工具调用/状态展示要自然（不能只显示"运行中"，要有实时流式输出、
  状态图标、可折叠卡片、审批交互）。
- [进度要求] 基础部分完成即继续第三章/第四章扩展，未完成前不停顿。
- [问题时机] 用户在任务开始阶段可以提问；任务开始后须先完成全部可实现内容再提问。
- [无 EMOJI] 用户明确要求 NO MORE EMOJIS：所有 UI（网页版与桌面版）禁止使用 emoji 表情符号，
  统一用 SVG 图标或纯文本/Unicode 几何符号（需确保 Windows 字体有字形）。此约束适用于
  活动栏、侧边栏、聊天面板、状态栏、工具卡片、专家卡片、肝完睡觉倒计时等全部界面。
- [双入口并存] 网页版与桌面版作为独立入口并存：main.py 只跑桌面版（PyQt6），
  web_main.py 作为网页版启动入口（uvicorn + 自动开浏览器）。两者互不干扰，功能对等但代码独立。
- [相对路径安全] AI 只能看到工作区文件的大代号（codename），写文件/命令只能用相对路径；
  绝对路径由系统层（bridge.py / desktop/tools.py）翻译，以减少隐私与安全风险。
- [代码恢复源] 网页版 Agent 以当前 static/js/（9 个 JS）为基础修复，不从 backup 整体覆盖；
  backup 仅作参考。
- [v8.12 设计权衡] DevTools「复制为 curl」可复现化采用「登录响应回传 HMAC 会话令牌 +
  前端存 localStorage + 后端 current_user 接受 Authorization: Bearer」方案：Bearer 令牌即会话本身
  （无状态 HMAC、随 session_ttl 过期）；登出只清 Cookie 与本地令牌、不吊销已签发令牌。
  代价是 XSS 场景下会话令牌可被窃取（此前 HttpOnly Cookie 免疫）；换取的是 curl 可直接
  在终端完整复现鉴权请求。已按此权衡实施。
- [v8.12 设计决策] 命令桥 run_command 危险命令拦截（danger_ok 协议）定位为「防误触 /
  防裸调 API 误操作」，不是对已登录用户的安全边界——本产品设计即「已登录用户在授权工作区内
  执行真实命令」，恶意登录用户本身即主机操作者。误判修正：--force-with-lease 不拦截。
- [v8.12 冲突记录] 审计发现「全局单例工作区 + 开放注册 + 任意命令执行」若暴露 0.0.0.0 即为主机 RCE，
  与「本地单用户 Agent 主机」定位冲突 → 处理方案：保持产品定位不变（默认监听 127.0.0.1 已生效），
  在 web_main.py 对 0.0.0.0 监听打印安全警告，README 安全红线注明「不要用 --host 0.0.0.0 暴露本地资源桥」。
- [v8.12 冲突记录] sync_server 未设 SYNC_TOKEN 时全开放（任意网页可跨站读取快照）与安全要求冲突
  → 处理方案：未设令牌时仅环回客户端可访问（反代 XFF 存在则强制要求令牌），CORS 收紧为环回源；
  非环回部署必须设置 SYNC_TOKEN（桌面端设置中填同一令牌）。
- [v8.12 残留项裁决] /api/server/info 未鉴权仅泄露低敏布尔状态（allow_register/has_users/
  email_enabled/bridge_authorized）→ 可接受；app_screenshot wait_for 超时线程有内层三重兜底自结 →
  可接受；web_main 与 sync_server 默认端口均 8765 → README 已注明；security.audit 同步小量写入 →
  可接受。以上均按「可接受 + 文档注明」处理，未再改动。
- [v8.12 冲突记录] 审计发现 /tree/expert_report、/tree/inject 为「后端有端口但前端零调用」的孤儿端口
  → 处理方案：网页版无专家团体系（客户端 treeProblemsText 直接从 /tree/scan 重建校验问题注入文本，
  功能等价），两端口保留为桌面版专家体系专用，前端不接线；已记录于此并在 Design.md 决策 102 注明。
- [v8.13 设计决策] danger_ok 确认协议统一为严格布尔解析（仅 true/1/yes/on 字符串或 bool true 视为确认，
  "false"/"0" 一律拒绝），bridge/lite/snap_bridge/远程指挥 HTML 全链路一致。
- [v8.13 设计决策] 远程指挥 shell 与网页版命令桥同权：/cmd/dispatch 对危险命令要求 payload.danger_ok，
  HTML 控制台提供「危险命令显式确认」勾选框；sync_token 是唯一远程鉴权令牌（sync_password 仅作快照加密，
  绝不作为 X-Sync-Token 发送）。
- [v8.13 残留风险裁决] DNS rebinding TOCTOU（校验时公网、连接时内网）为通用网络攻击面；当前在
  校验层阻断私有地址并把 DNS 解析移出事件循环，但未做 IP pinning/自定义 transport。按「可接受 +
  文档注明」处理：仅限可信网络/本地环回部署，出口网络建议配合防火墙缓解。
- [v8.13 契约差异] 完整版与 Lite 为刻意差异：Lite 注册/登录不返回 curl 用 session token、auth/me 无
  email、fs/read 无 size、fs/tree 无 modified、写上限 5MB（完整版 10MB）。README 已列差异说明。
- [v8.13 平台差异] 桌面版有专家团/浏览器 CDP/exe 自动化/租约锁/端口文档等专属工具；网页版有 mkdir/
  rename/search_assets 等浏览器端原生工具；同名工具大部分 schema 跨端一致（v8.13 已统一 plan_and_execute
  的 task 参数）；已知例外：run_command 的 background 参数为桌面专属，网页版不提供后台命令。
- [v8.13 文档单套制] tools/Err.log 为历史遗留空文件（0 字节），不写入、不读取；Err.log 唯一活跃副本
  只有根目录一份，所有运行时错误统一写根目录 Err.log。
- [v8.13.1 安全边界] Agent 可看开发者网络面板（network_list/network_curl）与 static/app/desktop 源码，
  但可见内容全部经打码（Bearer/api_key/password/token 打码为 ***）；HttpOnly 会话 Cookie 永不向 JS/Agent
  开放；data/backups/config.json/Err.log 仍禁读，源码目录仍禁写/删/改名。
- [v8.13.1 FSS 保护说明] 浏览器 File System Access（FSS）模式无法获知绝对路径，因此按相对路径首层/文件名
  执行与 bridge/desktop 同源保护；任意授权目录中同名 data/backups/config.json/Err.log 同样受保护（宁可
  误拦，不放行密钥与运行数据）。
- [2026-08-17 语音助手] 用户要求「检修后跑起来，跑起来后开始增加功能」语音助手「小龙」。调研 Qwen API 语音能力：STT/TTS 使用浏览器 Web Speech API（零依赖），LLM 使用 Qwen（DashScope OpenAI 兼容接口）。完整性校验使用 HKDF + HMAC-SHA256 自主实现。语音助手默认关闭（ENABLE_VOICE_ASSISTANT=false），完整性校验默认开启（ENABLE_INTEGRITY=true）。
- [2026-08-22 DSG Cordis 插件] 用户要求按 `参考图/` 修改 DeverAI cordis 插件前端效果。最终落地：`dehub-11/pkg-34`「DeverAI Hub」Cordis dynamic plugin 试运行成功后，用户立即要求转换为 disk-persistent 静态插件。已落盘的静态 npm-package：`C:\Users\Administrator\.dsh\profiles\web\deverai-hub\`（含 `package.json`/`lib/index.js`/`lib/client.js`/`README.md`），并更新 `cordis.patch.yml` 加 `- insert: [{ id: deverai-hub-host, name: '@deverai/hub' }, { id: deverai-hub-client, name: '@deverai/hub', client: true }]`。Image 路径：`/registry.npmjs.org/pnpm/latest` 在本会话不可达；通过两步骤绕过 ①用 `npm install -g pnpm --force` 通过 `~/.npmrc` 的 `registry=https://registry.npmmirror.com` 装 pnpm 11.22.0 standalone（覆盖 corepack pnpm.cmd shim）；②`pnpm install ./deverai-hub` 在 `~/.dsh/profiles/web` 创建 `node_modules/@deverai/hub → ../../deverai-hub` 符号链接，Node ESM `import('@deverai/hub')` 解析成功（返回 `apply/inject/name`）。修复链：本轮解决了 4 个 Cordis 框架坑（①`ctx.harness` Proxy 拒绝 → 全局 `harness`；②`ctx.locale` 未声明 inject → `inject: ["locale", "slots"]`；③locale namespace duplicate fatal → safeRegister try-catch + t(key) fallback；④动态 plugin 重启即失 → 转 npm-package 静态化）。DSH 重启后应通过 patch.yml 加载 `@deverai/hub` 的 host 与 client 两个 plugin id（`dsh.client` metadata 自动指向 `lib/client.js`）。
- [2026-08-16 文档整理] 用户要求「把 Design 等文档按章节整理、源码全部翻一遍」。处理方案：Design.md 按
  章节重构为当前状态式（版本记录迁出到 dev_log）；Techniques.md 加目录与本轮方法；README 补测试/CDN/环回
  开关说明；源码盘点发现的数据清单过时项（exchanges/*.json、themes/*.json）与模块开关缺项已按源码修正。
  本轮为文档维护，不改产品代码。
- [2026-08-23 全量检查深度加强] 用户要求全项目全量检查并深度加强，最后一个操作必须是检查。本轮密钥排除
  升级为六字段（新增 dashscope_api_key）；网页桥新增 /allow_ai_delete、/feature_flags、/power_authorized
  三个设置同步端点；checkpoint/rollback 端口与文件桥同源保护；Guard 只读态在文件/命令桥后端强制生效；
  桌面 LockManager 全部时效统一 time.monotonic()。详细修复见 dev_log/20260823.md 与 Techniques.md
  「全项目检查与深度加强」章节。
- [2026-08-23 边界裁决] run_command/远程 shell 是“已登录用户/已授权 Agent 在授权工作区执行真实命令”的
  显式能力，shell 层不施加 fs/read 的 data/backups 读保护；数据保护语义只适用于文件类工具，shell 的边界
  是工作区 resolve + 危险命令审批（danger_ok）。若部署场景不接受该边界，应改用不可信模型或关闭命令桥/
  远程指挥，而不是依赖工具层过滤。该边界已在 README 安全红线注明。
- [2026-08-24 全量检修 v8.14] 用户要求三版本（lite/webui/pyqt）全量检修、允许大改架构（容忍度 95/100）、
  不删库。用户裁决：①桌面版 PyQt5 迁移到 PyQt6（本机仅装 PyQt6）；②lite/app 与 webui/app 保持独立副本，
  不抽共享包；③检修前 git init 建本地基线快照（api_keys.py/data/backups/Err.log 永不入库）。
  本轮关键修复：Lite P0（缺 codename.py 致工作区授权 500，Lite 核心功能整体不可用）；webui P1×4
  （SSE 64KB 断流/设置同步门控/storage 明文导出/rmtree 冻结事件循环）；pyqt P1×6（CRLF 字节漂移/
  goal_reached 子串兜底误触发关机倒计时/远程 shell 绕过 danger_ok/Vault 搜索冻结 UI/浏览器 profile
  固定共享路径/_ALL_DEFS 竞态）；emoji 清理（✅❌⚠️ → [OK]/[X]/[!]，tools/bridge 判定端同步改）。
  行尾策略裁决：写入口统一精确写（newline=""），读侧规范化 \n，写回按原文件 sniff_crlf 保持风格；
  新文件一律 LF。PyQt6 迁移采用两轮 codemod + 属性审计（hasattr 全量校验）归零 + offscreen 实例化验证。
  详细修复清单见 dev_log/20260824.md。
- [2026-08-24 环境事实] 本机 Python 3.12.10 + PyQt6.11（无 PyQt5）；历史 pyc 为 3.10/3.13（另一台
  机器的开发环境），本仓库在该机器上曾存在 dev_log/ 与 README/requirements/AGENT.txt/sync_server.py，
  当前工作区副本中不存在；server.py 打包清单对缺失文件静默跳过（向前兼容，恢复文件后自动入包）。
  webui 与 lite 的 config.py 日志前缀均为 [lite] 属历史遗留，本轮已把 webui 侧改回 [web]。
- [2026-08-27 轮次裁决] 用户指令「继续构建…先看目录再问问题后续直接一口气干完不返工」。问询裁决：
  ①范围=收尾进行中的 v8.15 检修 + 开发 Future.md 的「多会话标签」；②本机不使用 git（用户明示
  "我没有Git，不加"），本轮全程零 git 操作，改用 AGENT.txt backups/ 备份机制；③验证=全量+静态双深度。
- [v8.16 会话语义] 多会话=仅隔离对话历史与跟随历史的视图；工作区/模型/配置全局共享。同一时刻全局只
  一个 AI 回合，运行中禁切会话（桌面新增软阻止提示、网页版沿用 v8.13 守卫）。桌面镜像写活跃会话到旧
  desktop_history.json（关闭开关后仍见最新对话，回退安全方向）；关闭最后一个标签=清空内容保留会话；
  关闭非最后标签仅移除条目不删历史文件。网页版标签=打开态视图（localStorage deverai.v2.open_tasks），
  删除标签仅移除打开态不删数据（当前版本主界面无任务删除入口，为后续增强方向——v8.16.1 复检
  收敛口径）；Lite 持久化升级为 localStorage 分槽（浏览器重启可恢复），旧 sessionStorage 键
  仅作首启迁移源。
- [v8.16 测试隔离契约] 双服务端新增 DEVERAI_DATA_DIR 环境变量重定向数据目录（未设置时行为与历史版本
  完全一致），配合既有 DEVERAI_RUNTIME_PORT 实现"零接触真实 data/"的端到端冒烟；tests/ 目录首次入库：
  test_desktop_offscreen.py（复检修正恒真断言并增补开关链路后 35 项）+ test_smoke_servers.py（38 项），
  退出码纪律为非零=存在失败。
- [v8.17 安全中心裁决] 轻档（纯聚合现有安全能力为可视化 UI，不改底层删除语义、不加黑白名单/前缀放行/
  备份配额）+ 三版本对齐（桌面设置对话框 Security Tab / webui 设置面板安全分区 / lite 只读审计面板）；
  不做回收站删除保护语义（用户明确拒绝）。
- [v8.18 终端环境池 + 悬浮小助手 Agent 循环 + 系统专家裁决] ① 命令行新增持久环境（cwd+env 轻量会话）+ 更新间隔心跳 + 任务后杀进程，全部带默认值 warning（未指定时返回 [WARN] 使用默认值：...，AI 可见）；四端同源（webui 后端 bridge.py / webui 前端 tools+fs+agent+voice-pet / 桌面后端 terms+tools / 桌面前端 tools）。② 悬浮小助手（小龙）升级为 Agent 循环入口——一次 LLM 调用自判 {mode: chat|agent_loop|system_expert}，agent_loop 暂停主循环（软暂停·工具边界安全停靠）后独立 runPetAgent 循环，system_expert 只读查阅 Design/Techniques/Fact 回答系统问题。③ 用户与 Agent 共享系统专家。④ 主循环软暂停原语（agentPauseMain/ResumeMain/WaitIfPaused）+ 工具循环两处停靠点。
- [v8.15 验证基线] 89 个 .py 全量编译通过、15 个独立 js node --check 通过、双 lite.html 内联 JS 抽取
  编译通过且两份逐字节一致；危险正则新增两条已四端同源（pyqt tools / app security ×2 / static js tools /
  lite.html ×2——js/html 版以 [\s\S]* 与 .* 的等价写法保持行为一致）。
- [v8.19 全量检修裁决] 用户指令「全量检修优化，不限范围，不限操作类型 + 阶段性备份 + 维护文档」。检修轮
  无新功能：修复 test_desktop_offscreen.py 悬空 try:（桌面套件此前整体不可运行）、bridge.py 补
  _save_term_envs（term/create 500）、冒烟测试按 expect_term 门控（lite 刻意不实现 v8.18 终端池）、
  index.html 四 js 版本号 bump 8.19.0（修 v8.17/v8.18 漏 bump）、Design.md 六处 PyQt5→PyQt6 + §4.5
  幽灵文件表改真实表。sync_server.py 确认为跨机器工作区差异（本副本刻意不含，见 2026-08-24 环境事实），
  文档加注记而非补文件。
- [2026-08-31 v8.24 WorkTree 命名裁决] 用户指出「WorkTree 不是 GitHub WorkTree 那个东西」——本项目术语
  WorkTree = 独立安全备份审核（checkpoint/session_snap/dep_tree/audit 三层防线，Design 特点3），与
  git worktree 无关。v8.21 引入的 git worktree 能力（bridge 四端点 + 面板 + 工具）保留但 UI 文案与
  提示词更名「Git 分支实验」；内部标识符（/api/bridge/worktree/*、WorkTreePanel、worktree_list/switch
  工具名）不动，避免无谓回归。
- [2026-08-31 v8.24 sync_server 落地裁决] 用户追问「算力漂移是齐的吗？全面看 Design 别漏一堆东西」——
  按 Design §2.3/§6.8 契约补建 sync_server.py 完整版（/push /pull /chat /drift/* /cmd/* + HTML
  / 与 /chat/page + SYNC_TOKEN 鉴权），v8.19「加注记而非补文件」裁决就此作废；README.md 同轮补建
  （Design §9 要求的首要入口文档，此前因跨机器差异缺失）。
- [2026-09-05 v8.25/v8.26 用户资产与编辑器裁决] ①AI 禁碰语义=拷贝（用户原话「AI禁止碰不是不能碰，而是拷贝，
  原内容不修改，我们直接拷贝到其他地方干」）→ 用户资产硬拦截升级为拦截+引导 copy_user_asset 生成 workcopy/
  工作副本（审批门 + 开关 ENABLE_WORK_COPY），原文件永不修改，workcopy/ 内 AI 副本豁免保护；Lite/DSH 插件不接
  （用户明示）。②编辑器落点：用户裁决「DeverAI两端（Python+Web）两端统一」——桌面与网页编辑器统一 WorkTree
  保护语义（保存前快照 source=human、编辑器内版本历史入口、行尾保持），Lite 与 DSH 插件明确不加。③新建文件
  命名规则：优先找资料沿用工作区已有命名惯例；无惯例按「时间-作者-内容」（用户交付物作者=用户，AI 中间产物
  作者=AI）。④PPT 参考库&知识库：Mavis 侧固定库+技能先行落地（用户允许的 PPT 导出 PNG 入库，做 PPT 任务先圈
  PNG 再拷相关切片进上下文）；DeverAI 资产银行扩展模块入 Future.md 下轮做。⑤「未选择工作区」会话不把代码/
  过程摔桌面——Mavis 侧行为规则，已记 user memory；DeverAI 侧 workspace 默认 APP_DIR 行为不变。
- [2026-09-05 版本事实] 当天实际两轮：上午 v8.25 交付整治（dev_log/20250905.md 已记），下午 v8.25 用户文件
  保护（file_protect 四端接线，此前漏记 dev_log，v8.26 轮补记 dev_log/20250905_v825b.md）。本轮 v8.26 编辑器
  统一 + 工作副本，index.html ?v= 统一 bump 8.26.0。
- [2026-09-05 v8.26 存量缺陷修复与遗留] v8.26 冒烟实证：webui 服务进程内 `desktop` 别名不可导入
  （bridge.py:35 _REPO_ROOT 只保证 `pyqt.desktop` 形式），v8.25 接线的用户资产拦截在 webui 端实为死代码
  （写放行、命令不拦）。本轮已修 WorkTree/资产保护链路 8 处（bridge file_protect×4 + session_snap×1、
  snap_bridge checkpoint/file_protect×2、snap_util×1），webui 拦截与 /checkpoint/*、workcopy 全部实证恢复。
  **遗留待修 4 处同类死导入**（非 WorkTree 链路，激活前需先解决 desktop.config 不认 DEVERAI_DATA_DIR 的
  数据目录隔离问题，避免冒烟/多实例污染真实 pyqt/data）：meta_bridge.py models/matcher/auto_score×3、
  proxy.py models×1（toolsmith×3 已在阶段3 一并修复并复验）。
- [2026-09-05 v8.27 真·算力漂移四项裁决] ①密钥随漂移加密上自有服务器（用户原话「密钥的话还是同样传上服务器，
  但得找办法加密……MQTT肯定是不行的」）→ 复用 sync_password PBKDF2 派生 + AES-GCM 信封，服务器仅存密文
  （keys_enc 不透明串），续算时凭口令解密到内存不落盘；安全红线「API Key 仅本机」就此修订为「本机明文、
  自有服务器仅密文信封」。②不看守模式（用户原话「不使用提问工具、审批工具，能做的做完，没做完的保留进度」）
  → agent_unattended：审批门自动策略（非危险放行/危险跳过记账）+ 工作区 unattended_progress.json 台账随快照
  往返。③会话迁移=轮边界断点上传，服务器以普通端 CLI 续同一会话；工作区素材化到 APPDATA
  （%LOCALAPPDATA%\DeverAI\drift\<项目号>\workspace，SYNC_WS_DIR 可覆盖），WorkTree/记忆/设置同步到对应
  data 位（绝不放工作区内）。④退出上传模式 drift_upload_mode（最简=WorkTree 即时变更增量 / 完整=全工作区
  +data 子集），漂移退出对话框选一次即记住、以后不再询问，设置页可改。
- [2026-09-05 v8.28 六项裁决] ①备份范围控制：C 盘爆红 → checkpoint 每文件保留版本数改为可配（默认上两版），
  WorkTree 的 Copilot 拦截危险操作等安全语义不变。②上传/换机时把「环境可能需要重配」注入 AI 上下文（drift
  续算状态自动注入）。③任务结束末尾要变更栏，AI 引用文件可选形式：HTML=预览/源码（源码默认折叠、可展开可
  复制、不直接展开）或仅文件；客户端配置自动上云漂移时提示词告知 AI：用户不能预览文件，必须用引用形式交付。
  多图引用=微信式扑克牌压缩叠放、悬停距离驱动排斥位移、点击放大。④Agent+ 模式预设（无人值守即一种预设，
  目的=让 AI 把能干的活先干完）。⑤上传文件功能：本轮只出方案未实现（见对话/ Future.md）。⑥变更栏与引用
  卡片本轮落地网页端；桌面端变更栏 UI 排下一轮（桌面既有「打开文件」路径可用）。
- [2026-09-05 v8.30 上传功能裁决] 用户拍板 Future.md 三选一之**方案1**（桌面拖拽入工作区），并要求支持
  「输入框粘贴文件、文件拖动进入」。落地：窗口级拖放 + 输入框 Ctrl+V（文件/剪贴板图片）→
  uploads/<ts>-user/（uploads_ingest.py：敏感名拒绝、512MB 上限、同名加序号）→ 自动挂引用条
  （指针式引用，AI read_file 读取）；checkpoint 跳过 uploads/ 前缀（C 盘约束，session_snap 整包/
  一键备份仍覆盖）。远程端追问裁决：手机/云端文件走漂移反向带回（方案3 既有通道），网页端受控
  上传=方案2 另行立项（攻击面单独审）。桌面变更栏与引用卡片已在 v8.29 补齐（⑥兑现）。
- [2026-09-05 v8.32 全端检查轮] 用户指令「字面意义上的检查：看码→验证→修复→记录；同步各端（除 DSH 插件），
  分歧谁新谁强用谁；Lite 保持轻量」。落地：①desktop.config 认 DEVERAI_DATA_DIR（v8.26 遗留前置解除），
  meta_bridge×3/proxy×1 死导入激活（/api/bridge/models* 复活、proxy 注册表 url/key 覆盖生效）；②note_ai_write
  五端接线（AI 生成资产不再被「用户文件保护」误锁，编辑后必须刷新指纹）；③Lite lite_server 补后端用户资产
  拦截 + CRLF 保持（webui 副本同源移植，lite.html 孪生零改动）；④drift 上传包补 .pem/.key/.pfx/.p12 密钥
  后缀排除（uploads_ingest 注释声称的同源就此成立）；⑤ENABLE_MODES 死开关接线（9 消费面+设置页三勾选联动；
  行为门控落地，聊天区模式芯片保留可切换、总开关关时不生效）；⑥powershell -enc 危险正则四端统一为 \b 强
  形式（新增四端静态一致性锁 test_dangerous_patterns_four_end_sync，首跑即抓到 lite.html 漂移）。备份=基线
  提交 d988bee（红线禁命令行复制项目文件，git 基线替代手工副本，v8.24「检修前建基线快照」先例）。
  遗留表态结果（用户同日答复）：①备份裁决=本地 git 基线提交制可行（远端网络环境不支持，不推远端）；
  ②ENABLE_MODES 关=「演示模式」语义（后端没接口的那种样子），聊天区模式芯片不隐藏、保持现状。
  仍未表态（不阻塞，见 dev_log v8.32 遗留）：webui 保护开关差异、冒烟提速开关、storage load_json 兜底、
  uploads 并发同名竞态。
- [2026-09-06 v8.33 全局协调协议裁决] 用户原话要点：「加一个全局的闸口，检查每一个正在并发执行任务的 agent
  都在做什么；如果发现有冲突，就让这两个 agent 互通信息——你在干什么？我在干什么？你接下来要干什么？我接下来
  要干什么？这两个信息也有必要让用户能看到，人类可以在损害发生前直接阻止，而不是等事后兜底」。场景=跨工作区
  的外部资源并发（两个不同工作区的 Agent 同时 SSH 同一服务器，一个重启、另一个上传中掉线还懵着）。落地：
  coordination.py 注册表（DATA_DIR 本机共享）+ 心跳 TTL + 冲突检测（归一化资源键）+ 双向收件箱协议 +
  轮开始注入 + coordination_board/declare 工具 + 桌面协调看板对话框（命令面板入口）+ webui 只读端点 +
  run_command 自动嗅探 ssh/scp/rsync 主机。开关 ENABLE_COORDINATION 默认开。
- [2026-09-06 v8.34 检查轮（v8.33 协调协议过查 + UX 别扭排查）] 用户指令「过的，直接走。顺便检查有没有用户
  用起来会别扭的地方并修好」。查实修复 11 项：H1 看板 QTableWidgetItem 未导入（v8.33 人类看板自诞生起从未
  打开过，py_compile 与数据层单测都抓不到）；H2 纯心跳不落盘（看板只显示声明过资源的 Agent）；H2b 轮开始
  不带 task（「在干什么」列恒为未声明，要等 AI 自愿 declare）；H3 看板对话框常驻不销毁 + 5s 定时器永不停；
  H4 TTL 回收只改内存不回写（coordination.json 只增不减，死 Agent 收件箱永久残留）；H5 被禁用的模式勾选
  无理由提示；H6 看板只藏在命令面板（补「工作区」菜单入口）；H7 Agent 列只有「目录名@pid」，人类不知道
  叫停哪个窗口（补工作区路径 tooltip）；H8 webui 人类无看板（补设置「安全中心」只读区块，兑现 v8.33
  「信息人类也要能看到」）；H9 看板文案承诺"直接叫停"但界面无叫停能力（改为可操作指引）；H10 看板模态
  exec 挡住本窗口「停止」（改非模态 + 单实例复用）；H11 轮结束状态不置 idle（v8.33 自述的已知边界收口）。
  顺带补：v8.33 新增的 /api/bridge/coordination 端点此前冒烟零覆盖（违反「新端点必须真实调用一次」纪律）。
  index.html ?v=8.34.0（panels.js 改动触发）；新增 15 条断言；备份=基线提交 4c88299（git 基线制，用户已认可）。
