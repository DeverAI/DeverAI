/* ============ tools.js 浏览器端工具 ============
 * Agent 可调用的全部工具：文件读写/编辑/搜索/命令/子Agent/资产/AOE。
 * 所有工具在浏览器端直接实现；文件走 fs.js（FSS 或桥），命令走命令桥。
 */

/* ---------- 审批门：命令执行需用户确认 ---------- */
const Approval = {
  _resolver: null,
  _pending: null,
  _timer: null,
  request(payload) {
    if (this._resolver) {
      // v8.5.x 审查修复：并发审批保守拒绝时提示用户，避免子任务静默失败无感知
      toast('已有审批待处理，当前请求已被拒绝', 'warn');
      return Promise.resolve(false);
    }
    return new Promise((resolve) => {
      this._resolver = resolve;
      this._pending = payload;
      // P1-2（查修）：审批挂起有界——120s 未处理自动拒绝（对齐桌面 600s 超时降级语义，
      // 防"清空会话/切页面后 Promise 永不 resolve"导致 Agent 永久卡死）
      this._timer = setTimeout(() => { this.respond(false); }, 120000);
      if (App.onAgentEvent) App.onAgentEvent({ type: 'approval_needed', payload, id: uid() });
    });
  },
  respond(ok) {
    if (this._resolver) {
      if (this._timer) { clearTimeout(this._timer); this._timer = null; }
      const r = this._resolver;
      this._resolver = null;
      this._pending = null;
      r(ok);
      // v8.13：通知 UI 移除审批卡片（停止按钮 respond(false) 时不再残留无响应按钮）
      try {
        if (typeof App !== 'undefined' && App.onAgentEvent) {
          App.onAgentEvent({ type: 'approval_dismiss' });
        }
      } catch (e) {}
    }
  },
};

/* ---------- v8.5 批次2：审批四模式（对齐桌面 _request_approval / is_dangerous） ---------- */
// v8.13：与 app/security.py / desktop/tools.py DANGEROUS_PATTERNS 完全同源——
// 补 rd /s、taskkill 顺序无关、--force 排除 --force-with-lease（JS 同样支持负向先行断言）。
const DANGEROUS_PATTERNS = [
  /\brm\s+-[a-z]*[rf]/i, /\bdel\s+\/[sfqi]/i, /\brmdir\s+\/s/i, /\brd\s+\/s\b/i,
  /\bformat\b/i, /\bdiskpart\b/i, /\bmkfs\b/i, /\bdd\s+if=/i, /:\(\)\{/,
  /\breg\s+delete\b/i, /\bshutdown\b/i, /\breboot\b/i, /\bpowershell\s+-enc/i,
  /Invoke-Expression/i, /\btaskkill\b(?=[\s\S]*\s\/f(?=\s|$))(?=[\s\S]*\s\/(?:im|pid)\b)/i,
  />\s*\/dev\//i, /\bgit\s+push\s+[\s\S]*--force(?!-with-lease)/i,
  /\bdrop\s+(table|database)/i, /\btruncate\s+table/i,
  // v8.13：执行代码/脚本的等价危险形式（与后端同源）
  /\bpython(?:3)?\s+-c\b/i, /\bpy\s+-c\b/i,
  /\bpowershell\s+(?:-[a-z]+\s+)*-(?:command|enc)\b/i, /\bcmd(?:\.exe)?\s+\/[cq]\b/i,
  /\bRemove-Item\b[\s\S]*-Recurse\b/i, /\bshutil\.rmtree\b/i, /\bos\.remove\b/i,
  // v8.15 检修：PowerShell 短参数/别名递归删除 + git clean（与后端四端同源）
  /\b(?:Remove-Item|ri|rm|del|erase|rmdir|rd)\b[^|\n;&]*\s-(?:recurse|r|rf|fr)\b/i,
  /\bgit\s+clean\b[^|\n;&]*\s-[a-z]*f/i,
];

function isDangerousCommand(command) {
  const cmd = String(command || '');
  for (const re of DANGEROUS_PATTERNS) {
    if (re.test(cmd)) return true;
  }
  return false;
}

// v8.25 用户文件保护（与桌面 file_protect.USER_SUFFIXES 同源）：用户资产后缀AI禁写禁命令
const USER_ASSET_SUFFIXES = new Set([
  '.ppt', '.pptx', '.pot', '.potx', '.pps', '.ppsx', '.odp',
  '.xls', '.xlsx', '.xlsm', '.csv', '.ods',
  '.doc', '.docx', '.odt', '.rtf', '.wps',
  '.pdf', '.psd', '.ai', '.sketch', '.fig',
  '.mp4', '.mov', '.avi', '.mkv', '.zip', '.rar', '.7z',
]);
function _isUserAsset(rel) {
  const s = String(rel || '').toLowerCase();
  // v8.26：workcopy/ 首段且无「..」穿越/盘符 = AI 工作副本，豁免保护（与桌面 _is_workcopy 同语义）
  const parts = s.replace(/\\/g, '/').split('/').filter(Boolean);
  if (parts.length && parts[0] === 'workcopy' && !parts.includes('..') && !s.includes(':')) return false;
  const m = s.match(/\.[a-z0-9]+$/);
  return !!(m && USER_ASSET_SUFFIXES.has(m[0]));
}
function _commandTouchesUserAsset(command) {
  const cmd = String(command || '');
  if (!cmd) return false;
  // v8.25修复：按空白/引号切分token（旧正则把"libreoffice a.pptx"整段当文件名导致漏判）
  const toks = cmd.match(/[^\s"'<>|]+?\.[A-Za-z0-9]{2,5}/g) || [];
  return toks.some((t) => {
    const clean = t.trim().replace(/^["']|["']$/g, '');
    const base = clean.replace(/\\/g, '/').split('/').pop();
    return _isUserAsset(base);
  });
}

async function requestApproval(payload, cfg) {
  if (!cfg || cfg.ENABLE_APPROVAL === false) return true; // 开关关闭全放行
  const mode = String(cfg.approval_mode || 'danger');
  if (mode === 'free') return true;                       // free：全放行
  if (mode === 'danger') {                                // danger（默认）：仅危险弹窗
    // v8.13：显式 dangerous 标记（delete_file 等本身即破坏性操作）同样强制弹窗，
    // 与桌面 is_dangerous(payload) 语义一致。
    const danger = !!payload.dangerous || isDangerousCommand(payload.command);
    return danger ? await Approval.request(payload) : true;
  }
  if (mode === 'copilot') {                               // copilot：副驾驶代批
    if (typeof llmChat !== 'function') return await Approval.request(payload);
    try {
      const resp = await llmChat({
        stream: false,
        // P1-2（查修）：副驾驶审查接入 abortCtrl，点停止即可中断（否则挂起最长 600s）
        signal: App.abortCtrl ? App.abortCtrl.signal : null,
        messages: [{
          role: 'user',
          content: '你是命令安全审查员。判断下面命令是否危险（可能破坏系统/数据）。只输出 JSON：{"kill":true|false,"uncertain":true|false,"note":"一句话说明"}。\n命令: ' + String(payload.command || ''),
        }],
      });
      const txt = String((resp && resp.content) || '');
      const m = txt.match(/\{[\s\S]*?\}/);
      const j = m ? JSON.parse(m[0]) : null;
      if (j && j.kill) {
        if (App.onAgentEvent) App.onAgentEvent({ type: 'copilot_block', reason: (j.note || '危险命令') });
        return false;
      }
      if (!j || j.uncertain) return await Approval.request(payload); // 拿不准 → 升级用户
      return true;
    } catch (e) {
      if (e && e.name === 'AbortError') return false;     // 用户已停止 → 拒绝
      return await Approval.request(payload);             // 副驾驶不可用 → 保守升级用户
    }
  }
  return await Approval.request(payload);                  // all / 未知：全部弹窗
}

/* ---------- 工具 Schema（OpenAI 兼容 function calling） ---------- */
function getToolDefs(extra = {}) {
  const cfg = App.config || {};
  let defs = [
    {
      type: 'function',
      function: {
        name: 'workspace_info',
        description: '获取当前工作区信息：文件后端模式（浏览器直连/命令桥）、工作区名称、命令桥是否已授权。',
        parameters: { type: 'object', properties: {}, required: [] },
      },
    },
    {
      type: 'function',
      function: {
        name: 'worktree_list',
        description: '列出当前 git 仓库的所有 worktree（工作树）。返回每个工作树的名称、分支、HEAD、是否未提交、是否当前。只读，无需审批。',
        parameters: { type: 'object', properties: {}, required: [] },
      },
    },
    {
      type: 'function',
      function: {
        name: 'worktree_switch',
        description: '切换当前工作区到指定的 git worktree（改全局工作区指向）。需用户审批确认（danger_ok 由系统注入）。name 为 worktree_list 返回的 name。',
        parameters: {
          type: 'object',
          properties: {
            name: { type: 'string', description: '目标工作树名称（worktree_list 返回的 name，主工作树为 codename 或 "main"）' },
          },
          required: ['name'],
        },
      },
    },
    /* ---------- v8.22 基础能力：记忆 / 出关审核 / 外部 API ---------- */
    {
      type: 'function',
      function: {
        name: 'memory_save',
        description: '保存一条长期记忆（跨会话持久）。kind: fact=项目事实, lesson=经验教训(踩坑后必须沉淀), pref=用户偏好, skill=技能要点。完成任务、发现用户偏好、踩坑并修复后应主动记录。同标题会合并更新。',
        parameters: {
          type: 'object',
          properties: {
            title: { type: 'string', description: '简短标题（唯一键，同标题合并）' },
            content: { type: 'string', description: '记忆内容（事实/教训/偏好/要点，具体可执行）' },
            kind: { type: 'string', description: 'fact / lesson / pref / skill' },
            tags: { type: 'array', items: { type: 'string' }, description: '检索标签（可选）' },
          },
          required: ['title', 'content'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'memory_search',
        description: '按关键词检索长期记忆，返回最相关的条目（本地打分，零成本）。动手前先查有没有相关经验教训可复用。',
        parameters: {
          type: 'object',
          properties: {
            query: { type: 'string', description: '检索关键词' },
            limit: { type: 'number', description: '返回条数上限（默认 8）' },
          },
          required: ['query'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'memory_list',
        description: '列出长期记忆（可按 kind 过滤）。用于回顾已有记忆避免重复记录。',
        parameters: {
          type: 'object',
          properties: {
            kind: { type: 'string', description: 'fact / lesson / pref / skill（空=全部）' },
            limit: { type: 'number', description: '返回条数上限（默认 30）' },
          },
          required: [],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'memory_delete',
        description: '删除一条长期记忆（id 来自 memory_list/memory_search）。',
        parameters: {
          type: 'object',
          properties: {
            id: { type: 'string', description: '记忆条目 id' },
          },
          required: ['id'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'outbound_deliver',
        description: '出关审核（强制）：任何要向外传递的最终内容（交付物、对外说明、邮件正文、发布文本）必须先走此工具。你需先逐字复述用户的原始要求（requirement），再给出完整内容（content）。用户审核批准后才真正出关。',
        parameters: {
          type: 'object',
          properties: {
            requirement: { type: 'string', description: '逐字复述用户的原始要求（repeat 一遍，供用户核对）' },
            content: { type: 'string', description: '即将出关的完整内容' },
            target: { type: 'string', description: '出关去向：clipboard=复制到剪贴板 / file=存为工作区文件 / email=邮件草稿' },
            title: { type: 'string', description: '内容标题（file/email 出关时的文件名或邮件主题）' },
          },
          required: ['requirement', 'content', 'target'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'api_request',
        description: '调用用户授权清单中的外部 API。首次调用某个 API 时会向用户请求授权（弹卡片），批准后执行。api_name 必须来自清单（未知名称会返回可用清单）。',
        parameters: {
          type: 'object',
          properties: {
            api_name: { type: 'string', description: '清单中的 API 名称' },
            method: { type: 'string', description: 'HTTP 方法（GET/POST 等，默认 GET）' },
            path: { type: 'string', description: 'API 路径（拼在 base_url 后，如 /v1/data）' },
            body: { type: 'object', description: '请求体（JSON，可选）' },
          },
          required: ['api_name', 'path'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'list_dir',
        description: '列出指定目录下的子项（文件与文件夹）。path 为空表示工作区根目录。',
        parameters: {
          type: 'object',
          properties: {
            path: { type: 'string', description: '相对工作区的目录路径，如 "src" 或 "src/utils"' },
          },
          required: [],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'read_file',
        description: '读取文件内容（UTF-8，最大 2MB）。offset/limit 按行截取，避免一次性读入过多内容。',
        parameters: {
          type: 'object',
          properties: {
            path: { type: 'string', description: '相对路径' },
            offset: { type: 'integer', description: '从第几行开始（0 起）' },
            limit: { type: 'integer', description: '最多读取多少行' },
          },
          required: ['path'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'write_file',
        description: '写入文件（覆盖）。目录不存在会自动创建。用于新建或整体覆盖文件。',
        parameters: {
          type: 'object',
          properties: {
            path: { type: 'string', description: '相对路径' },
            content: { type: 'string', description: '完整文件内容' },
          },
          required: ['path', 'content'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'edit_file',
        description: '精确替换文件中的一段文本（推荐用于小范围修改，比整体覆盖更省 token）。old_string 必须唯一匹配，除非 replace_all 为 true。',
        parameters: {
          type: 'object',
          properties: {
            path: { type: 'string', description: '相对路径' },
            old_string: { type: 'string', description: '要替换的原文' },
            new_string: { type: 'string', description: '替换后的新文本' },
            replace_all: { type: 'boolean', description: '全部替换（默认 false）' },
          },
          required: ['path', 'old_string', 'new_string'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'mkdir',
        description: '创建文件夹（含父目录）。',
        parameters: {
          type: 'object',
          properties: { path: { type: 'string', description: '相对路径' } },
          required: ['path'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'rename',
        description: '重命名/移动文件或文件夹。',
        parameters: {
          type: 'object',
          properties: {
            old_path: { type: 'string', description: '原相对路径' },
            new_path: { type: 'string', description: '新相对路径' },
          },
          required: ['old_path', 'new_path'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'delete_file',
        description: '删除文件或文件夹。危险操作：需要在设置中开启"允许 AI 删除文件"后才能执行。',
        parameters: {
          type: 'object',
          properties: { path: { type: 'string', description: '相对路径' } },
          required: ['path'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'grep',
        description: '在项目中按正则搜索文件内容，返回带行号的匹配结果（最多 200 条，最多扫描 3000 个文件）。',
        parameters: {
          type: 'object',
          properties: {
            pattern: { type: 'string', description: '正则表达式' },
            path: { type: 'string', description: '限定搜索目录（默认根目录）' },
            glob: { type: 'string', description: '限定文件类型，如 "*.py"' },
            ignore_case: { type: 'boolean', description: '忽略大小写匹配' },
          },
          required: ['pattern'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'glob',
        description: '按 glob 模式查找文件路径（支持 * 与 **）。',
        parameters: {
          type: 'object',
          properties: {
            pattern: { type: 'string', description: 'glob 模式，如 "src/**/*.py"' },
            path: { type: 'string', description: '搜索起点（默认根目录）' },
          },
          required: ['pattern'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'run_command',
        description: '在授权工作区执行命令行命令（需先授权命令桥并可能需用户确认）。命令输出会流式回传。未指定的可选参数按默认值执行并在结果中返回 warning。',
        parameters: {
          type: 'object',
          properties: {
            command: { type: 'string', description: '要执行的命令' },
            cwd: { type: 'string', description: '工作目录（相对路径，默认工作区根；指定 env 时默认用环境的目录）' },
            timeout: { type: 'integer', description: '超时秒数 1-600，默认 120' },
            env: { type: 'string', description: '持久命令行环境的 id 或名称（term_create 创建；同一环境多次执行继承 cwd 与环境变量）' },
            update_interval: { type: 'number', description: '更新间隔秒数 0-120：静默期到点把输出尾部心跳回传（长命令如固件刷写建议 10-30）。默认 0=不发心跳' },
            kill_after: { type: 'boolean', description: '超时/停止后是否杀整棵进程树，默认 true；false 时进程保留后台运行（适合保留服务器/烧录器守护）' },
          },
          required: ['command'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'term_create',
        description: '创建一个持久命令行环境（记住工作目录与环境变量）。后续 run_command 通过 env 参数在该环境里多次执行命令，上下文（cwd/env）持续保留。适合固件刷写、长驻构建等需要稳定上下文的场景。',
        parameters: {
          type: 'object',
          properties: {
            name: { type: 'string', description: '环境名称（可选，默认 env-N）' },
            cwd: { type: 'string', description: '工作目录（相对路径，默认工作区根）' },
            env_vars: { type: 'object', description: '环境变量对象 {KEY: VALUE}（如 {\"PATH\":\"...\"}）' },
          },
          required: [],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'term_list',
        description: '列出所有持久命令行环境（含 id、名称、工作目录、最后使用的命令与退出码）。',
        parameters: { type: 'object', properties: {}, required: [] },
      },
    },
    {
      type: 'function',
      function: {
        name: 'term_delete',
        description: '删除一个持久命令行环境（仅销毁上下文，不影响已结束的进程）。body: {id}',
        parameters: {
          type: 'object',
          properties: { id: { type: 'string', description: '要删除的环境 id' } },
          required: ['id'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'delegate_task',
        description: '把子任务委派给一个独立的子 Agent 执行（独立上下文，互不干扰，完成后只返回摘要）。适合需要专注、互不依赖的独立子任务。',
        parameters: {
          type: 'object',
          properties: {
            task: { type: 'string', description: '子任务描述，务必给出明确目标与产出要求' },
            context: { type: 'string', description: '必要的背景信息（代码片段、路径、约束等）' },
          },
          required: ['task'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'search_assets',
        description: '从本地资产银行检索历史可复用产物（代码、模板、片段）。执行新任务前建议先检索，复用优先于重建。',
        parameters: {
          type: 'object',
          properties: { query: { type: 'string', description: '检索关键词' } },
          required: ['query'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'store_asset',
        description: '把一份有价值的可复用产物存入本地资产银行（代码、脚本、模板等），供未来任务复用。',
        parameters: {
          type: 'object',
          properties: {
            title: { type: 'string', description: '资产标题，简洁准确' },
            content: { type: 'string', description: '资产内容（代码/文本）' },
            kind: { type: 'string', description: '类型：code/script/template/note' },
            tags: { type: 'array', items: { type: 'string' }, description: '标签' },
            scene: { type: 'string', description: '适用场景说明' },
          },
          required: ['title', 'content'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'inspect_asset',
        description: '检查资产银行（资料库）记录完整性：缺失字段/空内容/非法 tags/重复 id。发现资料异常时用这个做体检。',
        parameters: { type: 'object', properties: {}, required: [] },
      },
    },
    {
      type: 'function',
      function: {
        name: 'repair_assets',
        description: '批量修复资产银行可自动修复的问题（补 id/时间戳、tags 规范化、超长截断）；dry_run=true 只检查不落盘。',
        parameters: {
          type: 'object',
          properties: { dry_run: { type: 'boolean', description: '是否试运行（默认 false=直接修复）' } },
          required: [],
        },
      },
    },
    // v8.9 自研工具库（工具设计专家 + 工具医生）——网页版桥接桌面 tool smith
    {
      type: 'function',
      function: {
        name: 'build_tool',
        description: '构建自研工具（工具设计专家）：自动查重→设计→审核→过了才入库。接到"造工具/写脚本/做转换器"类需求时用。',
        parameters: {
          type: 'object',
          properties: { requirement: { type: 'string', description: '工具需求完整描述' } },
          required: ['requirement'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'list_tools',
        description: '列出已过审入库的自研工具。做新工具前先查是否已有。',
        parameters: { type: 'object', properties: {}, required: [] },
      },
    },
    {
      type: 'function',
      function: {
        name: 'use_tool',
        description: '执行一个自研工具（prompt 型直接出结果）。',
        parameters: {
          type: 'object',
          properties: {
            name: { type: 'string', description: '工具名' },
            inputs: { type: 'object', description: '模板参数键值对' },
          },
          required: ['name'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'list_tool_bugs',
        description: '列出待修的自研工具 bug（use_tool 失败时自动收集）。看有哪些工具需要修。',
        parameters: { type: 'object', properties: {}, required: [] },
      },
    },
    {
      type: 'function',
      function: {
        name: 'fix_tool_bugs',
        description: '工具医生：批量诊断并修复待修 bug。链路：读 bug→读原工具 impl→LLM 诊断→沙箱跑 example 回归→通过才覆盖入库，失败保留原版可回退。',
        parameters: {
          type: 'object',
          properties: { limit: { type: 'integer', description: '最多修复几个工具（默认 5，上限 20）' } },
          required: [],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'clear_tool_bug',
        description: '手动标记单条 bug 为已修（用户确认无需自动修复时用）。',
        parameters: {
          type: 'object',
          properties: { bug_id: { type: 'string', description: 'bug 编号（list_tool_bugs 可查）' } },
          required: ['bug_id'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'plan_and_execute',
        description: 'AOE 并行规划：把复杂目标拆成多个可并行的子任务，同时执行并汇总结果。适合可以分解并行的大任务；小任务直接用对话即可。',
        parameters: {
          type: 'object',
          properties: { task: { type: 'string', description: '要完成的目标' } },
          required: ['task'],
        },
      },
    },
    // v8.4 Python 应用截图 / UI 自截图确认（网页版）
    {
      type: 'function',
      function: {
        name: 'app_screenshot',
        description: '运行工作区内的 Python UI 脚本（PyQt/Tkinter 等），等窗口出现后截图保存到工作区。制造本地 GUI 应用后必须用这个自检效果。script=脚本相对路径，path=截图保存相对路径(.png/.jpg)，title=窗口标题片段(可选，多窗口时定位)，timeout=等待秒数(默认30)。',
        parameters: {
          type: 'object',
          properties: {
            script: { type: 'string', description: 'Python 脚本相对工作区路径，如 ui/myapp.py' },
            path: { type: 'string', description: '截图保存相对路径，如 docs/ui.png' },
            title: { type: 'string', description: '窗口标题包含片段（可选）' },
            timeout: { type: 'integer', description: '等待窗口秒数，默认30，最大120' },
            extra_args: { type: 'array', items: { type: 'string' }, description: '传给脚本的额外参数（可选）' },
          },
          required: ['script', 'path'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'ui_review',
        description: '视觉审查一张截图：读取截图并调用视觉模型（设置里的视觉专家模型，未配置则用当前模型）描述布局/配色/问题并给改进建议。制造 UI 截图后、或用户要求"看看效果"时用。path=截图相对路径，prompt=审查重点(可选)，model=指定审查模型(可选，默认视觉专家配置或当前模型)。',
        parameters: {
          type: 'object',
          properties: {
            path: { type: 'string', description: '截图相对工作区路径，如 docs/ui.png' },
            prompt: { type: 'string', description: '审查重点，默认描述布局配色问题并给建议' },
            model: { type: 'string', description: '审查模型 id（可选；默认用设置中的视觉专家模型，未配置则当前模型）' },
          },
          required: ['path'],
        },
      },
    },
    // v8.5.7 补齐：文件搜索 / 元工具 / 联网搜索 / 浏览器 / 暂存 / checkpoint 查询
    {
      type: 'function',
      function: {
        name: 'file_search',
        description: '按文件名相似度找文件（非内容搜索）。找文件优先用这个；内容搜索请用 grep。',
        parameters: {
          type: 'object',
          properties: {
            query: { type: 'string', description: '文件名/路径语义查询，如 "登录按钮"' },
            path: { type: 'string', description: '搜索起点（默认工作区根）' },
            glob: { type: 'string', description: '文件名过滤，如 *.py' },
            limit: { type: 'integer', description: '最多返回条数（默认 15）' },
          },
          required: ['query'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'search_tool',
        description: '查询当前可用工具及其用途。不确定用哪个工具时先问它。',
        parameters: {
          type: 'object',
          properties: { query: { type: 'string', description: '想做的事，如 "执行命令"' } },
          required: [],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'web_search',
        description: '互联网搜索（DuckDuckGo，零 key）：查资料/Benchmark/文档/最新动态。',
        parameters: {
          type: 'object',
          properties: {
            query: { type: 'string', description: '搜索关键词' },
            limit: { type: 'integer', description: '结果条数，默认 6' },
          },
          required: ['query'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'browser_open',
        description: '在新标签页打开网页（用户可见可交互）。需要外链/演示/让用户看页面时用。',
        parameters: {
          type: 'object',
          properties: { url: { type: 'string', description: 'http(s):// 开头的完整网址' } },
          required: ['url'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'browser_read',
        description: '无头读取网页渲染后的正文文本（JS 执行后的真实内容）。查在线文档/抓页面内容时用。',
        parameters: {
          type: 'object',
          properties: {
            url: { type: 'string', description: 'http(s):// 开头的完整网址' },
            timeout: { type: 'integer', description: '超时秒数，默认 30，最大 90' },
          },
          required: ['url'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'browser_screenshot',
        description: '无头截取网页整页图片保存到工作区（path 相对工作区，.png/.jpg 结尾）。',
        parameters: {
          type: 'object',
          properties: {
            url: { type: 'string', description: 'http(s):// 开头的完整网址' },
            path: { type: 'string', description: '相对工作区的保存路径，如 docs/page.png' },
            timeout: { type: 'integer', description: '超时秒数，默认 30，最大 90' },
          },
          required: ['url', 'path'],
        },
      },
    },
    // ---- v8.23 浏览器直控（CDP：受控实例可真实点击/输入/跳转） ----
    {
      type: 'function',
      function: {
        name: 'browser_launch',
        description: '启动一个可被直接操控的受控浏览器（CDP 调试口仅 127.0.0.1，独立临时 profile，不动用户主浏览器）。默认无头，被风控拦截时自动回退有头+反检测。需要真实点击/输入/跳转网页时先启动。会请求用户批准。',
        parameters: {
          type: 'object',
          properties: {
            url: { type: 'string', description: '初始打开的网址（默认当前网页版本机地址）' },
          },
          required: [],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'browser_navigate',
        description: '让受控浏览器跳转到新网址（自动注入反检测脚本）。需先 browser_launch。',
        parameters: {
          type: 'object',
          properties: { url: { type: 'string', description: 'http(s):// 开头的完整网址' } },
          required: ['url'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'browser_click',
        description: '在受控浏览器页面中点击元素：selector=CSS 选择器（如 #submit、button.primary），text=链接/按钮可见文本（二选一）。需先 browser_launch。',
        parameters: {
          type: 'object',
          properties: {
            selector: { type: 'string', description: 'CSS 选择器（与 text 二选一）' },
            text: { type: 'string', description: '可见文本（与 selector 二选一）' },
          },
          required: [],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'browser_type',
        description: '向受控浏览器当前焦点元素输入文本（Unicode）。需先 browser_launch。',
        parameters: {
          type: 'object',
          properties: { text: { type: 'string', description: '要输入的文本（≤2000 字符）' } },
          required: ['text'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'browser_press_keys',
        description: '受控浏览器按键：单键（enter/tab/esc/方向键/f1-f12）或组合键（ctrl,c）。需先 browser_launch。',
        parameters: {
          type: 'object',
          properties: { keys: { type: 'string', description: '按键名，多键逗号分隔' } },
          required: ['keys'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'browser_close',
        description: '关闭受控浏览器并清理临时 profile。',
        parameters: { type: 'object', properties: {}, required: [] },
      },
    },
    // ---- v8.23 外部软件（exe）自动化 ----
    {
      type: 'function',
      function: {
        name: 'exe_launch',
        description: '启动本地外部软件（exe）。会请求用户批准。返回 pid（后续 exe_close 只允许关闭由本工具启动的进程）。',
        parameters: {
          type: 'object',
          properties: {
            path: { type: 'string', description: 'exe 完整路径（本机绝对路径，如 C:\\\\Windows\\\\notepad.exe）' },
            args: { type: 'array', items: { type: 'string' }, description: '启动参数（可选，≤20 个）' },
          },
          required: ['path'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'exe_list_windows',
        description: '枚举当前所有可见顶层窗口（返回 hwnd/title/pid）。操作软件前先枚举找目标窗口。',
        parameters: { type: 'object', properties: {}, required: [] },
      },
    },
    {
      type: 'function',
      function: {
        name: 'exe_screenshot',
        description: '截取指定窗口的图片保存到工作区（path 相对工作区 .png/.jpg 结尾；hwnd 或 title 指定窗口）。',
        parameters: {
          type: 'object',
          properties: {
            path: { type: 'string', description: '相对工作区的保存路径，如 shots/app.png' },
            hwnd: { type: 'integer', description: '窗口句柄（与 title 二选一）' },
            title: { type: 'string', description: '窗口标题片段（与 hwnd 二选一）' },
          },
          required: ['path'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'exe_click',
        description: '在屏幕绝对坐标处点击鼠标（影响真实桌面，会请求用户批准）。先用 exe_screenshot 看清界面再点。',
        parameters: {
          type: 'object',
          properties: {
            x: { type: 'integer', description: '屏幕 X 坐标（像素）' },
            y: { type: 'integer', description: '屏幕 Y 坐标（像素）' },
          },
          required: ['x', 'y'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'exe_type',
        description: '向当前焦点窗口输入文本（真实键盘，影响桌面，会请求用户批准）。先用 exe_click 聚焦输入框。',
        parameters: {
          type: 'object',
          properties: { text: { type: 'string', description: '要输入的文本（≤2000 字符）' } },
          required: ['text'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'exe_press_keys',
        description: '真实键盘按键：单键（enter/tab/esc/f1-f12）或组合键（ctrl,s）。影响桌面，会请求用户批准。',
        parameters: {
          type: 'object',
          properties: { keys: { type: 'string', description: '按键名，多键逗号分隔' } },
          required: ['keys'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'exe_bring_to_front',
        description: '把指定窗口置前并激活（按 hwnd）。操作前把目标软件窗口调到前台。',
        parameters: {
          type: 'object',
          properties: { hwnd: { type: 'integer', description: '窗口句柄（exe_list_windows 查）' } },
          required: ['hwnd'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'exe_close',
        description: '关闭外部软件（仅允许关闭由 exe_launch 启动过的进程，白名单硬闸）。会请求用户批准。',
        parameters: {
          type: 'object',
          properties: {
            hwnd: { type: 'integer', description: '窗口句柄（与 pid 二选一）' },
            pid: { type: 'integer', description: '进程 ID（与 hwnd 二选一）' },
          },
          required: [],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'exe_journal',
        description: '查看 AI 对外部软件的操作记录（审计：点了哪里、输入了什么）。只读。',
        parameters: {
          type: 'object',
          properties: {
            tail: { type: 'integer', description: '最近条数，默认 50' },
            tool: { type: 'string', description: '按工具过滤（如 exe_click）' },
          },
          required: [],
        },
      },
    },
    // v8.14 F12 开发者工具（网页版——当前页面自检）
    ...(cfg.ENABLE_BROWSER !== false && cfg.ENABLE_BROWSER_DEVTOOLS !== false ? [
      {
        type: 'function',
        function: {
          name: 'browser_networks_get',
          description: '获取当前页面的网络请求记录（F12 Networks 面板，网页版）。通过 Performance API 获取资源加载记录。',
          parameters: {
            type: 'object',
            properties: {
              filter_url: { type: 'string', description: 'URL 包含的字符串（可选）' },
              filter_type: { type: 'string', description: '资源类型（xhr/fetch/document/script/stylesheet/image，可选）' },
              max_results: { type: 'integer', description: '最大返回条数（默认 50，最大 200）' },
            },
            required: [],
          },
        },
      },
      {
        type: 'function',
        function: {
          name: 'browser_storage_get',
          description: '获取当前页面存储信息（F12 Storage 面板，网页版）。',
          parameters: {
            type: 'object',
            properties: {
              storage_type: { type: 'string', description: '存储类型：cookies / localStorage / sessionStorage / all（默认 all）' },
            },
            required: [],
          },
        },
      },
      {
        type: 'function',
        function: {
          name: 'browser_storage_set',
          description: '设置当前页面存储项（F12 Storage 面板，网页版）。',
          parameters: {
            type: 'object',
            properties: {
              storage_type: { type: 'string', description: '存储类型：cookies / localStorage / sessionStorage' },
              key: { type: 'string', description: '键名' },
              value: { type: 'string', description: '值' },
            },
            required: ['key', 'value'],
          },
        },
      },
      {
        type: 'function',
        function: {
          name: 'browser_storage_clear',
          description: '清除当前页面存储（F12 Storage 面板，网页版）。',
          parameters: {
            type: 'object',
            properties: {
              storage_type: { type: 'string', description: '存储类型：cookies / localStorage / sessionStorage / all（默认 all）' },
            },
            required: [],
          },
        },
      },
      {
        type: 'function',
        function: {
          name: 'browser_console_eval',
          description: '在当前页面上下文中执行 JavaScript 表达式（F12 Console 面板，网页版）。',
          parameters: {
            type: 'object',
            properties: {
              expression: { type: 'string', description: 'JavaScript 表达式（≤2000 字符）' },
            },
            required: ['expression'],
          },
        },
      },
      {
        type: 'function',
        function: {
          name: 'browser_sources_list',
          description: '获取当前页面加载的 JavaScript 源文件列表（F12 Sources 面板，网页版）。',
          parameters: {
            type: 'object',
            properties: {
              filter_pattern: { type: 'string', description: 'URL/ID 过滤字符串（可选）' },
            },
            required: [],
          },
        },
      },
      {
        type: 'function',
        function: {
          name: 'browser_find_api_endpoints',
          description: '从当前页面的网络请求中找出疑似 API 端点（仅返回用户已付费/已授权服务的数据，不绕过任何付费墙）。',
          parameters: {
            type: 'object',
            properties: {
              filter_pattern: { type: 'string', description: 'URL 过滤字符串（默认 api）' },
              min_response_size: { type: 'integer', description: '最小响应体大小（默认 0）' },
            },
            required: [],
          },
        },
      },
    ] : []),
    {
      type: 'function',
      function: {
        name: 'notepad_save',
        description: '暂存一条中间结果（跨轮可用）。多步重构进度/待回填 TODO/对比基线等场景。',
        parameters: {
          type: 'object',
          properties: {
            key: { type: 'string', description: '暂存键名（英文小写下划线）' },
            content: { type: 'string', description: '暂存内容' },
          },
          required: ['key', 'content'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'notepad_read',
        description: '读取暂存内容。',
        parameters: {
          type: 'object',
          properties: { key: { type: 'string', description: '暂存键名' } },
          required: ['key'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'notepad_list',
        description: '列出所有暂存 key 与预览。',
        parameters: { type: 'object', properties: {}, required: [] },
      },
    },
    {
      type: 'function',
      function: {
        name: 'notepad_clear',
        description: '清除暂存（key 为空清全部，非空清单条）。',
        parameters: {
          type: 'object',
          properties: { key: { type: 'string', description: '要清除的键名（空=全部）' } },
          required: [],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'list_checkpoints',
        description: '列出最近的 AI 改动 checkpoint（write/edit 前自动快照的原文件），供查询历史改动。',
        parameters: {
          type: 'object',
          properties: { limit: { type: 'integer', description: '返回文件条数（默认 20）' } },
          required: [],
        },
      },
    },
    // v8.13.1：Agent 可见开发者网络面板（API 控制台）——只读、敏感字段打码
    // v8.25 一键备份 + 重名治理（与桌面 backup_workspace/scan_ambiguous_files/quarantine_files 同语义）
    {
      type: 'function',
      function: {
        name: 'backup_workspace',
        description: '一键备份完整工作区到 backups/<时间>_full.zip。用户要求备份时用；动用户资产文件前先建议备份。',
        parameters: { type: 'object', properties: { label: { type: 'string', description: '备份标签（默认full）' } }, required: [] },
      },
    },
    {
      type: 'function',
      function: {
        name: 'copy_user_asset',
        description: 'v8.26 工作副本：把工作区文件（尤其用户资产 PPT/Excel/Word/PDF）拷贝为 workcopy/ 下的工作副本（时间-作者-内容命名），原文件保持不变。处理用户资产内容前先征得用户同意再调用。',
        parameters: { type: 'object', properties: { path: { type: 'string', description: '原文件相对路径' }, actor: { type: 'string', description: '作者标识（默认AI）' } }, required: ['path'] },
      },
    },
    {
      type: 'function',
      function: {
        name: 'coordination_board',
        description: 'v8.33 全局协调看板（只读）：本机所有并发 Agent 在干什么、要操作哪些外部资源、有无冲突。跨工作区操作外部资源（如 SSH 服务器）前先看一眼。',
        parameters: { type: 'object', properties: {}, required: [] },
      },
    },
    {
      type: 'function',
      function: {
        name: 'scan_ambiguous_files',
        description: '扫描重名/命名不清文件（副本/copy/(1)/新建未命名等）。发现文件名奇怪时必须调用，结果请用户逐组识别。',
        parameters: { type: 'object', properties: {}, required: [] },
      },
    },
    {
      type: 'function',
      function: {
        name: 'quarantine_files',
        description: '把用户确认的重名文件备份转移到 backups/quarantine/<时间>/（可恢复）。需用户审批确认。',
        parameters: {
          type: 'object',
          properties: {
            files: { type: 'array', items: { type: 'string' }, description: '要转移的相对路径列表' },
            reason: { type: 'string', description: '原因说明' },
          },
          required: ['files'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'network_list',
        description: '查看开发者网络面板（API 控制台）最近记录的浏览器端请求：LLM 代理/命令桥/快照桥等，含状态码/方法/URL/耗时/请求体预览。敏感字段（api_key/password/token/Bearer）自动打码，Cookie 为 HttpOnly 不可见。',
        parameters: {
          type: 'object',
          properties: { limit: { type: 'integer', description: '最近条数，默认 10，最大 50' } },
          required: [],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'network_curl',
        description: '把开发者网络面板最近记录的请求转成打码后的 curl 命令预览（Bearer 令牌与请求体敏感字段显示为 ***）。真实 curl 请让用户在 API 控制台面板手动复制。',
        parameters: {
          type: 'object',
          properties: { limit: { type: 'integer', description: '最近条数，默认 3，最大 10' } },
          required: [],
        },
      },
    },
    // DashScope API（通义万相：文生图/图生图/视频生成）——服务端代理，API Key 不暴露给浏览器
    {
      type: 'function',
      function: {
        name: 'dashscope_image_generate',
        description: '文生图：根据文字描述生成图片（DashScope 通义万相，同步调用，返回图片 URL）。需要先配置 dashscope_api_key。',
        parameters: {
          type: 'object',
          properties: {
            prompt: { type: 'string', description: '画面描述文字（中文/英文，≤2000字符）' },
            model: { type: 'string', description: '模型名（默认 wan2.7-image-pro，可用 dashscope_list_models 查看）' },
            size: { type: 'string', description: '图片尺寸（默认 1024*1024）' },
            n: { type: 'integer', description: '生成数量（1-4，默认 1）' },
            style: { type: 'string', description: '风格（默认 <auto>）' },
            negative_prompt: { type: 'string', description: '负面描述' },
          },
          required: ['prompt'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'dashscope_image_edit',
        description: '图生图：对输入图片按文字描述进行编辑。需要先配置 dashscope_api_key。',
        parameters: {
          type: 'object',
          properties: {
            image_url: { type: 'string', description: '输入图片 URL（http(s):// 或 data:image/...）' },
            prompt: { type: 'string', description: '编辑要求描述' },
            model: { type: 'string', description: '模型名（默认 qwen-image-edit-max）' },
          },
          required: ['image_url', 'prompt'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'dashscope_video_generate',
        description: '文生视频/图生视频：根据文字或图片生成视频（异步调用，自动轮询结果）。需要先配置 dashscope_api_key。',
        parameters: {
          type: 'object',
          properties: {
            prompt: { type: 'string', description: '视频画面描述' },
            model: { type: 'string', description: '模型名（默认 wan2.7-t2v，图生视频用 wan2.7-i2v）' },
            image_url: { type: 'string', description: '输入图片 URL（非空时走图生视频）' },
          },
          required: ['prompt'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'dashscope_task_status',
        description: '查询 DashScope 异步任务（视频生成）状态。task_id 由 dashscope_video_generate 返回。',
        parameters: {
          type: 'object',
          properties: { task_id: { type: 'string', description: '异步任务 ID' } },
          required: ['task_id'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'dashscope_list_models',
        description: '列出 DashScope 可用模型（读 model-library/models.json）。可选 category 过滤。',
        parameters: {
          type: 'object',
          properties: { category: { type: 'string', description: '分类过滤（可选）' } },
          required: [],
        },
      },
    },
  ];
  // v8.13：Chat 形态只暴露只读工具（与桌面 build_tool_defs readonly 语义一致），
  // 防止 agent_mode=chat 时 LLM 仍拿到 write/run_command/delete 等写工具。
  const CHAT_READONLY_TOOLS = new Set([
    'workspace_info', 'worktree_list', 'list_dir', 'read_file', 'grep', 'glob',
    'search_assets', 'inspect_asset', 'list_checkpoints', 'scan_ambiguous_files',
    'file_search', 'search_tool', 'web_search', 'browser_read',
    'notepad_read', 'notepad_list', 'list_tools', 'list_tool_bugs', 'ui_review',
    'network_list', 'network_curl',
  ]);
  if ((cfg.agent_mode || 'builder') === 'chat') {
    defs = defs.filter((d) => CHAT_READONLY_TOOLS.has(d.function.name));
  }
  // 开关裁剪（与桌面版 build_tool_defs 语义一致；开关缺失按默认开）
  const gated = {
    app_screenshot: cfg.ENABLE_APP_SHOT,
    ui_review: cfg.ENABLE_UI_REVIEW,
    web_search: cfg.ENABLE_WEB_SEARCH,
    browser_open: cfg.ENABLE_BROWSER,
    browser_read: cfg.ENABLE_BROWSER,
    browser_screenshot: cfg.ENABLE_BROWSER,
    notepad_save: cfg.ENABLE_NOTEPAD,
    notepad_read: cfg.ENABLE_NOTEPAD,
    notepad_list: cfg.ENABLE_NOTEPAD,
    notepad_clear: cfg.ENABLE_NOTEPAD,
    list_checkpoints: cfg.ENABLE_CHECKPOINT,
    copy_user_asset: cfg.ENABLE_WORK_COPY !== false,
    coordination_board: cfg.ENABLE_COORDINATION !== false,
    // v8.13：delete_file 与桌面一致按 ALLOW_AI_DELETE 开关裁剪（关=不注册给 LLM）
    delete_file: cfg.ALLOW_AI_DELETE,
    // v8.5.x 审查修复：补齐 AOE/子Agent/资产银行的开关裁剪（否则开关关闭工具仍注册给 LLM）
    plan_and_execute: cfg.ENABLE_AOE,
    delegate_task: cfg.ENABLE_SUBAGENT,
    search_assets: cfg.ENABLE_VAULT,
    store_asset: cfg.ENABLE_VAULT,
    inspect_asset: cfg.ENABLE_VAULT,
    repair_assets: cfg.ENABLE_VAULT,
    // v8.9 自研工具库/工具医生（工具医生入口同时要求工具库开启，与桌面版一致）
    build_tool: cfg.ENABLE_TOOLSMITH,
    list_tools: cfg.ENABLE_TOOLSMITH,
    use_tool: cfg.ENABLE_TOOLSMITH,
    list_tool_bugs: cfg.ENABLE_TOOLSMITH !== false && cfg.ENABLE_TOOL_DOCTOR !== false,
    fix_tool_bugs: cfg.ENABLE_TOOLSMITH !== false && cfg.ENABLE_TOOL_DOCTOR !== false,
    clear_tool_bug: cfg.ENABLE_TOOLSMITH !== false && cfg.ENABLE_TOOL_DOCTOR !== false,
    // v8.22 基础能力开关裁剪（关=不注册给 LLM；执行侧另有兜底拦截）
    memory_save: cfg.ENABLE_MEMORY,
    memory_search: cfg.ENABLE_MEMORY,
    memory_list: cfg.ENABLE_MEMORY,
    memory_delete: cfg.ENABLE_MEMORY,
    outbound_deliver: cfg.ENABLE_OUTBOUND_GATE,
    // v8.23 浏览器直控（CDP）与外部软件（exe）自动化
    browser_launch: cfg.ENABLE_BROWSER_CTL !== false && cfg.ENABLE_BROWSER !== false,
    browser_navigate: cfg.ENABLE_BROWSER_CTL !== false && cfg.ENABLE_BROWSER !== false,
    browser_click: cfg.ENABLE_BROWSER_CTL !== false && cfg.ENABLE_BROWSER !== false,
    browser_type: cfg.ENABLE_BROWSER_CTL !== false && cfg.ENABLE_BROWSER !== false,
    browser_press_keys: cfg.ENABLE_BROWSER_CTL !== false && cfg.ENABLE_BROWSER !== false,
    browser_close: cfg.ENABLE_BROWSER_CTL !== false && cfg.ENABLE_BROWSER !== false,
    exe_launch: cfg.ENABLE_EXE_AUTOMATE !== false,
    exe_list_windows: cfg.ENABLE_EXE_AUTOMATE !== false,
    exe_screenshot: cfg.ENABLE_EXE_AUTOMATE !== false,
    exe_click: cfg.ENABLE_EXE_AUTOMATE !== false,
    exe_type: cfg.ENABLE_EXE_AUTOMATE !== false,
    exe_press_keys: cfg.ENABLE_EXE_AUTOMATE !== false,
    exe_bring_to_front: cfg.ENABLE_EXE_AUTOMATE !== false,
    exe_close: cfg.ENABLE_EXE_AUTOMATE !== false,
    exe_journal: cfg.ENABLE_EXE_JOURNAL !== false,
  };
  defs = defs.filter((d) => {
    const g = gated[d.function.name];
    return g === undefined || g !== false;
  });
  return defs;
}

/* ---------- 工具执行分发 ---------- */
async function executeTool(name, args, ctx) {
  const cfg = ctx.config || {};
  try {
    switch (name) {
      case 'workspace_info': return { ok: true, output: workspaceInfo() };
      case 'worktree_list': return await execWorktreeList(args, ctx, cfg);
      case 'worktree_switch': return await execWorktreeSwitch(args, ctx, cfg);
      // v8.22 基础能力
      case 'memory_save': return await execMemorySave(args, cfg);
      case 'memory_search': return await execMemorySearch(args);
      case 'memory_list': return await execMemoryList(args);
      case 'memory_delete': return await execMemoryDelete(args);
      case 'outbound_deliver': return await execOutboundDeliver(args, ctx, cfg);
      case 'api_request': return await execApiRequest(args, ctx, cfg);
      case 'list_dir': return await execListDir(args, ctx);
      case 'read_file': return await execReadFile(args, ctx);
      case 'write_file': return await execWriteFile(args, ctx);
      case 'edit_file': return await execEditFile(args, ctx);
      case 'mkdir': return await execMkdir(args, ctx);
      case 'rename': return await execRename(args, ctx);
      case 'delete_file': return await execDelete(args, ctx, cfg);
      case 'grep': return await execGrep(args, ctx);
      case 'glob': return await execGlob(args, ctx);
      case 'run_command': return await execRunCommand(args, ctx, cfg);
      case 'term_create': return await execTermCreate(args, ctx, cfg);
      case 'term_list': return await execTermList(args, ctx, cfg);
      case 'term_delete': return await execTermDelete(args, ctx, cfg);
      case 'delegate_task': return await execDelegate(args, ctx, cfg);
      case 'search_assets': return await execSearchAssets(args, ctx);
      case 'store_asset': return await execStoreAsset(args, ctx);
      case 'inspect_asset': return await execInspectAsset();
      case 'repair_assets': return await execRepairAssets(args);
      // v8.9 自研工具库（桥接桌面 tool smith）
      case 'build_tool': return await execBuildTool(args, cfg);
      case 'list_tools': return await execListTools();
      case 'use_tool': return await execUseTool(args, cfg);
      case 'list_tool_bugs': return await execListToolBugs();
      case 'fix_tool_bugs': return await execFixToolBugs(args, cfg);
      case 'clear_tool_bug': return await execClearToolBug(args);
      case 'plan_and_execute': return await execPlanAndExecute(args, ctx, cfg);
      case 'app_screenshot': return await execAppScreenshot(args, ctx, cfg);
      case 'ui_review': return await execUiReview(args, ctx, cfg);
      // v8.5.7 补齐
      case 'file_search': return await execFileSearch(args);
      case 'search_tool': return await execSearchTool(args);
      case 'web_search': return await execWebSearch(args, ctx);
      case 'browser_open': return await execBrowserOpen(args);
      case 'browser_read': return await execBrowserRead(args, ctx);
      case 'browser_screenshot': return await execBrowserScreenshot(args, ctx, cfg);
      // v8.23 浏览器直控（CDP）与外部软件（exe）自动化
      case 'browser_launch': return await execBrowserCtlLaunch(args, ctx, cfg);
      case 'browser_navigate': return await execBrowserCtlOp(args, ctx, 'navigate');
      case 'browser_click': return await execBrowserCtlOp(args, ctx, 'click');
      case 'browser_type': return await execBrowserCtlOp(args, ctx, 'type');
      case 'browser_press_keys': return await execBrowserCtlOp(args, ctx, 'press_keys');
      case 'browser_close': return await execBrowserCtlOp(args, ctx, 'close');
      case 'exe_launch': return await execExeOp(args, ctx, cfg, 'launch', true);
      case 'exe_list_windows': return await execExeOp(args, ctx, cfg, 'list_windows');
      case 'exe_screenshot': return await execExeOp(args, ctx, cfg, 'screenshot');
      case 'exe_click': return await execExeOp(args, ctx, cfg, 'click', true);
      case 'exe_type': return await execExeOp(args, ctx, cfg, 'type', true);
      case 'exe_press_keys': return await execExeOp(args, ctx, cfg, 'press_keys', true);
      case 'exe_bring_to_front': return await execExeOp(args, ctx, cfg, 'bring_to_front');
      case 'exe_close': return await execExeOp(args, ctx, cfg, 'close', true);
      case 'exe_journal': return await execExeOp(args, ctx, cfg, 'journal');
      case 'notepad_save': return await execNotepadSave(args);
      case 'notepad_read': return await execNotepadRead(args);
      case 'notepad_list': return await execNotepadList(args);
      case 'notepad_clear': return await execNotepadClear(args);
      case 'list_checkpoints': return await execListCheckpoints(args);
      // v8.25 一键备份 + 重名治理
      case 'backup_workspace': return await execBackupWorkspace(args);
      case 'scan_ambiguous_files': return await execScanAmbiguous();
      case 'quarantine_files': return await execQuarantineFiles(args, ctx, cfg);
      case 'copy_user_asset': return await execCopyUserAsset(args, ctx, cfg);
      case 'coordination_board': return await execCoordinationBoard(args);
      // v8.13.1：Agent 可见开发者网络面板（只读、打码）
      case 'network_list': return execNetworkList(args);
      case 'network_curl': return execNetworkCurl(args);
      // v8.14 F12 开发者工具（网页版——当前页面自检）
      case 'browser_networks_get': return execBrowserNetworksGet(args);
      case 'browser_storage_get': return execBrowserStorageGet(args);
      case 'browser_storage_set': return execBrowserStorageSet(args);
      case 'browser_storage_clear': return execBrowserStorageClear(args);
      case 'browser_console_eval': return execBrowserConsoleEval(args);
      case 'browser_sources_list': return execBrowserSourcesList(args);
      case 'browser_find_api_endpoints': return execBrowserFindApiEndpoints(args);
      // DashScope API（通义万相）——服务端代理
      case 'dashscope_image_generate': return await execDashscopeImageGenerate(args);
      case 'dashscope_image_edit': return await execDashscopeImageEdit(args);
      case 'dashscope_video_generate': return await execDashscopeVideoGenerate(args);
      case 'dashscope_task_status': return await execDashscopeTaskStatus(args);
      case 'dashscope_list_models': return await execDashscopeListModels(args);
      default: return { ok: false, output: `未知工具: ${name}` };
    }
  } catch (e) {
    return { ok: false, output: String((e && e.message) || e) };
  }
}

/* ---------- 各工具实现 ---------- */

/* v8.26 工作副本：禁碰=拷贝出去改，原内容不修改 */
async function execCopyUserAsset(args, ctx, cfg) {
  if (cfg && cfg.ENABLE_WORK_COPY === false) return { ok: false, output: '工作副本未开启（ENABLE_WORK_COPY）。' };
  if (!(FS.mode === 'bridge' && FS.bridge.authorized)) {
    return { ok: false, output: '需要命令桥授权才能创建工作副本。' };
  }
  const rel = normPath(args.path || '');
  if (!rel) return { ok: false, output: 'path 不能为空' };
  const allowed = await requestApproval({ command: `拷贝工作副本 ${rel} → workcopy/（原文件不动）`, path: rel, tool: 'copy_user_asset', dangerous: true }, cfg);
  if (!allowed) return { ok: false, output: '用户拒绝了工作副本创建。', meta: { denied: true } };
  try {
    const r = await api('/api/bridge/fs/workcopy', { method: 'POST', body: { path: rel, actor: String(args.actor || 'AI') } });
    if (!r || !r.ok) return { ok: false, output: '工作副本创建失败' };
    Tree.refresh().catch(() => {});
    return { ok: true, output: `已生成工作副本：${r.copy}（原文件 ${r.src} 保持不变，后续只在副本上操作）。`, meta: { copy: r.copy, src: r.src } };
  } catch (e) {
    return { ok: false, output: '工作副本创建失败: ' + String((e && e.message) || e) };
  }
}

/* v8.33 全局协调看板（只读）：跨工作区 Agent 闸口 */
async function execCoordinationBoard(args) {
  if (!(FS.mode === 'bridge' && FS.bridge.authorized)) {
    return { ok: false, output: '需要命令桥授权才能读取协调看板。' };
  }
  try {
    const r = await api('/api/bridge/coordination');
    if (!r || !r.ok) return { ok: false, output: '读取协调看板失败' };
    const lines = (r.agents || []).map((a) =>
      `- ${a.agent_id}（${a.status}）在干什么: ${a.task || '（未声明）'} | 资源: ${(a.resources || []).join(', ') || '无'} | 接下来: ${a.next_action || '（未声明）'}`);
    let out = '本机并发 Agent（含本端 CLI/桌面）:\n' + (lines.join('\n') || '（无）');
    if ((r.conflicts || []).length) {
      out += '\n资源冲突:\n' + r.conflicts.map((c) => `- ${c.key} → ${(c.agents || []).join(' 与 ')}`).join('\n');
    }
    return { ok: true, output: out, meta: { snapshot: r } };
  } catch (e) {
    return { ok: false, output: '读取协调看板失败: ' + String((e && e.message) || e) };
  }
}

/* v8.5 批次1：文件级 checkpoint 快照 + 依赖树更新（写文件前自动备份原内容，防误改丢数据） */
async function snapCheckpointBefore(rel, cfg, ctx) {
  // 需要命令桥授权 + 开关开启
  if (!(cfg && cfg.ENABLE_CHECKPOINT !== false) || !(FS.mode === 'bridge' && FS.bridge.authorized)) return null;
  try {
    // P2-3：不再用 exists() 前置门——bridge 模式 exists() 走 /fs/read，
    // >2MB 文件会 413 误判为"不存在"导致大文件永远无备份。
    // 直接 POST：后端内置"文件不存在则返回 bak=None"，无副作用。
    // content 传空串由后端直读原文件（也省一次大文件回传）。
    const r = await api('/api/bridge/checkpoint/save', {
      method: 'POST',
      body: { path: rel, content: '', source: 'ai' },
    });
    return (r && r.bak) || null;
  } catch (e) {
    // v8.12：快照失败不阻塞写操作，但不再静默（错误不得吞没）——以 note 事件呈现
    try { if (ctx && ctx.emit) ctx.emit('note', { text: `写前快照失败（${rel}）: ${(e && e.message) || e}` }); } catch (_) {}
    return null;
  }
}

async function snapTreeAfter(rel, op, cfg, ctx) {
  if (!(cfg && cfg.ENABLE_DEP_TREE !== false) || !(FS.mode === 'bridge' && FS.bridge.authorized)) return;
  try {
    // size 由后端读取真实字节数
    const r = await api('/api/bridge/tree/update', {
      method: 'POST', body: { rel, op: op || 'write' },
    });
    // v8.12：后端已改为失败返回 ok:false（不再静默），前端转 note 呈现
    if (r && r.ok === false) {
      try { if (ctx && ctx.emit) ctx.emit('note', { text: `依赖树更新失败（${rel}）: ${r.note || '未知原因'}` }); } catch (_) {}
    }
  } catch (e) {
    try { if (ctx && ctx.emit) ctx.emit('note', { text: `依赖树更新失败（${rel}）: ${(e && e.message) || e}` }); } catch (_) {}
  }
}

/* v8.5 批次1：只读报警态拦截写工具（与桌面 _readonly_block 对齐） */
async function guardBlocked(ctx) {
  if (!(FS.mode === 'bridge' && FS.bridge.authorized)) return '';
  try {
    const r = await api('/api/bridge/guard');
    if (r && r.readonly) return (r.reason || '系统处于只读保护状态');
  } catch (e) {
    // v8.12：查询失败不拦截但不再静默——note 呈现
    try { if (ctx && ctx.emit) ctx.emit('note', { text: '只读保护状态查询失败: ' + ((e && e.message) || e) }); } catch (_) {}
  }
  return '';
}

async function ensureWritable(rel, cfg, ctx) {
  const reason = await guardBlocked(ctx);
  if (reason) return reason;
  await snapCheckpointBefore(rel, cfg, ctx);
  return '';
}

function workspaceInfo() {
  const lines = [];
  lines.push(`文件后端: ${fsModeLabel()}`);
  lines.push(`工作区: ${FS.rootName || '未授权'}`);
  lines.push(`命令桥: ${FS.bridge.authorized ? FS.bridge.workspace : '未授权（无法执行命令）'}`);
  // 真实生效条件：本地开关开启，且桥模式还要求后端 allow_ai_delete 同步开启
  const localAllow = !!(App.config && App.config.ALLOW_AI_DELETE === true);
  const allowDelete = localAllow && (FS.mode !== 'bridge' || !!FS.bridge.allowAiDelete);
  lines.push(`允许 AI 删除: ${allowDelete ? '是' : '否'}`);
  return lines.join('\n');
}

/* v8.21：WorkTree 工具实现（git worktree 感知/切换，AI 可主动调用） */
async function execWorktreeList(args, ctx, cfg) {
  if (!FS.bridge.authorized) {
    return { ok: false, output: '命令桥未授权：WorkTree 需命令桥已授权的 git 仓库。' };
  }
  try {
    const r = await api('/api/bridge/worktree/list');
    const items = r.items || [];
    if (!items.length) return { ok: true, output: '当前仓库无 worktree（仅主工作树）。' };
    const lines = ['WorkTree 列表：'];
    items.forEach((wt) => {
      const flag = wt.current ? '[当前]' : '      ';
      const dirty = wt.dirty ? '未提交' : '干净';
      lines.push(`${flag} ${wt.name} @ ${wt.branch || '(detached)'} ${wt.head} [${dirty}]`);
    });
    return { ok: true, output: lines.join('\n') };
  } catch (e) {
    return { ok: false, output: 'worktree_list 失败: ' + e.message };
  }
}

async function execWorktreeSwitch(args, ctx, cfg) {
  if (!FS.bridge.authorized) {
    return { ok: false, output: '命令桥未授权：WorkTree 切换需命令桥已授权。' };
  }
  const name = String(args.name || '').trim();
  if (!name) return { ok: false, output: 'name 不能为空' };
  const blocked = await guardBlocked(ctx);
  if (blocked) return { ok: false, output: '工作区切换被拦截：' + blocked };
  // 切换工作区 = 改全局单例，等同危险操作，走审批门
  const allowed = await requestApproval({ command: 'worktree_switch ' + name, tool: 'worktree_switch', dangerous: true }, cfg);
  if (!allowed) return { ok: false, output: '用户拒绝了 WorkTree 切换。' };
  try {
    const r = await api('/api/bridge/worktree/switch', { method: 'POST', body: { name, danger_ok: true } });
    // 切换后刷新前端工作区状态
    if (typeof FS !== 'undefined' && FS.refreshRoot) await FS.refreshRoot();
    return { ok: true, output: `已切换 WorkTree 到 ${r.workspace || name}。后续文件/命令操作作用于新工作树。` };
  } catch (e) {
    return { ok: false, output: 'worktree_switch 失败: ' + e.message };
  }
}

/* ---------- v8.22 基础能力实现：记忆 / 出关审核 / 外部 API ---------- */

async function execMemorySave(args, cfg) {
  if (!cfg || cfg.ENABLE_MEMORY === false) return { ok: false, output: '记忆系统未开启（设置中可开启）。' };
  if (typeof Memory === 'undefined') return { ok: false, output: '记忆模块未加载。' };
  const item = await Memory.save(args.title, args.content, args.kind, args.tags);
  if (!item) return { ok: false, output: 'title/content 不能为空。' };
  return { ok: true, output: `已记忆 [${Memory.KIND_LABEL[item.kind] || item.kind}]「${item.title}」（跨会话持久）。` };
}

async function execMemorySearch(args) {
  if (typeof Memory === 'undefined') return { ok: false, output: '记忆模块未加载。' };
  const hits = await Memory.search(String(args.query || ''), Number(args.limit) || 8);
  if (!hits.length) return { ok: true, output: '无相关记忆。' };
  const lines = hits.map((h) =>
    `[${Memory.KIND_LABEL[h.kind] || h.kind}] ${h.title} (id=${h.id}, score=${h.score})\n  ${String(h.content).slice(0, 300)}`
  );
  return { ok: true, output: lines.join('\n\n') };
}

async function execMemoryList(args) {
  if (typeof Memory === 'undefined') return { ok: false, output: '记忆模块未加载。' };
  const items = await Memory.list(args.kind, Number(args.limit) || 30);
  if (!items.length) return { ok: true, output: '记忆库为空。' };
  const lines = items.map((m) => `[${Memory.KIND_LABEL[m.kind] || m.kind}] ${m.title} (id=${m.id}, hits=${m.hits || 0})`);
  return { ok: true, output: lines.join('\n') };
}

async function execMemoryDelete(args) {
  if (typeof Memory === 'undefined') return { ok: false, output: '记忆模块未加载。' };
  const id = String(args.id || '').trim();
  if (!id) return { ok: false, output: 'id 不能为空' };
  const m = await Memory.get(id);
  if (!m) return { ok: false, output: '记忆不存在: ' + id };
  await Memory.remove(id);
  return { ok: true, output: `已删除记忆「${m.title}」。` };
}

/* 出关审核：AI 复述要求 + 内容 → 用户批准 → 按目标落地（剪贴板/文件/邮件草稿） */
async function execOutboundDeliver(args, ctx, cfg) {
  if (!cfg || cfg.ENABLE_OUTBOUND_GATE === false) {
    return { ok: false, output: '出关审核未开启（设置中可开启）。外发内容需开启出关审核。' };
  }
  const requirement = String(args.requirement || '').trim();
  const content = String(args.content || '');
  const target = String(args.target || 'clipboard').toLowerCase();
  const title = String(args.title || '').trim();
  if (!requirement) return { ok: false, output: 'requirement（用户原始要求复述）不能为空。' };
  if (!content) return { ok: false, output: 'content（出关内容）不能为空。' };
  if (!['clipboard', 'file', 'email'].includes(target)) {
    return { ok: false, output: 'target 必须是 clipboard / file / email。' };
  }
  // 出关审核不走 approval_mode 分级——永远强制用户确认（repeat 机制的语义核心）
  const allowed = await Approval.request({
    type: 'outbound',
    requirement,
    content: content.length > 2000 ? content.slice(0, 2000) + '\n…（截断显示，共 ' + content.length + ' 字符）' : content,
    target, title,
  });
  if (!allowed) return { ok: false, output: '用户审核未通过，内容未出关。请根据用户反馈修改后重新提交。' };
  try {
    if (target === 'clipboard') {
      await navigator.clipboard.writeText(content);
      return { ok: true, output: '出关审核通过，内容已复制到剪贴板。' };
    }
    if (target === 'file') {
      const safeName = (title || 'outbound_' + Date.now()).replace(/[\\/:*?"<>|]/g, '_').slice(0, 80) + '.md';
      const rel = 'outbound/' + safeName;
      const r = await FS.write(rel, content);
      return { ok: true, output: `出关审核通过，内容已存为工作区文件 ${rel}。` };
    }
    // email：存为草稿（发送由用户在安全中心手动触发，避免 AI 直发邮件）
    const drafts = JSON.parse(localStorage.getItem('deverai.outbound.drafts') || '[]');
    drafts.push({ id: uid(), title: title || '(无主题)', content, created_at: Date.now() });
    localStorage.setItem('deverai.outbound.drafts', JSON.stringify(drafts.slice(-50)));
    return { ok: true, output: '出关审核通过，邮件草稿已保存（在 设置→安全中心→出关草稿 查看与发送）。' };
  } catch (e) {
    return { ok: false, output: '出关落地失败: ' + e.message };
  }
}

/* 外部 API 调用：查清单 → 首次需授权 → 后端代理转发（SSRF 防护在服务端） */
async function execApiRequest(args, ctx, cfg) {
  if (typeof ExtAPIs === 'undefined') return { ok: false, output: '外部 API 模块未加载。' };
  const name = String(args.api_name || '').trim();
  const apis = ExtAPIs.list();
  if (!apis.length) return { ok: false, output: '外部 API 清单为空。请用户在 设置→安全中心→外部 API 清单 中添加。' };
  const item = ExtAPIs.get(name);
  if (!item) {
    return { ok: false, output: 'API「' + name + '」不在清单中。可用清单: ' + apis.map((x) => x.name + '(' + (x.desc || x.base_url) + ')').join('; ') };
  }
  const method = String(args.method || 'GET').toUpperCase();
  if (!['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    return { ok: false, output: '不支持的 HTTP 方法: ' + method };
  }
  let path = String(args.path || '');
  if (!path.startsWith('/')) path = '/' + path;
  if (/[\r\n]/.test(path)) return { ok: false, output: 'path 非法' };
  const body = args.body !== undefined ? args.body : null;
  // 首次调用该 API 需用户授权（会话内记住）
  execApiRequest._granted = execApiRequest._granted || {};
  if (!execApiRequest._granted[name]) {
    const allowed = await Approval.request({
      type: 'api_call',
      api_name: name,
      api_desc: item.desc || '',
      base_url: item.base_url,
      method, path,
      body_preview: body ? JSON.stringify(body).slice(0, 300) : '',
    });
    if (!allowed) return { ok: false, output: '用户拒绝了 API「' + name + '」的调用授权。' };
    execApiRequest._granted[name] = true;
  }
  try {
    const r = await api('/api/llm/ext_proxy', {
      method: 'POST',
      body: { base_url: item.base_url, api_key: item.api_key || '', method, path, body },
    });
    const out = typeof r.body === 'string' ? r.body : JSON.stringify(r.body);
    return { ok: true, output: `HTTP ${r.status}\n` + String(out || '').slice(0, 4000) };
  } catch (e) {
    return { ok: false, output: 'api_request 失败: ' + e.message };
  }
}

async function execListDir(args) {
  const rel = normPath(args.path || '');
  const entries = await listDir(rel);
  if (!entries.length) return { ok: true, output: `(空目录) ${rel || '/'}` };
  const lines = entries.map((it) => (it.is_dir ? `[DIR]  ${it.name}` : `      ${it.name} (${fmtBytes(it.size)})`));
  return { ok: true, output: `${rel || '/'}:\n` + lines.join('\n') };
}

async function execReadFile(args) {
  const rel = normPath(args.path || '');
  const data = await readFile(rel);
  let content = data.content || '';
  const totalLines = content.split('\n').length;
  if (args.offset != null || args.limit != null) {
    const lines = content.split('\n');
    const off = Math.max(0, parseInt(args.offset) || 0);
    const lim = args.limit != null ? Math.max(1, parseInt(args.limit) || 0) : lines.length;
    content = lines.slice(off, off + lim).join('\n');
  }
  const out = `=== ${rel} (${totalLines} 行) ===\n${content}`;
  return { ok: true, output: out };
}

async function execWriteFile(args, ctx) {
  const cfg = (ctx && ctx.config) || {};
  const rel = normPath(args.path || '');
  // v8.25 用户文件保护（与桌面/后端同源）：用户资产AI禁写
  if (cfg.ENABLE_USER_FILE_PROTECT !== false && _isUserAsset(rel)) {
    return { ok: false, output: `[用户文件保护] ${rel} 疑似用户手工资产（Office/PDF/二进制），AI不允许直接覆盖。请先提示用户一键备份完整工作区，并由用户手动处理。` };
  }
  // v8.13：content 缺失显式报错，避免 String(undefined) 的“9 字符”假象并落空文件
  if (args.content == null) return { ok: false, output: 'write_file 缺少 content 参数' };
  const content = String(args.content);
  const blocked = await ensureWritable(rel, cfg, ctx); // v8.5: 只读拦截 + 写前快照
  if (blocked) return { ok: false, output: '写入被拦截：' + blocked };
  const existed = await exists(rel);
  // v8.12：轮内回退点（/rollback_point 端口接线，对齐桌面：审批通过后、落盘前记录旧内容）
  if (ctx && ctx.rollbackNo && typeof SnapRound !== 'undefined') {
    await SnapRound.rollbackPoint(rel, 'write_file', ctx.rollbackNo);
  }
  await writeFile(rel, content);
  Tree.refresh().catch(() => {});
  await snapTreeAfter(rel, 'write', cfg, ctx); // v8.5: 依赖树增量更新
  return {
    ok: true,
    output: existed ? `已覆盖写入 ${rel}（${content.length} 字符）` : `已创建文件 ${rel}`,
    meta: { created: !existed },
  };
}

async function execEditFile(args, ctx) {
  const cfg = (ctx && ctx.config) || {};
  const rel = normPath(args.path || '');
  // v8.25 用户文件保护
  if (cfg.ENABLE_USER_FILE_PROTECT !== false && _isUserAsset(rel)) {
    return { ok: false, output: `[用户文件保护] ${rel} 疑似用户手工资产，AI不允许直接编辑。请先提示用户一键备份，并由用户手动处理。` };
  }
  const data = await readFile(rel);
  const content = data.content || '';
  const oldS = String(args.old_string || '');
  const newS = String(args.new_string == null ? '' : args.new_string);
  if (!oldS) return { ok: false, output: 'old_string 不能为空' };
  const count = content.split(oldS).length - 1;
  if (count === 0) return { ok: false, output: `在 ${rel} 中未找到要替换的文本，请核对 old_string 与实际内容一致（注意缩进与换行）。` };
  if (count > 1 && !args.replace_all) {
    return { ok: false, output: `old_string 在 ${rel} 中出现 ${count} 次，请补充更多上下文使唯一匹配，或设置 replace_all: true。` };
  }
  const newContent = args.replace_all ? content.split(oldS).join(newS) : content.replace(oldS, newS);
  const blocked = await ensureWritable(rel, cfg, ctx); // v8.5: 只读拦截 + 写前快照（防 edit 绕过 guard）
  if (blocked) return { ok: false, output: '写入被拦截：' + blocked };
  // v8.12：轮内回退点（对齐桌面：审批/校验通过后、落盘前记录旧内容）
  if (ctx && ctx.rollbackNo && typeof SnapRound !== 'undefined') {
    await SnapRound.rollbackPoint(rel, 'edit_file', ctx.rollbackNo);
  }
  await writeFile(rel, newContent);
  Tree.refresh().catch(() => {});
  await snapTreeAfter(rel, 'write', cfg, ctx); // v8.5: 依赖树增量更新
  return { ok: true, output: `已修改 ${rel}（替换 ${args.replace_all ? count : 1} 处）`, meta: { old: oldS, new: newS } };
}

async function execMkdir(args, ctx) {
  const cfg = (ctx && ctx.config) || {};
  const blocked = await guardBlocked(ctx); // v8.5: 只读拦截
  if (blocked) return { ok: false, output: '创建目录被拦截：' + blocked };
  const rel = normPath(args.path || '');
  await mkdir(rel);
  Tree.refresh().catch(() => {});
  await snapTreeAfter(rel, 'write', cfg, ctx); // v8.13：mkdir 后同步依赖树增量
  return { ok: true, output: `已创建目录 ${rel}` };
}

async function execRename(args, ctx) {
  const blocked = await guardBlocked(ctx); // v8.5: 只读拦截
  if (blocked) return { ok: false, output: '重命名被拦截：' + blocked };
  await renameEntry(normPath(args.old_path || ''), normPath(args.new_path || ''));
  Tree.refresh().catch(() => {});
  return { ok: true, output: `已重命名 ${args.old_path} → ${args.new_path}` };
}

async function execDelete(args, ctx, cfg) {
  if (!cfg.ALLOW_AI_DELETE) {
    return { ok: false, output: '删除被禁止：请在设置中开启"允许 AI 删除文件"（危险操作，默认关闭）。' };
  }
  const rel = normPath(args.path || '');
  if (!rel) return { ok: false, output: 'path 不能为空' };
  // v8.25 用户文件保护：用户资产AI禁删
  if (cfg.ENABLE_USER_FILE_PROTECT !== false && _isUserAsset(rel)) {
    return { ok: false, output: `[用户文件保护] ${rel} 疑似用户手工资产，AI不允许删除。请用户手动处理或走重名隔离流程。` };
  }
  const blocked = await guardBlocked(ctx); // v8.5: 只读拦截
  if (blocked) return { ok: false, output: '删除被拦截：' + blocked };
  // 破坏性删除必须走审批门（对齐 run_command/app_screenshot），否则 approval_mode=all 形同虚设
  const allowed = await requestApproval({ command: `删除 ${rel}`, path: rel, tool: 'delete_file', dangerous: true }, cfg);
  if (!allowed) return { ok: false, output: '用户拒绝了删除操作。' };
  // P2-9/P2-13：删除前先快照原内容（文件级 checkpoint），删除后仍可从版本快照恢复
  await snapCheckpointBefore(rel, cfg, ctx);
  // v8.12：轮内回退点（文件存在时记录旧内容，回退=恢复；不存在则回退=删除）
  if (ctx && ctx.rollbackNo && typeof SnapRound !== 'undefined') {
    await SnapRound.rollbackPoint(rel, 'delete_file', ctx.rollbackNo);
  }
  await deleteEntry(rel);
  Tree.refresh().catch(() => {});
  await snapTreeAfter(rel, 'delete', cfg, ctx); // v8.5: 依赖树更新（从树移除被删文件）
  return { ok: true, output: `已删除 ${rel}` };
}

async function execGrep(args) {
  // v6.4: bridge 模式下走 Python 后端搜索（性能更好，不用把文件全读到浏览器）
  if (FS.mode === 'bridge' && FS.bridge.authorized) {
    try {
      const data = await bridgeGrep({
        pattern: args.pattern || '',
        path: args.path || '',
        glob: args.glob || '',
        ignoreCase: !!args.ignore_case,
        lineNumbers: true,
      });
      if (!data.ok) return { ok: false, output: '搜索失败' };
      if (!data.count) return { ok: true, output: `未找到匹配 "${args.pattern}"（扫描 ${data.scanned_lines} 行）` };
      const lines = (data.matches || []).map((m) => `${m.file}:${m.line}: ${m.text}`);
      return { ok: true, output: `匹配 ${data.count} 处（扫描 ${data.scanned_lines} 行）：\n` + lines.join('\n') };
    } catch (e) {
      return { ok: false, output: '后端搜索失败: ' + e.message };
    }
  }
  let pattern;
  try {
    pattern = new RegExp(args.pattern, args.ignore_case ? 'i' : ''); // v8.13：尊重 ignore_case
  } catch (e) {
    return { ok: false, output: '正则无效: ' + e.message };
  }
  const walk = await walkFs(args.path || '');
  const globRx = args.glob ? globToRegExp(args.glob) : null;
  let matches = 0;
  const lines = [];
  const maxMatches = 200;
  for (const f of walk.files) {
    if (lines.length >= maxMatches) break;
    if (globRx && !globRx.test(f.path)) continue;
    let text;
    try {
      const d = await readFile(f.path);
      text = d.content || '';
    } catch (e) { continue; }
    const ls = text.split('\n');
    for (let i = 0; i < ls.length; i++) {
      if (ls[i].length > 5000) continue; // 跳过超长行
      if (pattern.test(ls[i])) {
        lines.push(`${f.path}:${i + 1}: ${ls[i].slice(0, 300)}`);
        matches++;
        if (matches >= maxMatches) break;
      }
    }
  }
  if (!lines.length) return { ok: true, output: `未找到匹配 "${args.pattern}"` };
  const out = `匹配 ${matches} 处：\n` + lines.join('\n') + (walk.truncated ? '\n(扫描达到上限，结果可能不完整)' : '');
  return { ok: true, output: out };
}

async function execGlob(args) {
  // v6.4: bridge 模式下走 Python 后端 glob
  if (FS.mode === 'bridge' && FS.bridge.authorized) {
    try {
      const data = await bridgeGlob({
        pattern: args.pattern || '**/*',
        path: args.path || '',
      });
      if (!data.ok) return { ok: false, output: 'glob 失败' };
      if (!data.count) return { ok: true, output: `无匹配文件: ${args.pattern || '**/*'}` };
      return { ok: true, output: `匹配 ${data.count} 个文件:\n` + (data.files || []).join('\n') };
    } catch (e) {
      return { ok: false, output: '后端 glob 失败: ' + e.message };
    }
  }
  const pattern = normPath(args.pattern || '');
  if (!pattern) return { ok: false, output: 'pattern 不能为空' };
  const base = normPath(args.path || '');
  const rx = globToRegExp(pattern);
  const walk = await walkFs(base);
  const hits = walk.files.map((f) => f.path).filter((p) => rx.test(p));
  if (!hits.length) return { ok: true, output: `无匹配文件: ${pattern}` };
  return { ok: true, output: `匹配 ${hits.length} 个文件:\n` + hits.join('\n') + (walk.truncated ? '\n(扫描达到上限)' : '') };
}

async function execTermCreate(args, ctx, cfg) {
  if (!FS.bridge.authorized) return { ok: false, output: '命令桥未授权：请在设置中授权命令桥工作区。' };
  const body = {};
  if (args.name !== undefined) body.name = String(args.name);
  if (args.cwd !== undefined) body.cwd = String(args.cwd);
  if (args.env_vars !== undefined) body.env_vars = args.env_vars;
  const r = await api('/api/bridge/term/create', { method: 'POST', body });
  if (!r || !r.ok) return { ok: false, output: (r && r.detail) || r?.message || '创建失败' };
  return { ok: true, output: `环境已创建：${r.env.name}（id: ${r.env.id}）` };
}

async function execTermList(args, ctx, cfg) {
  if (!FS.bridge.authorized) return { ok: false, output: '命令桥未授权：请在设置中授权命令桥工作区。' };
  const r = await api('/api/bridge/term/list', { method: 'GET' });
  if (!r) return { ok: false, output: '查询失败' };
  const envs = r.envs || [];
  if (!envs.length) return { ok: true, output: '暂无持久命令行环境（可用 term_create 创建）。' };
  const lines = envs.map(e => `- [${e.id}] ${e.name} | cwd=${e.cwd_rel || '.'} | last=${e.last_cmd || '-'} | rc=${e.last_rc ?? '-'}`);
  return { ok: true, output: `持久命令行环境（${envs.length} 个）：\n` + lines.join('\n') };
}

async function execTermDelete(args, ctx, cfg) {
  if (!FS.bridge.authorized) return { ok: false, output: '命令桥未授权：请在设置中授权命令桥工作区。' };
  if (!args.id) return { ok: false, output: '缺少 id 参数' };
  const r = await api('/api/bridge/term/delete', { method: 'POST', body: { id: String(args.id) } });
  if (!r || !r.ok) return { ok: false, output: (r && r.detail) || r?.message || '删除失败' };
  return { ok: true, output: `已删除环境：${args.id}` };
}

async function execRunCommand(args, ctx, cfg) {
  if (!FS.bridge.authorized) {
    return { ok: false, output: '命令桥未授权：请在设置中授权命令桥工作区，然后重试。' };
  }
  const blocked = await guardBlocked(ctx); // v8.5: 只读拦截（P2-3：ctx 透传，失败以 note 呈现）
  if (blocked) return { ok: false, output: '命令执行被拦截：' + blocked };
  const command = String(args.command || '').trim();
  if (!command) return { ok: false, output: 'command 不能为空' };
  // v8.25 用户文件保护：命令串提及用户资产文件名直接拦截（后端同样会拦）
  if (cfg.ENABLE_USER_FILE_PROTECT !== false && _commandTouchesUserAsset(command)) {
    return { ok: false, output: '[用户文件保护] 命令涉及用户手工资产（PPT/Excel/Word/PDF等），AI不允许执行。请先提示用户一键备份，并由用户手动执行。' };
  }
  // v8.13：dangerOk 只应在「本地判定危险且用户已确认」时为 true。
  // 本地未判危险的命令保持 false——若后端命中额外危险模式返回 403，再二次确认重试，
  // 避免前端漏判时零确认执行（P1 修复）。
  const dangerous = isDangerousCommand(command);
  // v8.5 批次2：审批四模式路由（all/danger/copilot/free），对齐桌面 _request_approval
  const allowed = await requestApproval({ command, tool: 'run_command', dangerous }, cfg);
  if (!allowed) return { ok: false, output: '用户拒绝了命令执行。' };
  const lines = [];
  const cap = 500;
  const exec = async (dangerOk) => bridgeRunCommand({
    command,
    cwd: args.cwd,
    timeout: args.timeout,
    dangerOk,
    signal: ctx.signal,
    // v8.18：持久环境 / 心跳间隔 / 任务后杀进程
    env: args.env,
    updateInterval: args.update_interval,
    killAfter: args.kill_after,
    onUpdate(u) {
      if (ctx.emit) ctx.emit('cmd_update', { elapsed_s: u.elapsed_s, tail: u.tail });
    },
    onLine(line) {
      lines.push(line);
      if (lines.length > cap) lines.shift();
      if (ctx.emit) ctx.emit('cmd_output', { line });
    },
  });
  let result;
  try {
    result = await exec(dangerous);
  } catch (e) {
    const is403 = !!(e && (e.status === 403 || String((e && e.message) || e).indexOf('403') !== -1));
    if (!dangerous && is403) {
      // 后端危险拦截：本地漏判时补一次用户确认后带 danger_ok 重试
      const ok2 = await Approval.request({ command, tool: 'run_command', dangerous: true });
      if (!ok2) return { ok: false, output: '用户拒绝了命令执行（后端判定为危险命令）。' };
      lines.length = 0;
      result = await exec(true);
    } else {
      throw e;
    }
  }
  // v8.18：默认值 warning 注入输出首行（AI 可见并可在下次显式传参）
  const warnLine = (result.warnings && result.warnings.length)
    ? '[WARN] 使用默认值：' + result.warnings.join('；') + '\n' : '';
  if (result.timedOut) {
    const kept = result.leftRunning ? '（kill_after=false：进程已保留后台运行）' : '';
    return { ok: false, output: warnLine + `命令超时被终止。${kept}\n${lines.join('\n')}` };
  }
  if (result.error) {
    return { ok: false, output: warnLine + `命令执行失败：${result.error}${lines.length ? '\n' + lines.join('\n') : ''}` };
  }
  const out = lines.join('\n');
  const summary = result.rc === 0 ? `命令成功（退出码 0）` : `命令失败（退出码 ${result.rc}）`;
  return { ok: result.rc === 0, output: warnLine + (out ? `${summary}:\n${out}` : summary) };
}

async function execDelegate(args, ctx, cfg) {
  if (!cfg.ENABLE_SUBAGENT) {
    // P2-4（查修）：未开启子Agent 时退化为普通 LLM 单次调用（此前空 if 形同虚设）
    try {
      const resp = await llmChat({
        stream: false,
        messages: [
          { role: 'system', content: '你是子任务执行 Agent。只输出最终结果，简洁直接，不要复述任务。' },
          { role: 'user', content: String(args.task || '') + (args.context ? '\n\n背景:\n' + String(args.context) : '') },
        ],
        signal: ctx ? ctx.signal : null,
      });
      return { ok: true, output: String((resp && resp.content) || '（空结果）') };
    } catch (e) {
      return { ok: false, output: '子任务执行失败: ' + String((e && e.message) || e) };
    }
  }
  if (typeof Agent !== 'undefined' && Agent.runSubagent) {
    const summary = await Agent.runSubagent(args.task, args.context || '', ctx);
    return { ok: true, output: summary };
  }
  return { ok: false, output: '子Agent 未就绪' };
}

async function execSearchAssets(args) {
  if (typeof Vault === 'undefined') return { ok: false, output: '资产银行未加载' };
  const hits = await Vault.search(args.query || '');
  if (!hits.length) return { ok: true, output: '资产银行无匹配资产。' };
  const lines = hits.map((h, i) => `[${i + 1}] ${h.title} (相似度 ${h.score.toFixed(2)})\n    kind=${h.kind} tags=${(h.tags || []).join(',')}\n    ${(h.scene || h.description || '').slice(0, 120)}\n    ---content---\n${String(h.content || '').slice(0, 800)}`);
  return { ok: true, output: `检索到 ${hits.length} 条资产:\n` + lines.join('\n') };
}

async function execStoreAsset(args) {
  if (typeof Vault === 'undefined') return { ok: false, output: '资产银行未加载' };
  await Vault.store({
    title: String(args.title || '未命名资产'),
    content: String(args.content || ''),
    kind: args.kind || 'code',
    tags: Array.isArray(args.tags) ? args.tags : [],
    scene: args.scene || '',
  });
  if (App.onAgentEvent) App.onAgentEvent({ type: 'vault_stored', asset: { title: args.title } });
  return { ok: true, output: `已存入资产银行: ${args.title}` };
}

/* ---------- v8.9 资料检修 + 自研工具库桥接 ---------- */
async function execInspectAsset() {
  try {
    const r = await api('/api/bridge/assets/inspect', { method: 'POST', body: {} });
    if (!r || !r.ok) return { ok: false, output: '资产检查失败' };
    const probs = r.problems || [];
    if (!probs.length) return { ok: true, output: `资产银行共 ${r.total} 条记录，全部健康。`, meta: r };
    const lines = probs.slice(0, 50).map((p) => `- [${p.id}] ${p.title}: ${(p.problems || []).join('; ')}`);
    return {
      ok: true,
      output: `资产银行共 ${r.total} 条，${r.healthy} 条健康、${probs.length} 条有问题：\n` + lines.join('\n')
        + (probs.length > 50 ? '\n...(仅列前 50 条)' : '') + '\n可用 repair_assets 批量修复可自动修复的问题。',
      meta: r,
    };
  } catch (e) {
    return { ok: false, output: '资产检查失败: ' + String((e && e.message) || e) };
  }
}

async function execRepairAssets(args) {
  try {
    const r = await api('/api/bridge/assets/repair', {
      method: 'POST',
      body: { dry_run: !!args.dry_run },
    });
    if (!r || !r.ok) return { ok: false, output: '资产修复失败' };
    const remaining = r.problems_remaining || [];
    return {
      ok: true,
      output: `${args.dry_run ? '[试运行] ' : ''}修复 ${r.repaired} 条资产记录；剩余问题 ${remaining.length} 条。`
        + (remaining.length ? '\n' + remaining.slice(0, 20).map((p) => `- [${p.id}] ${p.title}: ${(p.problems || []).join('; ')}`).join('\n') : ''),
      meta: r,
    };
  } catch (e) {
    return { ok: false, output: '资产修复失败: ' + String((e && e.message) || e) };
  }
}

function _llmBody(cfg) {
  return {
    // 网页端配置字段为 base_url（历史原因），桌面桥字段为 api_base；两者都兼容
    api_base: (cfg && (cfg.api_base_url || cfg.base_url)) || '',
    api_key: (cfg && cfg.api_key) || '',
    model: (cfg && cfg.model) || '',
  };
}

async function execBuildTool(args, cfg) {
  if (cfg && cfg.ENABLE_TOOLSMITH === false) return { ok: false, output: '自研工具库未开启。' };
  const requirement = String(args.requirement || '').trim();
  if (!requirement) return { ok: false, output: '必须提供工具需求描述（requirement）。' };
  try {
    const r = await api('/api/bridge/toolsmith/build', {
      method: 'POST',
      body: { requirement, ..._llmBody(cfg) },
    });
    if (!r) return { ok: false, output: '工具构建失败' };
    return { ok: !!r.ok, output: r.output || '', meta: { tool_name: r.tool_name } };
  } catch (e) {
    return { ok: false, output: '工具构建失败: ' + String((e && e.message) || e) };
  }
}

async function execListTools() {
  try {
    const r = await api('/api/bridge/toolsmith/list', { method: 'POST', body: {} });
    if (!r || !r.ok) return { ok: false, output: '读取自研工具库失败' };
    const tools = r.tools || [];
    if (!tools.length) return { ok: true, output: '自研工具库暂无过审工具。可用 build_tool 构建。' };
    const lines = tools.map((t) => `- ${t.name}（${t.kind}）: ${t.description || ''}\n  参数: ${(t.params || []).join(', ') || '无'}`);
    return { ok: true, output: '[自研工具]\n' + lines.join('\n'), meta: { count: tools.length } };
  } catch (e) {
    return { ok: false, output: '读取自研工具库失败: ' + String((e && e.message) || e) };
  }
}

async function execUseTool(args, cfg) {
  if (cfg && cfg.ENABLE_TOOLSMITH === false) return { ok: false, output: '自研工具库未开启。' };
  const name = String(args.name || '').trim();
  if (!name) return { ok: false, output: '必须提供工具名（name）。' };
  try {
    const r = await api('/api/bridge/toolsmith/use', {
      method: 'POST',
      body: { name, inputs: args.inputs || {}, ..._llmBody(cfg) },
    });
    // v8.14：显式映射 executeTool 契约（{ok,output}）——后端结构缺 output 时
    // 不再把原始响应直接透传给 LLM（会变成空工具消息）
    return { ok: !!r.ok, output: String((r && r.output) || ''), meta: { tool_name: name } };
  } catch (e) {
    return { ok: false, output: '自研工具执行失败: ' + String((e && e.message) || e) };
  }
}

async function execListToolBugs() {
  try {
    const r = await api('/api/bridge/toolsmith/bugs', { method: 'POST', body: {} });
    if (!r || !r.ok) return { ok: false, output: '读取工具 bug 库失败' };
    const bugs = r.bugs || [];
    if (!bugs.length) return { ok: true, output: '无待修工具 bug。' };
    const agg = {};
    bugs.forEach((b) => { agg[b.tool_name] = (agg[b.tool_name] || 0) + 1; });
    const lines = Object.entries(agg).sort((a, b) => b[1] - a[1]).map(([n, c]) => `- ${n}: ${c} 条 bug`);
    const recent = bugs.slice(-5).map((b) => `  [${b.bug_id}] ${b.tool_name}: ${(b.reason || '').slice(0, 80)}`);
    return { ok: true, output: `待修工具 bug（共 ${bugs.length} 条）：\n` + lines.join('\n') + '\n最近 5 条：\n' + recent.join('\n'), meta: { count: bugs.length } };
  } catch (e) {
    return { ok: false, output: '读取工具 bug 库失败: ' + String((e && e.message) || e) };
  }
}

async function execFixToolBugs(args, cfg) {
  if (cfg && cfg.ENABLE_TOOL_DOCTOR === false) return { ok: false, output: '工具医生未开启。' };
  try {
    return await api('/api/bridge/toolsmith/fix', {
      method: 'POST',
      body: { limit: args.limit || 5, ..._llmBody(cfg) },
    });
  } catch (e) {
    return { ok: false, output: '工具修复失败: ' + String((e && e.message) || e) };
  }
}

async function execClearToolBug(args) {
  const bug_id = String(args.bug_id || '').trim();
  if (!bug_id) return { ok: false, output: '必须提供 bug_id。' };
  try {
    const r = await api('/api/bridge/toolsmith/clear_bug', { method: 'POST', body: { bug_id } });
    return { ok: !!r.ok, output: r.ok ? `bug ${bug_id} 已标记为已修。` : `未找到 pending 的 bug ${bug_id}。` };
  } catch (e) {
    return { ok: false, output: '标记 bug 失败: ' + String((e && e.message) || e) };
  }
}

async function execPlanAndExecute(args, ctx, cfg) {
  if (!cfg.ENABLE_AOE) {
    return { ok: false, output: 'AOE 并行规划未开启（设置中可开启）。' };
  }
  if (typeof Agent !== 'undefined' && Agent.planAndExecute) {
    // v8.13：schema 与桌面端统一为 task（兼容旧 goal 参数）
    const summary = await Agent.planAndExecute(String(args.task || args.goal || ''), ctx);
    return { ok: true, output: summary };
  }
  return { ok: false, output: 'AOE 规划器未就绪' };
}

/* ---------- v8.4 Python 应用截图 / UI 自截图确认（网页版） ---------- */
async function execAppScreenshot(args, ctx, cfg) {
  if (!FS.bridge.authorized) {
    return { ok: false, output: '命令桥未授权：请在设置中授权命令桥工作区，然后重试。' };
  }
  const script = String(args.script || '').trim();
  const path = normPath(args.path || '');
  if (!script) return { ok: false, output: 'script 不能为空' };
  if (!/\.(png|jpe?g)$/i.test(path)) return { ok: false, output: 'path 必须以 .png/.jpg/.jpeg 结尾' };
  // 运行脚本等同执行代码：与 run_command 一致走审批门（command 供审批卡片展示）
  // v8.5 批次2：四模式路由（all/danger/copilot/free）
  const allowed = await requestApproval({
    command: `python ${script} 并截图到 ${path}`,
    script, path, tool: 'app_screenshot',
  }, cfg);
  if (!allowed) return { ok: false, output: '用户拒绝了脚本运行。' };
  const body = { script, path };
  if (args.title) body.title = String(args.title);
  if (args.timeout != null) body.timeout = parseInt(args.timeout, 10);
  if (Array.isArray(args.extra_args)) body.extra_args = args.extra_args;
  try {
    const r = await api('/api/bridge/app_screenshot', { method: 'POST', body, signal: ctx.signal });
    return { ok: !!r.ok, output: r.output || (r.ok ? '截图完成' : '截图失败') };
  } catch (e) {
    if (e && e.name === 'AbortError') return { ok: false, output: '已停止' };
    return { ok: false, output: String((e && e.message) || e) };
  }
}

async function execUiReview(args, ctx, cfg) {
  if (!FS.bridge.authorized) {
    return { ok: false, output: '命令桥未授权：请在设置中授权命令桥工作区，然后重试。' };
  }
  const path = normPath(args.path || '');
  if (!path) return { ok: false, output: 'path 不能为空' };
  // 读取截图 base64（后端桥限制 3MB）
  let img;
  try {
    img = await api('/api/bridge/fs/image?path=' + encodeURIComponent(path));
  } catch (e) {
    return { ok: false, output: '读取截图失败：' + String((e && e.message) || e) };
  }
  if (!img || !img.ok || !img.base64) return { ok: false, output: '读取截图失败：无法获取图片数据' };
  // 视觉专家模型：显式 model 参数 > 设置里的视觉专家模型 > 当前模型
  const model = String(args.model || '').trim() || String(cfg.visual_expert_model || '').trim() || String(cfg.model || '').trim();
  if (!model) return { ok: false, output: '未配置模型，无法进行视觉审查。' };
  const prompt = String(args.prompt || '请仔细描述这张 UI 截图的布局、配色与问题，并给出具体改进建议。');
  const messages = [{
    role: 'user',
    content: [
      { type: 'text', text: prompt },
      { type: 'image_url', image_url: { url: `data:${img.mime || 'image/png'};base64,${img.base64}` } },
    ],
  }];
  // 复用 llmChat 但指定视觉模型（非流式单次审查；透传 signal 支持"停止"）
  const res = await llmChat({ messages, stream: false, model, signal: ctx.signal });
  const content = String(res.content || '').trim();
  if (!content) return { ok: false, output: '视觉审查无返回内容。' };
  return { ok: true, output: `[视觉专家 ${model} 审查 ${path}]\n${content}` };
}

/* ---------- v8.5.7 补齐：文件搜索 / 元工具 / 联网搜索 / 浏览器 / 暂存 / checkpoint ---------- */
async function execFileSearch(args) {
  const query = String(args.query || '').trim();
  if (!query) return { ok: false, output: 'query 不能为空' };
  const walk = await walkFs(args.path || '');
  const globRx = args.glob ? globToRegExp(args.glob) : null;
  const cands = [];
  for (const f of walk.files) {
    if (globRx && !globRx.test(f.path)) continue;
    const name = String(f.path).split('/').pop();
    const score = charSimilarity(String(f.path) + ' ' + name, query);
    cands.push({ path: f.path, score });
  }
  cands.sort((a, b) => b.score - a.score);
  let limit = 15;
  if (args.limit != null) limit = Math.max(1, parseInt(args.limit, 10) || 15);
  const hits = cands.slice(0, limit);
  if (!hits.length) return { ok: true, output: `未找到与「${query}」相关的文件。` };
  const lines = hits.map((h) => `- ${h.path}（相似度 ${h.score.toFixed(2)}）`);
  return { ok: true, output: `文件匹配（共 ${hits.length} 个）:\n` + lines.join('\n') };
}

async function execSearchTool(args) {
  const defs = getToolDefs();
  const query = String(args.query || '').trim().toLowerCase();
  const names = defs.map((d) => d.function.name);
  if (!query) {
    return { ok: true, output: '当前可用工具：' + (names.join(', ') || '无') + '。用 query 参数查具体用法。' };
  }
  const hits = defs.filter((d) => {
    const fn = d.function;
    return fn.name.includes(query) || String(fn.description || '').toLowerCase().includes(query);
  });
  if (!hits.length) return { ok: true, output: `没有与「${args.query}」相关的工具。` };
  const lines = hits.map((d) => `- ${d.function.name}: ${String(d.function.description || '').slice(0, 120)}`);
  return { ok: true, output: '[匹配的工具]\n' + lines.slice(0, 8).join('\n') };
}

async function execWebSearch(args, ctx) {
  if (App.config && App.config.ENABLE_WEB_SEARCH === false) return { ok: false, output: '互联网搜索未开启。' };
  const query = String(args.query || '').trim();
  if (!query) return { ok: false, output: 'query 为空。' };
  try {
    const r = await api('/api/bridge/web_search', {
      method: 'POST', body: { query, limit: args.limit }, signal: ctx ? ctx.signal : null,
    });
    const results = (r && r.results) || [];
    const channel = (r && r.channel) || 'DuckDuckGo';
    if (!results.length) return { ok: false, output: `搜索失败或无结果（${channel}）。可换个关键词或稍后重试。` };
    const raw = results.map((x) => `- ${x.title}\n  ${x.url}\n  ${x.snippet || ''}`).join('\n');
    return { ok: true, output: `[搜索：${query} | 通道：${channel}]\n${raw}`, meta: { results, channel } };
  } catch (e) {
    if (e && e.name === 'AbortError') return { ok: false, output: '已停止' };
    return { ok: false, output: '搜索失败: ' + String((e && e.message) || e) };
  }
}

async function execBrowserOpen(args) {
  if (App.config && App.config.ENABLE_BROWSER === false) return { ok: false, output: '浏览器控制未开启。' };
  const url = String(args.url || '').trim();
  if (!/^https?:\/\//i.test(url) || url.length > 2048 || url.includes(' ')) {
    return { ok: false, output: 'URL 无效：仅支持 http(s) 且长度 ≤2048。' };
  }
  try {
    // 注意：带 'noopener' 特性时 window.open 恒返回 null，不能用返回值判“是否被拦截”。
    const w = window.open(url, '_blank');
    if (w) { try { w.opener = null; } catch (e) { /* 跨域时忽略 */ } }
    return { ok: true, output: `已请求在新标签页打开：${url}${w ? '' : '（若未弹出请检查浏览器拦截）'}` };
  } catch (e) {
    return { ok: false, output: '打开失败: ' + String((e && e.message) || e) };
  }
}

async function execBrowserRead(args, ctx) {
  if (App.config && App.config.ENABLE_BROWSER === false) return { ok: false, output: '浏览器控制未开启。' };
  const url = String(args.url || '').trim();
  if (!/^https?:\/\//i.test(url)) return { ok: false, output: 'URL 无效：仅支持 http(s)。' };
  try {
    const r = await api('/api/bridge/browser_read', {
      method: 'POST', body: { url, timeout: args.timeout }, signal: ctx ? ctx.signal : null,
    });
    return { ok: !!r.ok, output: r.output || (r.ok ? '已读取' : '读取失败') };
  } catch (e) {
    if (e && e.name === 'AbortError') return { ok: false, output: '已停止' };
    return { ok: false, output: String((e && e.message) || e) };
  }
}

async function execBrowserScreenshot(args, ctx, cfg) {
  if (App.config && App.config.ENABLE_BROWSER === false) return { ok: false, output: '浏览器控制未开启。' };
  if (!FS.bridge.authorized) return { ok: false, output: '命令桥未授权：请在设置中授权命令桥工作区，然后重试。' };
  const url = String(args.url || '').trim();
  const path = normPath(args.path || '');
  if (!/^https?:\/\//i.test(url)) return { ok: false, output: 'URL 无效：仅支持 http(s)。' };
  if (!/\.(png|jpe?g)$/i.test(path)) return { ok: false, output: 'path 必须以 .png/.jpg/.jpeg 结尾' };
  try {
    const r = await api('/api/bridge/browser_screenshot', {
      method: 'POST', body: { url, path, timeout: args.timeout }, signal: ctx ? ctx.signal : null,
    });
    return { ok: !!r.ok, output: r.output || (r.ok ? '截图完成' : '截图失败') };
  } catch (e) {
    if (e && e.name === 'AbortError') return { ok: false, output: '已停止' };
    return { ok: false, output: String((e && e.message) || e) };
  }
}

/* ===================================================================
 * v8.23 浏览器直控（CDP）+ 外部软件（exe）自动化
 * 后端桥：/api/bridge/browser_ctl 与 /api/bridge/exe（复用桌面成熟实现，
 * 服务端硬校验：exe_close 白名单、click 越界拒绝、screenshot 保护目录）
 * =================================================================== */

async function execBrowserCtlLaunch(args, ctx, cfg) {
  if (cfg.ENABLE_BROWSER_CTL === false || cfg.ENABLE_BROWSER === false) {
    return { ok: false, output: '直接操控浏览器未开启（ENABLE_BROWSER_CTL/ENABLE_BROWSER）。' };
  }
  if (!FS.bridge.authorized) return { ok: false, output: '命令桥未授权：请先在设置中授权命令桥。' };
  let url = String(args.url || '').trim();
  if (!url) url = location.origin; // 默认打开本机网页版
  if (!/^https?:\/\//i.test(url) || url.length > 2048) {
    return { ok: false, output: 'URL 无效：仅支持 http(s) 且长度 ≤2048。' };
  }
  // 启动进程按危险操作走审批（受控实例虽隔离，但会在用户机器上拉起浏览器进程）
  const allowed = await requestApproval({ command: 'browser_launch ' + url, tool: 'browser_launch', dangerous: true }, cfg);
  if (!allowed) return { ok: false, output: '用户未批准启动受控浏览器。' };
  try {
    const r = await api('/api/bridge/browser_ctl', {
      method: 'POST', body: { action: 'launch', url }, signal: ctx ? ctx.signal : null,
    });
    return { ok: !!r.ok, output: r.output || (r.ok ? '受控浏览器已启动' : '启动失败') };
  } catch (e) {
    if (e && e.name === 'AbortError') return { ok: false, output: '已停止' };
    return { ok: false, output: String((e && e.message) || e) };
  }
}

async function execBrowserCtlOp(args, ctx, action) {
  if (!FS.bridge.authorized) return { ok: false, output: '命令桥未授权：请先在设置中授权命令桥。' };
  const body = { action };
  if (action === 'navigate') {
    const url = String(args.url || '').trim();
    if (!/^https?:\/\//i.test(url) || url.length > 2048) {
      return { ok: false, output: 'URL 无效：仅支持 http(s) 且长度 ≤2048。' };
    }
    body.url = url;
  } else if (action === 'click') {
    body.selector = String(args.selector || '').slice(0, 500);
    body.text = String(args.text || '').slice(0, 500);
    if (!body.selector && !body.text) return { ok: false, output: '需要 selector 或 text 之一。' };
  } else if (action === 'type') {
    body.text = String(args.text || '');
    if (!body.text || body.text.length > 2000) return { ok: false, output: 'text 无效（1-2000 字符）。' };
  } else if (action === 'press_keys') {
    body.keys = String(args.keys || '').trim();
    if (!body.keys || body.keys.length > 100) return { ok: false, output: 'keys 无效。' };
  }
  try {
    const r = await api('/api/bridge/browser_ctl', {
      method: 'POST', body, signal: ctx ? ctx.signal : null,
    });
    return { ok: !!r.ok, output: r.output || (r.ok ? '操作完成' : '操作失败') };
  } catch (e) {
    if (e && e.name === 'AbortError') return { ok: false, output: '已停止' };
    return { ok: false, output: String((e && e.message) || e) };
  }
}

async function execExeOp(args, ctx, cfg, action, dangerous) {
  if (action === 'journal') {
    if (cfg.ENABLE_EXE_JOURNAL === false) return { ok: false, output: '外部软件操作记录未开启（ENABLE_EXE_JOURNAL）。' };
  } else if (cfg.ENABLE_EXE_AUTOMATE === false) {
    return { ok: false, output: '外部软件自动化未开启（ENABLE_EXE_AUTOMATE）。' };
  }
  if (action !== 'journal' && !FS.bridge.authorized) {
    return { ok: false, output: '命令桥未授权：请先在设置中授权命令桥。' };
  }
  const body = { action };
  let desc = '';
  if (action === 'launch') {
    body.path = String(args.path || '').trim();
    if (!body.path || body.path.length > 500) return { ok: false, output: 'path 无效。' };
    if (Array.isArray(args.args)) body.args = args.args.slice(0, 20).map((x) => String(x).slice(0, 500));
    desc = 'exe_launch ' + body.path + (body.args ? ' ' + body.args.join(' ') : '');
  } else if (action === 'screenshot') {
    body.path = normPath(args.path || '');
    if (!/\.(png|jpe?g)$/i.test(body.path)) return { ok: false, output: 'path 必须以 .png/.jpg/.jpeg 结尾' };
    if (args.hwnd) body.hwnd = parseInt(args.hwnd, 10) || 0;
    if (args.title) body.title = String(args.title).slice(0, 200);
    if (!body.hwnd && !body.title) return { ok: false, output: '需要 hwnd 或 title 之一（可先 exe_list_windows 查）。' };
    desc = 'exe_screenshot → ' + body.path;
  } else if (action === 'click') {
    body.x = parseInt(args.x, 10);
    body.y = parseInt(args.y, 10);
    if (!(body.x >= 0) || !(body.y >= 0)) return { ok: false, output: '缺少有效坐标 x/y（≥0）。' };
    desc = `exe_click (${body.x},${body.y})`;
  } else if (action === 'type') {
    body.text = String(args.text || '');
    if (!body.text || body.text.length > 2000) return { ok: false, output: 'text 无效（1-2000 字符）。' };
    desc = 'exe_type "' + (body.text.length > 60 ? body.text.slice(0, 60) + '…' : body.text) + '"';
  } else if (action === 'press_keys') {
    body.keys = String(args.keys || '').trim();
    if (!body.keys || body.keys.length > 100) return { ok: false, output: 'keys 无效。' };
    desc = 'exe_press_keys ' + body.keys;
  } else if (action === 'bring_to_front') {
    body.hwnd = parseInt(args.hwnd, 10) || 0;
    if (!body.hwnd) return { ok: false, output: '缺少 hwnd。' };
    desc = 'exe_bring_to_front ' + body.hwnd;
  } else if (action === 'close') {
    if (args.pid) body.pid = parseInt(args.pid, 10) || 0;
    if (args.hwnd) body.hwnd = parseInt(args.hwnd, 10) || 0;
    if (!body.pid && !body.hwnd) return { ok: false, output: '需要 hwnd 或 pid 之一。' };
    desc = 'exe_close ' + (body.pid ? 'pid=' + body.pid : 'hwnd=' + body.hwnd);
  } else if (action === 'journal') {
    if (args.tail) body.tail = parseInt(args.tail, 10) || 50;
    if (args.tool) body.tool = String(args.tool).slice(0, 80);
  }
  // 真实桌面操作（启动/点击/输入/按键/关闭）强制用户审批
  if (dangerous) {
    const allowed = await requestApproval({ command: desc, tool: action, dangerous: true }, cfg);
    if (!allowed) return { ok: false, output: '用户未批准该外部软件操作。' };
  }
  try {
    const r = await api('/api/bridge/exe', {
      method: 'POST', body, signal: ctx ? ctx.signal : null,
    });
    if (action === 'list_windows' && r.ok) {
      const ws = r.windows || [];
      const lines = ws.slice(0, 40).map((w) => `- hwnd=${w.hwnd} pid=${w.pid} | ${w.title}`);
      return { ok: true, output: `可见顶层窗口（共 ${ws.length} 个，最多列 40）：\n` + lines.join('\n'),
               meta: { count: ws.length } };
    }
    return { ok: !!r.ok, output: r.output || (r.ok ? '操作完成' : '操作失败') };
  } catch (e) {
    if (e && e.name === 'AbortError') return { ok: false, output: '已停止' };
    return { ok: false, output: String((e && e.message) || e) };
  }
}

/* ===================================================================
   * v8.14 F12 开发者工具（网页版——当前页面自检）
   * 通过浏览器 Performance API / Storage API / document.scripts 获取信息。
   * =================================================================== */

function execBrowserNetworksGet(args) {
  try {
    const filterUrl = String(args.filter_url || '').toLowerCase();
    const filterType = String(args.filter_type || '').toLowerCase();
    const maxResults = Math.min(Math.max(parseInt(args.max_results) || 50, 1), 200);

    const entries = performance.getEntriesByType('resource');
    let requests = entries.map(e => ({
      url: e.name,
      type: e.initiatorType,
      status: e.responseStatus || 0,
      size: e.transferSize || 0,
      duration: Math.round(e.duration),
      startTime: Math.round(e.startTime),
    }));

    if (filterUrl) requests = requests.filter(r => r.url.toLowerCase().includes(filterUrl));
    if (filterType) requests = requests.filter(r => r.type.toLowerCase() === filterType);

    requests = requests.slice(0, maxResults);
    return { ok: true, output: `返回 ${requests.length} 条网络请求`, requests, total: entries.length };
  } catch (e) {
    return { ok: false, output: '获取网络请求失败: ' + (e.message || e) };
  }
}

function execBrowserStorageGet(args) {
  try {
    const stype = String(args.storage_type || 'all').toLowerCase();
    // v8.14：与 network_curl 同策略 fail-closed——存储值必须打码后才能进入工具结果
    //（deverai.v2.cfg 含明文 api_key、deverai_token 是会话令牌，直接导出会泄露给 LLM 供应商）
    const SENSITIVE_KEY_RE = /token|key|secret|password|authorization|session/i;
    const _maskVal = (key, val) => {
      if (SENSITIVE_KEY_RE.test(String(key))) return '[已打码:敏感凭据]';
      if (typeof DevTools !== 'undefined' && typeof DevTools.mask === 'function') return DevTools.mask(val);
      return val;
    };
    const result = {};

    if (stype === 'cookies' || stype === 'all') {
      result.cookies = document.cookie ? document.cookie.split(';').map(c => {
        const [k, ...v] = c.trim().split('=');
        return { name: k, value: _maskVal(k, v.join('=')) };
      }) : [];
    }
    if (stype === 'localstorage' || stype === 'all') {
      const items = {};
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k) items[k] = _maskVal(k, (localStorage.getItem(k) || '').substring(0, 2000));
      }
      result.localStorage = Object.entries(items).map(([key, value]) => ({ key, value }));
    }
    if (stype === 'sessionstorage' || stype === 'all') {
      const items = {};
      for (let i = 0; i < sessionStorage.length; i++) {
        const k = sessionStorage.key(i);
        if (k) items[k] = _maskVal(k, (sessionStorage.getItem(k) || '').substring(0, 2000));
      }
      result.sessionStorage = Object.entries(items).map(([key, value]) => ({ key, value }));
    }

    const summary = Object.entries(result).map(([k, v]) => `${k}=${v.length}`).join(', ');
    return { ok: true, output: `获取存储（敏感项已打码）: ${summary}`, ...result };
  } catch (e) {
    return { ok: false, output: '获取存储失败: ' + (e.message || e) };
  }
}

function execBrowserStorageSet(args) {
  try {
    const stype = String(args.storage_type || 'localStorage').toLowerCase();
    const key = String(args.key || '');
    const value = String(args.value || '');
    if (!key) return { ok: false, output: '必须提供 key。' };

    if (stype === 'localstorage') {
      localStorage.setItem(key, value.substring(0, 5000));
      return { ok: true, output: `已设置 localStorage: ${key}` };
    } else if (stype === 'sessionstorage') {
      sessionStorage.setItem(key, value.substring(0, 5000));
      return { ok: true, output: `已设置 sessionStorage: ${key}` };
    } else if (stype === 'cookies') {
      document.cookie = `${encodeURIComponent(key)}=${encodeURIComponent(value.substring(0, 4000))}; path=/; SameSite=Lax`;
      return { ok: true, output: `已设置 cookie: ${key}` };
    }
    return { ok: false, output: `不支持的存储类型: ${stype}` };
  } catch (e) {
    return { ok: false, output: '设置存储失败: ' + (e.message || e) };
  }
}

function execBrowserStorageClear(args) {
  try {
    const stype = String(args.storage_type || 'all').toLowerCase();
    if (stype === 'cookies' || stype === 'all') {
      document.cookie.split(';').forEach(c => {
        const [k] = c.trim().split('=');
        if (k) document.cookie = `${k}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
      });
    }
    if (stype === 'localstorage' || stype === 'all') localStorage.clear();
    if (stype === 'sessionstorage' || stype === 'all') sessionStorage.clear();
    return { ok: true, output: `已清除 ${stype} 存储` };
  } catch (e) {
    return { ok: false, output: '清除存储失败: ' + (e.message || e) };
  }
}

// v8.14b：eval 输出侧打码——console_eval 可用脚本直读 localStorage 绕过
// browser_storage_get 的键级遮蔽，这里对返回值做同源策略脱敏（令牌精确替换 +
// KEY_RE 字段遮蔽 + sk- 通用模式），纵深防御而非完美沙箱（产品语义保留 F12 能力）
function _maskEvalOutput(out) {
  let s = String(out);
  try {
    const tok = localStorage.getItem('deverai_token');
    if (tok) s = s.split(tok).join('***');
  } catch (e) { /* 隐私模式等 */ }
  try {
    if (typeof DevTools !== 'undefined' && typeof DevTools.mask === 'function') s = DevTools.mask(s);
  } catch (e) { /* DevTools 未载入 */ }
  return s.replace(/sk-[A-Za-z0-9_-]{6,}/g, 'sk-***');
}

function execBrowserConsoleEval(args) {
  try {
    const expr = String(args.expression || '').trim();
    if (!expr) return { ok: false, output: '缺少要执行的 JavaScript 表达式。' };
    if (expr.length > 2000) return { ok: false, output: '表达式过长（≤2000 字符）。' };

    const fn = new Function(expr);
    const result = fn();
    let output = result === undefined ? '(undefined)' :
      result === null ? '(null)' :
        typeof result === 'object' ? JSON.stringify(result, null, 2) : String(result);
    if (output.length > 2000) output = output.slice(0, 2000) + '…（已截断）';
    return { ok: true, output: _maskEvalOutput(output) };
  } catch (e) {
    return { ok: false, output: '执行失败: ' + (e.message || e) };
  }
}

function execBrowserSourcesList(args) {
  try {
    const filter = String(args.filter_pattern || '').toLowerCase();
    const scripts = [...document.querySelectorAll('script')];
    const sources = scripts.map((s, i) => ({
      index: i,
      src: s.src || '',
      inline: !s.src,
      contentLength: s.textContent ? s.textContent.length : 0,
      id: s.id || '',
      async: s.async,
      defer: s.defer,
    }));

    const filtered = filter
      ? sources.filter(s => (s.src + s.id).toLowerCase().includes(filter))
      : sources;

    return { ok: true, output: `找到 ${filtered.length} 个脚本源`, sources: filtered };
  } catch (e) {
    return { ok: false, output: '获取源文件列表失败: ' + (e.message || e) };
  }
}

function execBrowserFindApiEndpoints(args) {
  try {
    const filterPattern = String(args.filter_pattern || 'api').toLowerCase();
    const minSize = parseInt(args.min_response_size) || 0;

    const entries = performance.getEntriesByType('resource');
    const endpoints = [];
    const seen = new Set();

    for (const e of entries) {
      const url = e.name.toLowerCase();
      if (filterPattern && !url.includes(filterPattern)) continue;
      if (e.transferSize < minSize) continue;
      if (/\.(js|css|png|jpg|jpeg|gif|svg|ico|woff2?|ttf|eot)(\?|$)/.test(url)) continue;

      const key = e.initiatorType + ':' + e.name;
      if (seen.has(key)) continue;
      seen.add(key);

      endpoints.push({
        url: e.name,
        type: e.initiatorType,
        size: e.transferSize || 0,
        duration: Math.round(e.duration),
        status: e.responseStatus || 0,
      });
    }

    endpoints.sort((a, b) => (b.size || 0) - (a.size || 0));
    return { ok: true, output: `找到 ${endpoints.length} 个疑似 API 端点`, endpoints: endpoints.slice(0, 100) };
  } catch (e) {
    return { ok: false, output: '查找 API 端点失败: ' + (e.message || e) };
  }
}

const NOTEPAD_KEY = 'deverai.notepad.v1';
function notepadLoad() {
  try {
    const d = JSON.parse(localStorage.getItem(NOTEPAD_KEY) || '{}');
    return (d && typeof d === 'object' && !Array.isArray(d)) ? d : {};
  } catch (e) { return {}; }
}
function notepadStore(data) {
  try { localStorage.setItem(NOTEPAD_KEY, JSON.stringify(data)); } catch (e) { /* 隐私模式静默 */ }
}

async function execNotepadSave(args) {
  if (App.config && App.config.ENABLE_NOTEPAD === false) return { ok: false, output: 'Notepad 未开启。' };
  const key = String(args.key || '').trim();
  if (!key) return { ok: false, output: '必须提供 key。' };
  const content = String(args.content || '');
  const data = notepadLoad();
  data[key] = { content, ts: Date.now() };
  notepadStore(data);
  return { ok: true, output: `已暂存「${key}」（${content.length} 字符）。` };
}

async function execNotepadRead(args) {
  if (App.config && App.config.ENABLE_NOTEPAD === false) return { ok: false, output: 'Notepad 未开启。' };
  const key = String(args.key || '').trim();
  if (!key) return { ok: false, output: '必须提供 key。' };
  const it = notepadLoad()[key];
  if (!it || !it.content) return { ok: false, output: `暂存「${key}」不存在或为空。` };
  return { ok: true, output: it.content, meta: { key, length: it.content.length } };
}

async function execNotepadList(args) {
  if (App.config && App.config.ENABLE_NOTEPAD === false) return { ok: false, output: 'Notepad 未开启。' };
  const data = notepadLoad();
  const keys = Object.keys(data);
  if (!keys.length) return { ok: true, output: 'Notepad 为空。', meta: { count: 0 } };
  const lines = keys.map((k) => `- ${k}（${new Date(data[k].ts).toISOString()}）: ${String(data[k].content || '').slice(0, 40)}`);
  return { ok: true, output: `Notepad 暂存（共 ${keys.length} 条）:\n` + lines.join('\n'), meta: { count: keys.length } };
}

async function execNotepadClear(args) {
  if (App.config && App.config.ENABLE_NOTEPAD === false) return { ok: false, output: 'Notepad 未开启。' };
  const key = String(args.key || '').trim();
  const data = notepadLoad();
  let n = 0;
  if (key) {
    if (data[key]) { delete data[key]; n = 1; }
  } else {
    n = Object.keys(data).length;
    notepadStore({});
    return { ok: true, output: `已清除全部暂存（${n} 条）。` };
  }
  notepadStore(data);
  return { ok: true, output: `已清除「${key}」暂存（${n} 条）。` };
}

async function execListCheckpoints(args) {
  if (App.config && App.config.ENABLE_CHECKPOINT === false) return { ok: false, output: 'Checkpoint 未开启。' };
  if (!(FS.mode === 'bridge' && FS.bridge.authorized)) {
    return { ok: false, output: '需要命令桥授权才能查询 checkpoint。' };
  }
  try {
    const r = await api('/api/bridge/checkpoint/files');
    if (!r || !r.ok) return { ok: false, output: '读取 checkpoint 失败' };
    let files = r.files || [];
    let limit = 20;
    if (args.limit != null) limit = Math.max(1, parseInt(args.limit, 10) || 20);
    files = files.slice(0, limit);
    if (!files.length) return { ok: true, output: '无 checkpoint 记录。', meta: { count: 0 } };
    const lines = [];
    for (const f of files) {
      const v = (f.versions && f.versions[0]) || {};
      lines.push(`- [${v.ts || f.last_ts || ''}] ${f.rel_path}（task: ${v.task_id || 'default'}）`);
    }
    return { ok: true, output: `最近 checkpoint（共 ${files.length} 个文件）:\n` + lines.join('\n'), meta: { count: files.length } };
  } catch (e) {
    return { ok: false, output: '读取 checkpoint 失败: ' + String((e && e.message) || e) };
  }
}

/* ---------- v8.25：一键备份 + 重名治理 ---------- */
async function execBackupWorkspace(args) {
  if (!(FS.mode === 'bridge' && FS.bridge.authorized)) {
    return { ok: false, output: '需要命令桥授权才能一键备份。' };
  }
  try {
    const r = await api('/api/bridge/backup/full', { method: 'POST', body: { label: String((args && args.label) || 'full') } });
    return { ok: true, output: `已一键备份完整工作区：${r.path}（${r.count} 个文件）`, meta: { path: r.path, count: r.count } };
  } catch (e) {
    return { ok: false, output: '一键备份失败: ' + String((e && e.message) || e) };
  }
}
async function execScanAmbiguous() {
  if (!(FS.mode === 'bridge' && FS.bridge.authorized)) {
    return { ok: false, output: '需要命令桥授权才能扫描重名文件。' };
  }
  try {
    const r = await api('/api/bridge/protect/scan');
    const groups = (r && r.groups) || [];
    if (!groups.length) return { ok: true, output: '未发现重名/命名不清文件。', meta: { count: 0 } };
    const lines = groups.slice(0, 20).map((g) => `- [${g.group}] ${g.reason}\n  ` + g.files.slice(0, 8).join('\n  '));
    return { ok: true, output: `发现 ${r.count} 组重名/命名不清文件，请用户逐组识别（哪个保留、其余备份/转移）：\n` + lines.join('\n'), meta: { count: r.count } };
  } catch (e) {
    return { ok: false, output: '扫描重名文件失败: ' + String((e && e.message) || e) };
  }
}
async function execQuarantineFiles(args, ctx, cfg) {
  const files = Array.isArray(args && args.files) ? args.files.filter((x) => String(x || '').trim()) : [];
  if (!files.length) return { ok: false, output: '请提供 files（要备份/转移的相对路径列表）。' };
  if (!(FS.mode === 'bridge' && FS.bridge.authorized)) {
    return { ok: false, output: '需要命令桥授权才能备份转移。' };
  }
  if (!confirm(`备份转移 ${files.length} 个文件到 backups/quarantine/<时间>/？\n` + files.slice(0, 10).join('\n'))) {
    return { ok: false, output: '用户拒绝了备份转移操作。', meta: { denied: true } };
  }
  try {
    const r = await api('/api/bridge/protect/quarantine', { method: 'POST', body: { files, reason: String((args && args.reason) || '') } });
    return { ok: true, output: `已备份转移 ${r.moved.length} 个文件到 ${r.dir}`, meta: { dir: r.dir, moved: r.moved } };
  } catch (e) {
    return { ok: false, output: '备份转移失败: ' + String((e && e.message) || e) };
  }
}

/* ---------- v8.13.1：Agent 可见开发者网络面板（只读、敏感字段打码） ---------- */
function _networkEntries() {
  if (typeof DevTools === 'undefined' || !DevTools.entries) return [];
  return Array.isArray(DevTools.entries) ? DevTools.entries : [];
}

function _maskNetwork(s) {
  // v8.13.1：打码函数缺失时 fail-closed——隐藏原文，绝不把密钥放进 Agent 输出
  if (typeof DevTools === 'undefined' || typeof DevTools.mask !== 'function') return '[敏感内容已隐藏]';
  return DevTools.mask(s);
}

function execNetworkList(args) {
  const entries = _networkEntries();
  if (!entries.length) {
    return { ok: true, output: 'API 控制台暂无请求记录。发送一条消息后，这里会列出 LLM 代理/命令桥/快照桥请求。' };
  }
  let limit = 10;
  if (args && args.limit != null) limit = Math.max(1, Math.min(parseInt(args.limit, 10) || 10, 50));
  const rows = entries.slice(0, limit).map((e, i) => {
    let body = '';
    if (e.body !== undefined) {
      try { body = _maskNetwork(JSON.stringify(e.body)); } catch (_) { body = String(e.body || ''); }
      body = String(body).slice(0, 300);
    }
    return `${i + 1}. [${e.status || 0}] ${e.method || 'GET'} ${e.url || ''} · ${e.costMs || 0}ms`
      + (body ? `\n   body: ${body}` : '');
  });
  return {
    ok: true,
    output: `最近 ${rows.length} 条网络请求（敏感字段已打码，Cookie 为 HttpOnly 不可见）:\n` + rows.join('\n'),
    meta: { count: entries.length, shown: rows.length },
  };
}

function execNetworkCurl(args) {
  if (typeof DevTools === 'undefined' || typeof DevTools.curlOf !== 'function') {
    return { ok: false, output: 'API 控制台未就绪，无法生成 curl。' };
  }
  const entries = _networkEntries();
  if (!entries.length) return { ok: true, output: '暂无请求记录，无法生成 curl。' };
  let limit = 3;
  if (args && args.limit != null) limit = Math.max(1, Math.min(parseInt(args.limit, 10) || 3, 10));
  const rows = entries.slice(0, limit).map((e) => {
    try {
      const raw = DevTools.curlOf(e);
      // v8.13.1：maskCurl 不可用时 fail-closed（隐藏整条 curl），绝不输出真实 Bearer
      if (typeof DevTools.maskCurl !== 'function') {
        return `# ${e.method || 'GET'} ${e.url || ''}\n（打码功能不可用，curl 已隐藏；请在 API 控制台面板手动查看）`;
      }
      return `# ${e.method || 'GET'} ${e.url || ''}\n${DevTools.maskCurl(raw)}`;
    } catch (_) {
      return `# 无法生成: ${e.method || ''} ${e.url || ''}`;
    }
  });
  return {
    ok: true,
    output: 'curl 预览（Bearer/密钥已打码；真实命令请在 API 控制台面板手动复制）:\n\n' + rows.join('\n\n'),
    meta: { count: entries.length, shown: rows.length },
  };
}

/* ---------- DashScope API 处理器（服务端代理） ---------- */
async function _dashscopeProxy(endpoint, body) {
  try {
    const r = await api(`/api/bridge/dashscope/${endpoint}`, { method: 'POST', body });
    return r || { ok: false, output: '代理调用无响应' };
  } catch (e) {
    return { ok: false, output: `DashScope 代理失败: ${(e && e.message) || e}` };
  }
}

async function execDashscopeImageGenerate(args) {
  const r = await _dashscopeProxy('image_generate', {
    prompt: args.prompt || '',
    model: args.model || '',
    size: args.size || '',
    n: args.n != null ? args.n : 1,
    style: args.style || '',
    negative_prompt: args.negative_prompt || '',
  });
  if (r.ok && r.images) {
    const urls = r.images.map((img) => img.url).join('\n');
    return { ok: true, output: `${r.output}\n图片 URL:\n${urls}`, meta: { images: r.images } };
  }
  return r;
}

async function execDashscopeImageEdit(args) {
  return await _dashscopeProxy('image_edit', {
    image_url: args.image_url || '',
    prompt: args.prompt || '',
    model: args.model || '',
  });
}

async function execDashscopeVideoGenerate(args) {
  return await _dashscopeProxy('video_generate', {
    prompt: args.prompt || '',
    model: args.model || '',
    image_url: args.image_url || '',
  });
}

async function execDashscopeTaskStatus(args) {
  try {
    const task_id = String(args.task_id || '').trim();
    if (!task_id) return { ok: false, output: 'task_id 不能为空' };
    const r = await api(`/api/bridge/dashscope/task_status/${encodeURIComponent(task_id)}`);
    return r || { ok: false, output: '查询无响应' };
  } catch (e) {
    return { ok: false, output: `查询失败: ${(e && e.message) || e}` };
  }
}

async function execDashscopeListModels(args) {
  try {
    const cat = String(args.category || '').trim();
    const qs = cat ? `?category=${encodeURIComponent(cat)}` : '';
    const r = await api(`/api/bridge/dashscope/list_models${qs}`);
    return r || { ok: false, output: '查询无响应' };
  } catch (e) {
    return { ok: false, output: `查询失败: ${(e && e.message) || e}` };
  }
}

/* ---------- 辅助 ---------- */
function globToRegExp(glob) {
  let re = '';
  const s = String(glob || '').replace(/\\/g, '/');
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === '*') {
      if (s[i + 1] === '*') {
        // ** 匹配任意层目录（含空）
        re += '(?:.*/)?';
        i++;
      } else {
        re += '[^/]*';
      }
    } else if (c === '?') {
      re += '[^/]';
    } else if ('.+()^${}|[]\\'.includes(c)) {
      re += '\\' + c;
    } else {
      re += c;
    }
  }
  return new RegExp('^' + re + '$');
}
