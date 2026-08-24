/* ============ chat.js 聊天面板：消息 / 工具卡片 / 子Agent / AOE DAG / 审批 ============ */
const Chat = {
  currentAI: null,
  cards: {},        // call_id -> 工具卡片
  sub: {},          // label -> 子Agent 盒子
  planNodeEls: {},
  planCard: null,
  flushers: {},
  // 内存安全：每轮会话结束后清理引用，防止对象无限增长
  cleanup() {
    this.cards = {};
    this.sub = {};
    this.planNodeEls = {};
    this.planCard = null;
    this.flushers = {};
    this.currentAI = null;
    if (_progressEl) { _progressEl.remove(); _progressEl = null; }
    if (_progressTimer) { clearTimeout(_progressTimer); _progressTimer = null; }
  },
};

const TC_ICON = {
  workspace_info: 'info', list_dir: 'folder', read_file: 'memo', write_file: 'pen', edit_file: 'pen',
  mkdir: 'folder', rename: 'link', delete_file: 'tag', grep: 'search', glob: 'search', run_command: 'terminal',
  delegate_task: 'bot', search_assets: 'search', store_asset: 'plus', plan_and_execute: 'chart',
};

/* ---------- busy 状态 ---------- */
function setBusyUI(busy) {
  App.busy = busy;
  const send = $('#btn-send');
  const stop = $('#btn-chat-stop');
  const inp = $('#chat-input');
  if (send) send.disabled = busy;
  if (stop) stop.classList.toggle('hidden', !busy);
  if (inp) inp.disabled = busy;
}

/* ---------- 基础消息 ---------- */
function addUserMessage(text) {
  const hero = $('#hero');
  if (hero) hero.classList.add('hidden');
  const wrap = el('div', 'msg msg-user');
  wrap.appendChild(el('div', 'bubble', renderMd(text)));
  highlightBlock(wrap);
  $('#chat-messages').appendChild(wrap);
  scrollChat();
}

function newAIMessage() {
  const wrap = el('div', 'msg msg-ai');
  const head = el('div', 'ai-head');
  head.appendChild(el('span', 'ai-name', icon('atom', 14) + ' DeverAI'));
  const status = el('span', 'ai-status', '<span class="typing-dots"><i></i><i></i><i></i></span> 思考中…');
  head.appendChild(status);
  const body = el('div', 'ai-body');
  const textEl = el('div', 'markdown');
  body.appendChild(textEl);
  const toolsWrap = el('div', 'tools-wrap');
  body.appendChild(toolsWrap);
  const foot = el('div', 'ai-foot', '');
  body.appendChild(foot);
  wrap.appendChild(head);
  wrap.appendChild(body);
  $('#chat-messages').appendChild(wrap);
  Chat.currentAI = { el: wrap, textEl, toolsWrap, footEl: foot, statusEl: status, buf: '', flushTimer: null };
  scrollChat();
  return Chat.currentAI;
}

function flushAI() {
  const m = Chat.currentAI;
  if (!m || !m.buf) return;
  // v8.6：流式输出时追加打字光标（结束后自动消失）
  const streaming = App.busy ? '<span class="stream-cursor"></span>' : '';
  m.textEl.innerHTML = renderMd(m.buf) + streaming;
  highlightBlock(m.textEl);
  m.buf = '';
  scrollChat();
}

function appendAIText(text) {
  const m = Chat.currentAI;
  if (!m) return;
  m.buf += text;
  if (m.flushTimer) clearTimeout(m.flushTimer);
  m.flushTimer = setTimeout(flushAI, 90);
}

function setAIStatus(html) {
  if (Chat.currentAI) Chat.currentAI.statusEl.innerHTML = html;
}

/* ---------- 工具卡片 ---------- */
function addToolCard(ev) {
  const m = Chat.currentAI || newAIMessage();
  const card = el('div', 'tool-card running');
  const head = el('div', 'tc-head');
  head.appendChild(el('span', 'tc-icon', icon(TC_ICON[ev.name] || 'cog', 12)));
  head.appendChild(el('span', 'tc-name', esc(ev.name)));
  const status = el('span', 'tc-status', '<span class="spinner"></span><span>运行中</span>');
  head.appendChild(status);
  card.appendChild(head);
  const args = ev.args || {};
  const argLines = [];
  if (args.path !== undefined) argLines.push('path: ' + args.path);
  if (args.old_path !== undefined) argLines.push('old: ' + args.old_path);
  if (args.new_path !== undefined) argLines.push('new: ' + args.new_path);
  if (args.command !== undefined) argLines.push('cmd: ' + args.command);
  if (args.script !== undefined) argLines.push('script: ' + args.script);
  if (args.title !== undefined) argLines.push('title: ' + String(args.title).slice(0, 120));
  if (args.pattern !== undefined) argLines.push('pattern: ' + args.pattern);
  if (args.query !== undefined) argLines.push('query: ' + args.query);
  if (args.task !== undefined) argLines.push('task: ' + String(args.task).slice(0, 200));
  if (args.goal !== undefined) argLines.push('goal: ' + String(args.goal).slice(0, 200));
  if (args.content !== undefined) {
    const c = String(args.content);
    argLines.push(`content: ${c.split('\n').length} 行 · ${c.length} 字符`);
  }
  if (args.old_string !== undefined) argLines.push('old_string: ' + String(args.old_string).slice(0, 120));
  if (args.new_string !== undefined) argLines.push('new_string: ' + String(args.new_string).slice(0, 120));
  if (args.cwd !== undefined) argLines.push('cwd: ' + args.cwd);
  if (argLines.length) {
    card.appendChild(el('div', 'tc-args', argLines.map(esc).join('\n')));
  }
  const outputEl = el('div', 'tc-output hidden');
  card.appendChild(outputEl);
  m.toolsWrap.appendChild(card);
  Chat.cards[ev.call_id] = { card, status, outputEl, lines: [], name: ev.name };
  setAIStatus('<span class="spinner"></span> 执行工具…');
  scrollChat();
  return card;
}

function setToolStatus(callId, cls, html) {
  const c = Chat.cards[callId];
  if (!c) return;
  c.card.className = 'tool-card ' + cls;
  c.status.className = 'tc-status' + (cls === 'ok' ? ' ok' : cls === 'err' ? ' err' : '');
  c.status.innerHTML = html;
}

function finalizeTool(ev) {
  const c = Chat.cards[ev.call_id];
  if (!c) return;
  if (ev.ok) setToolStatus(ev.call_id, 'ok', '✓ 完成');
  else setToolStatus(ev.call_id, 'err', '✗ 失败');
  const out = ev.output || '';
  if (out) {
    c.outputEl.classList.remove('hidden');
    c.outputEl.textContent = out.length > 6000 ? out.slice(0, 6000) + '\n…(输出已截断)' : out;
  }
  if (c.name === 'write_file' && ev.ok) {
    const meta = ev.meta || {};
    c.outputEl.classList.remove('hidden');
    c.outputEl.textContent = (meta.created ? '新建文件\n' : '已覆盖写入\n') + (ev.output || '');
  }
  if (c.name === 'edit_file' && ev.ok) {
    const meta = ev.meta || {};
    if (meta.old && meta.new && meta.old.length < 500) {
      c.outputEl.classList.remove('hidden');
      c.outputEl.innerHTML = '';
      const oldB = el('div');
      oldB.style.cssText = 'color:#f85149;border-left:3px solid #f85149;padding:4px 8px;margin:2px 0;white-space:pre-wrap;';
      oldB.textContent = '- ' + meta.old;
      const newB = el('div');
      newB.style.cssText = 'color:#3fb950;border-left:3px solid #3fb950;padding:4px 8px;margin:2px 0;white-space:pre-wrap;';
      newB.textContent = '+ ' + meta.new;
      c.outputEl.appendChild(oldB);
      c.outputEl.appendChild(newB);
      c.outputEl.appendChild(el('div', '', esc(ev.output || '')));
    }
  }
  scrollChat();
}

function onCmdOutput(ev) {
  const c = Chat.cards[ev.call_id];
  if (c) {
    c.outputEl.classList.remove('hidden');
    c.lines.push(ev.line);
    if (c.lines.length > 500) c.lines.shift();
    c.outputEl.textContent = c.lines.join('\n');
    c.outputEl.scrollTop = c.outputEl.scrollHeight;
  }
  if (Terminal) Terminal.mirror(ev.line, ev.call_id);
}

/* ---------- 审批卡片 ---------- */
function onApproval(ev) {
  const container = Chat.currentAI ? Chat.currentAI.toolsWrap : $('#chat-messages');
  const ac = el('div', 'approval-card');
  ac.appendChild(el('div', 'approval-title', icon('lock', 14) + ' 需要确认命令执行'));
  ac.appendChild(el('div', 'approval-cmd', esc((ev.payload && ev.payload.command) || '')));
  const actions = el('div', 'approval-actions');
  const allow = el('button', 'btn primary', '允许执行');
  const deny = el('button', 'btn danger', '拒绝');
  allow.onclick = () => { Approval.respond(true); ac.remove(); };
  deny.onclick = () => { Approval.respond(false); ac.remove(); };
  actions.appendChild(allow);
  actions.appendChild(deny);
  ac.appendChild(actions);
  container.appendChild(ac);
  scrollChat();
}

/* ---------- 子Agent ---------- */
function getSubBox(label) {
  if (Chat.sub[label]) return Chat.sub[label];
  const m = Chat.currentAI || newAIMessage();
  const box = el('div', 'sub-card');
  const head = el('div', 'sub-head', `<span class="spinner"></span> ${esc(label)} 运行中…`);
  const body = el('div', 'sub-body');
  const textEl = el('div', 'sub-text');
  const tools = el('div', 'sub-tools');
  body.appendChild(textEl);
  body.appendChild(tools);
  box.appendChild(head);
  box.appendChild(body);
  m.toolsWrap.appendChild(box);
  const obj = { box, head, textEl, tools, buf: '', cards: {}, flushTimer: null, label };
  Chat.sub[label] = obj;
  scrollChat();
  return obj;
}

function flushSub(sub) {
  if (!sub.buf) return;
  sub.textEl.innerHTML = renderMd(sub.buf);
  highlightBlock(sub.textEl);
  sub.buf = '';
}

function onSubagent(ev) {
  const evt = ev.event || {};
  const t = evt.type;
  if (t === 'run_start') { getSubBox(ev.label); return; }
  const sub = getSubBox(ev.label);
  if (t === 'text_delta') {
    sub.buf += evt.content || '';
    if (sub.flushTimer) clearTimeout(sub.flushTimer);
    sub.flushTimer = setTimeout(() => flushSub(sub), 120);
  } else if (t === 'tool_start') {
    const mini = el('div', 'tool-card running');
    const head = el('div', 'tc-head');
    head.appendChild(el('span', 'tc-icon', icon(TC_ICON[evt.name] || 'cog', 12)));
    head.appendChild(el('span', 'tc-name', esc(evt.name)));
    const status = el('span', 'tc-status', '<span class="spinner"></span>');
    head.appendChild(status);
    mini.appendChild(head);
    const args = evt.args || {};
    if (args.command) mini.appendChild(el('div', 'tc-args', esc(args.command)));
    if (args.path) mini.appendChild(el('div', 'tc-args', 'path: ' + esc(args.path)));
    const out = el('div', 'tc-output hidden');
    mini.appendChild(out);
    sub.tools.appendChild(mini);
    sub.cards[evt.call_id] = { card: mini, status, outputEl: out, lines: [] };
  } else if (t === 'cmd_output') {
    const c = sub.cards[evt.call_id];
    if (c) {
      c.outputEl.classList.remove('hidden');
      c.lines.push(evt.line);
      if (c.lines.length > 500) c.lines.shift();
      c.outputEl.textContent = c.lines.join('\n');
      c.outputEl.scrollTop = c.outputEl.scrollHeight;
    }
  } else if (t === 'tool_result') {
    const c = sub.cards[evt.call_id];
    if (c) {
      c.card.className = 'tool-card ' + (evt.ok ? 'ok' : 'err');
      c.status.innerHTML = evt.ok ? '✓' : '✗';
      if (evt.output) {
        c.outputEl.classList.remove('hidden');
        c.outputEl.textContent = String(evt.output).slice(0, 3000);
      }
    }
  } else if (t === 'run_done') {
    sub.head.innerHTML = `✓ ${esc(sub.label)} 完成`;
    sub.head.style.color = 'var(--ok)';
    flushSub(sub);
  } else if (t === 'run_error') {
    sub.head.innerHTML = `✗ ${esc(sub.label)} 失败`;
    sub.head.style.color = 'var(--err)';
    sub.buf += '\n\n**错误:** ' + (evt.message || '');
    flushSub(sub);
  } else if (t === 'run_cancelled') {
    sub.head.innerHTML = `⏹ ${esc(sub.label)} 已停止`;
    sub.head.style.color = 'var(--warn)';
  }
  scrollChat();
}

/* ---------- AOE 规划 DAG ---------- */
function onPlan(plan) {
  const m = Chat.currentAI || newAIMessage();
  const card = el('div', 'plan-card');
  card.appendChild(el('div', 'plan-head', '<span class="tc-icon">' + icon('chart', 12) + '</span> AOE 并行规划'));
  card.appendChild(el('div', 'plan-goal', icon('atom', 13) + ' ' + esc(plan.goal || '')));
  const nodes = plan.nodes || [];
  const byId = {};
  nodes.forEach((n) => (byId[n.id] = n));
  const indeg = {};
  nodes.forEach((n) => (indeg[n.id] = (n.deps || []).filter((d) => byId[d]).length));
  const levels = [];
  const done = new Set();
  let ready = nodes.filter((n) => indeg[n.id] === 0).map((n) => n.id);
  while (ready.length) {
    levels.push(ready.slice());
    ready.forEach((id) => done.add(id));
    ready = nodes
      .filter((n) => !done.has(n.id) && (n.deps || []).every((d) => !byId[d] || done.has(d)))
      .map((n) => n.id);
  }
  const dag = el('div', 'plan-dag');
  levels.forEach((lvl, i) => {
    const row = el('div', 'plan-level');
    lvl.forEach((id) => {
      const n = byId[id];
      const chip = el('div', 'plan-node pending', `<span>${i + 1}</span> ${esc(n.name || id)}`);
      chip.dataset.id = id;
      if (n.deps && n.deps.length) chip.title = '依赖: ' + n.deps.join(', ');
      row.appendChild(chip);
      Chat.planNodeEls[id] = chip;
    });
    dag.appendChild(row);
    if (i < levels.length - 1) dag.appendChild(el('div', 'plan-arrow', '↓ 并行 ↓'));
  });
  card.appendChild(dag);
  const summary = el('div', 'plan-summary hidden');
  card.appendChild(summary);
  m.toolsWrap.appendChild(card);
  Chat.planCard = { card, summary };
  setAIStatus('<span class="spinner"></span> AOE 并行执行中…');
  scrollChat();
}

function onPlanNode(ev) {
  const chip = Chat.planNodeEls[ev.id];
  if (!chip) return;
  // v8.13：error 显式样式（此前落入 done）
  const cls = ev.status === 'running' ? 'running' : ev.status === 'timeout' ? 'timeout' : ev.status === 'error' ? 'error' : 'done';
  chip.className = 'plan-node ' + cls;
  if (ev.status === 'done' && ev.cost_s != null) chip.title = `${ev.cost_s}s`;
  if (ev.output) chip.title = (chip.title ? chip.title + '\n' : '') + ev.output;
}

function onPlanDone(ev) {
  if (Chat.planCard) {
    Chat.planCard.summary.classList.remove('hidden');
    Chat.planCard.summary.innerHTML = '<b>汇总结果</b><br>' + renderMd(ev.summary || '');
    highlightBlock(Chat.planCard.summary);
  }
}

/* ---------- v8.5.3 聊天内 Thought / To-dos / Progress / FileChanges ---------- */
function addThoughtCard(thought) {
  const m = Chat.currentAI || newAIMessage();
  const card = el('div', 'thought-card');
  const head = el('div', 'thought-head', icon('brain', 13) + ' Thought');
  const body = el('div', 'thought-body');
  body.innerHTML = renderMd(thought || '');
  card.appendChild(head);
  card.appendChild(body);
  head.onclick = () => card.classList.toggle('open');
  m.toolsWrap.appendChild(card);
  scrollChat();
}

function addTodoCard(title, items) {
  const m = Chat.currentAI || newAIMessage();
  const card = el('div', 'todo-card');
  const head = el('div', 'todo-head', icon('todos', 13) + ' ' + esc(title || 'To-dos'));
  const body = el('div', 'todo-body');
  (items || []).forEach((it) => {
    const row = el('div', 'todo-item' + (it.done ? ' done' : ''));
    row.appendChild(el('span', 'todo-check', ''));
    row.appendChild(el('span', '', esc(it.text || '')));
    body.appendChild(row);
  });
  if (!items || !items.length) body.appendChild(el('div', 'form-hint', '暂无任务'));
  card.appendChild(head);
  card.appendChild(body);
  head.onclick = () => card.classList.toggle('open');
  if (/^add\b/i.test(String(title || ''))) card.classList.add('open');
  m.toolsWrap.appendChild(card);
  updateSummaryTodos(title, items);
  scrollChat();
}

function updateSummaryTodos(title, items) {
  const box = $('#summary-progress');
  if (!box) return;
  box.innerHTML = '';
  const list = Array.isArray(items) ? items : [];
  const label = el('div', 'summary-progress-title', esc(title || 'Progress'));
  box.appendChild(label);
  if (!list.length) {
    box.appendChild(el('div', 'empty-state', 'No tasks yet.'));
    return;
  }
  list.forEach((it) => {
    const row = el('div', 'summary-todo' + (it.done ? ' done' : ''));
    row.appendChild(el('span', 'summary-todo-check', ''));
    row.appendChild(el('span', 'summary-todo-text', esc(it.text || '')));
    box.appendChild(row);
  });
}

let _progressEl = null;
let _progressTimer = null;
function updateProgressFloat(done, total, review) {
  if (!_progressEl) {
    _progressEl = el('div', 'progress-float');
    document.body.appendChild(_progressEl);
  }
  const d = Number(done) || 0;
  const t = Number(total) || 0;
  _progressEl.innerHTML = `${icon('chart', 14)} <span>Progress</span> <span class="progress-count">${d}/${t}</span>${review ? ' · <span class="progress-review">Under review</span>' : ''}`;
  _progressEl.classList.remove('hidden');
  const summary = $('#summary-progress');
  if (summary) {
    let meta = summary.querySelector('.summary-progress-meta');
    if (!meta) {
      meta = el('div', 'summary-progress-meta');
      summary.prepend(meta);
    }
    meta.innerHTML = `<b>${d}/${t}</b> completed${review ? ' · <span>Under review</span>' : ''}`;
  }
  if (_progressTimer) clearTimeout(_progressTimer);
  _progressTimer = setTimeout(() => {
    if (_progressEl) { _progressEl.remove(); _progressEl = null; }
  }, 8000);
}

function addFileChangesCard(changes) {
  const m = Chat.currentAI || newAIMessage();
  const list = el('div', '');
  (changes || []).forEach((c) => {
    const card = el('div', 'file-change-card');
    const status = c.status || 'modified';
    const badge = status === 'added' ? '<span class="add">+</span>' : status === 'deleted' ? '<span class="del">-</span>' : '<span>•</span>';
    card.innerHTML = `${badge} <span>${esc(c.path || '')}</span>`;
    list.appendChild(card);
  });
  if (list.children.length) m.toolsWrap.appendChild(list);
  scrollChat();
}

/* ---------- 事件分发（由 main.js 挂到 App.onAgentEvent） ---------- */
function onAgentEvent(ev) {
  if (!ev) return;
  switch (ev.type) {
    case 'run_start':
      newAIMessage();
      setAIStatus('<span class="spinner"></span> 思考中…');
      setChatSub('思考中');
      setBusyUI(true);
      break;
    case 'thinking':
      setAIStatus('<span class="spinner"></span> 思考中 (第 ' + ev.round + ' 轮)…');
      break;
    case 'text_delta':
      appendAIText(ev.content);
      break;
    case 'tool_start':
      addToolCard(ev);
      if (ev.name === 'run_command' && ev.args && Terminal) Terminal.logCmdHeader(ev.args.command || '');
      break;
    case 'tool_result':
      finalizeTool(ev);
      break;
    case 'cmd_output':
      onCmdOutput(ev);
      break;
    case 'assistant_tool_calls':
      setAIStatus('<span class="spinner"></span> 调用工具…');
      break;
    case 'approval_needed':
      onApproval(ev);
      break;
    case 'approval_dismiss': {
      // v8.13：审批被停止/超时等外部路径 resolve 后，移除残留审批卡
      const m = Chat.currentAI;
      const scope = m ? m.toolsWrap : $('#chat-messages');
      if (scope) scope.querySelectorAll('.approval-card').forEach((n) => n.remove());
      break;
    }
    case 'note': {
      const m = Chat.currentAI || newAIMessage();
      m.toolsWrap.appendChild(el('div', 'note-chip', icon('info', 13) + ' ' + esc(ev.text || '')));
      break;
    }
    case 'compress_start':
      setChatSub('上下文压缩中…');
      break;
    case 'compress': {
      const m = Chat.currentAI || newAIMessage();
      m.toolsWrap.appendChild(el('div', 'note-chip', icon('cog', 13) + ' 已压缩上下文（移除 ' + ev.removed + ' 条旧消息，保留 ' + ev.kept + ' 条）'));
      setChatSub('上下文已压缩');
      break;
    }
    case 'ctx_gate': {
      // v8.5 批次1：上下文守门结果
      const s = ev.stats || {};
      const m = Chat.currentAI || newAIMessage();
      m.toolsWrap.appendChild(el('div', 'note-chip',
        icon('filter', 13) + ' 上下文守门：保留 ' + (s.kept || 0) + ' 块 / 禁 ' + ((ev.banned || []).length) + ' 块'));
      setChatSub('上下文守门完成');
      break;
    }
    case 'guard_alert': {
      // v8.5 批次1：只读报警横幅（依赖树校验失败/健康 P0 时）
      showGuardBanner(ev.reason || '');
      break;
    }
    case 'guard_clear': {
      // P1-4：只读保护自动解除（依赖树校验通过）→ 移除横幅
      hideGuardBanner();
      break;
    }
    case 'copilot_block': {
      // v8.5 批次2：副驾驶拦截危险命令（copilot 审批模式）
      const m = Chat.currentAI || newAIMessage();
      m.toolsWrap.appendChild(el('div', 'note-chip', icon('shield', 13) + ' 副驾驶拦截危险命令：' + esc(ev.reason || '')));
      break;
    }
    case 'subagent':
      onSubagent(ev);
      break;
    case 'plan':
      onPlan(ev);
      break;
    case 'plan_node':
      onPlanNode(ev);
      break;
    case 'plan_done':
      onPlanDone(ev);
      break;
    case 'plan_error': {
      const m = Chat.currentAI || newAIMessage();
      m.toolsWrap.appendChild(el('div', 'note-chip', icon('info', 13) + ' AOE 规划失败：' + esc(ev.message || '')));
      setChatSub('规划失败');
      break;
    }
    case 'thought':
      addThoughtCard(ev.content);
      break;
    case 'todos':
      addTodoCard(ev.title || 'Updated To-dos', ev.items);
      break;
    case 'progress':
      updateProgressFloat(ev.done, ev.total, ev.review);
      break;
    case 'file_changes':
      addFileChangesCard(ev.changes);
      break;
    case 'vault_stored':
      toast('已存入资产银行: ' + ((ev.asset && ev.asset.title) || ''), 'ok');
      if (typeof VaultPanel !== 'undefined') VaultPanel.refresh();
      break;
    case 'sleep_goal_pending':
      // v8.5.x 审查修复：双因子确认——用户确认目标达成才进入倒计时（此前无分支，功能整体失效）
      if (confirm(`检测到目标可能已达成：「${ev.goal || ''}」\n\n确认进入关机/休眠倒计时？`)) {
        startSleepOverlay(ev.goal);
      }
      break;
    case 'run_done': {
      setAIStatus('✓ 完成');
      setChatSub('就绪');
      if (Chat.currentAI) {
        const parts = [];
        if (ev.rounds) parts.push(`↻ ${ev.rounds} 轮`);
        const u = ev.usage || {};
        if (u.prompt_tokens) parts.push(`⧉ ${u.prompt_tokens}→${u.completion_tokens || 0} tokens`);
        if (parts.length) Chat.currentAI.footEl.innerHTML = parts.map(esc).join(' · ');
      }
      flushAI();
      setBusyUI(false);
      break;
    }
    case 'run_error':
      setAIStatus('✗ 失败');
      setChatSub('出错了');
      appendAIText('\n\n> **错误**: ' + (ev.message || ''));
      flushAI();
      setBusyUI(false);
      toast('运行出错: ' + (ev.message || ''), 'err', 5000);
      break;
    case 'run_cancelled':
      setAIStatus('⏹ 已停止');
      setChatSub('已停止');
      flushAI();
      setBusyUI(false);
      break;
    default:
      break;
  }
  // v8.8：同步到工作轨迹视图（只读，异常自吞）
  try {
    if (typeof TraceView !== 'undefined' && TraceView.addEvent) TraceView.addEvent(ev);
  } catch (e) {}
}

/* ---------- 发送 ---------- */
function sendMessage() {
  if (typeof showTraceView === 'function') showTraceView(false);
  const inp = $('#chat-input');
  const text = inp.value.trim();
  if (!text || App.busy) return;
  if (!getApiKey() || !App.config.model) {
    toast('请先在设置中配置模型与 API Key', 'err', 4000);
    openSettings();
    return;
  }
  // v8.10：拼接引用条内容（轨迹预览 / 详情对话框引用）
  let fullText = text;
  if (typeof App !== 'undefined' && App.buildQuoteMessage) {
    const q = App.buildQuoteMessage();
    if (q) fullText = q + '\n\n' + text;
  }
  addUserMessage(fullText);
  inp.value = '';
  autoGrow();
  Chat.sub = {};
  setChatSub('已发送');
  // v8.13：更新当前任务时间戳并刷新左栏会话列表
  if (typeof touchTask === 'function') touchTask(App.taskId || 'default');
  if (typeof renderTaskLists === 'function') renderTaskLists();
  Agent.runSession(fullText);
  if (typeof App !== 'undefined' && App.clearQuotes) App.clearQuotes();
  maybeStartSuggest(text);
}

/* ---------- v8.3 建议系统（TRAE CUE 式）：消息发送完成时生成 AI 精选建议 ---------- */
const SUGGEST_DOCS = ['Design.md', 'Techniques.md', 'Fact.md', 'Future.md', 'FreqErr.md', 'AGENT.txt'];
const SUGGEST_SYSTEM = (
  '你是资深产品/技术/视觉顾问。基于提供的项目文档与当前工作状态，站在刁钻角度' +
  '疯狂提出改进建议。严格输出 JSON 数组，每项：' +
  '{"type":"functional|technical|art","title":"一句话标题（≤20字）",' +
  '"detail":"具体做法（≤80字）","value":0-10}。' +
  '要求：覆盖三类；避免已完全实现的功能；value 高=收益大。只输出 JSON。'
);

let _suggestSeq = 0;

async function maybeStartSuggest(userText) {
  const cfg = App.config || {};
  if (!cfg.ENABLE_SUGGEST) return;
  if (!getApiKey() || !cfg.model) return;
  const seq = ++_suggestSeq;
  const box = $('#chat-suggest');
  const loading = document.createElement('span');
  loading.className = 'suggest-loading';
  loading.textContent = 'AI 建议生成中…';
  box.prepend(loading);
  try {
    // 读取项目文档要点（工作区未授权/文件缺失时静默跳过）
    const docs = [];
    for (const name of SUGGEST_DOCS) {
      try {
        const r = await readFile(name);
        const txt = (r && r.content != null ? r.content : r) || '';
        docs.push('=== ' + name + ' ===\n' +
          txt.split('\n').slice(0, 80).join('\n').slice(0, 2500));
      } catch (e) { /* 文档缺失或 fs 未授权，跳过 */ }
    }
    const context = [
      '工作区: ' + ((typeof FS !== 'undefined' && FS.workspace) || ''),
      '当前输入: ' + String(userText || '').slice(0, 200),
    ].concat(docs).join('\n\n').slice(0, 12000);
    const resp = await llmChat({
      messages: [
        { role: 'system', content: SUGGEST_SYSTEM },
        { role: 'user', content: context },
      ],
      stream: false, temperature: 0.4,
      // v8.13：建议请求接入 abortCtrl，停止后不再后台占用调用
      signal: App.abortCtrl ? App.abortCtrl.signal : null,
    });
    if (seq !== _suggestSeq) return;
    renderSuggestions(box, filterDedupeSuggestions(parseSuggestJson(resp.content)));
  } catch (e) {
    if (seq === _suggestSeq) loading.remove();
  }
}

function parseSuggestJson(text) {
  const s = String(text || '');
  const m = s.match(/```(?:json)?\s*([\s\S]*?)```/);
  const body = m ? m[1] : s;
  const start = body.indexOf('[');
  const end = body.lastIndexOf(']');
  if (start >= 0 && end > start) {
    try {
      const arr = JSON.parse(body.slice(start, end + 1));
      return Array.isArray(arr) ? arr : [];
    } catch (e) { /* 解析失败返回空 */ }
  }
  return [];
}

/* v8.5 批次2：建议去重 + 分类均衡（对齐桌面 suggest.filter_dedupe） */
function filterDedupeSuggestions(items, maxItems) {
  const max = maxItems || 6;
  if (!items || !items.length) return [];
  const seen = new Set();
  const uniq = items.filter((it) => {
    if (!it || typeof it !== 'object') return false;
    const key = String(it.title || '').trim().toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  uniq.sort((a, b) => (Number(b.value) || 0) - (Number(a.value) || 0));
  const byType = { functional: [], technical: [], art: [] };
  for (const it of uniq) {
    const t = byType[it.type] ? it.type : 'functional';
    byType[t].push(it);
  }
  const out = [];
  for (const t of ['functional', 'technical', 'art']) {
    if (byType[t].length) out.push(byType[t].shift()); // 每类至少 1 条优先
  }
  for (const t of ['functional', 'technical', 'art']) out.push(...byType[t]);
  return out.slice(0, max);
}

function renderSuggestions(box, items) {
  box.querySelectorAll('.suggest-loading, .suggest-item').forEach((n) => n.remove());
  if (!items || !items.length) return;
  const TYPE_CN = { functional: '功能性', technical: '技术性', art: '美术性' };
  items.slice(0, 6).forEach((it) => {
    const chip = document.createElement('button');
    chip.className = 'chip suggest-item';
    chip.title = it.detail || it.title;
    chip.innerHTML = '<span class="suggest-type">' + esc(TYPE_CN[it.type] || it.type) + '</span>' +
      esc(it.title) + ' <span class="suggest-val">' + Math.round(Number(it.value) || 0) + '</span>';
    chip.onclick = () => {
      const inp = $('#chat-input');
      inp.value = it.detail || it.title;
      autoGrow();
      inp.focus();
    };
    box.appendChild(chip);
  });
}

/* ---------- 历史恢复 ---------- */
function restoreChat() {
  const box = $('#chat-messages');
  const hasHistory = Boolean(App.history && App.history.length);
  const hero = $('#hero');
  if (hero) hero.classList.toggle('hidden', hasHistory);
  box.innerHTML = '';
  Chat.cards = {};
  Chat.sub = {};
  Chat.planNodeEls = {};
  Chat.currentAI = null;
  (App.history || []).forEach((m) => {
    if (m.role === 'user') {
      addUserMessage(m.content || '');
    } else if (m.role === 'assistant' && m.content && !m.content.startsWith('（调用工具')) {
      const ai = newAIMessage();
      ai.buf = m.content;
      flushAI();
      setAIStatus('✓ 完成');
    }
  });
  scrollChat();
  // P2-15：刷新后恢复只读报警横幅（后端按用户持久）
  restoreGuardState().catch(() => {});
}

function autoGrow() {
  const inp = $('#chat-input');
  inp.style.height = 'auto';
  inp.style.height = Math.min(inp.scrollHeight, 160) + 'px';
}

/* ---------- v8.5 批次1：只读报警横幅 + 版本/轮次回退 ---------- */
let _guardBanner = null; // P2-15：横幅去重（避免每轮重复叠加）

function hideGuardBanner() {
  if (_guardBanner) { _guardBanner.remove(); _guardBanner = null; }
}

function showGuardBanner(reason) {
  if (_guardBanner && _guardBanner.isConnected) return; // 已有横幅不重复叠加
  const wrap = $('#chat-messages');
  const banner = el('div', 'guard-banner');
  _guardBanner = banner;
  banner.appendChild(el('span', '', icon('shield', 13) + ' 已进入只读保护：' + esc(reason || '校验发现问题')));
  const btn = el('button', 'btn', '查看快照并恢复');
  btn.onclick = () => openSnapshotDialog();
  banner.appendChild(btn);
  const dismiss = el('button', 'btn danger', '解除只读');
  dismiss.onclick = async () => {
    try {
      await api('/api/bridge/guard', { method: 'POST', body: { on: false } });
      hideGuardBanner();
      toast('已解除只读保护', 'ok');
    } catch (e) { toast('解除失败: ' + e.message, 'err'); }
  };
  banner.appendChild(dismiss);
  wrap.prepend(banner);
}

// P2-15：页面加载/刷新后恢复 guard 状态（后端按用户持久，横幅不丢）
async function restoreGuardState() {
  if (!(typeof FS !== 'undefined' && FS.mode === 'bridge' && FS.bridge.authorized)) return;
  try {
    const g = await api('/api/bridge/guard');
    if (g && g.readonly) showGuardBanner(g.reason || '校验发现问题');
  } catch (e) { /* 桥未就绪，静默 */ }
}

function openSnapshotDialog() {
  const modal = $('#modal-root');
  const box = $('#modal');
  if (!modal || !box) { toast('缺少模态容器', 'err'); return; }
  modal.classList.remove('hidden');
  box.innerHTML = `
    <div class="modal-head"><span class="modal-title">${icon('archive', 14)} 快照与版本回退</span>
      <button class="icon-btn" id="snap-close">${icon('close', 12)}</button></div>
    <div class="modal-body">
      <div class="section-title">文件版本快照（写文件前自动备份）</div>
      <div id="snap-files"></div>
      <div class="section-title">任务级会话快照（一轮对话）</div>
      <div id="snap-sessions"></div>
    </div>`;
  $('#snap-close').onclick = () => modal.classList.add('hidden');
  closeOnBackdrop();
  loadSnapshotFiles();
  loadSnapshotSessions();
}

async function loadSnapshotFiles() {
  const box = $('#snap-files');
  if (!box) return;
  box.innerHTML = '<div class="form-hint">加载中…</div>';
  try {
    const r = await api('/api/bridge/checkpoint/files');
    const files = (r && r.files) || [];
    if (!files.length) { box.innerHTML = '<div class="form-hint">暂无版本快照（写文件前自动生成）</div>'; return; }
    box.innerHTML = '';
    files.slice(0, 30).forEach((f) => {
      const row = el('div', 'snap-row');
      row.appendChild(el('span', '', esc(f.rel_path) + `（${esc(String(f.count))} 版）`));
      const sel = el('select', '');
      (f.versions || []).slice(0, 8).forEach((v) => {
        const opt = el('option', '', esc(`${v.source_name || v.source || 'ai'} · ${v.ts}`));
        opt.value = v.bak_path;
        sel.appendChild(opt);
      });
      const btn = el('button', 'btn', '恢复');
      btn.onclick = async () => {
        if (!confirm(`恢复到 ${f.rel_path} 的所选版本？当前内容会先备份`)) return;
        try {
          // P1-3：兑现"当前内容会先备份"承诺——恢复前先对当前文件打 checkpoint（可二次回滚）；
          // content 传空串由后端直读原文件，>2MB 大文件也不受影响
          await api('/api/bridge/checkpoint/save', {
            method: 'POST',
            body: { path: f.rel_path, content: '', source: 'restore' },
          }).catch(() => {});
          const rr = await api('/api/bridge/checkpoint/restore', { method: 'POST', body: { bak_path: sel.value, path: f.rel_path } });
          if (rr.ok && typeof rr.content === 'string') {
            await writeFile(f.rel_path, rr.content);
            Tree.refresh().catch(() => {});
            // P2-11：恢复后刷新依赖树增量（防 too_small/missing 误报）
            if (typeof snapTreeAfter === 'function' && App.config && App.config.ENABLE_DEP_TREE !== false) {
              snapTreeAfter(f.rel_path, 'write', App.config).catch(() => {});
            }
            toast('已恢复 ' + f.rel_path, 'ok');
          } else {
            toast('恢复失败', 'err');
          }
        } catch (e) { toast('恢复失败: ' + e.message, 'err'); }
      };
      // v8.12：查看全部版本（/checkpoint/versions 端口接线 + 内容预览）
      const allBtn = el('button', 'btn', '全部');
      allBtn.title = '查看该文件全部版本（含内容预览）';
      allBtn.onclick = () => openVersionDialog(f.rel_path);
      row.appendChild(sel);
      row.appendChild(allBtn);
      row.appendChild(btn);
      box.appendChild(row);
    });
  } catch (e) { box.innerHTML = '<div class="form-hint">加载失败（需命令桥授权）</div>'; }
}

/* v8.12：单文件全部版本列表（/checkpoint/versions 端口接线 + /checkpoint/restore 内容预览） */
async function openVersionDialog(rel) {
  const modal = $('#modal-root');
  const box = $('#modal');
  if (!modal || !box) { toast('缺少模态容器', 'err'); return; }
  modal.classList.remove('hidden');
  box.innerHTML = `
    <div class="modal-head"><span class="modal-title">${icon('archive', 14)} 版本历史 — ${esc(rel)}</span>
      <button class="icon-btn" id="ver-close">${icon('close', 12)}</button></div>
    <div class="modal-body"><div id="ver-list"><div class="form-hint">加载中…</div></div></div>`;
  $('#ver-close').onclick = () => modal.classList.add('hidden');
  closeOnBackdrop();
  const list = $('#ver-list');
  try {
    const r = await api('/api/bridge/checkpoint/versions?path=' + encodeURIComponent(rel));
    const vers = (r && r.versions) || [];
    if (!vers.length) { list.innerHTML = '<div class="form-hint">暂无版本</div>'; return; }
    list.innerHTML = '';
    vers.forEach((v) => {
      const row = el('div', 'snap-row');
      row.appendChild(el('span', '', esc(`${v.source_name || v.source || 'ai'} · ${v.ts}`)));
      const prevBtn = el('button', 'btn', '预览');
      prevBtn.onclick = async () => {
        try {
          const rr = await api('/api/bridge/checkpoint/restore', { method: 'POST', body: { bak_path: v.bak_path, path: rel } });
          showVersionPreview(rel, (rr && rr.content) || '');
        } catch (e) { toast('预览失败: ' + e.message, 'err'); }
      };
      const resBtn = el('button', 'btn', '恢复');
      resBtn.onclick = async () => {
        if (!confirm(`恢复到 ${esc(rel)} 的该版本？当前内容会先备份`)) return;
        try {
          await api('/api/bridge/checkpoint/save', { method: 'POST', body: { path: rel, content: '', source: 'restore' } }).catch(() => {});
          const rr = await api('/api/bridge/checkpoint/restore', { method: 'POST', body: { bak_path: v.bak_path, path: rel } });
          if (rr.ok && typeof rr.content === 'string') {
            await writeFile(rel, rr.content);
            Tree.refresh().catch(() => {});
            // P2-2：与 loadSnapshotFiles 恢复分支一致——补依赖树增量更新（防 too_small/missing 误报）
            if (typeof snapTreeAfter === 'function' && App.config && App.config.ENABLE_DEP_TREE !== false) {
              snapTreeAfter(rel, 'write', App.config).catch(() => {});
            }
            toast('已恢复 ' + rel, 'ok');
            modal.classList.add('hidden');
          } else { toast('恢复失败', 'err'); }
        } catch (e) { toast('恢复失败: ' + e.message, 'err'); }
      };
      row.appendChild(prevBtn);
      row.appendChild(resBtn);
      list.appendChild(row);
    });
  } catch (e) { list.innerHTML = '<div class="form-hint">加载失败: ' + esc(String((e && e.message) || e)) + '</div>'; }
}

/* v8.12：版本内容预览（走 /checkpoint/restore 只读返回内容，不落盘） */
function showVersionPreview(rel, content) {
  const modal = $('#modal-root');
  const box = $('#modal');
  if (!modal || !box) return;
  modal.classList.remove('hidden');
  box.innerHTML = `
    <div class="modal-head"><span class="modal-title">${icon('archive', 14)} 版本预览 — ${esc(rel)}</span>
      <button class="icon-btn" id="verp-close">${icon('close', 12)}</button>
      <button class="btn flatbtn" id="verp-back">返回版本列表</button></div>
    <div class="modal-body"><pre class="snap-rb-detail" style="max-height:60vh;overflow:auto">${esc(String(content || '').slice(0, 20000))}</pre></div>`;
  $('#verp-close').onclick = () => modal.classList.add('hidden');
  $('#verp-back').onclick = () => openVersionDialog(rel);
  closeOnBackdrop();
}

async function loadSnapshotSessions() {
  const box = $('#snap-sessions');
  if (!box) return;
  box.innerHTML = '<div class="form-hint">加载中…</div>';
  try {
    const r = await api('/api/bridge/sessions');
    const sessions = (r && r.sessions) || [];
    if (!sessions.length) { box.innerHTML = '<div class="form-hint">暂无任务级快照</div>'; return; }
    box.innerHTML = '';
    sessions.forEach((s) => {
      const row = el('div', 'snap-row');
      const label = s.title || ('会话 ' + s.round_id);
      row.appendChild(el('span', '', esc(label) + `（${(Number(s.size_mb) || 0).toFixed(1)}MB · ${esc(s.status)}）`));
      // v8.11：轮内回退点 + 会话详情（/sessions/{rid}/rollback_points、/sessions/{rid}、/rollback/{round_no} 端口接线）
      const rpBtn = el('button', 'btn', '回退点');
      rpBtn.onclick = () => toggleRollbackPoints(row, s.round_id);
      row.appendChild(rpBtn);
      const btn = el('button', 'btn', '恢复');
      btn.onclick = async () => {
        if (!confirm(`从快照「${esc(label)}」恢复整个工作区？`)) return;
        try {
          const rr = await api(`/api/bridge/sessions/${s.round_id}/restore`, { method: 'POST' });
          toast((rr && rr.output) || '已恢复', 'ok');
          // v8.13：恢复会改写工作区文件，刷新文件树并提示（编辑器打开的文件下次打开取新内容）
          if (typeof Tree !== 'undefined') Tree.refresh().catch(() => {});
        } catch (e) { toast('恢复失败: ' + e.message, 'err'); }
      };
      const del = el('button', 'btn danger', '删除');
      del.onclick = async () => {
        if (!confirm('删除该快照？不可恢复。')) return;
        try {
          await api(`/api/bridge/sessions/${s.round_id}`, { method: 'DELETE' });
          toast('已删除快照', 'ok');
          loadSnapshotSessions();
        } catch (e) { toast('删除失败: ' + e.message, 'err'); }
      };
      row.appendChild(btn);
      row.appendChild(del);
      box.appendChild(row);
    });
  } catch (e) { box.innerHTML = '<div class="form-hint">加载失败</div>'; }
}

/* v8.11：轮内回退点展开面板（此前三端点后端已有、前端从未接线） */
async function toggleRollbackPoints(row, rid) {
  if (row._rbPanel) { row._rbPanel.remove(); row._rbPanel = null; return; }
  const panel = el('div', 'snap-rb');
  panel.innerHTML = '<div class="form-hint">加载回退点…</div>';
  row.after(panel);
  row._rbPanel = panel;
  try {
    // 会话详情（/sessions/{rid}）：展示输入与 TODO 摘要
    try {
      const dr = await api(`/api/bridge/sessions/${rid}`);
      const sess = (dr && dr.session) || {};
      if (sess && (sess.input || sess.todo)) {
        const det = el('div', 'snap-rb-detail');
        det.textContent = '输入: ' + String(sess.input || '').slice(0, 200)
          + (sess.todo ? '\nTODO: ' + String(sess.todo).slice(0, 200) : '');
        panel.appendChild(det);
      }
    } catch (e) { /* 详情失败不阻塞回退点列表 */ }
    const r = await api(`/api/bridge/sessions/${rid}/rollback_points`);
    const pts = (r && r.points) || [];
    if (!pts.length) {
      const h = el('div', 'form-hint', '该轮暂无回退点（轮内工具调用前自动记录，提交时保留才可见）');
      panel.appendChild(h);
      return;
    }
    pts.forEach((p) => {
      const pr = el('div', 'snap-row');
      const files = Array.isArray(p.files) && p.files.length ? `${p.files.length} 个文件` : '';
      pr.appendChild(el('span', '', esc(p.tool || '工具') + (p.ts ? ' · ' + esc(p.ts) : '') + (files ? ' · ' + files : '')));
      const go = el('button', 'btn', '回退到该点');
      go.onclick = async () => {
        if (!confirm(`回退到「${esc(p.tool || '工具')}」调用前（round ${p.round_no}）？`)) return;
        try {
          const rr = await api(`/api/bridge/sessions/${rid}/rollback/${p.round_no}`, { method: 'POST' });
          toast((rr && rr.output) || '已回退', 'ok');
          if (typeof Tree !== 'undefined') Tree.refresh().catch(() => {});
        } catch (e) { toast('回退失败: ' + e.message, 'err'); }
      };
      pr.appendChild(go);
      panel.appendChild(pr);
    });
  } catch (e) {
    panel.innerHTML = '<div class="form-hint">加载失败: ' + esc(String((e && e.message) || e)) + '</div>';
  }
}
