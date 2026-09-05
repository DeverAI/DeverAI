/* ============ 编辑器：Monaco + 降级 textarea + 标签页 ============ */
const Editor = {
  monaco: null,
  instance: null,
  monacoReady: false,
  fallback: false,
  models: {},   // rel -> monaco model
  tabs: [],     // {rel, dirty, viewState}
  active: null, // rel
  _suppressChange: false, // v8.13：程序性 setValue 不触发 dirty
};

const LANG_MAP = {
  py: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript', ts: 'typescript',
  tsx: 'typescript', jsx: 'javascript', html: 'html', htm: 'html', css: 'css', scss: 'scss',
  less: 'less', json: 'json', md: 'markdown', yaml: 'yaml', yml: 'yaml', toml: 'ini',
  sh: 'shell', bat: 'bat', ps1: 'powershell', sql: 'sql', xml: 'xml', java: 'java',
  c: 'c', h: 'c', cpp: 'cpp', go: 'go', rs: 'rust', rb: 'ruby', php: 'php', cs: 'csharp',
  vue: 'html', svelte: 'html', dockerfile: 'dockerfile', ini: 'ini', txt: 'plaintext',
};

function initEditor() {
  const tryMonaco = () => {
    if (window.require && window.MONACO_URL) {
      try {
        window.require.config({ paths: { vs: window.MONACO_URL } });
        window.require(['vs/editor/editor.main'], () => {
          if (Editor.monacoReady) return;
          Editor.monaco = window.monaco;
          Editor.monacoReady = true;
          Editor.monaco.editor.defineTheme('deverai-dark', {
            base: 'vs-dark', inherit: true,
            rules: [
              { token: 'comment', foreground: '5c6774', fontStyle: 'italic' },
              { token: 'keyword', foreground: 'c792ea' },
              { token: 'string', foreground: 'c3e88d' },
              { token: 'number', foreground: 'f78c6c' },
              { token: 'type', foreground: '82aaff' },
              { token: 'function', foreground: '82aaff' },
            ],
            colors: {
              'editor.background': '#0d1117',
              'editor.foreground': '#dbe2f0',
              'editor.lineHighlightBackground': '#141b2b',
              'editorLineNumber.foreground': '#3d4a61',
              'editorLineNumber.activeForeground': '#8a94a8',
              'editor.selectionBackground': '#264f78',
              'editorIndentGuide.background1': '#1c2332',
              'editorIndentGuide.activeBackground1': '#2e3a50',
              'editorWidget.background': '#11161f',
              'editorWidget.border': '#232c3d',
            },
          });
          Editor.monaco.editor.setTheme('deverai-dark');
          Editor.instance = Editor.monaco.editor.create($('#editor'), {
            theme: 'deverai-dark', automaticLayout: true,
            fontSize: 13.5, fontFamily: "'Cascadia Code', Consolas, monospace",
            minimap: { enabled: false }, scrollBeyondLastLine: false,
            wordWrap: 'off', tabSize: 4, renderWhitespace: 'none',
            bracketPairColorization: { enabled: true }, padding: { top: 10 },
          });
          Editor.instance.onDidChangeModelContent(() => {
            if (Editor._suppressChange) return; // v8.13：程序性 setValue 不误标 dirty
            if (Editor.active && Editor.models[Editor.active]) {
              const t = Editor.tabs.find((x) => x.rel === Editor.active);
              if (t && !t.dirty) { t.dirty = true; renderTabbar(); }
            }
          });
          $('#editor-fallback').classList.add('hidden');
          $('#editor').classList.remove('hidden');
          Editor.pending && Object.values(Editor.pending).forEach((p) => applyModel(p.rel, p.content));
          Editor.pending = {};
        });
      } catch (e) {
        enableFallback();
      }
    } else {
      enableFallback();
    }
  };
  Editor.pending = {};
  tryMonaco();
  setTimeout(() => { if (!Editor.monacoReady && !Editor.fallback) enableFallback(); }, 8000);
}

function enableFallback() {
  if (Editor.fallback) return;
  Editor.fallback = true;
  const fb = $('#fallback-editor');
  $('#editor-fallback').classList.remove('hidden');
  $('#editor').classList.add('hidden');
  fb.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const s = fb.selectionStart, en = fb.selectionEnd;
      fb.value = fb.value.slice(0, s) + '    ' + fb.value.slice(en);
      fb.selectionStart = fb.selectionEnd = s + 4;
    }
  });
  fb.addEventListener('input', () => {
    if (Editor.active) {
      const t = Editor.tabs.find((x) => x.rel === Editor.active);
      if (t && !t.dirty) { t.dirty = true; renderTabbar(); }
    }
  });
}

function applyModel(rel, content) {
  if (!Editor.monacoReady || Editor.fallback) return;
  Editor._suppressChange = true;
  try {
    if (!Editor.models[rel]) {
      const lang = LANG_MAP[extOf(rel)] || 'plaintext';
      const model = Editor.monaco.editor.createModel(content || '', lang);
      Editor.models[rel] = model;
    } else {
      Editor.models[rel].setValue(content || '');
    }
  } finally {
    Editor._suppressChange = false;
  }
  // v8.13：模型已装载即释放 pending 副本，长期打开大量文件不积压内容
  if (Editor.pending) delete Editor.pending[rel];
}

async function openTab(rel) {
  // v8.13：已打开文件的保护——当前激活 tab 再次点击不重载（保留未保存编辑）；
  // 其它 dirty tab 必须先经用户确认才允许丢弃未保存修改重新打开
  const existing = Editor.tabs.find((t) => t.rel === rel);
  if (existing) {
    if (Editor.active === rel) {
      setActiveTab(rel);
      return;
    }
    if (existing.dirty && !confirm(`文件 ${rel} 有未保存修改，重新打开将丢弃这些修改。继续？`)) {
      return;
    }
  }
  let data;
  try {
    data = await readFile(rel);
  } catch (e) {
    toast('无法打开文件: ' + e.message, 'err');
    return;
  }
  if (!existing) {
    Editor.tabs.push({ rel, dirty: false });
  } else {
    existing.dirty = false;
  }
  Editor.pending = Editor.pending || {};
  Editor.pending[rel] = { rel, content: data.content };
  if (Editor.monacoReady && !Editor.fallback) {
    applyModel(rel, data.content);
  } else if (Editor.fallback) {
    $('#fallback-editor').value = data.content;
  }
  setActiveTab(rel);
  App.currentFile = rel;
  if (Editor.monacoReady && !Editor.fallback && Editor.models[rel]) {
    Editor.instance.setModel(Editor.models[rel]);
    if (Editor.tabs.find((x) => x.rel === rel).viewState) {
      Editor.instance.restoreViewState(Editor.tabs.find((x) => x.rel === rel).viewState);
    }
  }
  // v8.5.x 审查修复：#editor-welcome 不存在（空指针），且 #editor-host 恒 hidden 导致编辑器不可见；
  // 打开文件时显示编辑器容器
  $('#editor-host').classList.remove('hidden');
  renderTabbar();
}

function setActiveTab(rel) {
  Editor.active = rel;
  renderTabbar();
}

async function saveActiveTab() {
  if (!Editor.active) return;
  let content;
  if (Editor.monacoReady && !Editor.fallback && Editor.models[Editor.active]) {
    content = Editor.models[Editor.active].getValue();
  } else if (Editor.fallback) {
    content = $('#fallback-editor').value;
  } else {
    return;
  }
  // v8.26 两端统一 WorkTree 保护：保存前先快照磁盘原文件（source=human，与桌面对齐）。
  // 不设 dirty 条件——外部程序改过磁盘而标签未 dirty 时同样兜底；后端直读，幂等低成本。
  await api('/api/bridge/checkpoint/save', {
    method: 'POST',
    body: { path: Editor.active, content: '', source: 'human' },
  }).catch(() => toast('保存前快照失败（已继续保存，本次无版本快照）', 'err'));
  try {
    await writeFile(Editor.active, content);
    const t = Editor.tabs.find((x) => x.rel === Editor.active);
    if (t) t.dirty = false;
    if (Editor.monacoReady && !Editor.fallback && Editor.models[Editor.active]) {
      const vs = Editor.instance.saveViewState();
      t && (t.viewState = vs);
    }
    renderTabbar();
    toast('已保存 ' + basename(Editor.active), 'ok');
    Tree.refresh();
  } catch (e) {
    toast('保存失败: ' + e.message, 'err');
  }
}

function closeTab(rel) {
  const idx = Editor.tabs.findIndex((t) => t.rel === rel);
  if (idx === -1) return;
  const t = Editor.tabs[idx];
  // v8.13：dirty 标签关闭前必须确认，未保存修改不再被静默丢弃
  if (t.dirty && !confirm(`文件 ${rel} 有未保存修改，关闭将丢弃这些修改。继续？`)) return;
  Editor.tabs.splice(idx, 1);
  if (Editor.models[rel]) { Editor.models[rel].dispose(); delete Editor.models[rel]; }
  if (Editor.active === rel) {
    if (Editor.monacoReady && !Editor.fallback) Editor.instance.setModel(null);
    if (Editor.tabs.length) {
      const next = Editor.tabs[Math.min(idx, Editor.tabs.length - 1)];
      Editor.active = null;
      renderTabbar();
      const model = (Editor.monacoReady && !Editor.fallback) ? Editor.models[next.rel] : null;
      $('#editor-host').classList.remove('hidden');
      if (model) {
        // v8.14：目标标签已装载模型时直接切换，不走 openTab 重读盘——
        // 此前若该标签是 dirty 且用户在确认框取消，active 仍被置为目标，
        // 造成「高亮标签与编辑器内容不一致」的错位
        Editor.instance.setModel(model);
        if (next.viewState) Editor.instance.restoreViewState(next.viewState);
        Editor.active = next.rel;
        App.currentFile = next.rel;
        renderTabbar();
      } else {
        // 未装载（fallback 或首次打开）：交给 openTab；失败/取消时不强行指 active
        openTab(next.rel).catch(() => { renderTabbar(); });
      }
    } else {
      Editor.active = null;
      // v8.5.x 审查修复：#editor-welcome 不存在；关闭最后一个标签时隐藏编辑器容器
      $('#editor-host').classList.add('hidden');
      if (Editor.monacoReady && !Editor.fallback) Editor.instance.setModel(null);
      if (Editor.fallback) $('#fallback-editor').value = '';
      renderTabbar();
    }
  } else {
    renderTabbar();
  }
  if (Editor.active === rel) App.currentFile = null;
  else App.currentFile = Editor.active;
}

function renderTabbar() {
  const bar = $('#tabbar');
  bar.innerHTML = '';
  Editor.tabs.forEach((t) => {
    const tab = el('div', 'tab' + (t.rel === Editor.active ? ' active' : ''));
    const dirty = el('span', 'tab-dirty' + (t.dirty ? '' : ' hidden'));
    tab.appendChild(dirty);
    tab.appendChild(el('span', 'tab-name', esc(basename(t.rel))));
    const close = el('span', 'tab-close', '✕');
    close.onclick = (e) => { e.stopPropagation(); closeTab(t.rel); };
    tab.appendChild(close);
    tab.onclick = () => {
      if (Editor.active === t.rel) return;
      if (Editor.monacoReady && !Editor.fallback && Editor.models[t.rel]) {
        const prev = Editor.tabs.find((x) => x.rel === Editor.active);
        if (prev) prev.viewState = Editor.instance.saveViewState();
        Editor.instance.setModel(Editor.models[t.rel]);
        if (t.viewState) Editor.instance.restoreViewState(t.viewState);
      } else if (Editor.fallback) {
        // v8.5.x 审查修复：切换前把当前 tab 的未保存编辑写回 pending，避免数据丢失
        if (Editor.active) {
          Editor.pending = Editor.pending || {};
          Editor.pending[Editor.active] = { rel: Editor.active, content: $('#fallback-editor').value };
        }
        $('#fallback-editor').value = (Editor.pending && Editor.pending[t.rel] && Editor.pending[t.rel].content) || '';
      }
      setActiveTab(t.rel);
      App.currentFile = t.rel;
    };
    bar.appendChild(tab);
  });
  // v8.26 两端统一：编辑器内版本历史入口（复用 chat.js openVersionDialog /checkpoint/versions）
  if (Editor.active && typeof openVersionDialog === 'function') {
    const hist = el('span', 'tab tab-hist', '历史');
    hist.title = '查看/恢复该文件的版本快照（WorkTree）';
    hist.onclick = () => openVersionDialog(Editor.active);
    bar.appendChild(hist);
  }
}
