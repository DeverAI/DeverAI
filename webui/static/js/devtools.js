/* v8.9 API 控制台（DevTools）
 * 记录浏览器端 Agent 发出的同源请求（LLM 代理 / 命令桥 / 快照桥），
 * 每条记录提供「复制为 curl」，让用户在浏览器里按 F12 之外，也能一键拿到可复现命令。
 * 安全：只记录方法/URL/请求体/状态/耗时；API Key 在预览中打码，复制时按真实内容复制。
 */
(function () {
  const MAX_ENTRIES = 200;
  // v8.11：敏感键白名单扩展（password/token/sync_token）——登录请求体的密码与令牌同样打码
  const KEY_RE = /("(?:api_key|api-key|authorization|sync_password|sync_token|drift_api_key|search_api_key|dashscope_api_key|password|token)"\s*:\s*")[^"]*(")/gi;
  const entries = [];
  let tableEl = null;
  let seq = 0;

  function mask(s) {
    return String(s || '').replace(KEY_RE, '$1***$2');
  }

  function truncate(s, n) {
    s = String(s || '');
    return s.length > n ? s.slice(0, n) + '…' : s;
  }

  // 单引号安全包裹（POSIX shell）；不直接拼接 Cookie 到命令，避免屏幕/剪贴板泄露登录态
  function shq(s) {
    s = String(s || '');
    return "'" + s.replace(/'/g, "'\\''") + "'";
  }

  // v8.11：会话令牌（登录时后端随响应回传，存 localStorage）→ curl 以 Authorization 复现鉴权
  function getToken() {
    try { return localStorage.getItem('deverai_token') || ''; } catch (e) { return ''; }
  }

  // 预览打码：Bearer 令牌与请求体内的 api_key/password 等敏感字段在界面上以 *** 显示，
  // 复制时才用真实值（与 API Key 同策略）。
  // 注意：curlOf 用 shq 整体单引号包裹 -H 值（无内层引号），打码正则以实际输出形态匹配。
  function maskCurl(s) {
    s = String(s || '').replace(/(-H 'Authorization: Bearer )[^']*'/, "$1***'");
    return mask(s);  // KEY_RE 同时打码 --data-raw 里的 "api_key":"..."、"password":"..." 等
  }

  function curlOf(e) {
    const rawUrl = (typeof e.url === 'string') ? e.url : String(e.url || '');
    const url = rawUrl.startsWith('http') ? rawUrl : (location.origin + (rawUrl.startsWith('/') ? '' : '/') + rawUrl);
    const method = (e.method || 'GET').toUpperCase();
    const parts = ['curl'];
    if (method !== 'GET') parts.push('-X', method);
    parts.push(shq(url));
    if (e.contentType) parts.push('-H', shq('Content-Type: ' + e.contentType));
    if (e.accept) parts.push('-H', shq('Accept: ' + e.accept));
    // v8.11：携带 Bearer 令牌，登录后的端口（LLM 代理/命令桥/快照桥）可直接在终端复现。
    // 不自动携带 Cookie（HttpOnly 不可读）；需要 Cookie 场景仍可从 F12 自行追加。
    const tok = getToken();
    if (tok) {
      parts.push('-H', shq('Authorization: Bearer ' + tok));
    } else if (method !== 'GET' || rawUrl.startsWith('/api/') || rawUrl.includes('/api/')) {
      // v8.14：本地令牌缺失（过期清理/仅 Cookie 会话）时明示，避免复制出必然 401 的 curl
      parts.push('-H', shq('Authorization: Bearer <本地令牌缺失：请重新登录或从 F12 应用面板取 Cookie>'));
    }
    if (e.body) parts.push('--data-raw', shq(JSON.stringify(e.body)));
    else if (method !== 'GET') parts.push('--data-raw', shq('{}'));
    return parts.join(' ');
  }

  function render() {
    if (!tableEl) return;
    if (!entries.length) {
      tableEl.innerHTML = '<div class="devtools-empty">暂无请求记录。发送一条消息后，这里会列出 LLM 代理与命令桥的每次请求。</div>';
      return;
    }
    const html = entries.map((e) => {
      const ok = e.ok ? 'ok' : 'err';
      const body = e.body !== undefined ? mask(truncate(JSON.stringify(e.body), 400)) : '（无）';
      return `<div class="devtools-item">
        <div class="devtools-row">
          <span class="devtools-badge ${ok}">${e.status || 'ERR'}</span>
          <span class="devtools-method">${escapeHtml(e.method)}</span>
          <span class="devtools-url" title="${escapeHtml(e.url)}">${escapeHtml(truncate(e.url, 120))}</span>
          <span class="devtools-cost">${e.costMs}ms</span>
          <button class="btn devtools-copy" data-id="${e.id}">复制为 curl</button>
        </div>
        <div class="devtools-detail">
          <div class="devtools-body">${escapeHtml(body)}</div>
          <textarea class="devtools-curl" rows="2" readonly>${escapeHtml(maskCurl(curlOf(e)))}</textarea>
        </div>
      </div>`;
    }).join('');
    tableEl.innerHTML = html;
    tableEl.querySelectorAll('.devtools-copy').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = Number(btn.dataset.id);
        const found = entries.find((x) => x.id === id);
        if (!found) return;
        copyText(curlOf(found));
      });
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function copyText(text) {
    const done = () => { if (typeof toast === 'function') toast('已复制 curl 命令', 'ok'); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
    } else fallbackCopy(text, done);
  }

  function fallbackCopy(text, done) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); } catch (e) { if (typeof toast === 'function') toast('复制失败，请手动选择文本复制', 'err'); }
    ta.remove();
  }

  function push(entry) {
    entries.unshift(entry);
    if (entries.length > MAX_ENTRIES) entries.length = MAX_ENTRIES;
    render();
  }

  function wrapFetch() {
    const original = window.fetch;
    if (!original || original.__deverai_wrapped) return;
    window.fetch = function (input, init) {
      const started = performance.now();
      let url = '';
      let method = 'GET';
      let body;
      let contentType = '';
      let accept = '';
      if (typeof input === 'string') url = input;
      else if (input && input.url) { url = input.url; method = input.method || 'GET'; }
      init = init || {};
      if (init.method) method = init.method.toUpperCase();
      if (init.headers) {
        const h = new Headers(init.headers);
        contentType = h.get('content-type') || '';
        accept = h.get('accept') || '';
      }
      if (typeof init.body === 'string') {
        try { body = JSON.parse(init.body); } catch (e) { body = init.body; }
      }
      const p = original.apply(this, arguments);
      const id = ++seq;
      return p.then((resp) => {
        push({
          id, ts: new Date().toISOString(), method, url, body,
          contentType, accept, status: resp.status, ok: resp.ok,
          costMs: Math.round(performance.now() - started), error: '',
        });
        return resp;
      }, (err) => {
        push({
          id, ts: new Date().toISOString(), method, url, body,
          contentType, accept, status: 0, ok: false,
          costMs: Math.round(performance.now() - started), error: String((err && err.message) || err),
        });
        throw err;
      });
    };
    window.fetch.__deverai_wrapped = true;
  }

  function init() {
    wrapFetch();
    tableEl = document.getElementById('devtools-list');
    // v8.13：nav-devtools 的视图切换已由 main.js wireUI 统一接线，此处不再重复绑定
    const clearBtn = document.getElementById('devtools-clear');
    if (clearBtn) clearBtn.addEventListener('click', () => { entries.length = 0; render(); });
    render();
  }

  window.DevTools = {
    entries,
    curlOf,
    render,
    clear: () => { entries.length = 0; render(); },
    push,
    init,
    // v8.11：暴露打码函数供测试与复用（预览打码、复制真实值）
    mask,
    maskCurl,
  };
})();
