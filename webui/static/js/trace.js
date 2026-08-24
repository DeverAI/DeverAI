/* ============ trace.js 网页版工作轨迹（v8.10） ============
 *
 * 只读展示 Agent 运行轨迹：顶部时间线 + 按角色分类的消息流 + 操作筛选 +
 * 关键词搜索 + 手动标记点 + 区间查询 + 详情预览 modal。
 *
 * 事件由 chat.js 的 onAgentEvent 在分发到聊天区后同步透传。
 *
 * 新增（v8.10）：
 * - 操作下拉多选（system/context/user/assistant/tool）
 * - 关键词多关键词 AND 搜索 + 区分大小写开关
 * - 时间线手动标记点（双击/工具条+）+ 区间拖拽查询
 * - 区间统计：条数/Turns/Calls/Top 工具
 * - 卡片详情 modal（可缩放、引用文字到对话）
 */
const TraceView = {
  _items: [],
  _maxItems: 2000,
  _maxBars: 160,
  _roleMeta: {
    system:    { label: 'SYSTEM',    color: '#7c3aed', bg: 'rgba(124,58,237,0.10)', icon: 'shield' },
    context:   { label: 'CONTEXT',   color: '#0891b2', bg: 'rgba(8,145,178,0.10)',  icon: 'memo' },
    user:      { label: 'USER',      color: '#2563eb', bg: 'rgba(37,99,235,0.10)',  icon: 'user' },
    assistant: { label: 'ASSISTANT', color: '#111827', bg: 'rgba(17,24,39,0.06)',  icon: 'atom' },
    tool:      { label: 'TOOL',      color: '#ca8a04', bg: 'rgba(202,138,4,0.10)', icon: 'cog' },
  },
  _keyword: '',
  _caseSensitive: false,
  _roleFilter: new Set(),
  _range: [0, 0],           // (t0, t1); (0,0) 表示无区间
  _markers: [],             // [{time, idx, note}]
  _dragStart: null,
  _dragEnd: null,
  _zoom: 100,
  _zoomLevels: [80, 100, 120, 150],
  _inited: false,           // P2-3 修复：防重复 init
  _dragging: false,
  _dragOffsetX: 0,
  _dragOffsetY: 0,
  _pinned: false,

  init() {
    if (typeof $ !== 'function' || typeof el !== 'function' || typeof esc !== 'function') {
      console.error('TraceView requires core.js to be loaded first');
      return;
    }
    if (this._inited) return;
    this._inited = true;
    // P1-4 修复：按 ENABLE_TRACE_ADVANCED 决定是否挂高级交互
    const advanced = !App || App.config == null || App.config.ENABLE_TRACE_ADVANCED !== false;
    this._advanced = advanced;
    this._initModalDrag();
    const search = $('#trace-search');
    if (search) search.addEventListener('input', (e) => this.setKeyword(e.target.value));
    const clear = $('#trace-clear');
    if (clear) clear.addEventListener('click', () => this.clear());
    if (!advanced) {
      // 关闭高级能力：隐藏 ops/case/+标记/详情按钮 + 阻止时间线鼠标交互
      const opsBtn = $('#trace-ops'); if (opsBtn) opsBtn.style.display = 'none';
      const caseBtn = $('#trace-case'); if (caseBtn) caseBtn.style.display = 'none';
      const addBtn = $('#trace-add-marker'); if (addBtn) addBtn.style.display = 'none';
      this._renderFlow();
      this._renderTimeline();
      return;
    }
    const caseBtn2 = $('#trace-case');
    if (caseBtn2) caseBtn2.addEventListener('click', () => this.toggleCase());
    const opsMenu = $('#trace-ops-menu');
    if (opsMenu) {
      opsMenu.querySelectorAll('input[type=checkbox]').forEach((cb) => {
        cb.addEventListener('change', (e) => this.toggleOpRole(cb.dataset.role, cb.checked));
      });
      const clearOp = $('#trace-ops-clear');
      if (clearOp) clearOp.addEventListener('click', () => this.clearOpFilter());
    }
    const addMarker = $('#trace-add-marker');
    if (addMarker) addMarker.addEventListener('click', () => this.addMarkerAtEnd());
    const canvas = $('#trace-timeline');
    if (canvas) {
      canvas.addEventListener('mousedown', (e) => this._onCanvasMouseDown(e));
      window.addEventListener('mousemove', (e) => this._onCanvasMouseMove(e));
      window.addEventListener('mouseup', (e) => this._onCanvasMouseUp(e));
      canvas.addEventListener('dblclick', (e) => this._onCanvasDblClick(e));
      canvas.addEventListener('contextmenu', (e) => this._onCanvasContextMenu(e));
    }
    this._renderFlow();
    this._renderTimeline();
  },

  /* ---------- 事件入口 ---------- */
  addEvent(ev) {
    const item = this._eventToItem(ev);
    if (!item) return;
    item.kind = ev && ev.type ? ev.type : (item.kind || '');
    this._items.push(item);
    if (this._items.length > this._maxItems) this._items.shift();
    this._renderTimeline();
    if (!this._keyword && this._roleFilter.size === 0 && this._range[0] === this._range[1]) {
      this._appendFlowCard(item);
    } else {
      this._renderFlow();
    }
    const flow = $('#trace-flow');
    if (flow) flow.scrollTop = flow.scrollHeight;
  },

  setHistory(history) {
    const items = [];
    (history || []).forEach((msg) => {
      const role = msg.role || '';
      const content = String(msg.content || '');
      if (role === 'system') {
        items.push({ role: 'system', time: 0, summary: 'Initial System Prompt', detail: content.slice(0, 2000), kind: 'system' });
      } else if (role === 'user') {
        items.push({ role: 'user', time: 0, summary: content.slice(0, 120), detail: content.slice(0, 2000), kind: 'user' });
      } else if (role === 'assistant') {
        items.push({ role: 'assistant', time: 0, summary: content.slice(0, 120) || 'AI 回复', detail: content.slice(0, 2000), kind: 'assistant' });
        (msg.tool_calls || []).forEach((tc) => {
          const fn = (tc && tc.function) || {};
          items.push({ role: 'tool', time: 0, summary: fn.name || 'tool', detail: String(fn.arguments || '').slice(0, 2000), kind: 'tool' });
        });
      }
    });
    const base = Date.now() / 1000 - Math.max(items.length, 1);
    items.forEach((it, i) => { if (!it.time) it.time = base + i; });
    this._items = items.slice(-this._maxItems);
    this._markers = [];
    this._range = [0, 0];
    this._renderTimeline();
    this._renderFlow();
  },

  clear() {
    this._items = [];
    this._markers = [];
    this._range = [0, 0];
    this._keyword = '';
    this._caseSensitive = false;
    this._roleFilter = new Set();
    // 同步复位搜索框文本/大小写按钮/操作菜单勾选
    const search = $('#trace-search');
    if (search) { search.value = ''; }
    const caseBtn = $('#trace-case');
    if (caseBtn) {
      caseBtn.classList.remove('trace-case-on');
      caseBtn.setAttribute('aria-pressed', 'false');
    }
    const opsMenu = $('#trace-ops-menu');
    if (opsMenu) {
      opsMenu.querySelectorAll('input[type=checkbox]').forEach((cb) => { cb.checked = false; });
    }
    const opsBtn = $('#trace-ops');
    if (opsBtn) opsBtn.textContent = '操作';
    this._renderTimeline();
    this._renderFlow();
    this._renderRangeStat();
  },

  setKeyword(text) {
    this._keyword = String(text || '').trim();
    this._renderFlow();
  },

  toggleCase() {
    this._caseSensitive = !this._caseSensitive;
    const btn = $('#trace-case');
    if (btn) {
      btn.classList.toggle('trace-case-on', this._caseSensitive);
      btn.setAttribute('aria-pressed', this._caseSensitive ? 'true' : 'false');
    }
    this._renderFlow();
  },

  toggleOpRole(role, checked) {
    if (checked) this._roleFilter.add(role);
    else this._roleFilter.delete(role);
    // 更新操作下拉文本
    const opsBtn = $('#trace-ops');
    if (opsBtn) {
      opsBtn.textContent = this._roleFilter.size > 0 ? `操作(${this._roleFilter.size})` : '操作';
    }
    this._renderFlow();
  },

  clearOpFilter() {
    this._roleFilter.clear();
    const menu = $('#trace-ops-menu');
    if (menu) menu.querySelectorAll('input[type=checkbox]').forEach((cb) => { cb.checked = false; });
    const opsBtn = $('#trace-ops');
    if (opsBtn) opsBtn.textContent = '操作';
    this._renderFlow();
  },

  addMarkerAtEnd() {
    if (!this._items.length) return;
    const last = this._items[this._items.length - 1];
    this._markers.push({ time: last.time, idx: this._items.length - 1, note: '' });
    this._renderTimeline();
  },

  /* ---------- 时间线交互 ---------- */
  _onCanvasMouseDown(e) {
    if (!this._items.length) return;
    const rect = e.target.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = this._xToTime(x);
    const idx = this._nearestItemIndex(t);
    if (idx < 0) return;
    // 检查是否点击已有标记
    for (let i = 0; i < this._markers.length; i++) {
      const m = this._markers[i];
      if (Math.abs(m.idx - idx) < 2) {
        this._onMarkerActivated({ marker: m, index: i, item: this._items[idx] });
        return;
      }
    }
    // P2-1 修复：记录 mousedown 起点，双击判定延迟到 mouseup 后
    this._mouseDownInfo = { x: e.clientX, y: e.clientY, t, idx };
    this._dragStart = t;
    this._dragEnd = t;
    this._renderTimeline();
  },

  _onCanvasMouseMove(e) {
    if (this._dragStart === null) return;
    const canvas = $('#trace-timeline');
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    this._dragEnd = this._xToTime(x);
    this._renderTimeline();
  },

  _onCanvasMouseUp(e) {
    if (this._dragStart === null) return;
    const t0 = this._dragStart;
    const t1 = this._dragEnd || t0;
    const info = this._mouseDownInfo;
    this._dragStart = null;
    this._dragEnd = null;
    this._mouseDownInfo = null;
    if (Math.abs(t1 - t0) < 0.001) {
      // P2-1 修复：单击位移 < 5px 才视为有效单击，且用延时避免被 dblclick 重复触发
      if (!info || (Math.abs((e.clientX - info.x)) > 5 || Math.abs((e.clientY - info.y)) > 5)) {
        return;
      }
      // 延时 250ms 后才真新增标记——双击会被 dblclick 提前抢占
      const idx = info.idx;
      const targetTime = this._items[idx].time;
      clearTimeout(this._clickDelayTimer);
      this._clickDelayTimer = setTimeout(() => {
        // 已存在则不重复添加
        const exists = this._markers.some((m) => Math.abs(m.idx - idx) < 1 && Math.abs(m.time - targetTime) < 0.5);
        if (!exists) {
          this._markers.push({ time: targetTime, idx, note: '' });
          this._renderTimeline();
        }
      }, 250);
    } else {
      const lo = Math.min(t0, t1);
      const hi = Math.max(t0, t1);
      this._range = [lo, hi];
      this._renderTimeline();
      this._renderFlow();
      this._renderRangeStat();
    }
  },

  _onCanvasDblClick(e) {
    // P2-1 修复：双击时取消 click delay timer（避免重复新增）
    clearTimeout(this._clickDelayTimer);
    if (!this._items.length) return;
    const rect = e.target.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = this._xToTime(x);
    const idx = this._nearestItemIndex(t);
    if (idx >= 0) {
      const exists = this._markers.some((m) => Math.abs(m.idx - idx) < 1 && Math.abs(m.time - (this._items[idx].time)) < 0.5);
      if (!exists) {
        this._markers.push({ time: this._items[idx].time, idx, note: '' });
        this._renderTimeline();
      }
    }
  },

  _onCanvasContextMenu(e) {
    if (!this._markers.length) return;
    const rect = e.target.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = this._xToTime(x);
    const idx = this._nearestItemIndex(t);
    for (let i = 0; i < this._markers.length; i++) {
      const m = this._markers[i];
      if (Math.abs(m.idx - idx) < 2) {
        e.preventDefault();
        if (confirm('删除该标记？')) {
          this._markers.splice(i, 1);
          this._renderTimeline();
        }
        return;
      }
    }
  },

  _onMarkerActivated(payload) {
    const idx = payload.item ? this._items.indexOf(payload.item) : -1;
    const flow = $('#trace-flow');
    if (flow && idx >= 0) {
      // 滚动到对应卡片（按 idx 比例推算位置）
      const cards = flow.querySelectorAll('.trace-item');
      if (cards[idx]) cards[idx].scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    this.openPreview(payload.item);
  },

  /* ---------- 详情预览 modal（可拖动 + 置顶 + 元素选择引用） ---------- */
  openPreview(item) {
    if (!item) return;
    const modal = $('#trace-modal');
    if (!modal) return;
    const meta = this._roleMeta[item.role] || this._roleMeta.assistant;
    const roleEl = modal.querySelector('.trace-modal-role');
    if (roleEl) { roleEl.textContent = meta.label; roleEl.style.cssText = `color:${meta.color};background:${meta.bg};border-color:${meta.color};`; }
    const summaryEl = modal.querySelector('.trace-modal-summary');
    if (summaryEl) summaryEl.textContent = item.summary || '(无摘要)';
    // 重置拖动位置与置顶状态
    const card = $('#trace-modal-card');
    if (card) { card.style.left = ''; card.style.top = ''; card.style.transform = ''; }
    this._pinned = false;
    this._syncPin();
    // 按元素分块渲染
    this._renderElements(item);
    modal.classList.remove('hidden');
    this._currentItem = item;
    this._zoom = 100;
    this._applyZoom();
  },

  closePreview() {
    const modal = $('#trace-modal');
    if (modal) modal.classList.add('hidden');
    this._currentItem = null;
  },

  zoomIn() {
    const i = this._zoomLevels.indexOf(this._zoom);
    if (i >= 0 && i < this._zoomLevels.length - 1) this._zoom = this._zoomLevels[i + 1];
    this._applyZoom();
  },

  zoomOut() {
    const i = this._zoomLevels.indexOf(this._zoom);
    if (i > 0) this._zoom = this._zoomLevels[i - 1];
    this._applyZoom();
  },

  zoomReset() {
    this._zoom = 100;
    this._applyZoom();
  },

  _applyZoom() {
    const modal = $('#trace-modal');
    if (!modal) return;
    const zoomLbl = modal.querySelector('.trace-modal-zoom');
    if (zoomLbl) zoomLbl.textContent = `${this._zoom}%`;
    modal.querySelectorAll('.trace-modal-elem-body').forEach((b) => {
      b.style.fontSize = `${13 * this._zoom / 100}px`;
    });
    modal.querySelectorAll('.trace-modal-elem-label').forEach((b) => {
      b.style.fontSize = `${12 * this._zoom / 100}px`;
    });
  },

  quoteSelection() {
    if (!this._currentItem) return;
    const sel = window.getSelection();
    const text = sel ? sel.toString().trim() : '';
    const payload = text || this._buildFullText();
    if (payload && typeof App !== 'undefined' && App.addQuote) {
      App.addQuote('轨迹预览', payload);
      this.closePreview();
    }
  },

  quoteElement(key) {
    if (!this._currentItem) return;
    const e = this._elementList(this._currentItem).find((x) => x.key === key);
    if (!e) return;
    if (typeof App !== 'undefined' && App.addQuote) {
      App.addQuote('轨迹预览', `[${e.label}] ${e.text}`);
    }
  },

  togglePin() {
    this._pinned = !this._pinned;
    this._syncPin();
  },

  _syncPin() {
    const modal = $('#trace-modal');
    if (!modal) return;
    modal.classList.toggle('trace-modal-pinned', !!this._pinned);
    const pinBtn = $('#trace-modal-pin');
    if (pinBtn) pinBtn.textContent = this._pinned ? '已置顶' : '置顶';
  },

  _buildFullText() {
    if (!this._currentItem) return '';
    return this._elementList(this._currentItem).map((e) => `[${e.label}] ${e.text}`).join('\n');
  },

  _fmtTime(ts) {
    try {
      const d = new Date(ts * 1000);
      if (isNaN(d.getTime())) return '';
      const p = (n) => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    } catch (e) { return ''; }
  },

  _elementList(item) {
    const meta = this._roleMeta[item.role] || this._roleMeta.assistant;
    const els = [];
    els.push({ key: 'role', label: '角色', text: meta.label });
    if (item.time) {
      const ft = this._fmtTime(item.time);
      if (ft) els.push({ key: 'time', label: '时间', text: ft });
    }
    const kind = String(item.kind || '').trim();
    if (kind) els.push({ key: 'kind', label: '类型', text: kind });
    const summary = String(item.summary || '').trim();
    if (summary) els.push({ key: 'summary', label: '摘要', text: summary });
    const detail = String(item.detail || '').trim();
    if (detail) els.push({ key: 'detail', label: '详情', text: detail });
    return els;
  },

  _renderElements(item) {
    const body = $('#trace-modal-body');
    if (!body) return;
    body.innerHTML = '';
    this._elementList(item).forEach((e) => {
      const block = el('div', 'trace-modal-elem');
      const head = el('div', 'trace-modal-elem-head');
      const label = el('span', 'trace-modal-elem-label');
      label.textContent = e.label;
      head.appendChild(label);
      const quote = el('button', 'btn flatbtn trace-modal-elem-quote', '引用');
      quote.dataset.key = e.key;
      quote.addEventListener('click', () => this.quoteElement(e.key));
      head.appendChild(quote);
      block.appendChild(head);
      const content = el('div', 'trace-modal-elem-body');
      content.textContent = e.text;
      block.appendChild(content);
      body.appendChild(block);
    });
  },

  _endModalDrag() {
    this._dragging = false;
    document.body.style.userSelect = '';
  },

  _initModalDrag() {
    const head = $('#trace-modal-head');
    const card = $('#trace-modal-card');
    if (!head || !card) return;
    head.addEventListener('mousedown', (e) => {
      if (e.target.closest('button')) return;
      e.preventDefault();
      const rect = card.getBoundingClientRect();
      this._dragOffsetX = e.clientX - rect.left;
      this._dragOffsetY = e.clientY - rect.top;
      this._dragging = true;
      document.body.style.userSelect = 'none';
    });
    window.addEventListener('mousemove', (e) => {
      if (!this._dragging) return;
      const c = $('#trace-modal-card');
      if (!c) { this._endModalDrag(); return; }
      if (!(e.buttons & 1)) { this._endModalDrag(); return; }  // 释放丢失兜底
      const keepW = 80, keepH = 48;
      let x = e.clientX - this._dragOffsetX;
      let y = e.clientY - this._dragOffsetY;
      const minX = keepW - c.offsetWidth;
      const minY = keepH - c.offsetHeight;
      const maxX = window.innerWidth - keepW;
      const maxY = window.innerHeight - keepH;
      x = Math.min(Math.max(x, minX), maxX);
      y = Math.min(Math.max(y, minY), maxY);
      c.style.left = `${x}px`;
      c.style.top = `${y}px`;
      c.style.transform = 'none';
    });
    window.addEventListener('mouseup', () => this._endModalDrag());
    window.addEventListener('blur', () => this._endModalDrag());
  },

  /* ---------- 时间线渲染 ---------- */
  _xToTime(x) {
    const canvas = $('#trace-timeline');
    if (!canvas) return 0;
    const w = canvas.clientWidth || 600;
    const usableW = Math.max(w - 24, 1);
    if (!this._items.length) return 0;
    const times = this._items.map((it) => it.time);
    const t0 = Math.min.apply(null, times);
    const t1 = Math.max.apply(null, times);
    const span = Math.max(t1 - t0, 1);
    if (x <= 12) return t0;
    if (x >= 12 + usableW) return t1;
    return t0 + (x - 12) / usableW * span;
  },

  _nearestItemIndex(t) {
    if (!this._items.length) return -1;
    let best = -1, bestDt = Infinity;
    for (let i = 0; i < this._items.length; i++) {
      const dt = Math.abs((this._items[i].time || 0) - t);
      if (dt < bestDt) { bestDt = dt; best = i; }
    }
    return best;
  },

  _renderTimeline() {
    const canvas = $('#trace-timeline');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    canvas.height = Math.max(1, Math.floor((rect.height || 110) * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const w = rect.width || canvas.clientWidth || 600;
    const h = rect.height || 110;
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    ctx.fillStyle = isDark ? '#232428' : '#f8fafc';
    ctx.fillRect(0, 0, w, h);
    const items = this._items;
    const times = items.map((it) => it.time).filter((t) => t);
    const duration = times.length > 1 ? Math.max.apply(null, times) - Math.min.apply(null, times) : 0;
    const turns = items.filter((it) => it.role === 'user').length;
    const calls = items.filter((it) => it.role === 'tool').length;
    const durTxt = duration >= 60
      ? `Duration ${String(Math.floor(duration / 60)).padStart(2, '0')}:${String(Math.floor(duration % 60)).padStart(2, '0')}`
      : `Duration ${Math.floor(duration)}s`;
    const stat = `${durTxt}  ·  Turns ${turns}  ·  Calls ${calls}`;
    ctx.fillStyle = isDark ? '#e8e9ed' : '#374151';
    ctx.font = 'bold 12px var(--sans)';
    ctx.fillText(stat, 12, 22);
    const barTop = 38;
    const barH = Math.max(16, h - 54);
    if (!items.length) {
      ctx.fillStyle = isDark ? '#6c6e78' : '#9ca3af';
      ctx.font = '11px var(--sans)';
      ctx.fillText('暂无轨迹数据（双击时间线加标记 · 拖拽划选区间）', 12, barTop + barH / 2 + 4);
      this._renderLegend(ctx, w, h, isDark);
      return;
    }
    const t0 = Math.min.apply(null, times);
    const t1 = Math.max.apply(null, times);
    const span = Math.max(t1 - t0, 1);
    const usableW = Math.max(w - 24, 1);
    // 区间高亮
    let range = this._range;
    if (this._dragStart !== null && this._dragEnd !== null) {
      range = [Math.min(this._dragStart, this._dragEnd), Math.max(this._dragStart, this._dragEnd)];
    }
    if (range[0] !== range[1]) {
      const x0 = 12 + ((range[0] - t0) / span) * usableW;
      const x1 = 12 + ((range[1] - t0) / span) * usableW;
      ctx.fillStyle = isDark ? 'rgba(255, 213, 79, 0.30)' : 'rgba(255, 213, 79, 0.45)';
      ctx.fillRect(x0, barTop - 2, Math.max(2, x1 - x0), barH + 4);
    }
    const sampled = this._sample(items);
    sampled.forEach((it) => {
      const meta = this._roleMeta[it.role] || this._roleMeta.assistant;
      const x = 12 + ((it.time - t0) / span) * usableW;
      ctx.fillStyle = meta.color;
      const bw = Math.max(4, usableW * 0.015);
      this._roundRect(ctx, x, barTop, bw, barH, 2);
      ctx.fill();
    });
    // 标记点
    this._markers.forEach((m) => {
      const x = 12 + ((m.time - t0) / span) * usableW;
      ctx.fillStyle = '#f59e0b';
      ctx.strokeStyle = '#b45309';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x - 5, barTop - 1);
      ctx.lineTo(x + 5, barTop - 1);
      ctx.lineTo(x, barTop + 9);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    });
    this._renderLegend(ctx, w, h, isDark);
  },

  _renderLegend(ctx, w, h, isDark) {
    let lx = 12;
    const bottom = h - 8;
    ctx.font = '10px var(--sans)';
    ctx.fillStyle = isDark ? '#9b9da6' : '#6b7280';
    Object.entries(this._roleMeta).forEach(([role, meta]) => {
      ctx.fillStyle = meta.color;
      this._roundRect(ctx, lx, bottom - 8, 8, 8, 2);
      ctx.fill();
      ctx.fillStyle = isDark ? '#9b9da6' : '#6b7280';
      ctx.fillText(meta.label, lx + 12, bottom);
      lx += 56;
    });
  },

  _roundRect(ctx, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + width - r, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + r);
    ctx.lineTo(x + width, y + height - r);
    ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
    ctx.lineTo(x + r, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  },

  _sample(items) {
    if (items.length <= this._maxBars) return items;
    const step = items.length / this._maxBars;
    const out = [];
    for (let i = 0; i < this._maxBars; i++) {
      out.push(items[Math.min(Math.floor(i * step), items.length - 1)]);
    }
    if (!out.includes(items[items.length - 1])) out[out.length - 1] = items[items.length - 1];
    return out;
  },

  /* ---------- 区间统计 ---------- */
  _renderRangeStat() {
    const stat = $('#trace-range-stat');
    if (!stat) return;
    const [t0, t1] = this._range;
    if (t0 === 0 && t1 === 0) {
      stat.textContent = '';
      stat.classList.add('hidden');
      return;
    }
    const items = this._items.filter((it) => {
      const t = it.time || 0;
      return t0 <= t && t <= t1;
    });
    const turns = items.filter((it) => it.role === 'user').length;
    const calls = items.filter((it) => it.role === 'tool').length;
    const errors = items.filter((it) => it.kind === 'run_error').length;
    // Top 工具
    const counts = {};
    items.filter((it) => it.role === 'tool' && it.summary).forEach((it) => {
      counts[it.summary] = (counts[it.summary] || 0) + 1;
    });
    const top3 = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([n, c]) => `${n}(${c})`).join(', ') || '-';
    stat.textContent = `区间：${items.length} 条 · Turns ${turns} · Calls ${calls} · Errors ${errors} · Top ${top3}`;
    stat.classList.remove('hidden');
  },

  /* ---------- 消息流渲染 ---------- */
  _renderFlow() {
    const flow = $('#trace-flow');
    if (!flow) return;
    flow.innerHTML = '';
    let items = this._items;
    items = this._applyFilters(items);
    if (!items.length) {
      flow.innerHTML = '<div class="empty-state">' + (this._items.length ? '暂无匹配（请检查筛选条件）' : '暂无轨迹') + '</div>';
      this._renderRangeStat();
      return;
    }
    items.forEach((it) => this._appendFlowCard(it));
    this._renderRangeStat();
  },

  _applyFilters(items) {
    const kws = this._keyword ? this._keyword.split(/\s+/).filter(Boolean) : [];
    return items.filter((it) => {
      if (this._roleFilter.size > 0 && !this._roleFilter.has(it.role)) return false;
      if (this._range[0] !== this._range[1]) {
        const t = it.time || 0;
        if (!(this._range[0] <= t && t <= this._range[1])) return false;
      }
      if (kws.length) {
        const hay = `${it.role} ${it.summary || ''} ${it.detail || ''}`;
        const target = this._caseSensitive ? hay : hay.toLowerCase();
        for (let i = 0; i < kws.length; i++) {
          const kw = this._caseSensitive ? kws[i] : kws[i].toLowerCase();
          if (target.indexOf(kw) === -1) return false;
        }
      }
      return true;
    });
  },

  _appendFlowCard(item) {
    const flow = $('#trace-flow');
    if (!flow) return;
    // v8.13：只要容器里还有空态就清掉再追加（此前 !this._items.length 在首个事件时失效）
    if (flow.querySelector('.empty-state')) flow.innerHTML = '';
    const meta = this._roleMeta[item.role] || this._roleMeta.assistant;
    const card = el('div', 'trace-item');
    card.dataset.search = [item.role, item.summary || '', item.detail || ''].join(' ');
    card.dataset.idx = this._items.indexOf(item);
    if (this._advanced !== false) {
      card.addEventListener('dblclick', () => this.openPreview(item));
    }
    const head = el('div', 'trace-item-head');
    const badge = el('span', 'trace-badge', meta.label);
    badge.style.cssText = `color:${meta.color};background:${meta.bg};border-color:${meta.color};`;
    head.appendChild(badge);
    if (item.time) {
      const d = new Date(item.time * 1000);
      head.appendChild(el('span', 'trace-time', `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`));
    }
    head.appendChild(el('span', 'trace-summary', esc((item.summary || '').slice(0, 120))));
    // 详情按钮（仅高级模式）
    if (this._advanced !== false) {
      const detailBtn = el('button', 'btn flatbtn trace-detail-btn', '详情');
      detailBtn.style.marginLeft = 'auto';
      detailBtn.addEventListener('click', () => this.openPreview(item));
      head.appendChild(detailBtn);
    }
    card.appendChild(head);
    if (item.detail) {
      const body = el('pre', 'trace-detail', esc(item.detail.slice(0, 600)));
      card.appendChild(body);
    }
    flow.appendChild(card);
  },

  /* ---------- 事件映射（与桌面版对齐） ---------- */
  _eventToItem(ev) {
    const t = ev && ev.type;
    const now = Date.now() / 1000;
    switch (t) {
      case 'run_start':
        return { role: 'system', time: now, summary: '会话开始', detail: '', kind: 'run_start' };
      case 'text_delta':
        return { role: 'assistant', time: now, summary: 'AI 输出', detail: String(ev.content || '').slice(0, 2000), kind: 'text_delta' };
      case 'tool_start': {
        let argText = '';
        try { argText = ev.args != null ? JSON.stringify(ev.args) : ''; }
        catch (e) { argText = String(ev.args || ''); }
        return { role: 'tool', time: now, summary: ev.name || 'tool', detail: argText.slice(0, 2000), kind: 'tool_start' };
      }
      case 'tool_result':
        return { role: 'tool', time: now, summary: (ev.name || 'tool') + ' 完成', detail: String(ev.output || '').slice(0, 2000), kind: 'tool_result' };
      case 'expert_plan_start':
      case 'expert_plan':
        return { role: 'context', time: now, summary: '总司令规划', detail: String(ev.summary || '').slice(0, 2000), kind: 'expert_plan' };
      case 'guard_alert':
        return { role: 'system', time: now, summary: '报警', detail: String(ev.reason || '').slice(0, 2000), kind: 'guard_alert' };
      case 'drift_merged':
        return { role: 'system', time: now, summary: '算力漂移合并', detail: `追加 ${ev.added || 0} 条消息`, kind: 'drift_merged' };
      case 'run_done':
      case 'run_final':
        return { role: 'system', time: now, summary: '会话完成', detail: '', kind: 'run_final' };
      case 'run_error':
        return { role: 'system', time: now, summary: '运行出错', detail: String(ev.message || '').slice(0, 2000), kind: 'run_error' };
      case 'run_cancelled':
        return { role: 'system', time: now, summary: '已停止', detail: String(ev.message || '').slice(0, 2000), kind: 'run_cancelled' };
      default:
        return null;
    }
  },
};

/* 窗口尺寸变化时重绘时间线，保证 canvas 清晰（防抖） */
let _traceResizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(_traceResizeTimer);
  _traceResizeTimer = setTimeout(() => {
    if (TraceView && typeof TraceView._renderTimeline === 'function') TraceView._renderTimeline();
  }, 100);
});

/* 详情 modal 关闭与缩放快捷键：ESC 关闭，Ctrl+=/Ctrl+-/Ctrl+0 缩放 */
window.addEventListener('keydown', (e) => {
  const modal = document.getElementById('trace-modal');
  if (!modal || modal.classList.contains('hidden')) return;
  if (e.key === 'Escape') {
    if (TraceView) TraceView.closePreview();
    return;
  }
  if (!(e.ctrlKey || e.metaKey)) return;
  if (e.key === '=' || e.key === '+') {
    e.preventDefault();
    if (TraceView) TraceView.zoomIn();
  } else if (e.key === '-' || e.key === '_') {
    e.preventDefault();
    if (TraceView) TraceView.zoomOut();
  } else if (e.key === '0') {
    e.preventDefault();
    if (TraceView) TraceView.zoomReset();
  }
});