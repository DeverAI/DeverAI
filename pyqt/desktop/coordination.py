"""v8.33 全局协调协议：跨工作区 Agent 闸口 + 互通收件箱 + 人类看板。

问题：同一台机器上多个不同工作区的 Agent 并发时，可能同时操作同一**外部资源**
（典型：两个 Agent 都 SSH 同一服务器，一个重启服务器，另一个上传中掉线还不知道为什么）。
工作区内的租约锁/文件分区调度管不到跨工作区的外部资源——本模块补这一层。

机制：
- 注册表 data/coordination.json（本机所有 DeverAI 进程共享 DATA_DIR，进程间天然互通）：
  agents: {agent_id: {workspace, workspace_name, pid, status, task, resources, next_action,
           model, started, last_seen}}；inbox: {agent_id: [消息]}。
- 心跳 TTL：轮开始/工具调用节流刷新；超 TTL（默认 180s，DEVERAI_COORD_TTL_S 可覆盖）
  视为离线并在下次读取时回收（GUI 关闭自然过期）。
- 冲突 = 两个存活 Agent 声明同一归一化资源键（如 ssh:host）。检测到新冲突时双向投递
  协议消息（你在干什么/我要干什么/下一步是什么）。
- AI 协议工具：coordination_board（只读看板）/ coordination_declare（声明并即时返回冲突+收件箱）；
  Agent 轮开始把收件箱+冲突注入 system prompt。
- 人类看板：桌面「协调看板」对话框 + webui GET /api/bridge/coordination——
  人类可在损害发生前直接叫停对应 Agent，而不是等事后兜底。

线程/进程安全：storage.save_json 原子替换 + 模块锁；跨进程为 last-writer-wins 的
尽力看板（各 Agent 心跳自愈），不追求强一致——协调语义靠「冲突即消息」保证。
"""
from __future__ import annotations

import datetime
import os
import re
import threading
import time
from pathlib import Path

from .config import DATA_DIR
from .storage import load_json, save_json

STATE_PATH = DATA_DIR / "coordination.json"
TTL_S = float(os.environ.get("DEVERAI_COORD_TTL_S") or 180)
_TOUCH_THROTTLE_S = 8.0

_LOCK = threading.Lock()
_KEEP_OVERRIDE: int | None = None   # 测试钩子：覆盖 TTL（秒）
# v8.34（H2）：aid → 上次落盘时刻。节流写盘的记账本——缺了它，纯心跳（无 task/资源）
# 既不落盘也不写新条目，轮开始登记的 Agent 从未进过注册表。
_LAST_PERSIST: dict = {}

_SSH_RE = re.compile(r"(?i)\b(?:ssh|sshpass|plink)\s+(?:-\S+\s+)*(?:([^\s@/]+)@)?([\w.\-]+)")
_CP_RE = re.compile(r"(?i)\b(?:scp|rsync|pscp)\s+(?:-\S+\s+)*(?:([^\s@/]+)@)?([\w.\-]+):")


def self_agent_id(workspace: str) -> str:
    """本进程内某工作区 Agent 的全局唯一标识：工作区名@进程号。"""
    name = Path(str(workspace or "")).name or "ws"
    return f"{name}@{os.getpid()}"


def norm_resource(s: str) -> str:
    """资源归一化键：小写、去协议头（含 ssh: 裸前缀与 x:// 形式）/空白/尾斜杠，截 120 字符。

    统一后 "ssh:mindog" 与 "ssh://mindog" 归一为同一键（v8.33 收口修复：此前只剥 x:// 形式）。
    """
    t = re.sub(r"^[a-z][a-z0-9+.-]*://", "", str(s or "").strip().lower())
    t = re.sub(r"^(ssh|http|https|ftp|sftp):", "", t)
    t = re.sub(r"[\s，。；;]+", "", t).strip("/")
    return t[:120]


def resources_from_command(cmd: str) -> list:
    """从命令串嗅探外部资源（ssh/scp/rsync/sshpass/plink 的主机）→ ['ssh:host', ...]。"""
    out = []
    try:
        for m in _SSH_RE.finditer(str(cmd or "")):
            host = (m.group(2) or "").strip().lower()
            if host and host not in ("127.0.0.1", "localhost"):
                out.append(f"ssh:{host}")
        for m in _CP_RE.finditer(str(cmd or "")):
            host = (m.group(2) or "").strip().lower()
            if host and host not in ("127.0.0.1", "localhost"):
                out.append(f"ssh:{host}")
    except Exception:
        return []
    return sorted(set(out))


def _now() -> float:
    return time.time()


def _alive(entry: dict, now: float = None) -> bool:
    ttl = float(_KEEP_OVERRIDE) if _KEEP_OVERRIDE is not None else TTL_S
    try:
        return (_now() if now is None else now) - float(entry.get("last_seen") or 0) <= ttl
    except Exception:
        return False


def _load() -> dict:
    data = load_json(STATE_PATH, {}) or {}
    if not isinstance(data, dict):
        data = {}
    agents = data.get("agents") if isinstance(data.get("agents"), dict) else {}
    inbox = data.get("inbox") if isinstance(data.get("inbox"), dict) else {}
    return {"agents": agents, "inbox": inbox}


def _save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_json(STATE_PATH, data)


def _purge(data: dict) -> int:
    """回收离线 Agent（TTL 过期）与其收件箱。返回回收条数（调用方据此决定是否落盘）。"""
    removed = 0
    for aid in list((data.get("agents") or {}).keys()):
        e = data["agents"].get(aid) or {}
        if not _alive(e):
            data["agents"].pop(aid, None)
            removed += 1
            # v8.34（H4）：死 Agent 的收件箱一并清理——此前只删 agents 条目，inbox 键永久
            # 残留（每个死 pid 最多 50 条消息），coordination.json 随进程重启单调增长。
            # 语义无损：该 Agent 复活后重新声明资源会再次触发双向投递。
            (data.get("inbox") or {}).pop(aid, None)
            _LAST_PERSIST.pop(aid, None)
    return removed


def _conflicts_for(data: dict, agent_id: str) -> list:
    """与指定存活 Agent 共享资源键的冲突列表：[{key, others:[{agent_id, task, next_action, status}]}]"""
    me = (data.get("agents") or {}).get(agent_id) or {}
    my_keys = {r.get("key") for r in (me.get("resources") or []) if isinstance(r, dict)}
    out = []
    for aid, e in (data.get("agents") or {}).items():
        if aid == agent_id or not _alive(e):
            continue
        for r in (e.get("resources") or []):
            key = (r or {}).get("key")
            if key and key in my_keys:
                out.append({"key": key,
                            "agent_id": aid,
                            "workspace_name": e.get("workspace_name", ""),
                            "status": e.get("status", ""),
                            "task": e.get("task", ""),
                            "next_action": e.get("next_action", "")})
                break
    return out


def _protocol_text(me: dict, other: dict, key: str) -> str:
    return (f"【协调协议】资源「{key}」出现并发使用\n"
            f"- 对方：{other.get('agent_id', '')}（工作区 {other.get('workspace_name', '')}，"
            f"状态 {other.get('status', '')}）\n"
            f"  在干什么：{other.get('task') or '（未声明）'}\n"
            f"  接下来要干什么：{other.get('next_action') or '（未声明）'}\n"
            f"- 我：{me.get('agent_id', '')}（在干什么：{me.get('task') or '（未声明）'}；"
            f"接下来：{me.get('next_action') or '（未声明）'}）\n"
            f"请与对方协调顺序（等待/错峰/经用户确认）；重启/停服/批量删除等破坏性操作必须先经用户确认。")


def touch(agent_id: str, workspace: str, status: str = "running", task: str = None,
          resources=None, next_action: str = None, model: str = None,
          throttle: bool = True) -> dict:
    """登记/心跳 + 冲突检测 + 新冲突双向投递协议消息。返回本 Agent 视角快照。

    throttle=True 时若距上次心跳 < _TOUCH_THROTTLE_S 且无新增信息则跳过磁盘写（读侧仍返回快照）。
    """
    aid = str(agent_id or "").strip()
    if not aid:
        return {"agents": [], "conflicts": [], "inbox": []}
    keys = []
    seen = set()
    for r in (resources or []):
        k = norm_resource(r)
        if k and k not in seen:
            seen.add(k)
            keys.append({"key": k, "display": str(r).strip()[:160]})
    with _LOCK:
        data = _load()
        purged = _purge(data)   # v8.34（H4）：回收条数参与落盘判定
        agents = data["agents"]
        e = agents.get(aid)
        now = _now()
        created = False
        if e is None:
            created = True   # v8.34（H2）：新条目必须落盘，否则看板永远看不到这个 Agent
            e = {"agent_id": aid, "workspace": str(workspace or ""),
                 "workspace_name": Path(str(workspace or "")).name or "ws",
                 "pid": os.getpid(), "started": now, "status": status,
                 "task": "", "resources": [], "next_action": "",
                 "model": str(model or ""), "last_seen": now}
            agents[aid] = e
        else:
            e["last_seen"] = now
            e["status"] = status or e.get("status") or "running"
        if task is not None:
            e["task"] = str(task)[:300]
        if next_action is not None:
            e["next_action"] = str(next_action)[:300]
        if model:
            e["model"] = str(model)[:80]
        # 资源合并（去重），并检测新冲突 → 双向投递
        old_keys = {r.get("key") for r in (e.get("resources") or []) if isinstance(r, dict)}
        merged = {r["key"]: r for r in (e.get("resources") or []) if isinstance(r, dict)}
        new_conflicts = []
        for it in keys:
            k = it["key"]
            merged.setdefault(k, it)
            if k not in old_keys:
                for other_aid, oe in agents.items():
                    if other_aid == aid or not _alive(oe):
                        continue
                    if any((r or {}).get("key") == k for r in (oe.get("resources") or [])):
                        new_conflicts.append((other_aid, oe, k))
        e["resources"] = list(merged.values())
        changed = bool(keys) or task is not None or next_action is not None
        # v8.34（H2）修复：纯心跳也要按节流窗口落盘。此前条件为
        # `new_conflicts or not throttle or changed`——轮开始的 touch（只带 status/model）
        # 三者全 False → 连新建条目都不写盘，看板只显示"声明过资源"的 Agent，
        # 违背「检查每一个并发 Agent 在做什么」。写盘频率仍受 _TOUCH_THROTTLE_S 约束。
        due = (now - float(_LAST_PERSIST.get(aid) or 0.0)) >= _TOUCH_THROTTLE_S
        if new_conflicts or not throttle or changed or created or due or purged:
            for other_aid, oe, k in new_conflicts:
                inbox = data["inbox"].setdefault(other_aid, [])
                inbox.append({"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                              "from": aid, "resource": k,
                              "text": _protocol_text(e, oe, k)})
                data["inbox"][other_aid] = inbox[-50:]
                mine = data["inbox"].setdefault(aid, [])
                mine.append({"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             "from": aid, "resource": k,
                             "text": _protocol_text(e, oe, k)})
                data["inbox"][aid] = mine[-50:]
            _save(data)
            _LAST_PERSIST[aid] = now
    return _self_view(data, aid)


def _self_view(data: dict, agent_id: str) -> dict:
    return {"conflicts": _conflicts_for(data, agent_id),
            "inbox_count": len(data.get("inbox", {}).get(agent_id) or []),
            "agents_count": len([a for a in (data.get("agents") or {}).values() if _alive(a)])}


def snapshot(self_agent_id: str = None) -> dict:
    """人类看板/AI 看板：存活 Agent 列表 + 冲突对（不清收件箱）。"""
    with _LOCK:
        data = _load()
        # v8.34（H4）修复：回收必须落盘。此前 _purge 只改内存、snapshot 从不回写——
        # 死 Agent 与其收件箱在 coordination.json 里永久堆积（每次进程重启新增一个
        # 「目录名@新pid」条目，文件只增不减）；v8.33 的 TTL 断言只看 snapshot 返回值，
        # 所以测不出来。
        if _purge(data):
            _save(data)
        agents = []
        for aid, e in (data.get("agents") or {}).items():
            if not _alive(e):
                continue
            agents.append({
                "agent_id": aid,
                "workspace": e.get("workspace", ""),
                "workspace_name": e.get("workspace_name", ""),
                "pid": e.get("pid", 0),
                "status": e.get("status", ""),
                "task": e.get("task", ""),
                "next_action": e.get("next_action", ""),
                "resources": [r.get("display") or r.get("key") for r in (e.get("resources") or [])],
                "model": e.get("model", ""),
                "last_seen": e.get("last_seen", 0),
            })
        agents.sort(key=lambda x: x.get("last_seen") or 0, reverse=True)
        pairs = {}
        for aid, e in (data.get("agents") or {}).items():
            if not _alive(e):
                continue
            for r in (e.get("resources") or []):
                key = (r or {}).get("key")
                if key:
                    pairs.setdefault(key, []).append(aid)
        conflicts = [{"key": k, "agents": v} for k, v in pairs.items() if len(v) > 1]
        inbox = []
        if self_agent_id:
            for m in (data.get("inbox") or {}).get(self_agent_id) or []:
                inbox.append(m)
        return {"ok": True, "agents": agents, "conflicts": conflicts,
                "inbox": inbox, "ttl_s": (float(_KEEP_OVERRIDE) if _KEEP_OVERRIDE is not None else TTL_S)}


def pop_inbox(agent_id: str) -> list:
    """读取并清空本 Agent 收件箱（轮开始注入用）。"""
    with _LOCK:
        data = _load()
        msgs = (data.get("inbox") or {}).get(agent_id) or []
        if agent_id in (data.get("inbox") or {}):
            data["inbox"][agent_id] = []
            _save(data)
        return msgs


def send(to_agent_id: str, from_agent_id: str, text: str) -> bool:
    """Agent→Agent 直投消息（协议互通的人工/工具通道）。"""
    if not str(to_agent_id or "").strip() or not str(text or "").strip():
        return False
    with _LOCK:
        data = _load()
        inbox = data["inbox"].setdefault(str(to_agent_id), [])
        inbox.append({"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      "from": str(from_agent_id or ""), "resource": "",
                      "text": str(text)[:2000]})
        data["inbox"][to_agent_id] = inbox[-50:]
        _save(data)
        return True


def render_protocol_block(agent_id: str, conflicts: list, inbox: list) -> str:
    """轮开始注入的协调协议块（冲突 + 收件箱消息）。"""
    lines = ["【全局协调协议（v8.33）——检测到跨工作区并发】"]
    for c in conflicts or []:
        lines.append(f"- 资源「{c.get('key')}」正被 {c.get('agent_id')}"
                     f"（工作区 {c.get('workspace_name')}，状态 {c.get('status')}）使用："
                     f"在干什么={c.get('task') or '（未声明）'}；"
                     f"接下来={c.get('next_action') or '（未声明）'}")
    for m in inbox or []:
        lines.append(f"- 收件箱[{m.get('ts')}] 来自 {m.get('from')}：{str(m.get('text') or '')[:500]}")
    lines.append("协调要求：与对方错峰或等待；重启/停服/批量删除等破坏性操作必须先经用户确认；"
                 "用 coordination_declare 更新你的任务/资源/下一步，让对方也能看到。")
    return "\n".join(lines)
