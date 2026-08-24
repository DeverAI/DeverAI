/* ============ agent.js 浏览器端 Agent 核心 ============
 * - llmChat:   无状态转发代理(/api/llm/chat) 的 SSE 流式客户端
 * - runSession:主 Agent 循环（多轮"思考→调用工具→反馈"）
 * - runSubagent: 子 Agent 委派（独立上下文，完成返回摘要）
 * - planAndExecute: AOE 并行规划（拆 DAG → 按层并行执行 → 汇总）
 * - compressMessages: 上下文压缩（token 超限时摘要旧消息）
 *
 * 所有状态只存在浏览器内存/localStorage/IndexedDB，服务端不落盘任何 Agent 数据。
 */
const Agent = {
  running: false,
  aborted: false,
  MAX_ROUNDS: 15,
  SUB_ROUNDS: 8,
};

function emit(ev) {
  if (App.onAgentEvent) App.onAgentEvent(ev);
}

function estTokens(text) {
  return Math.ceil(String(text == null ? '' : text).length / 2.5);
}
function estMessagesTokens(messages) {
  return messages.reduce((s, m) => s + estTokens(m.content) + 16, 0);
}

function requireLlmConfig(model) {
  const cfg = App.config;
  if (!cfg) throw new Error('配置未加载');
  if (!getApiKey()) throw new Error('未配置 API Key，请在设置中填写');
  // v8.4: 显式 model 参数优先（ui_review 可指定视觉专家模型）
  const effective = model || cfg.model;
  if (!effective) throw new Error('未配置模型，请在设置中选择模型');
  return cfg;
}

/* ---------- 系统提示词 ---------- */
function buildSystemPrompt() {
  const cfg = App.config || {};
  const parts = [
    '你是 DeverAI，一个运行在用户浏览器里的 AI 编程助手（类似 Cursor 的 Agent）。',
    '你可以读写用户授权的工作区文件、搜索代码、执行命令行、委派子 Agent、并行规划任务。',
    '',
    '工作环境（当前会话）：',
    `- 文件后端: ${fsModeLabel()}；工作区代号: ${FS.rootName || '未授权'}`,
    `- 命令桥: ${FS.bridge.authorized ? '已授权' : '未授权，执行命令前需要用户先在设置中授权'}`,
    // v8.13：FSS 模式下删除只受 ALLOW_AI_DELETE 控制；bridge 模式还需后端授权
    `- 允许 AI 删除文件: ${cfg.ALLOW_AI_DELETE && (FS.mode !== 'bridge' || FS.bridge.allowAiDelete) ? '是' : '否'}`,
    '',
    '【路径安全约束（必须遵守）】',
    '- 你只能看到工作区的大代号（如 ' + (FS.rootName || 'WORKSPACE-XXXX') + '），绝对路径由系统层翻译，你不可见。',
    '- 所有文件/命令操作只能写相对路径（如 "desktop/gui.py"、"tests/test_smoke.py"），禁止尝试使用绝对路径。',
    '- 系统返回的路径信息都是相对路径或代号，不要尝试反推绝对路径或用户名。',
    '- list_dir 返回的 name 是文件名（相对当前目录），read_file/write_file 的 path 是相对工作区的相对路径。',
    '- 这样设计是为了减少隐私与安全风险（用户名、目录结构不暴露给 AI）。',
    '',
    '工作规范：',
    '1. 小修改用 edit_file（精确替换），大改动/新建用 write_file。',
    '2. 动手前先探查：read_file 看现状，list_dir/grep 了解结构。',
    '3. 命令类操作（安装依赖、跑测试、git）用 run_command，会实时展示输出。',
    '4. 明显独立、互不依赖的子任务用 delegate_task 委派给子 Agent。',
    '5. 大型复杂目标用 plan_and_execute 拆解并行执行。',
    '6. 需要复用已有产物时先 search_assets。',
    '7. 修改文件后给出简短总结，不要复述全部代码。',
    '8. 遇到失败要读错误、分析原因、修正重试；不要盲目重复同一操作。',
    '9. 需要排查前端请求时可用 network_list/network_curl 查看 API 控制台记录（敏感字段已打码，Cookie 为 HttpOnly 不可见）。',
  ];
  if (cfg.traffic_mode) parts.push('', '[流量模式] 输出精简、只给结论与要点，避免长篇解释。');
  if (cfg.token_mode) parts.push('', '[Token 计费模式] 尽量减少调用轮次与输出长度，优先一次性完成。');
  if ((cfg.agent_mode || 'builder') === 'chat') {
    parts.push('', '[Chat 形态] 你是对话助手：只用只读工具查看/搜索/检索，不写文件、不执行命令、不删除。');
  }
  // v6.3 决策52：AGENT.txt 安全规则注入（网页版精简版）
  parts.push(
    '',
    '【工作流安全约束（源自 AGENT.txt，必须遵守）】',
    '- 文件操作全部用内置工具（read_file/write_file/edit_file/list_dir/grep），禁止用 run_command 调 cmd/shell 的 mv/cp/rm/sed 改项目文件。',
    '- 修改前先 read_file 确认原文，用 edit_file 精确改，不要整文件覆盖（除非新文件）。',
    '- 重要文档（Design.md/Techniques.md/Fact.md/AGENT.txt/FreqErr.md/Err.log）只读，不主动改。',
    '- 不臆造文件/函数，不确定先 list_dir/grep 确认。',
    '- 异常不吞，错误由系统写 Err.log；不要在输出里掩盖错误。',
    '- 不引入非必要第三方库；能用标准库解决的不引新依赖。',
    '- 副作用后置：落盘/建目录在审批通过后才执行，审批异常升级用户不静默放行。',
  );
  // v8.4 UI 自截图确认纪律：制造本地 GUI 后必须截图自检（chat 形态以外都适用）
  if (cfg.ENABLE_APP_SHOT !== false || cfg.ENABLE_UI_REVIEW !== false) {
    parts.push(
      '',
      '【UI 自截图确认纪律】',
      '- 制造任何本地 GUI 应用（PyQt/Tkinter 等 UI 脚本）后，必须用 app_screenshot 运行脚本并截图保存到工作区自检效果。',
      '- 截图后必须调用 ui_review 让视觉专家（设置里的视觉专家模型，未配置则用当前模型）审查截图，按审查意见改进后再截图复验。',
      '- 未完成截图确认前，不得宣称 UI 已完成。',
    );
  }
  return parts.join('\n');
}

/* ---------- LLM 客户端（SSE 流式） ---------- */
async function llmChat({ messages, tools, stream = true, temperature, signal, onDelta, model, max_tokens }) {
  const cfg = requireLlmConfig(model);
  const body = {
    base_url: cfg.base_url,
    api_key: getApiKey(),
    model: model || cfg.model,   // v8.4: 支持显式指定视觉专家模型（ui_review）
    messages,
    stream,
    temperature: temperature != null ? temperature : cfg.temperature,
    // v8.5.x 审查修复：优先用调用方传入的 max_tokens（如压缩 1500），否则回落全局配置
    max_tokens: max_tokens != null ? max_tokens : cfg.max_tokens,
  };
  if (tools && tools.length) {
    body.tools = tools;
    body.tool_choice = 'auto';
  }

  let content = '';
  let finishReason = '';
  const toolCalls = {};
  let usage = {};

  await sseFetch(
    '/api/llm/chat',
    body,
    {
      onData(obj) {
        if (obj.error && obj.error.message) {
          throw new Error(String(obj.error.message));
        }
        // 非流式路径：整体 JSON
        if (obj.choices == null && obj.usage) usage = obj.usage;
        const choice = (obj.choices && obj.choices[0]) || {};
        if (choice.finish_reason) finishReason = choice.finish_reason;
        if (obj.usage) usage = obj.usage;
        if (choice.delta) {
          if (choice.delta.content) {
            content += choice.delta.content;
            if (onDelta) onDelta(choice.delta.content);
          }
          if (choice.delta.tool_calls) {
            for (const tc of choice.delta.tool_calls) {
              const i = tc.index != null ? tc.index : 0;
              if (!toolCalls[i]) toolCalls[i] = { id: tc.id || '', name: '', arguments: '' };
              if (tc.id) toolCalls[i].id = tc.id;
              if (tc.function) {
                if (tc.function.name) toolCalls[i].name += tc.function.name;
                if (tc.function.arguments) toolCalls[i].arguments += tc.function.arguments;
              }
            }
          }
        } else if (choice.message) {
          // 某些实现非流式地给出完整 message（content 无条件累加，onDelta 仅作通知）
          if (choice.message.content) {
            content += choice.message.content;
            if (onDelta) onDelta(choice.message.content);
          }
          if (choice.message.tool_calls) {
            choice.message.tool_calls.forEach((tc, i) => {
              toolCalls[i] = {
                id: tc.id || 'call_' + uid(),
                name: (tc.function && tc.function.name) || '',
                arguments: (tc.function && tc.function.arguments) || '',
              };
            });
          }
        }
      },
      onEvent(evt, data) {
        if (evt === 'error') {
          throw new Error((data && (data.message || data.body)) || 'LLM 代理错误');
        }
      },
      onAbort() {
        const e = new Error('已停止');
        e.name = 'AbortError';
        throw e;
      },
    },
    signal
  );

  const toolCallsArr = Object.values(toolCalls).map((c) => ({
    id: c.id || 'call_' + uid(),
    type: 'function',
    function: { name: c.name, arguments: c.arguments || '{}' },
  }));

  return { content, tool_calls: toolCallsArr, finish_reason: finishReason, usage };
}

/* ---------- 消息构建与压缩 ---------- */
function buildMessages() {
  const msgs = [{ role: 'system', content: buildSystemPrompt() }];
  for (const m of App.history) {
    msgs.push({ role: m.role, content: m.content });
  }
  return msgs;
}

async function compressMessages(messages) {
  const cfg = App.config || {};
  const threshold = cfg.compress_threshold_tokens || 12000;
  const keep = cfg.context_keep_recent || 6;
  if (estMessagesTokens(messages) <= threshold) return messages;
  const dropable = messages.slice(1); // 保留 system
  if (dropable.length <= keep) return messages;
  const compressPart = dropable.slice(0, -keep);
  const recent = dropable.slice(-keep);
  // P1-1（查修）：压缩切分可能把 recent 首条落在 role='tool' 上（其配对的 assistant 已被
  // 压进摘要）→ 无配对 assistant 的 tool 消息会让 OpenAI 兼容接口 400。丢弃孤立 tool 前缀。
  while (recent.length && recent[0].role === 'tool') recent.shift();
  if (!recent.length) return messages; // 极端情况（保留段全是 tool）：放弃压缩保可用
  try {
    emit({ type: 'compress_start' });
    const resp = await llmChat({
      messages: [
        { role: 'system', content: '你是上下文压缩器。把下面的对话历史压缩成简洁的中文摘要（保留关键事实、路径、已做修改、结论）。不要丢失重要技术信息。' },
        { role: 'user', content: compressPart.map((m) => `[${m.role}] ${m.content}`).join('\n\n') },
      ],
      stream: false,
      // P2-17（查修）：压缩 LLM 调用接入 abortCtrl，停止按钮在压缩阶段也可中断
      signal: App.abortCtrl ? App.abortCtrl.signal : null,
      temperature: 0.2,
      max_tokens: 1500,
    });
    const summary = resp.content || '';
    emit({ type: 'compress', removed: compressPart.length, kept: recent.length + 1 });
    // v6.2 P1-4：保留原始 system 提示词（含路径安全约束/工具规范/工作区代号），
    // 仅在摘要前插入历史压缩段，避免压缩后 AI 失去安全约束
    return [messages[0], { role: 'system', content: '【历史上下文摘要】' + summary }, ...recent];
  } catch (e) {
    return messages; // 压缩失败不阻塞主流程
  }
}

/* ---------- v8.5 批次1：轮次快照生命周期 + 守门 + 依赖树注入 ---------- */
const SnapRound = {
  rid: '',
  async begin(cfg) {
    this.rid = '';
    if (!(cfg && cfg.ENABLE_SESSION_SNAP !== false)) return '';
    if (!(FS.mode === 'bridge' && FS.bridge.authorized)) return '';
    try {
      const r = await api('/api/bridge/sessions/begin', { method: 'POST', body: { enabled: true } });
      this.rid = r.round_id || '';
    } catch (e) { this.rid = ''; this._note('任务快照建立失败（不阻塞对话）: ' + (e && e.message)); }
    return this.rid;
  },
  async setInput(text) {
    if (!this.rid) return;
    try { await api(`/api/bridge/sessions/${this.rid}/input`, { method: 'POST', body: { input: String(text || '') } }); }
    catch (e) { this._note('任务快照输入记录失败: ' + (e && e.message)); }
  },
  /* v8.12：返回本次工具调用序号（round_no），供写工具登记轮内回退点 */
  async toolCall(toolName) {
    if (!this.rid) return 0;
    try {
      const r = await api(`/api/bridge/sessions/${this.rid}/tool_call`, { method: 'POST', body: { tool: toolName } });
      return (r && r.count) || 0;
    } catch (e) { this._note('任务快照工具调用记录失败: ' + (e && e.message)); return 0; }
  },
  /* v8.12：轮内回退点（/rollback_point 端口接线）——写工具执行前调用；
   * 旧内容由后端直读（content 传空串），existed 由后端按文件存在判定 */
  async rollbackPoint(rel, toolName, roundNo) {
    if (!this.rid || !roundNo) return;
    try {
      await api(`/api/bridge/sessions/${this.rid}/rollback_point`, {
        method: 'POST',
        body: { rel, content: '', tool_name: toolName || '', round_no: roundNo },
      });
    } catch (e) { this._note('轮内回退点保存失败: ' + (e && e.message)); }
  },
  async commit(meta, keepRollback) {
    if (!this.rid) return;
    const rid = this.rid;
    this.rid = '';
    try {
      await api(`/api/bridge/sessions/${rid}/commit`, {
        method: 'POST',
        body: {
          title: (meta && meta.title) || '', todo: (meta && meta.todo) || '',
          devlog: (meta && meta.devlog) || '', pack: true,
          // v8.12：出错/取消轮保留回退点（对齐桌面：正常轮提交后丢弃回退点）
          keep_rollback: !!keepRollback,
        },
      });
    } catch (e) { this._note('任务快照提交失败: ' + (e && e.message)); }
  },
  _note(text) {
    // v8.12：快照链路失败不再静默——以 note 事件呈现在对话中（错误不得吞没）
    try { if (typeof App !== 'undefined' && App.onAgentEvent) App.onAgentEvent({ type: 'note', text: String(text || '') }); } catch (e) {}
  },
};

/* v8.5 批次1：上下文守门（分块→轻量模型守门 keep/ban→装配）。简化版：失败回落不阻塞。 */
async function gateHistory(history, userText, signal) {
  // 分块：每 2 条历史一块（与桌面 split_blocks 语义对齐）
  const blocks = [];
  for (let i = 0; i < history.length; i += 2) {
    const chunk = history.slice(i, i + 2);
    blocks.push({
      uid: uid(),
      user: String(chunk[0] ? chunk[0].content : '').slice(0, 200),
      msgs: chunk,
    });
  }
  if (blocks.length <= 3) return { keep: history, banned: [] };
  try {
    const resp = await llmChat({
      stream: false,
      // P2-14：守门 LLM 调用接入 abortCtrl，停止按钮对守门阶段生效
      signal: signal || (App.abortCtrl ? App.abortCtrl.signal : null),
      messages: [{
        role: 'user',
        content: '以下是对话历史块列表（含 uid 与用户消息摘要）。请只输出 JSON 数组，列出应当保留的块 uid；'
          + '其余块视为过时上下文应丢弃。JSON 数组，不要其它文字。\n'
          + JSON.stringify(blocks.map((b) => ({ uid: b.uid, user: b.user }))),
      }],
    });
    const txt = String(resp.content || '').trim();
    const m = txt.match(/\[[^\]]*\]/);
    const keepUids = new Set(m ? (() => { try { return JSON.parse(m[0]); } catch (e) { return []; } })() : []);
    if (!keepUids.size) return { keep: history, banned: [] };
    const keepMsgs = [];
    const banned = [];
    for (const b of blocks) {
      if (keepUids.has(b.uid)) keepMsgs.push(...b.msgs);
      else banned.push(b.uid);
    }
    return { keep: keepMsgs, banned };
  } catch (e) {
    return { keep: history, banned: [] };
  }
}

/* v8.5 批次1：依赖树校验问题注入（激活轮内有 missing/too_small 时追加 system 提示 + 触发只读报警）
 * P1-4：校验通过时自动解除只读报警，避免 guard 置位后永不解除导致 AI 反复空转。 */
async function treeProblemsText(signal) {
  if (!(FS.mode === 'bridge' && FS.bridge.authorized)) return '';
  if (signal && signal.aborted) return '';
  try {
    const r = await api('/api/bridge/tree/scan');
    const problems = (r && r.problems) || [];
    if (!problems.length) {
      // 校验通过：若仍处于只读报警态（上一轮发现问题置位），自动解除
      const g = await api('/api/bridge/guard');
      if (g && g.readonly) {
        await api('/api/bridge/guard', { method: 'POST', body: { on: false } }).catch(() => {});
        emit({ type: 'guard_clear' });
        emit({ type: 'note', text: '依赖树校验通过，只读保护已解除' });
      }
      return '';
    }
    // 依赖树校验失败 = 只读报警源之一（与桌面版对齐）
    emit({ type: 'guard_alert', reason: '依赖树校验发现问题（' + problems.length + ' 项）' });
    await api('/api/bridge/guard', { method: 'POST', body: { on: true, reason: '依赖树校验失败' } }).catch(() => {});
    const lines = problems.map((p) => `- ${p.rel}: ${p.kind} ${p.detail}`);
    return '【依赖树校验警告（请优先修复）】\n' + lines.join('\n');
  } catch (e) { return ''; }
}

/* v8.5 批次2：token_mode 工具裁剪（对齐桌面 select_tools：基础工具保留 + 按输入相似度裁剪） */
const BASE_TOOL_NAMES_WEB = [
  'list_dir', 'read_file', 'workspace_info', 'write_file', 'edit_file',
  'run_command', 'app_screenshot', 'ui_review',
];

function selectToolsWeb(cfg, defs, todoText) {
  if (!(cfg && cfg.token_mode)) return defs;       // 非计费模式不裁剪
  const todo = String(todoText || '').trim();
  if (!todo || !defs || !defs.length) return defs;
  const base = new Set(BASE_TOOL_NAMES_WEB);
  const scored = [];
  for (const d of defs) {
    const name = (d.function && d.function.name) || '';
    if (base.has(name)) continue;                  // 基础工具无条件保留
    const desc = String((d.function && d.function.description) || '') + ' ' + name;
    scored.push({ name, score: charSimilarity(desc, todo) });
  }
  scored.sort((a, b) => b.score - a.score);
  const keep = new Set(
    scored.filter((s) => s.score >= 0.08).slice(0, 8).map((s) => s.name)
  );
  return defs.filter((d) => base.has(d.function.name) || keep.has(d.function.name));
}

/* ---------- 主 Agent 会话 ---------- */
async function runSession(userText) {
  if (Agent.running) {
    toast('AI 正在处理中，请稍候', 'err');
    return;
  }
  const cfg = App.config || {};
  Agent.running = true;
  Agent.aborted = false;
  App.abortCtrl = new AbortController();

  // 错误回滚基线：本次 runSession 开始前的 history 长度。
  // 失败（非用户主动停止）时把本次追加的 user/assistant 消息弹出，避免脏历史污染下一轮。
  const rollbackBase = App.history.length;
  emit({ type: 'run_start' });
  App.history.push({ role: 'user', content: userText });
  saveHistory().catch(() => {});

  // v8.5 批次1：开启任务级快照轮次
  await SnapRound.begin(cfg);
  await SnapRound.setInput(userText);

  let messages = buildMessages();
  let rounds = 0;
  let usage = {};
  // v8.5 批次1：上下文守门（可选，失败回落）—— 在工具循环前裁剪过时历史
  if (cfg && cfg.ENABLE_CTX_EXPERT !== false && App.history.length > 6) {
    const g = await gateHistory(App.history.slice(0, -1), userText, App.abortCtrl ? App.abortCtrl.signal : null);
    if (g.banned && g.banned.length) {
      emit({ type: 'ctx_gate', stats: { kept: g.keep.length / 2, banned: g.banned.length }, banned: g.banned });
      // 重新构建 messages：system + 保留历史 + 当前用户消息
      messages = [{ role: 'system', content: buildSystemPrompt() },
                  ...g.keep, { role: 'user', content: userText }];
    }
  }
  // v8.5 批次1：依赖树校验问题注入（追加 system 警告段）
  if (cfg && cfg.ENABLE_DEP_TREE !== false) {
    const treeWarn = await treeProblemsText(App.abortCtrl ? App.abortCtrl.signal : null);
    if (treeWarn) {
      messages = [...messages, { role: 'system', content: treeWarn }];
      emit({ type: 'note', text: '依赖树校验发现问题，已注入上下文' });
    }
  }
  // v8.12：本轮是否以错误/取消结束（决定任务快照 commit 是否保留轮内回退点）
  let endedBadly = false;

  try {
    // P2-10：守门/树扫描阶段已被用户停止 → 走取消语义，不误报 run_done
    if (Agent.aborted) {
      const e = new Error('已停止');
      e.name = 'AbortError';
      throw e;
    }
    for (;;) {
      if (Agent.aborted) break;
      if (rounds >= Agent.MAX_ROUNDS) {
        emit({ type: 'note', text: `已达最大轮数(${Agent.MAX_ROUNDS})，中止工具循环` });
        break;
      }
      rounds++;
      emit({ type: 'thinking', round: rounds });

      messages = await compressMessages(messages);
      const resp = await llmChat({
        messages,
        tools: selectToolsWeb(cfg, getToolDefs(), userText), // v8.5 批次2：token_mode 裁剪
        signal: App.abortCtrl.signal,
        onDelta: (text) => emit({ type: 'text_delta', content: text }),
      });
      if (resp.usage && (resp.usage.prompt_tokens || resp.usage.completion_tokens)) {
        usage = resp.usage;
      }

      const assistantMsg = { role: 'assistant', content: resp.content || '' };
      if (resp.tool_calls && resp.tool_calls.length) {
        assistantMsg.tool_calls = resp.tool_calls;
      }
      messages.push(assistantMsg);
      App.history.push({
        role: 'assistant',
        content: resp.content || (resp.tool_calls && resp.tool_calls.length ? '（调用工具…）' : ''),
      });
      saveHistory().catch(() => {});

      if (!resp.tool_calls || !resp.tool_calls.length) break;

      emit({ type: 'assistant_tool_calls', count: resp.tool_calls.length });
      for (const tc of resp.tool_calls) {
        if (Agent.aborted) break;
        let args = {};
        try {
          args = JSON.parse(tc.function.arguments || '{}');
        } catch (e) {
          args = { _parse_error: String(tc.function.arguments || '') };
        }
        const callId = tc.id || 'call_' + uid();
        emit({ type: 'tool_start', call_id: callId, name: tc.function.name, args });
        // v8.12：记录工具调用并取回序号（round_no），写工具据此登记轮内回退点
        const rno = await SnapRound.toolCall(tc.function.name);

        const result = await executeTool(tc.function.name, args, {
          config: cfg,
          signal: App.abortCtrl.signal,
          rollbackNo: rno,
          emit: (type, payload) => emit({ ...payload, type, call_id: callId }),
        });

        emit({
          type: 'tool_result',
          call_id: callId,
          name: tc.function.name,
          ok: result.ok,
          output: result.output,
          meta: result.meta,
        });

        // 集中截断：防巨型工具输出撑爆上下文
        let toolContent = String(result.output || '');
        if (toolContent.length > 20000) {
          toolContent = toolContent.slice(0, 20000) + '\n…(输出过长，已截断)';
        }
        messages.push({ role: 'tool', tool_call_id: callId, content: toolContent });
        if (!result.ok) {
          messages.push({
            role: 'system',
            content: `工具 ${tc.function.name} 执行失败。错误: ${String(result.output).slice(0, 600)}。请分析原因后修正重试，或改用其他方法。`,
          });
        }
      }
      if (Agent.aborted) break;
      // v8.13：工具执行期间被停止（如审批/命令流中止）→ 走取消语义，
      // 不再误发 run_done 把停止动作标成成功完成
      if (Agent.aborted) {
        const e = new Error('已停止');
        e.name = 'AbortError';
        throw e;
      }
    }

    emit({ type: 'run_done', rounds, usage });
    if (cfg.sleep_enabled) checkSleepGoal();
  } catch (e) {
    // 运算符优先级显式加括号：`e && e.name === 'AbortError' || Agent.aborted`
    // JS 默认即 ((e && e.name==='AbortError') || Agent.aborted)，此处显式写明防误读
    const isAbort = (e && e.name === 'AbortError') || Agent.aborted;
    endedBadly = true;  // v8.12：错误/取消轮 → commit 保留回退点
    if (isAbort) {
      emit({ type: 'run_cancelled' });
    } else {
      // 错误回滚：弹出本次 runSession 追加的脏消息（user/assistant），
      // 用户主动停止不回滚（已产生的部分输出可能有用）
      if (App.history.length > rollbackBase) {
        App.history.length = rollbackBase;
        saveHistory().catch(() => {});
      }
      emit({ type: 'run_error', message: String((e && e.message) || e) });
    }
  } finally {
    // v8.5 批次1：提交任务级快照（标题=todo 摘要兜底；异常路径也提交，不丢进度）
    try {
      await SnapRound.commit({
        title: (rounds ? `第${rounds}轮: ${userText.slice(0, 30)}` : userText.slice(0, 30)),
        todo: '', devlog: userText.slice(0, 500),
      }, endedBadly);
    } catch (e) { /* SnapRound.commit 内部已 note，不再二次抛 */ }
    // v8.13：await 落盘，避免刚结束立刻切任务时读旧会话
    await saveHistory().catch(() => {});
    Agent.running = false;
    App.abortCtrl = null;
  }
}

/* ---------- 停止 ---------- */
function stopSession() {
  if (!Agent.running) return;
  Agent.aborted = true;
  if (App.abortCtrl) {
    try { App.abortCtrl.abort(); } catch (e) {}
  }
  // P1-2（查修）：中止挂起的审批（审批卡片/副驾驶代判）→ 按拒绝处理，
  // 否则 runSession 永久卡在 await Approval.request，界面永远 busy
  if (typeof Approval !== 'undefined' && Approval.respond) Approval.respond(false);
}

/* ---------- 子 Agent（delegate_task） ---------- */
function getSubTools(cfg) {
  const full = getToolDefs();
  const allow = new Set([
    'workspace_info', 'list_dir', 'read_file', 'write_file', 'edit_file',
    'mkdir', 'rename', 'delete_file', 'grep', 'glob', 'run_command',
  ]);
  // 工具 defs 必须随开关裁剪：ALLOW_AI_DELETE 关闭时子 Agent 不得看到 delete_file
  if (!cfg || cfg.ALLOW_AI_DELETE !== true) allow.delete('delete_file');
  return full.filter((d) => allow.has(d.function.name));
}

async function runSubagent(task, context, parentCtx) {
  const label = '子Agent';
  const signal = parentCtx && parentCtx.signal ? parentCtx.signal : (App.abortCtrl ? App.abortCtrl.signal : null);
  const cfg = parentCtx && parentCtx.config ? parentCtx.config : (App.config || {});

  emit({ type: 'subagent', label, event: { type: 'run_start' } });

  const sys = [
    '你是一个子 Agent，被主 Agent 委派完成一个独立子任务。',
    '你的执行环境与主 Agent 相同（可以读写文件、执行命令）。',
    '完成任务后，用简洁的最终总结回复（不要复述过程代码，只给结论、关键产物路径、遇到的问题）。',
    context ? '\n【委派方提供的背景】\n' + context : '',
  ].join('\n');

  const subMessages = [
    { role: 'system', content: sys },
    { role: 'user', content: task },
  ];
  let rounds = 0;

  try {
    for (;;) {
      if (rounds >= Agent.SUB_ROUNDS) break;
      if (signal && signal.aborted) throw new Error('已停止');
      rounds++;
      const resp = await llmChat({
        messages: subMessages,
        tools: getSubTools(cfg),
        signal,
        onDelta: (text) => emit({ type: 'subagent', label, event: { type: 'text_delta', content: text } }),
      });
      const assistantMsg = { role: 'assistant', content: resp.content || '' };
      if (resp.tool_calls && resp.tool_calls.length) assistantMsg.tool_calls = resp.tool_calls;
      subMessages.push(assistantMsg);

      if (!resp.tool_calls || !resp.tool_calls.length) break;

      for (const tc of resp.tool_calls) {
        if (signal && signal.aborted) throw new Error('已停止');
        let args = {};
        try { args = JSON.parse(tc.function.arguments || '{}'); } catch (e) { args = {}; }
        const callId = tc.id || 'call_' + uid();
        emit({ type: 'subagent', label, event: { type: 'tool_start', call_id: callId, name: tc.function.name, args } });
        const result = await executeTool(tc.function.name, args, {
          config: cfg,
          signal,
          emit: (type, payload) => emit({ type: 'subagent', label, event: { ...payload, type, call_id: callId } }),
        });
        emit({ type: 'subagent', label, event: { type: 'tool_result', call_id: callId, ok: result.ok, output: result.output, name: tc.function.name } });
        let subToolContent = String(result.output || '');
        if (subToolContent.length > 20000) {
          subToolContent = subToolContent.slice(0, 20000) + '\n…(输出过长，已截断)';
        }
        subMessages.push({ role: 'tool', tool_call_id: callId, content: subToolContent });
        if (!result.ok) {
          subMessages.push({ role: 'system', content: `工具执行失败: ${String(result.output).slice(0, 400)}。修正后重试。` });
        }
      }
    }
  } catch (e) {
    emit({ type: 'subagent', label, event: { type: 'run_error', message: String((e && e.message) || e) } });
    return `【子 Agent 失败】${String((e && e.message) || e)}`;
  }

  const last = subMessages[subMessages.length - 1];
  const summary = last && last.role === 'assistant' ? last.content : '';
  emit({ type: 'subagent', label, event: { type: 'run_done' } });
  return summary || '（子 Agent 无输出）';
}

/* ---------- AOE 并行规划 ---------- */
async function planAndExecute(goal, parentCtx) {
  const signal = parentCtx && parentCtx.signal ? parentCtx.signal : (App.abortCtrl ? App.abortCtrl.signal : null);
  const cfg = parentCtx && parentCtx.config ? parentCtx.config : (App.config || {});
  const timeout = (cfg.aoe_timeout_s || 20) * 1000;

  // 1) 生成 DAG
  let nodes = [];
  try {
    const resp = await llmChat({
      messages: [
        { role: 'system', content: '你是 AOE 任务分解器。把用户目标拆成可并行执行的子任务 DAG。只输出 JSON（不要任何其他文字、不要 markdown 代码块），格式：\n{"nodes":[{"id":"n1","name":"简短任务名","deps":["n0"],"instruction":"给子 Agent 的完整执行指令"}]}\n要求：每个节点可独立由子 Agent 完成；依赖明确；节点 3-8 个。' },
        { role: 'user', content: goal },
      ],
      stream: false,
      temperature: 0.2,
      signal,
    });
    const text = (resp.content || '').replace(/```json|```/g, '').trim();
    const parsed = JSON.parse(text);
    nodes = (parsed.nodes || []).filter((n) => n && n.id && n.instruction);
    if (!nodes.length) throw new Error('规划结果为空');
  } catch (e) {
    emit({ type: 'plan_error', message: String((e && e.message) || e) });
    return `AOE 规划失败: ${String((e && e.message) || e)}`;
  }

  emit({ type: 'plan', goal, nodes });

  // 2) 拓扑分层
  const byId = {};
  nodes.forEach((n) => (byId[n.id] = n));
  const done = new Set();
  const levels = [];
  let remain = nodes.map((n) => n.id);
  while (remain.length) {
    const ready = remain.filter((id) => (byId[id].deps || []).every((d) => !byId[d] || done.has(d)));
    if (!ready.length) {
      // 有环，强制推进第一个
      ready.push(remain[0]);
    }
    ready.forEach((id) => done.add(id));
    levels.push(ready);
    remain = remain.filter((id) => !done.has(id));
  }

  const results = {};
  const startTs = Date.now();

  // 3) 按层并行执行
  // v6.2 修复：原 Promise.race 只是让 race 先 resolve，子 Agent 仍在后台跑（资源泄漏+任务串话）。
  // 改为：每个节点配独立 AbortController，超时/取消时 abort() 真正中止子 Agent 的 SSE 请求。
  for (const level of levels) {
    const jobs = level.map((id) => runAoeNodeWithTimeout(id, byId[id], cfg, timeout, signal));
    const settled = await Promise.allSettled(jobs);
    settled.forEach((s) => {
      if (s.status === 'fulfilled') {
        const r = s.value;
        results[r.id] = r;
      }
    });
    if (signal && signal.aborted) break;
  }

  // 4) 汇总
  let summaryText = '';
  try {
    const order = levels.flat();
    const pieces = order.map((id) => {
      const r = results[id] || {};
      return `### ${byId[id].name}\n状态: ${r.status || 'done'}\n${r.summary || '(无结果)'}`;
    });
    const resp = await llmChat({
      messages: [
        { role: 'system', content: '你是 AOE 结果汇总器。把各并行子任务结果整合成一份面向用户的最终总结：目标达成情况、关键产物路径、仍需注意的事项。简洁、结构化。' },
        { role: 'user', content: `目标: ${goal}\n\n子任务结果:\n${pieces.join('\n\n')}` },
      ],
      stream: false,
      temperature: 0.3,
      signal,
    });
    summaryText = resp.content || '';
  } catch (e) {
    summaryText = '(汇总失败: ' + String((e && e.message) || e) + ')';
  }

  emit({ type: 'plan_done', summary: summaryText, cost_s: ((Date.now() - startTs) / 1000).toFixed(1) });
  return summaryText;
}

/**
 * v6.2 修复：AOE 节点独立超时 + 真正取消。
 * - 节点配独立 AbortController，超时调 abort() 中止 SSE 请求
 * - 父 signal abort 时联动取消所有节点
 * - 子 Agent 内部需感知 signal.aborted 并尽快 break
 */
async function runAoeNodeWithTimeout(id, node, cfg, timeout, parentSignal) {
  emit({ type: 'plan_node', id, status: 'running' });
  const nodeCtrl = new AbortController();

  // 父 signal 联动：父 abort 时同步 abort 节点
  const onParentAbort = () => { try { nodeCtrl.abort(); } catch (e) {} };
  if (parentSignal) {
    if (parentSignal.aborted) {
      nodeCtrl.abort();
    } else {
      parentSignal.addEventListener('abort', onParentAbort, { once: true });
    }
  }

  let timer = null;
  let timedOut = false;
  // 启动超时定时器：到点 abort 节点（与子任务并行，不阻塞）
  timer = setTimeout(() => {
    timedOut = true;
    try { nodeCtrl.abort(); } catch (e) {}
  }, timeout);

  try {
    const summary = await runSubagent(node.instruction, '（这是 AOE 并行计划的子任务）', { config: cfg, signal: nodeCtrl.signal });
    if (timer) { clearTimeout(timer); timer = null; }
    if (parentSignal) parentSignal.removeEventListener('abort', onParentAbort);
    if (timedOut) {
      emit({ type: 'plan_node', id, status: 'timeout' });
      return { id, status: 'timeout', summary: summary || '(AOE 节点超时熔断)' };
    }
    // v8.13：父级停止/节点取消不能混入 done 汇总
    if (nodeCtrl.signal.aborted) {
      emit({ type: 'plan_node', id, status: 'error' });
      return { id, status: 'error', summary: '(已取消)' };
    }
    emit({ type: 'plan_node', id, status: 'done' });
    return { id, status: 'done', summary };
  } catch (e) {
    if (timer) { clearTimeout(timer); timer = null; }
    if (parentSignal) parentSignal.removeEventListener('abort', onParentAbort);
    const isAbort = (e && e.name === 'AbortError') || nodeCtrl.signal.aborted;
    if (isAbort) {
      emit({ type: 'plan_node', id, status: timedOut ? 'timeout' : 'error' });
      return { id, status: timedOut ? 'timeout' : 'error', summary: timedOut ? '(AOE 节点超时熔断)' : '(已取消)' };
    }
    emit({ type: 'plan_node', id, status: 'error' });
    return { id, status: 'error', summary: String((e && e.message) || e) };
  }
}

/* ---------- 肝完睡觉模式：任务完成检测（双因子） ---------- */
function checkSleepGoal() {
  const cfg = App.config || {};
  if (!cfg.sleep_enabled || !cfg.sleep_goal) return;
  const goal = String(cfg.sleep_goal || '');
  const lastUser = [...App.history].reverse().find((m) => m.role === 'user');
  if (!lastUser) return;
  // v6.2 双因子判定：
  //   因子1（关键词）：目标关键词出现在最近用户消息中（轻量初筛，防误触发）
  //   因子2（用户确认）：UI 层弹出确认对话，用户点「确认目标已达成」才真正进入倒计时
  //   两个因子都满足才触发 sleep_goal_done，避免单因子误判直接关机。
  const keywords = goal.replace(/[，。,.!！?？\s]/g, '').slice(0, 20);
  const msg = String(lastUser.content || '').replace(/[，。,.!！?？\s]/g, '');
  if (keywords && msg.includes(keywords.slice(0, 8))) {
    // 关键词匹配通过后，发 sleep_goal_pending 让 UI 弹确认对话框；
    // UI 确认后再回 emit sleep_goal_done，避免直接关机。
    emit({ type: 'sleep_goal_pending', goal });
  }
}
