# FreqErr.md — 常见错误类型记录

格式：`[错误类型] 错误描述（技术上和现象上） → 正确做法`

[WS 重连竞态] 旧连接的 finally 无条件 `state.ws = None` 并取消 agent_task；断线 2s 内重连后新连接事件被静默丢弃、运行中的对话被误杀 → 仅在 `state.ws is ws` 时才清理连接并取消任务。

[CancelledError 被吞] Agent 在 `except asyncio.CancelledError` 里 break 而不重抛 → AOE 的 20s 超时熔断与「停止」按钮全部失效，慢节点可拖 600s → 捕获后必须 `raise`，由 `wait_for`/调用方处理；软停止只依赖 `_cancel` 事件。

[取消泄漏子进程] 子进程读取协程被取消时 `except Exception` 捕不到 CancelledError（继承自 BaseException），`proc.kill()` 不执行 → Windows 下产生孤儿进程无限运行 → 改用 `except BaseException` 兜底 kill 并重抛。

[并行审批 call_id 冲突] 兜底 call_id（`call_{name}_{len}`）在并行 AOE 节点/子Agent 间相同，共享 ApprovalGate 的 Future 互相覆盖 → 兜底 id 加 Agent 实例前缀（`f"call_{id(self):x}_..."`）。

[降级加密导入必炸] 混淆标记 `b"obfuscated"` 被压进 zlib 内部，导入先 `startswith` 判断恒为 False → JSONDecodeError 直接 500 → 先 `zlib.decompress` 再剥离标记。

[EOF 后 wait 卡死] 命令提前关闭 stdout 但进程仍存活（daemonize 类），`proc.wait()` 无超时无限阻塞 Agent 循环 → `asyncio.wait_for(proc.wait(), timeout=15)`，超时 kill。

[工具输出进上下文爆炸] read_file/grep 大输出原样写入 tool 消息，下一轮上下文膨胀甚至 API 拒绝 → 协议消息内容截断 20k 字符（UI 事件仍发完整输出）。

[未导入却引用] 模块内引用 `AI_ACTIONS`、`queue.Empty` 等名称但未在 import 中引入 → NameError，在 Qt 槽内触发时还伴随堆损坏崩溃（exit 3221226505）→ 新增功能用到的常量/工具类必须一并 import；运行前用 `py_compile` 全量编译检查。

[QThread 活销毁] 子 QThread 仍在 run 中（LLM 请求/子进程读取）其父对象就被销毁（关闭对话框/退出窗口）→ Qt5 报 "QThread: Destroyed while thread is still running"（内部 TerminateThread，Windows 上常连带死锁/崩溃）→ QThread 无 parent 化 + 上层持引用 + 销毁前先 disconnect 信号再 `wait()`；closeEvent 统一收尾所有在途线程。

[ghost 陈旧位置误删] ghost 插入后用户 undo/外部编辑使文档变化，`ghost_start/end` 变成陈旧位置，清理时按区间 `insertText("")` 删掉用户刚恢复的文本 → 删除前校验区间两端字符确为 `\x1f` 标记，不匹配只复位标记不删文本。

[loop 被同步扫描阻塞] asyncio loop 内直接跑 `os.walk`/`re.search` 等同步重操作（超大目录/灾难性回溯正则）→ 循环被阻塞，「停止」与退出全部失效 → 丢 `run_in_executor`，并限制行长/条目数；行正则前先截断 5000 字符。

[UI 事件泵一次排空] 事件泵 `while True` 排空整个队列，2000 行命令输出单 tick 内 2000 次 insertHtml → UI 冻结数秒 → 每 tick 限流（≤200 条），剩余留给下个 tick。

[协程未 await] 同步函数里直接调用 async 协程函数拿到的是协程对象，`obj.get(...)` 抛 AttributeError 被 `except Exception` 吞掉 → 功能"静默失效"（Inline 补全恒为空）→ 在无 running loop 的线程里用 `asyncio.run()` 包装；但注意**不能在已有 running loop 的环境里再 asyncio.run**（测试从 async main 调会抛 RuntimeError），需用线程模拟。

[死导入炸运行时] 模块内 `from .storage import load_text` 但 storage.py 实际函数名是 `read_text`，该 import 在函数体内且为死代码（导入后未使用）→ py_compile 通过（编译期不解析运行时导入），但运行时调用 restore_checkpoint 抛 ImportError → 新模块的跨模块导入必须在文件顶部且名称与被导入模块的 def 完全一致；冒烟测试必须实际调用函数（不能只 py_compile）才能抓到此类 bug。

[Edit 误重复行] 对 _emit 函数做 Edit 时，old_string 只匹配了函数体的前两行，new_string 重复了 `await ctx.emit(event)`，导致事件被发送两次 → Edit 的 old_string 要覆盖完整待替换块，替换后必须 Read 复核；批量编辑后立即 py_compile + 实际调用验证。

[ghost 光标自我熔断] 逐行流式补全用"光标位置 == 触发位置"判断文档无变化，但 ghost 自身插入/追加会移动光标 → 第二行到达即被判"已变化"而杀掉 worker，多行补全永远只显第一行 → ghost 存在即视为无用户编辑（任何真实编辑都会经 textChanged 清除 ghost），仅在尚无 ghost 时校验光标位置。

[keyPressEvent 传错对象] 自定义 Enter 处理把 QTextCursor 当 QKeyEvent 传给 `QPlainTextEdit.keyPressEvent` → TypeError，有选区时 Enter 被吞 → 选区先 `cur.insertText("")` 删除再走统一逻辑；调默认处理器必须传原始 QKeyEvent。

[重渲染抹掉流式提示] finish_ai 对 `[_ai_start, _ai_end]` 区间做 Markdown 重渲染，会把流式期间插在区间内的守门/压缩提示一并抹掉（闪现后消失）→ 流式期间的 note 记入 `_ai_notes`，重渲染时拼回 HTML 头部。

[专家模型非 JSON 回复] 守门/守护专家要求 JSON 协议，模型（或 mock 服务）返回非 JSON 时 extract_json 抛 ValueError → 属设计内的容错路径（fallback 不阻塞主流程），Err.log 出现此类记录先检查 expert_model 配置；代码必须保持"失败回落、绝不阻塞"。

[整段区间替换误删] 用 `[start, end)` 区间替换渲染富文本时，区间内可能夹带其它事件插入的内容（工具卡片）→ 整段被抹掉 → 先记录"区间内是否有非目标内容"标记，有则跳过替换或改为容器级渲染。

[并行编辑部分丢失] 对同一文件的多个 SearchReplace 并行提交，工具各自返回成功但实际只有部分修改落盘（其余维持旧代码）→ 表现为"宣称已修复但运行时报 NameError/行为不变" → 同一文件的多个修改必须**串行**逐个应用，应用后用 Grep/Read 复核关键标记确认落盘，再进入验证阶段。

[bridge exists() 2MB 误判] bridge 模式 `exists()` 走 `/fs/read`，>2MB 文件 413 被 catch 后返回 false → 快照/存在性判断对大文件恒判"不存在" → 文件存在性判断不能依赖 readFile（有 2MB 上限），改用后端 stat/直读或直接调 save 由后端兜底。

[前置门阻断兜底] 后端已实现"content 空串直读原文件"兜底，前端却在调用前加 `exists()` 前置门，而该前置门本身对 >2MB 文件误判 false → 兜底能力永远走不到（形同虚设）→ 兜底要端到端验证：兜底路径前不要加会让其失效的前置判断；新增兜底后补一条覆盖该场景的冒烟断言。

[SearchReplace 误删行] 对含 `with 锁:` + `try:` 的嵌套块做 SearchReplace 时，old_str 只覆盖到 try 头，替换后把块内首行（如 `ms = await ...`）误删 → 运行时 NameError → old_str 必须覆盖完整待替换块（含块内首行），替换后必须 Read 复核被删行仍在。

[threading.Lock 跨 await] async 函数里 `with threading.Lock():` 内 `await asyncio.to_thread(...)`——锁竞争时第二个请求的 `acquire()` 阻塞整个事件循环（FastAPI 单 loop）→ 并发请求卡死 loop → 跨 await 持锁必须用 `asyncio.Lock`（`async with`），同步临界区才用 threading.Lock。

[事件无人消费=future 悬挂] 新增"发出事件+等待 resolve"的机制（diff_preview_needed 等）时，若某入口（CLI/测试）没实现对应事件分支，future 永远无人 resolve → 默认路径卡死至超时（600s）→ 新增事件必须同步为所有渲染入口补分支；判定"无 UI 放行"的条件要覆盖所有无消费者场景（`ctx.emit is None`）。

[提示词模板大括号未转义] `.format()` 模板里的 JSON 示例 `{"kill": ...}` 未双写转义 → format 抛 KeyError 被 except 吞掉 → 整个功能"静默死亡"（副驾永远 uncertain，测试也测不出）→ 凡 format 模板内嵌 JSON 必须双写大括号；每个提示词都要有一条 `PROMPT.format(...)` 冒烟断言。

[返回共享 Config 被改] 配置解析函数找不到注册表条目时直接 `return cfg`，调用方原地改 temperature → 全局共享配置被污染并随 save() 落盘 → 一律 `return replace(cfg)`，永不返回原对象。

[子Agent 生命周期事件灌主 UI] 专家 Agent 的 run_start/run_done 经包装 emit 直通主 UI → 每个专家新开 AI 气泡、run_error 误触收尾置 _busy=False → 包装 emit 处过滤子级 run_start/run_done/run_final；错误事件键名与主 UI 对齐（message）。

[按序号配对 LLM 输出] `zip(feedbacks, form_items)` 假设 LLM JSON 条目顺序/数量与输入一致 → 错位升级错误的任务 → 按名称字段（expert == title/expert_id）匹配，配不上就丢弃。
[按 title 索引任务必须先去重] LLM 计划里两个任务同名时，DAG 拓扑按 title 建 dict/判重 → 重复项永不入层也不进 leftover，整轮结果静默缺块 → parse_plan 阶段同名加后缀去重；系统自动注入的任务（如「工具查重」）也要先撞名检查。
[QThread 无引用被 GC] 后台推送线程局部变量返回后无人持有 → 引用计数归零 PyQt 包装器销毁而 C++ 线程还在跑 → "Destroyed while thread is still running"，且进程退出时任务被杀 → 模块级保活集合 add + finished 移除；关键路径（关机前）用有界 wait。
[快照密钥排除要穷举] 快照 config 白名单排除只列了 api_key/sync_password，新增的 search_api_key/drift_api_key 漏网，随无口令冷备副本明文上传 → 每新增敏感配置字段必须同步进排除列表与 to_public 掩码，并加测试断言。
[配置开关必须三层贯通] 功能开关只在 config 定义、处理器/匹配序列也写好，但工具定义列表漏加 → LLM 永远看不到该工具，开关开了也形同虚设（delete_file 即前车之鉴） → 每个开关贯通三层：config.py 字段 → build_tool_defs 暴露 → settings_dialog 展示，缺一层即静默失效，并加断言测试锁住。
[Qt 控件 API 要看准类属] 在 QTextEdit 上调 setOpenLinks/anchorClicked（属子类 QTextBrowser） → 启动即 AttributeError，且单元测试不构造窗口就永远测不出 → 需要链接拦截就用 QTextBrowser（继承 QTextEdit，append 等全兼容）；GUI 启动路径必须有 offscreen 实例化回归（test_gui.py）。
[PyQt5 高 DPI 与 QSS 属性] 不设置 AA_EnableHighDpiScaling → Windows 高分屏按物理 96dpi 渲染，全局字体偏小布局显窄（必须在 QApplication 创建前设置）；QSS 写了文档之外的自造属性（如 titlebar-padding） → 报 Unknown property 且该条规则失效 → 只用 Qt 文档列出的属性，测试断言 stylesheet 不含已知无效属性；活动栏/按钮用生僻 Unicode（如 ⌁）在 Windows 字体无字形显示为方框，选全字体通用字符并补 tooltip。
[测试绝不允许吞异常假绿] 测试入口在 finally 里 os._exit(0)（或 swallow 后固定返回 0） → 崩溃被吞、退出码恒 0，上轮全绿其实是假绿，用户一跑就崩 → 测试入口必须：成功才退 0；异常打印堆栈后以非零退出（为避免 Qt 析构挂起可用 os._exit(1)，但绝不能在 finally 无条件退 0）；PyQt5 的 resizeDocks 等 C++ 重载签名必须显式传 orientation，缺参即 TypeError。
[Cookie secure 硬编码] set_session 的 secure 属性写死 False，注释声称"动态决定"但实际不接受 request 参数 → HTTPS 生产部署时 Cookie 可被中间人降级截获 → secure 必须根据请求 scheme（X-Forwarded-Proto 或 url.scheme）动态决定，set_session 需接受 request 参数。
[SSRF 仅阻止同端口] LLM 代理的 _validate_base 只阻止指向本服务自身端口的请求 → 攻击者可设 base_url 为 http://127.0.0.1:6379 探测内部 Redis/PG → 必须阻止所有非环回内网地址（10.x/172.16-31.x/192.168.x）；环回地址由 llm_allow_loopback 开关控制（兼容本地 LLM）。
[SVG fill="none" 被 blanket 替换] icon() 用 `fill="[^"]*"` 全量替换为 currentColor → fill="none"（轮廓图标背景透明）变 currentColor 使图标变实心色块；fill="white"（背景层）在深色主题变深色不可见 → 正则用负向前瞻 `fill="(?!none|white)[^"]*"` 排除 none/white。
[上下文压缩丢弃 system prompt] compressMessages 返回 `[{摘要}, ...recent]`，原始 system prompt（含安全约束/路径规范/工作区代号）被永久丢弃 → 压缩后 AI 不受"只能写相对路径"约束 → 必须保留 `messages[0]`，返回 `[messages[0], {摘要}, ...recent]`。
[后端不强制前端开关] 功能开关（如 allow_ai_delete）仅在前端检查，后端 API 不校验 → 攻击者直接 POST API 绕过前端防护 → 后端必须强制校验配置开关，前端检查只是 UX 优化不是安全边界。
[异常信息直接回显] `raise HTTPException(500, f"执行失败: {e}")` → 异常可能含文件路径/内部 IP/堆栈，泄露服务器信息 → 用泛化提示（"执行失败"），详情写 Err.log/审计日志而非回传客户端。
[内存字典无 GC 非 deque 类型] _gc_dict 只清理空 deque 条目，_code_fails（list 类型）永不清理 → 放弃注册的用户失败计数永久驻留，内存泄漏 → 对非 deque 类型的限速字典需单独 GC 逻辑（扫描过期条目删除）。
[SSE 子进程未 kill] run_command 的 asyncio.subprocess 在客户端断开连接后未 kill，stdout/stderr 管道 fd 泄漏累积 → 长时间运行后进程 fd 耗尽 → async generator 必须 try/finally，finally 中 proc.kill() + await proc.wait() + 显式关闭管道；httpx.AsyncClient 同理 finally 中 await client.aclose()。
[SSE reader 未 cancel] 前端 sseFetch 的 ReadableStream reader 在 fetch abort 或异常路径未 cancel → 浏览器持有流引用，多次对话后内存渐增 → try/catch/finally 三段式，finally 中无条件 try { reader.cancel(); } catch(e){}；AbortController 主动中止 fetch 请求。
[对象引用未回收] 清空对话仅清 DOM，messages 数组与卡片引用对象仍驻留 → 长时间使用内存渐增 → 新增 cleanup()：清空时显式置空数组、移除所有事件监听器、释放对象引用。
[报警只读不拦清理] v8.3 只读标志只拦了写工具，commit_round/_gc_sessions 仍在报警后删除回退点并淘汰旧快照 → 用户看到"已停止清理"实际清得最狠 → 一切清理入口（commit 的 rollback 删除、_gc_sessions）开头必须检查 is_readonly()；出错/取消路径用 keep_rollback 参数保留回退点。
[计数器传参丢默认] 轮内回退点函数带 round_no 默认 0，调用方不传序号 → 每次覆盖 snap_0.json，round_keep_rollback 形同虚设 → add_tool_call 必须返回自增序号（meta.call_count），tool_rollback 用该序号命名文件，杜绝默认 0。
[GUI 后台线程直接清共享状态] _finish_round_worker 后台线程无条件 set_current_round("") → 与用户新轮 begin_round 竞争，旧轮清掉新轮记录 → 清空前校验 current_round() == rid，且放 finally 确保不悬挂。
[新功能字段只有配置无消费] ai_decision_delay_s 只在 config/settings 出现，执行路径不读 → 设置改了半天毫无作用 → 每个新配置字段必须追查到消费方（审批/预览超时自动决策），无消费方等于死代码。
[功能写好未接线] list_sessions/rollback_points/restore_* 实现齐全但没有任何 UI/事件调用 → 用户永远用不上 → 新功能验收标准=走通一条真实调用链（恢复对话框/四层树/删除审查），仅 py_compile 抓不到。
[自动 min_size 只增不减] 依赖树下限= max(旧, size×0.3) 且写后不更新 → 合法缩容被判 too_small 报警锁死整个应用 → 自动下限随当前 size 浮动（写操作重置、变大跟随上调、变小不追），expert locked 值才固定。
[QThread 引用覆写被 GC] _SuggestThread 每次发送覆写 self._suggest_thread 引用，上次未结束即失去 Python 引用 → "Destroyed while thread is still running"崩溃 → 模块级/实例级保活集合 add + finished 移除，closeEvent 有界 wait。
[工作线程直接操作 Qt GUI 对象] Agent 跑在 QThread + asyncio.to_thread 线程池里直接调用 QScreen.grabWindow/QWidget 等 → 跨线程访问 GUI 对象属未定义行为，偶发黑屏/崩溃（非主线程构造 QApplication 直接 qFatal）→ Qt 抓图类操作必须经 QObject 桥 + QMetaObject.invokeMethod(BlockingQueuedConnection) 投递回主线程事件循环执行，且桥对象 moveToThread(app.thread()) 是投递生效的前提；无 QApplication 时返回明确失败而非硬造实例。
[新增执行代码工具绕过审批门] 新工具（如 app_screenshot）直接运行工作区脚本却不走 _request_approval → 等同免审批的 run_command，破坏审批模型（approval_mode=all 形同虚设）→ 一切可执行任意代码的工具必须复用 _request_approval 审批门；danger 模式对纯脚本路径 is_dangerous=False 自动放行，all 模式仍由用户确认。
[多模态图片打爆 API] 截图 base64 后直接塞进多模态请求，高 DPI 下 PNG 数 MB → API 拒绝/内存尖峰 → 编码前必须限文件大小（app_shot.MAX_IMG_BYTES=3MB），超限报错引导减小截图。
[非流式 content 被 onDelta 门控] llmChat 的非流式分支 `if (content && onDelta)` 把内容累加挂在回调门控上，未传 onDelta 的调用（ui_review/compressMessages/AOE）永远拿到空串 → 新功能静默失败 → content 累加必须无条件，onDelta 只作通知；任何"拿 LLM 结果"的调用点都要检查传入回调是否可能缺失。
[网页版桥盲审] 审批载荷只有 script/path 无 command 字段，审批卡片渲染 `ev.payload.command` 为空 → 用户批准运行任意 Python 脚本却看不到脚本路径，审批门形同虚设 → 审批载荷必须带展示字段（如 command: `python <script>`），addToolCard 参数白名单同步补充。
[网页版图片端点只看后缀] /fs/image 按扩展名推断 mime 且不校验内容 → 任意工作区文件可借"图片"名义 base64 外发 → 用文件头魔数判定真实格式，非图片 415。
[网页版开关不裁剪工具] 开关只在提示词条件里用，getToolDefs 仍无条件注册 → 用户关掉开关 LLM 仍能调用 → 工具 defs 与提示词都必须按开关过滤（两层一致）。
[异步审批 future 悬挂] 审批交互协程被取消（Ctrl+C）或渲染异常时不 resolve ApprovalGate future → Agent 卡死到 600s 超时 → 审批分支必须 try/except（含 CancelledError）兜底 resolve(False)，且用会话级 asyncio.Lock 串行化共享 stdin 的多路并行审批。
[重定向输出编码崩] Windows GBK 终端/重定向下 print 特殊字符（✓/⛔/▶/…）抛 UnicodeEncodeError，被多层 except:pass 吞掉后事件整段静默丢失 → 入口统一 sys.stdout.reconfigure(errors="replace")，并预截断大字段后再序列化。
[CLI 取消丢历史] Ctrl+C 取消分支直接 return 跳过历史同步 → 本轮已产生内容不落盘 → 历史同步放 finally（取消/异常/正常三路径统一），内存侧截断防无限增长。

[标签页只切 hidden 未切 active] 内容页 CSS 由 `.right-tab.active {display:flex}` 控制，但点击处理只移除 `hidden` → 标签高亮变化而 Terminal/Files 内容仍不可见 → 标签按钮、内容页的 active 与 hidden 三种状态必须在同一循环原子同步。

[window.open noopener 恒返回 null] 用 `window.open(url,'_blank','noopener')` 的返回值判断“是否被拦截”，但带 `noopener` 特性时规范上恒返回 null（与是否拦截无关）→ browser_open 每次都误报“被拦截” → 去掉 `noopener`，改为 `window.open(url,'_blank')` 后手动 `w.opener=null`，且不要用返回值判成功/失败。

[桥接端点透传本地错误回显路径] 复用桌面工具函数时把其原始 `output` 直接透传给前端，失败分支常含本机绝对路径/浏览器 stderr → 与“响应永不返回绝对路径”红线冲突 → 桥接层对失败结果统一泛化回显，异常细节写 Err.log 不回传。

[命名空间声明与调用不一致] 文件树只声明全局 `refreshTree()`，其它模块统一调用 `Tree.refresh()` → 点击 Files、写文件后刷新、编辑器保存等路径运行时 TypeError → 公共命名空间必须暴露兼容方法，并用真实 UI 点击覆盖跨模块调用链。

[静态资源缓存掩盖前端修复] HTML 更新后浏览器仍复用旧 CSS/JS，普通 reload 看起来代码未生效 → 发布批次为本地静态资源加版本查询串；UI 验证必须确认实际加载的资源 URL 再判定修复失败。

[LLM 隐藏参数绕过审批门] 审批门用 `args.get("_pre_approved")` 之类的隐藏参数旁路，而 args 直接来自 LLM 生成的 tool_calls JSON → 提示注入输出 `_pre_approved=true` 即可免审批执行 run_command/脚本 → 审批门放行逻辑绝不放任何 LLM 可控的旁路参数；内部自动放行必须用可信上下文标记（非模型输入）。

[delete_file 无审批门] 破坏性删除工具只查开关/路径/只读，既不 `_request_approval` 也不 `_write_guard` → 开启 ALLOW_AI_DELETE 后即静默数据丢失/专家越权删清单外文件 → 一切破坏性工具（删/命令/执行脚本）必须统一走审批门；专家实例额外走写权校验。

[无头浏览器 SSRF] browser_read/screenshot 的 URL 校验只查 http(s) 协议与长度，不拦内网/环回 → 无头浏览器会执行 JS，可被网页诱导抓取云元数据 169.254.169.254 或内网服务 → 无头抓取/截图 URL 必须校验回环/内网/链路本地/保留地址；DNS 多结果判定用 any（任一命中即拒绝）防 rebinding。

[hex 形式 IP SSRF 旁路] SSRF 校验的 host 解析只走 ipaddress + inet_aton，两者都不识别十六进制/混合数值形式（0x7f000001、0x7f.0.0.1）→ 校验放行而 httpx/浏览器按数值 IP 解析为 127.0.0.1 → 旁路成功 → host 解析链必须加「数值形态归一化」（GURL 语义：0x 段按 16 进制、普通段按十进制、1/2/3 段右对齐展开），归一化结果参与环回/内网判定；仅对 `[0-9a-fx.]+` 全匹配的 host 做归一化，域名解析失败自然回落 getaddrinfo 不误伤。

[漂移回本地失败路径仍复位] 「漂移回本地」回调不判断拉取/合并结果就 end_drift+finish+解锁 → 服务器不可达时云端产出从未合并进本地，且 finish 复位未回传计数，下次推送用缺消息的本地快照覆盖服务器 → 云端产出永久丢失 → 合并函数必须返回成功状态，仅合并成功才复位漂移状态与只读锁；失败保持锁定并提示重试。

[desktop 别名导入恒失败] webui 服务端写 `from desktop import ...`（bridge/snap_bridge/meta_bridge/proxy/snap_util）——sys.path 只有 repo 根（bridge.py:35 前置），`desktop` 别名不存在，导入恒 ModuleNotFoundError 且被 try/except 静默吞掉 → 功能整体死代码：v8.25 用户资产拦截在 webui 端从未生效（写放行）、/checkpoint/* 一类端点 501 → 服务端懒导入一律用 `from pyqt.desktop import ...`（正确形式见 lite_server.py / bridge.py:1481）；冒烟必须真实调用新接线端点（workcopy 403/200 断言即抓出此类）。遗留同类 meta_bridge×3/proxy×1 已于 v8.32 修复激活（toolsmith×3 早于 v8.26 阶段3 修复）；前置「desktop.config 不认 DEVERAI_DATA_DIR」已同轮解除（与 webui/lite 同款 env 重定向，未设置时行为不变）。

[漂移登记线程退出竞态] closeEvent 发起的「登记漂移」QThread fire-and-forget 不等待 → 登记请求可能未发出就关窗（服务器不计数），且解释器退出时 QThread 活销毁 → 关窗路径对关键登记线程有界 wait（2.5s），线程注册模块级保活集合 finished 移除。

[加密主快照被明文覆盖] 服务器 /chat 把无口令冷备压缩流直接回写主快照文件 → 用户设了同步口令后，加密主副本被静默降级为明文（功能不报错，隐私承诺被破） → 回写前判定现有主快照格式（尝试按冷备流解包），加密快照绝不覆盖；云端消息只进冷备文件，由 /pull 附带 cold 副本回传，桌面端历史以冷备为准（冷备恒为超集）。

[进程终止无白名单硬闸] exe_close 对任意 pid 直接 taskkill /T /F → 提示注入可诱导杀死任意进程 → 工具层维护 exe_launch 启动成功的 pid 白名单，close 只允许白名单内 pid / 属主 pid 匹配的窗口；click 坐标限屏幕范围内。

[数值参数 int() 无守卫] 工具层 `int(args.get("hwnd") or 0)` 对 LLM 传入的非数字字符串抛 ValueError → 被 dispatcher 兜底转「工具执行异常」污染 err_mirror、体验差 → 所有 LLM 可控数值参数必须 try (TypeError, ValueError) 包裹后优雅拒绝。

[数值 host 归一化误伤域名] 归一化函数对纯 `[0-9a-f]` 组成的多段域名（如 dead.beef）按 16 进制展开可能误判 → 段值超界（>0xFF/>0xFFFFFFFF 等）必须返回 None 回落原始解析路径，不得直接拒绝。

[大输出子进程内存尖峰] `subprocess.run(capture_output=True)` 全量缓存 --dump-dom 输出 → 超大/恶意页面打爆内存 → Popen + 独立线程分块 drain（64KB 块、8MB 上限），超限 kill 进程，`join(5)` 有界收尾；超时路径 kill 后必须回收。

[审批弹窗盲批] 审批弹窗只渲染 `payload.get("command")`，非命令类工具（exe_*/浏览器自动化等）payload 无 command 字段 → 弹窗完全空白，用户看不到任何内容却要点允许/拒绝 → 审批弹窗必须做通用摘要：无 command 时列出工具名 + 关键参数字段（逐字段截断）+ note，空 payload 显示"(无参数)"，绝不空白。

[进程白名单只增不减] 启动进程 pid 白名单只 add 不删 → 白名单无限增长且 pid 被系统复用后误杀无关进程 → 白名单用 dict[pid→ts] 上限淘汰最旧，进程被终止成功后立即 pop；白名单随进程生命周期（重启即空，fail-safe 方向）。

[通配申报与具体申报的双向冲突] 文件所有权冲突检测只拦「先 * 后具体」方向，「先具体后 *」方向放行 → 通配写权与已有申报重叠时另一专家无感知 → 具体申报 vs 已有 `*` 阻断；`*` 申报（总司令授权，过副驾驶）记录放行但返回信息性冲突并在返回 note 中提示，授权语义高于冲突。

[本地推送吞掉云端续聊增量] 漂移未回传期间本地 /push 全量覆盖服务器冷备 → /chat 刚追加的 from_server 消息被吞、unacked 已 +1 → 幽灵锁定/消息丢失 → 漂移 active 且 unacked>0 且冷备存在时，/push 跳过冷备覆盖（主快照仍覆盖，云端增量经冷备回传）。

[Chrome 孙进程残留] 超时/超限时只 kill 无头浏览器根进程 → renderer/GPU 孙进程残留占用资源 → Windows 用 `taskkill /PID /T /F` 杀进程树，其余平台 proc.kill()；taskkill 自身 10s 超时兜底再 proc.kill()。

[路径规范化丢前导 ..] `_norm` 用 `lstrip("./")` 剥前导点 → `"../../x"` 规范化成 `"x"`，逃逸路径与正常路径同键 → 用 `strip("/")` + 前导 `..` 保留（`"../..x"` 与 `"x"` 不同键）；注释必须与实际行为一致。

[固定 tmp + os.replace 并发追加丢记录] 审计/journal 用固定 `.tmp` 文件 append 后 `os.replace`，多线程/多进程下互相覆盖或 PermissionError，审计记录大量丢失 → 单行 JSONL 追加直接用 `open(path, "a")` O_APPEND（单行 write 不破坏并发写入者），不要用固定 tmp + replace 模拟原子追加。

[dry_run 试运行污染内存] `repair_assets(dry_run=True)` 直接改 `self._assets` 只是跳过 `_save()`，长寿命对象后续任何 save 都会把试运行修改写盘 → dry_run 必须在 `copy.deepcopy` 副本上修复并报告，绝不触碰原内存对象。

[LLM files 字符串按字符迭代] LLM 返回 `files: "a.py"`（字符串）时，`for f in files` 逐字符拆解，文件分区/所有权申报全部错乱 → 提取 files 字段时先做类型归一化：`isinstance(files, str)` 包装为单元素列表，非 list/tuple/set 视为空。

[selector 拼进 JS 源码注入] 把用户/LLM 可控 selector 直接字符串拼进 `Runtime.evaluate` 的 JS 表达式错误分支 → 含引号即可闭合 JS，在受控页面执行任意脚本 → 所有嵌入 JS 的字符串字面量一律 `json.dumps` 生成；错误消息由 Python 侧拼接，JS 只返回通用错误码。
[引用条 innerHTML 注入] 把 LLM/工具输出/工作区文件内容直接塞进 `el()` 第三参（走 innerHTML）→ 含 `<img onerror>`/`<script>` 的内容在同源上下文执行 → 引用条 label/text 必须走 `textContent`（或 `esc()` 包裹），不允许直接传动态内容到 innerHTML。
[过滤态双击激活错条目] 卡片列表双击/详情按钮拿「过滤后下标」去索引 `_all_items` → 筛选激活时点 N 张卡片弹出第 N 条原始数据 → 写入「真实 __idx」字段（_all_items 真实下标），setData(Qt.UserRole, __idx) 而非 setData(Qt.UserRole, idx)；滚动联动同样按 __idx 反查。
[流式事件冲掉用户标记] 每次 `add_event` 调 `timeline.set_items(items)` 无条件 `self._markers=[]` → 直播态用户刚加的 marker 被立即抹掉 → `set_items(items, reset_extras=False)` 流式追加保留 marker/range；clear/set_history 走 `reset_extras=True`。
[模块开关只关一半] `ENABLE_TRACE_ADVANCED` 定义在 config 但仅关闭部分高级入口（其他仍可用）→ 开关形同虚设 → 消费侧（Timeline/Flow/Panel/View）必须全套检查：关闭时清空 marker/range + 隐藏操作下拉 + 隐藏详情按钮 + 断开 item_activated + 跳过时间线鼠标事件。
[case_sensitive 重写 token] 切大小写时 `[(t.lower(), True) for t in tokens]` → 已 lower 的 token 在敏感模式下匹配不上原始大小写内容 → 保存**原始 token**，匹配时按 `_case_sensitive` 决定是否 lower。
[单击+双击重复新增] mousedown 新增 + dblclick 再新增 → 双击一次产生两条标记 → mousedown 仅记录状态 + 位移阈值（6px）；mouseup 时若距离近，启动 250ms QTimer 延时新增；dblclick 抢先 `cancel` 掉 timer。
[Web init 重复挂监听] TraceView.init() 被多处调用 → 时间线 canvas 的 mousedown/mousemove/mouseup + 搜索框 input 等监听器重复注册，事件触发双倍 → init() 加 `_inited` 守卫；或保留单一调用入口。
[非模态 dialog 不销毁] QDialog.setModal(False) 但未设 WA_DeleteOnClose → 用户点 X 只 hide，引用计数仍持有，_previews 列表堆积 → setAttribute(Qt.WA_DeleteOnClose, True)；destroyed 信号出队清理。

[字符串 "false" 被 bool() 判真] 后端确认/开关字段用 `bool(body.get(...))` → `"false"`/`"0"` 全变 True，danger_ok、guard、sessions/begin 等确认协议可被字符串绕过 → 所有“确认/开关”类字段必须严格布尔解析：bool 直接取，字符串仅 1/true/yes/on 为真；三处共用同一 helper。

[危险正则三端手工复制漂移] desktop/tools.py、app/security.py、static/js/tools.js、lite.html 各存一份 DANGEROUS_PATTERNS，修正一处其余照旧 → 同一命令前后端判定不一致（前端漏判+后端放行或反之）→ 新增模式必须四端同步，并用同一组危险/安全样例做断言；Python 侧统一 `re.IGNORECASE|re.DOTALL`，JS 用 `[\s\S]*`，防换行拆分关键词。（v8.32 起桌面套件新增四端静态一致性锁 test_dangerous_patterns_four_end_sync——从四份源码抽取清单做集合比对，首跑即抓到 lite.html 的 \bpowershell 漂移并四端统一为强形式。）

[switchEl outerHTML 丢事件] `switchEl(...).outerHTML` 插进 innerHTML → onclick 是 DOM property 不随 outerHTML 序列化，开关变死控件 → 动态控件一律 DOM append，禁止模板字符串 + outerHTML 混用。

[web-cold 信封被服务端改写格式] sync_server 冷备解包后统一按桌面 zlib 格式 `_save_cold` 写回 → 浏览器端 /pull 拿回 zlib 却无 zlib 解压能力，跨端链路半途失效 → 冷备写回必须保持入参格式（web-cold 信封原样写回，zlib 才写 zlib），并加 /chat 后格式回归测试。

[只读检查 fail-open] 受保护写操作外层 `except Exception: pass` 吞掉只读状态查询异常后继续执行写操作 → 保护模块故障时反而放行破坏性动作 → 安全检查失败必须 fail-closed（落 Err.log 并拒绝），只有明确可降级的查询才允许放行。

[根目录删除缺闸] 文件/桥的 delete 对 `"."`/`""`/`"\\"` 解析到工作区根后未拦截 → ALLOW_AI_DELETE 开启时一次删光整个工作区 → 桌面工具与桥端都必须在 resolve 后立即 `if p == root: 拒绝`，且与系统目录保护各自独立生效。

[互斥标记在 await 之后置位] `if running: 409` 检查与 `running=True` 之间隔着 `await` → 两个并发请求同时通过检查 → 先占标记、后 await，finally 复位；任何互斥状态都不允许检查与置位之间有 await。

[固定 .tmp + os.replace 并发覆盖] 原子写用固定文件名 → 并发写同文件互相覆盖，失败残留 .tmp → 唯一临时名（pid+uuid）+ os.replace + finally 清理。

[数值 host 先 DNS 后归一化] 把 `_normalize_numeric_ip` 放在 getaddrinfo 之后 → Windows 上 `127.1` 解析失败，本应环回却报“域名无法解析”；且归一化前就做 DNS 会白做一次阻塞解析 → 数值形态 host 必须先归一化，归一化成功即按 IP 判定并跳过 DNS。

[保护只做桥端，FSS 直连全绕过] 文件保护只写在 bridge/desktop 后端，Chrome File System Access 授权后前端直接读写目录句柄 → Agent 可绕过 read/write/delete 保护读取 data/config.json 或写 static 源码 → 保护必须双端落地：后端按绝对路径，FSS 前端按相对路径首层/文件名执行同源规则，并加静态断言锁住。

[打码函数缺失时 fail-open] Agent 可见面板的脱敏用 `if (typeof mask==='function')` 兜底返回原文 → 旧缓存/版本错配时真实 Bearer/密钥直接进 Agent 上下文 → 脱敏链路必须 fail-closed：mask/maskCurl 缺失时返回「已隐藏」或整条隐藏，绝不输出原文。

[目录列举绕过文件读保护] 只保护 fs/read，fs/tree、grep、glob、file_search 仍可列出/搜索 data/backups 的文件名与内容 → 受保护数据被间接泄露 → 每个“读类端口”都要应用同一 read-protected 谓词：目录本身 403、根列表隐藏受保护子项、遍历时剪枝；新增读类工具必须同步检查。

[Cordis host `ctx.harness` Proxy 拒绝] 在 DSH Cordis dynamic plugin 的 host apply 内写 `ctx.harness.handle(...)` 时，host sandbox Proxy 通过 `rejectGuard` → `denyRead("harness")` 拒绝访问，错误：`sandbox ctx does not expose "harness"`。ctx 仅开放 `ctx.tools.register / ctx.on / ctx.provide / any service you declared in inject` → `harness` 是 Host builtin 全局符号（与 client 的 `React` / `host` / `styles` / `console` 同级），正确做法是直接 `harness.handle(method, fn)`（去掉 `ctx.` 前缀），并用 `typeof harness !== "undefined"` 安全探测，sandbox 也可能完全剥离某个 builtin。

[Cordis 客户端不声明 `inject` 服务访问即抛 `service "X" is not declared`] 在 client apply 内写 `ctx.locale.register(...)` / `ctx.locale.bind(...)` / `ctx.get("slots")` 等服务调用时，框架报错 `service "locale" is not declared by your plugin. Declare it on the plugin you return: { inject: ['locale', …], apply(ctx) { … } } — a plain `function` has no declaration site`。plugin 对象必须显式列出 `inject: [...需要的服务名...]`，框架才会把它们装进 ctx；也可以走 `ctx.get(name)`，但保险起见两者都加上 → 正确做法：返回的 plugin 加 `inject: ["locale", "slots"]`，并用 `ctx.get("slots")` 兜底。

[Cordis 客户端重复 locale 注册致命] 同一 plugin 内先注册了 `ctx.locale.register(NS, "zh", D)` 又在 update 包中再调一次（即使 Cordis 先调旧 cleanup），框架抛 `locale namespace "NS" already has locale "zh"` 直接 fatal loader 失败。`locale.register` 返回的 cleanup function 不是幂等保险，跨包更新不安全 → 防护式做法：包装 `safeRegister(loc, locale, dict)` 用 try-catch 包 register；catch 后仅 `console.log` 警告，不 push cleanup；`tn = ctx.locale.bind(NS)` 仍能拿到上一次注册的字典（新人若有未注册键则回退为 key 字面量——前端 `t(key)` 应做 `if (v == null || v === "") return key` 兜底）。

[动态 Cordis Plugin 重启即失] 所有通过 `cordis_define` 注册的 plugin 都活在当前 DSH 进程内存里：`Plugin and Package definitions exist only in the current process. define itself does not modify repository source, configuration, or disk, and definitions do not survive a process restart.` → 重要资产必须立即转静态：1) 创建 npm-package 结构（`package.json` 含 `name`/`type: "module"`/`main`/`exports: { ".", "./client" }`/`dsh.client` 块；2) `lib/index.js` 用 ES module 形式 `export const name` / `export const inject` / `export function apply(ctx)`；3) `lib/client.js` 用 `window.__ModuleLoader__.load({ id, factory: (require) => { var module={exports:{}}; ...; exports.apply=...; exports.name=...; exports.inject=...; return module.exports; } })`；4) 编辑 `~/.dsh/profiles/web/cordis.patch.yml` 加 `- insert: [{ id: ..., name: '@you/pkg' }, { id: ..., name: '@you/pkg', client: true }]`；5) 在 profile/web 目录运行 `pnpm install` 让 workspace 解析新子包（package.json 添加 `dependencies`，建立 node_modules symlink）；6) 重启 DSH → 静态 plugin 从盘上加载并 idempotent 启动，每次 `cordis_inspect` 都看到 current，不需重 define。

[同一模块混用 time.time 与 time.monotonic] acquire 用 monotonic 记心跳、try_acquire/heartbeat/reap 用 time.time 比较 → 两套时间基准相差数十亿秒，锁被瞬间误判过期、try_acquire 误放行（桌面测试第 312 行 assertion 失败）→ 所有“时间差/时效”字段必须选定同一基准（推荐 time.monotonic），涉及同一数据结构的读改写全部一起改，并加“刚 acquire 后其他 owner try_acquire 必须失败”断言。

[新密钥字段漏出快照/导出] 新增 dashscope_api_key 后只改 config/to_public，快照 build_snapshot、设置导出 secret_fields、网页 buildSnapshot、DevTools KEY_RE 四处仍按旧五字段排除 → 无口令冷备把 DashScope Key 明文上传服务器 → 每新增敏感配置字段必须同步 6 处：config.to_public、sync.build_snapshot、settings 导出/导入、static/js/panels.js buildSnapshot、static/js/devtools.js KEY_RE、测试断言。

[快照/回退端口绕过文件桥保护] app/snap_bridge 的 checkpoint/save、checkpoint/restore、sessions/rollback_point、sessions/rollback 只做 _resolve 越界校验，不套 bridge._is_read_protected/_is_protected → 登录用户可把 data/config.json 抄进 checkpoint 再取回，或经回退点覆写 app/desktop/static → 所有“新读类/写类端口”必须复用同一保护谓词；恢复类端口要在落盘前对快照内全部 rel 先全量校验，命中保护路径即整体拒绝。

[共享变量笔误只靠 compile 抓不到] `_MODELS_JSON` 定义被写成 `_MODES_JSON` → py_compile/AST import 全通过，功能每次运行才 NameError 且被 except 转成可读错误文案，长期静默失效 → 每个“读 JSON 文件”的函数都要有一条真实调用断言（不 mock 文件、直接调用），并优先用编辑器“查找引用”检查常量名。

[前端 String.strip 不存在] JS 里调用 `String(...).strip()`（Python 方法）→ 工具调用每次 TypeError 被 catch 成失败结果 → 新写 JS 工具必须 node --check + 实际调用链验证；跨语言迁移时逐方法核对（trim/strip、append/extend、dict/object）。

[DOMContentLoaded 早于配置加载导致 init 失效] voice-pet.js 在 DOMContentLoaded 检查 App.config.ENABLE_VOICE_ASSISTANT，但 App.config 由 main.js boot 异步加载 → 开关开启时 init 从未执行，点击入口 TypeError → 模块初始化的时序必须由统一 boot 显式调用；公开 init 要可重入/可延迟（开关关闭时不得把 _initialized 置真）。

[只读 Guard 只拦前端不拦后端] 网页版写/命令端点不检查 snap_bridge 的 Guard 只读态 → 绕过前端直接 POST 即可在只读态写文件/跑命令 → 后端每个写/命令端点统一加 _readonly_block，查询失败 fail-closed（拒绝而非放行）。

[固定 .tmp 在 app/sync_server 复活] 老模块修过唯一临时名，新入口又用 path.with_name(name+".tmp") → 多 worker/多线程并发写互踩 → 所有“原子写”必须统一唯一临时名 + finally 清理，或复用 storage.save_json。

[孪生副本分片编辑块序漂移] 对必须逐字节一致的双副本（如 lite/static/lite.html ≡ webui/static/lite.html、app/security.py ×2）按记忆逐条 edit，容易造成"内容都在但函数/块顺序不同"，功能等价却破坏字节一致纪律且肉眼难查 → 双副本改动后必须立刻用哈希比对收口（Get-FileHash 相等），不一致时以 git diff --no-index 的精确差异驱动补丁，或直接读取主副本区段做整段还原；禁止凭记忆拼装副本内容。

[测试时序错位误报应用 bug] 冒烟断言在错误的生命周期时点执行（例如先新建会话再向 self.history 追加消息，随后断言旧会话回放含消息）→ 应用逻辑正确却被判 FAIL → 写多状态流转的验收链前先列"哪个对象在哪个会话/槽位持有什么数据"的归属表；每个失败先区分「应用缺陷」与「测试脚本自身预期写错」，后者修测试并在报告中明示。

[收口验证时序] v8.18 收口声明「测试全过」，但最终代码留有悬空 try:（IndentationError，整个桌面套件不可运行）——收口验证跑在最终编辑之前 → 每轮收口验证必须在最后一次文件编辑之后执行；py_compile 全量静态 + 双测试套件缺一不可（_audit_tmp.py 一键入口即为此用）。

[同文件并行 Edit 竞态] 对同一文件（index.html）并行发多个 Edit 工具调用，后续编辑基于陈旧内容整体回写，先前编辑被覆盖（fs.js 版本号被回滚到旧值）→ 同一文件的多次 Edit 必须串行执行；编辑后 Read 复核终态。

[四端功能版本门控] 冒烟测试把 v8.18 终端环境池断言同时套在 lite 与 webui 上，而 lite 刻意不实现该功能（v8.18 裁决四端=webui 前后端+桌面前后端）→ lite 永久红 → 测试按版本能力门控（expect_term 参数）；新增「部分版本实现」的功能时，同步声明测试范围。

[JSON 端点被当资源 URL 直用] /fs/image 返回 JSON（base64 字段），v8.28 引用卡片却把端点 URL 直接赋给 img.src → 浏览器把 JSON 当图片解码必挂，扑克牌组/单图/lightbox 全部渲染失败，且冒烟无浏览器级断言测不出 → 「供 <img>/资源标签直用」与「供 fetch 解析」是两种契约：资源直用必须返回原始字节（raw 参数 + content-type），JSON 契约保持不变；新端点必须在使用方语义下真实调用一次（v8.29 补 raw 模式 + 4 条冒烟断言）。

[copy2 保留 mtime 破坏下游增量] v8.30 入库用 shutil.copy2（保留源 mtime），而 drift minimal 增量按 mtime > 上次推送基准筛文件 → 拖进来的旧文件永远进不了增量包，续算端工作区缺文件（冒烟测不出，只有顺着时间语义想才看得见）→ 「复制/落盘类动作」必须顺着下游的时间/顺序语义核对一遍：入库类复制用 shutil.copy（新 mtime）或显式 os.utime；同族陷阱还有 Windows 大小写不敏感路径让前缀过滤被 Uploads/ 变体绕过。

[AI 写入指纹登记缺失] file_protect.note_ai_write（AI 写入登记 actor=ai 指纹）定义后全仓库零调用——AI 生成的资产文件（.csv 等）无指纹记录，check_ai_write_block 内 sync_user_modified 把无记录文件补记 actor=human/user_modified，AI 下次编辑被「用户文件保护」永久拦截，且"AI 刚生成可继续改"分支永不生效 → AI 成功写入/编辑用户资产后缀文件后必须登记/刷新指纹（编辑后不刷新会把 AI 自己的编辑误判为用户修改并锁死）；接线点=桌面 tools.py write/edit + webui bridge + webui/lite_server + lite/lite_server（v8.32 五端修复），且仅 is_user_asset(rel) 时登记防状态文件膨胀；配套断言锁闭环（默认拦截→登记放行→用户修改识别→重新锁死→非资产不受影响）。
