"""AI Agent 核心：流式对话 + 原生工具调用循环 + 上下文压缩 + 子Agent委派。

运行模型：
  用户消息 → [上下文压缩] → LLM(stream) → 若含 tool_calls → 执行工具(审批门/流式命令) 
  → 追加 assistant+tool 消息 → 再入循环 → 直到无工具调用（finish_reason=stop）。

事件流（emit）：
  run_start / text_delta / tool_start / tool_result / compress /
  approval_needed(经工具) / subagent(子Agent包装) / run_done / run_error / run_cancelled
"""
import asyncio
import json
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import ctx_expert
from . import err_mirror
from .config import Config
from .context import compress_history, estimate_tokens
from .errors import log_error
from .llm import LLMError, stream_chat
from .tools import (
    TOOL_HANDLERS,
    ApprovalGate,
    ToolContext,
    build_tool_defs,
    select_tools,
)

BASE_SYSTEM = (
    "你是 DeverAI，一个运行在用户本地电脑上的 AI 编程助手，集成在类似 Cursor 的 IDE 界面中。\n"
    "你可以读写工作区文件、搜索代码、执行命令行、检索资产银行复用已有成果。\n"
    "工作原则：\n"
    "1. 修改文件前先 read_file 读取原文确认；用 edit_file 做精确修改，避免整文件覆盖。\n"
    "2. 执行命令前先想清楚，尽量一条命令完成；输出过长时只保留关键部分。\n"
    "3. 输出使用简洁的 Markdown；代码片段放在代码块中。\n"
    "4. 重复性/可复用的生成物应优先 search_vault 复用，完成后可 store_asset 入库。\n"
    "5. 复杂多步骤任务优先用 plan_and_execute 拆解并行执行。\n"
    "6. 不要臆造不存在的文件；不确定时用 list_dir/glob 确认。\n"
)

# v4 三形态 Agent 系统提示词
CHAT_SYSTEM = (
    "你是 DeverAI 的 Chat 对话形态：轻快、精准的对话助手。\n"
    "规则：\n"
    "1. 以解答、讨论、代码讲解为主；默认只使用只读工具（read_file/list_dir/grep/glob）了解现状。\n"
    "2. 不主动修改文件、不主动执行命令；用户明确要求时才提醒切换到 Builder 形态。\n"
    "3. 输出简洁 Markdown，代码放代码块；需要展示图表时可用 mermaid 代码块。\n"
    "4. 不臆造文件与事实；不确定先检索确认。\n"
)

EXPERTS_SYSTEM = (
    "你是 DeverAI 的 Experts 专家团形态入口。专家团开启时由编排器接管（总司令规划→专家并行执行→反馈表单）；"
    "若专家团开关关闭，请以资深多领域顾问的口吻工作：分角色思考（架构/前端/后端/测试），"
    "给出分工建议与关键风险；可用只读工具了解现状，但不主动修改文件。\n"
)

MODE_SYSTEMS = {
    "chat": CHAT_SYSTEM,
    "builder": BASE_SYSTEM,
    "experts": EXPERTS_SYSTEM,
}

# v6.3 决策52：AGENT.txt 安全规则注入（用户自用，让 AI 自觉遵守工作流）
# 精简提取 AGENT.txt 核心——相对路径/文件操作/错误记录/模块开关/不臆造/先读后改/精确改/重要文档只读
AGENT_SAFETY_RULES = """
【工作流安全约束（源自 AGENT.txt，必须遵守）】
1. 路径安全：写文件/命令只能用相对工作区的相对路径（如 "desktop/gui.py"），绝对路径由系统层翻译，你不可见也不可用。list_dir 返回的是文件名，read_file/write_file/edit_file 的 path 是相对路径。
2. 文件操作红线：所有文件读写必须使用内置工具（read_file/write_file/edit_file/list_dir/glob/grep），禁止尝试通过命令行（run_command 调用 cmd/shell 的 mv/cp/rm/sed/awk）修改、删除、复制项目文件。备份文件除外。
3. 修改前先读：修改任何文件前必须先 read_file 读取原文确认，用 edit_file 做精确范围修改，不要整文件覆盖（除非是新文件）。重要文档（Design.md/Techniques.md/Fact.md/AGENT.txt/FreqErr.md/Err.log）只读，不主动修改。
4. 不臆造：不要臆造不存在的文件或函数；不确定时先 list_dir/glob/grep 确认。
5. 错误记录：遇到异常不要吞，运行时错误会被系统捕获写入 Err.log；你发现重复出现的错误类型可提示用户记录到 FreqErr.md。
6. 模块开关意识：项目内功能用 ENABLE_XXX 开关控制，关闭的模块不要调用其工具；造工具/工具医生/检查点/暂存等都有开关。
7. 轻量化：不引入非必要的大型第三方库；能用标准库解决的不引新依赖。
8. 副作用后置：落盘/建目录等副作用必须在审批通过后才执行；审批异常一律升级用户，不静默放行。
""".strip()

# 网页版精简版（去掉桌面版特有项，供 static/js/agent.js 同步注入）
AGENT_SAFETY_RULES_WEB = """
【工作流安全约束（源自 AGENT.txt，必须遵守）】
1. 路径安全：写文件/命令只能用相对路径（如 "app/server.py"），绝对路径由系统层翻译。list_dir 返回文件名，read_file/write_file 的 path 是相对路径。
2. 修改前先读：修改前先 read_file 确认原文，用 edit_file 精确改，不要整文件覆盖。重要文档（Design.md/Techniques.md/Fact.md/AGENT.txt）只读。
3. 不臆造文件：不确定先 list_dir/grep 确认。
4. 错误不吞：异常由系统捕获写 Err.log，不要在输出里掩盖错误。
5. 轻量化：不引入非必要第三方库。
""".strip()


@dataclass
class AgentResult:
    text: str = ""
    summary: str = ""
    tools_used: int = 0
    rounds: int = 0
    usage: dict = field(default_factory=dict)
    cancelled: bool = False


class Agent:
    def __init__(
        self,
        cfg: Config,
        emit: Optional[callable] = None,
        history: Optional[List[dict]] = None,
        vault: Optional[object] = None,
        is_sub: bool = False,
        sub_label: str = "子Agent",
        parent_emit: Optional[callable] = None,
        approval: Optional[ApprovalGate] = None,
        depth: int = 0,
        expert_id: str = "",                     # v5: 专家身份（空=主对话/总司令）
        restrictions: Optional[list] = None,     # v5: 强制限制（如禁用某库）
        tool_defs: Optional[list] = None,        # v5: 显式工具集（专家用）
    ):
        self.cfg = cfg
        self.vault = vault
        self.is_sub = is_sub
        self.sub_label = sub_label
        self.depth = depth
        self.expert_id = expert_id
        self.restrictions = restrictions or []
        self.tool_defs = tool_defs
        self._todo_text = ""                     # v5: 工具裁剪依据（当前任务/用户消息）
        self.history: List[dict] = list(history or [])
        # 子Agent 共享父级审批门，审批按钮一次点击即可放行；
        # 无事件渲染者（emit=None）不创建审批门——否则危险命令 future 无人 resolve，卡满 600s
        self.approval = approval if approval is not None else (
            ApprovalGate() if emit is not None or parent_emit is not None else None)
        self._cancel = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None  # v8.14：cancel() 线程安全用
        self._artifacts: List[dict] = []
        self.output_text = ""
        self.tool_log: List[dict] = []

        if emit is not None:
            self.emit = emit
        elif parent_emit is not None:
            self.emit = self._make_sub_emit(parent_emit)
        else:
            self.emit = None

    # ------------------------------------------------------------------ #
    def _make_sub_emit(self, parent_emit):
        async def wrapped(ev):
            try:
                await parent_emit({"type": "subagent", "label": self.sub_label, "event": ev})
            except Exception:
                pass
        return wrapped

    async def _emit(self, ev: dict) -> None:
        if self.emit is not None:
            try:
                await self.emit(ev)
            except Exception:
                pass

    def _build_system_prompt(self, current_file: Optional[str] = None) -> str:
        mode = getattr(self.cfg, "agent_mode", "builder") or "builder"
        if self.expert_id:
            # v5 专家实例：用 Builder 内核提示词 + 专家职责约束
            parts = [BASE_SYSTEM + (
                f"\n【你的身份】专家团普通专家 {self.expert_id}。只负责分配给你的任务；"
                "先 todo_update 拆解待办再逐项完成（每完成一项标 done）；"
                "申报清单外的文件一律只读；重要文档不得碰；遇到实现不了/需求不清的情况直接如实上报，不要硬撑。"
            )]
        else:
            parts = [MODE_SYSTEMS.get(mode, BASE_SYSTEM)]
        parts.append(
            f"运行环境: {platform.system()} {platform.release()} / Python {sys.version.split()[0]}"
        )
        if current_file:
            # current_file 可能来自编辑器绝对路径——只注入相对工作区路径，守住大代号红线
            try:
                cf_path = Path(str(current_file)).resolve()
                cf_ws = Path(str(self.cfg.workspace or "")).resolve()
                current_file = str(cf_path.relative_to(cf_ws)).replace("\\", "/")
            except Exception:
                current_file = str(current_file)
            parts.append(f"用户当前打开的文件: {current_file}")
        # 相对路径安全红线：AI 只见工作区大代号，绝不注入绝对路径
        try:
            from .codename import workspace_codename
            ws_label = workspace_codename()
        except Exception:
            ws_label = "已授权工作区"
        parts.append(
            f"工作区: {ws_label}\n"
            "注意：工作区内文件路径均相对工作区根目录书写。"
        )
        if self.cfg.traffic_mode:
            parts.append(
                "【流量模式已开启】输出保持最精简纯文本，避免冗余 Markdown 渲染包；"
                "不发起大规模同步/上传任务。"
            )
        if self.cfg.token_mode:
            parts.append(
                "【Token 计费模式已开启】目标是显著减少模型调用次数：尽量在单次回复内完成，"
                "能合并的步骤合并；优先直接给出方案而非多次往返。"
            )
        if self.cfg.ENABLE_ERR_MIRROR:
            hints = err_mirror.get_hints()
            if hints:
                parts.append(hints)
        if self.cfg.ENABLE_VAULT and self.vault is not None and self.vault.list():
            parts.append(
                "本地资产银行有可复用资产，做图标/图表/模板/代码片段类工作前务必 search_vault。"
            )
        if self.restrictions:
            parts.append(f"【强制限制（违反会被工具层拒绝）】{'；'.join(self.restrictions)}")
        # v6.6 外交型任务硬性规定：允许全放行，但 AI 检测到不完全符合用户要求立即拦截
        parts.append(
            "【外交型任务硬性规定】browser_open/browser_read/browser_screenshot/web_search "
            "等对外交互任务默认全放行（无本地破坏性，不会弹审批框）。但你必须严格对照用户本次要求："
            "目标地址/搜索词与用户意图不完全一致时，禁止执行——要么修正参数后重试，要么停止并如实说明"
            "哪一点不符合用户要求。目标 URL 只允许 http(s)://，禁止 file:// 与 javascript:。"
        )
        # v6.3 决策52：AGENT.txt 安全规则注入（builder/experts/专家实例统一生效）
        if getattr(self.cfg, "ENABLE_SAFETY_RULES", True):
            parts.append(AGENT_SAFETY_RULES)
        # v8.4 UI 自截图确认纪律：制造本地 GUI 后必须截图自检（chat 形态只读不注入）
        if (getattr(self.cfg, "ENABLE_APP_SHOT", True)
                or getattr(self.cfg, "ENABLE_UI_REVIEW", True)) and mode != "chat":
            parts.append(
                "【UI 自截图确认纪律】制造任何本地 GUI 应用（PyQt/Tkinter 等 UI 脚本）后，"
                "必须用 app_screenshot 运行脚本并截图保存到工作区自检效果；"
                "截图后必须调用 ui_review 让视觉专家（配置的视觉专家模型，否则主模型/注册表图像模型）"
                "审查截图，按审查意见改进后再截图复验。未完成截图确认前不得宣称 UI 已完成。"
            )
        # v8.6 外部程序/浏览器自动化安全铁律（高风险能力，copilot 全程盯着做主）
        if getattr(self.cfg, "ENABLE_UI_AUTOMATION", False) and mode != "chat":
            parts.append(
                "【外部程序/浏览器自动化安全铁律】你被允许操作外部程序 exe 与浏览器"
                "（网页界面已转成可点击点）。这是高风险能力，必须时刻提醒自己：\n"
                "1. 千万千万不要做任何危险动作：不得删除/格式化/修改系统关键配置，不得执行破坏性命令，"
                "不得点击涉及付款、删除账户、发送验证码、发布内容、修改权限的按钮。\n"
                "2. 千万不要把信息外传：不得把工作区文件内容、代码、密钥、对话记录粘贴/上传到任何"
                "外部网页或第三方服务；不得在输入框填入本机敏感信息。\n"
                "3. 全程有 copilot（副驾驶）盯着做主：每个点击/输入/按键动作都会经副驾驶与用户审批，拿不准就停。\n"
                "4. 目标明确：操作外部产品只为理解其结构、对比与我们的差异/共同点，不是替代用户做决定或对外发布。\n"
                "5. 只操作用户配置的唯一 exe（ui_automation_exe）与明确授权的页面；不得启动其他程序。"
            )
        return "\n".join(parts)

    def _tool_defs(self) -> list:
        """v5: 工具集解析——专家用显式集合；token 计费模式按当前任务裁剪（KV 缓存友好方案 A）。"""
        if self.tool_defs is not None:
            return self.tool_defs
        if self.expert_id:
            defs = build_tool_defs(self.cfg, expert_mode=True, allow_delegate=False)
        else:
            defs = build_tool_defs(self.cfg)
        return select_tools(self.cfg, defs, self._todo_text, expert=bool(self.expert_id))

    # ------------------------------------------------------------------ #
    async def run(
        self,
        user_message: str,
        *,
        current_file: Optional[str] = None,
        max_rounds: int = 10,
        serial: Optional[bool] = None,
        qa_reply: bool = False,
    ) -> AgentResult:
        self._cancel.clear()
        self._artifacts = []
        self._loop = asyncio.get_running_loop()  # v8.14：cancel() 线程安全用
        self._todo_text = user_message  # v5: 工具裁剪依据
        # 主对话：解析主模型注册表条目（独立 url/api_key 生效 + 空/无效回退第一个 chat 模型）
        if not self.expert_id and not self.is_sub:
            try:
                from .models import get_llm_cfg, resolve_model_id
                mid = resolve_model_id(self.cfg, getattr(self.cfg, "model", ""))
                self.cfg = get_llm_cfg(self.cfg, mid)
            except Exception as e:
                # v8.14：记录而非吞没，便于排查模型配置问题
                from .errors import log_error
                log_error("模型配置解析失败，使用默认配置", e)
        # ---- v5 专家团模式：交由编排器接管整轮 ----
        mode = getattr(self.cfg, "agent_mode", "builder") or "builder"
        if (mode == "experts" and getattr(self.cfg, "ENABLE_EXPERTS", True)
                and not self.is_sub and not self.expert_id):
            return await self._run_experts(user_message, serial=serial, qa_reply=qa_reply)
        await self._emit({"type": "run_start"})
        full_history = list(self.history)

        # ---- v4 上下文守门（pre-round）：分块保留 / 永久禁用，失败不阻塞 ----
        call_history = full_history
        if self.cfg.ENABLE_CTX_EXPERT and self.cfg.api_key and not self.is_sub:
            await self._emit({"type": "gate_start"})
            keep, bans, reason = await ctx_expert.gate_history(self.cfg, full_history, user_message)
            for uid in bans:
                ctx_expert.ban_block(uid, by="expert")
            call_history, stats = ctx_expert.assemble_history(full_history, keep)
            await self._emit({"type": "ctx_gate", "stats": stats, "reason": reason, "banned": bans})

        # 本轮新增交互镜像：无论守门如何裁剪调用上下文，完整历史都必须完整落盘
        self._exchange: List[dict] = [{"role": "user", "content": user_message}]
        messages = call_history + [{"role": "user", "content": user_message}]
        messages, _ = await compress_history(self.cfg, messages, emit=self.emit)
        final_text = ""
        usage_total = {}
        tools_used = 0
        rounds = 0

        for r in range(max_rounds):
            if self._cancel.is_set():
                break
            rounds += 1
            system = self._build_system_prompt(current_file)
            msgs = [{"role": "system", "content": system}] + messages
            try:
                text, calls, finish, usage, visible_tools = await self._consume_stream(msgs)
            except LLMError as e:
                # v8.15 检修：异常/取消轮此前直接 raise 跳过历史落账，但工具副作用已发生
                # （文件已写/命令已跑），下一轮 AI 对自己半途的改动毫无记忆——先入档再抛
                self.history = full_history + self._exchange
                raise
            except asyncio.CancelledError:
                self._cancel.set()
                self.history = full_history + self._exchange
                raise  # 必须重抛：让 wait_for 熔断 / 取消真正生效（S2）
            except Exception as e:
                log_error("Agent 流式对话异常", e)
                self.history = full_history + self._exchange
                raise

            if usage:
                # v8.14b：多轮累计而非覆盖（此前只保留最后一轮的 token 用量）
                for k, v in (usage or {}).items():
                    if isinstance(v, (int, float)):
                        usage_total[k] = usage_total.get(k, 0) + v
            if finish == "length":
                await self._emit({"type": "text_delta", "content": "\n\n> [!] 输出达到长度上限，可能不完整。"})

            if calls:
                await self._emit({"type": "assistant_tool_calls", "calls": calls, "text": text})
                # 文本已通过流式 text_delta 实时展示，这里只把内容挂到协议消息上，避免重复渲染
                await self._execute_tools(calls, messages, text=text, visible_tools=visible_tools)
                tools_used += len(calls)
                continue

            if text:
                final_text = text
            elif not final_text:
                final_text = ""
            break
        else:
            await self._emit({"type": "text_delta", "content": "\n\n> [!] 已达到最大工具调用轮数，已停止。"})

        # 把最终回复写入历史，保证下一轮对话上下文连续
        if final_text and not self._cancel.is_set():
            messages.append({"role": "assistant", "content": final_text})
            self._exchange.append({"role": "assistant", "content": final_text})

        self.output_text = final_text
        # 完整历史 = 原全量历史 + 本轮全部交互（不受守门裁剪影响）
        self.history = full_history + self._exchange
        result = AgentResult(
            text=final_text,
            summary=final_text,
            tools_used=tools_used,
            rounds=rounds,
            usage=usage_total,
            cancelled=self._cancel.is_set(),
        )
        # 资产评估入库（3.1）
        await self._maybe_vault_eval()
        # ---- v4 规则守护（post-round）：检查对话 AI 是否遵守规则，失败静默跳过 ----
        if self.cfg.ENABLE_CTX_EXPERT and final_text and not self._cancel.is_set() and not self.is_sub:
            try:
                g = await asyncio.wait_for(
                    ctx_expert.guard_check(self.cfg, user_message, final_text), timeout=30.0
                )
                if g is not None:
                    await self._emit({"type": "guard", "ok": g["ok"], "note": g["note"]})
            except Exception:
                pass
        await self._emit({"type": "run_done", "result": result.__dict__})
        return result

    # ------------------------------------------------------------------ #
    async def _run_experts(self, user_message: str, serial: Optional[bool] = None,
                           qa_reply: bool = False) -> AgentResult:
        """v5 专家团入口：总司令规划 → 专家并行/串行执行 → 反馈表单 → 汇总。
        qa_reply 且上轮留有表单时走总司令问答，不重新规划。"""
        from .experts import run_expert_team, has_pending_form, answer_form  # 延迟导入避免循环依赖
        full_history = list(self.history)
        self._exchange: List[dict] = [{"role": "user", "content": user_message}]

        # ---- 问答模式：直接回复反馈表单 → 总司令答复 ----
        if qa_reply and has_pending_form():
            await self._emit({"type": "run_start"})
            try:
                text = await answer_form(self.cfg, user_message, self.emit)
            except asyncio.CancelledError:
                self._cancel.set()
                raise
            except Exception as e:
                log_error("专家团表单问答异常", e)
                await self._emit({"type": "run_error", "message": str(e)})
                raise
            self._exchange.append({"role": "assistant", "content": text})
            self.history = full_history + self._exchange
            await self._emit({"type": "text_delta", "content": text})
            result = AgentResult(text=text, summary=text, rounds=1)
            await self._emit({"type": "run_done", "result": result.__dict__})
            return result

        await self._emit({"type": "run_start"})

        def factory(expert_cfg: Config, task):
            edefs = build_tool_defs(expert_cfg, expert_mode=True, allow_delegate=False)
            edefs = select_tools(expert_cfg, edefs, task.goal, expert=True)

            async def xemit(ev):
                if self.emit is None:
                    return
                # 专家级 run_start/run_done 不入主 UI：避免每个专家新开 AI 气泡/误触收尾逻辑
                if ev.get("type") in ("run_start", "run_done", "run_final"):
                    return
                try:
                    ev = dict(ev)
                    ev.setdefault("expert_id", task.expert_id)
                    await self.emit(ev)
                except Exception:
                    pass

            return Agent(
                expert_cfg, emit=xemit, vault=self.vault, approval=self.approval,
                expert_id=task.expert_id, restrictions=task.restrictions,
                tool_defs=edefs, is_sub=True, sub_label=f"专家·{task.title}",
            )

        try:
            out = await run_expert_team(
                self.cfg, user_message, self.emit, vault=self.vault,
                approval=self.approval, serial=serial, agent_factory=factory,
            )
        except asyncio.CancelledError:
            self._cancel.set()
            raise
        except Exception as e:
            log_error("专家团执行异常", e)
            await self._emit({"type": "run_error", "message": str(e)})
            raise

        # 汇总文本：总规划 + 各任务状态 + 反馈表单
        lines = [f"【专家团总结】{out.get('summary') or user_message[:60]}", ""]
        for t in out.get("tasks") or []:
            lines.append(f"- {t.title}：{t.status}")
        form = out.get("form") or []
        if form:
            lines += ["", "【反馈表单】"]
            for it in form:
                kind = it.get("kind", "需要提问")
                lines.append(f"- [{kind}] {it.get('expert','')}: {it.get('question','')}"
                             + (f"（成本: {it.get('cost_estimate','')}）" if it.get("cost_estimate") else ""))
        final_text = "\n".join(lines)
        self._exchange.append({"role": "assistant", "content": final_text})
        self.history = full_history + self._exchange
        result = AgentResult(text=final_text, summary=final_text, rounds=1)
        await self._emit({"type": "text_delta", "content": final_text})  # 总结入聊天气泡
        await self._emit({"type": "run_done", "result": result.__dict__})
        return result

    # ------------------------------------------------------------------ #
    async def run_task(
        self, task: str, *, extra_context: str = "", max_rounds: int = 8
    ) -> AgentResult:
        """子Agent 独立执行子任务：全新上下文，可调用全部工具。"""
        content = task
        if extra_context:
            content = f"【前置上下文】\n{extra_context}\n\n【任务】\n{task}"
        return await self.run(content, max_rounds=max_rounds)

    # ------------------------------------------------------------------ #
    async def _consume_stream(self, msgs: list):
        text_parts: List[str] = []
        tool_calls: dict = {}
        finish = None
        usage = None
        # 工具裁剪可能触发 embedding/文件检索，丢线程执行，避免阻塞事件循环
        tool_defs = await asyncio.to_thread(self._tool_defs)
        async for ev in stream_chat(
            self.cfg, msgs, tools=tool_defs
        ):
            t = ev["type"]
            if t == "delta":
                text_parts.append(ev["content"])
                await self._emit({"type": "text_delta", "content": ev["content"]})
            elif t == "tool_call_delta":
                idx = ev["index"]
                tc = tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if ev.get("id"):
                    tc["id"] = ev["id"]
                if ev.get("name"):
                    tc["name"] += ev["name"]
                if ev.get("arguments"):
                    tc["arguments"] += ev["arguments"]
            elif t == "done":
                finish = ev.get("finish_reason")
                usage = ev.get("usage")
        text = "".join(text_parts)
        calls = [tool_calls[i] for i in sorted(tool_calls)]
        visible_tools = {d["function"]["name"] for d in tool_defs}
        return text, calls, finish, usage, visible_tools

    # ------------------------------------------------------------------ #
    async def _execute_tools(self, calls: list, messages: list, text: str = "",
                             visible_tools: Optional[set] = None) -> None:
        for call in calls:
            name = str(call.get("name") or "").strip()
            # 工具可见性 fail-closed：模型若输出本轮 defs 之外的工具名（chat 形态写工具等），
            # 即使 handler 存在也必须拒绝，不能仅靠 schema 裁剪
            if visible_tools is not None and name not in visible_tools:
                result = {
                    "ok": False,
                    "output": f"当前形态/开关下工具 {name or '(空)'} 不可用，已拒绝。",
                    "meta": {"denied": True},
                }
                handler = None
                raw_output = result.get("output", "")
                self.tool_log.append(
                    {"call_id": "", "name": name, "args": {}, "ok": False}
                )
                await self._emit({
                    "type": "tool_result",
                    "call_id": "",
                    "name": name,
                    "ok": False,
                    "output": raw_output,
                    "meta": result.get("meta", {}),
                })
                # v8.17：生成唯一占位 id，防 OpenAI API 400（要求非空 tool_call_id）
                _denied_id = f"denied_{id(self):x}_{len(self.tool_log)}"
                messages.append({
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [{
                        "id": _denied_id,
                        "type": "function",
                        "function": {"name": name, "arguments": ""},
                    }],
                })
                messages.append({"role": "tool", "tool_call_id": _denied_id, "content": raw_output})
                text = ""
                continue
            args_str = str(call.get("arguments") or "")
            # S4+P2-5 审查修复：call_id 统一加 Agent 实例前缀——即使 LLM 原始 id 在
            # 并行专家/子Agent 间相同（本地模型常见 call_0），共享 ApprovalGate 也不会互相覆盖
            raw_id = call.get("id") or f"call_{name}_{len(self.tool_log)}"
            call_id = f"{id(self):x}:{raw_id}"
            try:
                args = json.loads(args_str) if args_str.strip() else {}
            except json.JSONDecodeError:
                args = {}
                result = {
                    "ok": False,
                    "output": "工具参数不是合法的 JSON，请重新提供完整参数。",
                    "meta": {},
                }
                handler = None
            else:
                handler = TOOL_HANDLERS.get(name)
                if handler is None:
                    result = {"ok": False, "output": f"未知工具: {name}", "meta": {}}
                else:
                    if name == "search_tool":
                        # v5: 注入当前可见工具集，供元工具区分已裁剪隐藏的工具
                        args["_visible"] = [d["function"]["name"] for d in await asyncio.to_thread(self._tool_defs)]
                    ctx = ToolContext(
                        cfg=self.cfg,
                        emit=self.emit,
                        workspace=self.cfg.workspace,
                        call_id=call_id,
                        approval=self.approval,
                        agent=self,
                        vault=self.vault,
                        depth=self.depth,
                        expert_id=self.expert_id,
                        restrictions=self.restrictions,
                        user_intent=self._todo_text,
                    )
                    await self._emit(
                        {"type": "tool_start", "call_id": call_id, "name": name, "args": args}
                    )
                    try:
                        result = await handler(args, ctx)
                    except PermissionError as e:
                        result = {"ok": False, "output": str(e), "meta": {}}
                    except Exception as e:
                        log_error(f"工具执行异常: {name}", e)
                        if self.cfg.ENABLE_ERR_MIRROR:
                            err_mirror.record_error(
                                type(e).__name__, name, args_str[:300], str(e)
                            )
                        result = {
                            "ok": False,
                            "output": f"工具执行异常({type(e).__name__}): {e}",
                            "meta": {},
                        }
                    else:
                        # v6 防呆库增强：ok=False（审批拒绝/越界/权限类）也记录特征
                        if (not result.get("ok") and self.cfg.ENABLE_ERR_MIRROR
                                and str(result.get("output") or "").strip()):
                            err_mirror.record_error(
                                "ToolFail", name, args_str[:300], str(result.get("output"))[:300]
                            )

            # v8.9 外部软件探索记录：仅 exe/browser_ctl 的写操作记 journal；
            # 排除只读的 exe_journal 与 exe_list_windows，防止自我膨胀。
            try:
                if name in ("exe_launch", "exe_screenshot", "exe_click", "exe_type",
                            "exe_press_keys", "exe_close") or name in (
                            "browser_launch", "browser_navigate", "browser_click",
                            "browser_type", "browser_press_keys", "browser_close"):
                    from . import tool_journal as _journal
                    _journal.record(name, args, result)
            except Exception:
                pass

            # 记录生成物，供资产银行评估（3.1）
            if name in ("write_file", "edit_file") and result.get("ok"):
                meta = result.get("meta", {}) or {}
                rel = meta.get("path", "")
                snippet = args.get("content") or (args.get("new_string") if name == "edit_file" else "")
                self._artifacts.append(
                    {
                        "path": rel,
                        "kind": "code",
                        "size": meta.get("size") or len(snippet),
                        "content": snippet or "",
                    }
                )

            raw_output = result.get("output", "")
            self.tool_log.append(
                {"call_id": call_id, "name": name, "args": args, "ok": result.get("ok", False)}
            )
            await self._emit(
                {
                    "type": "tool_result",
                    "call_id": call_id,
                    "name": name,
                    "ok": result.get("ok", False),
                    "output": raw_output,
                    "meta": result.get("meta", {}),
                }
            )
            # M19: 工具结果写回协议消息时截断，避免上下文爆炸
            msg_output = raw_output
            MAX_TOOL_MSG = 20000
            if len(msg_output) > MAX_TOOL_MSG:
                msg_output = msg_output[:MAX_TOOL_MSG] + f"\n…(工具输出过长，已截断 {len(raw_output) - MAX_TOOL_MSG} 字符)"
            messages.append(
                {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": args_str},
                        }
                    ],
                }
            )
            messages.append(
                {"role": "tool", "tool_call_id": call_id, "content": msg_output}
            )
            # 同步镜像到本轮交互（完整落盘用）
            if getattr(self, "_exchange", None) is not None:
                self._exchange.append(
                    {
                        "role": "assistant",
                        "content": text or None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": args_str},
                            }
                        ],
                    }
                )
                self._exchange.append(
                    {"role": "tool", "tool_call_id": call_id, "content": msg_output}
                )
            text = ""  # 内容只挂在第一个 assistant 消息上

    # ------------------------------------------------------------------ #
    async def _maybe_vault_eval(self) -> None:
        if not self.cfg.ENABLE_VAULT or self.vault is None or not self._artifacts:
            return
        try:
            await asyncio.wait_for(
                self.vault.evaluate_and_store(self.cfg, self._artifacts, emit=self.emit),
                timeout=120.0,
            )
        except Exception:
            pass

    async def flush_artifacts(self) -> None:
        """v6：专家产物主动复用入库入口（experts 编排在每个专家完工后调用）。失败不阻塞。"""
        await self._maybe_vault_eval()
        self._artifacts = []

    def cancel(self) -> None:
        # v8.14：asyncio.Event 非线程安全，必须经 call_soon_threadsafe 投递到 loop 线程
        loop = getattr(self, "_loop", None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._cancel.set)
        else:
            self._cancel.set()
