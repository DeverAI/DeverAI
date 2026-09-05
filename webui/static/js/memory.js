/* ============ memory.js v8.22 Agent 长期记忆 / 经验系统 ============
 * 基础向能力：跨会话的 Agent 记忆（事实 / 经验教训 / 用户偏好 / 技能要点）。
 * - 存储：IndexedDB 'memory' store（浏览器本地，服务端不落盘任何 Agent 数据）。
 * - 检索：本地关键词打分（标题+内容 token 命中 + bigram 相似度），零 API 成本。
 * - 注入：每轮 runSession 开始时按用户消息召回 top-K 相关记忆进 system 上下文。
 * - 记录：AI 主动调用 memory_save；会话出错-恢复后由 agent 提示沉淀经验。
 */
const Memory = {
  KINDS: ['fact', 'lesson', 'pref', 'skill'],
  KIND_LABEL: { fact: '事实', lesson: '经验教训', pref: '用户偏好', skill: '技能要点' },
  MAX_INJECT: 5,        // 每轮注入的最大记忆条数
  MAX_TOTAL: 2000,     // 记忆总量上限（防无限膨胀）

  async save(title, content, kind, tags) {
    const k = this.KINDS.includes(kind) ? kind : 'fact';
    const t = String(title || '').trim().slice(0, 120);
    const c = String(content || '').trim().slice(0, 4000);
    if (!t || !c) return null;
    const all = await IDB.getAll('memory');
    // 查重：标题相同则合并更新（保留原命中计数）
    const dup = all.find((m) => (m.title || '').trim() === t);
    const item = {
      id: dup ? dup.id : uid(),
      title: t,
      content: c,
      kind: k,
      tags: Array.isArray(tags) ? tags.slice(0, 10).map((x) => String(x).slice(0, 40)) : [],
      created_at: dup ? dup.created_at : Date.now(),
      updated_at: Date.now(),
      hits: dup ? (dup.hits || 0) : 0,
    };
    if (!dup && all.length >= this.MAX_TOTAL) {
      // 淘汰最旧且命中最少的记忆（LFU 简化）
      all.sort((a, b) => (a.hits || 0) - (b.hits || 0) || (a.created_at || 0) - (b.created_at || 0));
      await IDB.delete('memory', all[0].id);
    }
    await IDB.put('memory', item);
    return item;
  },

  async list(kind, limit) {
    let all = await IDB.getAll('memory');
    if (kind && this.KINDS.includes(kind)) all = all.filter((m) => m.kind === kind);
    all.sort((a, b) => (b.updated_at || b.created_at || 0) - (a.updated_at || a.created_at || 0));
    return typeof limit === 'number' ? all.slice(0, limit) : all;
  },

  async get(id) {
    const all = await IDB.getAll('memory');
    return all.find((m) => m.id === id) || null;
  },

  async remove(id) {
    await IDB.delete('memory', id);
  },

  async clear() {
    await IDB.clear('memory');
  },

  /* 本地关键词打分：token 命中（标题×3 + 标签×2 + 内容×1）+ bigram 相似度 */
  _score(mem, query) {
    const q = String(query || '').toLowerCase();
    if (!q) return 0;
    const title = (mem.title || '').toLowerCase();
    const content = (mem.content || '').toLowerCase();
    const tags = (mem.tags || []).join(' ').toLowerCase();
    let s = 0;
    const tokens = q.split(/[\s,，。;；:：/\\]+/).filter((w) => w.length >= 2);
    for (const w of tokens) {
      if (title.includes(w)) s += 3;
      if (tags.includes(w)) s += 2;
      if (content.includes(w)) s += 1;
    }
    s += charSimilarity(title + ' ' + content, q) * 2;
    return s;
  },

  async search(query, limit) {
    const all = await IDB.getAll('memory');
    const scored = all
      .map((m) => ({ m, s: this._score(m, query) }))
      .filter((x) => x.s > 0.5)
      .sort((a, b) => b.s - a.s)
      .slice(0, limit || 8)
      .map((x) => ({ ...x.m, score: Number(x.s.toFixed(2)) }));
    return scored;
  },

  /* 每轮召回：按用户消息取 top-K，命中计数 +1，返回可注入的文本段 */
  async recall(userText) {
    try {
      const hits = await this.search(userText, this.MAX_INJECT);
      if (!hits.length) return '';
      // 异步命中计数（不阻塞）
      hits.forEach((h) => {
        IDB.put('memory', { ...h, hits: (h.hits || 0) + 1 }).catch(() => {});
      });
      const lines = hits.map((h) =>
        `- [${this.KIND_LABEL[h.kind] || h.kind}] ${h.title}: ${String(h.content).slice(0, 160)}`
      );
      return '【长期记忆召回（与当前任务相关）】\n' + lines.join('\n');
    } catch (e) {
      return '';
    }
  },

  /* 经验自动沉淀建议（agent 侧提示词用）：返回当前记忆统计 */
  async stats() {
    try {
      const all = await IDB.getAll('memory');
      const byKind = {};
      this.KINDS.forEach((k) => { byKind[k] = all.filter((m) => m.kind === k).length; });
      return { total: all.length, byKind };
    } catch (e) {
      return { total: 0, byKind: {} };
    }
  },
};
