"""AOE 规划器（第四章 4.1）：DAG 拆解 → 强制查库复用拦截(3.2) → 并行执行 + 超时熔断。

- 规划：LLM 生成严格 JSON 的 DAG（nodes + deps）。
- 复用拦截：每个节点先语义检索资产银行，相似度 ≥ 阈值则标记为"复用节点"，不再调用 Agent。
- 执行：按拓扑层并行（asyncio.gather），每个节点 = 独立子Agent（全新上下文，可调工具）；
  分支超时熔断（默认 20s）返回空值，主流程继续。
- Token 计费模式：要求规划器最小化节点数（合并调用链），并跳过最终汇总调用。
"""
import asyncio
import datetime
import json
import time
from dataclasses import dataclass, field
from typing import List, Optional

from . import err_mirror
from .config import Config
from .errors import log_error
from .llm import LLMError, chat_complete, extract_json

PLAN_SYSTEM = (
    "你是 AOE 规划器。将用户的复杂任务拆解为可并行执行的子任务有向无环图(DAG)。\n"
    "规则：\n"
    "1. 只输出严格 JSON，不要任何其他文字。格式：\n"
    '{"goal":"一句话目标","nodes":[{"id":"n1","name":"短名称","description":"子任务完整描述",'
    '"deps":["n2"],"expected_output":"预期产物","files":["该节点将写入的文件相对路径，仅只读则为空数组"]}]}\n'
    "2. id 唯一（n1,n2,...）；deps 引用其他节点 id，无依赖则省略。\n"
    "3. 无相互依赖的节点尽量相互独立，以便并行执行；声明 files 时，写不同文件的节点会被自动并行，"
    "写相同文件的节点会被调度器自动串行。\n"
    "4. 节点数控制在 2~6 个；每个节点必须在独立上下文中可独立完成，描述要自带足够信息。\n"
)


@dataclass
class AoEResult:
    summary: str = ""
    plan: dict = field(default_factory=dict)
    node_results: dict = field(default_factory=dict)
    reuse_hits: int = 0


def _listify(value):
    """LLM 可控 list 字段归一化：字符串包装为单元素，非容器置空。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(x) for x in value]
    return []


def _topo_levels(nodes: List[dict]) -> List[List[dict]]:
    by_id = {n["id"]: n for n in nodes}
    indeg = {n["id"]: len(set(_listify(n.get("deps"))) & set(by_id)) for n in nodes}
    ready = [nid for nid, d in indeg.items() if d == 0]
    levels = []
    done = set()
    while ready:
        level = []
        for nid in ready:
            level.append(by_id[nid])
            done.add(nid)
        levels.append(level)
        next_ready = []
        for n in nodes:
            if n["id"] in done:
                continue
            deps = set(_listify(n.get("deps"))) & set(by_id)
            if deps and deps.issubset(done):
                next_ready.append(n["id"])
            elif not deps:
                next_ready.append(n["id"])
        ready = next_ready
    # v8.5.x 审查修复：成环/非法依赖导致部分节点永远无法调度时，把它们降级并入末层，
    # 绝不让节点静默丢失（否则 AOE 汇总会静默输出空结果）。
    leftover = [n for n in nodes if n["id"] not in done]
    if leftover:
        levels.append(leftover)
    return levels


def _make_node_emit(emit, label: str):
    async def wrapped(ev):
        if emit is not None:
            try:
                await emit({"type": "subagent", "label": label, "event": ev})
            except Exception:
                pass
    return wrapped


async def _run_node(cfg, node: dict, deps_results: dict, emit, vault, parent_agent=None, depth=1) -> str:
    """执行单个节点：优先资产银行复用拦截。"""
    # 3.2 复用拦截：规划执行前的语义检索（embedding API 会阻塞，丢线程执行）
    if cfg.ENABLE_VAULT and vault is not None:
        q = f"{node.get('name','')} {node.get('description','')} {node.get('expected_output','')}"
        hits = await asyncio.to_thread(vault.search, q, limit=1, threshold=cfg.vault_threshold)
        if hits:
            asset = hits[0]["asset"]
            return (
                f"【复用节点】已从资产银行命中并复用现有资产，无需重新生成。\n"
                f"资产: {asset.get('title')} (相似度 {hits[0]['score']})\n"
                f"内容摘要: {(asset.get('content') or '')[:2000]}"
            )
    from .agent import Agent  # 延迟导入避免循环依赖

    sub = Agent(
        cfg,
        emit=None,
        parent_emit=emit,
        is_sub=True,
        sub_label=node.get("name", "节点"),
        vault=vault,
        approval=parent_agent.approval if parent_agent is not None else None,  # 共享父级审批门
        depth=depth,
    )
    deps_text = "\n".join(
        f"[{did} 结果]\n{deps_results.get(did, '')[:1500]}" for did in _listify(node.get("deps"))
    )
    task = (
        f"【子任务】{node.get('name')}\n{node.get('description')}\n"
        f"【预期产物】{node.get('expected_output', '')}\n"
        + (f"【依赖节点结果】\n{deps_text}\n" if deps_text else "")
        + "完成后只给出最终结论与产物，无需客套。"
    )
    result = await sub.run_task(task, max_rounds=6)
    return result.summary or "(节点无输出)"


async def execute_aoe(
    cfg: Config,
    task: str,
    emit=None,
    vault=None,
    parent_agent=None,
    depth: int = 1,
) -> AoEResult:
    res = AoEResult()
    if emit:
        await emit({"type": "plan_start", "task": task})

    # ---- 1. 规划 ----
    try:
        system = PLAN_SYSTEM
        if getattr(cfg, "ENABLE_MODES", True) and cfg.token_mode:  # v8.32（F7）：总开关门控
            system += (
                "\n6. 【Token 计费模式】优先选择调用次数最少的路径，允许合并步骤以最小化节点数。\n"
            )
        msg = await chat_complete(
            cfg,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"任务：{task}"},
            ],
            temperature=0.2,
            max_tokens=3000,
            timeout=120.0,
        )
        plan = extract_json(msg.get("content") or "")
    except Exception as e:
        log_error("AOE 规划失败", e)
        if emit:
            await emit({"type": "plan_error", "message": f"AOE 规划失败: {e}"})
        res.summary = f"AOE 规划失败: {e}"
        return res

    nodes = [n for n in (plan.get("nodes") or []) if isinstance(n, dict) and n.get("id")]
    if not nodes:
        res.summary = "规划器未产出有效节点。"
        if emit:
            await emit({"type": "plan_error", "message": "规划器未产出有效节点。"})
        return res
    # v8.9 审查修复：LLM 产出重复 id 时自动重命名，避免 _topo_levels 按 id 去重导致节点静默丢失
    seen_ids = set()
    for i, n in enumerate(nodes):
        nid = str(n.get("id"))
        if nid in seen_ids:
            n = dict(n)
            n["id"] = f"{nid}#{i+1}"
            nodes[i] = n
            # 同步回 plan，UI/汇总拿到的计划与执行结果一致
            if isinstance(plan.get("nodes"), list) and i < len(plan["nodes"]):
                plan["nodes"][i] = dict(n)
            nid = n["id"]
        seen_ids.add(nid)
    res.plan = plan
    if emit:
        await emit({"type": "plan", "plan": plan})

    # ---- 2. 分层并行执行（v8.9：同层内按文件分区波次调度）----
    results: dict = {}
    reuse = 0
    levels = _topo_levels(nodes)
    from .partition import wave_partition
    for li, level in enumerate(levels):
        if emit:
            await emit({"type": "plan_level", "level": li, "ids": [n["id"] for n in level]})
        waves = wave_partition(level)
        if emit and len(waves) > 1:
            await emit({"type": "plan_partition", "level": li,
                        "waves": [[n["id"] for n in w] for w in waves]})
        for wave in waves:
            async def _wrap(node):
                if emit:
                    await emit({"type": "plan_node", "id": node["id"], "status": "running",
                                "name": node.get("name")})
                t0 = time.monotonic()
                try:
                    out = await asyncio.wait_for(
                        _run_node(cfg, node, results, emit, vault, parent_agent, depth=depth),
                        timeout=cfg.aoe_timeout_s,
                    )
                    timed_out = False
                except asyncio.TimeoutError:
                    out = f"(节点 {node['id']} 执行超时 {cfg.aoe_timeout_s}s，已熔断返回空值)"
                    timed_out = True
                except LLMError as e:
                    out = f"(节点 {node['id']} 失败: {e})"
                    timed_out = False
                except Exception as e:
                    log_error(f"AOE 节点执行异常: {node['id']}", e)
                    out = f"(节点 {node['id']} 异常: {e})"
                    timed_out = False
                if emit:
                    await emit({
                        "type": "plan_node", "id": node["id"], "status": "timeout" if timed_out else "done",
                        "name": node.get("name"), "cost_s": round(time.monotonic() - t0, 1),
                        "output": out[:800],
                    })
                return node["id"], out

            done = await asyncio.gather(*[_wrap(n) for n in wave])
            for nid, out in done:
                results[nid] = out
                if "复用节点" in out:
                    reuse += 1
    res.node_results = results
    res.reuse_hits = reuse

    # ---- 3. 汇总 ----
    if getattr(cfg, "ENABLE_MODES", True) and cfg.token_mode:  # v8.32（F7）：总开关门控
        # 计费模式：跳过额外汇总调用，直接拼接
        parts = [f"[目标] {plan.get('goal', task)}", "[各节点结果]"]
        for n in nodes:
            parts.append(f"### {n.get('name')} ({n.get('id')})\n{results.get(n['id'], '(空)')[:1500]}")
        res.summary = "\n".join(parts)
    else:
        try:
            flat = "\n".join(
                f"[{n.get('id')} {n.get('name')}]\n{results.get(n['id'], '(空)')[:2000]}"
                for n in nodes
            )
            msg = await chat_complete(
                cfg,
                [
                    {"role": "system", "content": "你是 AOE 结果汇总员。根据各并行子节点结果，"
                     "输出给用户看的最终结论（简洁、结构化，包含关键结果与下一步建议）。"},
                    {"role": "user", "content": f"目标: {plan.get('goal', task)}\n\n{flat}"},
                ],
                temperature=0.3,
                max_tokens=2500,
                timeout=120.0,
            )
            res.summary = msg.get("content") or "\n".join(results.values())
        except Exception as e:
            log_error("AOE 汇总失败", e)
            res.summary = "\n".join(results.values())
    if emit:
        await emit({"type": "plan_done", "summary": res.summary})
    return res
