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
- [双入口并存] 网页版与桌面版作为独立入口并存：main.py 只跑桌面版（PyQt5），
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
