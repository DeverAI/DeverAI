"""语音助手「小龙」2.0 宿主端：对话历史 + 增强任务管理 + 进度查询。

Adapted from dsh-plugin-pet (https://github.com/c-ling/dsh-plugin-pet)
Copyright (c) 2026 chenchao, MIT License
Modified for DeverAI voice assistant "小龙" — MIT License

能力：
  - 对话历史持久化（多轮上下文记忆）
  - 增强任务 CRUD（进度、优先级、标签）
  - 进度查询（任务 + 会话状态摘要）
  - 配置持久化（名字、大小、可见性、对话模式）

所有端点受 ENABLE_VOICE_ASSISTANT 与登录态双重保护。
"""

import asyncio
import datetime
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from .auth import current_user
from .config import DATA_DIR
from .storage import load_json, save_json
from .errors import log_error
from .bridge import _voice_check_enabled  # 复用 bridge.py 的开关检查，避免重复定义






router = APIRouter(prefix="/api/bridge/voice-pet", tags=["voice-pet"])

# ==================== 路径与常量 ====================

VOICE_CONVERSATIONS_PATH = DATA_DIR / "voice_conversations.json"
VOICE_PET_CONFIG_PATH = DATA_DIR / "voice_pet_config.json"
VOICE_TASKS_PATH = DATA_DIR / "voice_tasks.json"

# 与 bridge.py 共享同一把锁，避免对 voice_tasks.json 的并发写竞态
_VOICE_LOCK = asyncio.Lock()
try:
    from .bridge import _VOICE_TASK_LOCK as _VOICE_LOCK
except Exception:
    pass
_MAX_CONVERSATIONS = 500
_MAX_TASKS = 500
_MAX_MSG_LEN = 4000


# ==================== 开关检查 ====================

# _voice_check_enabled 从 bridge.py 导入（避免重复定义）


# ==================== 工具函数 ====================

def _now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _gen_id(prefix: str = "vt_") -> str:
    return prefix + secrets.token_hex(6)


def _load_conversations() -> dict:
    data = load_json(VOICE_CONVERSATIONS_PATH, {"conversations": []})
    if not isinstance(data, dict):
        return {"conversations": []}
    if not isinstance(data.get("conversations"), list):
        data["conversations"] = []
    return data


def _save_conversations(data: dict) -> None:
    save_json(VOICE_CONVERSATIONS_PATH, data)


def _load_pet_config() -> dict:
    data = load_json(VOICE_PET_CONFIG_PATH, {})
    if not isinstance(data, dict):
        return {}
    return data


def _save_pet_config(data: dict) -> None:
    save_json(VOICE_PET_CONFIG_PATH, data)


def _load_tasks() -> dict:
    data = load_json(VOICE_TASKS_PATH, {"tasks": []})
    if not isinstance(data, dict):
        return {"tasks": []}
    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        tasks = list(tasks.values()) if isinstance(tasks, dict) else []
    return {"tasks": tasks}


def _save_tasks(data: dict) -> None:
    save_json(VOICE_TASKS_PATH, data)


# ==================== 配置端点 ====================

@router.get("/config")
async def get_pet_config(user: dict = Depends(current_user)):
    """获取语音助手配置（名字、大小、可见性、对话模式等）。"""
    _voice_check_enabled()
    cfg = _load_pet_config()
    # 补充默认值
    defaults = {
        "name": "小龙",
        "size": 1,
        "visible": True,
        "conv_mode": "feedback",
        "mood_state": "idle",
        "builtin_sprite": "blob",
    }
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k] = v
    return {"ok": True, "config": cfg}


@router.post("/config")
async def update_pet_config(body: dict, user: dict = Depends(current_user)):
    """更新语音助手配置。body: {name?, size?, visible?, conv_mode?, mood_state?, builtin_sprite?}"""
    _voice_check_enabled()
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须为 JSON 对象")
    async with _VOICE_LOCK:
        cfg = _load_pet_config()
        # 白名单字段
        if "name" in body and isinstance(body["name"], str):
            name = body["name"].strip()
            if 0 < len(name) <= 32:
                cfg["name"] = name
        if "size" in body and isinstance(body["size"], (int, float)):
            s = float(body["size"])
            cfg["size"] = max(0.5, min(1.5, s))
        if "visible" in body and isinstance(body["visible"], bool):
            cfg["visible"] = body["visible"]
        if "conv_mode" in body and body["conv_mode"] in ("feedback", "agent"):
            cfg["conv_mode"] = body["conv_mode"]
        if "mood_state" in body and isinstance(body["mood_state"], str):
            cfg["mood_state"] = body["mood_state"][:32]
        if "builtin_sprite" in body and isinstance(body["builtin_sprite"], str):
            cfg["builtin_sprite"] = body["builtin_sprite"][:32]
        _save_pet_config(cfg)
    return {"ok": True, "config": cfg}


# ==================== 对话历史端点 ====================

@router.get("/conversations")
async def get_conversations(limit: int = 50, user: dict = Depends(current_user)):
    """获取对话历史（最近 limit 条）。"""
    _voice_check_enabled()
    data = _load_conversations()
    convs = data.get("conversations", [])
    # 负/超大 limit 夹取，避免切片语义错误或全量回传
    try:
        limit = max(0, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 50
    # 返回最近 N 条
    recent = convs[-limit:] if limit and len(convs) > limit else (convs if limit else [])
    return {"ok": True, "conversations": recent, "total": len(convs)}


@router.post("/conversations")
async def append_conversation(body: dict, user: dict = Depends(current_user)):
    """追加一条对话记录。body: {role: "user"|"assistant", content: string, mood?: string}"""
    _voice_check_enabled()
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须为 JSON 对象")
    role = body.get("role")
    if role not in ("user", "assistant"):
        raise HTTPException(400, "role 必须为 user 或 assistant")
    content = str(body.get("content", "")).strip()
    if not content:
        raise HTTPException(400, "content 不能为空")
    if len(content) > _MAX_MSG_LEN:
        content = content[:_MAX_MSG_LEN]
    async with _VOICE_LOCK:
        data = _load_conversations()
        convs = data.get("conversations", [])
        msg = {
            "id": _gen_id("cv_"),
            "role": role,
            "content": content,
            "ts": _now_iso(),
        }
        if "mood" in body and isinstance(body["mood"], str):
            msg["mood"] = body["mood"]
        convs.append(msg)
        # 有界淘汰
        if len(convs) > _MAX_CONVERSATIONS:
            convs = convs[-_MAX_CONVERSATIONS:]
        data["conversations"] = convs
        _save_conversations(data)
    return {"ok": True, "message": msg}


@router.delete("/conversations")
async def clear_conversations(user: dict = Depends(current_user)):
    """清空对话历史。"""
    _voice_check_enabled()
    async with _VOICE_LOCK:
        _save_conversations({"conversations": []})
    return {"ok": True, "message": "对话历史已清空"}


# ==================== 增强任务端点 ====================

@router.get("/tasks")
async def get_tasks(user: dict = Depends(current_user)):
    """获取任务列表（增强版，含进度/优先级/标签）。"""
    _voice_check_enabled()
    data = _load_tasks()
    return {"ok": True, **data}


@router.post("/tasks")
async def add_task(body: dict, user: dict = Depends(current_user)):
    """添加任务。body: {title, detail?, priority?("low"|"mid"|"high"), tags?: string[]}"""
    _voice_check_enabled()
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须为 JSON 对象")
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "任务标题不能为空")
    if len(title) > 500:
        raise HTTPException(400, "任务标题过长（上限 500 字符）")
    priority = body.get("priority", "mid")
    if priority not in ("low", "mid", "high"):
        priority = "mid"
    tags = body.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    tags = [str(t)[:32] for t in tags if isinstance(t, str)][:10]
    async with _VOICE_LOCK:
        data = _load_tasks()
        tasks = data.get("tasks", [])
        if isinstance(tasks, dict):
            tasks = list(tasks.values())
        now = _now_iso()
        task = {
            "id": _gen_id("vt_"),
            "title": title,
            "detail": str(body.get("detail") or "")[:2000],
            "status": "pending",
            "priority": priority,
            "tags": tags,
            "progress": 0,
            "createdAt": now,
            "updatedAt": now,
        }
        tasks.append(task)
        if len(tasks) > _MAX_TASKS:
            tasks = tasks[-_MAX_TASKS:]
        result = {"tasks": tasks}
        _save_tasks(result)
    return {"ok": True, "task": task}


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: dict, user: dict = Depends(current_user)):
    """更新任务。body: {title?, detail?, status?, priority?, tags?, progress?}"""
    _voice_check_enabled()
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须为 JSON 对象")
    async with _VOICE_LOCK:
        data = _load_tasks()
        tasks = data.get("tasks", [])
        if isinstance(tasks, dict):
            tasks = list(tasks.values())
        target = None
        for t in tasks:
            if t.get("id") == task_id:
                target = t
                break
        if target is None:
            raise HTTPException(404, f"任务 {task_id} 不存在")
        # 白名单更新
        for field in ("title", "detail"):
            if field in body and isinstance(body[field], str):
                val = body[field].strip()
                if field == "title" and len(val) > 500:
                    raise HTTPException(400, "任务标题过长")
                if field == "detail" and len(val) > 2000:
                    val = val[:2000]
                target[field] = val
        if "status" in body and body["status"] in ("pending", "done", "cancelled"):
            target["status"] = body["status"]
        if "priority" in body and body["priority"] in ("low", "mid", "high"):
            target["priority"] = body["priority"]
        if "tags" in body and isinstance(body["tags"], list):
            target["tags"] = [str(t)[:32] for t in body["tags"] if isinstance(t, str)][:10]
        if "progress" in body and isinstance(body["progress"], (int, float)):
            try:
                p = float(body["progress"])
                if p != p:  # NaN 防护
                    raise ValueError("NaN")
                target["progress"] = max(0, min(100, int(p)))
            except (TypeError, ValueError, OverflowError):
                pass
        target["updatedAt"] = _now_iso()
        _save_tasks({"tasks": tasks})
    return {"ok": True, "task": target}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, user: dict = Depends(current_user)):
    """删除任务。"""
    _voice_check_enabled()
    async with _VOICE_LOCK:
        data = _load_tasks()
        tasks = data.get("tasks", [])
        if isinstance(tasks, dict):
            tasks = list(tasks.values())
        new_tasks = [t for t in tasks if t.get("id") != task_id]
        if len(new_tasks) == len(tasks):
            raise HTTPException(404, f"任务 {task_id} 不存在")
        _save_tasks({"tasks": new_tasks})
    return {"ok": True, "message": f"已删除任务 {task_id}"}


@router.post("/tasks/continue")
async def continue_next_task(user: dict = Depends(current_user)):
    """将最早一个 pending 任务标记为 done，返回执行报告。"""
    _voice_check_enabled()
    async with _VOICE_LOCK:
        data = _load_tasks()
        tasks = data.get("tasks", [])
        if isinstance(tasks, dict):
            tasks = list(tasks.values())
        for t in tasks:
            if t.get("status") == "pending":
                t["status"] = "done"
                t["progress"] = 100
                t["updatedAt"] = _now_iso()
                _save_tasks({"tasks": tasks})
                return {"ok": True, "message": f"已完成任务：{t.get('title', '')}", "task": t}
        return {"ok": False, "message": "没有待办任务，全部已完成。"}


# ==================== 进度查询端点 ====================

@router.get("/progress")
async def get_progress(user: dict = Depends(current_user)):
    """获取进度摘要：任务统计 + 配置状态。"""
    _voice_check_enabled()
    tasks_data = _load_tasks()
    tasks = tasks_data.get("tasks", [])
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    pet_cfg = _load_pet_config()
    pending = [t for t in tasks if t.get("status") == "pending"]
    done = [t for t in tasks if t.get("status") == "done"]
    cancelled = [t for t in tasks if t.get("status") == "cancelled"]
    # 计算平均进度
    total_progress = sum(t.get("progress", 0) for t in tasks)
    avg_progress = round(total_progress / len(tasks), 1) if tasks else 0
    # 下一个优先任务
    next_task = None
    for t in sorted(pending, key=lambda x: {"high": 0, "mid": 1, "low": 2}.get(x.get("priority", "mid"), 1)):
        next_task = t
        break
    return {
        "ok": True,
        "summary": {
            "total": len(tasks),
            "pending": len(pending),
            "done": len(done),
            "cancelled": len(cancelled),
            "avgProgress": avg_progress,
            "nextTask": next_task,
            "petName": pet_cfg.get("name", "小龙"),
            "petMood": pet_cfg.get("mood_state", "idle"),
            "convMode": pet_cfg.get("conv_mode", "feedback"),
        },
    }
