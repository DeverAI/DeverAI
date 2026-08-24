/* ============ voice.js — 语音助手「小龙」核心模块（增强版） ============
 * STT（Web Speech API）→ 意图解析 → 双模式执行 → TTS 语音回复
 * 轻量化：零外部依赖，纯浏览器原生 Web Speech API + Qwen LLM（OpenAI 兼容）
 *
 * 模块开关：App.config.ENABLE_VOICE_ASSISTANT（默认 false，关闭即隐藏入口）
 *
 * v2.0 增强：
 *   - 多轮对话记忆（通过 VoicePet 模块）
 *   - 增强意图解析（13 种意图，含进度查询/消息注入/任务管理）
 *   - 进度查询（实时任务进度 + Agent 状态）
 *   - 与 voice-pet.js 浮层联动
 */

const VoiceAssistant = (() => {
  // ---- 状态机 ----
  const STATE = { IDLE: 'idle', RECORDING: 'recording', THINKING: 'thinking', SPEAKING: 'speaking' };
  let _state = STATE.IDLE;
  let _recognition = null;
  let _synthesis = null;
  let _onResult = null;
  let _onStateChange = null;
  let _onTranscript = null;
  let _locale = 'zh-CN';
  // 多轮对话记忆
  let _contextMemory = [];  // [{role, content, ts}]
  const _MAX_MEMORY = 20;

  // ---- 意图模式（增强版，13 种意图） ----
  const INTENT_PATTERNS = [
    { id: 'read_status', patterns: [/读取状态/, /任务多少/, /查看任务/, /进度如何/, /在做什么/, /当前状态/, /status/, /progress/, /list tasks/], desc: '读取任务列表与统计' },
    { id: 'add_task', patterns: [/添加任务[：:]\s*(.+)/, /新建任务[：:]\s*(.+)/, /插入任务[：:]\s*(.+)/, /add task[:\s]+(.+)/i, /new task[:\s]+(.+)/i], desc: '插入新任务', capture: true },
    { id: 'complete_task', patterns: [/完成(第?[一二三四五12345]?)个?任务/, /完成任务/, /done/, /finish/, /complete\s+(.+)/i], desc: '完成一个任务', capture: true },
    { id: 'delete_task', patterns: [/删除任务[：:]\s*(.+)/, /移除任务[：:]\s*(.+)/, /delete task[:\s]+(.+)/i], desc: '删除任务', capture: true },
    { id: 'remember', patterns: [/记住[：:]\s*(.+)/, /注意[：:]\s*(.+)/, /remember[:\s]+(.+)/i, /note[:\s]+(.+)/i], desc: '注入上下文到 notepad', capture: true },
    { id: 'add_constraint', patterns: [/不要\s*(.+)/, /禁止\s*(.+)/, /don'?t\s+(.+)/i, /never\s+(.+)/i], desc: '添加限制条件', capture: true },
    { id: 'continue_task', patterns: [/继续/, /下一条/, /下一个/, /continue/, /next task/, /next/], desc: '继续执行下一个任务' },
    { id: 'inject_message', patterns: [/告诉Agent[：:]\s*(.+)/, /注入消息[：:]\s*(.+)/, /插入消息[：:]\s*(.+)/, /send to agent[:\s]+(.+)/i, /inject[:\s]+(.+)/i], desc: '向主对话注入消息', capture: true },
    { id: 'execute_task', patterns: [/执行\s*(.+)/, /开始\s*(.+)/, /execute\s+(.+)/i, /start\s+(.+)/i, /run\s+(.+)/i], desc: '触发 Agent 模式执行任务', capture: true },
    { id: 'query_progress', patterns: [/进度/, /进行到哪/, /做到哪/, /当前进度/, /progress/], desc: '查询当前进度' },
    { id: 'clear_history', patterns: [/清空对话/, /清除历史/, /clear history/, /clear conversation/], desc: '清空对话历史' },
    { id: 'show_pet', patterns: [/显示小龙/, /唤出小龙/, /show pet/], desc: '显示浮层宠物' },
    { id: 'hide_pet', patterns: [/隐藏小龙/, /收起小龙/, /hide pet/], desc: '隐藏浮层宠物' },
  ];

  // ---- 状态管理 ----
  function getState() { return _state; }
  function setState(s) {
    _state = s;
    if (_onStateChange) _onStateChange(s);
    // 联动 VoicePet 心情
    if (window.VoicePet) {
      var moodMap = { idle: 'idle', recording: 'listening', thinking: 'thinking', speaking: 'talking' };
      window.VoicePet.setMood(moodMap[s] || s);
    }
  }
  function onStateChange(cb) { _onStateChange = cb; }
  function onTranscript(cb) { _onTranscript = cb; }
  function onResult(cb) { _onResult = cb; }

  // ---- 多轮对话记忆 ----
  function _pushMemory(role, content) {
    _contextMemory.push({ role, content, ts: Date.now() });
    if (_contextMemory.length > _MAX_MEMORY) {
      _contextMemory = _contextMemory.slice(-_MAX_MEMORY);
    }
  }
  function getMemory() { return _contextMemory.slice(); }
  function clearMemory() { _contextMemory = []; }

  // ---- Web Speech API 可用性检测 ----
  function isSupported() {
    return !!(window.SpeechRecognition || window.webkitSpeechRecognition) && !!window.speechSynthesis;
  }

  function getSupportedLocales() {
    const voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
    const locales = new Set(voices.map(v => v.lang));
    return Array.from(locales).sort();
  }

  // ---- STT：语音识别 ----
  function startListening() {
    if (_state === STATE.RECORDING) return false;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return false;

    try {
      _recognition = new SR();
      _recognition.lang = _locale;
      _recognition.continuous = false;
      _recognition.interimResults = true;
      _recognition.maxAlternatives = 1;

      _recognition.onstart = () => { setState(STATE.RECORDING); };
      _recognition.onerror = (e) => {
        console.warn('[Voice] STT error:', e.error);
        setState(STATE.IDLE);
      };
      _recognition.onend = () => { if (_state === STATE.RECORDING) setState(STATE.IDLE); };
      _recognition.onresult = (event) => {
        let transcript = '';
        let isFinal = false;
        for (let i = event.resultIndex; i < event.results.length; i++) {
          transcript += event.results[i][0].transcript;
          if (event.results[i].isFinal) isFinal = true;
        }
        if (_onTranscript) _onTranscript(transcript, isFinal);
        if (isFinal && transcript.trim()) {
          setState(STATE.THINKING);
          processIntent(transcript.trim());
        }
      };

      _recognition.start();
      return true;
    } catch (e) {
      console.warn('[Voice] startListening failed:', e);
      setState(STATE.IDLE);
      return false;
    }
  }

  function stopListening() {
    if (_recognition) {
      try { _recognition.stop(); } catch (e) {}
      _recognition = null;
    }
    if (_state === STATE.RECORDING) setState(STATE.IDLE);
  }

  // ---- TTS：语音合成 ----
  function speak(text, opts = {}) {
    if (!window.speechSynthesis || !text) return false;
    try {
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = opts.lang || _locale;
      utter.rate = opts.rate || 1.0;
      utter.pitch = opts.pitch || 1.0;
      utter.volume = opts.volume || 1.0;

      const voices = window.speechSynthesis.getVoices();
      const match = voices.find(v => v.lang === _locale) || voices.find(v => v.lang.startsWith('zh'));
      if (match) utter.voice = match;

      utter.onstart = () => setState(STATE.SPEAKING);
      utter.onend = () => setState(STATE.IDLE);
      utter.onerror = () => setState(STATE.IDLE);

      window.speechSynthesis.speak(utter);
      return true;
    } catch (e) {
      console.warn('[Voice] TTS error:', e);
      return false;
    }
  }

  function stopSpeaking() {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    if (_state === STATE.SPEAKING) setState(STATE.IDLE);
  }

  // ---- 意图解析（增强版） ----
  function parseIntent(text) {
    for (const intent of INTENT_PATTERNS) {
      for (const pattern of intent.patterns) {
        const m = text.match(pattern);
        if (m) {
          return {
            id: intent.id,
            desc: intent.desc,
            capture: intent.capture ? (m[1] || '') : null,
            raw: text,
          };
        }
      }
    }
    return null;
  }

  // ---- 处理识别结果 ----
  function processIntent(transcript) {
    _pushMemory('user', transcript);
    const intent = parseIntent(transcript);
    if (_onResult) _onResult(transcript, intent);
  }

  // ---- 反馈模式：LLM 直接回答（带多轮记忆） ----
  async function feedbackMode(text) {
    if (typeof llmChat !== 'function') {
      return { ok: false, error: 'LLM 客户端不可用' };
    }
    try {
      const cfg = App.config || {};
      // 构建带记忆的消息
      const messages = [{ role: 'system', content: _buildSystemPrompt() }];
      // 注入最近记忆
      const mem = getMemory();
      for (const m of mem.slice(-10)) {
        messages.push({ role: m.role, content: m.content });
      }
      // 确保最后一条是用户当前输入
      if (mem.length === 0 || mem[mem.length - 1].content !== text) {
        messages.push({ role: 'user', content: text });
      }
      const resp = await llmChat({
        messages: messages,
        model: cfg.model || '',
        temperature: 0.3,
        max_tokens: 1024,
      });
      const reply = resp.text || resp.content || '';
      _pushMemory('assistant', reply);
      return { ok: true, text: reply };
    } catch (e) {
      return { ok: false, error: e.message || 'LLM 调用失败' };
    }
  }

  // ---- Agent 模式：触发 Agent 循环 ----
  function agentMode(taskText) {
    // 将任务注入输入区并触发发送
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('btn-send');
    if (input) {
      input.value = taskText;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (sendBtn) sendBtn.click();
    return { ok: true, message: `已触发 Agent 执行: ${taskText}` };
  }

  // ---- System Prompt（反馈模式用，带多轮记忆说明） ----
  function _buildSystemPrompt() {
    return `你是 DeverAI 语音助手「小龙」，一个运行在浏览器中的 AI 助手。
回答要求：简洁、自然、口语化，适合语音回复（不超过 3 句话）。
当前工作区文件操作通过工具完成，不要编造文件内容。
你可以：查询任务进度、添加任务、注入消息到主对话、执行任务。
你具有多轮对话记忆能力，可以参考之前的对话上下文。`;
  }

  // ---- 任务管理（通过 bridge API） ----
  const VoiceData = {
    async _api(endpoint, method = 'GET', body = null) {
      const cfg = App.config || {};
      if (!cfg.ENABLE_VOICE_ASSISTANT) return null;
      const opts = { method, headers: { 'Content-Type': 'application/json' } };
      if (body !== null && body !== undefined) opts.body = body;
      try {
        const r = await api(`/api/bridge/voice/${endpoint}`, opts);
        return r;
      } catch (e) {
        console.warn(`[Voice] API ${endpoint} failed:`, e);
        return null;
      }
    },
    // 增强版 API（voice-pet 宿主端）
    async _petApi(endpoint, method = 'GET', body = null) {
      const cfg = App.config || {};
      if (!cfg.ENABLE_VOICE_ASSISTANT) return null;
      const opts = { method, headers: { 'Content-Type': 'application/json' } };
      if (body !== null && body !== undefined) opts.body = body;
      try {
        const r = await api(`/api/bridge/voice-pet/${endpoint}`, opts);
        return r;
      } catch (e) {
        console.warn(`[Voice] Pet API ${endpoint} failed:`, e);
        return null;
      }
    },

    async getTasks() { return await this._petApi('tasks') || { tasks: [] }; },
    async addTask(title, detail) { return await this._petApi('tasks', 'POST', { title, detail }) || {}; },
    async completeTask() { return await this._petApi('tasks/continue', 'POST') || {}; },
    async deleteTask(taskId) { return await this._petApi(`tasks/${taskId}`, 'DELETE') || {}; },
    async getConstraints() { return await this._api('constraints') || { constraints: [] }; },
    async addConstraint(text) { return await this._api('constraints', 'POST', { text }) || {}; },
    async injectNotepad(text) { return await this._api('notepad', 'POST', { text }) || {}; },
    async getProgress() { return await this._petApi('progress') || {}; },
    async clearConversations() { return await this._petApi('conversations', 'DELETE') || {}; },
    async getStatus() {
      const [tasks, constraints] = await Promise.all([this.getTasks(), this.getConstraints()]);
      const taskList = tasks.tasks || [];
      const constraintList = constraints.constraints || [];
      const pending = taskList.filter(t => t.status === 'pending').length;
      const done = taskList.filter(t => t.status === 'done').length;
      return {
        total: taskList.length,
        pending,
        done,
        constraints: constraintList.length,
        tasks: taskList,
        constraintList,
      };
    },
  };

  // ---- 执行意图动作（增强版） ----
  async function executeIntent(intent) {
    const cfg = App.config || {};
    switch (intent.id) {
      case 'read_status': {
        const st = await VoiceData.getStatus();
        let msg;
        if (st.total === 0) {
          msg = '当前没有任务。你可以说"添加任务"来创建新任务。';
        } else {
          msg = `当前共 ${st.total} 个任务：待办 ${st.pending} 个，已完成 ${st.done} 个。限制条件 ${st.constraints} 条。`;
          if (st.pending > 0 && st.tasks) {
            const first = st.tasks.find(t => t.status === 'pending');
            if (first) msg += ` 下一个待办是：${first.title}。`;
          }
        }
        if (cfg.ENABLE_VOICE_ASSISTANT) speak(msg);
        return { ok: true, message: msg };
      }

      case 'query_progress': {
        const prog = await VoiceData.getProgress();
        let msg;
        if (prog && prog.summary) {
          const s = prog.summary;
          msg = `当前共 ${s.total} 个任务：待办 ${s.pending} 个，已完成 ${s.done} 个，平均进度 ${s.avgProgress}%。`;
          if (s.nextTask) msg += ` 下一个优先任务：${s.nextTask.title}。`;
        } else {
          msg = '暂无进度数据。';
        }
        if (cfg.ENABLE_VOICE_ASSISTANT) speak(msg);
        return { ok: true, message: msg };
      }

      case 'add_task': {
        const title = intent.capture || '新任务';
        const r = await VoiceData.addTask(title);
        const msg = `已添加任务：${title}。`;
        if (cfg.ENABLE_VOICE_ASSISTANT) speak(msg);
        return { ok: true, message: msg, result: r };
      }

      case 'complete_task': {
        const r = await VoiceData.completeTask();
        const msg = r.message || '已继续下一个任务。';
        if (cfg.ENABLE_VOICE_ASSISTANT) speak(msg);
        return { ok: true, message: msg, result: r };
      }

      case 'delete_task': {
        const tasksData = await VoiceData.getTasks();
        const tasks = tasksData.tasks || [];
        const targetTitle = intent.capture || '';
        let found = null;
        for (const t of tasks) {
          if (t.title === targetTitle || t.id === targetTitle) { found = t; break; }
        }
        if (found) {
          await VoiceData.deleteTask(found.id);
          const msg = `已删除任务：${found.title}。`;
          if (cfg.ENABLE_VOICE_ASSISTANT) speak(msg);
          return { ok: true, message: msg };
        }
        const msg = `未找到匹配的任务：${targetTitle}。`;
        if (cfg.ENABLE_VOICE_ASSISTANT) speak(msg);
        return { ok: false, message: msg };
      }

      case 'remember': {
        const text = intent.capture || '';
        await VoiceData.injectNotepad(text);
        const msg = `已记住：${text}。`;
        if (cfg.ENABLE_VOICE_ASSISTANT) speak(msg);
        return { ok: true, message: msg };
      }

      case 'add_constraint': {
        const text = intent.capture || '';
        await VoiceData.addConstraint(text);
        const msg = `已添加限制条件：${text}。`;
        if (cfg.ENABLE_VOICE_ASSISTANT) speak(msg);
        return { ok: true, message: msg };
      }

      case 'continue_task': {
        const r = await VoiceData.completeTask();
        const msg = r.message || '已继续下一个任务。';
        if (cfg.ENABLE_VOICE_ASSISTANT) speak(msg);
        return { ok: true, message: msg, result: r };
      }

      case 'inject_message': {
        const text = intent.capture || '';
        agentMode(text);
        const msg = `已注入消息到主对话：${text}。`;
        if (cfg.ENABLE_VOICE_ASSISTANT) speak(msg);
        return { ok: true, message: msg };
      }

      case 'execute_task': {
        const task = intent.capture || '';
        return agentMode(task);
      }

      case 'clear_history': {
        await VoiceData.clearConversations();
        clearMemory();
        const msg = '对话历史已清空。';
        if (cfg.ENABLE_VOICE_ASSISTANT) speak(msg);
        return { ok: true, message: msg };
      }

      case 'show_pet':
        if (window.VoicePet) window.VoicePet.show();
        return { ok: true, message: '小龙已出现!' };

      case 'hide_pet':
        if (window.VoicePet) window.VoicePet.hide();
        return { ok: true, message: '小龙已隐藏。' };

      default:
        return { ok: false, error: '未知意图' };
    }
  }

  return {
    STATE,
    isSupported,
    getSupportedLocales,
    startListening,
    stopListening,
    speak,
    stopSpeaking,
    parseIntent,
    processIntent,
    feedbackMode,
    agentMode,
    executeIntent,
    VoiceData,
    getState,
    onStateChange,
    onTranscript,
    onResult,
    getMemory,
    clearMemory,
    get locale() { return _locale; },
    set locale(v) { _locale = v; },
  };
})();
