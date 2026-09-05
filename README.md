# DeverAI — 「UI 即 Agent 运行地」AI 开发工作台

AI Agent 直接运行在本机进程内（桌面版为 PyQt6 进程、网页版为浏览器 JS），通过 OpenAI 兼容接口直连大模型，无需浏览器权限桥接即可操作工作区内的真实文件与真实命令行。

**核心理念**：本地记忆即资产，复用优先于重建；UI 即 Agent 运行地。

## 核心特点

| 特点 | 说明 |
|------|------|
| 算力漂移 | 工作期间按周期推送完整工作状态（含冷备副本）到用户自有服务器；退出默认自动漂移；服务器冷备 Agent 续聊（HTML 状态页手机可用）；本机开机自动拉取合并云端产出并去重 |
| 总司令调度文件分区并发 | 专家团 DAG 分层并行；文件分区调度器按申报文件冲突自动分批——写不同文件并行、写同一文件串行；租约锁 + CoW 原子替换 |
| WorkTree 独立安全备份审核 | 三层防线：文件级版本快照、任务级会话快照、依赖树校验；回退审核日志 `data/audit.jsonl` 供 AI 自查 |
| 自动化设计·搜索·工具池 | 工具设计专家、自研工具库 + 工具医生、资产银行资料检修、互联网搜索、三级文件匹配、CDP 浏览器直接操控、外部 exe 自动化 |
| 记忆·出关·外部 API 治理 | Agent 跨会话长期记忆（教训沉淀 + 自动召回）；外发内容前逐字复述要求 + 审核卡批准；外部 API 清单 + SSRF 防护代理 + 心跳泄露检查 |

> 本文档为首要入口文档。架构与设计决策见 `Design.md`；实现方法见 `Techniques.md`；用户偏好裁决见 `Fact.md`；常见错误见 `FreqErr.md`；版本演进见 `dev_log/`。

## 环境要求

- Python 3.12+（本仓库开发环境为 3.12.10 + PyQt6 6.11，无 PyQt5）
- 网页版运行时需可访问公共 CDN（jsdelivr 等，加载 marked/dompurify/highlight.js/monaco-editor）；Lite 版与同步服务器零 CDN 依赖
- 一个 OpenAI 兼容的大模型 API（DeepSeek / Kimi / 智谱 / Ollama / OpenAI 均可）

## 安装

```bash
pip install -r requirements.txt
# 或手动：
# pip install PyQt6 fastapi uvicorn httpx
# pip install cryptography   # 可选：快照加密导出（Fernet/AES-GCM）需要

# API Key 配置（二选一，不落盘到 git）：
# 1) 环境变量：DEVERAI_API_KEY / DEVERAI_SEARCH_KEY / DEVERAI_DRIFT_KEY / DEVERAI_DASHSCOPE_KEY
# 2) 本地文件：复制 api.txt.example 为 api.txt 后填入（已加入 .gitignore）
```

无构建步骤：网页版前端为零构建静态文件，克隆即用。首次运行会在 `data/` 下生成默认配置（幂等，不影响 git）。

## 启动（六入口）

| 入口 | 命令 | 默认地址 |
|------|------|----------|
| 桌面版（主形态，功能最全） | `python pyqt/main.py` | 本机 GUI |
| 完整网页版 | `python webui/web_main.py` | http://127.0.0.1:8765（`--port` / `--no-browser` / `--reload`） |
| 超轻量远控 Lite | `python lite/lite_main.py` | http://127.0.0.1:8733（零 CDN 单文件前端） |
| 命令行 CLI | `python pyqt/cli_main.py` | 交互式 Agent（`--model/--workspace/--mode/--no-color`） |
| 同步服务器 | `python sync_server.py` | 0.0.0.0:8765（部署在用户自有服务器；`--host/--port`） |
| DSH 插件版 | Cordis 静态插件 `@deverai/hub` | http://127.0.0.1:3080 |

首次使用：启动桌面版或网页版后，在设置中填入 API base_url / api_key / 模型名。API Key 仅存本机 `data/config.json`（网页版仅存浏览器 localStorage）。

### 同步服务器（算力漂移）

部署在用户自有服务器，提供快照存取（`/push`、`/pull`）、冷备 Agent 续聊（`/chat`）、漂移状态（`/drift/*`）、远程指挥（`/cmd/*`）与 HTML 状态页（`/`、`/chat/page`）。

```bash
# 环境变量
SYNC_TOKEN=...            # 远程鉴权令牌（客户端设置中填同一令牌）；未设置时仅环回可访问
DRIFT_API_BASE=...        # 冷备 Agent 直连 LLM（OpenAI 兼容 base_url）
DRIFT_API_KEY=...
DRIFT_ALLOW_LOOPBACK=1    # 允许 LLM 指向环回地址（本地 LLM 时需要）
SYNC_DATA_DIR=./sync_data # 数据目录（snapshots.json / cold.json / drift_state.json）

python sync_server.py --host 0.0.0.0 --port 8765
```

> 完整网页版与 sync_server 默认端口同为 8765；同一台机器同时运行需错开端口。

## 测试

```bash
python _audit_tmp.py            # 一键回归：gui + server 两套测试
python _audit_tmp.py server      # 仅双服务端冒烟（lite + webui + sync_server）
python -m pytest tests/ -v       # 桌面离屏回归 + 服务端冒烟
```

## 目录结构

```
DeverAI/
├── pyqt/            # 桌面版（主形态）
│   ├── main.py      #   桌面入口
│   ├── cli_main.py  #   CLI 入口
│   └── desktop/     #   Agent 核心/工具/专家团/快照/漂移/自动化等全部模块
├── webui/           # 完整网页版
│   ├── web_main.py  #   入口（FastAPI + uvicorn）
│   ├── app/         #   后端：鉴权/LLM 代理/本地资源桥
│   └── static/      #   零构建前端（Agent 循环在浏览器 JS 执行）
├── lite/            # 超轻量远控版（自包含，零 CDN）
├── sync_server.py   # 同步服务器（快照存取/冷备续聊/漂移状态/远程指挥）
├── tests/           # 桌面离屏回归 + 双服务端冒烟
├── data/            # 本机持久化（配置/历史/资产/快照/审计；含密钥，勿外传）
├── backups/         # 阶段性备份（时间命名，只读参考）
├── dev_log/         # 版本更新文档（按日期命名）
├── Design.md        # 架构与设计决策（当前状态）
├── Techniques.md    # 技术方案
├── Fact.md          # 用户偏好约束与冲突裁决
└── FreqErr.md       # 常见错误类型
```

## 安全红线（摘要）

- API Key 仅存本机；快照/导出不含明文密钥（六字段穷举排除）。
- 文件操作限制在工作区内（`resolve()` 防 `../` 越界）；系统目录与 `config.json`、`Err.log` 受保护；AI 删除默认禁止。
- 危险命令四端同源 + `danger_ok` 严格确认；命令桥对未确认的危险命令直接 403。
- 网页版 `--host 0.0.0.0` 会暴露本地资源桥，仅限可信网络；sync_server 未设 `SYNC_TOKEN` 时仅环回可访问，非环回部署必须设置令牌。
- AI 只见工作区文件大代号（codename），绝对路径由系统层翻译；Cookie 维持 HttpOnly。
- 外部 API 调用统一走 `/api/llm/ext_proxy`（SSRF 防护）；AI 不能直发邮件，仅存草稿由用户手动触发。

## 常见问题

- **网页版 JS 行为异常/缺新功能**：浏览器缓存了旧 JS，强制刷新（Ctrl+F5）；版本号以 `index.html` 的 `?v=` 为准。
- **8765 端口冲突**：完整网页版与同步服务器默认端口相同，给其中一个 `--port` 错开。
- **快照导入报「请先安装 cryptography」**：加密快照需要 `pip install cryptography`。
- **sync_server 返回 403**：非环回访问且未设置 `SYNC_TOKEN`；设置后客户端在设置中填同一令牌。
- **Lite 与完整版差异**：Lite 刻意精简（写上限 5MB、无快照桥/邮箱注册/项目下载、文件树无 modified），详见 `Design.md` §2.3。
- **运行错误排查**：统一读根目录 `Err.log`（5MB 轮转）。

## 维护纪律（贡献者必读）

- 任何功能开关必须三层贯通：`config.py` 字段 → `tools.build_tool_defs` 暴露 → 设置面板展示。
- 凡改 `webui/static/js/` 或 CSS，必须同步 bump `index.html` 对应 `?v=`。
- 危险正则四端同源：`pyqt/tools.py` / `webui/app/security.py` / `lite/app/security.py` / `webui/static/js/tools.js` / `lite/static/lite.html`。
- 全部 UI 禁止 emoji，统一 SVG 图标或 `[OK]`/`[X]`/`[!]` 文本标记。
- 收口验证必须跑在最后一次编辑之后：`python _audit_tmp.py` + 全量 `py_compile` + `node --check`。
- 关键更新备份至 `backups/YYYYMMDD_vXYZ/`，维护文档写 `dev_log/YYYYMMDD_vXYZ.md`。
