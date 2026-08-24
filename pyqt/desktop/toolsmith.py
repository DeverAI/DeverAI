"""自研工具库（v6 工具设计专家 + v6.3 工具医生）：接需求→查重→构建→审核→入库→执行→bug 收集→诊断→修复→回归。

- 注册表 data/tools.json：每项 {name, description, kind(prompt|script),
  impl, params, example, status(pending|active|rejected), created_at, reviewed_at}。
- 查重：find_duplicates 用资产银行同款 bigram 相似度（阈值 0.5），
  避免重复劳动（如爬虫、Md→Word 这类常见重复建设）。
- 构建：build_tool_via_llm 让模型产出 prompt 模板型工具定义（严格 JSON）。
- 审核通过（experts.review_tool_build）后才 register(status=active)；
  审核不过一律不落库（宁严勿松）；status 保留 pending/rejected 供扩展/审计用。
- 执行：use_tool 对 prompt 型做安全模板填充（regex 替换，不用 str.format，
  防 LLM 模板里的游离大括号炸 KeyError）；script 型提示走 run_command 审批。
- v6.3 工具医生：use_tool 失败时 record_tool_bug 追加到 data/tool_bugs.jsonl；
  fix_tool_bugs 走「读 bug→读原 impl→LLM 诊断→沙箱跑 example 回归→通过才覆盖入库」链路，
  失败不覆盖原版（保留可回退）。
"""
import datetime
import json
import re
import secrets

from .config import DATA_DIR, Config
from .llm import chat_complete, extract_json
from .matcher import similarity as match_similarity
from .storage import load_json, save_json

TOOLS_PATH = DATA_DIR / "tools.json"
BUGS_PATH = DATA_DIR / "tool_bugs.jsonl"
DUP_THRESHOLD = 0.5

# 识别"造工具"类需求的关键词（总司令规划后自动前置查重任务的判据）
TOOL_BUILD_KEYWORDS = (
    "造工具", "造个工具", "写工具", "写个工具", "做工具", "做个工具", "建工具",
    "写个脚本", "做个脚本", "小工具", "转换器", "写个插件", "做个插件",
)

BUILD_TOOL_PROMPT = """你是工具设计师。请根据需求设计一个可复用的 prompt 模板型工具。
查重结果如下（若已有相似工具/资产，请说明差异或直接建议复用，不要重复建设）：
{duplicates}

输出严格 JSON（不要任何其他文字）：
{{"name": "english_snake_case 工具名", "description": "一句话中文说明", "template": "prompt 模板，用 {{param}} 形式嵌入输入参数", "params": ["参数1", "参数2"], "example": "一次调用的输入示例与预期效果"}}
规则：模板必须自包含（脱离本次对话也能看懂怎么用）；参数名用英文小写。"""


def load_tools() -> list:
    return load_json(TOOLS_PATH, [])


def save_tools(tools: list) -> None:
    save_json(TOOLS_PATH, tools)


def get_tool(name: str) -> dict:
    for t in load_tools():
        if t.get("name") == name:
            return t
    return {}


def list_active_tools() -> list:
    """只返回过审可用的工具（pending/rejected 不暴露给 Agent）。"""
    return [t for t in load_tools() if t.get("status") == "active"]


def is_build_request(text: str) -> bool:
    """判断需求是否属于"造工具"类（触发查重前置与入库审核流程）。"""
    return any(k in (text or "") for k in TOOL_BUILD_KEYWORDS)


def find_duplicates(query: str, limit: int = 3) -> list:
    """自研工具库内查重：返回 [{tool, score}]，相似度 ≥ DUP_THRESHOLD（v8.1 三级匹配）。"""
    q = (query or "").strip()
    if not q:
        return []
    from .matcher import rank as _match_rank
    tools = load_tools()
    docs = [" ".join([str(t.get("name", "")), str(t.get("description", "")),
                      str(t.get("example", ""))]) for t in tools]
    scored = []
    for i, s in _match_rank(q, docs, min_score=DUP_THRESHOLD):
        scored.append({"tool": tools[i], "score": round(s, 4)})
    return scored[:limit]


def dedup_report(query: str, vault=None) -> str:
    """查重汇总文本（自研工具库 + 资产银行），供构建提示词与查重任务使用。"""
    lines = []
    for d in find_duplicates(query):
        t = d["tool"]
        lines.append(f"- [自研工具] {t.get('name')}（相似度 {d['score']}）: {t.get('description', '')[:80]}")
    if vault is not None:
        try:
            for hit in vault.search(query, limit=3, threshold=DUP_THRESHOLD):
                a = hit["asset"]
                lines.append(f"- [资产银行] {a.get('title', '')}（相似度 {hit['score']}）: "
                             f"{str(a.get('description', ''))[:80]}")
        except Exception:
            pass
    return "\n".join(lines) or "未发现相似工具或资产。"


def register(name: str, description: str, kind: str, impl: str,
             params: list = None, example: str = "", status: str = "active") -> dict:
    """注册/更新一个自研工具。同名直接覆盖（审核流程在调用方完成）。"""
    name = re.sub(r"[^0-9A-Za-z_]+", "_", (name or "").strip()).strip("_")[:48]
    if not name:
        raise ValueError("工具名不能为空")
    if kind not in ("prompt", "script"):
        raise ValueError("kind 只能是 prompt 或 script")
    tools = load_tools()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "name": name,
        "description": str(description or "")[:300],
        "kind": kind,
        "impl": str(impl or "")[:20000],
        "params": [str(p)[:40] for p in (params or [])][:10],
        "example": str(example or "")[:500],
        "status": status,
        "created_at": next((t.get("created_at") for t in tools if t.get("name") == name), now),
        "reviewed_at": now if status == "active" else "",
    }
    tools = [t for t in tools if t.get("name") != name]
    tools.insert(0, entry)
    save_tools(tools)
    return entry


def _fill_template(template: str, inputs: dict) -> str:
    """安全模板填充：{param} 用 regex 替换，未提供的参数保留原样（不用 str.format）。"""
    def rep(m):
        key = m.group(1)
        return str(inputs.get(key, m.group(0)))
    return re.sub(r"\{([a-z0-9_]+)\}", rep, template or "")


async def build_tool_via_llm(cfg: Config, requirement: str, duplicates_text: str) -> dict:
    """让模型产出 prompt 模板型工具定义（解析失败抛 ValueError，调用方兜底）。"""
    msg = await chat_complete(cfg, [
        {"role": "system", "content": BUILD_TOOL_PROMPT.format(
            duplicates=(duplicates_text or "未查重")[:1500])},
        {"role": "user", "content": f"需求：{requirement[:800]}"},
    ], temperature=0.3, max_tokens=1500, timeout=120.0)
    data = extract_json(msg.get("content") or "")
    if not isinstance(data, dict) or not data.get("name") or not data.get("template"):
        raise ValueError("工具设计输出缺少 name/template")
    return {
        "name": str(data.get("name")),
        "description": str(data.get("description") or ""),
        "kind": "prompt",
        "impl": str(data.get("template")),
        "params": [str(p) for p in (data.get("params") or [])],
        "example": str(data.get("example") or ""),
    }


async def use_tool(cfg: Config, name: str, inputs: dict, extra_system_prompt: str = "") -> dict:
    """执行自研工具。prompt 型：填充模板后一次 chat_complete；script 型：引导走 run_command。

    extra_system_prompt: v6.4 用例反哺注入的修复钩子提示（可选）。
    """
    t = get_tool(name)
    if not t:
        return {"ok": False, "output": f"自研工具不存在: {name}"}
    if t.get("status") != "active":
        return {"ok": False, "output": f"工具 {name} 未过审（状态: {t.get('status')}），不可用。"}
    if t.get("kind") == "script":
        return {"ok": True, "output": (
            f"script 型工具 {name} 请通过 run_command 执行（会走审批门）。\n"
            f"说明：{t.get('description')}\n用法示例：{t.get('example')}"), "meta": {"tool": t}}
    prompt = _fill_template(t.get("impl", ""), inputs or {})
    sys_content = f"你是工具「{t.get('name')}」的执行器，严格按模板要求完成任务。{extra_system_prompt}"
    try:
        msg = await chat_complete(cfg, [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": prompt},
        ], temperature=0.3, max_tokens=3000, timeout=180.0)
        content = str(msg.get("content") or "")
        # v6.3 工具医生：输出为空视为 bug（模板可能漏了关键指令）
        if not content.strip():
            record_tool_bug(name, "工具执行返回空内容（模板可能缺失关键指令）", inputs)
            return {"ok": False, "output": f"工具 {name} 执行返回空内容，已记录 bug 等待修复。", "meta": {"tool": t}}
        return {"ok": True, "output": content, "meta": {"tool": t}}
    except Exception as e:
        # v6.3 工具医生：执行异常自动收集 bug
        record_tool_bug(name, f"执行异常: {e}", inputs)
        return {"ok": False, "output": f"自研工具执行失败: {e}（已记录 bug）", "meta": {"tool": t}}


# ==========================================================================
# v6.3 工具医生（Tool Doctor）：bug 收集 / 诊断 / 修复 / 回归
# ==========================================================================
DIAGNOSE_PROMPT = """你是工具医生。下面是一个自研工具的 bug 报告，请诊断原因并产出修复后的工具定义。

工具名：{name}
工具说明：{description}
原 impl（模板/脚本）：
{old_impl}
参数：{params}
示例：{example}

Bug 原因：
{reason}
失败时的输入：{inputs}

请输出严格 JSON（不要任何其他文字）：
{{"name": "保持原名", "description": "可微调说明", "template": "修复后的 prompt 模板，用 {{param}} 嵌入参数", "params": ["参数1"], "example": "一次调用示例", "fix_note": "一句话说明修复了什么"}}

规则：
- 模板必须自包含、可脱离本次对话独立运行。
- 修复要针对 bug 原因，不要无故改写无关部分。
- 参数名用英文小写。
"""


def record_tool_bug(name: str, reason: str, inputs: dict | None = None) -> str:
    """v6.3 工具医生：记录一条 bug 到 data/tool_bugs.jsonl。返回 bug_id。"""
    bug_id = f"{int(datetime.datetime.now().timestamp())}-{secrets.token_hex(2)}"
    entry = {
        "bug_id": bug_id,
        "tool_name": str(name or "")[:64],
        "reason": str(reason or "")[:500],
        "inputs": str(inputs or "")[:200],
        "status": "pending",
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(BUGS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # bug 收集失败不阻塞主流程（FreqErr：不得吞没主对话）
    return bug_id


def load_tool_bugs(status: str = "pending") -> list:
    """读取 bug 列表。status=pending|fixed|all。"""
    if not BUGS_PATH.exists():
        return []
    out = []
    try:
        with open(BUGS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if status == "all" or e.get("status") == status:
                    out.append(e)
    except Exception:
        pass
    return out


def clear_tool_bug(bug_id: str) -> bool:
    """标记 bug 为 fixed（写回 jsonl）。"""
    if not BUGS_PATH.exists():
        return False
    try:
        lines = []
        changed = False
        with open(BUGS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    e = json.loads(s)
                except json.JSONDecodeError:
                    lines.append(s)
                    continue
                if e.get("bug_id") == bug_id and e.get("status") == "pending":
                    e["status"] = "fixed"
                    e["fixed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    changed = True
                lines.append(json.dumps(e, ensure_ascii=False))
        if changed:
            with open(BUGS_PATH, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        return changed
    except Exception:
        return False


async def _regression_test(cfg: Config, tool: dict) -> tuple[bool, str]:
    """v6.3 工具医生：沙箱回归——用 example 跑一次新 impl，校验输出非空且无异常。

    返回 (ok, note)。超时 60s，异常即判失败。
    """
    # v8.5.x 审查修复：用工具声明的 params 构造占位输入填充模板，否则 {param} 占位符
    # 原样保留，回归只校验"输出非空"却会放行仍然损坏的模板。
    params = tool.get("params") or []
    sample = {p: f"<{p}>" for p in params}
    prompt = _fill_template(tool.get("impl", ""), sample)
    try:
        msg = await chat_complete(cfg, [
            {"role": "system", "content": f"你是工具「{tool.get('name')}」的执行器，严格按模板要求完成任务。"},
            {"role": "user", "content": prompt},
        ], temperature=0.3, max_tokens=1500, timeout=60.0)
        content = str(msg.get("content") or "").strip()
        if not content:
            return False, "回归测试返回空内容"
        return True, content[:200]
    except Exception as e:
        return False, f"回归测试异常: {e}"


async def fix_tool_bugs(cfg: Config, limit: int = 5) -> dict:
    """v6.3 工具医生：批量修复 pending bug。

    链路：读 bug→取原 tool→LLM 诊断出新 impl→沙箱跑 example 回归→通过则覆盖入库+标 fixed。
    失败不覆盖原版（保留可回退），bug 留 pending。
    """
    bugs = load_tool_bugs("pending")
    if not bugs:
        return {"ok": True, "fixed": 0, "output": "无待修工具 bug。", "details": []}
    # 按工具名聚合，每工具取最近一条 bug（避免同工具反复修）
    seen = {}
    for b in bugs:
        # v8.5.x 审查修复：后者覆盖前者，保留最近一条；setdefault 会保留最旧的一条
        seen[b.get("tool_name", "")] = b
    todo = list(seen.values())[:limit]
    results = []
    fixed = 0
    for b in todo:
        name = b.get("tool_name", "")
        t = get_tool(name)
        if not t:
            results.append({"tool": name, "ok": False, "note": "工具已不存在（可能已删除），跳过"})
            clear_tool_bug(b.get("bug_id", ""))
            continue
        # LLM 诊断
        try:
            msg = await chat_complete(cfg, [
                {"role": "system", "content": DIAGNOSE_PROMPT.format(
                    name=name,
                    description=t.get("description", ""),
                    old_impl=str(t.get("impl", ""))[:3000],
                    params=t.get("params", []),
                    example=t.get("example", ""),
                    reason=b.get("reason", ""),
                    inputs=b.get("inputs", ""),
                )},
                {"role": "user", "content": "请输出修复后的工具 JSON。"},
            ], temperature=0.2, max_tokens=2000, timeout=120.0)
        except Exception as e:
            results.append({"tool": name, "ok": False, "note": f"诊断调用失败: {e}"})
            continue
        try:
            data = extract_json(msg.get("content") or "")
        except Exception:
            results.append({"tool": name, "ok": False, "note": "诊断输出非合法 JSON，跳过（保留原版）"})
            continue
        if not isinstance(data, dict) or not data.get("template"):
            results.append({"tool": name, "ok": False, "note": "诊断输出非合法 JSON，跳过（保留原版）"})
            continue
        # 构造候选新工具
        candidate = {
            "name": name,
            "description": str(data.get("description") or t.get("description", "")),
            "kind": t.get("kind", "prompt"),
            "impl": str(data.get("template")),
            "params": [str(p) for p in (data.get("params") or t.get("params") or [])],
            "example": str(data.get("example") or t.get("example", "")),
        }
        # 沙箱回归
        ok, note = await _regression_test(cfg, candidate)
        if not ok:
            results.append({"tool": name, "ok": False, "note": f"回归测试未通过: {note}（保留原版）"})
            continue
        # 通过则覆盖入库（register 会保留 created_at）
        register(
            name=name,
            description=candidate["description"],
            kind=candidate["kind"],
            impl=candidate["impl"],
            params=candidate["params"],
            example=candidate["example"],
            status="active",
        )
        clear_tool_bug(b.get("bug_id", ""))
        fixed += 1
        results.append({
            "tool": name, "ok": True,
            "note": f"已修复: {data.get('fix_note', '')}（回归通过）",
        })
    summary = f"工具医生完成：修复 {fixed}/{len(todo)} 个工具 bug。"
    return {"ok": True, "fixed": fixed, "output": summary, "details": results}


# ==========================================================================
# v6.4 用例反哺（Case Feedback）：连续报错 2 次→副驾驶生成修复钩子→存入资产库
# 同类上下文再次出现时，use_tool 执行前自动匹配并注入钩子提示，避免重蹈覆辙
# ==========================================================================
HOOK_FAIL_THRESHOLD = 2          # 连续失败 N 次触发钩子生成
HOOK_SIM_THRESHOLD = 0.35        # 上下文相似度阈值（低于此不注入）
HOOK_TTL_SECONDS = 60 * 60 * 24  # 钩子有效期 24h，过期不再注入（避免陈旧钩子干扰）

FAIL_COUNT_PATH = DATA_DIR / "tool_fail_count.json"

HOOK_GEN_PROMPT = """你是工具修复钩子生成器。下面是一个自研工具连续报错的记录，请生成一个简短的"修复钩子"提示，
让 AI 下次在相似上下文调用该工具时，能直接参考此钩子避免再次踩坑。

工具名：{name}
工具说明：{description}
参数：{params}
示例：{example}

最近 2 次失败原因：
{reasons}

请输出严格 JSON（不要任何其他文字）：
{{"hook": "一句话修复钩子，形如：调用 X 时注意 Y，否则会 Z。正确做法是 ...", "pattern": "用于匹配的关键词或上下文特征（空格分隔）"}}

规则：
- 钩子必须可操作、具体（不要空泛的"注意参数"）。
- 钩子长度 ≤ 200 字。
- pattern 用于后续相似度匹配，提取失败原因中的核心特征词。
"""


def _load_fail_counts() -> dict:
    """加载连续失败计数 {tool_name: {"count": N, "last_reasons": [str], "last_ts": str}}。"""
    return load_json(FAIL_COUNT_PATH, {}) or {}


def _save_fail_counts(d: dict) -> None:
    save_json(FAIL_COUNT_PATH, d)


def _bump_fail_count(name: str, reason: str) -> int:
    """递增某工具的连续失败计数，返回当前计数。成功调用时清零（见 _reset_fail_count）。"""
    d = _load_fail_counts()
    entry = d.get(name, {"count": 0, "last_reasons": [], "last_ts": ""})
    entry["count"] = int(entry.get("count", 0)) + 1
    reasons = list(entry.get("last_reasons") or [])
    reasons.append(str(reason)[:200])
    entry["last_reasons"] = reasons[-3:]  # 仅保留最近 3 条
    entry["last_ts"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    d[name] = entry
    _save_fail_counts(d)
    return entry["count"]


def _reset_fail_count(name: str) -> None:
    """工具调用成功时清零连续失败计数。"""
    d = _load_fail_counts()
    if name in d:
        d.pop(name, None)
        _save_fail_counts(d)


async def _generate_fix_hook(cfg: Config, name: str) -> dict | None:
    """副驾驶生成修复钩子并存入资产库。返回钩子资产 dict 或 None。"""
    t = get_tool(name)
    if not t:
        return None
    fc = _load_fail_counts().get(name, {})
    reasons = fc.get("last_reasons") or []
    if not reasons:
        return None
    try:
        msg = await chat_complete(cfg, [
            {"role": "system", "content": HOOK_GEN_PROMPT.format(
                name=name,
                description=t.get("description", ""),
                params=t.get("params", []),
                example=t.get("example", ""),
                reasons="\n".join(f"- {r}" for r in reasons),
            )},
            {"role": "user", "content": "请输出修复钩子 JSON。"},
        ], temperature=0.2, max_tokens=300, timeout=60.0)
    except Exception:
        return None
    data = extract_json(msg.get("content") or "")
    if not isinstance(data, dict) or not data.get("hook"):
        return None
    # 存入资产库（kind=fix_hook，便于后续检索区分）
    try:
        from .vault import Vault
        vault = Vault()
        asset = vault.add({
            "title": f"修复钩子: {name}",
            "kind": "fix_hook",
            "scene": "tool_error_recovery",
            "tags": ["fix_hook", name] + str(data.get("pattern") or "").split(),
            "description": str(data.get("hook"))[:300],
            "content": str(data.get("hook"))[:500],
            "tool_name": name,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        return asset
    except Exception:
        return None


def match_fix_hook(name: str, context: str = "") -> str | None:
    """匹配当前工具+上下文是否有适用的修复钩子。返回钩子提示文本或 None。

    匹配逻辑：从资产库找 kind=fix_hook 且 tool_name=name 的资产，
    用 bigram 相似度匹配 context vs asset.tags+description，
    超过阈值且未过期则返回钩子文本。
    """
    try:
        from .vault import Vault
        vault = Vault()
        for a in vault.list():
            if a.get("kind") != "fix_hook":
                continue
            if a.get("tool_name") != name:
                continue
            # 过期检查
            created = a.get("created_at") or ""
            try:
                ct = datetime.datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
                if (datetime.datetime.now() - ct).total_seconds() > HOOK_TTL_SECONDS:
                    continue
            except Exception:
                pass
            # 相似度匹配
            tags = a.get("tags") or []
            if not isinstance(tags, list):
                tags = [tags]
            doc = " ".join([str(t) for t in tags] + [str(a.get("description", ""))])
            if match_similarity(context or name, doc) >= HOOK_SIM_THRESHOLD:
                return str(a.get("content") or a.get("description") or "")
    except Exception:
        pass
    return None


async def use_tool_with_feedback(cfg: Config, name: str, inputs: dict) -> dict:
    """v6.4 增强版 use_tool：执行前匹配修复钩子注入，执行后按成败更新计数/生成钩子。

    连续失败 ≥ HOOK_FAIL_THRESHOLD 次时，自动触发副驾驶生成修复钩子并存入资产库。
    下次同类上下文调用时，钩子作为 system 提示注入，避免重蹈覆辙。
    """
    # 1. 执行前匹配修复钩子
    hook_hint = match_fix_hook(name, json.dumps(inputs, ensure_ascii=False)[:500])
    # 2. 调用原 use_tool
    extra_system = ""
    if hook_hint:
        extra_system = f"\n\n【修复钩子（来自历史失败教训）】{hook_hint}"
    r = await use_tool(cfg, name, inputs, extra_system_prompt=extra_system)
    # 3. 按成败更新计数
    if r.get("ok"):
        _reset_fail_count(name)
    else:
        count = _bump_fail_count(name, r.get("output", ""))
        # 连续失败达阈值 → 副驾驶生成钩子（仅生成一次：count==threshold 时）
        if count == HOOK_FAIL_THRESHOLD and getattr(cfg, "ENABLE_CASE_FEEDBACK", True):
            try:
                await _generate_fix_hook(cfg, name)
            except Exception:
                pass  # 钩子生成失败不影响主流程
    return r
