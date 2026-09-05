"""DeverAI 同步服务器（sync_server.py）——部署在用户自有服务器的第四端。

职责（Design.md §2.3 / §6.8）：
  - 快照存取：POST /push、GET /pull（桌面 zlib/base64 冷备与网页 web-cold 信封双格式原样写回）
  - 冷备 Agent 续聊：POST /chat（读→LLM→写三段式 _CHAT_LOCK，每轮 unacked_count+1）
  - 漂移状态：/drift/begin|status|finish（服务器端 drift_state.json）
  - 远程指挥：/cmd/register|poll|heartbeat|result|dispatch|nodes|results
  - HTML 状态页：/ （漂移状态 + 冷备会话续聊 + 远程指挥控制台）、/chat/page（续聊页）

环境变量：
  - SYNC_TOKEN           远程鉴权令牌（客户端请求头 X-Sync-Token）。
                         未设置时仅环回客户端可访问；检测到反代 XFF 时强制要求令牌。
  - DRIFT_API_BASE       冷备 Agent 直连 LLM base_url（OpenAI 兼容，如 https://api.deepseek.com/v1）
  - DRIFT_API_KEY        冷备 Agent LLM Key
  - DRIFT_ALLOW_LOOPBACK 允许 DRIFT_API_BASE 指向环回地址（本地 LLM；默认拒绝）
  - DRIFT_MODEL          冷备续聊默认模型（缺省取快照内 config.model，再缺省 gpt-4o-mini）
  - SYNC_DATA_DIR        数据目录（默认 ./sync_data，存放 snapshots.json/cold.json/drift_state.json）

数据文件（Design.md §5.2）：
  snapshots.json  主快照（保持客户端入参格式原样写回）
  cold.json       无口令冷备副本（冷备 Agent 续聊用，恒为历史超集）
  drift_state.json 服务器端漂移状态（project_id/active/unacked_count）

安全红线（Design.md §8 / 决策99）：
  - 未设 SYNC_TOKEN 时仅环回可访问；X-Forwarded-For 存在时强制要求令牌
  - CORS 收紧为环回源；/cmd/result 体限 1MB；命令队列 maxlen=100、节点 TTL 2 分钟、结果 TTL 10 分钟
  - 加密主快照永不降级：/chat 只在主快照本就是无口令冷备格式时才回写
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import datetime
import json
import os
import time
import uuid
import zlib
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response

APP_DIR = Path(__file__).resolve().parent

# ----------------------------- 配置（环境变量） -----------------------------

SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "").strip()
DRIFT_API_BASE = (os.environ.get("DRIFT_API_BASE", "") or "").strip().rstrip("/")
DRIFT_API_KEY = (os.environ.get("DRIFT_API_KEY", "") or "").strip()
DRIFT_MODEL = (os.environ.get("DRIFT_MODEL", "") or "").strip()
DRIFT_ALLOW_LOOPBACK = str(os.environ.get("DRIFT_ALLOW_LOOPBACK", "")).strip().lower() in ("1", "true", "yes", "on")
DATA_DIR = Path(os.environ.get("SYNC_DATA_DIR", str(APP_DIR / "sync_data"))).resolve()

SNAPSHOTS_PATH = DATA_DIR / "snapshots.json"
COLD_PATH = DATA_DIR / "cold.json"
DRIFT_PATH = DATA_DIR / "drift_state.json"

# 内存上限（Design 决策59/99）
NODE_TTL_S = 120          # 节点注册表 TTL 2 分钟
RESULT_TTL_S = 600        # 命令结果 TTL 10 分钟
CMD_QUEUE_MAX = 100       # 每节点命令队列上限
PUSH_MAX_BYTES = 64 * 1024 * 1024    # /push 体上限 64MB
RESULT_MAX_BYTES = 1024 * 1024       # /cmd/result 体上限 1MB
BODY_MAX_BYTES = 2 * 1024 * 1024     # 其余请求体上限 2MB
CHAT_MSG_MAX = 16000      # 单条续聊消息长度上限
LLM_TIMEOUT_S = 120.0

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}

# ----------------------------- 运行态（内存） -----------------------------

_NODES: dict[str, dict] = {}      # node_id -> {name, workspace, agent_mode, last_seen}
_QUEUES: dict[str, deque] = {}    # node_id -> deque[{cmd_id, type, payload}] maxlen=100
_RESULTS: dict[str, dict] = {}    # node_id -> {cmd_id: {"result":..., "ts":...}}
_CHAT_LOCK = asyncio.Lock()       # 读→LLM→写 三段式 + 与 /push 文件写互斥

app = FastAPI(title="DeverAI Sync Server", docs_url=None, redoc_url=None, openapi_url=None)


# ----------------------------- 基础工具 -----------------------------

def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_text(p: Path) -> str:
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def _atomic_write(p: Path, text: str) -> None:
    """唯一临时文件 + os.replace 原子写（与项目 storage.py 同一纪律）。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="")
        os.replace(str(tmp), str(p))
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _parse_snapshot(text: str):
    """解析快照文本（双格式，Design 决策106）。

    返回 (payload | None, fmt)；fmt ∈ {"desktop", "web-cold", "encrypted", "invalid"}。
    - desktop：urlsafe base64(zlib(b"obfuscated\\x00" + JSON))（无口令冷备）
    - web-cold：{"v":2,"kind":"web-cold","data":标准base64(UTF-8 JSON)}
    - encrypted：网页 AES-GCM 信封或桌面 Fernet 密文（服务器无法解，只有 /pull 原样回传）
    """
    t = (text or "").strip()
    if not t:
        return None, "invalid"
    if t.startswith("{"):
        try:
            obj = json.loads(t)
        except Exception:
            return None, "invalid"
        if not isinstance(obj, dict):
            return None, "invalid"
        if obj.get("kind") == "web-cold" and isinstance(obj.get("data"), str):
            try:
                raw = base64.b64decode(obj["data"], validate=True)
                return json.loads(raw.decode("utf-8")), "web-cold"
            except Exception:
                return None, "invalid"
        if isinstance(obj.get("salt"), str) and isinstance(obj.get("iv"), str) and isinstance(obj.get("data"), str):
            return None, "encrypted"
        return None, "invalid"
    try:
        data = base64.urlsafe_b64decode(t.encode("ascii"))
    except Exception:
        return None, "invalid"
    try:
        plain = zlib.decompress(data)
    except Exception:
        # base64 合法但解不开 zlib：桌面 Fernet 加密主快照
        return None, "encrypted"
    if plain.startswith(b"obfuscated"):
        parts = plain.split(b"\x00", 1)
        if len(parts) < 2:
            return None, "invalid"
        plain = parts[1]
    try:
        return json.loads(plain.decode("utf-8")), "desktop"
    except Exception:
        return None, "invalid"


def _serialize_snapshot(payload: dict, fmt: str) -> str:
    """按原格式写回（保持入参格式，决策106）。"""
    if fmt == "web-cold":
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return json.dumps({"v": 2, "kind": "web-cold",
                           "data": base64.b64encode(raw).decode("ascii")}, ensure_ascii=False)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(b"obfuscated\x00" + raw, 9)).decode("ascii")


def _load_drift_state() -> dict:
    try:
        if DRIFT_PATH.is_file():
            data = json.loads(DRIFT_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"project_id": "", "active": False, "unacked_count": 0, "updated_at": ""}


def _save_drift_state(state: dict) -> None:
    _atomic_write(DRIFT_PATH, json.dumps(state, ensure_ascii=False))


def _load_main_snapshot() -> dict:
    text = _read_text(SNAPSHOTS_PATH)
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower().strip("[]")
    return h in _LOOPBACK_HOSTS


def _gc_nodes() -> None:
    """惰性 GC：过期节点/过期结果（决策59）。"""
    now = time.time()
    for nid in [k for k, v in _NODES.items() if now - v.get("last_seen", 0) > NODE_TTL_S]:
        _NODES.pop(nid, None)
        _QUEUES.pop(nid, None)
    for nid in list(_RESULTS.keys()):
        results = _RESULTS.get(nid) or {}
        for cid in [k for k, v in results.items() if now - v.get("ts", 0) > RESULT_TTL_S]:
            results.pop(cid, None)
        if not results and nid not in _NODES:
            _RESULTS.pop(nid, None)


# ----------------------------- 鉴权与体限中间件（决策99） -----------------------------

def _origin_is_loopback(origin: str) -> bool:
    try:
        host = urlparse(origin).hostname or ""
        return _is_loopback_host(host)
    except Exception:
        return False


@app.middleware("http")
async def _gate_middleware(request: Request, call_next):
    # CORS 预检：仅环回源放行
    if request.method == "OPTIONS":
        origin = request.headers.get("origin", "")
        if origin and _origin_is_loopback(origin):
            return Response(status_code=204, headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Headers": "Content-Type, X-Sync-Token",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Vary": "Origin",
            })
        return Response(status_code=204)

    client_ip = request.client.host if request.client else ""
    xff = request.headers.get("x-forwarded-for", "")
    is_loopback = _is_loopback_host(client_ip)

    # 反代存在时强制要求令牌
    if xff and not SYNC_TOKEN:
        return JSONResponse(
            {"detail": "检测到反向代理（X-Forwarded-For）：非环回部署必须设置 SYNC_TOKEN 环境变量。"},
            status_code=403)
    # 未设令牌时仅环回客户端可访问
    if not SYNC_TOKEN and not is_loopback:
        return JSONResponse(
            {"detail": "未设置 SYNC_TOKEN：仅环回客户端可访问。远程部署请设置 SYNC_TOKEN 并在客户端配置 sync_token。"},
            status_code=403)
    # 设置了令牌：所有请求必须携带（HTML 页可用 ?token= 便于浏览器直开）
    if SYNC_TOKEN:
        supplied = request.headers.get("x-sync-token", "") or request.query_params.get("token", "")
        if supplied != SYNC_TOKEN:
            return JSONResponse({"detail": "无效的同步令牌（X-Sync-Token）。"}, status_code=401)

    # 请求体上限（按端点分级）
    path = request.url.path
    if path == "/push":
        limit = PUSH_MAX_BYTES
    elif path.startswith("/cmd/result"):
        limit = RESULT_MAX_BYTES + 65536
    else:
        limit = BODY_MAX_BYTES
    content_length = request.headers.get("content-length", "")
    if content_length:
        try:
            n = int(content_length)
            if n > limit:
                return JSONResponse({"detail": f"请求体超过上限（{limit // 1024}KB）。"}, status_code=413)
        except ValueError:
            pass

    resp = await call_next(request)

    # CORS 收紧为环回源
    origin = request.headers.get("origin", "")
    if origin and _origin_is_loopback(origin):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    return resp


# ----------------------------- 快照存取 -----------------------------

@app.post("/push")
async def push(body: dict):
    """接收客户端快照。data=主快照（加密或无口令），cold=无口令冷备副本。

    保持入参格式原样写回（决策106）；与 /chat 的文件写互斥（决策79）。
    """
    data = str(body.get("data") or "")
    cold = str(body.get("cold") or "")
    if not data:
        raise HTTPException(400, "缺少 data 字段（主快照）。")
    now = _now()
    async with _CHAT_LOCK:
        snap = {"data": data, "cold": cold, "updated_at": now}
        _atomic_write(SNAPSHOTS_PATH, json.dumps(snap, ensure_ascii=False))
        if cold:
            _atomic_write(COLD_PATH, cold)
    return {"ok": True, "message": "已接收快照", "updated_at": now}


@app.get("/pull")
async def pull():
    """回传主快照 + 冷备副本（drift_cold 含云端续聊消息，恒为历史超集）。"""
    snap = _load_main_snapshot()
    data = str(snap.get("data") or "")
    drift_cold = _read_text(COLD_PATH)
    return {"data": data, "drift_cold": drift_cold, "updated_at": snap.get("updated_at", "")}


# ----------------------------- 漂移状态机 -----------------------------

@app.post("/drift/begin")
async def drift_begin(body: dict):
    """登记「正在算力漂移」并复位未回传计数（客户端退出漂移时调用）。"""
    state = {
        "project_id": str(body.get("project_id") or ""),
        "active": bool(body.get("active", True)),
        "began_at": _now(),
        "unacked_count": 0,
        "updated_at": _now(),
    }
    _save_drift_state(state)
    return {"ok": True, "project_id": state["project_id"], "unacked_count": 0}


@app.get("/drift/status")
async def drift_status(project_id: str = Query(default="")):
    """查询服务器是否仍有未回传的云端产出（本地开机判断是否锁定项目）。"""
    _gc_nodes()
    st = _load_drift_state()
    return {
        "ok": True,
        "project_id": str(st.get("project_id") or ""),
        "active": bool(st.get("active", False)),
        "unacked_count": int(st.get("unacked_count", 0) or 0),
        "updated_at": str(st.get("updated_at") or ""),
    }


@app.post("/drift/finish")
async def drift_finish(body: dict):
    """本地确认已把云端产出漂移回本地，复位服务器漂移状态。"""
    st = _load_drift_state()
    st["active"] = False
    st["unacked_count"] = 0
    st["ended_at"] = _now()
    st["updated_at"] = _now()
    _save_drift_state(st)
    return {"ok": True}


# ----------------------------- 冷备 Agent 续聊 -----------------------------

async def _llm_reply(model: str, history: list) -> str:
    """冷备 Agent 直连 LLM（OpenAI 兼容 /chat/completions，非流式）。"""
    if not DRIFT_API_BASE:
        raise HTTPException(503, "服务器未配置 DRIFT_API_BASE/DRIFT_API_KEY，冷备 Agent 无法续聊。")
    host = urlparse(DRIFT_API_BASE).hostname or ""
    if _is_loopback_host(host) and not DRIFT_ALLOW_LOOPBACK:
        raise HTTPException(400, "DRIFT_API_BASE 指向环回地址：需设置 DRIFT_ALLOW_LOOPBACK=1 才允许本地 LLM。")
    msgs = []
    for m in history:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role in ("system", "user", "assistant") and isinstance(content, str) and content.strip():
            msgs.append({"role": role, "content": content})
    if not msgs:
        raise HTTPException(400, "冷备会话历史为空，无法续聊。")
    msgs = msgs[-40:]
    headers = {"Authorization": f"Bearer {DRIFT_API_KEY}"} if DRIFT_API_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
            resp = await client.post(
                DRIFT_API_BASE + "/chat/completions",
                json={"model": model, "messages": msgs, "stream": False},
                headers=headers,
            )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"冷备 LLM 网络异常：{str(e)[:200]}")
    if resp.status_code != 200:
        raise HTTPException(502, f"冷备 LLM 调用失败 HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        reply = resp.json()["choices"][0]["message"]["content"]
    except Exception:
        raise HTTPException(502, "冷备 LLM 响应格式异常。")
    return str(reply or "").strip() or "（空回复）"


@app.post("/chat")
async def chat(body: dict):
    """冷备 Agent 续聊：读冷备 → LLM → 写回（三段式 _CHAT_LOCK，每轮 unacked+1）。

    只回写无口令流（cold.json）；主快照仅当其本就是无口令冷备格式时才同步回写，
    加密主快照永不降级（Design 决策79 / §8 红线）。
    """
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "消息不能为空。")
    if len(message) > CHAT_MSG_MAX:
        raise HTTPException(400, f"消息过长（上限 {CHAT_MSG_MAX} 字符）。")

    async with _CHAT_LOCK:
        cold_text = _read_text(COLD_PATH)
        if not cold_text:
            raise HTTPException(400, "服务器上暂无冷备副本：请先在客户端推送含冷备的快照（include_cold=True）。")
        payload, fmt = _parse_snapshot(cold_text)
        if payload is None:
            raise HTTPException(400, f"冷备副本无法解析（fmt={fmt}）：冷备应为无口令格式。")
        history = payload.get("history")
        if not isinstance(history, list):
            history = []

        now = _now()
        cfg = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        model = str(cfg.get("model") or DRIFT_MODEL or "gpt-4o-mini")
        history.append({"role": "user", "content": message, "from_server": True, "drift_ts": now})
        reply = await _llm_reply(model, history)
        history.append({"role": "assistant", "content": reply, "from_server": True, "drift_ts": now})
        payload["history"] = history

        # 冷备恒为历史超集：写回 cold.json（保持原格式）
        _atomic_write(COLD_PATH, _serialize_snapshot(payload, fmt))

        # 主快照无口令时保持一致回写；加密主快照保持不动
        snap = _load_main_snapshot()
        main_text = str(snap.get("data") or "")
        if main_text:
            main_payload, main_fmt = _parse_snapshot(main_text)
            if main_payload is not None:
                main_payload["history"] = history
                snap["data"] = _serialize_snapshot(main_payload, main_fmt)
                snap["updated_at"] = now
                _atomic_write(SNAPSHOTS_PATH, json.dumps(snap, ensure_ascii=False))

        # 未回传计数 +1（本地开机据此锁定项目，漂移回本地后 /drift/finish 复位）
        st = _load_drift_state()
        st["unacked_count"] = int(st.get("unacked_count", 0) or 0) + 1
        st["updated_at"] = now
        _save_drift_state(st)

    return {"ok": True, "reply": reply, "drift_ts": now}


@app.get("/chat/history")
async def chat_history():
    """HTML 状态页用：解析冷备副本返回最近会话历史（两种格式统一服务端解包）。"""
    cold_text = _read_text(COLD_PATH)
    if not cold_text:
        return {"ok": False, "history": [], "updated_at": ""}
    payload, fmt = _parse_snapshot(cold_text)
    if payload is None:
        return {"ok": False, "history": [], "updated_at": ""}
    history = [m for m in (payload.get("history") or []) if isinstance(m, dict)]
    cfg = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    return {"ok": True, "history": history[-200:],
            "updated_at": str(payload.get("ts") or ""), "model": str(cfg.get("model") or "")}


# ----------------------------- 远程指挥（/cmd/*） -----------------------------

@app.post("/cmd/register")
async def cmd_register(body: dict):
    """节点注册（本地端 RemoteCmdClient 启动时调用）。"""
    _gc_nodes()
    node_id = uuid.uuid4().hex[:12]
    _NODES[node_id] = {
        "name": str(body.get("name") or "local")[:100],
        "workspace": str(body.get("workspace") or "")[:300],
        "agent_mode": str(body.get("agent_mode") or "builder")[:50],
        "last_seen": time.time(),
        "registered_at": _now(),
    }
    _QUEUES[node_id] = deque(maxlen=CMD_QUEUE_MAX)
    _RESULTS.setdefault(node_id, {})
    return {"ok": True, "node_id": node_id}


@app.get("/cmd/poll/{node_id}")
async def cmd_poll(node_id: str, wait: int = Query(default=30, ge=0, le=60)):
    """长轮询命令：hold 至 wait（上限 25s），无命令到点返回空列表。"""
    node = _NODES.get(node_id)
    if not node:
        raise HTTPException(404, "节点未注册或已过期，请重新 register。")
    node["last_seen"] = time.time()
    deadline = time.time() + min(wait, 25)
    while time.time() < deadline:
        q = _QUEUES.get(node_id)
        if q:
            cmds = list(q)
            q.clear()
            node["last_seen"] = time.time()
            return {"commands": cmds}
        await asyncio.sleep(0.5)
    return {"commands": []}


@app.post("/cmd/heartbeat/{node_id}")
async def cmd_heartbeat(node_id: str, body: dict):
    node = _NODES.get(node_id)
    if not node:
        raise HTTPException(404, "节点未注册或已过期。")
    node["last_seen"] = time.time()
    node["status"] = str(body.get("status") or "alive")[:50]
    return {"ok": True}


@app.post("/cmd/result/{node_id}/{cmd_id}")
async def cmd_result(node_id: str, cmd_id: str, request: Request):
    """本地端回传命令执行结果（体限 1MB，中间件强制）。"""
    node = _NODES.get(node_id)
    if not node:
        raise HTTPException(404, "节点未注册或已过期。")
    raw = await request.body()
    try:
        data = json.loads(raw)
    except Exception:
        raise HTTPException(400, "结果体不是有效 JSON。")
    if not isinstance(data, dict):
        raise HTTPException(400, "结果体必须是 JSON 对象。")
    _RESULTS.setdefault(node_id, {})[cmd_id] = {"result": data, "ts": time.time()}
    return {"ok": True}


@app.post("/cmd/dispatch")
async def cmd_dispatch(body: dict):
    """远程指挥控制台下发命令：type ∈ shell/read_file/write_file/ping。

    危险命令由本地端 danger_ok 协议拦截（remote_cmd._exec_shell 同权，
    服务器只做形状校验，不重复维护危险模式表——四端同源纪律不破坏）。
    """
    node_id = str(body.get("node_id") or "")
    node = _NODES.get(node_id)
    if not node:
        raise HTTPException(404, "节点未注册或已过期。")
    if time.time() - node.get("last_seen", 0) > NODE_TTL_S:
        raise HTTPException(404, "节点心跳已过期。")
    cmd_type = str(body.get("type") or "")
    payload = body.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    if cmd_type not in ("shell", "read_file", "write_file", "ping"):
        raise HTTPException(400, "type 必须是 shell/read_file/write_file/ping。")
    if cmd_type == "shell":
        command = str(payload.get("command") or "").strip()
        if not command:
            raise HTTPException(400, "shell 命令不能为空。")
        if len(command) > 32000:
            raise HTTPException(400, "命令过长（上限 32000 字符）。")
    elif cmd_type in ("read_file", "write_file"):
        if not str(payload.get("path") or "").strip():
            raise HTTPException(400, "path 不能为空。")
    cmd_id = uuid.uuid4().hex[:12]
    _QUEUES.setdefault(node_id, deque(maxlen=CMD_QUEUE_MAX)).append(
        {"cmd_id": cmd_id, "type": cmd_type, "payload": payload})
    return {"ok": True, "cmd_id": cmd_id}


@app.get("/cmd/nodes")
async def cmd_nodes():
    _gc_nodes()
    nodes = []
    for nid, n in _NODES.items():
        nodes.append({
            "node_id": nid,
            "name": n.get("name", ""),
            "workspace": n.get("workspace", ""),
            "agent_mode": n.get("agent_mode", ""),
            "registered_at": n.get("registered_at", ""),
            "alive": time.time() - n.get("last_seen", 0) <= NODE_TTL_S,
        })
    return {"ok": True, "nodes": nodes}


@app.get("/cmd/results/{node_id}")
async def cmd_results(node_id: str):
    """轮询某节点最近命令结果（TTL 10 分钟内）。"""
    _gc_nodes()
    results = _RESULTS.get(node_id) or {}
    items = []
    for cid, rec in sorted(results.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)[:50]:
        r = rec.get("result") or {}
        items.append({"cmd_id": cid, "ts": _fmt_ts(rec.get("ts", 0)), **r})
    return {"ok": True, "results": items}


def _fmt_ts(ts: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


# ----------------------------- HTML 状态页（零 CDN，无 emoji） -----------------------------

_PAGE_CSS = """
:root{--bg:#101418;--fg:#d8dee6;--fg2:#8b95a1;--card:#181e26;--line:#2a323d;
--acc:#4f8cc9;--ok:#3f9d6b;--warn:#c9a24f;--err:#c95f5f;--mono:Consolas,Menlo,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font:14px/1.6 "Segoe UI",system-ui,sans-serif;padding:16px}
h1{font-size:18px;margin-bottom:4px} .sub{color:var(--fg2);font-size:12px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px;margin-bottom:14px}
.card h2{font-size:14px;margin-bottom:10px;color:var(--acc)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:6px}
.kv{font-size:13px;color:var(--fg2)} .kv b{color:var(--fg);font-weight:600}
.tag-ok{color:var(--ok)} .tag-warn{color:var(--warn)} .tag-err{color:var(--err)}
input[type=text],textarea,select{background:#0c1014;border:1px solid var(--line);color:var(--fg);
border-radius:6px;padding:7px 9px;font:13px var(--mono);outline:none;flex:1;min-width:120px}
textarea{width:100%;min-height:60px;resize:vertical}
button{background:var(--acc);color:#fff;border:none;border-radius:6px;padding:7px 14px;
cursor:pointer;font-size:13px} button.sec{background:#2a323d} button:hover{filter:brightness(1.15)}
.msgs{max-height:340px;overflow-y:auto;border:1px solid var(--line);border-radius:6px;padding:8px;
background:#0c1014;margin-bottom:8px}
.m{margin-bottom:8px;padding:6px 8px;border-radius:6px;font-size:13px;white-space:pre-wrap;word-break:break-word}
.m .r{font-size:11px;color:var(--fg2);margin-bottom:2px}
.m.user{background:#1c2733} .m.assistant{background:#182420} .m .rr{font-size:11px;color:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{color:var(--fg2);font-weight:500}
.small{font-size:12px;color:var(--fg2)} code{font-family:var(--mono);color:var(--warn)}
.out{font-family:var(--mono);font-size:12px;white-space:pre-wrap;word-break:break-all;background:#0c1014;
border:1px solid var(--line);border-radius:6px;padding:8px;max-height:200px;overflow-y:auto}
label{font-size:13px;color:var(--fg2);display:flex;align-items:center;gap:4px}
"""

_PAGE_JS = """
const $=id=>document.getElementById(id);
function token(){let t=localStorage.getItem('sync_token')||'';
  const q=new URLSearchParams(location.search).get('token');
  if(q){t=q;localStorage.setItem('sync_token',t);}return t;}
async function api(path,opts){opts=opts||{};opts.headers=Object.assign({'Content-Type':'application/json'},opts.headers||{});
  const t=token();if(t)opts.headers['X-Sync-Token']=t;
  const r=await fetch(path,opts);const d=await r.json().catch(()=>({detail:'非 JSON 响应'}));
  if(!r.ok)throw new Error(d.detail||('HTTP '+r.status));return d;}
function esc(s){return String(s==null?'':s);}
function renderMsgs(el,hist){el.innerHTML='';hist.forEach(m=>{
  const d=document.createElement('div');d.className='m '+(esc(m.role)==='user'?'user':'assistant');
  const r=document.createElement('div');r.className='r';
  r.textContent=(esc(m.role)==='user'?'用户':'冷备 Agent')+(m.drift_ts?(' · '+m.drift_ts):'')+(m.from_server?' · [云端]':'');
  const c=document.createElement('div');c.textContent=esc(m.content);
  d.appendChild(r);d.appendChild(c);el.appendChild(d);});el.scrollTop=el.scrollHeight;}
"""


def _page_shell(title: str, body: str, extra_js: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - DeverAI Sync Server</title>
<style>{_PAGE_CSS}</style></head>
<body>{body}<script>{_PAGE_JS}{extra_js}</script></body></html>"""


_STATUS_PAGE = _page_shell(
    "状态页",
    """
<h1>DeverAI 同步服务器</h1>
<div class="sub">快照存取 · 冷备 Agent 续聊 · 漂移状态 · 远程指挥控制台（服务器端只存密文与冷备，数据归用户所有）</div>

<div class="card">
  <h2>访问令牌</h2>
  <div class="row">
    <input type="text" id="tk" placeholder="SYNC_TOKEN（服务器未设置令牌时留空）">
    <button class="sec" onclick="saveTk()">保存</button>
    <button onclick="refreshAll()">刷新全部</button>
  </div>
  <div class="small" id="tk-note"></div>
</div>

<div class="card">
  <h2>漂移状态</h2>
  <div class="kv" id="drift-kv">加载中…</div>
</div>

<div class="card">
  <h2>冷备会话（云端续聊）</h2>
  <div class="msgs" id="msgs"><div class="small">加载中…</div></div>
  <div class="row">
    <input type="text" id="chat-in" placeholder="向冷备 Agent 发送消息，关机续算…" onkeydown="if(event.key==='Enter')sendChat()">
    <button onclick="sendChat()">发送</button>
    <button class="sec" onclick="loadHistory()">刷新</button>
  </div>
  <div class="small" id="chat-note"></div>
</div>

<div class="card">
  <h2>远程指挥控制台</h2>
  <table><thead><tr><th>节点</th><th>工作区</th><th>模式</th><th>注册时间</th><th>状态</th></tr></thead>
  <tbody id="nodes"><tr><td colspan="5" class="small">加载中…</td></tr></tbody></table>
  <div style="height:10px"></div>
  <div class="row">
    <select id="dc-node" style="max-width:220px"></select>
    <select id="dc-type" style="max-width:130px">
      <option value="shell">shell</option><option value="read_file">read_file</option>
      <option value="write_file">write_file</option><option value="ping">ping</option>
    </select>
    <button onclick="dispatch()">下发命令</button>
    <button class="sec" onclick="loadNodes()">刷新节点</button>
    <button class="sec" onclick="loadResults()">轮询结果</button>
  </div>
  <div class="row"><input type="text" id="dc-command" placeholder="shell 命令（本地端 danger_ok 协议拦截危险命令）"></div>
  <div class="row"><input type="text" id="dc-path" placeholder="read/write 的相对路径"></div>
  <div class="row"><textarea id="dc-content" placeholder="write_file 内容"></textarea></div>
  <div class="row"><label><input type="checkbox" id="dc-danger"> 危险命令显式确认（danger_ok）</label></div>
  <div id="dc-out" class="out" style="margin-top:8px">命令结果将显示在这里（TTL 10 分钟）。</div>
</div>
""",
    """
function saveTk(){localStorage.setItem('sync_token',$('tk').value.trim());
  $('tk-note').textContent='[OK] 令牌已保存到浏览器 localStorage';refreshAll();}
async function loadDrift(){try{const d=await api('/drift/status');
  const cls=d.unacked_count>0?'tag-warn':'tag-ok';
  $('drift-kv').innerHTML='project_id=<b>'+esc(d.project_id||'-')+'</b> · active=<b>'+esc(d.active)+'</b> · '
   +'未回传云端产出=<b class="'+cls+'">'+esc(d.unacked_count)+'</b> 条 · updated=<b>'+esc(d.updated_at||'-')+'</b>';
  }catch(e){$('drift-kv').innerHTML='<span class="tag-err">[X] '+esc(e.message)+'</span>';}}
async function loadHistory(){try{const d=await api('/chat/history');
  if(!d.ok){$('msgs').innerHTML='<div class="small">暂无冷备副本（客户端推送含 cold 的快照后出现）。</div>';return;}
  renderMsgs($('msgs'),d.history||[]);
  $('chat-note').textContent=d.model?('模型: '+d.model):'';}catch(e){
  $('msgs').innerHTML='<div class="small tag-err">[X] '+esc(e.message)+'</div>';}}
async function sendChat(){const v=$('chat-in').value.trim();if(!v)return;
  $('chat-in').value='';$('chat-note').textContent='发送中…';
  try{const d=await api('/chat',{method:'POST',body:JSON.stringify({message:v})});
    $('chat-note').textContent='[OK] 已续聊';
    await loadHistory();}catch(e){$('chat-note').textContent='[X] '+esc(e.message);}}
async function loadNodes(){try{const d=await api('/cmd/nodes');
  const tb=$('nodes');tb.innerHTML='';
  if(!d.nodes||!d.nodes.length){tb.innerHTML='<tr><td colspan="5" class="small">暂无注册节点（桌面端开启 ENABLE_REMOTE_CMD 后出现）。</td></tr>';}
  else d.nodes.forEach(n=>{const tr=document.createElement('tr');
    tr.innerHTML='<td><code>'+esc(n.node_id)+'</code></td><td class="small">'+esc(n.workspace||'-')
    +'</td><td class="small">'+esc(n.agent_mode)+'</td><td class="small">'+esc(n.registered_at)
    +'</td><td class="'+(n.alive?'tag-ok':'tag-err')+'">'+(n.alive?'[OK] 在线':'[X] 离线')+'</td>';
    tb.appendChild(tr);});
  const sel=$('dc-node');const keep=sel.value;sel.innerHTML='';
  (d.nodes||[]).forEach(n=>{const o=document.createElement('option');
    o.value=n.node_id;o.textContent=n.name+' ('+n.node_id+')';sel.appendChild(o);});
  if(keep)sel.value=keep;}catch(e){$('nodes').innerHTML='<tr><td colspan="5" class="small tag-err">[X] '+esc(e.message)+'</td></tr>';}}
async function dispatch(){const nid=$('dc-node').value;if(!nid){alert('请先选择节点');return;}
  const type=$('dc-type').value;const payload={};
  if(type==='shell')payload.command=$('dc-command').value;
  if(type==='read_file'||type==='write_file')payload.path=$('dc-path').value;
  if(type==='write_file')payload.content=$('dc-content').value;
  if(type==='shell')payload.danger_ok=$('dc-danger').checked;
  try{const d=await api('/cmd/dispatch',{method:'POST',body:JSON.stringify({node_id:nid,type:type,payload:payload})});
    $('dc-out').textContent='[OK] 已下发 cmd_id='+d.cmd_id+'，等待本地端回传结果…';loadResults();}
  catch(e){$('dc-out').textContent='[X] '+e.message;}}
async function loadResults(){const nid=$('dc-node').value;if(!nid)return;
  try{const d=await api('/cmd/results/'+nid);const out=$('dc-out');
    if(!d.results||!d.results.length){out.textContent='暂无结果（下发命令后本地端长轮询执行并回传）。';return;}
    out.textContent=d.results.map(r=>'['+r.ts+'] cmd='+r.cmd_id+(r.ok!==undefined?(' '+(r.ok?'[OK]':'[X]')):'')
      +'\\n'+(r.output||'')).join('\\n----\\n');}catch(e){$('dc-out').textContent='[X] '+e.message;}}
function refreshAll(){loadDrift();loadHistory();loadNodes();}
window.addEventListener('load',()=>{$('tk').value=token();refreshAll();setInterval(loadNodes,30000);});
""",
)

_CHAT_PAGE = _page_shell(
    "冷备续聊",
    """
<h1>冷备 Agent 续聊</h1>
<div class="sub">关机期间在服务器上继续任务；本地开机后经「漂移回本地」合并回主历史（from_server 去重）。</div>
<div class="card">
  <div class="msgs" id="msgs" style="max-height:60vh"><div class="small">加载中…</div></div>
  <div class="row">
    <input type="text" id="chat-in" placeholder="继续任务…" onkeydown="if(event.key==='Enter')sendChat()">
    <button onclick="sendChat()">发送</button>
    <button class="sec" onclick="loadHistory()">刷新</button>
  </div>
  <div class="small" id="chat-note"></div>
</div>
""",
    """
async function loadHistory(){try{const d=await api('/chat/history');
  if(!d.ok){$('msgs').innerHTML='<div class="small">暂无冷备副本。</div>';return;}
  renderMsgs($('msgs'),d.history||[]);}catch(e){
  $('msgs').innerHTML='<div class="small tag-err">[X] '+esc(e.message)+'</div>';}}
async function sendChat(){const v=$('chat-in').value.trim();if(!v)return;
  $('chat-in').value='';$('chat-note').textContent='发送中…';
  try{await api('/chat',{method:'POST',body:JSON.stringify({message:v})});
    $('chat-note').textContent='[OK] 已续聊';await loadHistory();}
  catch(e){$('chat-note').textContent='[X] '+esc(e.message);}}
window.addEventListener('load',loadHistory);
""",
)


@app.get("/", response_class=HTMLResponse)
async def index_page():
    """HTML 状态页：漂移状态 + 冷备会话续聊 + 远程指挥控制台（Design 特点1/5）。"""
    return HTMLResponse(_STATUS_PAGE)


@app.get("/chat/page", response_class=HTMLResponse)
async def chat_page():
    """冷备续聊独立页（手机/平板浏览器打开即可继续任务）。"""
    return HTMLResponse(_CHAT_PAGE)


@app.get("/health")
async def health():
    return {"ok": True, "service": "deverai-sync-server", "ts": _now()}


# ----------------------------- 入口 -----------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="DeverAI 同步服务器（部署在用户自有服务器）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SYNC_PORT", "8765")),
                        help="监听端口（默认 8765，可用 SYNC_PORT 覆盖）")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[DeverAI Sync Server] 监听 {args.host}:{args.port}")
    print(f"[DeverAI Sync Server] 数据目录: {DATA_DIR}")
    if SYNC_TOKEN:
        print("[DeverAI Sync Server] 令牌鉴权: 已启用（客户端配置 sync_token，请求头 X-Sync-Token）")
    else:
        print("[DeverAI Sync Server] 令牌鉴权: 未设置 SYNC_TOKEN —— 仅环回客户端可访问，远程部署必须设置")
    if DRIFT_API_BASE:
        print(f"[DeverAI Sync Server] 冷备 Agent LLM: {DRIFT_API_BASE}")
    else:
        print("[DeverAI Sync Server] 冷备 Agent LLM: 未配置 DRIFT_API_BASE（/chat 续聊不可用，快照存取不受影响）")
    print("[DeverAI Sync Server] HTML 状态页: http://<host>:<port>/ 与 /chat/page")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
