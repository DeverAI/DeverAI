/* ============ fs.js 文件系统抽象层 ============
 * 两种后端，透明切换：
 *  1) fss    —— File System Access API（Chrome/Edge 86+）：浏览器直接读写用户授权目录。
 *  2) bridge —— 本地资源桥 /api/bridge/fs/*（无 FSS 时的兜底，走服务端读文件）。
 * 命令执行/系统对话框永远走 bridge（浏览器无法直接做）。
 */
const FS = {
  mode: null,            // 'fss' | 'bridge' | null
  rootHandle: null,      // FileSystemDirectoryHandle
  rootName: '',          // 工作区显示名
  bridge: { authorized: false, workspace: '', allowAiDelete: false },
};

const _HANDLE_CACHE = new Map(); // rel -> FileSystemDirectoryHandle

/* v8.13.1：FSS 模式下在浏览器侧执行与 bridge/desktop 同源的系统目录保护。
 * FSS 无法获知绝对路径，因此按相对路径首层/文件名执行（对任意授权目录同名
 * 系统目录同样生效，宁可误拦、不可放行密钥与运行数据）。 */
const FSS_READ_PROTECTED_TOP = ['data', 'backups'];
const FSS_WRITE_PROTECTED_TOP = ['app', 'data', 'backups', 'dev_log', 'updates', 'desktop', 'static'];
const FSS_PROTECTED_FILES = ['Err.log', 'config.json'];

function _fsPathInfo(rel) {
  const key = normPath(rel || '');
  const top = key.split('/').filter(Boolean)[0] || '';
  const name = key.split('/').filter(Boolean).pop() || '';
  return { key, top, name };
}
function _assertFsPath(rel, mode) {
  const { key, top, name } = _fsPathInfo(rel);
  if (!key) return;
  if (FSS_PROTECTED_FILES.includes(name)) {
    throw new Error(`路径 ${name} 属于受保护文件，禁止${mode === 'write' ? '修改' : '读取'}。`);
  }
  if (mode === 'read' && FSS_READ_PROTECTED_TOP.includes(top)) {
    throw new Error(`目录 ${top}/ 属于受保护数据，禁止读取。`);
  }
  if (mode === 'write' && FSS_WRITE_PROTECTED_TOP.includes(top)) {
    throw new Error(`目录 ${top}/ 属于系统保护目录，禁止写入。`);
  }
}

/* ---------- 授权 ---------- */
async function pickWorkspace() {
  // 优先 File System Access API
  if (window.showDirectoryPicker) {
    try {
      const handle = await window.showDirectoryPicker({ mode: 'readwrite' });
      FS.mode = 'fss';
      FS.rootHandle = handle;
      FS.rootName = handle.name || '工作区';
      _HANDLE_CACHE.clear();
      _HANDLE_CACHE.set('', handle);
      try { await IDB.put('fs-root', { id: 'root', handle, ts: Date.now() }); } catch (e) {}
      return { ok: true, mode: 'fss', name: FS.rootName };
    } catch (e) {
      if (e && e.name === 'AbortError') return { ok: false, message: '已取消选择' };
      // 降级：尝试桥
    }
  }
  return pickBridge();
}

async function pickBridge() {
  try {
    const r = await api('/api/bridge/pick');
    if (r.ok) {
      FS.mode = 'bridge';
      FS.rootName = basename(r.workspace) || '工作区';
      FS.bridge = { authorized: true, workspace: r.workspace, allowAiDelete: FS.bridge.allowAiDelete };
      return { ok: true, mode: 'bridge', workspace: r.workspace };
    }
    return { ok: false, message: r.message || '未选择' };
  } catch (e) {
    return { ok: false, message: '无法打开系统对话框: ' + e.message };
  }
}

async function refreshBridgeState() {
  try {
    const r = await api('/api/bridge/workspace');
    FS.bridge = {
      authorized: !!r.authorized,
      workspace: r.workspace || '',
      allowAiDelete: !!r.allow_ai_delete,
    };
  } catch (e) {
    FS.bridge = { authorized: false, workspace: '', allowAiDelete: false };
  }
  return FS.bridge;
}

/* 启动时尝试恢复已授权的 FSS 目录句柄（IndexedDB 中保存） */
async function restoreFsRoot() {
  try {
    const db = await IDB._open();
    const rec = await new Promise((resolve) => {
      const tx = db.transaction('fs-root', 'readonly');
      const req = tx.objectStore('fs-root').get('root');
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => resolve(null);
    });
    if (rec && rec.handle) {
      const perm = await rec.handle.queryPermission({ mode: 'readwrite' });
      if (perm === 'granted') {
        FS.mode = 'fss';
        FS.rootHandle = rec.handle;
        FS.rootName = rec.handle.name || '工作区';
        _HANDLE_CACHE.clear();
        _HANDLE_CACHE.set('', rec.handle);
        return true;
      }
    }
  } catch (e) { /* ignore */ }
  return false;
}

function fsWorkspaceReady() {
  return (FS.mode === 'fss' && FS.rootHandle) || (FS.mode === 'bridge' && FS.bridge.authorized);
}
function fsModeLabel() {
  return FS.mode === 'fss' ? '浏览器直连' : FS.mode === 'bridge' ? '命令桥' : '未授权';
}

/* ---------- 路径句柄解析 ---------- */
async function _getDirHandle(rel) {
  const key = normPath(rel || '');
  if (_HANDLE_CACHE.has(key)) return _HANDLE_CACHE.get(key);
  const parts = key.split('/').filter(Boolean);
  let h = FS.rootHandle;
  for (const part of parts) {
    try {
      h = await h.getDirectoryHandle(part);
    } catch (e) {
      return null;
    }
  }
  _HANDLE_CACHE.set(key, h);
  return h;
}
async function _getFileHandle(rel) {
  const dir = await _getDirHandle(dirname(rel));
  if (!dir) throw new Error('目录不存在: ' + (dirname(rel) || '/'));
  try {
    return await dir.getFileHandle(basename(rel));
  } catch (e) {
    throw new Error('文件不存在: ' + rel);
  }
}

/* ---------- 目录 ---------- */
async function listDir(rel) {
  const key = normPath(rel || '');
  if (FS.mode === 'fss') {
    _assertFsPath(key, 'read'); // v8.13.1：data/backups 不可列
    const dir = await _getDirHandle(key);
    if (!dir) throw new Error('目录不存在: ' + (key || '/'));
    const entries = [];
    for await (const [name, handle] of dir.entries()) {
      const childRel = key ? `${key}/${name}` : name;
      // 根目录/子目录列表隐藏受保护数据项
      const info = _fsPathInfo(childRel);
      if (FSS_READ_PROTECTED_TOP.includes(info.top) || FSS_PROTECTED_FILES.includes(info.name)) continue;
      let size = 0;
      if (handle.kind === 'file') {
        try { const f = await handle.getFile(); size = f.size; } catch (e) {}
      }
      entries.push({ name, is_dir: handle.kind === 'directory', size });
    }
    entries.sort((a, b) => (a.is_dir === b.is_dir ? a.name.localeCompare(b.name) : a.is_dir ? -1 : 1));
    return entries;
  }
  // bridge
  const data = await api(`/api/bridge/fs/tree?path=${encodeURIComponent(key)}`);
  return data.children || [];
}

/* ---------- 文件读写 ---------- */
async function readFile(rel) {
  const key = normPath(rel);
  if (FS.mode === 'fss') {
    _assertFsPath(key, 'read'); // v8.13.1：config.json/Err.log/data/backups 不可读
    const fh = await _getFileHandle(key);
    const file = await fh.getFile();
    if (file.size > 2 * 1024 * 1024) throw new Error('文件超过 2MB，编辑器不支持打开');
    return { ok: true, content: await file.text(), size: file.size };
  }
  const data = await api(`/api/bridge/fs/read?path=${encodeURIComponent(key)}`);
  return data;
}

async function writeFile(rel, content) {
  const key = normPath(rel);
  if (FS.mode === 'fss') {
    _assertFsPath(key, 'write'); // v8.13.1：源码/数据目录仍不可写
    const dir = await _getDirHandle(dirname(key));
    if (!dir) throw new Error('目录不存在: ' + (dirname(key) || '/'));
    let fh;
    try {
      fh = await dir.getFileHandle(basename(key), { create: true });
    } catch (e) {
      throw new Error('无法创建文件: ' + key);
    }
    const writable = await fh.createWritable();
    await writable.write(String(content == null ? '' : content));
    await writable.close();
    return { ok: true };
  }
  await api('/api/bridge/fs/write', { method: 'POST', body: { path: key, content: String(content == null ? '' : content) } });
  return { ok: true };
}

async function mkdir(rel) {
  const key = normPath(rel);
  if (FS.mode === 'fss') {
    _assertFsPath(key, 'write'); // v8.13.1：源码/数据目录不可创建
    const parent = await _getDirHandle(dirname(key));
    if (!parent) throw new Error('父目录不存在');
    await parent.getDirectoryHandle(basename(key), { create: true });
    return { ok: true };
  }
  await api('/api/bridge/fs/mkdir', { method: 'POST', body: { path: key } });
  return { ok: true };
}

async function deleteEntry(rel) {
  const key = normPath(rel);
  if (FS.mode === 'fss') {
    _assertFsPath(key, 'write'); // v8.13.1：受保护目录不可删
    const parent = await _getDirHandle(dirname(key));
    if (!parent) throw new Error('父目录不存在');
    const name = basename(key);
    const dir = await _getDirHandle(key);
    await parent.removeEntry(name, { recursive: !!dir });
    // v8.13：目录删除后清理该目录及全部子路径缓存（重建同名树不会命中旧句柄）
    for (const k of Array.from(_HANDLE_CACHE.keys())) {
      if (k === key || k.startsWith(key + '/')) _HANDLE_CACHE.delete(k);
    }
    return { ok: true };
  }
  await api('/api/bridge/fs/delete', { method: 'POST', body: { path: key } });
  return { ok: true };
}

async function renameEntry(oldRel, newRel) {
  const oldK = normPath(oldRel);
  const newK = normPath(newRel);
  if (FS.mode === 'fss') {
    _assertFsPath(oldK, 'write');
    _assertFsPath(newK, 'write');
    // v8.13：目录重命名不能走“读内容→写新文件”路径（会直接失败）；
    // 优先使用 FileSystemHandle.move（Chromium 新版本），否则给出明确错误
    const dir = await _getDirHandle(oldK);
    if (dir) {
      if (typeof dir.move === 'function') {
        await dir.move(basename(newK));
        for (const key of Array.from(_HANDLE_CACHE.keys())) {
          if (key === oldK || key.startsWith(oldK + '/')) _HANDLE_CACHE.delete(key);
        }
        return { ok: true };
      }
      throw new Error('当前浏览器不支持目录重命名，请改用命令桥工作区');
    }
    const content = await readFile(oldK);
    await writeFile(newK, content.content);
    await deleteEntry(oldK);
    return { ok: true };
  }
  await api('/api/bridge/fs/rename', { method: 'POST', body: { old: oldK, new: newK } });
  return { ok: true };
}

async function exists(rel) {
  const key = normPath(rel || '');
  if (!key) return true; // 根目录
  if (FS.mode === 'fss') {
    try {
      const dir = await _getDirHandle(dirname(key));
      if (!dir) return false;
      await dir.getFileHandle(basename(key));
      return true;
    } catch (e) {
      // 可能是目录
      return !!(await _getDirHandle(key));
    }
  }
  try {
    await readFile(key);
    return true;
  } catch (e) {
    // v8.5.x 审查修复：413 表示"文件存在但超过 2MB 读取上限"，应视为存在而非不存在
    if (e && e.status === 413) return true;
    return false;
  }
}

/* ---------- 递归遍历（grep/glob 用） ---------- */
const SKIP_DIRS = ['node_modules', '.git', '__pycache__', '.venv', 'venv', '.idea', '.vscode', 'dist', 'build'];
const MAX_WALK_FILES = 3000;
const MAX_WALK_DEPTH = 8;

async function walkFs(rel, opts = {}) {
  const root = normPath(rel || '');
  if (FS.mode === 'fss') _assertFsPath(root, 'read'); // v8.13.1：data/backups 不可遍历
  const out = { files: [], dirs: [], truncated: false, scanned: 0 };

  const visit = async (dirRel, depth) => {
    if (out.scanned >= MAX_WALK_FILES) { out.truncated = true; return; }
    if (depth > MAX_WALK_DEPTH) return;
    let entries;
    try {
      entries = await listDir(dirRel);
    } catch (e) {
      return;
    }
    for (const it of entries) {
      if (out.scanned >= MAX_WALK_FILES) { out.truncated = true; return; }
      if (it.is_dir && SKIP_DIRS.includes(it.name)) continue;
      const p = dirRel ? `${dirRel}/${it.name}` : it.name;
      if (it.is_dir) {
        out.dirs.push(p);
        await visit(p, depth + 1);
      } else {
        out.scanned++;
        out.files.push({ path: p, name: it.name, size: it.size });
      }
    }
  };

  await visit(root, 0);
  return out;
}

/* ---------- 命令桥（浏览器无法直接执行命令，全部走桥） ---------- */
async function bridgeRunCommand({ command, cwd, timeout, dangerOk, onLine, signal }) {
  if (!FS.bridge.authorized) throw new Error('尚未授权命令桥工作区，请在设置中授权');
  let done = false;
  let rc = -1;
  let timedOut = false;
  let lastError = '';
  await sseFetch(
    '/api/bridge/run_command',
    { command, cwd: cwd || '', timeout: timeout || 120, danger_ok: !!dangerOk },
    {
      onData(obj) {
        // 后端 error 事件（如启动失败）此前被静默吞掉——现在显式记录并随结果返回
        if (obj.error !== undefined && obj.error !== null) lastError = String(obj.error);
        if (obj.line !== undefined && onLine) onLine(obj.line);
        if (obj.done) { done = true; rc = obj.rc; timedOut = !!obj.timed_out; }
      },
      onError(e) { throw e; },
      onAbort() {
        const e = new Error('已停止');
        e.name = 'AbortError';
        throw e;
      },
    },
    signal
  );
  return { ok: rc === 0 && !lastError, rc, timedOut, done, error: lastError };
}

/* ---------- v6.4 搜索桥（grep / glob，走 Python 后端） ---------- */
async function bridgeGrep({ pattern, path, glob, ignoreCase, lineNumbers }) {
  if (!FS.bridge.authorized) throw new Error('尚未授权命令桥工作区');
  const params = new URLSearchParams();
  params.set('pattern', pattern || '');
  if (path) params.set('path', path);
  if (glob) params.set('glob', glob);
  if (ignoreCase) params.set('ignore_case', 'true');
  if (lineNumbers !== false) params.set('line_numbers', 'true');
  return await api(`/api/bridge/fs/grep?${params.toString()}`);
}

async function bridgeGlob({ pattern, path }) {
  if (!FS.bridge.authorized) throw new Error('尚未授权命令桥工作区');
  const params = new URLSearchParams();
  params.set('pattern', pattern || '**/*');
  if (path) params.set('path', path);
  return await api(`/api/bridge/fs/glob?${params.toString()}`);
}
