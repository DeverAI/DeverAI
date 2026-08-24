/* ============ 文件树（基于 fs.js 抽象层） ============ */
const Tree = {
  root: '',
  openDirs: new Set(),
  lastRowRel: '',
  lastRowIsDir: false,
  refresh() { return refreshTree(); },
};

async function initTree() {
  const name = FS.rootName || '未授权';
  const wsName = $('#ws-name');
  if (wsName) wsName.textContent = name;
  const wsNameRight = $('#ws-name-right');
  if (wsNameRight) wsNameRight.textContent = name;
  const wsBar = $('#ws-bar');
  if (wsBar) wsBar.title = `文件后端: ${fsModeLabel()}`;
  if (fsWorkspaceReady()) await refreshTree();
}

async function refreshTree() {
  const box = $('#file-tree');
  box.innerHTML = '';
  if (!fsWorkspaceReady()) {
    box.innerHTML = '<div class="tree-row" style="color:var(--text-faint)">' + icon('folder', 14) + ' 点击上方授权工作区后开始</div>';
    return;
  }
  await loadDir(Tree.root, box);
}

async function loadDir(rel, container) {
  let entries;
  try {
    entries = await listDir(rel);
  } catch (e) {
    container.innerHTML = '<div class="tree-row">' + esc(e.message) + '</div>';
    return;
  }
  const dirs = entries.filter((c) => c.is_dir);
  const files = entries.filter((c) => !c.is_dir);
  [...dirs, ...files].forEach((item) => {
    const relPath = rel ? `${rel}/${item.name}` : item.name;
    const isOpen = Tree.openDirs.has(relPath);
    const node = el('div', 'tree-node');
    const row = el('div', 'tree-row');
    row.dataset.rel = relPath;
    const arrow = el('span', 'tree-arrow' + (item.is_dir ? (isOpen ? ' open' : '') : ' leaf'), item.is_dir ? '▶' : '');
    row.appendChild(arrow);
    row.appendChild(el('span', 'tree-icon', item.is_dir ? icon('folder', 13) : fileIcon(relPath)));
    const nameEl = el('span', '', esc(item.name));
    row.appendChild(nameEl);
    if (!item.is_dir) {
      const sizeEl = el('span', 'tree-size');
      sizeEl.style.cssText = 'margin-left:auto;font-size:10px;color:#5c667a;padding-right:8px;';
      sizeEl.textContent = fmtBytes(item.size);
      row.appendChild(sizeEl);
    }
    node.appendChild(row);
    row.addEventListener('click', () => { Tree.lastRowRel = relPath; Tree.lastRowIsDir = item.is_dir; });
    if (item.is_dir) {
      const children = el('div', 'tree-children' + (isOpen ? '' : ' hidden'));
      node.appendChild(children);
      arrow.onclick = (e) => { e.stopPropagation(); toggleDir(relPath, arrow, children); };
      nameEl.onclick = (e) => { e.stopPropagation(); toggleDir(relPath, arrow, children); };
      // v8.13：目录行整行可点击展开/折叠（此前空白区域点击无反应，半死控件）
      row.onclick = (e) => { e.stopPropagation(); toggleDir(relPath, arrow, children); };
      if (isOpen) loadDir(relPath, children).catch(() => {});
    } else {
      row.onclick = () => openTab(relPath).catch((e) => toast('打开失败: ' + e.message, 'err'));
    }
    row.oncontextmenu = (e) => {
      e.preventDefault();
      e.stopPropagation();
      showCtxMenu(e.clientX, e.clientY, relPath, item.is_dir);
    };
    container.appendChild(node);
  });
}

async function toggleDir(rel, arrow, children) {
  if (Tree.openDirs.has(rel)) {
    Tree.openDirs.delete(rel);
    arrow.classList.remove('open');
    children.classList.add('hidden');
  } else {
    Tree.openDirs.add(rel);
    arrow.classList.add('open');
    children.classList.remove('hidden');
    if (!children.dataset.loaded) {
      await loadDir(rel, children);
      children.dataset.loaded = '1';
    }
  }
}

/* ---------- 右键菜单 ---------- */
function showCtxMenu(x, y, rel, isDir) {
  const menu = $('#ctx-menu');
  menu.innerHTML = '';
  const items = [];
  // v8.13：目录的「打开」=展开目录（此前是死项）
  items.push({ label: '打开', action: () => {
    if (isDir) { Tree.openDirs.add(rel); refreshTree().catch(() => {}); }
    else openTab(rel).catch((e) => toast('打开失败: ' + e.message, 'err'));
  }, danger: false });
  items.push({ label: '新建文件', action: () => promptPath('新建文件（相对路径）', rel, isDir, false), danger: false });
  items.push({ label: '新建文件夹', action: () => promptPath('新建文件夹（相对路径）', rel, isDir, true), danger: false });
  items.push({ label: '重命名', action: () => promptRename(rel), danger: false });
  items.push({ sep: true });
  items.push({ label: '删除', action: () => promptDelete(rel), danger: true });
  items.push({ label: '复制相对路径', action: () => { navigator.clipboard && navigator.clipboard.writeText(rel); toast('已复制路径'); }, danger: false });
  items.forEach((it) => {
    if (it.sep) { menu.appendChild(el('div', 'ctx-sep')); return; }
    const d = el('div', 'ctx-item' + (it.danger ? ' danger' : ''), esc(it.label));
    d.onclick = () => { hideCtxMenu(); it.action(); };
    menu.appendChild(d);
  });
  menu.classList.remove('hidden');
  menu.style.left = Math.min(x, window.innerWidth - 180) + 'px';
  menu.style.top = Math.min(y, window.innerHeight - 200) + 'px';
}
function hideCtxMenu() { $('#ctx-menu').classList.add('hidden'); }

function promptPath(title, ctx, isDir, makeDir) {
  const base = isDir ? ctx : dirname(ctx);
  showPrompt(title, base ? base + '/' : '', async (val) => {
    const rel = normPath(val || '');
    if (!rel) return;
    try {
      if (makeDir) await mkdir(rel);
      else await writeFile(rel, '');
      Tree.openDirs.add(base || '');
      await refreshTree();
      if (!makeDir) openTab(rel).catch(() => {});
    } catch (e) { toast('创建失败: ' + e.message, 'err'); }
  });
}

function promptRename(rel) {
  showPrompt('重命名', basename(rel), async (val) => {
    const name = String(val || '').trim();
    if (!name || name === basename(rel)) return;
    const newRel = dirname(rel) ? dirname(rel) + '/' + name : name;
    try {
      await renameEntry(rel, newRel);
      await refreshTree();
    } catch (e) { toast('重命名失败: ' + e.message, 'err'); }
  });
}

function promptDelete(rel) {
  showPrompt(`删除「${basename(rel)}」？输入 yes 确认删除`, '', async (val) => {
    if (String(val || '').trim().toLowerCase() !== 'yes') { toast('已取消删除'); return; }
    try {
      await deleteEntry(rel);
      if (Editor.tabs.find((t) => t.rel === rel)) closeTab(rel);
      await refreshTree();
      toast('已删除', 'ok');
    } catch (e) { toast('删除失败: ' + e.message, 'err'); }
  });
}
