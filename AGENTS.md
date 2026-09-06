# DeverAI 工作区指令（Local AGENTS.md）
> 项目专属规范与故障库。DSH 进入本工作区时自动加载。

## 项目概况
- 定位：「UI 即 Agent 运行地」AI 开发工作台
- 主形态：PyQt6 桌面程序（`pyqt/main.py`）
- 其他入口：网页版（`webui/`）、超轻量 Lite（`lite/`）、CLI（`pyqt/cli_main.py`）
- 架构文档：`Design.md`；技术方案：`Techniques.md`；用户偏好：`Fact.md`；常见错误：`FreqErr.md`

## 工作规范（项目专属）
- 网页版 Agent 以当前 `static/js/` 为基础修复，不从 backup 整体覆盖。
- PyQt6 迁移已完成（本机无 PyQt5），不要回退到 PyQt5 语法。
- `data/backups/config.json/Err.log` 永不入库（git 敏感文件排除）。
- 危险正则必须四端同源：`pyqt/tools.py` / `webui/app/security.py` / `lite/app/security.py` / `webui/static/js/tools.js` / `lite/static/lite.html`。
- `lite/static/lite.html` 与 `webui/static/lite.html` 必须逐字节一致（孪生副本）。
- NO EMOJI：所有 UI 禁止 emoji，统一用 SVG 图标或 `[OK]`/`[X]`/`[!]` 纯文本标记。
- 行尾策略：写入口统一精确写（`newline=""`），读侧规范化 `\n`，写回按原文件 `sniff_crlf` 保持风格；新文件一律 LF。

## 模块开关三层贯通纪律
- 开关必须在三层一致：`config.py` 字段定义 → `build_tool_defs` 开关裁剪 → `settings_dialog.py` SWITCHES 列表。
- 内部机制开关（快照/依赖树/建议/反哺等）可仅在 `config.py` + `build_tool_defs` 两层。

## 故障库（项目专属）

### [DeverAI] 多会话标签切换时旧历史丢失
- **现象**：切换会话标签后，旧会话的对话历史在界面上消失。
- **可能的远因**：`desktop/sessions.py` 切换时未保存当前 history 到旧会话文件；或 `chat_history/{id}.json` 未正确写入。
- **解决方法**：切换前先 `save_history(active_id, current_history)`；切换后从目标会话文件 `load_history` 回放。
- **类型**：工作区专属

### [DeverAI] Vault 搜索冻结 UI
- **现象**：资产银行搜索时界面冻结。
- **可能的远因**：搜索在事件循环线程执行，而 `add()`/`delete()` 在同一线程改列表，按索引回查 `self._assets` 错位；或 embedding API 调用阻塞。
- **解决方法**：搜索前对 `self._assets` 建索引快照（`assets = list(self._assets)`）；embedding 调用丢 `asyncio.to_thread()`。
- **类型**：工作区专属

### [DeverAI] 浏览器 CDP profile 固定共享路径
- **现象**：多实例启动浏览器时 profile 冲突，导致 CDP 连接失败。
- **可能的远因**：浏览器 profile 目录硬编码，多实例共享同一 profile 路径。
- **解决方法**：每个实例使用独立 profile 目录（如临时目录 + 实例 ID）。
- **类型**：工作区专属

### [DeverAI] goal_reached 子串兜底误触发关机倒计时
- **现象**：AI 回复中包含 "goal_reached" 子串时，误触发肝完睡觉倒计时。
- **可能的远因**：用 `in` 子串匹配而非严格 JSON 解析。
- **解决方法**：改用严格 JSON 协议解析，要求模型输出 `{"goal_reached": true}` 完整结构。
- **类型**：工作区专属

### [DeverAI] 远程 shell 绕过 danger_ok
- **现象**：远程指挥命令桥未校验 `danger_ok` 就执行危险命令。
- **可能的远因**：`/cmd/dispatch` 端口未复用 `is_dangerous_cmd()` 检测。
- **解决方法**：远程 shell 与网页版命令桥同权，危险命令要求 `payload.danger_ok`。
- **类型**：工作区专属

### [DeverAI] CRLF 字节漂移
- **现象**：写文件后行尾被隐式翻译，回退后与原文件字节不一致。
- **可能的远因**：`open()` 未指定 `newline=""`，Python 隐式翻译 `\r\n` → `\n`。
- **解决方法**：写入口统一 `open(path, "w", newline="")`；读侧规范化 `\n`；写回按原文件 `sniff_crlf` 保持风格。
- **类型**：工作区专属

### [DeverAI] SSE 64KB 断流
- **现象**：网页版 SSE 流式输出在 64KB 处中断。
- **可能的远因**：`asyncio.subprocess.PIPE` 默认 `limit=64KB`，单行超长输出触发 `ValueError`。
- **解决方法**：`create_subprocess_shell(..., limit=1024*1024)` 提升上限。
- **类型**：工作区专属

### [DeverAI] lite.html document 级 click 监听器自 abort
- **现象**：Lite 版发送消息后立即被 abort，聊天功能完全不可用。
- **可能的远因**：document 级 click 监听器在 onclick 设 `State.busy=true` 后冒泡触发，立即 abort 刚发的消息。
- **解决方法**：删除冗余的 document 级监听器（`sendMessage` 已内联处理 stop）。
- **类型**：工作区专属

### [DeverAI] 收口后代码留语法错误（测试套件整体不可运行）
- **现象**：v8.18 dev_log 声称测试全过，但 tests/test_desktop_offscreen.py main() 留有悬空 `try:`，py_compile 直接 IndentationError；同轮 webui/app/bridge.py 的 `_save_term_envs` 被调用但未定义（term/create 500）。
- **可能的远因**：收口验证跑在最终编辑之前；功能代码写完后未实际调用新端点。
- **解决方法**：收口验证必须在最后一次编辑之后执行；每轮必跑 `python _audit_tmp.py`（gui+server 双套件）+ 全量 py_compile；新端点必须真实调用一次（冒烟断言）。
- **类型**：工作区专属

### [DeverAI] webui 缓存破坏版本号漏 bump
- **现象**：v8.17/v8.18 修改 panels.js/fs.js/tools.js/agent.js 但 index.html 的 `?v=` 未更新，浏览器旧缓存 JS 静默缺新功能。
- **可能的远因**：改动只动 js 文件本体，忘记同步 index.html 引用行。
- **解决方法**：凡改 webui/static/js/ 下文件，必须同步 bump index.html 对应 `?v=`（统一 bump 全部 17 处；v8.32 起当前为 8.32.0）。
- **类型**：工作区专属

### [DeverAI] browser_ctl 会话假启动（global 缺失）
- **现象**：browser_launch 返回成功，但 navigate/click/type 全报"尚未启动浏览器"。
- **可能的远因**：`pyqt/desktop/browser_ctl.py` 的 `_do_launch` 内 `_SESSION = session` 缺 `global _SESSION` 声明，只写局部变量，模块级 `_SESSION` 恒为 None（桌面版与网页版同源中招，v8.23 修复）。
- **解决方法**：函数内赋值模块级变量必须显式 `global`；CDP 类工具必须真实跑 launch→操作→close 全链路再收口（单元断言测不出）。
- **类型**：工作区专属

### [DeverAI] webui 懒导入 pyqt.desktop 必 500（sys.path）
- **现象**：webui 服务从 webui/ 目录启动时，bridge 端点内 `from pyqt.desktop import ...` 抛 ModuleNotFoundError → 500。
- **可能的远因**：cwd=webui 时 repo 根不在 sys.path；懒导入错误发生在 try 块外直接冒泡。
- **解决方法**：bridge.py 模块级把 repo 根插入 sys.path（v8.23 已加）；懒导入尽量放进 try 块。
- **类型**：工作区专属

### [DeverAI] AI 生成资产被用户保护误锁（note_ai_write 零接线）
- **现象**：AI 刚生成的 .csv 等用户资产后缀文件，下一次 edit_file 被「用户文件保护」拦截并要求走 copy_user_asset；桌面/网页/Lite 同现。
- **可能的远因**：file_protect.note_ai_write 定义后全仓库零调用——AI 写入无 actor=ai 指纹，check_ai_write_block 内 sync_user_modified 把无记录文件补记为 human/user_modified。
- **解决方法**：AI 成功 write/edit 用户资产后缀文件后登记/刷新指纹（编辑后必须刷新，否则 AI 自己的编辑被误判为用户修改）；v8.32 五端接线（桌面 tools.py + webui bridge + 两个 lite_server），并有闭环回归断言。
- **类型**：工作区专属

### [DeverAI] drift 增量测试墙钟脆弱（间歇红）
- **现象**：test_unattended_and_keys 的「cutoff 后新文件入包」间歇性失败，隔离复现恒绿（v8.32 收口时两连红）。
- **可能的远因**：time.time() 墙钟与 NTFS mtime 之间可能被 NTP 步进/时钟回拨反转，测试对墙钟做了时序假设（FreqErr「同模块混用 time 基准」同族）。
- **解决方法**：测试内显式 os.utime 锚定 mtime（cut+60s）保证确定性；产品侧 drift 增量跨机比较必须用墙钟，语义不动（v8.32 修测试并注明）。
- **类型**：工作区专属

### [DeverAI] 协调看板打不开（Qt 类未导入）
- **现象**：点「全局协调看板」毫无反应/报 AttributeError，v8.33 交付的人类看板从未真正打开过。
- **可能的远因**：`ide_extras.py` 的 `CoordinationBoardDialog.refresh()` 用 `QTableWidgetItem`，但模块级与 `__init__` 局部 import 都没有它；`__init__` 末尾就调 `refresh()` → NameError。py_compile 抓不到，coordination 数据层单测也抓不到。
- **解决方法**：补 import；PyQt6 枚举用 scoped 形式（`Qt.WidgetAttribute.WA_DeleteOnClose`）；新增 Qt 对话框必须加离屏「构造+刷新+读回单元格」断言，且排在已有 GUI 测试之后复用 QApplication 单例（v8.34 修复）。
- **类型**：工作区专属

### [DeverAI] coordination.json 只增不减 + 看板看不到普通 Agent
- **现象**：协调看板只显示声明过外部资源的 Agent；`data/coordination.json` 随每次重启单调变大，死 Agent 与其收件箱永不清除。
- **可能的远因**：`touch()` 的节流条件 `new_conflicts or not throttle or changed` 对纯心跳（只带 status/model）全 False → 新条目也不落盘；`_purge()` 只改内存 dict，`snapshot()` 只 return 从不回写。
- **解决方法**：节流条件补 `created` 与 `due`（`_LAST_PERSIST` 记账）；`_purge` 返回条数、`snapshot/touch` 据此 `_save`，并连带清理死 Agent 的 inbox；断言要读磁盘终态而非只看返回值（v8.34 修复）。
- **类型**：工作区专属

---

## 全局故障库
> 跨项目复用的通用错误请查 `~/.dsh/faults.md`（与全局 AGENTS.md 同目录）。
