/* ============ DeverAI v8.16 多会话标签条 ============
 * 复用 v8.13 任务存储（localStorage 任务索引 + IndexedDB 分会话消息）。
 * 标签 = 「打开态」视图：关闭标签仅从打开集合移除，不删任务与消息；
 * 会话删除仍走左栏管理入口（Design 决策 117）。
 * 开关 ENABLE_MULTI_SESSION=false 时整条隐藏，回到单会话行为。
 * 依赖（均运行时解析）：App / switchTaskHandler / toast（core.js 与 main.js 全局）。
 */
(function () {
  const OPEN_KEY = 'deverai.v2.open_tasks';

  function loadOpen() {
    try {
      const raw = JSON.parse(localStorage.getItem(OPEN_KEY) || '[]');
      if (Array.isArray(raw) && raw.every((x) => typeof x === 'string')) return raw;
    } catch (e) { /* 隐私模式等场景忽略 */ }
    return [];
  }

  function saveOpen(list) {
    try { localStorage.setItem(OPEN_KEY, JSON.stringify(list)); } catch (e) {}
  }

  function enabled() {
    return !window.App || !App.config || App.config.ENABLE_MULTI_SESSION !== false;
  }

  /* 打开集合 = 持久化列表 ∩ 现存任务；活跃会话恒在。 */
  function openList() {
    const tasks = (App && App.tasks) || [];
    let open = loadOpen().filter((id) => tasks.some((t) => t.id === id));
    const activeId = App && App.taskId;
    if (!open.length && activeId) open.push(activeId);
    if (activeId && !open.includes(activeId)) open.unshift(activeId);
    return open;
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function onCloseTab(taskId, ev) {
    ev.stopPropagation();
    // 使用与渲染一致的归一化列表（含活跃回填）；直接读存储会在
    // "活跃会话尚未持久化进打开集合"时误判为最后一个标签
    const open = openList();
    const idx = open.indexOf(taskId);
    if (idx < 0 || open.length <= 1) { toast('最后一个标签不可关闭', 'err'); render(); return; }
    // 关闭活跃标签 → 先切到相邻打开的会话（优先右侧），再移除
    saveOpen(open.filter((x) => x !== taskId));
    if (taskId === App.taskId) {
      const neighbor = open[idx + 1] || open[idx - 1];
      if (neighbor && typeof switchTaskHandler === 'function') switchTaskHandler(neighbor);
      else render();
      return;
    }
    render();
  }

  function onNewSession() {
    // 复用左栏「新建任务」入口（showPrompt + createTask + restoreChat 同链路）
    const btn = document.getElementById('btn-new-task');
    if (btn) btn.click();
    else if (typeof toast === 'function') toast('新建任务入口未就绪', 'err');
  }

  function render() {
    const bar = document.getElementById('session-tabs');
    if (!bar) return;
    if (!enabled() || !Array.isArray((window.App || {}).tasks)) {
      bar.classList.add('hidden');
      return;
    }
    bar.classList.remove('hidden');
    bar.innerHTML = '';
    const ids = openList();
    ids.forEach((id) => {
      const t = App.tasks.find((x) => x.id === id);
      if (!t) return;
      const pill = el('div', 'session-tab' + (t.id === App.taskId ? ' active' : ''));
      pill.title = t.name + (t.updated_at ? ' · ' + t.updated_at : '');
      const label = el('span', 'st-label', t.name);
      label.onclick = () => { if (typeof switchTaskHandler === 'function') switchTaskHandler(t.id); };
      const closeB = el('span', 'st-close', '\u00d7');
      closeB.title = '关闭标签（会话保留在左栏）';
      closeB.onclick = (ev) => onCloseTab(t.id, ev);
      pill.appendChild(label);
      pill.appendChild(closeB);
      bar.appendChild(pill);
    });
    const plus = el('button', 'st-new', '+');
    plus.type = 'button';
    plus.title = '新建会话';
    plus.onclick = onNewSession;
    bar.appendChild(plus);
    saveOpen(ids);   // 渲染即持久化归一化后的打开集合，保证存储与视图锁步
  }

  window.Sessions = { render, openList };
})();
