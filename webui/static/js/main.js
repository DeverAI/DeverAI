/* ============ main.js 主入口：鉴权流 / 事件分发 / UI 接线 ============ */
document.addEventListener('DOMContentLoaded', () => {
  boot();
});

/* ---------- v8.6 主题系统（auto / light / cream / dark，黑米白三色 + 自动） ---------- */
const THEME_ORDER = ['auto', 'light', 'cream', 'dark'];
const THEME_ICON = { auto: 'contrast', light: 'sun', cream: 'coffee', dark: 'moon' };
const THEME_LABEL = { auto: '自动（跟随系统）', light: '浅色（白）', cream: '暖米', dark: '深色（黑）' };

function applyTheme(pref) {
  const t = THEME_ORDER.includes(pref) ? pref : 'auto';
  const mode = t === 'auto'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : t;
  document.documentElement.setAttribute('data-theme', mode);
  const hl = document.getElementById('hljs-dark');
  if (hl) hl.disabled = (mode !== 'dark');
  const btn = document.getElementById('btn-theme');
  if (btn) {
    const name = THEME_ICON[t] || 'contrast';
    const size = 14;
    btn.innerHTML = (typeof icon === 'function') ? icon(name, size) : '';
    btn.title = '主题：' + THEME_LABEL[t] + '（点击切换）';
  }
  App.theme = t;
  return mode;
}

function cycleTheme() {
  const cur = App.theme || 'auto';
  const next = THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length];
  App.config.theme = next;
  saveConfig();
  const mode = applyTheme(next);
  if (typeof TraceView !== 'undefined') TraceView._renderTimeline();
  const modeLabel = mode === 'dark' ? '深色' : (mode === 'cream' ? '暖米' : '浅色');
  toast('主题：' + THEME_LABEL[next] + (next === 'auto' ? '（当前 ' + modeLabel + '）' : ''), 'ok');
}

async function boot() {
  loadConfig();
  applyTheme(App.config.theme);
  // 跟随系统：auto 模式下系统切换时实时联动
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if ((App.config.theme || 'auto') === 'auto') applyTheme('auto');
  });
  try {
    const info = await api('/api/server/info');
    App.serverInfo = info;
  } catch (e) {
    toast('无法连接服务端: ' + e.message, 'err', 5000);
  }
  updateAuthUI();

  // 挂 Agent 事件分发
  App.onAgentEvent = onAgentEvent;

  await refreshBridgeState();
  const restored = await restoreFsRoot();
  if (!restored && FS.bridge.authorized && !FS.mode) {
    FS.mode = 'bridge';
    FS.rootName = basename(FS.bridge.workspace) || '工作区';
  }

  // 检查登录态
  let logged = false;
  try {
    const me = await api('/api/auth/me');
    App.user = me.username;
    logged = true;
  } catch (e) {
    logged = false;
  }

  if (logged) {
    enterApp();
  } else {
    showAuth();
  }
}

/* ---------- 鉴权视图 ---------- */
function updateAuthUI() {
  const info = App.serverInfo;
  if (!info) return;
  const tabs = $('#auth-tabs');
  if (tabs) {
    const allow = info.allow_register;
    const regBtn = tabs.querySelector('[data-mode="register"]');
    if (regBtn) regBtn.style.display = allow ? '' : 'none';
    const emailBtn = tabs.querySelector('[data-mode="email"]');
    if (emailBtn) emailBtn.style.display = (allow && info.email_enabled) ? '' : 'none';
  }
}

function showAuth() {
  $('#auth-view').classList.remove('hidden');
  $('#app').classList.add('hidden');
  wireAuth();
}

function wireAuth() {
  if (Auth._wired) return;
  Auth._wired = true;
  const tabs = $('#auth-tabs');
  const submit = $('#auth-submit');
  const userIn = $('#auth-user');
  const passIn = $('#auth-pass');
  const errBox = $('#auth-err');
  let mode = 'login';

  // 切换表单：login/register 走用户名表单；email 走邮箱表单
  const switchForm = (m) => {
    mode = m;
    tabs.querySelectorAll('.auth-tab').forEach((x) => x.classList.remove('active'));
    const active = tabs.querySelector(`[data-mode="${m}"]`);
    if (active) active.classList.add('active');
    const isEmail = m === 'email';
    $('#auth-form-user').classList.toggle('hidden', isEmail);
    $('#auth-form-email').classList.toggle('hidden', !isEmail);
    if (!isEmail) submit.textContent = m === 'login' ? '进入 DeverAI' : '注册并进入';
    errBox.textContent = '';
    const emailErr = $('#auth-email-err'); if (emailErr) emailErr.textContent = '';
    // v6.2 P3-1：离开邮箱表单时清理倒计时定时器
    if (!isEmail && Auth._countdownTimer) {
      clearInterval(Auth._countdownTimer);
      Auth._countdownTimer = null;
      const sb = $('#auth-send-code');
      if (sb) { sb.disabled = false; sb.textContent = '发送验证码'; }
    }
  };

  const doAuth = async () => {
    const username = userIn.value.trim();
    const password = passIn.value;
    if (!username || !password) { errBox.textContent = '请输入用户名和密码'; return; }
    submit.disabled = true;
    errBox.textContent = '';
    try {
      const r = await api('/api/auth/' + (mode === 'login' ? 'login' : 'register'), { method: 'POST', body: { username, password } });
      App.user = r.username;
      // v8.11：保存会话令牌（供 API 控制台「复制为 curl」用 Authorization 复现请求）
      if (r && r.token) storeSessionToken(r.token);
      enterApp();
    } catch (e) {
      errBox.textContent = e.message;
      submit.disabled = false;
    }
  };

  tabs.querySelectorAll('.auth-tab').forEach((t) => {
    t.onclick = () => switchForm(t.dataset.mode);
  });

  submit.onclick = doAuth;
  userIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') doAuth(); });
  passIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') doAuth(); });

  // ---- 邮箱注册 ----
  wireEmailRegister();
}

/* ---------- 邮箱注册（验证码流程） ---------- */
function wireEmailRegister() {
  const sendBtn = $('#auth-send-code');
  const emailIn = $('#auth-email');
  const codeIn = $('#auth-code');
  const passIn2 = $('#auth-email-pass');
  const nameIn = $('#auth-email-name');
  const regBtn = $('#auth-email-submit');
  const errBox = $('#auth-email-err');
  const hint = $('#auth-email-hint');

  const startCountdown = (sec) => {
    let left = sec;
    sendBtn.disabled = true;
    sendBtn.textContent = left + 's';
    if (Auth._countdownTimer) clearInterval(Auth._countdownTimer);
    Auth._countdownTimer = setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(Auth._countdownTimer);
        Auth._countdownTimer = null;
        sendBtn.disabled = false;
        sendBtn.textContent = '发送验证码';
      } else {
        sendBtn.textContent = left + 's';
      }
    }, 1000);
  };

  sendBtn.onclick = async () => {
    const email = emailIn.value.trim();
    if (!email) { errBox.textContent = '请输入邮箱地址'; return; }
    sendBtn.disabled = true;
    errBox.textContent = '';
    try {
      const r = await api('/api/auth/send_code', { method: 'POST', body: { email } });
      if (hint) hint.textContent = '验证码已发送至 ' + email + '，10 分钟内有效。';
      startCountdown(60);
      toast('验证码已发送', 'ok');
    } catch (e) {
      errBox.textContent = e.message;
      sendBtn.disabled = false;
      sendBtn.textContent = '发送验证码';
    }
  };

  const doEmailRegister = async () => {
    const email = emailIn.value.trim();
    const code = codeIn.value.trim();
    const password = passIn2.value;
    const username = nameIn.value.trim();
    if (!email || !code || !password) { errBox.textContent = '邮箱、验证码、密码均为必填'; return; }
    regBtn.disabled = true;
    errBox.textContent = '';
    try {
      const r = await api('/api/auth/register_email', {
        method: 'POST', body: { email, code, password, username },
      });
      // v6.2 P3-1：注册成功清理倒计时
      if (Auth._countdownTimer) { clearInterval(Auth._countdownTimer); Auth._countdownTimer = null; }
      App.user = r.username;
      if (r && r.token) storeSessionToken(r.token);
      enterApp();
    } catch (e) {
      errBox.textContent = e.message;
      regBtn.disabled = false;
    }
  };

  regBtn.onclick = doEmailRegister;
  codeIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') doEmailRegister(); });
  passIn2.addEventListener('keydown', (e) => { if (e.key === 'Enter') doEmailRegister(); });
}

const Auth = { _wired: false, _countdownTimer: null };

/* v8.11：会话令牌本地存取（API 控制台 curl 复现请求用；令牌为 HMAC 签名、带过期时间） */
function storeSessionToken(token) {
  try { if (token) localStorage.setItem('deverai_token', String(token)); } catch (e) { /* 隐私模式等场景忽略 */ }
}
function clearSessionToken() {
  try { localStorage.removeItem('deverai_token'); } catch (e) {}
}

async function enterApp() {
  $('#auth-view').classList.add('hidden');
  $('#app').classList.remove('hidden');
  setConnDot(true);

  const uname = $('#ln-username');
  if (uname) uname.textContent = App.user || '未登录';

  // v8.13：加载多任务会话列表（左栏 Tasks/Chats 真实可点）
  loadTasks();
  renderTaskLists();
  App.history = await loadHistory();
  restoreChat();
  updateStatusbar();

  // v8.13：主输入区模型选择器启动即从注册表填充（此前只在打开设置时填充）
  try { await loadModelRegistry(); } catch (e) { /* 未登录/注册表不可用不阻塞 */ }
  wireModelSelect();

  if (typeof TraceView !== 'undefined') TraceView.init();
  if (typeof TraceView !== 'undefined') TraceView.setHistory(App.history);
  if (typeof DevTools !== 'undefined') DevTools.init();

  initEditor();
  try {
    await initTree();
  } catch (e) {
    toast('文件树初始化失败: ' + e.message, 'err');
  }
  VaultPanel.refresh();
  wireUI();
}

async function logout() {
  try {
    await api('/api/auth/logout', { method: 'POST' });
  } catch (e) {}
  App.user = null;
  Auth._wired = false;
  clearSessionToken();
  location.reload();
}

/* ---------- UI 接线（v8.5.3 新布局） ---------- */
function wireUI() {
  hydrateIcons();

  // 右侧面板标签切换
  $$('.rh-tab[data-tab]').forEach((tab) => {
    tab.onclick = () => {
      const t = tab.dataset.tab;
      $$('.rh-tab').forEach((x) => x.classList.toggle('active', x === tab));
      $$('.right-tab').forEach((x) => {
        const selected = x.id === 'tab-' + t;
        x.classList.toggle('active', selected);
        x.classList.toggle('hidden', !selected);
      });
      if (t === 'files') Tree.refresh().catch(() => {});
      if (t === 'worktree' && typeof WorkTreePanel !== 'undefined') WorkTreePanel.refresh();
    };
  });
  // v8.21/v8.24：Git 分支实验面板（git worktree）按钮接线
  if (typeof WorkTreePanel !== 'undefined') WorkTreePanel.wire();

  // 左侧导航：Tasks/Chats 切换会话；Trace/API 控制台切换中央视图；快捷入口真实接线
  $$('.ln-item').forEach((item) => {
    // v8.20：键盘可达性 —— 静态项 HTML 已带 role/tabindex，这里挂 keydown
    if (item.getAttribute('tabindex') !== '0') item.setAttribute('tabindex', '0');
    if (!item.getAttribute('role')) item.setAttribute('role', 'button');
    item.onkeydown = (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); item.click(); }
    };
    item.onclick = () => {
      $$('.ln-item').forEach((x) => x.classList.remove('active'));
      item.classList.add('active');
      // v8.13：恢复 Tasks/Chats 里当前会话的高亮（导航区互斥不吞任务高亮）
      renderTaskLists();
      if (item.id === 'nav-trace') {
        showTraceView(true);
        showDevtoolsView(false);
      } else if (item.id === 'nav-devtools') {
        showTraceView(false);
        showDevtoolsView(true);
      } else if (item.id === 'nav-assets') {
        showTraceView(false);
        showDevtoolsView(false);
        $$('.rh-tab[data-tab]').forEach((x) => x.classList.toggle('active', x.dataset.tab === 'summary'));
        $$('.right-tab').forEach((x) => {
          const on = x.id === 'tab-summary';
          x.classList.toggle('active', on);
          x.classList.toggle('hidden', !on);
        });
        VaultPanel.refresh();
      } else if (item.id === 'nav-experts') {
        showTraceView(false);
        showDevtoolsView(false);
        openSettings('agents');
      } else if (item.id === 'ln-doc-design' || item.id === 'ln-doc-techniques') {
        const rel = item.id === 'ln-doc-design' ? 'Design.md' : 'Techniques.md';
        openKnowledgeDoc(rel);
      } else if (item.dataset && item.dataset.taskId) {
        switchTaskHandler(item.dataset.taskId);
      } else {
        showTraceView(false);
        showDevtoolsView(false);
      }
    };
  });

  $('#editor-dropdown').onclick = () => {
    // Editor 标签：切到 Files 页并聚焦编辑器（与标签栏语义一致）
    $$('.rh-tab[data-tab]').forEach((x) => x.classList.toggle('active', x.dataset.tab === 'files'));
    $$('.right-tab').forEach((x) => {
      const on = x.id === 'tab-files';
      x.classList.toggle('active', on);
      x.classList.toggle('hidden', !on);
    });
  };

  $('#ln-schedule').onclick = () => {
    // Schedule：任务级会话快照（按轮次存档，相当于任务排期历史）
    if (typeof openSnapshotDialog === 'function') openSnapshotDialog();
    else toast('快照对话框未就绪', 'err');
  };
  $('#ln-marketplace').onclick = () => {
    // Marketplace：账户与项目下载（官方服务器分发）
    if (typeof openAccount === 'function') openAccount();
  };

  $('#btn-new-task').onclick = () => {
    // v8.16.1：运行中新建会把当前回合视区切进新任务——与切换同守卫（决策 115）
    if (Agent.running) { toast('AI 运行中，请先停止再新建会话', 'err'); return; }
    showPrompt('新建任务名称', '', async (name) => {
      const label = String(name || '').trim();
      if (!label) return;
      await createTask(label);
      renderTaskLists();
      restoreChat();
      Chat.cleanup();
      if (typeof TraceView !== 'undefined') TraceView.clear();
      showTraceView(false);
      toast('已创建任务「' + label + '」并切换', 'ok');
    });
  };

  $('#btn-settings').onclick = () => { openSettings(); };
  $('#btn-account').onclick = () => { openAccount(); };
  $('#btn-logout').onclick = logout;
  $('#btn-theme').onclick = cycleTheme;

  // 聊天
  $('#btn-send').onclick = () => sendMessage();
  $('#btn-chat-stop').onclick = () => Agent.stopSession();
  // v8.13：附件引用按钮 + Agent 形态选择器真实接线
  wireAttachButton();
  wireAgentMode();
  /* ---------- 语音助手「小龙」UI 接线 ---------- */
  wireVoiceAssistant();
  $('#btn-chat-clear').onclick = () => {
    if (Agent.running) { toast('Agent 运行中，请先停止再清空会话', 'err'); return; }
    showPrompt('清空会话？输入 yes 确认', '', async (v) => {
      if (String(v).trim().toLowerCase() !== 'yes') { toast('已取消'); return; }
      if (typeof Approval !== 'undefined' && Approval.respond) Approval.respond(false);
      App.history = [];
      await saveHistory();
      restoreChat();
      Chat.cleanup();
      if (typeof TraceView !== 'undefined') TraceView.clear();
      showTraceView(false);
      toast('会话已清空', 'ok');
    });
  };
  const input = $('#chat-input');
  input.addEventListener('input', autoGrow);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  $$('.chip[data-s]').forEach((chip) => {
    chip.onclick = () => { $('#chat-input').value = chip.dataset.s; autoGrow(); sendMessage(); };
  });

  // 保存快捷键
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      saveActiveTab();
    }
    if (e.key === 'Escape') {
      hideCtxMenu();
      if (!$('#sleep-overlay').classList.contains('hidden')) cancelSleep();
    }
  });

  // 文件树工具栏
  $('#btn-new-file').onclick = () => {
    const ctx = Tree.lastRowRel || '';
    promptPath('新建文件（相对路径）', ctx, Tree.lastRowIsDir !== false, false);
  };
  // v8.13：lastRowIsDir 必须显式判定——最后点击的是文件时不能把文件路径当父目录
  $('#btn-new-folder').onclick = () => promptPath('新建文件夹（相对路径）', Tree.lastRowRel || Tree.root, Tree.lastRowIsDir === true, true);
  $('#btn-refresh-tree').onclick = () => Tree.refresh();
  $('#ws-bar').onclick = async () => {
    const r = await pickWorkspace();
    if (r.ok) {
      toast('工作区已切换为 ' + (r.name || r.workspace), 'ok');
      updateStatusbar();
      Tree.refresh();
    } else {
      toast(r.message || '未选择', 'err');
    }
  };

  // 终端
  $('#terminal-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const cmd = e.target.value.trim();
      if (!cmd) return;
      e.target.value = '';
      Terminal.logCmdHeader(cmd);
      runTerminalCommand(cmd);
    }
  });
  $('#btn-clear-terminal').onclick = () => Terminal.clear();

  // 右键菜单关闭
  document.addEventListener('click', hideCtxMenu);
  document.addEventListener('contextmenu', (e) => {
    if (!$('#ctx-menu').contains(e.target)) hideCtxMenu();
  });
  window.addEventListener('resize', hideCtxMenu);

  // v8.20：三栏可拖动分隔条（pointer 事件统一抽象，宽度持久化到 localStorage）
  initResizableGutters();
  // v8.21：Peek 触发式交互（左栏窄图标条/右栏触发条，hover 展开，pin 固定）
  initPeekMode();
}

/* ---------- v8.20 三栏可拖动分隔条 ----------
 * 用 pointerdown/pointermove/pointerup（兼容鼠标/触摸/笔）。
 * 宽度经 CSS 变量 --nav-w / --right-w 驱动，拖动时实时 setProperty。
 * 命中区在伪元素上（CSS 已设 cursor:col-resize + z-index），这里只挂 pointerdown。
 * 边界 clamp 防止拖过头：每栏保留最小可见宽度，主区至少占视口 30%。
 * 宽度持久化到 localStorage，刷新后恢复。
 */
const GUTTER_KEY = 'deverai.gutters.v1';
function loadGutterPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem(GUTTER_KEY) || '{}');
    if (typeof p.nav === 'number' && p.nav >= 200 && p.nav <= 360) {
      document.documentElement.style.setProperty('--nav-w', p.nav + 'px');
    }
    if (typeof p.right === 'number' && p.right >= 240 && p.right <= 560) {
      document.documentElement.style.setProperty('--right-w', p.right + 'px');
    }
  } catch (e) { /* 配置损坏用默认值 */ }
}
function saveGutterPref(key, val) {
  try {
    const p = JSON.parse(localStorage.getItem(GUTTER_KEY) || '{}');
    p[key] = val; localStorage.setItem(GUTTER_KEY, JSON.stringify(p));
  } catch (e) { /* 隐私模式/配额满静默 */ }
}
function initResizableGutters() {
  loadGutterPrefs();
  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
  // 伪元素不直接接事件，用 hit 区（伪元素宽 --gutter-hit）落在父元素上，
  // 检测 pointerdown 距右缘/左缘距离判定是否抓分隔条
  const NAV_HIT = 8;   // 左栏右缘 8px 内视为抓分隔条
  const RIGHT_HIT = 8; // 右栏左缘 8px 内视为抓分隔条
  const NAV_MIN = 200, NAV_MAX = 360;
  const RIGHT_MIN = 240, RIGHT_MAX = 560;
  const MAIN_MIN_RATIO = 0.30; // 主区至少占视口 30%

  const nav = $('#left-nav');
  const right = $('#right-panel');
  if (!nav || !right) return;

  let dragging = null; // {type:'nav'|'right', startX, startW}

  const onDown = (e) => {
    if (e.button !== undefined && e.button !== 0) return; // 仅主键
    const navRect = nav.getBoundingClientRect();
    const rightRect = right.getBoundingClientRect();
    // 左栏可见且点击在其右缘命中区
    if (nav.offsetWidth > 0 && Math.abs(e.clientX - navRect.right) <= NAV_HIT) {
      dragging = { type: 'nav', startX: e.clientX, startW: nav.offsetWidth };
      e.preventDefault();
      document.body.style.userSelect = 'none';
      return;
    }
    // 右栏可见且点击在其左缘命中区
    if (right.offsetWidth > 0 && Math.abs(e.clientX - rightRect.left) <= RIGHT_HIT) {
      dragging = { type: 'right', startX: e.clientX, startW: right.offsetWidth };
      e.preventDefault();
      document.body.style.userSelect = 'none';
    }
  };
  const onMove = (e) => {
    if (!dragging) return;
    // 释放丢失兜底（pointerup 未触发时 buttons 为 0）
    if (e.buttons === 0) { onUp(); return; }
    const dx = e.clientX - dragging.startX;
    if (dragging.type === 'nav') {
      // 向右拖 → 左栏变宽；主区不能小于视口 30%
      const mainMin = window.innerWidth * MAIN_MIN_RATIO;
      const maxByMain = window.innerWidth - right.offsetWidth - mainMin - 2;
      const w = clamp(dragging.startW + dx, NAV_MIN, Math.min(NAV_MAX, maxByMain));
      document.documentElement.style.setProperty('--nav-w', w + 'px');
    } else {
      // 向左拖 → 右栏变宽；主区不能小于视口 30%
      const mainMin = window.innerWidth * MAIN_MIN_RATIO;
      const maxByMain = window.innerWidth - nav.offsetWidth - mainMin - 2;
      const w = clamp(dragging.startW - dx, RIGHT_MIN, Math.min(RIGHT_MAX, maxByMain));
      document.documentElement.style.setProperty('--right-w', w + 'px');
    }
  };
  const onUp = () => {
    if (!dragging) return;
    if (dragging.type === 'nav') saveGutterPref('nav', nav.offsetWidth);
    else saveGutterPref('right', right.offsetWidth);
    dragging = null;
    document.body.style.userSelect = '';
  };

  document.addEventListener('pointerdown', onDown);
  document.addEventListener('pointermove', onMove);
  document.addEventListener('pointerup', onUp);
  document.addEventListener('pointercancel', onUp);
  window.addEventListener('blur', onUp);

  // v8.21：拖动分隔条 = 用户主动调整宽度 → 移除 peek 固定展开（保持当前宽度）
  document.addEventListener('pointerdown', (e) => {
    if (e.target === nav || nav?.contains(e.target)) return;
    if (Math.abs(e.clientX - (nav?.getBoundingClientRect().right || 0)) <= 8) {
      setPeek('nav', false);
    }
    if (Math.abs(e.clientX - (right?.getBoundingClientRect().left || 0)) <= 8) {
      setPeek('right', false);
    }
  }, true);
}

/* ---------- v8.21 Peek 触发式交互 ----------
 * ChatGPT 桌面端逻辑：未碰的区域最简展示，hover/聚焦才展开。
 * 左栏 peek = 窄图标条（52px），hover 整栏展开挤占主区；
 * 右栏 peek = 触发条宽（6px），hover 展开浮起。
 * pin 按钮切换固定展开（移除 peek 类）；拖动分隔条也自动固定。
 * 窄屏（<900px）不启用 peek，避免移动端误触。
 */
const PEEK_KEY = 'deverai.peek.v1';
function loadPeekPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem(PEEK_KEY) || '{}');
    return { nav: p.nav !== false, right: p.right !== false };
  } catch (e) { return { nav: true, right: true }; }
}
function savePeekPref(key, val) {
  try {
    const p = JSON.parse(localStorage.getItem(PEEK_KEY) || '{}');
    p[key] = val; localStorage.setItem(PEEK_KEY, JSON.stringify(p));
  } catch (e) { /* 隐私模式静默 */ }
}
function setPeek(which, on) {
  const el = which === 'nav' ? $('#left-nav') : $('#right-panel');
  if (!el) return;
  el.classList.toggle('peek', on);
  const pin = which === 'nav' ? $('#btn-nav-pin') : $('#btn-right-pin');
  if (pin) {
    pin.classList.toggle('pinned', !on);
    pin.setAttribute('aria-pressed', String(!on));
  }
  savePeekPref(which, on);
}
function initPeekMode() {
  // 窄屏强制不 peek（media query 已处理布局，这里逻辑层也跳过）
  if (window.innerWidth < 900) return;
  const prefs = loadPeekPrefs();
  setPeek('nav', prefs.nav);
  setPeek('right', prefs.right);
  const navPin = $('#btn-nav-pin');
  if (navPin) navPin.onclick = () => setPeek('nav', !$('#left-nav').classList.contains('peek'));
  const rightPin = $('#btn-right-pin');
  if (rightPin) rightPin.onclick = () => setPeek('right', !$('#right-panel').classList.contains('peek'));
}

/* ---------- v8.13：多任务会话 / 知识文档 / 附件引用 / Agent 形态 ---------- */
function renderTaskLists() {
  const tasks = App.tasks || [];
  const mk = (boxId) => {
    const box = document.getElementById(boxId);
    if (!box) return;
    box.innerHTML = '';
    tasks.forEach((t) => {
      const item = el('div', 'ln-item' + (t.id === App.taskId ? ' active' : ''));
      item.dataset.taskId = t.id;
      item.title = t.name + ' · ' + (t.updated_at || '');
      item.setAttribute('role', 'button');
      item.setAttribute('tabindex', '0');
      item.setAttribute('aria-label', '切换到会话：' + t.name);
      item.appendChild(el('span', 'ln-dot'));
      item.appendChild(el('span', 'ln-label', esc(t.name)));
      item.onclick = () => switchTaskHandler(t.id);
      // v8.20：键盘可达性 —— Enter / Space 触发与点击同等行为
      item.onkeydown = (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); item.click(); }
      };
      box.appendChild(item);
    });
  };
  mk('task-list');
  mk('chat-list');
  // v8.16：同步刷新聊天区顶部会话标签条（sessions.js 暴露；开关关闭时自行隐藏）
  if (window.Sessions) Sessions.render();
}

async function switchTaskHandler(taskId) {
  if (!taskId || taskId === App.taskId) return;
  if (Agent.running) { toast('AI 运行中，请先停止再切换会话', 'err'); renderTaskLists(); return; }
  if (typeof Approval !== 'undefined' && Approval.respond) Approval.respond(false);
  const ok = await switchTask(taskId);
  if (!ok) return;
  restoreChat();
  Chat.cleanup();
  if (typeof TraceView !== 'undefined') {
    TraceView.clear();
    TraceView.setHistory(App.history);
  }
  renderTaskLists();
  updateStatusbar();
  showTraceView(false);
  showDevtoolsView(false);
  const t = App.tasks.find((x) => x.id === taskId);
  toast('已切换到：' + (t ? t.name : taskId), 'ok');
}

async function openKnowledgeDoc(rel) {
  if (!fsWorkspaceReady()) {
    toast('请先授权工作区，再打开 ' + rel, 'err');
    return;
  }
  try {
    await openTab(rel);
    $$('.rh-tab[data-tab]').forEach((x) => x.classList.toggle('active', x.dataset.tab === 'files'));
    $$('.right-tab').forEach((x) => {
      const on = x.id === 'tab-files';
      x.classList.toggle('active', on);
      x.classList.toggle('hidden', !on);
    });
  } catch (e) {
    toast('打开失败: ' + e.message, 'err');
  }
}

function wireAgentMode() {
  const sel = $('#mode-select');
  if (!sel || sel.__modeWired) return;
  sel.__modeWired = true;
  // v8.13：网页版尚无专家团编排（Fact 记录），隐藏 Experts 入口避免死选项
  Array.from(sel.options).forEach((o) => {
    if (o.value === 'experts') o.style.display = 'none';
  });
  if (sel.value === 'experts') sel.value = 'builder';
  sel.value = (App.config && App.config.agent_mode) || 'builder';
  sel.addEventListener('change', () => {
    const v = sel.value === 'chat' ? 'chat' : 'builder';
    App.config.agent_mode = v;
    saveConfig();
    updateStatusbar();
    toast(v === 'chat' ? '已切换 Chat 形态（只读工具优先）' : '已切换 Builder 形态（完整构建）', 'ok');
  });
}

function wireAttachButton() {
  const btn = $('#btn-attach');
  if (!btn) return;
  btn.onclick = () => openReferencePicker();
}

async function openReferencePicker() {
  if (!fsWorkspaceReady()) { toast('请先授权工作区', 'err'); return; }
  const modal = $('#modal-root');
  const box = $('#modal');
  if (!modal || !box) return;
  modal.classList.remove('hidden');
  box.innerHTML = `
    <div class="modal-head"><span class="modal-title">引用工作区文件</span>
      <button class="icon-btn" id="ref-close">${icon('close', 12)}</button></div>
    <div class="modal-body">
      <input class="form-input" id="ref-search" placeholder="输入文件名过滤…" />
      <div id="ref-list" style="max-height:50vh;overflow:auto;margin-top:10px;"><div class="form-hint">加载文件列表…</div></div>
    </div>`;
  $('#ref-close').onclick = () => modal.classList.add('hidden');
  closeOnBackdrop();
  const search = $('#ref-search');
  const list = $('#ref-list');
  let files = [];
  try {
    const walk = await walkFs('');
    files = (walk.files || []).slice(0, 500);
  } catch (e) {
    list.innerHTML = '<div class="form-hint">文件列表加载失败: ' + esc(String((e && e.message) || e)) + '</div>';
    return;
  }
  const render = () => {
    const q = String(search.value || '').trim().toLowerCase();
    const hits = q ? files.filter((f) => f.path.toLowerCase().includes(q)) : files;
    list.innerHTML = '';
    if (!hits.length) { list.innerHTML = '<div class="form-hint">无匹配文件</div>'; return; }
    hits.slice(0, 100).forEach((f) => {
      const row = el('div', 'snap-row');
      row.appendChild(el('span', '', esc(f.path)));
      const pick = el('button', 'btn', '引用');
      pick.onclick = async () => {
        try {
          const r = await readFile(f.path);
          const content = String((r && r.content) || '').slice(0, 12000);
          App.addQuote('文件引用 ' + f.path, '路径：' + f.path + '\n---\n' + content);
          modal.classList.add('hidden');
          toast('已加入引用条，发送时随消息带上', 'ok');
        } catch (e) { toast('读取失败: ' + e.message, 'err'); }
      };
      row.appendChild(pick);
      list.appendChild(row);
    });
  };
  search.addEventListener('input', render);
  render();
}

/* ---------- 视图切换 ---------- */
function _setTraceNavActive(active) {
  const nav = $('#nav-trace');
  if (!nav) return;
  if (active) {
    $$('.ln-item').forEach((x) => x.classList.remove('active'));
    nav.classList.add('active');
  } else {
    nav.classList.remove('active');
  }
}

function showTraceView(show) {
  const hero = $('#hero');
  const chat = $('#chat-messages');
  const trace = $('#trace-view');
  const input = $('#input-area');
  _setTraceNavActive(show);
  if (show) {
    if (hero) hero.classList.add('hidden');
    if (chat) chat.classList.add('hidden');
    if (trace) trace.classList.remove('hidden');
    if (input) input.classList.add('hidden');
    if (typeof TraceView !== 'undefined') TraceView._renderTimeline();
  } else {
    if (trace) trace.classList.add('hidden');
    if (chat && chat.childElementCount) {
      if (hero) hero.classList.add('hidden');
      if (chat) chat.classList.remove('hidden');
    } else {
      if (hero) hero.classList.remove('hidden');
      if (chat) chat.classList.add('hidden');
    }
    if (input) input.classList.remove('hidden');
  }
}

/* ---------- 引用条（轨迹预览/详情对话框引用） ---------- */
App._quotes = App._quotes || [];
function renderQuoteBar() {
  let bar = $('#quote-bar');
  if (!bar) {
    bar = el('div', 'quote-bar');
    bar.id = 'quote-bar';
    const inputArea = $('#input-area');
    if (inputArea) inputArea.insertBefore(bar, inputArea.firstChild);
  }
  bar.innerHTML = '';
  if (!App._quotes.length) {
    bar.classList.add('hidden');
    return;
  }
  bar.classList.remove('hidden');
  App._quotes.forEach((q, idx) => {
    const chip = el('div', 'quote-chip');
    // P0 修复：label/content 全部走 textContent，防 XSS（FreqErr #149）
    const labelEl = el('span', 'quote-chip-label');
    labelEl.textContent = q.label || '引用';
    chip.appendChild(labelEl);
    const textEl = el('span', 'quote-chip-text');
    const raw = String(q.content || '');
    textEl.textContent = raw.slice(0, 60) + (raw.length > 60 ? '…' : '');
    chip.appendChild(textEl);
    const rm = el('button', 'quote-chip-rm', '×');
    rm.title = '移除该引用';
    rm.onclick = () => {
      App._quotes.splice(idx, 1);
      renderQuoteBar();
    };
    chip.appendChild(rm);
    bar.appendChild(chip);
  });
}
App.addQuote = function (label, content) {
  if (!content) return;
  App._quotes.push({ label: label || '引用', content: String(content) });
  renderQuoteBar();
  // 切回聊天视图
  showTraceView(false);
  showDevtoolsView(false);
  const inp = $('#chat-input');
  if (inp) { inp.focus(); }
};
App.clearQuotes = function () {
  App._quotes = [];
  renderQuoteBar();
};
App.buildQuoteMessage = function () {
  if (!App._quotes || !App._quotes.length) return '';
  return App._quotes.map((q, i) => `【引用内容 ${i + 1} · ${q.label}】\n${q.content}`).join('\n\n');
};

/* ---------- v8.10：轨迹详情 modal + 操作下拉接线 ---------- */
function initTraceModal() {
  const modal = $('#trace-modal');
  if (!modal) return;
  const closeBtn = $('#trace-modal-close');
  if (closeBtn) closeBtn.addEventListener('click', () => TraceView.closePreview());
  const mask = modal.querySelector('.trace-modal-mask');
  if (mask) mask.addEventListener('click', () => { if (!TraceView._pinned) TraceView.closePreview(); });
  const pinBtn = $('#trace-modal-pin');
  if (pinBtn) pinBtn.addEventListener('click', () => TraceView.togglePin());
  const zoomIn = $('#trace-modal-zoom-in');
  if (zoomIn) zoomIn.addEventListener('click', () => TraceView.zoomIn());
  const zoomOut = $('#trace-modal-zoom-out');
  if (zoomOut) zoomOut.addEventListener('click', () => TraceView.zoomOut());
  const zoomReset = $('#trace-modal-zoom-reset');
  if (zoomReset) zoomReset.addEventListener('click', () => TraceView.zoomReset());
  const quoteSel = $('#trace-modal-quote-sel');
  if (quoteSel) quoteSel.addEventListener('click', () => TraceView.quoteSelection());
  const quoteAll = $('#trace-modal-quote-all');
  if (quoteAll) quoteAll.addEventListener('click', () => {
    const item = TraceView._currentItem;
    if (item && typeof App !== 'undefined' && App.addQuote) {
      App.addQuote('轨迹预览', TraceView._buildFullText());
      TraceView.closePreview();
    }
  });
}

function initTraceOps() {
  const btn = $('#trace-ops');
  const menu = $('#trace-ops-menu');
  if (!btn || !menu) return;
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    menu.classList.toggle('hidden');
  });
  document.addEventListener('click', (e) => {
    if (!menu.contains(e.target) && e.target !== btn) {
      menu.classList.add('hidden');
    }
  });
}

// 初始化 trace modal & ops（在 TraceView.init 之后调用）
const _origBoot = boot;
boot = async function () {
  await _origBoot();
  if (typeof TraceView !== 'undefined') {
    TraceView.init();
    initTraceModal();
    initTraceOps();
  }
  renderQuoteBar();
};

function showDevtoolsView(show) {
  const hero = $('#hero');
  const chat = $('#chat-messages');
  const trace = $('#trace-view');
  const dev = $('#devtools-view');
  const input = $('#input-area');
  if (show) {
    if (hero) hero.classList.add('hidden');
    if (chat) chat.classList.add('hidden');
    if (trace) trace.classList.add('hidden');
    if (dev) dev.classList.remove('hidden');
    if (input) input.classList.add('hidden');
    if (typeof DevTools !== 'undefined') DevTools.render();
  } else {
    if (dev) dev.classList.add('hidden');
  }
}

/* ---------- 终端手动命令（走命令桥） ---------- */
async function runTerminalCommand(cmd) {
  if (!FS.bridge.authorized) {
    Terminal.appendLine('命令桥未授权：请在设置中授权工作区', 't-err');
    return;
  }
  // v8.11：危险命令后端拦截——终端手动执行属用户主动操作，此处 confirm 后显式放行
  let dangerOk = false;
  if (typeof isDangerousCommand === 'function' && isDangerousCommand(cmd)) {
    if (!confirm('[危险] 命令：' + cmd + '\n\n可能造成不可逆影响，确认执行？')) {
      Terminal.appendLine('已取消执行（危险命令）', 't-err');
      return;
    }
    dangerOk = true;
  }
  const exec = (ok) => bridgeRunCommand({
    command: cmd,
    timeout: 600,
    dangerOk: ok,
    onLine(line) { Terminal.appendLine(line); },
  });
  try {
    let result = await exec(dangerOk);
    // v8.13：后端 403 危险拦截时补一次二次确认后带 danger_ok 重试（本地漏判不再卡死）
    if (result.error && result.error.indexOf('403') !== -1 && !dangerOk) {
      if (!confirm('[危险] 命令：' + cmd + '\n\n后端判定为危险命令，确认执行？')) {
        Terminal.appendLine('已取消执行（危险命令）', 't-err');
        return;
      }
      result = await exec(true);
    }
    if (result.error) Terminal.appendLine('执行失败: ' + result.error, 't-err');
  } catch (e) {
    const em = String((e && e.message) || e);
    const is403 = !!(e && (e.status === 403 || em.indexOf('403') !== -1));
    if (is403 && !dangerOk) {
      if (!confirm('[危险] 命令：' + cmd + '\n\n后端判定为危险命令，确认执行？')) {
        Terminal.appendLine('已取消执行（危险命令）', 't-err');
        return;
      }
      try {
        const result = await exec(true);
        if (result.error) Terminal.appendLine('执行失败: ' + result.error, 't-err');
        return;
      } catch (e2) { Terminal.appendLine('执行失败: ' + e2.message, 't-err'); return; }
    }
    Terminal.appendLine('执行失败: ' + em, 't-err');
  }
}

/* 兼容旧调用：sleep 事件从 agent 发出（Escape 处理已在 wireUI 中注册，此处不再重复） */

/* ---------- 语音助手「小龙」UI 接线（v2.0 集成 voice-pet 浮层） ---------- */
function wireVoiceAssistant() {
  const cfg = App.config || {};
  const btn = $('#btn-voice-assist');
  const panel = $('#voice-panel');

  // 开关控制：ENABLE_VOICE_ASSISTANT 决定是否显示入口
  if (btn) {
    btn.classList.toggle('hidden', !cfg.ENABLE_VOICE_ASSISTANT);
  }

  // VoicePet 浮层模式（优先）
  if (window.VoicePet) {
    // 设置里开启后必须显式初始化：voice-pet.js 的 DOMContentLoaded 早于 boot()，
    // 当时 App.config 尚未加载，自动初始化永远不成立（v2.0 集成修复）
    if (cfg.ENABLE_VOICE_ASSISTANT) window.VoicePet.init();
    // 侧栏按钮 → 打开 VoicePet 对话面板
    if (btn) {
      btn.onclick = function () {
        window.VoicePet.togglePanel();
        if (panel) panel.classList.add('hidden');
      };
    }
    // VoiceAssistant 结果 → 同步到 VoicePet 对话
    VoiceAssistant.onResult(async function (transcript, intent) {
      window.VoicePet.appendMessage('user', transcript);
      if (!intent) {
        window.VoicePet.setMood('thinking');
        var r = await VoiceAssistant.feedbackMode(transcript);
        window.VoicePet.setMood(r.ok ? 'happy' : 'sad');
        if (r.ok) {
          window.VoicePet.appendMessage('assistant', r.text);
          window.VoicePet.speak(r.text);
        } else {
          window.VoicePet.appendMessage('assistant', '错误: ' + (r.error || '未知'));
        }
        return;
      }
      window.VoicePet.setMood('thinking');
      var r2 = await VoiceAssistant.executeIntent(intent);
      window.VoicePet.setMood(r2 && r2.ok ? 'happy' : 'sad');
      window.VoicePet.appendMessage('assistant', r2.message || r2.error || '');
      window.VoicePet.loadProgress();
    });
    return;
  }

  // 旧版面板模式（fallback，无 VoicePet 时）
  if (!btn || !panel) return;

  var micBtn = $('#voice-mic-btn');
  var stopBtn = $('#voice-stop-btn');
  var closeBtn = $('#voice-close');
  var statusEl = $('#voice-status');
  var transcriptEl = $('#voice-transcript');
  var replyEl = $('#voice-reply');
  var mascotEl = $('#voice-mascot');
  var modeSelect = $('#voice-mode-select');

  // 根据配置渲染吉祥物
  function renderMascot(state) {
    if (!mascotEl) return;
    const style = (App.config && App.config.mascot_style) || 'dragon';
    if (style === 'siri') {
      mascotEl.className = 'mascot-siri-ball ' + (state || 'idle');
      mascotEl.innerHTML = '<div class="siri-orb siri-orb-1"></div><div class="siri-orb siri-orb-2"></div><div class="siri-orb siri-orb-3"></div><div class="siri-orb siri-orb-4"></div><div class="siri-orb siri-orb-5"></div>';
    } else if (style === 'custom' && App.config && App.config.mascot_custom_img) {
      mascotEl.className = '';
      mascotEl.innerHTML = '';
      const src = String(App.config.mascot_custom_img);
      if (/^data:image\/(?:png|jpe?g|gif|webp)(?:;base64)?,/i.test(src)) {
        const img = document.createElement('img');
        img.src = src;
        img.style.cssText = 'width:40px;height:40px;border-radius:50%;object-fit:cover';
        mascotEl.appendChild(img);
      }
    } else {
      // 龙猫（默认）
      mascotEl.className = 'voice-mascot ' + (state || 'idle');
      mascotEl.innerHTML = '<div class="mascot-body"><div class="mascot-ear left"></div><div class="mascot-ear right"></div><div class="mascot-face"><div class="mascot-eye left"></div><div class="mascot-eye right"></div><div class="mascot-nose"></div><div class="mascot-mouth"></div></div><div class="mascot-belly"></div></div>';
    }
  }

  // 设置吉祥物初始状态
  function setMascotState(state) {
    renderMascot(state);
  }
  setMascotState('idle');

  // 状态变更回调
  VoiceAssistant.onStateChange((state) => {
    const map = { idle: 'idle', recording: 'recording', thinking: 'thinking', speaking: 'speaking' };
    setMascotState(map[state] || 'idle');
    if (statusEl) {
      const labels = { idle: '点击麦克风开始说话', recording: '正在聆听…', thinking: '思考中…', speaking: '回复中…' };
      statusEl.textContent = labels[state] || '';
    }
    if (micBtn) micBtn.classList.toggle('recording', state === 'recording');
    if (stopBtn) stopBtn.classList.toggle('hidden', state === 'idle');
  });

  // 中间转录文本
  VoiceAssistant.onTranscript((text, isFinal) => {
    if (transcriptEl) {
      transcriptEl.textContent = text;
      transcriptEl.style.opacity = isFinal ? '1' : '0.6';
    }
  });

  // 识别结果处理
  VoiceAssistant.onResult(async (transcript, intent) => {
    if (transcriptEl) {
      transcriptEl.textContent = transcript;
      transcriptEl.style.opacity = '1';
    }
    if (!intent) {
      // 无匹配意图 → 反馈模式
      setMascotState('thinking');
      if (statusEl) statusEl.textContent = '正在思考…';
      const r = await VoiceAssistant.feedbackMode(transcript);
      setMascotState('idle');
      if (statusEl) statusEl.textContent = '';
      if (r.ok) {
        if (replyEl) { replyEl.textContent = r.text; replyEl.classList.remove('hidden'); }
        VoiceAssistant.speak(r.text);
      } else {
        if (replyEl) { replyEl.textContent = '错误: ' + (r.error || '未知'); replyEl.classList.remove('hidden'); }
      }
      return;
    }

    // 有匹配意图 → 根据模式执行
    const mode = modeSelect ? modeSelect.value : 'feedback';
    if (mode === 'agent' && intent.id === 'execute_task') {
      // Agent 模式：触发 Agent 循环
      const r = VoiceAssistant.agentMode(intent.capture || transcript);
      if (replyEl) { replyEl.textContent = r.message || ''; replyEl.classList.remove('hidden'); }
      setMascotState('idle');
      if (statusEl) statusEl.textContent = '';
    } else {
      // 反馈模式或简单意图
      setMascotState('thinking');
      if (statusEl) statusEl.textContent = '执行: ' + intent.desc + '…';
      const r = await VoiceAssistant.executeIntent(intent);
      setMascotState('idle');
      if (statusEl) statusEl.textContent = '';
      if (replyEl) { replyEl.textContent = r.message || r.error || ''; replyEl.classList.remove('hidden'); }
    }
  });

  // 麦克风按钮
  if (micBtn) {
    micBtn.onclick = () => {
      if (VoiceAssistant.getState() === 'recording') {
        VoiceAssistant.stopListening();
      } else {
        if (!VoiceAssistant.isSupported()) {
          toast('当前浏览器不支持 Web Speech API，请使用 Chrome/Edge', 'err', 5000);
          return;
        }
        if (replyEl) replyEl.classList.add('hidden');
        VoiceAssistant.startListening();
      }
    };
  }

  // 停止按钮
  if (stopBtn) {
    stopBtn.onclick = () => {
      VoiceAssistant.stopListening();
      VoiceAssistant.stopSpeaking();
    };
  }

  // 关闭按钮
  if (closeBtn) {
    closeBtn.onclick = () => {
      VoiceAssistant.stopListening();
      VoiceAssistant.stopSpeaking();
      panel.classList.add('hidden');
    };
  }

  // 侧栏入口按钮
  btn.onclick = () => {
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) {
      setMascotState('idle');
    }
  };
}
