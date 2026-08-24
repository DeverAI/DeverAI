"""远程指挥协调模块 — 本地端（desktop/remote_cmd.py）。

功能：作为后台线程运行，长轮询 sync_server 获取远程指令，
在本机执行后回传结果。实现"部署端 → 本地"的远程指挥协同。

线程安全：单线程后台运行，停止信号用 threading.Event。
内存安全：无全局缓存累积，httpx client 每次请求后即关闭，
         命令结果队列有界（防止积压）。

集成方式（desktop/gui.py）：
    from .remote_cmd import RemoteCmdClient
    client = RemoteCmdClient(cfg)
    client.start()  # 启动后台线程
    client.stop()   # 停止（线程安全退出）
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import subprocess
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 心跳间隔（秒）
HEARTBEAT_INTERVAL = 30
# 长轮询超时（秒）—— 服务器端 hold 25s 后无命令返回空
POLL_TIMEOUT = 30
# 命令执行默认超时
CMD_DEFAULT_TIMEOUT = 120
# 结果队列上限（防止积压）
MAX_PENDING_RESULTS = 50
# 重连退避（秒）
RECONNECT_BACKOFF = [1, 2, 5, 10, 30, 60]


class RemoteCmdClient:
    """远程指挥客户端：长轮询 + 心跳 + 命令执行 + 结果回传。

    生命周期：
        start() → 后台线程运行 _run_loop → stop() 退出
    线程模型：
        主线程 start/stop，后台线程 _run_loop（同步 httpx），
        命令执行在后台线程内同步执行（不开新线程，防资源泄漏）。
    """

    def __init__(self, cfg):
        """cfg: desktop.config.Config 实例"""
        self._cfg = cfg
        self._stop_event = threading.Event()
        _thread: threading.Thread | None = None
        self._thread = None
        self._node_id: str = ""
        self._pending_results: list[dict] = []  # 待回传的结果队列
        self._lock = threading.Lock()
        self._backoff_idx = 0

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """启动后台线程（非阻塞）。"""
        # v8.13：旧线程 join 超时仍存活时拒绝重复启动（此前置空引用会双线程同时 poll）
        if self._thread and self._thread.is_alive():
            logger.warning("[remote_cmd] 旧线程仍在退出中，忽略重复 start")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="RemoteCmd", daemon=True)
        self._thread.start()
        logger.info("[remote_cmd] 后台线程已启动")

    def stop(self, timeout: float = 3.0) -> None:
        """停止后台线程（阻塞等待退出，有界超时）。"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        # v8.13：join 超时保留线程引用；下次 start 会被存活检查拒绝，避免双实例
        if self._thread and not self._thread.is_alive():
            self._thread = None
        logger.info("[remote_cmd] 后台线程停止请求已发出")

    def _run_loop(self) -> None:
        """主循环：注册 → 轮询命令 → 执行 → 回传结果 → 心跳。"""
        server_url = (self._cfg.sync_server_url or "").rstrip("/")
        # v8.13：只允许 sync_token 鉴权。sync_password 是快照加密口令，把它当令牌发送
        # 等于把口令明文交给服务器（服务器据此可解密主快照）。
        token = getattr(self._cfg, "sync_token", "") or ""
        if not server_url:
            logger.warning("[remote_cmd] 未配置 sync_server_url，远程指挥不启动")
            return

        # 注册
        while not self._stop_event.is_set():
            try:
                reg = self._register(server_url, token)
                if reg and reg.get("node_id"):
                    self._node_id = reg["node_id"]
                    self._backoff_idx = 0
                    logger.info(f"[remote_cmd] 注册成功 node_id={self._node_id}")
                    break
            except Exception as e:
                logger.warning(f"[remote_cmd] 注册失败: {e}")
            if self._stop_event.wait(self._backoff()):
                return

        # 主循环
        last_heartbeat = 0.0
        while not self._stop_event.is_set():
            try:
                # 回传待发送的结果
                self._flush_results(server_url, token)

                # 长轮询命令
                cmds = self._poll_commands(server_url, token)
                self._backoff_idx = 0

                for cmd in cmds:
                    self._execute_and_queue(cmd)

                # 心跳
                now = time.monotonic()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                    self._heartbeat(server_url, token)
                    last_heartbeat = now

            except Exception as e:
                logger.warning(f"[remote_cmd] 轮询异常: {e}")
                if self._stop_event.wait(self._backoff()):
                    break

        logger.info("[remote_cmd] 主循环退出")

    def _backoff(self) -> float:
        """指数退避，返回等待秒数。"""
        idx = min(self._backoff_idx, len(RECONNECT_BACKOFF) - 1)
        wait = RECONNECT_BACKOFF[idx]
        self._backoff_idx += 1
        return wait

    def _headers(self, token: str) -> dict:
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Sync-Token"] = token
        return h

    def _register(self, server_url: str, token: str) -> dict | None:
        """向服务器注册本节点。"""
        body = {
            "name": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "local",
            "workspace": getattr(self._cfg, "workspace", ""),
            "agent_mode": getattr(self._cfg, "agent_mode", "builder"),
        }
        with httpx.Client(timeout=10.0) as c:
            r = c.post(f"{server_url}/cmd/register", json=body, headers=self._headers(token))
            r.raise_for_status()
            return r.json()

    def _poll_commands(self, server_url: str, token: str) -> list[dict]:
        """长轮询获取待执行命令。"""
        if not self._node_id:
            return []
        with httpx.Client(timeout=POLL_TIMEOUT + 10) as c:
            r = c.get(
                f"{server_url}/cmd/poll/{self._node_id}",
                headers=self._headers(token),
                params={"wait": POLL_TIMEOUT},
            )
            r.raise_for_status()
            data = r.json()
            return data.get("commands") or []

    def _heartbeat(self, server_url: str, token: str) -> None:
        """发送心跳保活。"""
        if not self._node_id:
            return
        with httpx.Client(timeout=10.0) as c:
            r = c.post(
                f"{server_url}/cmd/heartbeat/{self._node_id}",
                json={"status": "alive"},
                headers=self._headers(token),
            )
            r.raise_for_status()

    def _flush_results(self, server_url: str, token: str) -> None:
        """回传待发送的命令结果。"""
        if not self._node_id or not self._pending_results:
            return
        with self._lock:
            batch = self._pending_results[:MAX_PENDING_RESULTS]
            self._pending_results = self._pending_results[len(batch):]
        if not batch:
            return
        try:
            with httpx.Client(timeout=15.0) as c:
                for result in batch:
                    try:
                        resp = c.post(
                            f"{server_url}/cmd/result/{self._node_id}/{result['cmd_id']}",
                            json=result,
                            headers=self._headers(token),
                        )
                        resp.raise_for_status()  # v8.5.x 审查修复：非 2xx 视为失败重新入队
                    except Exception as e:
                        logger.debug(f"[remote_cmd] 回传结果失败: {e}")
                        # 失败则放回队列尾部（如果队列未满）
                        with self._lock:
                            if len(self._pending_results) < MAX_PENDING_RESULTS:
                                self._pending_results.append(result)
        except Exception:
            pass

    def _execute_and_queue(self, cmd: dict) -> None:
        """执行单条远程命令并把结果加入待回传队列。

        cmd 格式：{ cmd_id, type, payload }
        type: "shell" | "read_file" | "write_file" | "ping"
        """
        cmd_id = cmd.get("cmd_id", "")
        cmd_type = cmd.get("type", "shell")
        payload = cmd.get("payload") or {}
        result = {"cmd_id": cmd_id, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}

        try:
            if cmd_type == "ping":
                result["ok"] = True
                result["output"] = "pong"
            elif cmd_type == "shell":
                result.update(self._exec_shell(payload))
            elif cmd_type == "read_file":
                result.update(self._exec_read_file(payload))
            elif cmd_type == "write_file":
                result.update(self._exec_write_file(payload))
            else:
                result["ok"] = False
                result["output"] = f"未知命令类型: {cmd_type}"
        except Exception as e:
            result["ok"] = False
            result["output"] = f"执行异常: {e}"

        with self._lock:
            if len(self._pending_results) < MAX_PENDING_RESULTS:
                self._pending_results.append(result)
            # 超限丢弃最旧的结果（防内存泄漏）

    def _exec_shell(self, payload: dict) -> dict:
        """执行 shell 命令（在工作区内，输出有界）。"""
        command = str(payload.get("command") or "").strip()
        if not command:
            return {"ok": False, "output": "命令为空"}
        cwd = getattr(self._cfg, "workspace", "") or "."
        try:
            timeout = float(payload.get("timeout") or CMD_DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = CMD_DEFAULT_TIMEOUT
        if timeout != timeout:
            timeout = CMD_DEFAULT_TIMEOUT
        timeout = min(max(timeout, 1), 600)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=cwd, creationflags=creationflags,
            )
        except Exception as e:
            return {"ok": False, "output": f"启动失败: {e}"}
        # v8.13：有界读取——读者线程只保留最近 2000 行，主线程限时等待，
        # 远程命令输出再大也不会撑爆内存（与 tools.py 流式语义一致）
        lines: list[str] = []

        def _drain():
            try:
                for line in proc.stdout:
                    lines.append(line.rstrip("\n"))
                    if len(lines) > 2000:
                        lines.pop(0)
            except Exception:
                pass

        reader = threading.Thread(target=_drain, daemon=True)
        reader.start()
        timed_out = False
        start_ts = time.monotonic()
        try:
            while True:
                if self._stop_event.is_set():
                    # stop() 触发：立即终止当前命令，避免 600s 命令拖着线程无法退出
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    rc = -1
                    timed_out = True
                    break
                rc_now = proc.poll()
                if rc_now is not None:
                    rc = rc_now
                    break
                if time.monotonic() - start_ts >= timeout:
                    timed_out = True
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        rc = proc.wait(timeout=10)
                    except Exception:
                        rc = -1
                    break
                time.sleep(0.1)
        except Exception as e:
            return {"ok": False, "output": f"执行失败: {e}"}
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            reader.join(timeout=1)
        output = "\n".join(lines)
        if len(output) > 20000:
            output = output[:20000] + "\n…(输出已截断)"
        if timed_out:
            return {"ok": False, "output": f"命令超时（{timeout}s）\n{output}", "rc": rc}
        return {"ok": rc == 0, "output": output, "rc": rc}

    def _exec_read_file(self, payload: dict) -> dict:
        """读取工作区内文件（与桌面 read_file 同源只读保护）。"""
        from pathlib import Path
        ws = Path(getattr(self._cfg, "workspace", ".")).resolve()
        rel = str(payload.get("path") or "").strip()
        p = (ws / rel).resolve()
        if p != ws and ws not in p.parents:
            return {"ok": False, "output": "路径越界"}
        try:
            from .tools import ToolContext, _is_read_protected
            if _is_read_protected(ToolContext(self._cfg), p):
                return {"ok": False, "output": "该文件属于受保护数据，禁止读取"}
        except Exception:
            pass  # 保护模块不可用：继续按纯越界校验（远程命令本身需 sync_token 鉴权）
        if not p.is_file():
            return {"ok": False, "output": "文件不存在"}
        if p.stat().st_size > 2 * 1024 * 1024:
            return {"ok": False, "output": "文件超过 2MB"}
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "output": content[:50000]}

    def _exec_write_file(self, payload: dict) -> dict:
        """写入工作区内文件（与桌面写工具同源只读/系统目录保护 + 原子写入）。"""
        from pathlib import Path
        ws = Path(getattr(self._cfg, "workspace", ".")).resolve()
        rel = str(payload.get("path") or "").strip()
        if not rel or rel in (".", "\\", "/"):
            return {"ok": False, "output": "路径不能为空或指向工作区根目录"}
        p = (ws / rel).resolve()
        if p != ws and ws not in p.parents:
            return {"ok": False, "output": "路径越界"}
        try:
            from . import session_snap as _snap
            if _snap.is_readonly():
                return {"ok": False, "output": "AI 只读模式已锁定，远程写文件被禁止"}
            from .tools import ToolContext, _is_protected
            if _is_protected(ToolContext(self._cfg), p):
                return {"ok": False, "output": "该文件属于系统受保护文件，禁止写入"}
        except Exception:
            pass
        if p.is_dir():
            return {"ok": False, "output": "路径指向目录"}
        content = str(payload.get("content") or "")
        if len(content.encode("utf-8")) > 5 * 1024 * 1024:
            return {"ok": False, "output": "内容超过 5MB"}
        p.parent.mkdir(parents=True, exist_ok=True)
        # v8.13：唯一临时文件 + os.replace（固定 .tmp 并发写同文件会互相覆盖）
        import uuid as _uuid
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{_uuid.uuid4().hex}.tmp")
        tmp.write_text(content, encoding="utf-8")
        try:
            os.replace(str(tmp), str(p))
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return {"ok": True, "output": f"已写入 {rel}"}
