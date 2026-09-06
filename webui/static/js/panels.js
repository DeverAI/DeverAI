/* ============ panels.js 设置 / 资产银行 / 终端 / 同步 / 状态栏 / 休眠 ============ */

/* ---------- 终端 ---------- */
const Terminal = {
  _maxLines: 2000,
  logCmdHeader(cmd) {
    this.appendLine('> ' + cmd, 't-cmd-header');
  },
  mirror(line) {
    this.appendLine(line);
  },
  appendLine(text, cls) {
    const box = $('#terminal-output');
    if (!box) return;
    const d = el('div', 't-line' + (cls ? ' ' + cls : ''), esc(text));
    box.appendChild(d);
    while (box.childNodes.length > this._maxLines) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
  },
  clear() { $('#terminal-output').innerHTML = ''; },
};

/* ---------- 资产银行（IndexedDB） ---------- */
const Vault = {
  async store({ title, content, kind, tags, scene }) {
    const asset = {
      id: 'a_' + uid(),
      title: String(title || '未命名资产'),
      content: String(content || ''),
      kind: kind || 'code',
      tags: Array.isArray(tags) ? tags : [],
      scene: scene || '',
      description: '',
      created_at: new Date().toISOString().slice(0, 19).replace('T', ' '),
    };
    await IDB.put('vault', asset);
    return asset;
  },
  async list() {
    const all = await IDB.getAll('vault');
    return all.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  },
  async remove(id) { await IDB.delete('vault', id); },
  async clearAll() { await IDB.clear('vault'); },
  async search(q) {
    const assets = await IDB.getAll('vault');
    const query = String(q || '').trim().toLowerCase();
    if (!query) return assets.map((a) => ({ ...a, score: 0 })).slice(0, 50);
    const docs = assets.map((a) => [a.title, (a.tags || []).join(' '), a.scene, a.description, a.content].join(' '));
    const cfg = App.config || {};
    const threshold = Number(cfg.vault_threshold) || 0;
    if (typeof FS !== 'undefined' && FS.mode === 'bridge' && FS.bridge.authorized) {
      try {
        const r = await api('/api/bridge/matcher/rank', {
          method: 'POST',
          body: {
            query, docs, min_score: threshold, limit: 8,
            embedding_level: cfg.embedding_level || 'char',
            embedding_model: cfg.embedding_model || '',
          },
        });
        if (r && r.ok && Array.isArray(r.results)) {
          return r.results.filter((x) => assets[x.idx])
            .map((x) => ({ ...assets[x.idx], score: x.score }));
        }
      } catch (e) { /* 后端匹配失败 → 降级前端 */ }
    }
    const scored = [];
    for (let i = 0; i < assets.length; i++) {
      const s = charSimilarity(docs[i], query);
      if (s >= threshold) scored.push({ ...assets[i], score: s });
    }
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, 8);
  },
};

const VaultPanel = {
  _box() { return $('#vault-list') || $('#summary-artifacts'); },
  async refresh() {
    const box = this._box();
    if (!box) return;
    box.innerHTML = '<div style="padding:10px;color:var(--text-faint)">加载中…</div>';
    try {
      const assets = await Vault.list();
      renderVaultList(assets);
    } catch (e) {
      box.innerHTML = '<div style="padding:10px;color:var(--text-faint)">资产银行不可用</div>';
    }
  },
  async search(q) {
    const box = this._box();
    if (!box) return;
    if (!q.trim()) return this.refresh();
    const hits = await Vault.search(q);
    renderVaultList(hits, true);
  },
};

function renderVaultList(assets, isSearch) {
  const box = VaultPanel._box();
  if (!box) return;
  box.innerHTML = '';
  if (!assets.length) {
    box.innerHTML = '<div style="padding:14px;color:var(--text-faint);text-align:center">暂无资产<br><span style="font-size:11px">AI 完成可复用产物后会自动评估入库</span></div>';
    return;
  }
  assets.forEach((a) => {
    const item = el('div', 'vault-item');
    const head = el('div', 'vault-item-head');
    head.appendChild(el('span', 'vault-kind', esc(a.kind || 'code')));
    head.appendChild(el('span', 'vault-title', esc(a.title || '未命名')));
    if (isSearch && a.score != null) head.appendChild(el('span', 'vault-tag', '相似 ' + a.score.toFixed(2)));
    const del = el('button', 'icon-btn danger', icon('tag', 12));
    del.title = '删除资产';
    del.onclick = async () => {
      await Vault.remove(a.id);
      VaultPanel.refresh();
    };
    head.appendChild(del);
    item.appendChild(head);
    if (a.tags && a.tags.length) {
      const tags = el('div', 'vault-tags');
      a.tags.forEach((t) => tags.appendChild(el('span', 'vault-tag', esc(t))));
      item.appendChild(tags);
    }
    if (a.scene) item.appendChild(el('div', 'vault-scene', icon('atom', 12) + ' ' + esc(a.scene)));
    if (a.content) item.appendChild(el('div', 'vault-prompt', icon('memo', 12) + ' ' + esc(String(a.content).slice(0, 200))));
    const foot = el('div', 'vault-item-foot');
    foot.appendChild(el('span', '', esc(a.created_at || '')));
    item.appendChild(foot);
    box.appendChild(item);
  });
}

/* ---------- 状态栏 ---------- */
function updateStatusbar() {
  const c = App.config || {};
  $('#sb-model').textContent = c.model ? c.model : '模型未配置';
  $('#sb-ws').innerHTML = icon('folder', 12) + ' ' + esc(FS.rootName || '未授权');
  const modes = [];
  if (c.traffic_mode) modes.push(['流量模式', 'traffic']);
  if (c.token_mode) modes.push(['Token 计费', 'token']);
  if (c.sleep_enabled) modes.push(['肝完睡觉', 'sleep']);
  const box = $('#sb-modes');
  if (box) box.innerHTML = modes.map(([label, cls]) => `<span class="mode-chip ${cls}">${label}</span>`).join('');
}

/* ---------- 设置（v8.5.3 参考图风格：左侧导航 + 内容页） ---------- */
const PROVIDERS = [
  { name: 'OpenAI', base: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  { name: 'DeepSeek', base: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  { name: 'Kimi', base: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  { name: '智谱GLM', base: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  { name: 'Ollama本地', base: 'http://127.0.0.1:11434/v1', model: 'qwen2.5:7b' },
];
const SWITCHES = [
  ['ENABLE_VAULT', '资产银行', '生成物自动评估入库，规划前强制查库复用'],
  ['ENABLE_AOE', 'AOE 并行规划', '复杂任务拆 DAG 并行执行 + 超时熔断'],
  ['ENABLE_SUBAGENT', '子Agent', '上下文压缩与任务委派'],
  ['ENABLE_SYNC', '状态同步', '快照导出/导入/服务器推送'],
  ['ENABLE_MODES', '三大模式', '流量 / 肝完睡觉 / Token 计费'],
  ['ENABLE_APPROVAL', '命令审批门', 'AI 执行命令前需你确认'],
  ['ALLOW_AI_DELETE', '允许 AI 删除文件', '默认关闭（危险操作）'],
  ['ENABLE_SUGGEST', '建议系统', '消息发送完成时展示 AI 精选建议'],
  ['ENABLE_APP_SHOT', '应用截图', '运行 Python UI 脚本并截图自检'],
  ['ENABLE_UI_REVIEW', '截图视觉审查', '截图后调用视觉专家模型确认效果'],
  ['ENABLE_CHECKPOINT', '文件级版本快照', '写文件前自动备份原内容'],
  ['ENABLE_SESSION_SNAP', '任务级会话快照', '一轮对话打包+轮内回退+只读报警'],
  ['ENABLE_DEP_TREE', '依赖树校验', '扫描 import 依赖+大小下限，缺失报警注入上下文'],
  ['ENABLE_CTX_EXPERT', '上下文守门', '分块守门裁剪历史+永久禁用问题块'],
  ['ENABLE_MULTI_SESSION', '多会话标签', '聊天区顶部标签条：新建/切换/关闭多个对话'],
  ['ENABLE_WEB_SEARCH', '互联网搜索', 'DDG 零 key 联网查资料/Benchmark'],
  ['ENABLE_BROWSER', '浏览器控制', '无头抓取网页/截图/打开链接'],
  ['ENABLE_NOTEPAD', '暂存便签', 'Agent 跨轮暂存中间结果'],
  ['ENABLE_TOOLSMITH', '自研工具库', '工具设计专家：查重→构建→审核→入库→执行'],
  ['ENABLE_TOOL_DOCTOR', '工具医生', '工具 bug 收集与自动修复回归'],
  ['ENABLE_AUTO_DRIFT', '自动算力漂移', '工作期间定时推送 + 退出自动漂移到服务器'],
  ['auto_drift_on_exit', '退出自动漂移', '关闭时默认直接漂移退出（关闭后询问）'],
  ['ENABLE_AUDIT_LOG', '回退审计日志', '回退/删除/恢复/漂移回本地全量审计'],
  ['ENABLE_FILE_PARTITION', '文件分区并发', '同层任务按文件冲突分批并行加速'],
  ['ENABLE_BROWSER_CTL', '直接操控浏览器', 'CDP 启动浏览器并点击/输入/按键/跳转'],
  ['ENABLE_BROWSER_DEVTOOLS', 'F12 开发者工具', 'Networks/Storage/Console/Sources 面板（查找 API 端点）'],
  ['ENABLE_EXE_JOURNAL', '外部软件探索记录', '记录 exe 自动化每次操作到 journal'],
  ['ENABLE_EXE_AUTOMATE', '外部软件自动化', '启动/操控本地 exe 软件（点击/输入/按键/截图，危险操作强制审批）'],
  ['ENABLE_MEMORY', '长期记忆', '跨会话记忆/经验系统：自动召回注入 + memory_* 工具（浏览器本地 IndexedDB）'],
  ['ENABLE_OUTBOUND_GATE', '出关审核（repeat）', 'AI 向外传递最终内容前必须复述你的原始要求并经你审核批准'],
];

const SETTINGS_NAV = [
  { id: 'general', label: 'General', icon: 'general' },
  { id: 'models', label: 'Models', icon: 'models' },
  { id: 'agents', label: 'Agents', icon: 'agents' },
  { id: 'skills', label: 'Skills & Commands', icon: 'skills' },
  { id: 'security', label: 'Security', icon: 'shield' },   // v8.17 安全中心
  { id: 'advanced', label: 'Advanced', icon: 'advanced' },
];

const BUILTIN_EXPERTS = [
  { id: 'researcher', name: 'Researcher', desc: 'Responsible for research analysis, code location, dependency mapping, environment inspection, and report generation.' },
  { id: 'fullstack', name: 'Full-Stack Engineer', desc: 'Responsible for implementing and modifying frontend and backend code, as well as handling cross-stack and general coding tasks.' },
  { id: 'qa', name: 'QA', desc: 'Responsible for running tests and builds, and collecting validation evidence.' },
  { id: 'reviewer', name: 'Code Reviewer', desc: 'Responsible for reviewing code, identifying potential risks, and providing improvement recommendations.' },
  { id: 'ui', name: 'UI Operator', desc: 'Responsible for browser and UI end-to-end validation, as well as visual bug reproduction.' },
  { id: 'debug', name: 'Debug Engineer', desc: 'Responsible for reproducing failures, locating root causes, and diagnosing defects, with fix recommendations.' },
];

const BUILTIN_SKILLS = [
  { id: 'better-harness', name: 'better-harness', source: 'Plugin', desc: 'Use when /better-harness reviews the outer coding-agent Harness for lifecycle controls, repeated work, project feedback, agent assets, session ...' },
  { id: 'create-plugin', name: 'create-plugin', source: 'Plugin', desc: 'Create a native plugin directory from an external or local source. Convert GitHub SKILL.md files, local SKILL.md files, and marketplace packages ...' },
];

function openSettings(page = 'general') {
  $('#modal-root').classList.remove('hidden');
  $('#modal').innerHTML = settingsHTML();
  hydrateIcons($('#modal'));
  wireSettings(page);
  closeOnBackdrop();
  loadErrLog();
}

function settingsHTML() {
  return `
  <div class="settings-layout">
    <nav class="settings-nav" id="settings-nav"></nav>
    <div class="settings-content">
      <div id="settings-pages"></div>
      <div class="modal-foot" style="padding:14px 0 0;border-top:1px solid var(--border);margin-top:20px;">
        <button class="btn" id="btn-cancel-settings">取消</button>
        <button class="btn primary" id="btn-save-settings">保存设置</button>
      </div>
    </div>
  </div>`;
}

function renderSettingsNav(activeId) {
  const nav = $('#settings-nav');
  nav.innerHTML = `
    <div class="settings-back" id="settings-back">${icon('back', 14)} Back</div>
    <div class="settings-nav-group">
      ${SETTINGS_NAV.map((n) => `
        <div class="settings-nav-item ${n.id === activeId ? 'active' : ''}" data-page="${esc(n.id)}">
          ${icon(n.icon, 16)} ${esc(n.label)}
        </div>
      `).join('')}
    </div>`;
  $('#settings-back').onclick = closeModal;
  $$('.settings-nav-item', nav).forEach((item) => {
    item.onclick = () => switchSettingsPage(item.dataset.page);
  });
}

function switchSettingsPage(page) {
  renderSettingsNav(page);
  $$('.settings-page').forEach((p) => p.classList.toggle('active', p.dataset.page === page));
  if (page === 'models') loadModelRegistry();
  if (page === 'security') {
    loadSecurityAudit();
    loadCoordinationBoard();   // v8.34（H8）
    renderExtApiEditor();
    renderOutboundDrafts();
    loadSmtpStatus();
  }
}

function renderSettingsPages() {
  const box = $('#settings-pages');
  box.innerHTML = `
    ${renderGeneralPage()}
    ${renderModelsPage()}
    ${renderAgentsPage()}
    ${renderSkillsPage()}
    ${renderSecurityPage()}
    ${renderAdvancedPage()}
  `;
  hydrateIcons(box);
}

function renderGeneralPage() {
  const c = App.config || {};
  const keySaved = !!getApiKey();
  return `<div class="settings-page active" data-page="general">
    <div class="settings-header">
      <div>
        <div class="settings-title">General</div>
        <div class="settings-desc">General settings for DeverAI Agent.</div>
      </div>
    </div>

    <div class="section-title">外观（v8.6：双主题工作台）</div>
    <div class="switch-row">
      <div class="sw-info"><b>界面主题</b><span>参考 DeepSeek Harness 的深浅双主题；自动模式跟随系统</span></div>
      <select class="form-input" id="set-theme" style="flex:0 0 160px">
        <option value="auto" ${c.theme !== 'light' && c.theme !== 'dark' ? 'selected' : ''}>自动（跟随系统）</option>
        <option value="light" ${c.theme === 'light' ? 'selected' : ''}>浅色</option>
        <option value="dark" ${c.theme === 'dark' ? 'selected' : ''}>深色</option>
      </select>
    </div>

    <div class="section-title">模型（OpenAI 兼容；配置只存在本机浏览器，服务端不落盘）</div>
    <div class="provider-row" id="providers"></div>
    <div class="form-group"><label class="form-label">API Base URL</label><input class="form-input" id="set-base" placeholder="https://api.openai.com/v1" value="${esc(c.base_url || '')}" /></div>
    <div class="form-row" style="margin-top:12px;">
      <div class="form-group"><label class="form-label">模型</label><input class="form-input" id="set-model" placeholder="如 gpt-4o-mini" value="${esc(c.model || '')}" /></div>
      <div class="form-group"><label class="form-label">API Key ${keySaved ? '<span style="color:var(--ok);font-size:11px">(已保存)</span>' : ''}</label><input class="form-input" id="set-key" type="password" placeholder="${keySaved ? '留空则保留已保存的 Key' : 'sk-…'}" autocomplete="off" /></div>
    </div>
    <div class="form-row" style="margin-top:12px;">
      <div class="form-group"><label class="form-label">视觉专家模型</label><input class="form-input" id="set-visual-expert" placeholder="支持图片输入的模型（ui_review 审查用；留空=用当前模型）" value="${esc(c.visual_expert_model || '')}" /></div>
    </div>
    <div class="form-hint">视觉专家模型与主模型共用 API Base / Key；如需不同厂商请先切换 API Base 或填同一服务下的图像模型（如 gpt-4o / qwen-vl）。</div>
    <div class="form-row" style="margin-top:12px;">
      <div class="form-group"><label class="form-label">Temperature</label><input class="form-input" id="set-temp" type="number" step="0.1" min="0" max="2" value="${Number(c.temperature ?? 0.3)}" /></div>
      <div class="form-group"><label class="form-label">Max Tokens</label><input class="form-input" id="set-maxtok" type="number" min="128" value="${Number(c.max_tokens ?? 4096)}" /></div>
    </div>
    <div class="form-group" style="margin-top:12px;"><button class="btn" id="btn-test">测试连接</button> <span id="test-result" style="font-size:12px;color:var(--text-dim)"></span></div>

    <div class="section-title">工作区</div>
    <div class="form-hint">文件后端当前：<b id="ws-mode-label"></b> · 工作区：<b id="ws-name-settings"></b></div>
    <div class="form-row" style="gap:8px;flex-wrap:wrap;margin-top:10px;">
      <button class="btn" id="btn-pick-fss">${icon('folder', 14)} 授权浏览器工作区</button>
      <button class="btn" id="btn-pick-bridge">${icon('terminal', 14)} 授权命令桥工作区</button>
    </div>
    <div class="form-row" style="margin-top:10px;">
      <input class="form-input" id="set-ws-bridge" placeholder="或手动输入命令桥工作区路径" value="${esc(FS.bridge.workspace || '')}" />
      <button class="btn" id="btn-set-ws-bridge" style="flex:0 0 auto">应用</button>
    </div>
    <div class="form-hint">命令桥用于执行命令行（浏览器无法直接执行）。若浏览器支持 File System Access（Chrome/Edge），文件读写无需命令桥。</div>

    <div class="section-title">模块开关</div>
    <div id="switch-list"></div>

    <div class="section-title">审批与匹配（v8.5 批次2）</div>
    <div class="form-row">
      <div class="form-group"><label class="form-label">审批模式</label>
        <select class="form-input" id="set-approval-mode">
          <option value="all" ${c.approval_mode === 'all' ? 'selected' : ''}>全部需确认</option>
          <option value="danger" ${c.approval_mode !== 'free' && c.approval_mode !== 'all' && c.approval_mode !== 'copilot' ? 'selected' : ''}>仅危险命令（默认）</option>
          <option value="copilot" ${c.approval_mode === 'copilot' ? 'selected' : ''}>副驾驶代批</option>
          <option value="free" ${c.approval_mode === 'free' ? 'selected' : ''}>全部放行</option>
        </select>
      </div>
      <div class="form-group"><label class="form-label">资产匹配级别</label>
        <select class="form-input" id="set-embedding-level">
          <option value="char" ${(c.embedding_level || 'char') === 'char' ? 'selected' : ''}>char（本地零成本）</option>
          <option value="bm25" ${c.embedding_level === 'bm25' ? 'selected' : ''}>bm25（本地语义）</option>
          <option value="api" ${c.embedding_level === 'api' ? 'selected' : ''}>api（Embedding 接口）</option>
        </select>
      </div>
    </div>
    <div class="form-row" style="margin-top:12px;">
      <div class="form-group"><label class="form-label">Embedding 模型 id（api 级别用，可选）</label><input class="form-input" id="set-embedding-model" placeholder="如 qwen3-text-embedding" value="${esc(c.embedding_model || '')}" /></div>
      <div class="form-group"><label class="form-label">资产匹配阈值</label><input class="form-input" id="set-vault-thr" type="number" step="0.05" min="0" max="1" value="${Number(c.vault_threshold ?? 0.45)}" /></div>
    </div>
    <div class="form-hint">审批模式对齐桌面版：danger 仅危险命令弹窗；copilot 由当前模型代判、拿不准才问用户；free 全放行。匹配级别：api 需 Embedding 接口可用，失败自动降级 bm25→char。</div>

    <div class="section-title">阈值</div>
    <div class="form-row">
      <div class="form-group"><label class="form-label">上下文压缩阈值 (tokens)</label><input class="form-input" id="set-compress" type="number" value="${Number(c.compress_threshold_tokens ?? 12000)}" /></div>
      <div class="form-group"><label class="form-label">AOE 分支超时 (s)</label><input class="form-input" id="set-aoe" type="number" value="${Number(c.aoe_timeout_s ?? 20)}" /></div>
    </div>
    <div class="form-row" style="margin-top:12px;">
      <div class="form-group"><label class="form-label">保留最近消息数</label><input class="form-input" id="set-keep" type="number" value="${Number(c.context_keep_recent ?? 6)}" /></div>
    </div>

    <div class="section-title">三大模式</div>
    <div id="mode-cards"></div>

    <div class="section-title">同步（部署在用户自有服务器）</div>
    <div class="form-group"><input class="form-input" id="set-sync-url" placeholder="http://你的服务器:8765" value="${esc(c.sync_server_url || '')}" /></div>
    <div class="form-group" style="margin-top:10px;"><input class="form-input" id="set-sync-pw" type="password" placeholder="同步加密口令（用于 AES-256-GCM）" /></div>
    <div class="form-group" style="margin-top:10px;"><input class="form-input" id="set-sync-token" placeholder="同步令牌（服务器 SYNC_TOKEN，可选）" value="${esc(c.sync_token || '')}" /></div>
    <div class="form-row" style="gap:8px;flex-wrap:wrap;margin-top:10px;">
      <button class="btn" id="btn-sync-export">导出快照</button>
      <button class="btn" id="btn-sync-import">导入快照</button>
      <button class="btn" id="btn-sync-push">推送服务器</button>
      <button class="btn" id="btn-sync-pull">拉取服务器</button>
      <input type="file" id="sync-file" class="hidden" />
    </div>
    <div class="form-hint">快照含配置 / 会话 / 资产银行，主快照经口令加密（AES-256-GCM）；服务器续聊需附一份无口令冷备副本（base64 可读），仅用于云端冷备 Agent 续跑。</div>

    <div class="section-title">诊断</div>
    <div class="form-group"><label class="form-label">服务端 Err.log（运行时错误）</label>
      <textarea class="form-input" id="err-log-box" rows="4" readonly placeholder="暂无错误"></textarea>
    </div>
    <div class="form-row" style="margin-top:10px;">
      <button class="btn" id="btn-err-refresh">刷新</button>
      <button class="btn" id="btn-err-clear">清空 Err.log</button>
    </div>

    <div class="section-title">语音助手「小龙」</div>
    <div class="switch-row">
      <div class="sw-info"><b>启用语音助手</b><span>浮层宠物化身，支持多轮对话、查询进度、插入消息/任务、语音+文本双模输入</span></div>
      <label class="switch"><input type="checkbox" id="set-voice-assist" ${c.ENABLE_VOICE_ASSISTANT ? 'checked' : ''} /><span class="slider"></span></label>
    </div>
    <div class="form-hint">依赖浏览器 Web Speech API（Chrome/Edge 最佳）。语音识别使用浏览器原生 STT，LLM 对话使用当前配置的模型。浮层支持拖拽、抚摸、心情动画。</div>

    <div class="form-group" style="margin-top:12px;"><label class="form-label">吉祥物外观</label>
      <select class="form-input" id="set-mascot-style" style="flex:0 0 200px">
        <option value="custom" ${c.mascot_style === 'custom' || !c.mascot_style ? 'selected' : ''}>SVG 宠物（默认）</option>
        <option value="dragon" ${c.mascot_style === 'dragon' ? 'selected' : ''}>龙猫</option>
        <option value="siri" ${c.mascot_style === 'siri' ? 'selected' : ''}>Siri 彩色球</option>
        <option value="blob" ${c.mascot_style === 'blob' ? 'selected' : ''}>水滴</option>
        <option value="cat" ${c.mascot_style === 'cat' ? 'selected' : ''}>猫咪</option>
        <option value="robot" ${c.mascot_style === 'robot' ? 'selected' : ''}>机器人</option>
      </select>
    </div>
    <div class="form-row" style="margin-top:8px;align-items:center;gap:12px;">
      <div id="mascot-live-preview" style="width:56px;height:56px;flex:0 0 auto;"></div>
      <div class="form-hint" style="margin-top:0;">预览：选择外观后实时预览效果。</div>
    </div>

    <div class="section-title">完整性校验（DeveraiIntegrityService）</div>
    <div class="switch-row">
      <div class="sw-info"><b>启用完整性校验</b><span>HKDF + HMAC-SHA256 防篡改签名，保护工具/资产/文档/语音数据</span></div>
      <label class="switch"><input type="checkbox" id="set-integrity" ${c.ENABLE_INTEGRITY !== false ? 'checked' : ''} /><span class="slider"></span></label>
    </div>
    <div class="form-row" style="gap:8px;flex-wrap:wrap;margin-top:10px;">
      <button class="btn" id="btn-integrity-selfcheck">运行自检</button>
      <span id="integrity-selfcheck-result" style="font-size:12px;color:var(--text-dim)"></span>
    </div>
    <div class="form-hint">使用 HKDF 从机器身份 + 用户盐值 + 随机数派生主密钥，HMAC-SHA256 签名绑定上下文类型，timing-safe 比较防时序攻击。</div>
  </div>`;
}

function renderModelsPage() {
  return `<div class="settings-page" data-page="models">
    <div class="settings-header">
      <div>
        <div class="settings-title">Models</div>
        <div class="settings-desc">Manage custom AI models with your own API keys.</div>
      </div>
      <div class="settings-actions">
        <button class="btn" id="btn-model-docs">View Docs</button>
        <button class="btn primary" id="btn-model-add">${icon('plus', 14)} Add</button>
      </div>
    </div>
    <div id="model-registry"></div>
  </div>`;
}

function renderAgentsPage() {
  return `<div class="settings-page" data-page="agents">
    <div class="settings-header">
      <div>
        <div class="settings-title">Agents</div>
        <div class="settings-desc">Create specialized agents for specific tasks. Each agent has its own context window, tool permissions, and system prompts.</div>
      </div>
    </div>
    <div class="section-title" style="margin-top:0;border-top:none;padding-top:0;">Built-in (Experts)</div>
    <div class="form-hint">Configure models, tools, and more for each subagent. After individual customization, switching models in chat only affects the lead agent.</div>
    <div id="agents-built-in"></div>
    <div class="section-title">Custom</div>
    <div id="agents-custom"></div>
    <div class="form-hint">网页版为内置专家参考清单；自定义专家与模型级配置请使用桌面版。</div>
  </div>`;
}

function renderSkillsPage() {
  return `<div class="settings-page" data-page="skills">
    <div class="settings-header">
      <div>
        <div class="settings-title">Skills & Commands</div>
        <div class="settings-desc">Extend your Agent with skills(agents/skills included by default) and create commands to streamline your workflow.</div>
      </div>
    </div>
    <div class="section-title" style="margin-top:0;border-top:none;padding-top:0;">Skills</div>
    <div id="skills-list"></div>
    <div class="section-title">Commands</div>
    <div id="commands-list"></div>
  </div>`;
}

// v8.17 安全中心
function renderSecurityPage() {
  return `
  <div class="settings-page" data-page="security">
    <div class="section-title">安全中心</div>
    <div style="font-size:13px;color:var(--fg2);margin-bottom:12px;">聚合工作空间安全能力状态与审计日志</div>

    <div class="card" style="margin-bottom:8px;">
      <div style="font-weight:600;">命令安全</div>
      <div style="font-size:13px;color:var(--fg2);">危险命令审批门始终开启（danger_ok 严格校验）</div>
    </div>
    <div class="card" style="margin-bottom:8px;">
      <div style="font-weight:600;">文件安全</div>
      <div style="font-size:13px;color:var(--fg2);">只读保护名单内置（Windows 大小写归一）</div>
    </div>
    <div class="card" style="margin-bottom:8px;">
      <div style="font-weight:600;">网络安全</div>
      <div style="font-size:13px;color:var(--fg2);">SSRF 自环防护强制开启（数值 IP 归一 + 自环端口检测）</div>
    </div>
    <div class="card" style="margin-bottom:8px;">
      <div style="font-weight:600;">身份验证</div>
      <div style="font-size:13px;color:var(--fg2);">HMAC Cookie + Bearer Token 双通道鉴权</div>
    </div>

    <div class="section-title" style="margin-top:16px;">全局协调看板（跨工作区 Agent 闸口）</div>
    <div style="font-size:13px;color:var(--fg2);margin-bottom:8px;">本机所有并发 Agent 在干什么、要操作哪些外部资源（如 SSH 主机）、有无冲突。发现冲突要在损害发生前处理：按工作区路径找到对应窗口，点它的「停止」。</div>
    <div class="card" style="padding:10px;">
      <textarea id="coord-board-box" readonly style="width:100%;height:150px;font-size:12px;font-family:monospace;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:8px;resize:vertical;" placeholder="点击刷新读取协调看板"></textarea>
      <div style="display:flex;gap:8px;margin-top:8px;align-items:center;">
        <button class="btn" onclick="loadCoordinationBoard()">刷新</button>
        <span id="coord-board-status" style="font-size:12px;color:var(--fg2);"></span>
      </div>
    </div>

    <div class="section-title" style="margin-top:16px;">外部 API 清单（授权给 AI）</div>
    <div style="font-size:13px;color:var(--fg2);margin-bottom:8px;">清单内的 API，AI 可通过 api_request 工具调用（首次调用需你在聊天卡片中授权）。密钥仅存浏览器本地。</div>
    <div id="extapi-list"></div>
    <div class="card" style="margin-top:8px;padding:10px;">
      <div style="display:grid;grid-template-columns:1fr 2fr 1fr;gap:6px;margin-bottom:6px;">
        <input id="extapi-name" placeholder="名称（如 github）" style="width:100%;" />
        <input id="extapi-url" placeholder="https://api.example.com" style="width:100%;" />
        <input id="extapi-key" placeholder="API Key（可选）" style="width:100%;" />
      </div>
      <div style="display:flex;gap:6px;">
        <input id="extapi-desc" placeholder="用途说明（可选，展示给 AI 与授权卡片）" style="flex:1;" />
        <button class="btn primary" onclick="addExtApi()">添加</button>
      </div>
    </div>

    <div class="section-title" style="margin-top:16px;">Heartbeat 泄露检查</div>
    <div style="font-size:13px;color:var(--fg2);margin-bottom:8px;">一键检查：扫描本地工作区疑似泄露凭证（API Key/私钥/密码）+ 对上方 API 厂商做存活心跳。可选 SMTP 邮件报告（需服务端配置 SMTP 环境变量）。</div>
    <div class="card" style="padding:10px;">
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
        <button class="btn primary" id="btn-leak-scan" onclick="runLeakScan()">一键检查</button>
        <input id="leak-notify-email" placeholder="报告收件邮箱（可选，SMTP 未配置时忽略）" style="flex:1;min-width:200px;" />
        <span id="smtp-status" style="font-size:12px;color:var(--fg2);"></span>
      </div>
      <div id="leak-result" style="margin-top:10px;font-size:13px;"></div>
    </div>

    <div class="section-title" style="margin-top:16px;">出关草稿（邮件）</div>
    <div style="font-size:13px;color:var(--fg2);margin-bottom:8px;">AI 经 outbound_deliver 审核通过、目标为 email 的内容会暂存为草稿。发送需你在此手动触发（AI 不能直发邮件）。</div>
    <div id="outbound-drafts"></div>

    <div class="section-title" style="margin-top:16px;">审计日志</div>
    <textarea id="audit-log-box" readonly style="width:100%;height:180px;font-size:12px;font-family:monospace;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:8px;resize:vertical;" placeholder="暂无审计记录"></textarea>
    <div style="display:flex;gap:8px;margin-top:8px;">
      <button class="btn" onclick="loadSecurityAudit()">刷新</button>
      <button class="btn" onclick="exportSecurityAudit()">导出</button>
      <button class="btn danger" onclick="clearSecurityAudit()">清空审计日志</button>
    </div>

    <div class="section-title" style="margin-top:16px;">错误日志</div>
    <textarea id="err-log-box-sec" readonly style="width:100%;height:120px;font-size:12px;font-family:monospace;background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:8px;resize:vertical;"></textarea>
    <div style="display:flex;gap:8px;margin-top:8px;">
      <button class="btn" onclick="loadErrLog()">刷新</button>
      <button class="btn danger" onclick="clearErrLog()">清空错误日志</button>
    </div>
  </div>`;
}

function loadSecurityAudit() {
  api('/api/security/audit').then(r => {
    const box = $('#audit-log-box');
    if (!box) return;
    const entries = (r.entries || []).reverse();
    if (!entries.length) { box.value = '暂无审计记录'; return; }
    box.value = entries.map(e =>
      `[${e.ts || ''}] ${e.action || ''} ${e.user || ''} ${e.ip || ''} ${e.detail || ''}`
    ).join('\n');
  }).catch(() => {});
}

/* v8.34（H8）：webui 人类协调看板。此前网页端只有 AI 工具能读 /api/bridge/coordination，
   人类想看只能问 AI——违背 v8.33「这两个信息也有必要让用户能看到」的裁决。
   渲染走只读 textarea 的 .value 与 textContent，天然规避 innerHTML 注入（FreqErr #152）。 */
function loadCoordinationBoard() {
  const box = $('#coord-board-box');
  const st = $('#coord-board-status');
  if (!box) return;
  api('/api/bridge/coordination').then((r) => {
    const agents = (r && r.agents) || [];
    const conflicts = (r && r.conflicts) || [];
    if (!agents.length) {
      box.value = '当前没有存活的本机 Agent（心跳 TTL ' + ((r && r.ttl_s) || 180) + 's）。';
    } else {
      box.value = agents.map((a) =>
        '[' + (a.status || '?') + '] ' + (a.agent_id || '')
        + '\n    在干什么: ' + (a.task || '（未声明）')
        + '\n    外部资源: ' + ((a.resources || []).join(', ') || '无')
        + '\n    接下来: ' + (a.next_action || '（未声明）')
        + '\n    工作区: ' + (a.workspace || '')).join('\n')
        + (conflicts.length
          ? '\n\n资源冲突:\n' + conflicts.map((c) => '- ' + c.key + ' → ' + (c.agents || []).join(' 与 ')).join('\n')
          : '\n\n资源冲突: 无');
    }
    if (st) st.textContent = conflicts.length ? ('[!] ' + conflicts.length + ' 组资源冲突') : '[OK] 无冲突';
  }).catch((e) => {
    box.value = '读取协调看板失败（需先在设置中授权命令桥工作区）';
    if (st) st.textContent = String((e && e.message) || e);
  });
}

function exportSecurityAudit() {
  const box = $('#audit-log-box');
  if (!box || !box.value.trim()) { toast('无审计记录可导出', 'err'); return; }
  const blob = new Blob([box.value], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'audit_' + new Date().toISOString().slice(0, 10) + '.txt';
  a.click();
  URL.revokeObjectURL(a.href);
}

function clearSecurityAudit() {
  if (!confirm('确定清空全部审计记录？此操作不可逆。')) return;
  api('/api/security/audit/clear', { method: 'POST' }).then(() => {
    toast('审计日志已清空', 'ok');
    loadSecurityAudit();
  }).catch(() => toast('清空失败', 'err'));
}

function clearErrLog() {
  if (!confirm('确定清空 Err.log？')) return;
  api('/api/errs/clear', { method: 'POST' }).then(() => {
    toast('错误日志已清空', 'ok');
    loadErrLog();
  }).catch(() => toast('清空失败', 'err'));
}

// v8.22 外部 API 清单（安全中心编辑器；数据在 ExtAPIs/localStorage）
function renderExtApiEditor() {
  const box = $('#extapi-list');
  if (!box) return;
  const list = (typeof ExtAPIs !== 'undefined') ? ExtAPIs.list() : [];
  if (!list.length) {
    box.innerHTML = '<div style="font-size:13px;color:var(--fg2);padding:6px 0;">（清单为空——添加后 AI 才能调用外部 API）</div>';
    return;
  }
  box.innerHTML = '';
  list.forEach((it) => {
    const row = el('div', 'card');
    row.style.cssText = 'margin-bottom:6px;padding:8px 10px;display:flex;align-items:center;gap:8px;';
    const info = el('div', '');
    info.style.cssText = 'flex:1;min-width:0;';
    info.innerHTML = `<div style="font-weight:600;">${esc(it.name)}</div>` +
      `<div style="font-size:12px;color:var(--fg2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(it.base_url)}${it.api_key ? ' [已存密钥]' : ''}${it.desc ? ' — ' + esc(it.desc) : ''}</div>`;
    const del = el('button', 'btn danger', '删除');
    del.onclick = () => {
      if (!confirm('删除 API「' + it.name + '」？')) return;
      ExtAPIs.remove(it.name);
      renderExtApiEditor();
      toast('已删除 ' + it.name, 'ok');
    };
    row.appendChild(info);
    row.appendChild(del);
    box.appendChild(row);
  });
}

function addExtApi() {
  if (typeof ExtAPIs === 'undefined') { toast('ExtAPIs 模块未加载', 'err'); return; }
  const name = ($('#extapi-name') || {}).value || '';
  const url = (($('#extapi-url') || {}).value || '').trim();
  const key = (($('#extapi-key') || {}).value || '').trim();
  const desc = (($('#extapi-desc') || {}).value || '').trim();
  const ok = ExtAPIs.upsert({ name: name.trim(), base_url: url, api_key: key, desc });
  if (!ok) { toast('名称需为 1-64 位字母数字._- 且 URL 必填', 'err'); return; }
  ['#extapi-name', '#extapi-url', '#extapi-key', '#extapi-desc'].forEach((s) => { const i = $(s); if (i) i.value = ''; });
  renderExtApiEditor();
  toast('API 已加入清单', 'ok');
}

function loadSmtpStatus() {
  api('/api/security/smtp_status').then((r) => {
    const s = $('#smtp-status');
    if (s) s.textContent = r.configured ? 'SMTP 已配置' : 'SMTP 未配置（报告邮件不可用）';
  }).catch(() => {});
}

async function runLeakScan() {
  const btn = $('#btn-leak-scan');
  const out = $('#leak-result');
  if (!out) return;
  if (btn) { btn.disabled = true; btn.textContent = '检查中…'; }
  out.textContent = '正在扫描本地工作区与厂商心跳…';
  const email = (($('#leak-notify-email') || {}).value || '').trim();
  const vendors = (typeof ExtAPIs !== 'undefined')
    ? ExtAPIs.list().map((x) => ({ name: x.name, base_url: x.base_url })) : [];
  try {
    const r = await api('/api/security/leak_scan', { method: 'POST', body: { vendors, notify_email: email } });
    const parts = [];
    if (r.ws_note) parts.push('[!] ' + r.ws_note);
    const f = r.findings || [];
    parts.push(f.length ? `[X] 本地疑似泄露 ${f.length} 处：` : '[OK] 本地未发现疑似泄露凭证');
    f.slice(0, 30).forEach((x) => parts.push(`    ${x.file}:${x.line} (${x.kind}) ${x.preview}`));
    if (f.length > 30) parts.push(`    …另有 ${f.length - 30} 处`);
    const vs = r.vendors || [];
    if (vs.length) {
      vs.forEach((v) => {
        parts.push(v.error
          ? `[X] 厂商 ${v.name}: ${v.error}`
          : `[OK] 厂商 ${v.name}: HTTP ${v.status}（${v.latency_ms}ms）`);
      });
    } else {
      parts.push('（外部 API 清单为空，未执行厂商心跳）');
    }
    if (r.smtp_sent) parts.push('[OK] 报告邮件已发送至 ' + email);
    else if (r.smtp_note) parts.push('[!] 邮件发送失败: ' + r.smtp_note);
    out.style.whiteSpace = 'pre-wrap';
    out.textContent = parts.join('\n');
    toast('泄露检查完成', (r.findings || []).length ? 'err' : 'ok');
  } catch (e) {
    out.textContent = '检查失败: ' + e.message;
    toast('泄露检查失败', 'err');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '一键检查'; }
  }
}

function renderOutboundDrafts() {
  const box = $('#outbound-drafts');
  if (!box) return;
  let drafts = [];
  try { drafts = JSON.parse(localStorage.getItem('deverai.outbound.drafts') || '[]'); } catch (e) {}
  if (!drafts.length) {
    box.innerHTML = '<div style="font-size:13px;color:var(--fg2);padding:6px 0;">（暂无草稿）</div>';
    return;
  }
  box.innerHTML = '';
  drafts.slice().reverse().forEach((d) => {
    const row = el('div', 'card');
    row.style.cssText = 'margin-bottom:6px;padding:8px 10px;';
    const head = el('div', '');
    head.style.cssText = 'display:flex;align-items:center;gap:8px;';
    head.innerHTML = `<div style="font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(d.title || '(无主题)')}</div>` +
      `<div style="font-size:12px;color:var(--fg2);">${new Date(d.created_at || Date.now()).toLocaleString()}</div>`;
    const body = el('div', '');
    body.style.cssText = 'font-size:12px;color:var(--fg2);white-space:pre-wrap;max-height:120px;overflow-y:auto;margin:6px 0;border:1px solid var(--border);border-radius:6px;padding:6px;';
    body.textContent = String(d.content || '').slice(0, 2000);
    const acts = el('div', '');
    acts.style.cssText = 'display:flex;gap:6px;';
    const mail = el('button', 'btn primary', '用邮件客户端发送');
    mail.onclick = () => {
      const href = 'mailto:?subject=' + encodeURIComponent(d.title || '') + '&body=' + encodeURIComponent(String(d.content || '').slice(0, 1500));
      location.href = href;
    };
    const cp = el('button', 'btn', '复制全文');
    cp.onclick = () => {
      navigator.clipboard.writeText(String(d.content || '')).then(() => toast('已复制', 'ok'));
    };
    const del = el('button', 'btn danger', '删除');
    del.onclick = () => {
      const rest = drafts.filter((x) => x.id !== d.id);
      localStorage.setItem('deverai.outbound.drafts', JSON.stringify(rest));
      renderOutboundDrafts();
      toast('草稿已删除', 'ok');
    };
    acts.appendChild(mail); acts.appendChild(cp); acts.appendChild(del);
    row.appendChild(head); row.appendChild(body); row.appendChild(acts);
    box.appendChild(row);
  });
}

function renderAdvancedPage() {
  return `<div class="settings-page" data-page="advanced">
    <div class="settings-header">
      <div>
        <div class="settings-title">Advanced</div>
        <div class="settings-desc">Advanced options for power users.</div>
      </div>
    </div>
    <div class="section-title">实验性</div>
    <div class="form-hint">以下选项可能影响稳定性，修改前请确认。</div>
    <div id="advanced-switches"></div>
  </div>`;
}

function closeOnBackdrop() {
  $('#modal-root').onclick = (e) => { if (e.target === $('#modal-root')) closeModal(); };
}
function closeModal() { $('#modal-root').classList.add('hidden'); }

// v1.0.0 吉祥物外观设置接线：选择样式、上传自定义图片、实时预览
function wireMascotSettings() {
  var styleSelect = $('#set-mascot-style');
  var uploadRow = $('#mascot-custom-upload');
  var fileInput = $('#mascot-custom-file');
  var clearBtn = $('#mascot-clear-custom');
  var previewBox = $('#mascot-live-preview');

  // 内置 SVG 精灵（blob/cat/robot 等用小 SVG，custom/dragon/siri 用 CSS 渲染）
  var SPRITES = {
    blob: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><path d="M32 6c14 0 24 9 24 22 0 11-7 20-17 24-8 3-15 1-23-2-9-4-14-10-14-18 0-14 12-26 30-26z" fill="#6d8dff"/><circle cx="24" cy="30" r="3.4" fill="#1b2350"/><circle cx="40" cy="30" r="3.4" fill="#1b2350"/><circle cx="25" cy="29" r="1.2" fill="#fff"/><circle cx="41" cy="29" r="1.2" fill="#fff"/><path d="M28 40q4 3 8 0" stroke="#1b2350" stroke-width="2.4" stroke-linecap="round" fill="none"/></svg>',
    cat: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><path d="M16 26l-8-6 4 12zM48 26l8-6-4 12z" fill="#ffb36b"/><circle cx="32" cy="32" r="20" fill="#ffb36b"/><circle cx="24" cy="30" r="3.4" fill="#40260f"/><circle cx="40" cy="30" r="3.4" fill="#40260f"/><path d="M29 38l-1.5 2 4 2 3-3.5z" fill="#40260f"/><path d="M24 40q4 4 8 0" stroke="#8a5a2b" stroke-width="2.4" stroke-linecap="round" fill="none"/></svg>',
    robot: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect x="14" y="18" width="36" height="32" rx="8" fill="#b8c4d9"/><circle cx="32" cy="11" r="3" fill="#6d8dff"/><rect x="21" y="26" width="9" height="8" rx="2" fill="#26324d"/><rect x="34" y="26" width="9" height="8" rx="2" fill="#26324d"/><circle cx="25.5" cy="28.5" r="1.4" fill="#7fe3ff"/><circle cx="38.5" cy="28.5" r="1.4" fill="#7fe3ff"/><path d="M29 40h6" stroke="#55617a" stroke-width="2.2" stroke-linecap="round"/></svg>',
  };

  function updatePreview() {
    if (!previewBox) return;
    var key = (styleSelect && styleSelect.value) || App.config.mascot_style || 'custom';
    if (key === 'dragon') {
      previewBox.innerHTML = '<div class="voice-mascot recording" style="width:48px;height:48px;transform-origin:center"><div class="mascot-body"><div class="mascot-ear left"></div><div class="mascot-ear right"></div><div class="mascot-face"><div class="mascot-eye left"></div><div class="mascot-eye right"></div><div class="mascot-nose"></div><div class="mascot-mouth"></div></div><div class="mascot-belly"></div></div></div>';
    } else if (key === 'siri') {
      previewBox.innerHTML = '<div class="mascot-siri-ball" style="width:48px;height:48px;"><div class="siri-orb siri-orb-1"></div><div class="siri-orb siri-orb-2"></div><div class="siri-orb siri-orb-3"></div><div class="siri-orb siri-orb-4"></div><div class="siri-orb siri-orb-5"></div></div>';
    } else if (key === 'custom' && App.config.mascot_custom_img) {
      previewBox.innerHTML = '';
      const src = String(App.config.mascot_custom_img);
      if (/^data:image\/(?:png|jpe?g|gif|webp)(?:;base64)?,/i.test(src)) {
        const img = document.createElement('img');
        img.src = src;
        img.style.cssText = 'width:48px;height:48px;border-radius:50%;object-fit:cover';
        previewBox.appendChild(img);
      }
    } else {
      var svg = SPRITES[key] || SPRITES.blob;
      previewBox.innerHTML = '<div style="width:48px;height:48px">' + svg + '</div>';
    }
  }

  if (styleSelect) {
    styleSelect.onchange = function () {
      var style = styleSelect.value;
      if (uploadRow) uploadRow.style.display = (style === 'custom') ? '' : 'none';
      updatePreview();
    };
  }

  // 文件上传
  if (fileInput) {
    fileInput.onchange = function (e) {
      var file = e.target.files && e.target.files[0];
      if (!file) return;
      if (file.size > 2 * 1024 * 1024) { toast('图片过大（上限 2MB）', 'err'); return; }
      if (!file.type.startsWith('image/')) { toast('请选择图片文件', 'err'); return; }
      var reader = new FileReader();
      reader.onload = function (ev) {
        var dataUrl = ev.target.result;
        App.config.mascot_custom_img = dataUrl;
        var preview = $('#mascot-preview');
        if (preview) {
          preview.innerHTML = '';
          const img = document.createElement('img');
          img.src = dataUrl;
          img.style.cssText = 'width:100%;height:100%;object-fit:cover';
          preview.appendChild(img);
        }
        updatePreview();
        toast('自定义图片已上传', 'ok');
      };
      reader.onerror = function () { toast('图片读取失败', 'err'); };
      reader.readAsDataURL(file);
    };
  }

  // 清除自定义图片
  if (clearBtn) {
    clearBtn.onclick = function () {
      App.config.mascot_custom_img = '';
      var preview = $('#mascot-preview');
      if (preview) preview.innerHTML = '<span style="font-size:11px;color:var(--text-faint)">预览</span>';
      if (fileInput) fileInput.value = '';
      updatePreview();
      toast('自定义图片已清除', 'ok');
    };
  }

  // 初始化
  if (uploadRow) {
    var cur = (styleSelect && styleSelect.value) || App.config.mascot_style || 'custom';
    uploadRow.style.display = (cur === 'custom') ? '' : 'none';
  }
  updatePreview();
}

function wireSettings(page) {
  renderSettingsPages();
  renderSettingsNav(page);
  switchSettingsPage(page);

  $('#btn-cancel-settings').onclick = closeModal;
  $('#btn-save-settings').onclick = saveSettings;

  const prow = $('#providers');
  if (prow) {
    PROVIDERS.forEach((p) => {
      const b = el('button', 'provider-btn', esc(p.name));
      b.onclick = () => { $('#set-base').value = p.base; $('#set-model').value = p.model; };
      prow.appendChild(b);
    });
  }

  const sl = $('#switch-list');
  if (sl) {
    SWITCHES.forEach(([key, name, desc]) => {
      const row = el('div', 'switch-row');
      const info = el('div', 'sw-info');
      info.appendChild(el('b', '', esc(name)));
      info.appendChild(el('span', '', esc(desc)));
      row.appendChild(info);
      row.appendChild(switchEl(!!App.config[key], (on) => { App.config[key] = on; }));
      sl.appendChild(row);
    });
  }

  const mc = $('#mode-cards');
  if (mc) {
    mc.appendChild(modeCard('流量模式', 'traffic', '仅压缩传输内容：AI 输出精简纯文本、停止大规模同步，绝不切换本地模型。', ['traffic_mode']));
    mc.appendChild(modeCard('肝完睡觉模式', 'sleep', '指定最终目标；任务完成后全屏倒计时 5 分钟，无操作则关机/休眠（需授权）。', ['sleep_enabled'], true));
    mc.appendChild(modeCard('Token 计费模式', 'token', '显著减少模型调用次数：AOE 取最小调用路径、激进压缩历史。适合昂贵模型。', ['token_mode']));
  }

  const wsMode = $('#ws-mode-label');
  if (wsMode) wsMode.textContent = fsModeLabel();
  const wsName = $('#ws-name-settings');
  if (wsName) wsName.textContent = FS.rootName || '未授权';

  const btnFss = $('#btn-pick-fss');
  if (btnFss) {
    btnFss.onclick = async () => {
      if (!window.showDirectoryPicker) { toast('当前浏览器不支持 File System Access，将使用命令桥', 'err'); }
      const r = await pickWorkspace();
      if (r.ok) {
        toast('工作区已授权: ' + (r.name || r.workspace), 'ok');
        updateStatusbar();
        Tree.refresh();
      } else {
        toast(r.message || '未授权', 'err');
      }
    };
  }
  const btnBridge = $('#btn-pick-bridge');
  if (btnBridge) {
    btnBridge.onclick = async () => {
      const r = await pickBridge();
      if (r.ok) {
        toast('命令桥工作区: ' + r.workspace, 'ok');
        $('#set-ws-bridge').value = r.workspace;
        // v8.13：pickBridge 已把 FS 置为 bridge，这里无条件同步显示与文件树
        FS.mode = 'bridge';
        FS.rootName = basename(r.workspace) || '工作区';
        Tree.refresh().catch(() => {});
        updateStatusbar();
      } else {
        toast(r.message || '未选择', 'err');
      }
    };
  }
  const btnSetBridge = $('#btn-set-ws-bridge');
  if (btnSetBridge) {
    btnSetBridge.onclick = async () => {
      const path = $('#set-ws-bridge').value.trim();
      if (!path) return;
      try {
        await api('/api/bridge/workspace', { method: 'POST', body: { path } });
        const st = await refreshBridgeState();
        if (st.authorized) {
          FS.mode = 'bridge';
          FS.rootName = basename(st.workspace) || '工作区';
          Tree.refresh().catch(() => {});
          updateStatusbar();
        }
        toast('命令桥工作区已设置为 ' + path, 'ok');
      } catch (e) { toast('设置失败: ' + e.message, 'err'); }
    };
  }

  const btnTest = $('#btn-test');
  if (btnTest) {
    btnTest.onclick = async () => {
      const elr = $('#test-result');
      elr.textContent = '测试中…';
      const key = $('#set-key').value.trim() || getApiKey();
      try {
        const r = await testLlm($('#set-base').value.trim(), key, $('#set-model').value.trim());
        elr.innerHTML = r.ok ? `<span style="color:var(--ok)">OK ${esc(r.message)}</span>` : `<span style="color:var(--err)">FAIL ${esc(r.message)}</span>`;
      } catch (e) {
        elr.innerHTML = `<span style="color:var(--err)">FAIL ${esc(e.message)}</span>`;
      }
    };
  }

  const btnSyncExport = $('#btn-sync-export');
  if (btnSyncExport) {
    btnSyncExport.onclick = async () => {
      const pw = $('#set-sync-pw').value;
      if (!pw) return toast('请先填写同步加密口令', 'err');
      const snap = await buildSnapshot();
      const data = await encryptSnapshot(JSON.stringify(snap), pw);
      const blob = new Blob([data], { type: 'text/plain' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `deverai_${Date.now()}.deverai`;
      a.click();
      toast('快照已导出', 'ok');
    };
  }
  const btnSyncImport = $('#btn-sync-import');
  if (btnSyncImport) btnSyncImport.onclick = () => $('#sync-file').click();
  const syncFile = $('#sync-file');
  if (syncFile) {
    syncFile.onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const pw = $('#set-sync-pw').value;
      if (!pw) return toast('请先填写同步加密口令', 'err');
      try {
        const text = await f.text();
        const json = await decryptSnapshot(text, pw);
        const snap = JSON.parse(json);
        await applySnapshot(snap);
        toast('快照已导入', 'ok');
        closeModal();
        location.reload();
      } catch (err) {
        toast('导入失败: ' + err.message, 'err', 5000);
      }
    };
  }
  const btnSyncPush = $('#btn-sync-push');
  if (btnSyncPush) {
    btnSyncPush.onclick = async () => {
      const url = $('#set-sync-url').value.trim();
      const pw = $('#set-sync-pw').value;
      if (!url) return toast('请填写同步服务器地址', 'err');
      if (!pw) return toast('请填写同步加密口令', 'err');
      const snap = await buildSnapshot();
      const data = await encryptSnapshot(JSON.stringify(snap), pw);
      // v8.13：附带与桌面端 cold 语义对等的网页冷备信封（sync_server /chat 可直接解包续聊；
      // 桌面端 pull 也可经 drift_cold 拿回历史，两端同步链路不再互盲）。
      const cold = await buildWebCold(snap);
      try {
        const r = await fetch(url.replace(/\/$/, '') + '/push', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...(syncTokenHeader()) },
          body: JSON.stringify({ data, cold }),
        });
        const j = await r.json();
        toast(j.ok ? '已推送（' + j.stored + ' 字节）' : (j.message || '推送失败'), j.ok ? 'ok' : 'err');
      } catch (err) {
        toast('推送失败: ' + err.message, 'err');
      }
    };
  }
  const btnSyncPull = $('#btn-sync-pull');
  if (btnSyncPull) {
    btnSyncPull.onclick = async () => {
      const url = $('#set-sync-url').value.trim();
      const pw = $('#set-sync-pw').value;
      if (!url) return toast('请填写同步服务器地址', 'err');
      if (!pw) return toast('请填写同步加密口令', 'err');
      try {
        const r = await fetch(url.replace(/\/$/, '') + '/pull', { headers: { ...syncTokenHeader() } });
        const j = await r.json();
        if (!j.ok) return toast(j.message || '拉取失败', 'err');
        // v8.13：跨端快照解析——网页版信封优先，失败回落桌面版冷备/主快照格式
        const snap = await parseServerSnapshot(j.data, j.drift_cold || '', pw);
        await applySnapshot(snap);
        toast('已拉取并应用', 'ok');
        closeModal();
        location.reload();
      } catch (err) {
        toast('拉取失败: ' + err.message, 'err', 5000);
      }
    };
  }

  const btnErrRefresh = $('#btn-err-refresh');
  if (btnErrRefresh) btnErrRefresh.onclick = loadErrLog;
  const btnErrClear = $('#btn-err-clear');
  if (btnErrClear) {
    btnErrClear.onclick = async () => {
      await api('/api/errs/clear', { method: 'POST' });
      $('#err-log-box').value = '';
      toast('Err.log 已清空', 'ok');
    };
  }

  // v1.1.0 完整性校验自检按钮
  const btnIntegritySelfCheck = $('#btn-integrity-selfcheck');
  const integrityResult = $('#integrity-selfcheck-result');
  if (btnIntegritySelfCheck) {
    btnIntegritySelfCheck.onclick = async () => {
      if (integrityResult) integrityResult.textContent = '自检中…';
      try {
        const r = await api('/api/bridge/integrity/self_check', { method: 'POST' });
        if (r.ok && r.result) {
          const types = r.result.types || {};
          const failed = Object.entries(types).filter(([k, v]) => !v);
          if (r.result.ok) {
            if (integrityResult) integrityResult.innerHTML = '<span style="color:var(--ok)">✓ 自检通过（' + Object.keys(types).length + ' 项）</span>';
            toast('完整性自检通过', 'ok');
          } else {
            if (integrityResult) integrityResult.innerHTML = '<span style="color:var(--err)">✗ 失败: ' + failed.map(([k]) => k).join(', ') + '</span>';
            toast('完整性自检失败', 'err');
          }
        } else {
          if (integrityResult) integrityResult.textContent = '自检失败: ' + (r.error || '未知');
        }
      } catch (e) {
        if (integrityResult) integrityResult.textContent = '自检错误: ' + e.message;
      }
    };
  }

  // v1.0.0 吉祥物外观设置接线
  wireMascotSettings();

  const btnModelAdd = $('#btn-model-add');
  if (btnModelAdd) {
    btnModelAdd.onclick = () => {
      const inp = $('#model-add-input');
      if (inp) { inp.focus(); return; }
      const box = $('#model-registry');
      const row = el('div', 'switch-row');
      const input = el('input', 'form-input', '');
      input.id = 'model-add-input';
      input.placeholder = 'id|名称|url|kind(chat/embedding)';
      input.style.flex = '1';
      const addBtn = el('button', 'btn primary', 'Add');
      addBtn.onclick = async () => {
        const parts = input.value.split('|').map((s) => s.trim());
        if (!parts[0]) { toast('需填模型 id', 'err'); return; }
        try {
          await api('/api/bridge/models', { method: 'POST', body: {
            id: parts[0], name: parts[1] || parts[0], url: parts[2] || '', kind: parts[3] || 'chat',
          } });
          toast('已新增 ' + parts[0], 'ok');
          loadModelRegistry();
        } catch (e) { toast('新增失败: ' + e.message, 'err'); }
      };
      row.appendChild(input);
      row.appendChild(addBtn);
      box.prepend(row);
      input.focus();
    };
  }

  $$('.page-tab').forEach((tab) => {
    tab.onclick = () => {
      const parent = tab.closest('.settings-page');
      const scope = parent.dataset.page;
      $$('.page-tab', parent).forEach((t) => t.classList.toggle('active', t === tab));
      const userPanel = $(`#${scope}-user-panel`);
      const agentPanel = $(`#${scope}-agent-panel`);
      if (tab.dataset.tab === 'user') {
        if (userPanel) userPanel.classList.remove('hidden');
        if (agentPanel) agentPanel.classList.add('hidden');
      } else {
        if (userPanel) userPanel.classList.add('hidden');
        if (agentPanel) agentPanel.classList.remove('hidden');
      }
    };
  });
  renderBuiltInExperts();
  renderCustomAgents();
  renderSkillsList();
  renderCommandsList();
  renderAdvancedSwitches();

  loadModelRegistry();
  wireModelSelect();
}

function syncTokenHeader() {
  const t = App.config && App.config.sync_token ? App.config.sync_token : '';
  return t ? { 'X-Sync-Token': t } : {};
}

function modeCard(title, cls, desc, keys, hasBody) {
  const card = el('div', 'mode-card');
  const head = el('div', 'mode-card-head');
  head.appendChild(el('span', 'mode-card-title', title + ' <span class="mode-chip ' + cls + '">' + cls + '</span>'));
  head.appendChild(switchEl(!!App.config[keys[0]], (on) => {
    App.config[keys[0]] = on;
    if (hasBody) { const b = $('.mode-card-body', card); b.style.display = on ? '' : 'none'; }
  }));
  card.appendChild(head);
  card.appendChild(el('div', 'mode-card-desc', desc));
  const body = el('div', 'mode-card-body');
  if (cls === 'sleep') {
    body.style.display = App.config.sleep_enabled ? '' : 'none';
    body.innerHTML = `
      <div class="form-group"><label class="form-label">最终目标（完成后自动触发倒计时）</label>
        <input class="form-input" id="sleep-goal" placeholder="例如：完成登录页面并运行测试通过" value="${esc(App.config.sleep_goal || '')}" /></div>
      <div class="form-row">
        <div class="form-group"><label class="form-label">倒计时结束动作</label>
          <select class="form-input" id="sleep-action">
            <option value="shutdown" ${App.config.sleep_action === 'shutdown' ? 'selected' : ''}>关机</option>
            <option value="hibernate" ${App.config.sleep_action === 'hibernate' ? 'selected' : ''}>休眠</option>
          </select></div>
      </div>
      <div class="switch-row" id="sleep-auth-row">
        <div class="sw-info"><b>授权自动执行电源操作</b><span>仅在你明确同意后才会真正关机/休眠</span></div>
      </div>`;
    // v8.13：switchEl 的 onclick 是 DOM property，不能经 outerHTML 模板化（会丢事件）
    const authRow = body.querySelector('#sleep-auth-row');
    authRow.appendChild(switchEl(!!App.config.sleep_authorized, (on) => { App.config.sleep_authorized = on; }));
  }
  card.appendChild(body);
  return card;
}

/* ---------- v8.5 批次2：模型注册表（卡片式；v8.11 补打分 + 榜单 + 模型选择器填充） ---------- */
async function loadModelRegistry() {
  const box = $('#model-registry');
  const addInput = $('#model-add-input');
  if (box) box.innerHTML = '<div class="form-hint">加载中…</div>';
  let models = [];
  try {
    const r = await api('/api/bridge/models');
    models = (r && r.models) || [];
  } catch (e) {
    if (box) box.innerHTML = '<div class="form-hint">加载失败（需登录）</div>';
    return;
  }
  // v8.13：即使设置页未打开（无 #model-registry），也把注册表填充进主输入区 #model-select
  populateModelSelect(models);
  if (!box) return;
  // v8.11：榜单 + 指标（/models/ranks 端口接线，与桌面版性能榜/性价比榜/未定队列对齐）
  let ranksB1 = null, ranksB2 = null, unscored = [], metrics = [];
  try {
    const rb1 = await api('/api/bridge/models/ranks?mode=b1&top=5');
    const rb2 = await api('/api/bridge/models/ranks?mode=b2&top=5');
    ranksB1 = (rb1 && rb1.ranked) || [];
    ranksB2 = (rb2 && rb2.ranked) || [];
    unscored = (rb1 && rb1.unscored) || [];
    metrics = (rb1 && rb1.metrics) || [];
  } catch (e) { /* 榜单加载失败不影响列表主流程 */ }
  box.innerHTML = '';
  // ---- 榜单区 ----
  if (ranksB1 || ranksB2 || unscored.length || metrics.length) {
    const ranksBox = el('div', 'model-ranks-wrap');
    const ranksHead = el('div', 'model-ranks-head');
    ranksHead.appendChild(el('span', '', '模型榜单'));
    const autoBtn = el('button', 'btn flatbtn', '重搜打分');
    autoBtn.title = '对所有 chat 模型重搜 Benchmark 自动打分（可能需数分钟）';
    autoBtn.onclick = () => runAutoScore();
    ranksHead.appendChild(autoBtn);
    ranksBox.appendChild(ranksHead);
    const ranksCols = el('div', 'model-ranks');
    const mkCol = (title, items, isScore) => {
      const col = el('div', 'model-rank-col');
      col.appendChild(el('div', 'model-rank-title', title));
      if (!items || !items.length) {
        col.appendChild(el('div', 'form-hint', '暂无'));
        return col;
      }
      items.forEach((x, i) => {
        const m = (x && x.model) || x;
        const row = el('div', 'model-rank-row');
        row.appendChild(el('span', 'model-rank-idx', String(i + 1)));
        row.appendChild(el('span', 'model-rank-name', esc(m.name || m.id)));
        if (isScore) {
          const scoreText = (x && x.free) ? '免费' : (x && x.score != null ? Number(x.score).toFixed(2) : '-');
          row.appendChild(el('span', 'model-rank-score', scoreText));
        }
        col.appendChild(row);
      });
      return col;
    };
    if (ranksB1) ranksCols.appendChild(mkCol('性能榜 b.1', ranksB1, true));
    if (ranksB2) ranksCols.appendChild(mkCol('性价比榜 b.2', ranksB2, true));
    if (unscored.length) ranksCols.appendChild(mkCol('未定队列', unscored, false));
    ranksBox.appendChild(ranksCols);
    box.appendChild(ranksBox);
  }
  // ---- 模型卡 ----
  if (!models.length) {
    box.appendChild(el('div', 'empty-card', 'No Model Available'));
  } else {
    models.forEach((m) => {
      const card = el('div', 'model-card');
      const info = el('div', 'model-info');
      const tags = [];
      if (m.kind === 'embedding') tags.push('embedding');
      if (m.caps && m.caps.image) tags.push('视觉');
      info.innerHTML = `
        <div class="model-name">${esc(m.name || m.id)} <span class="tag">${tags.length ? esc(tags.join(' · ')) : esc(m.score_summary || 'Pay As You Go')}</span></div>
        <div class="model-meta">${esc(m.id)}${m.url ? ' · ' + esc(m.url) : ''}</div>`;
      const actions = el('div', 'model-actions');
      // v8.11：打分按钮（/models/score 端口接线）
      const scoreBtn = el('button', 'icon-btn', '打分');
      scoreBtn.title = 'Benchmark 打分（0-10，留空=未定）';
      scoreBtn.onclick = () => openScoreDialog(m, metrics);
      const editBtn = el('button', 'icon-btn', icon('edit', 16));
      editBtn.title = '编辑';
      editBtn.onclick = () => {
        const name = prompt('模型名称', m.name || m.id);
        if (name == null) return;
        const url = prompt('API URL', m.url || '');
        if (url == null) return;
        // 后端无 PUT /models/{id}，编辑复用 POST /models（upsert，带 id 更新非空字段）
        api('/api/bridge/models', { method: 'POST', body: { id: m.id, name, url } })
          .then(() => { toast('已更新', 'ok'); loadModelRegistry(); })
          .catch((e) => toast('更新失败: ' + e.message, 'err'));
      };
      const delBtn = el('button', 'icon-btn', icon('trash', 16));
      delBtn.title = '删除';
      delBtn.onclick = async () => {
        if (!confirm('删除模型 ' + m.id + '？')) return;
        try { await api('/api/bridge/models/' + encodeURIComponent(m.id), { method: 'DELETE' }); toast('已删除', 'ok'); loadModelRegistry(); } catch (e) {}
      };
      actions.appendChild(scoreBtn);
      actions.appendChild(editBtn);
      actions.appendChild(delBtn);
      card.appendChild(info);
      card.appendChild(actions);
      box.appendChild(card);
    });
  }
  // ---- 新增输入行 ----
  if (addInput) {
    const row = el('div', 'switch-row');
    const input = el('input', 'form-input');
    input.value = addInput.value;
    input.id = 'model-add-input';
    input.placeholder = 'id|名称|url|kind(chat/embedding)';
    input.style.flex = '1';
    const addBtn = el('button', 'btn primary', 'Add');
    addBtn.onclick = async () => {
      const parts = input.value.split('|').map((s) => s.trim());
      if (!parts[0]) { toast('需填模型 id', 'err'); return; }
      try {
        await api('/api/bridge/models', { method: 'POST', body: {
          id: parts[0], name: parts[1] || parts[0], url: parts[2] || '', kind: parts[3] || 'chat',
        } });
        toast('已新增 ' + parts[0], 'ok');
        loadModelRegistry();
      } catch (e) { toast('新增失败: ' + e.message, 'err'); }
    };
    row.appendChild(input);
    row.appendChild(addBtn);
    box.prepend(row);
  }
  // v8.11：主输入区模型选择器（#model-select）从注册表填充
  populateModelSelect(models);
}

/* v8.11：打分对话框（/models/score 端口接线） */
function openScoreDialog(m, metrics) {
  const modal = $('#modal-root');
  const box = $('#modal');
  if (!modal || !box) { toast('缺少模态容器', 'err'); return; }
  modal.classList.remove('hidden');
  const rows = (metrics || []).map((mt) => {
    const s = (m.scores && m.scores[mt]) || {};
    const v = (s.value != null) ? s.value : '';
    const fixed = (s.fixed !== false);
    return `<div class="switch-row score-row" data-metric="${esc(mt)}">
      <span class="sw-info"><b>${esc(mt)}</b><span>0-10 分，留空 = 未定</span></span>
      <input type="number" class="form-input score-input" min="0" max="10" step="0.1" value="${esc(v)}" style="width:80px" />
      <label class="score-fixed-label"><input type="checkbox" class="score-fixed-input" ${fixed ? 'checked' : ''} /> 已定</label>
    </div>`;
  }).join('');
  box.innerHTML = `
    <div class="modal-head"><span class="modal-title">打分 — ${esc(m.name || m.id)}</span>
      <button class="icon-btn" id="score-close">${icon('close', 12)}</button></div>
    <div class="modal-body">
      ${rows || '<div class="form-hint">暂无指标（桌面版设置中可配置 score_metrics）</div>'}
      <div class="form-hint">留空表示未定（不参与榜单）；「已定」关闭表示自动打分可刷新该指标。</div>
    </div>
    <div class="modal-foot">
      <button class="btn primary" id="score-save">保存</button>
    </div>`;
  $('#score-close').onclick = () => modal.classList.add('hidden');
  closeOnBackdrop();
  $('#score-save').onclick = async () => {
    try {
      const rowsEls = box.querySelectorAll('.score-row');
      for (const rowEl of rowsEls) {
        const metric = rowEl.dataset.metric;
        const inp = rowEl.querySelector('.score-input');
        const fix = rowEl.querySelector('.score-fixed-input');
        const raw = inp ? inp.value.trim() : '';
        const val = raw === '' ? null : Number(raw);
        const fixed = fix ? !!fix.checked : true;
        await api('/api/bridge/models/score', { method: 'POST', body: { model_id: m.id, metric, value: val, fixed } });
      }
      modal.classList.add('hidden');
      toast('打分已保存', 'ok');
      loadModelRegistry();
    } catch (e) { toast('保存失败: ' + e.message, 'err'); }
  };
}

/* v8.12：重搜 Benchmark 自动打分（/models/auto_score 端口接线，桌面「重搜打分」对等） */
async function runAutoScore() {
  try {
    toast('自动打分进行中（搜索 Benchmark + 评测，可能需数分钟）…', 'info', 5000);
    const cfg = App.config || {};
    // P2-5：浏览器端凭据随请求透传（纯网页部署的 localStorage 配置不写 config.json）
    // v8.13：字段兼容 base_url（网页配置）与 api_base_url（桌面桥）两种键名
    const r = await api('/api/bridge/models/auto_score', {
      method: 'POST',
      body: {
        force: true,
        api_base: String(cfg.api_base_url || cfg.base_url || ''),
        api_key: String(getApiKey ? getApiKey() : (cfg.api_key || '')),
        model: String(cfg.judge_model || cfg.model || ''),
      },
    });
    const done = (r && r.processed) || 0;
    toast(`自动打分完成：处理 ${done}/${(r && r.total) || 0} 个模型`, 'ok');
    loadModelRegistry();
  } catch (e) { toast('自动打分失败: ' + e.message, 'err'); }
}

/* v8.11：主输入区模型选择器从注册表填充（此前硬编码单一项，从未接注册表） */
function populateModelSelect(models) {
  const sel = $('#model-select');
  if (!sel) return;
  const chatModels = (models || []).filter((m) => m.kind !== 'embedding');
  const current = (App.config && App.config.model) || sel.value || '';
  sel.innerHTML = '';
  if (!chatModels.length) {
    const opt = el('option', '');
    opt.value = current || '';
    opt.textContent = current || '（无注册表模型）';
    sel.appendChild(opt);
    return;
  }
  // v8.13：未配置模型时保留一个明确空项，避免浏览器自动选中第一项造成“已切换”错觉
  if (!current) {
    const opt = el('option', '');
    opt.value = '';
    opt.textContent = '（选择模型）';
    sel.appendChild(opt);
  }
  chatModels.forEach((m) => {
    const opt = el('option', '');
    opt.value = m.id;
    opt.textContent = m.name || m.id;
    sel.appendChild(opt);
  });
  // 当前模型不在注册表（手填模型）：追加一项保留可选（textContent 防注入）
  if (current && !chatModels.some((m) => m.id === current)) {
    const extra = el('option', '');
    extra.value = current;
    extra.textContent = current;
    sel.appendChild(extra);
  }
  sel.value = current;
}

/* v8.11：模型选择器切换 → 更新 App.config.model（发消息时生效） */
function wireModelSelect() {
  const sel = $('#model-select');
  if (!sel || sel.__modelWired) return;
  sel.__modelWired = true;
  sel.addEventListener('change', () => {
    if (App.config) {
      App.config.model = sel.value;
      saveConfig(); // v8.13：主输入区切换模型持久化，刷新后不丢
    }
    toast('模型已切换：' + (sel.value || '默认'), 'ok');
  });
}

function renderBuiltInExperts() {
  const box = $('#agents-built-in');
  if (!box) return;
  box.innerHTML = '';
  BUILTIN_EXPERTS.forEach((ex) => {
    const card = el('div', 'agent-card');
    const info = el('div', 'agent-info');
    info.innerHTML = `<div class="agent-name">${esc(ex.name)}</div><div class="agent-meta">${esc(ex.desc)}</div>`;
    card.appendChild(info);
    box.appendChild(card);
  });
}

function renderCustomAgents() {
  const box = $('#agents-custom');
  if (!box) return;
  box.innerHTML = '<div class="empty-card">No Agent Available</div>';
}

function renderSkillsList() {
  const box = $('#skills-list');
  if (!box) return;
  box.innerHTML = '';
  BUILTIN_SKILLS.forEach((s) => {
    const card = el('div', 'skill-card');
    const info = el('div', 'skill-info');
    info.innerHTML = `<div class="skill-name">${esc(s.name)} <span class="tag">From ${esc(s.source)}</span></div><div class="skill-meta">${esc(s.desc)}</div>`;
    card.appendChild(info);
    box.appendChild(card);
  });
}

function loadCommands() {
  try {
    const raw = localStorage.getItem('deverai.v2.commands');
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch (e) { return []; }
}
function saveCommands(list) {
  try { localStorage.setItem('deverai.v2.commands', JSON.stringify(list)); } catch (e) {}
}

function renderCommandsList() {
  const box = $('#commands-list');
  if (!box) return;
  const commands = loadCommands();
  box.innerHTML = '';
  if (!commands.length) {
    box.innerHTML = `
      <div class="empty-card">
        <div>No Command Available</div>
        <div style="font-size:12px;color:var(--text-faint);margin:4px 0 12px;">点击 'New' 创建你的第一个快捷命令。</div>
        <button class="btn" id="btn-new-command">${icon('plus', 14)} New</button>
      </div>`;
  } else {
    commands.forEach((c) => {
      const row = el('div', 'skill-card');
      const info = el('div', 'skill-info');
      info.innerHTML = `<div class="skill-name">${esc(c.name)}</div><div class="skill-meta">${esc(c.prompt)}</div>`;
      const actions = el('div', 'model-actions');
      const run = el('button', 'icon-btn', '发送');
      run.title = '填入输入框';
      run.onclick = () => {
        const inp = $('#chat-input');
        inp.value = c.prompt;
        autoGrow();
        inp.focus();
        closeModal();
      };
      const del = el('button', 'icon-btn', icon('trash', 16));
      del.title = '删除命令';
      del.onclick = () => {
        if (!confirm('删除命令 ' + c.name + '？')) return;
        saveCommands(commands.filter((x) => x.id !== c.id));
        renderCommandsList();
      };
      actions.appendChild(run);
      actions.appendChild(del);
      row.appendChild(info);
      row.appendChild(actions);
      box.appendChild(row);
    });
    const add = el('button', 'btn', icon('plus', 14) + ' New');
    add.onclick = newCommand;
    box.appendChild(add);
  }
  const btn = $('#btn-new-command');
  if (btn) btn.onclick = newCommand;
}

function newCommand() {
  showPrompt('新建快捷命令名称', '', (name) => {
    const n = String(name || '').trim();
    if (!n) return;
    showPrompt('命令内容（发送时直接填入输入框）', '', (prompt) => {
      const p = String(prompt || '').trim();
      if (!p) return;
      const list = loadCommands();
      list.push({ id: 'cmd_' + uid(), name: n.slice(0, 60), prompt: p });
      saveCommands(list);
      toast('已创建命令「' + n + '」', 'ok');
      renderCommandsList();
    });
  });
}

function renderAdvancedSwitches() {
  const box = $('#advanced-switches');
  if (!box) return;
  box.innerHTML = '';
  // v8.13：开关全部走 DOM append（switchEl onclick 是 DOM property，outerHTML 模板化会丢事件）
  const rows = [
    ['低内存模式', '限制 IndexedDB 缓存与历史长度', 'low_memory_mode'],
    ['启用 Embedding API', 'api 级别匹配时允许调用 Embedding 接口', 'ENABLE_EMBEDDING_API'],
  ];
  rows.forEach(([name, desc, key]) => {
    const row = el('div', 'switch-row');
    const info = el('div', 'sw-info');
    info.appendChild(el('b', '', esc(name)));
    info.appendChild(el('span', '', esc(desc)));
    row.appendChild(info);
    const checked = key === 'ENABLE_EMBEDDING_API'
      ? App.config.ENABLE_EMBEDDING_API !== false
      : !!App.config[key];
    row.appendChild(switchEl(checked, (on) => { App.config[key] = on; }));
    box.appendChild(row);
  });
}

function collectSettings() {
  const g = (id) => {
    const elr = document.getElementById(id);
    return elr ? elr.value : undefined;
  };
  const num = (v, dft) => { const f = parseFloat(v); return isNaN(f) ? dft : f; };
  const cfg = {};
  cfg.base_url = g('set-base');
  cfg.model = g('set-model');
  const ve = g('set-visual-expert');
  if (ve != null) cfg.visual_expert_model = ve.trim();
  const key = g('set-key');
  if (key && key.trim()) cfg.api_key = key.trim();
  cfg.temperature = num(g('set-temp'), App.config.temperature ?? 0.3);
  cfg.max_tokens = Math.round(num(g('set-maxtok'), App.config.max_tokens ?? 4096));
  cfg.compress_threshold_tokens = Math.round(num(g('set-compress'), App.config.compress_threshold_tokens ?? 12000));
  cfg.aoe_timeout_s = Math.round(num(g('set-aoe'), App.config.aoe_timeout_s ?? 20));
  cfg.context_keep_recent = Math.round(num(g('set-keep'), App.config.context_keep_recent ?? 6));
  const am = g('set-approval-mode');
  if (am != null && ['all', 'danger', 'copilot', 'free'].includes(am)) cfg.approval_mode = am;
  const th = g('set-theme');
  if (th != null && ['auto', 'light', 'dark'].includes(th)) cfg.theme = th;
  const el = g('set-embedding-level');
  if (el != null && ['char', 'bm25', 'api'].includes(el)) cfg.embedding_level = el;
  const em = g('set-embedding-model');
  if (em != null) cfg.embedding_model = em.trim();
  cfg.vault_threshold = num(g('set-vault-thr'), App.config.vault_threshold ?? 0.45);
  cfg.sync_server_url = g('set-sync-url');
  const token = g('set-sync-token');
  if (token != null) cfg.sync_token = token.trim();
  if (document.getElementById('sleep-goal')) cfg.sleep_goal = g('sleep-goal');
  if (document.getElementById('sleep-action')) cfg.sleep_action = g('sleep-action');
  // v2.0 语音助手 + 完整性校验
  const voiceAssistEl = document.getElementById('set-voice-assist');
  if (voiceAssistEl) cfg.ENABLE_VOICE_ASSISTANT = !!voiceAssistEl.checked;
  const integrityEl = document.getElementById('set-integrity');
  if (integrityEl) cfg.ENABLE_INTEGRITY = !!integrityEl.checked;
  const mascotStyleEl = document.getElementById('set-mascot-style');
  if (mascotStyleEl && ['custom', 'dragon', 'siri', 'blob', 'cat', 'robot'].includes(mascotStyleEl.value)) cfg.mascot_style = mascotStyleEl.value;
  // mascot_custom_img 已通过文件上传事件直接写入 App.config，此处无需重复收集
  return cfg;
}

function saveSettings() {
  const cfg = collectSettings();
  Object.assign(App.config, cfg);
  saveConfig();
  // 桥模式：把后端强校验的三个开关同步到服务端（此前只写 localStorage，后端仍按旧值拦截）
  // v8.14：这三个端点只要求登录态、不依赖工作区授权——纯 FSS 模式下勾选
  // 「肝完睡觉/语音助手」此前不会同步，sleepExecute/voice_pet_host 全部 403 静默失效
  if (App.user) {
    api('/api/bridge/allow_ai_delete', { method: 'POST', body: { allow: !!App.config.ALLOW_AI_DELETE } }).catch(() => {});
    api('/api/bridge/feature_flags', {
      method: 'POST',
      body: {
        ENABLE_VOICE_ASSISTANT: !!App.config.ENABLE_VOICE_ASSISTANT,
        ENABLE_INTEGRITY: App.config.ENABLE_INTEGRITY !== false,
      },
    }).catch(() => {});
    api('/api/bridge/power_authorized', { method: 'POST', body: { authorized: !!App.config.sleep_authorized } }).catch(() => {});
    // v8.14：吉祥物外观键名对齐——服务端白名单只认 builtin_sprite（此前前端只写
    // 本地 mascot_style，服务端永不知道用户选了什么，浮层形象永远不变）
    api('/api/bridge/voice-pet/config', { method: 'POST', body: { builtin_sprite: cfg.mascot_style || 'blob' } }).catch(() => {});
  }
  closeModal();
  updateStatusbar();
  // v8.16.1：多会话开关即时生效（此前标签条显隐要等下一次左栏刷新）
  if (window.Sessions) Sessions.render();
  if (typeof applyTheme === 'function') applyTheme(App.config.theme);
  // 语音助手开关实时生效（浮层需重新初始化）
  if (window.VoicePet && App.config.ENABLE_VOICE_ASSISTANT) window.VoicePet.init();
  const voiceBtn = document.getElementById('btn-voice-assist');
  if (voiceBtn) voiceBtn.classList.toggle('hidden', !cfg.ENABLE_VOICE_ASSISTANT);
  toast('设置已保存', 'ok');
}

async function loadErrLog() {
  try {
    const r = await api('/api/errs');
    const content = r.content || '';
    // v8.17：同时填充 Advanced 页和 Security 页的错误日志框
    const box = $('#err-log-box');
    if (box) box.value = content;
    const box2 = $('#err-log-box-sec');
    if (box2) box2.value = content || '暂无错误记录';
  } catch (e) {}
}

/* ---------- 测试连接 ---------- */
async function testLlm(base, key, model) {
  if (!base || !key || !model) return { ok: false, message: '请填写完整的 Base URL / Key / 模型' };
  let last = '';
  let error = null;
  await sseFetch('/api/llm/chat', {
    base_url: base, api_key: key, model,
    messages: [{ role: 'user', content: 'ping' }], stream: false, temperature: 0,
  }, {
    onData(obj) { if (obj.choices && obj.choices[0]) last = obj.choices[0].message?.content || last; },
    onEvent(evt, data) { if (evt === 'error') error = (data && (data.message || data.body)) || '代理错误'; },
  });
  if (error) return { ok: false, message: error };
  return { ok: true, message: '连接成功 · ' + (last ? String(last).slice(0, 40) : model) };
}

/* ---------- 同步快照 ---------- */
async function buildSnapshot() {
  const c = { ...App.config };
  // 密钥排除穷举（与桌面 build_snapshot 六字段一致，DashScope 密钥同样不外流）
  ['api_key', 'sync_password', 'sync_token', 'drift_api_key', 'search_api_key', 'dashscope_api_key'].forEach((k) => { delete c[k]; });
  const vault = await IDB.getAll('vault');
  return { v: 2, ts: Date.now(), config: c, history: App.history, vault };
}

async function applySnapshot(snap) {
  if (!snap || snap.v !== 2) throw new Error('快照格式不正确');
  if (snap.config) {
    // 本机密钥/部署目标快照不覆盖：api_key/sync_token/sync_password/drift_api_key/search_api_key/dashscope_api_key/sync_server_url
    const localSecrets = ['api_key', 'sync_token', 'sync_password', 'drift_api_key',
                          'search_api_key', 'dashscope_api_key', 'sync_server_url'];
    const saved = {};
    localSecrets.forEach((k) => {
      if (App.config && App.config[k]) saved[k] = App.config[k];
    });
    App.config = { ...CFG_DEFAULT, ...snap.config };
    localSecrets.forEach((k) => {
      if (saved[k]) App.config[k] = saved[k];
    });
    saveConfig();
  }
  if (Array.isArray(snap.history)) {
    App.history = snap.history;
    await saveHistory();
  }
  if (Array.isArray(snap.vault)) {
    await IDB.clear('vault');
    for (const a of snap.vault) {
      if (a && a.id) await IDB.put('vault', a);
    }
  }
}

/* WebCrypto AES-256-GCM 快照加密 */
function _b64(u8) {
  let bin = '';
  for (let i = 0; i < u8.length; i++) bin += String.fromCharCode(u8[i]);
  return btoa(bin);
}
function _fromB64(s) {
  const bin = atob(s);
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return u8;
}
async function _deriveKey(pw, salt) {
  const enc = new TextEncoder();
  const mat = await crypto.subtle.importKey('raw', enc.encode(pw), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt, iterations: 200000, hash: 'SHA-256' },
    mat, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']
  );
}
async function encryptSnapshot(json, pw) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await _deriveKey(pw, salt);
  const data = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(json));
  return JSON.stringify({ v: 2, salt: _b64(salt), iv: _b64(iv), data: _b64(new Uint8Array(data)) });
}
async function decryptSnapshot(str, pw) {
  const obj = JSON.parse(str);
  const salt = _fromB64(obj.salt);
  const iv = _fromB64(obj.iv);
  const key = await _deriveKey(pw, salt);
  const buf = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, _fromB64(obj.data));
  return new TextDecoder().decode(buf);
}

/* v8.13：网页冷备信封 + 跨端快照解析（桌面版 /push 的 urlsafe base64 + zlib 冷备也可拉回） */
async function buildWebCold(snap) {
  const raw = new TextEncoder().encode(JSON.stringify(snap));
  return JSON.stringify({ v: 2, kind: 'web-cold', data: _b64(raw) });
}

function _urlsafeToStd(s) {
  return String(s || '').replace(/-/g, '+').replace(/_/g, '/');
}

async function _inflateDesktopCold(encodedText) {
  const b64 = _urlsafeToStd(String(encodedText || '').replace(/=+$/, ''));
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  let plain;
  if (typeof DecompressionStream === 'function') {
    const ds = new DecompressionStream('deflate');
    const stream = new Blob([bytes]).stream().pipeThrough(ds);
    plain = new Uint8Array(await new Response(stream).arrayBuffer());
  } else {
    throw new Error('当前浏览器不支持 DecompressionStream，无法读取桌面版冷备快照');
  }
  let decoded = new TextDecoder().decode(plain);
  if (decoded.startsWith('obfuscated')) decoded = decoded.split('\0')[1];
  return JSON.parse(decoded);
}

async function parseServerSnapshot(dataText, coldText, pw) {
  // 1) 网页版加密信封（本端推送）
  const data = String(dataText || '').trim();
  if (data.startsWith('{')) {
    try {
      const obj = JSON.parse(data);
      if (obj && Number(obj.v) === 2 && obj.kind === 'web-cold' && typeof obj.data === 'string') {
        const raw = new TextDecoder().decode(_fromB64(obj.data || ''));
        return JSON.parse(raw);
      }
      if (obj && Number(obj.v) === 2 && obj.salt && obj.iv && obj.data) {
        return JSON.parse(await decryptSnapshot(data, pw));
      }
    } catch (e) { /* 继续尝试桌面格式 */ }
  }
  // 2) 桌面版冷备（urlsafe base64 + zlib，sync_server /pull 会带 drift_cold）
  if (coldText) {
    try {
      const snap = await _inflateDesktopCold(coldText);
      if (snap && typeof snap === 'object') {
        // 桌面快照为 v1：转换成网页版可应用的 v2 形状（历史/资产直接可读）
        return {
          v: 2,
          ts: snap.ts || Date.now(),
          config: {},
          history: Array.isArray(snap.history) ? snap.history : [],
          vault: Array.isArray(snap.vault) ? snap.vault.map((a) => ({ ...a, id: a.id || ('a_' + uid()) })) : [],
        };
      }
    } catch (e) { /* 继续尝试主快照 */ }
  }
  // 3) 桌面版无口令主快照（同为 base64 + zlib）
  try {
    const snap = await _inflateDesktopCold(data);
    if (snap && typeof snap === 'object') {
      return {
        v: 2,
        ts: snap.ts || Date.now(),
        config: {},
        history: Array.isArray(snap.history) ? snap.history : [],
        vault: Array.isArray(snap.vault) ? snap.vault.map((a) => ({ ...a, id: a.id || ('a_' + uid()) })) : [],
      };
    }
  } catch (e) { /* 落入统一错误 */ }
  throw new Error('无法解析服务器快照（口令不匹配、桌面加密快照请在桌面版导入，或快照已损坏）');
}

/* ---------- 提示输入弹窗 ---------- */
function showPrompt(title, placeholder, cb) {
  $('#modal-root').classList.remove('hidden');
  $('#modal').innerHTML = `
    <div class="modal-head"><span class="modal-title">${esc(title)}</span><button class="icon-btn" id="modal-close">${icon('close', 12)}</button></div>
    <div class="modal-body">
      <input class="form-input" id="prompt-input" value="${esc(placeholder)}" autofocus />
    </div>
    <div class="modal-foot">
      <button class="btn" id="prompt-cancel">取消</button>
      <button class="btn primary" id="prompt-ok">确定</button>
    </div>`;
  hydrateIcons($('#modal'));
  const input = $('#prompt-input');
  input.focus();
  input.select();
  const finish = (ok) => { closeModal(); if (ok) cb(input.value); };
  $('#prompt-ok').onclick = () => finish(true);
  $('#prompt-cancel').onclick = () => finish(false);
  $('#modal-close').onclick = () => finish(false);
  $('#modal-root').onclick = (e) => { if (e.target === $('#modal-root')) finish(false); };
  input.onkeydown = (e) => { if (e.key === 'Enter') finish(true); if (e.key === 'Escape') finish(false); };
}

/* ---------- 肝完睡觉 全屏倒计时 ---------- */
const Sleep = { timer: null, remaining: 0 };
let _audioCtx = null;

function beep() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    _audioCtx = _audioCtx || new Ctx();
    const o = _audioCtx.createOscillator();
    const g = _audioCtx.createGain();
    o.connect(g); g.connect(_audioCtx.destination);
    o.frequency.value = 880;
    g.gain.setValueAtTime(0.25, _audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, _audioCtx.currentTime + 1);
    o.start(); o.stop(_audioCtx.currentTime + 1);
  } catch (e) {}
}

function startSleepOverlay(goal) {
  if (!$('#sleep-overlay').classList.contains('hidden')) return;
  $('#sleep-overlay').classList.remove('hidden');
  $('#sleep-goal-text').textContent = goal ? '目标：' + goal : '';
  const TOTAL = 300;
  Sleep.remaining = TOTAL;
  renderSleepTime();
  if (Sleep.timer) clearInterval(Sleep.timer);
  let lastMinute = Math.ceil(Sleep.remaining / 60);
  Sleep.timer = setInterval(() => {
    Sleep.remaining -= 1;
    renderSleepTime();
    const m = Math.ceil(Sleep.remaining / 60);
    if (m !== lastMinute) {
      lastMinute = m;
      if (m > 0) { beep(); flash(); }
    }
    if (Sleep.remaining <= 0) {
      clearInterval(Sleep.timer);
      Sleep.timer = null;
      sleepExecute();
    }
  }, 1000);
  $('#sleep-cancel').onclick = cancelSleep;
  $('#sleep-now').onclick = () => {
    if (Sleep.timer) { clearInterval(Sleep.timer); Sleep.timer = null; }
    sleepExecute();
  };
  beep();
}

function renderSleepTime() {
  const m = Math.floor(Sleep.remaining / 60);
  const s = Sleep.remaining % 60;
  $('#sleep-time').textContent = String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}

function flash() {
  const ov = $('#sleep-overlay');
  ov.classList.add('flashing');
  setTimeout(() => ov.classList.remove('flashing'), 1200);
}

function cancelSleep() {
  if (Sleep.timer) { clearInterval(Sleep.timer); Sleep.timer = null; }
  $('#sleep-overlay').classList.add('hidden');
  toast('已取消休眠计划', 'ok');
}

async function sleepExecute() {
  const cfg = App.config || {};
  if (!cfg.sleep_authorized) {
    cancelSleep();
    toast('未授权电源操作，已取消', 'err');
    return;
  }
  try {
    const r = await api('/api/bridge/power', { method: 'POST', body: { action: cfg.sleep_action || 'shutdown' } });
    toast(r.message || '已执行', 'ok');
  } catch (e) {
    toast('电源操作失败: ' + e.message, 'err');
    cancelSleep();
  }
}

/* ============ 账户与项目（官方服务器） ============ */
function openAccount() {
  $('#modal-root').classList.remove('hidden');
  $('#modal').innerHTML = accountHTML();
  hydrateIcons($('#modal'));
  wireAccount();
  closeOnBackdrop();
  loadAccountUsers();
  loadAccountProjects();
}

function accountHTML() {
  return `
  <div class="modal-head"><span class="modal-title">${icon('user', 14)} 账户与项目</span><button class="icon-btn" id="modal-close">${icon('close', 12)}</button></div>
  <div class="modal-body">
    <div class="section-title">当前登录</div>
    <div class="form-hint" id="acc-me">加载中…</div>

    <div class="section-title">已注册账户</div>
    <div id="acc-users">加载中…</div>

    <div class="section-title">项目下载（官方服务器分发，链接 15 分钟时效）</div>
    <div id="acc-projects">加载中…</div>
    <div class="form-hint">下载包为真实源码 ZIP（v8.12 起：排除 data/backups/缓存/Err.log，跳过符号链接与超大文件）。下载需登录鉴权，链接带 HMAC 签名。</div>

    <div class="section-title">数据安全（借鉴大厂实践）</div>
    <div class="form-hint">
      传输加密：全链路 HTTPS/TLS、POP3/SMTP SSL（端口 465/993）<br>
      存储加密：密码 PBKDF2-SHA256（200k 迭代）、快照 AES-256-GCM<br>
      零信任：每次请求验证身份、路径强制 resolve 防越界<br>
      最小权限：AI 只见代号/相对路径、命令审批门、AI 删除默认禁用<br>
      数据最小化：官方服务器不存代码/会话，仅身份与项目元数据<br>
      审计日志：登录/注册/下载操作留痕（data/audit.jsonl）<br>
      限速防护：登录 5 分钟 10 次、验证码 1 分钟 1 次、异常 IP 检测<br>
      密钥轮换：HMAC secret 自动生成、快照口令可更换、下载签名时效 15 分钟
    </div>
  </div>
  <div class="modal-foot">
    <button class="btn" id="btn-acc-close">关闭</button>
  </div>`;
}

function wireAccount() {
  $('#modal-close').onclick = closeModal;
  $('#btn-acc-close').onclick = closeModal;
}

async function loadAccountUsers() {
  const box = $('#acc-users');
  const meBox = $('#acc-me');
  try {
    const me = await api('/api/auth/me');
    if (meBox) meBox.textContent = '用户名：' + String(me.username || '') + (me.email ? '  ·  邮箱：' + String(me.email) : '');
  } catch (e) {
    if (meBox) meBox.textContent = '获取身份失败: ' + e.message;
  }
  try {
    const r = await api('/api/users');
    const users = r.users || [];
    if (!users.length) { box.textContent = '暂无账户'; return; }
    box.innerHTML = '<table class="acc-table"><thead><tr><th>用户名</th><th>邮箱</th><th>邮箱验证</th><th>注册时间</th></tr></thead><tbody>' +
      users.map((u) => `<tr><td>${esc(u.username)}</td><td>${esc(u.email || '-')}</td><td>${u.email_verified ? '是' : '否'}</td><td>${esc(u.created_at || '-')}</td></tr>`).join('') +
      '</tbody></table>';
  } catch (e) {
    box.textContent = '加载失败: ' + e.message;
  }
}

async function loadAccountProjects() {
  const box = $('#acc-projects');
  try {
    const r = await api('/api/projects');
    const items = r.projects || [];
    if (!items.length) { box.textContent = '暂无可下载项目'; return; }
    box.innerHTML = items.map((p) => `
      <div class="proj-item">
        <div class="proj-info">
          <div class="proj-name">${esc(p.name)} <span class="proj-ver">v${esc(p.version)}</span></div>
          <div class="proj-desc">${esc(p.description || '')}</div>
          <div class="proj-meta">${esc(p.size || '')} · 链接 ${esc(p.expires_in)}s 时效</div>
        </div>
        <button class="btn primary" data-dl="${esc(p.download_url)}">${icon('download', 14)} 下载</button>
      </div>`).join('');
    box.querySelectorAll('[data-dl]').forEach((btn) => {
      btn.onclick = () => {
        const url = btn.getAttribute('data-dl');
        window.open(url, '_blank');
        toast('开始下载（浏览器新标签）', 'ok');
      };
    });
  } catch (e) {
    box.textContent = '加载失败: ' + e.message;
  }
}

/* ============ v8.21 Git 分支实验面板（git worktree 列表/切换/新建/移除） ============
   v8.24 更名：与 Design「WorkTree 独立安全备份审核」（checkpoint/session_snap/dep_tree/audit）区分。 */
const WorkTreePanel = {
  async refresh() {
    const box = $('#wt-list');
    if (!box) return;
    box.innerHTML = '<div class="wt-empty">加载中…</div>';
    try {
      const r = await api('/api/bridge/worktree/list');
      this._render(r.items || []);
    } catch (e) {
      const msg = e.message || '加载失败';
      box.innerHTML = '<div class="wt-empty">' + esc(msg) + '<br><span style="font-size:11px">需工作区为 git 仓库且已授权</span></div>';
    }
  },
  _render(items) {
    const box = $('#wt-list');
    if (!box) return;
    box.innerHTML = '';
    if (!items.length) {
      box.innerHTML = '<div class="wt-empty">暂无工作树<br><span style="font-size:11px">点击右上 + 新建工作树</span></div>';
      return;
    }
    items.forEach((wt) => {
      const item = el('div', 'wt-item' + (wt.current ? ' current' : ''));
      item.setAttribute('role', 'listitem');
      const iconWrap = el('span', 'wt-icon');
      iconWrap.innerHTML = icon(wt.current ? 'pin' : 'worktree', 14);
      item.appendChild(iconWrap);
      const main = el('div', 'wt-main');
      const name = el('div', 'wt-name', esc(wt.name));
      main.appendChild(name);
      const meta = el('div', 'wt-meta');
      if (wt.branch) {
        const br = el('span', 'wt-branch', esc('@ ' + wt.branch));
        meta.appendChild(br);
      }
      const dirty = el('span', wt.dirty ? 'wt-dirty' : 'wt-clean', wt.dirty ? '[未提交]' : '[干净]');
      meta.appendChild(dirty);
      if (wt.head) {
        const h = el('span', 'wt-head', esc(wt.head));
        meta.appendChild(h);
      }
      main.appendChild(meta);
      item.appendChild(main);
      const actions = el('div', 'wt-actions');
      if (!wt.current) {
        const sw = el('button', 'icon-btn');
        sw.title = '切换到此工作树';
        sw.setAttribute('aria-label', '切换到 ' + wt.name);
        sw.innerHTML = icon('refresh', 12);
        sw.onclick = () => this._switch(wt.name);
        actions.appendChild(sw);
      }
      if (!wt.current) {
        const rm = el('button', 'icon-btn danger');
        rm.title = '移除工作树';
        rm.setAttribute('aria-label', '移除 ' + wt.name);
        rm.innerHTML = icon('close', 12);
        rm.onclick = () => this._remove(wt.name);
        actions.appendChild(rm);
      }
      item.appendChild(actions);
      box.appendChild(item);
    });
  },
  async _switch(name) {
    if (!confirm('切换工作区到工作树 "' + name + '"？（Git 分支实验）\n当前会话的工作区指向将改变。')) return;
    try {
      const r = await api('/api/bridge/worktree/switch', { method: 'POST', body: { name, danger_ok: true } });
      toast('已切换到 ' + (r.workspace || name), 'ok');
      await FS.refreshRoot();
      this.refresh();
      if (typeof updateStatusbar === 'function') updateStatusbar();
      if (typeof Tree !== 'undefined') Tree.refresh().catch(() => {});
    } catch (e) {
      toast('切换失败: ' + e.message, 'err');
    }
  },
  async _remove(name) {
    if (!confirm('移除工作树 "' + name + '"？（Git 分支实验）\n该工作树目录将被 git worktree remove 删除。')) return;
    try {
      await api('/api/bridge/worktree/remove', { method: 'POST', body: { name, danger_ok: true } });
      toast('已移除 ' + name, 'ok');
      this.refresh();
    } catch (e) {
      toast('移除失败: ' + e.message, 'err');
    }
  },
  async _create() {
    const name = prompt('新工作树名称（字母数字._-，将创建于 .worktrees/<name>）：');
    if (!name) return;
    const branch = prompt('新分支名（留空则 detached HEAD）：') || '';
    try {
      const r = await api('/api/bridge/worktree/create', {
        method: 'POST', body: { name: name.trim(), branch: branch.trim(), danger_ok: true }
      });
      toast('已创建工作树 ' + r.name + (r.branch ? ' @ ' + r.branch : ''), 'ok');
      this.refresh();
    } catch (e) {
      toast('创建失败: ' + e.message, 'err');
    }
  },
  wire() {
    const refresh = $('#btn-wt-refresh');
    if (refresh) refresh.onclick = () => this.refresh();
    const create = $('#btn-wt-new');
    if (create) create.onclick = () => this._create();
  },
};
