/* ============ DeverAI v2 core: 状态 / 工具 / API(fetch) / SSE / IndexedDB ============
 * 架构：Agent 全部在浏览器端运行。
 * 服务端只做三件事：下发 UI、验证身份(HttpOnly Cookie)、转发 LLM 与本地命令桥。
 * 模型配置(API Key)只存在于浏览器本地存储，随每次请求经同源代理转发，服务端不落盘。
 */
const App = {
  user: null,            // 当前登录用户名
  config: null,          // 浏览器端配置(localStorage)
  serverInfo: null,      // {name, version, allow_register, has_users, bridge_authorized}
  history: [],           // 会话历史 [{role, content}]
  busy: false,           // Agent 是否正在运行
  currentFile: null,     // 编辑器当前打开文件
  vaultCount: 0,
  abortCtrl: null,       // 当前运行的 AbortController（停止按钮用）
  onAgentEvent: null,    // set by agent.js —— 所有 Agent 事件回调
  // v8.13：多任务会话（左栏 Tasks/Chats 不再是死列表）
  taskId: 'default',
  tasks: [],
};

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
const uid = () => Math.random().toString(36).slice(2, 10);

/* ---------- 路径工具 ---------- */
function basename(p) {
  return String(p || '').replace(/\\/g, '/').split('/').filter(Boolean).pop() || '';
}
function dirname(p) {
  const parts = String(p || '').replace(/\\/g, '/').split('/').filter(Boolean);
  parts.pop();
  return parts.join('/');
}
function normPath(p) {
  return String(p || '').replace(/\\/g, '/').replace(/^\/+/, '');
}
function joinRel(a, b) {
  const p = normPath(a) + '/' + normPath(b);
  return p.replace(/\/+/g, '/').replace(/^\/+/, '');
}
function extOf(p) {
  const n = basename(p);
  const i = n.lastIndexOf('.');
  return i > 0 ? n.slice(i + 1).toLowerCase() : '';
}
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
function fmtBytes(n) {
  if (n == null) return '';
  if (n < 1024) return n + 'B';
  if (n < 1048576) return (n / 1024).toFixed(1) + 'KB';
  return (n / 1048576).toFixed(1) + 'MB';
}

/* ---------- Markdown 渲染 ---------- */
function renderMd(text) {
  if (!text) return '';
  try {
    let html = window.marked ? window.marked.parse(String(text)) : esc(String(text)).replace(/\n/g, '<br>');
    if (window.DOMPurify) html = window.DOMPurify.sanitize(html);
    else html = esc(String(text)).replace(/\n/g, '<br>');  // v8.14：DOMPurify 不可用时降级纯文本，防 XSS
    return html;
  } catch (e) {
    return esc(String(text)).replace(/\n/g, '<br>');
  }
}
function highlightBlock(el) {
  if (!el || !window.hljs) return;
  $$('pre code', el).forEach((b) => { try { hljs.highlightElement(b); } catch (e) {} });
}

/* ---------- HTTP API（同源，带 Cookie） ---------- */
async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin' };
  if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  if (opts.signal) init.signal = opts.signal;
  const resp = await fetch(path, init);
  let data = null;
  try { data = await resp.json(); } catch (e) { /* 非 JSON */ }
  if (!resp.ok) {
    const msg = (data && (data.detail || data.message)) || ('HTTP ' + resp.status);
    const err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data || {};
}

function toast(msg, type = 'info', ms = 2800) {
  const el = document.createElement('div');
  el.className = 'toast' + (type === 'ok' ? ' ok' : type === 'err' ? ' err' : '');
  el.textContent = msg;
  $('#toast-root').appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .3s';
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 320);
  }, ms);
}

/* ============ SSE 解析：POST 流式请求 ============
 * 支持事件行 "event: xxx" 与数据行 "data: ..."。数据行可能是 JSON。
 * 回调 onData(obj), onEvent(evtName, obj), onDone(), onError(err)。
 */
async function sseFetch(path, body, handlers, signal) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) {
    let detail = '';
    try { detail = JSON.stringify(await resp.json()); } catch (e) {}
    const err = new Error('HTTP ' + resp.status + (detail ? ' ' + detail : ''));
    err.status = resp.status;  // v8.13：调用方可按 403 做二次确认
    throw err;
  }
  if (!resp.body) throw new Error('响应无流');
  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buf = '';
  let curEvent = 'message';

  const dispatch = (line) => {
    const t = line.trim();
    // v8.12：SSE 规范——空行是事件分帧边界，同时复位事件类型（此前 curEvent 跨帧残留）
    if (!t) { curEvent = 'message'; return; }
    if (t.startsWith('event:')) { curEvent = t.slice(6).trim(); return; }
    if (t.startsWith('data:')) {
      const raw = t.slice(5).trim();
      let obj = null;
      try { obj = JSON.parse(raw); } catch (e) { obj = { raw }; }
      if (curEvent === 'message') { if (handlers.onData) handlers.onData(obj); }
      else if (handlers.onEvent) handlers.onEvent(curEvent, obj);
      return;
    }
    // 注释行(如 : keep-alive)或未知行，忽略
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n')) !== -1) {
        const line = buf.slice(0, idx);
        buf = buf.slice(idx + 1);
        dispatch(line);
      }
    }
    if (buf.trim()) dispatch(buf);
    if (handlers.onDone) handlers.onDone();
  } catch (e) {
    if (e && e.name === 'AbortError') {
      if (handlers.onAbort) handlers.onAbort();
    } else if (handlers.onError) {
      handlers.onError(e);
    } else {
      throw e;
    }
  } finally {
    // 内存安全：无论成功/异常/中止，都显式释放 reader 防止流泄漏
    try { reader.cancel(); } catch (e) {}
  }
}

/* ============ IndexedDB 轻封装 ============ */
const IDB = {
  _db: null,
  _open() {
    return new Promise((resolve, reject) => {
      if (this._db) return resolve(this._db);
      const req = indexedDB.open('deverai', 2);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains('vault')) {
          const st = db.createObjectStore('vault', { keyPath: 'id' });
          st.createIndex('title', 'title', { unique: false });
        }
        if (!db.objectStoreNames.contains('sessions')) {
          db.createObjectStore('sessions', { keyPath: 'id' });
        }
        if (!db.objectStoreNames.contains('fs-root')) {
          db.createObjectStore('fs-root', { keyPath: 'id' });
        }
      };
      req.onsuccess = () => { this._db = req.result; resolve(this._db); };
      req.onerror = () => reject(req.error);
    });
  },
  async put(store, obj) {
    const db = await this._open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, 'readwrite');
      tx.objectStore(store).put(obj);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  },
  async getAll(store) {
    const db = await this._open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, 'readonly');
      const req = tx.objectStore(store).getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  },
  async delete(store, key) {
    const db = await this._open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, 'readwrite');
      tx.objectStore(store).delete(key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  },
  async clear(store) {
    const db = await this._open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, 'readwrite');
      tx.objectStore(store).clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  },
};

/* ---------- 浏览器端配置(localStorage) ---------- */
const CFG_KEY = 'deverai.v2.cfg';
const CFG_DEFAULT = {
  base_url: 'https://api.openai.com/v1',
  api_key: '',
  model: '',
  temperature: 0.3,
  max_tokens: 4096,
  // v8.6 外观：auto / light / dark（参考 DeepSeek Harness 双主题）
  theme: 'auto',
  // 模块开关
  ENABLE_VAULT: true,
  ENABLE_AOE: true,
  ENABLE_SUBAGENT: true,
  ENABLE_SYNC: true,
  ENABLE_MODES: true,
  ENABLE_ERR_MIRROR: true,
  ENABLE_APPROVAL: true,
  ALLOW_AI_DELETE: false,
  // v8.5 批次2：审批四模式（all/danger/copilot/free）+ 三级匹配（char/bm25/api）
  approval_mode: 'danger',
  embedding_level: 'char',
  ENABLE_EMBEDDING_API: true,
  embedding_model: '',
  low_memory_mode: false,
  // v8.3 建议系统（TRAE CUE 式，消息发送完成时展示精选建议）
  ENABLE_SUGGEST: true,
  // v8.4 Python 应用截图 / UI 自截图确认
  ENABLE_APP_SHOT: true,
  ENABLE_UI_REVIEW: true,
  visual_expert_model: '',
  // v8.5 批次1：可靠性（文件级快照/任务级快照/依赖树/上下文守门）
  ENABLE_CHECKPOINT: true,
  ENABLE_SESSION_SNAP: true,
  ENABLE_DEP_TREE: true,
  ENABLE_CTX_EXPERT: true,
  // v8.5.7 补齐：联网搜索 / 浏览器控制 / 暂存便签
  ENABLE_WEB_SEARCH: true,
  ENABLE_BROWSER: true,
  ENABLE_NOTEPAD: true,
  // v8.9 五大核心能力深度优化
  ENABLE_TOOLSMITH: true,
  ENABLE_TOOL_DOCTOR: true,
  ENABLE_AUTO_DRIFT: true,
  auto_drift_interval_min: 30,
  auto_drift_on_exit: true,
  ENABLE_AUDIT_LOG: true,
  ENABLE_FILE_PARTITION: true,
  ENABLE_BROWSER_CTL: true,
  ENABLE_BROWSER_DEVTOOLS: true,
  ENABLE_EXE_JOURNAL: true,
  // v8.10 工作轨迹高级能力（手动标记 / 区间查询 / 操作筛选 / 详情预览）
  ENABLE_TRACE_ADVANCED: true,
  // v2.0 语音助手「小龙」（关闭即隐藏入口、不注册 RPC）
  ENABLE_VOICE_ASSISTANT: false,
  // v1.1.0 DeveraiIntegrityService 完整性校验（HKDF + HMAC-SHA256）
  ENABLE_INTEGRITY: true,
  // v1.0.0 吉祥物外观：custom（默认 SVG 宠物） / dragon / siri
  mascot_style: 'custom',
  mascot_custom_img: 'data:image/svg+xml;base64,77u/PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNzAgMjcwIj4KICAgIDxnPgogICAgICAgIDxnPgogICAgICAgICAgICA8ZyB0cmFuc2Zvcm09InRyYW5zbGF0ZSgwIDIwKSB0cmFuc2xhdGUoMTMwIDEyMCkiPgogICAgICAgICAgICAgICAgPGNpcmNsZSByPSI5MCIgZmlsbD0iI2VkZjBmMiI+CiAgICAgICAgICAgICAgICAgICAgPGFuaW1hdGVUcmFuc2Zvcm0gYXR0cmlidXRlTmFtZT0idHJhbnNmb3JtIiBiZWdpbj0iM3M7IGxvb3AuZW5kICsgM3MiIGNhbGNNb2RlPSJsaW5lYXIiIGR1cj0iMC4xNXMiCiAgICAgICAgICAgICAgICAgICAgICAgIHR5cGU9InNjYWxlIiB2YWx1ZXM9IjEgMTsgMSAwOyAxIDEiIC8+CiAgICAgICAgICAgICAgICA8L2NpcmNsZT4KICAgICAgICAgICAgPC9nPgogICAgICAgICAgICA8cGF0aCBmaWxsPSIjMjg1RDhGIgogICAgICAgICAgICAgICAgZD0iTTIxMS42IDUxYzYuOC03LjggMTUtMTEgMjQuMi03LjUgOC4zIDMgMTMgOS44IDEyIDE5IDE3IDEyLjggMTguMyAxOSA3LjYgMzguNSA2IDYgMTAuNyAxMi44IDkuOCAyMi4zLS4yIDIuMyAxLjcgNC44IDIuNiA3LjIgNS4yIDE1LjQtNC42IDI4LjItMjAuNiAyN2wtMTYuMy0xLjdjNSA1LjQgOSA5LjIgMTIuNSAxMy4zIDguNCA5LjYgMTAgMTcuNCA1LjQgMjUuNS00LjcgOC4yLTEyLjUgMTEuMi0yNC4yIDkuNGwtNi0xLjVjMCAyLjguMiA1LjIuNSA3LjYgMS4yIDkuMiAwIDE3LjQtOC42IDIyLjYtNy43IDQuNy0xOS41IDMuMy0yNy0zLjItMy42LTMtNi42LTctMTAtMTAuNi01LjUgMi40LTUgNy43LTYgMTIuMi0zIDEzLTcuMiAxNy42LTE3LjIgMTktOS41IDEtMTUtMi44LTIwLjgtMTQuNy0uMy0uNi0xLTEtMi0yLTYuNSA3LjYtMTMuMiAxNS0yNC4yIDE1LTkuNCAwLTE1LjItNi4zLTE4LjQtMjBsLTkuNy01LjRjLTEzLjQgOC41LTIyLjIgMTAtMzQtNi4yLTktMS0xOC0yLjItMjEuNS0xMi42LTQtMTEuMyAxLjYtMTkuMyAxMi40LTI2LjhsLTEyLjUgMUM2IDE3OC41LTMuMyAxNjYgMSAxNTNjMi4yLTYuNyA1LjMtMTMgNy41LTE5LjcgNC0xMiA1LTI0LjItLjgtMzYuMy0xLjctMy41LTMuNC04LjctMi0xMS42IDMuNy03LjggOS4yLTE0LjcgMTMuNi0yMiA1LTguNiAxMi42LTExIDIwLjItOS42IDcuMi0xMCAxMy0xOS4zIDIwLjMtMjcuNCAzLjQtMy43IDkuNS01IDE2LjQtOC4yQzg4LjcgMyA5OC44IDMuNyAxMTQgMjMuNGM4LjUtOS44IDEwLjItMTAuNCAyNi44LTkuM0MxNDUuNCAzLjYgMTU0LTEuNyAxNjUuNS44YzkuNiAyIDE0IDEwLjIgMTUuMiAyMC43IDkuNy00LjYgMTkuNC03LjYgMjcuNSAxLjUgNy4zIDggNS4zIDE3LjcgMy40IDI4ek0xODYgOTAuOGMtMTcuNS0uMi0zMSAxMi43LTMwLjggMjkuNiAwIDE3LjMgMTMuMyAzMSAzMCAzMSAxNi42IDAgMjkuNi0xMy41IDMwLTMwLjguNC0xNC0xMy0zMS43LTI5LjMtMjkuOHpNODQgODguNmMtMTcuNy0uMi0zMC43IDEyLTMxIDI5LjMgMCAxNy4yIDEzIDMxIDI5LjYgMzEuMiAxNi43LjIgMzAuMy0xMy40IDMwLjQtMzAuNC4yLTE3LTEyLjQtMzAtMjktMzAuMno=',
  // v2.0 浮层形象：blob（水滴）/ cat（猫咪）/ robot（机器人）/ axolotl（美西螈）
  builtin_sprite: 'blob',
  // 阈值
  compress_threshold_tokens: 12000,
  aoe_timeout_s: 20,
  vault_threshold: 0.45,
  context_keep_recent: 6,
  // 三大模式
  traffic_mode: false,
  sleep_enabled: false,
  sleep_goal: '',
  sleep_action: 'shutdown',
  sleep_authorized: false,
  token_mode: false,
  // v8.13：Agent 形态（chat=只读工具 / builder=完整构建；experts 网页版未开放）
  agent_mode: 'builder',
};

function loadConfig() {
  try {
    const raw = localStorage.getItem(CFG_KEY);
    const saved = raw ? JSON.parse(raw) : {};
    App.config = { ...CFG_DEFAULT, ...saved };
  } catch (e) {
    App.config = { ...CFG_DEFAULT };
  }
  // 安全：API Key 永不再渲染回设置框，读取走独立方法
  return App.config;
}

/* ---------- v8.5 批次2：字符 bigram 相似度（对齐 desktop/matcher.char_similarity） ---------- */
function reWs(text) {
  return String(text == null ? '' : text).toLowerCase().replace(/\s+/g, '');
}
function bigrams(text) {
  const s = reWs(text);
  const out = {};
  for (let i = 0; i < s.length - 1; i++) {
    const g = s[i] + s[i + 1];
    out[g] = (out[g] || 0) + 1;
  }
  return out;
}
function charSimilarity(doc, query) {
  const d = bigrams(doc), q = bigrams(query);
  const keys = new Set([...Object.keys(d), ...Object.keys(q)]);
  let dot = 0, nd = 0, nq = 0;
  for (const k of keys) {
    const a = d[k] || 0, b = q[k] || 0;
    dot += a * b; nd += a * a; nq += b * b;
  }
  if (!nd || !nq) return 0;
  const cos = dot / (Math.sqrt(nd) * Math.sqrt(nq));
  // 查询包含度：query 的 bigram 命中比例（权重 0.3）
  let qcount = 0, hit = 0;
  for (const k of Object.keys(q)) {
    qcount += q[k];
    if (d[k]) hit += q[k];
  }
  const contain = qcount ? hit / qcount : 0;
  return cos * 0.7 + contain * 0.3;
}
function saveConfig() {
  try {
    localStorage.setItem(CFG_KEY, JSON.stringify(App.config));
  } catch (e) { /* 隐私模式等场景静默 */ }
}
function getApiKey() {
  return App.config && App.config.api_key ? App.config.api_key : '';
}
function setApiKey(key) {
  if (!App.config) loadConfig();
  App.config.api_key = key || '';
  saveConfig();
}
function clearApiKey() {
  setApiKey('');
}

/* ---------- DOM 工具 ---------- */
function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
}
function switchEl(checked, onChange) {
  const s = el('div', 'switch' + (checked ? ' on' : ''));
  s.onclick = () => { s.classList.toggle('on'); if (onChange) onChange(s.classList.contains('on')); };
  return s;
}
function fileIcon(rel) {
  const map = {
    py: 'atom', js: 'pen', ts: 'pen', jsx: 'pen', tsx: 'pen', html: 'pen', css: 'pen', json: 'memo',
    md: 'memo', txt: 'memo', yaml: 'cog', yml: 'cog', toml: 'cog', ini: 'cog', sh: 'terminal', bat: 'terminal',
    ps1: 'terminal', sql: 'memo', csv: 'chart', gitignore: 'lock', lock: 'lock', pyc: 'cog',
  };
  return icon(map[extOf(rel)] || 'memo', 13);
}

function setConnDot(ok) {
  const d = $('#conn-dot');
  if (d) d.classList.toggle('online', !!ok);
}
function setChatSub(text) { const s = $('#chat-sub'); if (s) s.textContent = text; }
function scrollChat() {
  const box = $('#chat-messages');
  if (box) box.scrollTop = box.scrollHeight;
}

/* ---------- 会话历史持久化(IndexedDB) ---------- */
async function saveHistory() {
  try {
    await IDB.put('sessions', { id: App.taskId || 'default', messages: App.history, updated_at: Date.now() });
  } catch (e) {}
}
async function loadHistory() {
  try {
    const db = await IDB._open();
    return new Promise((resolve) => {
      const tx = db.transaction('sessions', 'readonly');
      const req = tx.objectStore('sessions').get(App.taskId || 'default');
      req.onsuccess = () => resolve(req.result ? req.result.messages || [] : []);
      req.onerror = () => resolve([]);
    });
  } catch (e) {
    return [];
  }
}

/* ---------- v8.13：多任务会话（左栏 Tasks/Chats 真实接线） ---------- */
const TASK_KEY = 'deverai.v2.tasks';
function _nowText() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function loadTasks() {
  try {
    const raw = localStorage.getItem(TASK_KEY);
    const list = raw ? JSON.parse(raw) : null;
    if (Array.isArray(list) && list.length && list.every((t) => t && t.id && t.name)) {
      App.tasks = list;
    }
  } catch (e) { App.tasks = []; }
  if (!App.tasks.length) {
    App.tasks = [{ id: 'default', name: '当前会话', created_at: _nowText(), updated_at: _nowText() }];
    saveTasks();
  }
  try {
    const active = localStorage.getItem('deverai.v2.active_task') || '';
    if (App.tasks.some((t) => t.id === active)) App.taskId = active;
    else App.taskId = App.tasks[0].id;
  } catch (e) { App.taskId = App.tasks[0] && App.tasks[0].id || 'default'; }
  return App.tasks;
}
function saveTasks() {
  try { localStorage.setItem(TASK_KEY, JSON.stringify(App.tasks)); } catch (e) {}
  try { localStorage.setItem('deverai.v2.active_task', App.taskId || 'default'); } catch (e) {}
}
function touchTask(taskId) {
  const t = App.tasks.find((x) => x.id === taskId);
  if (t) { t.updated_at = _nowText(); saveTasks(); }
}
async function createTask(name) {
  const label = String(name || '').trim() || ('任务 ' + (App.tasks.length + 1));
  const oldId = App.taskId || 'default';
  // 新建任务前先把当前会话写回旧任务，避免切换后丢失
  try {
    await IDB.put('sessions', { id: oldId, messages: App.history || [], updated_at: Date.now() });
  } catch (e) {}
  const task = { id: 'task_' + uid() + '_' + Date.now(), name: label.slice(0, 60), created_at: _nowText(), updated_at: _nowText() };
  App.tasks.push(task);
  App.taskId = task.id;
  App.history = [];
  try {
    await IDB.put('sessions', { id: task.id, messages: [], updated_at: Date.now() });
  } catch (e) {}
  saveTasks();
  return task;
}
async function switchTask(taskId) {
  if (!taskId || taskId === App.taskId) return false;
  if (!App.tasks.some((t) => t.id === taskId)) return false;
  // 先按当前任务 id 保存旧会话，再切换并加载目标会话
  try {
    await IDB.put('sessions', { id: App.taskId || 'default', messages: App.history, updated_at: Date.now() });
  } catch (e) {}
  App.taskId = taskId;
  App.history = await loadHistory();
  saveTasks();
  return true;
}
