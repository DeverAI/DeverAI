<div align="center">

# DeverAI

**本地优先的 AI 工作台 · 教育工具 · 游戏与研究实验场**

把 Agent 装进本机进程，把真实文件与真实命令行交回给你。

[旗舰产品 DeverAI](./DeverAI) · [项目地图](#-项目地图) · [快速选型](#-快速选型) · [技术栈](#-技术栈)

</div>

---

## 我在做什么

这里不是「又一个聊天壳」。DeverAI 体系围绕三条主线展开：

| 主线 | 核心主张 | 代表仓库 |
|------|----------|----------|
| **AI 工作台** | UI 即 Agent 运行地；本地记忆即资产；算力可漂移、状态可冷备 | `DeverAI` · `DeverAI-Hub` |
| **教育与学习** | 自招/中考/OI/专注力，工具优先、可本地运行 | `learning-agent` · `zizhao-learning` · `OISystem` · `OpenIME` |
| **模拟与实验** | 用可玩的系统理解复杂现实（班主任、博士、战术飞行） | `homeroom-simulator` · `paper-writing-simulator` · `tactical-simulation` |

---

## 项目地图

> 共 25 个仓库（22 公开 / 3 私有）。按用途浏览，不必按名字猜。

### 0 · 旗舰 · AI 工作台

| 仓库 | 一句话 | 状态 |
|------|--------|------|
| **[DeverAI](https://github.com/DeverAI/DeverAI)** | 桌面/网页/CLI/Lite 四端 AI Agent 工作台。文件分区并发、专家团 DAG、算力漂移、WorkTree 三层备份、外发审核 | 主产品 |
| **[DeverAI-Hub](https://github.com/DeverAI/DeverAI-Hub)** | 个人主页、插件与包分发枢纽（含 model-router、Cordis 插件） | 枢纽 |
| **[dsh-harness-fork](https://github.com/DeverAI/dsh-harness-fork)** | DSH harness 分支，接入 DeverAI 路由设计 | 基础设施 |

<details>
<summary>DeverAI 能力速览</summary>

- **六入口**：PyQt 桌面 / 完整网页 / Lite 远控 / CLI / 同步服务器 / Cordis 插件
- **总司令调度**：专家团 DAG 分层并行；文件分区调度器（写不同文件并行、写同文件串行）
- **算力漂移**：工作状态周期推送自有服务器；冷备 Agent 续聊；开机自动合并去重
- **WorkTree 备份审核**：文件级快照 + 任务级会话快照 + 依赖树校验 + 审计日志
- **安全红线**：工作区 `resolve()` 防越界、危险命令四端同源、外发逐字复述 + 审核卡、SSRF 防护代理

</details>

### 1 · 教育 · 学习系统

| 仓库 | 一句话 | 形态 |
|------|--------|------|
| **[learning-agent](https://github.com/DeverAI/learning-agent)** | 题库 / OCR / AI 批改 / 专注模式（FastAPI + Web） | 服务 |
| **[learning-agent-v2](https://github.com/DeverAI/learning-agent-v2)** | v2：Android 客户端、课堂特性、开发日志 | 服务+端 |
| **[zizhao-learning](https://github.com/DeverAI/zizhao-learning)** | 上海中考自招每日素材：哲学/历史/古诗文 + 找茬追问 | 服务 |
| **[OISystem](https://github.com/DeverAI/OISystem)** | 信息学奥赛桌面专注系统：AI 引导、屏幕分析、ZZOI 集成、图论编辑器 | 桌面 |
| **[focus-tools](https://github.com/DeverAI/focus-tools)** | FocusTools / OISystem v1.0.0 发布包 | 发布 |
| **[OpenIME](https://github.com/DeverAI/OpenIME)** | 微软拼音用户词库管家：垂域术语导入（教材/竞赛/黑话） | 桌面 |
| **[zhongkao-widget](https://github.com/DeverAI/zhongkao-widget)** | 中考倒计时桌面小组件（在校状态/模考/农历/课程） | 桌面 |
| **[study-workbench](https://github.com/DeverAI/study-workbench)** | 个人学习工作台后端与部署 | 服务 |

### 2 · 游戏 · 模拟器

| 仓库 | 一句话 | 玩法内核 |
|------|--------|----------|
| **[homeroom-simulator](https://github.com/DeverAI/homeroom-simulator)** | 班主任模拟器：信息迷雾 + 家长群 + 传闻链 + LLM 对话 | 管理/叙事 |
| **[homeroom-simulator-flask](https://github.com/DeverAI/homeroom-simulator-flask)** | 同主题 Flask 后端 + 静态前端版 | 管理/叙事 |
| **[paper-writing-simulator](https://github.com/DeverAI/paper-writing-simulator)** | 九死一生：博士毕业模拟器（开题→盲审→答辩，八年清退） | 周回合生存 |
| **[battlian](https://github.com/DeverAI/battlian)** | 策略对战：Python + Web 双版本，AI 指挥官 | 策略对战 |
| **[tactical-simulation](https://github.com/DeverAI/tactical-simulation)** | FALCON-SIM 战术飞行：起飞/投弹/躲避/拦截等七任务 | 飞行模拟 |

### 3 · 桌面工具 · 效率

| 仓库 | 一句话 |
|------|--------|
| **[DeepTrans](https://github.com/DeverAI/DeepTrans)** | 划词翻译：任意应用选中文本 → 悬浮窗；小米 MiMo / DeepSeek 双引擎 + 机翻兜底 |
| **[AIrater](https://github.com/DeverAI/AIrater)** | 大模型产品评测：多 API、联网搜索、自动纠偏、可视化分析 |

### 4 · 研究 · 安全 · 创作

| 仓库 | 一句话 |
|------|--------|
| **[retrace](https://github.com/DeverAI/retrace)** | ReTrace：Windows 漏洞查找分析反向工具（抓包/注册表/反编译/MV3/LLM 审计） |
| **[qinglian-platform](https://github.com/DeverAI/qinglian-platform)** | 青少年互联网平台合规监测：举报 → 审核 → 案例 / 论坛 / 法律知识库 |
| **[airender](https://github.com/DeverAI/airender)** | AI 建模驱动 3D 视频渲染流水线（六阶段，可选本地扩散） |
| **[dba-attention-genetic](https://github.com/DeverAI/dba-attention-genetic)** | 注意力遗传研究：Difference-Based Attention 拟合实验 |

### 5 · 内部仓库（私有）

| 仓库 | 用途 |
|------|------|
| `toolkit` | 绘图 / 建模脚本与端口注册表 |
| `mindog` | MindDog K210 四足教育机器人（固件 / 云端脑 / App） |
| `qoder-src-research` | Qoder SRC 取证研究笔记与补丁证据 |

---

## 快速选型

| 你想… | 去这里 |
|-------|--------|
| 跑一个本地 AI Agent，操作真实文件 | [`DeverAI`](https://github.com/DeverAI/DeverAI) |
| 上海中考自招每日素材 + 追问 | [`zizhao-learning`](https://github.com/DeverAI/zizhao-learning) |
| OI 专注学习 / 对接 ZZOI | [`OISystem`](https://github.com/DeverAI/OISystem) |
| 把讲义术语塞进微软拼音 | [`OpenIME`](https://github.com/DeverAI/OpenIME) |
| 划词翻译桌面工具 | [`DeepTrans`](https://github.com/DeverAI/DeepTrans) |
| 体验「班主任 / 博士」生存压力 | [`homeroom-simulator`](https://github.com/DeverAI/homeroom-simulator) · [`paper-writing-simulator`](https://github.com/DeverAI/paper-writing-simulator) |
| Windows 逆向与漏洞观察 | [`retrace`](https://github.com/DeverAI/retrace) |
| 评测多家大模型产品 | [`AIrater`](https://github.com/DeverAI/AIrater) |

---

## 技术栈

- **主语言**：Python（PyQt6 / FastAPI / PySide6）· JavaScript / TypeScript
- **形态**：桌面 GUI · 零构建静态前端 · CLI · 本地服务 · Cordis 插件 · Chrome MV3
- **风格偏好**：stdlib-first、本地优先、少依赖、可打包、可离线
- **LLM 接入**：OpenAI 兼容（DeepSeek / Kimi / 智谱 / 小米 MiMo / Ollama 等）

---

## 维护约定（跨仓共性）

1. **密钥不进 git**：环境变量或本地 `api.txt` / `config.json`（已 gitignore）。
2. **设计/技术文档分仓**：`Design.md` / `Techniques.md` / `Fact.md` / `FreqErr.md` 是标配。
3. **危险操作可审计**：命令确认、文件快照、审计日志尽量三件套齐。
4. **UI 不堆 emoji**：统一 SVG 图标或 `[OK]` / `[X]` / `[!]` 文本标记。

---

## License

各仓库独立授权，以根目录 `LICENSE` 为准（常见为 AGPL-3.0 / GPL-3.0）。网络提供服务且修改本项目时，请遵守对应 copyleft 条款。

<div align="center">

<sub>DeverAI · Local-first AI workbench & experiment field · 最后整理于 2026-09</sub>

</div>
