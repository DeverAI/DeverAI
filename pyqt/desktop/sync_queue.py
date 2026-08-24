"""信息同步队列（v4）：管理"本地变更 → 用户自有服务器"的快照同步。

规则：
- 设置保存 / 会话落盘 / 资产变更 → mark_dirty(reason) 入队。
- 流量模式下挂起不同步（ Design.md 决策 19）。
- 关闭流量模式时立即 flush()。
- 未配置同步服务器或未启用 ENABLE_SYNC → 跳过（不算失败）。
- flush 在后台线程执行（httpx 同步包装），信号通知结果，绝不阻塞 UI。
"""
import asyncio
import threading
from typing import Callable, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from . import sync as sync_mod
from .config import get_config


class SyncQueue(QObject):
    """待同步原因集合 + 后台推送。线程安全（GIL 下 set 操作原子性足够）。"""

    status_changed = pyqtSignal(str)   # 状态栏文案
    flushed = pyqtSignal(bool, str)    # (成功?, 消息)

    def __init__(self, snapshot_provider: Optional[Callable[[], dict]] = None, parent=None):
        super().__init__(parent)
        self._pending: set = set()
        self._lock = threading.Lock()
        self._running = False
        # snapshot_provider: 无参可调用，返回 build_snapshot 的 payload
        self._snapshot_provider = snapshot_provider

    # ------------------------------------------------------------------
    def mark_dirty(self, reason: str):
        with self._lock:
            self._pending.add(str(reason or "change"))
            n = len(self._pending)
        self.status_changed.emit(f"同步队列 {n}" if n else "同步队列 空闲")

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def pending_reasons(self) -> List[str]:
        with self._lock:
            return sorted(self._pending)

    def clear(self):
        with self._lock:
            self._pending.clear()
        self.status_changed.emit("同步队列 空闲")

    # ------------------------------------------------------------------
    def flush(self, force: bool = False) -> bool:
        """冲刷队列。返回是否真正发起了同步。

        force=True 用于"关闭流量模式时立即同步"（仍要求服务器已配置）。
        """
        cfg = get_config()
        with self._lock:
            n = len(self._pending)
        if n == 0:
            self.status_changed.emit("同步队列 空闲")
            return False
        if not cfg.ENABLE_SYNC or not cfg.sync_server_url:
            self.status_changed.emit(f"同步队列 {n}（未配置服务器）")
            return False
        if cfg.traffic_mode and not force:
            self.status_changed.emit(f"同步队列 {n}（流量模式挂起）")
            return False
        with self._lock:
            if self._running:
                self.status_changed.emit(f"同步队列 {n}（同步中…）")
                return False
            self._running = True
        if self._snapshot_provider is None:
            with self._lock:
                self._running = False
            self.status_changed.emit(f"同步队列 {n}（无快照源）")
            return False
        self.status_changed.emit(f"同步中…（{n} 项待同步）")
        t = threading.Thread(target=self._push, daemon=True)
        t.start()
        return True

    def _push(self):
        ok = False
        message = ""
        with self._lock:
            synced = set(self._pending)
        try:
            payload = self._snapshot_provider()
            cfg = get_config()
            res = asyncio.run(asyncio.wait_for(sync_mod.push_to_server(cfg, payload, include_cold=True), timeout=60.0))
            ok = bool(res.get("ok"))
            message = str(res.get("message", ""))
        except Exception as e:
            ok = False
            message = f"同步异常: {e}"
        finally:
            with self._lock:
                if ok:
                    # 只移除本次已同步的条目，避免清掉同步期间新产生的脏项（数据丢失）
                    self._pending.difference_update(synced)
                n = len(self._pending)
            self._running = False
            self.status_changed.emit(f"同步队列 {n}" if n else "同步队列 空闲")
            self.flushed.emit(ok, message)
