"""租约锁与文件所有权（v5）：防僵尸锁（TTL+heartbeat+Reaper）、防活锁（timeout）、
防脏数据（CoW 原子替换）。纯内存表 + threading.Lock，零依赖、零 token。

设计要点（用户拍板）：
- acquire 必须带 TTL；专家每完成一个待办项自动 heartbeat 续约；
- Reaper 为纯数据结构扫描（GUI 10s QTimer），扫出僵尸锁才触发副驾驶事件；
- 申请锁带 timeout，超时抛 AcquireLockTimeout，专家标记"资源等待失败"入反馈表单，绝不阻塞；
- 锁卡 2 分钟 UI 可强制解锁；
- 文件所有权：专家唤醒时申报管理文件清单，清单外一律只读；
- 写文件强制 CoW：临时文件 + os.replace 原子替换。
"""
import asyncio
import os
import threading
import time
import uuid
from pathlib import Path


class AcquireLockTimeout(Exception):
    """申请资源锁超时（活锁保护）：调用方应标记"资源等待失败"，绝不重试死等。"""

    def __init__(self, resource: str, owner: str):
        self.resource = resource
        self.owner = owner
        super().__init__(f"资源等待失败：{owner} 申请锁 {resource} 超时")


class LockManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._table: dict = {}          # resource -> {owner, ttl, heartbeat_at, acquired_at}
        self._owners: dict = {}         # expert_id -> set(文件相对路径)

    # ---------------- 资源锁 ----------------
    async def acquire(self, resource: str, owner: str, ttl: float = 60.0,
                      timeout: float = 5.0) -> bool:
        """申请锁；可重入（同 owner 再申请 = 续约）。超时抛 AcquireLockTimeout。"""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._lock:
                e = self._table.get(resource)
                # 过期锁自愈：逻辑已过期但 Reaper 尚未扫描到时，允许新申请者直接接管，
                # 避免无 GUI 环境下过期锁永久阻塞后续申请。
                # 所有时效字段统一使用 time.monotonic()：心跳/过期判断/快照时长同基准，
                # 防系统时钟调整导致锁被误判过期（try_acquire 此前混用 time.time 会误放行）。
                if e is not None and e["owner"] != owner and e["heartbeat_at"] + e["ttl"] < time.monotonic():
                    e = None
                if e is None or e["owner"] == owner:
                    self._table[resource] = {"owner": owner, "ttl": ttl,
                                             "heartbeat_at": time.monotonic(),
                                             "acquired_at": e["acquired_at"] if e else time.monotonic()}
                    return True
            if time.monotonic() >= deadline:
                raise AcquireLockTimeout(resource, owner)
            await asyncio.sleep(0.1)

    def try_acquire(self, resource: str, owner: str, ttl: float = 60.0) -> bool:
        """非阻塞版：拿得到返回 True，否则 False（同步上下文用）。"""
        with self._lock:
            e = self._table.get(resource)
            if e is not None and e["owner"] != owner and e["heartbeat_at"] + e["ttl"] < time.monotonic():
                e = None  # 过期锁自愈（同步非阻塞版）
            if e is None or e["owner"] == owner:
                now = time.monotonic()
                self._table[resource] = {"owner": owner, "ttl": ttl,
                                         "heartbeat_at": now, "acquired_at": e["acquired_at"] if e else now}
                return True
            return False

    def heartbeat(self, resource: str, owner: str, ttl: float = None) -> bool:
        """续约：刷新 heartbeat_at 并重置 ttl；锁不属于该 owner 或已失效返回 False。"""
        with self._lock:
            e = self._table.get(resource)
            if not e or e["owner"] != owner:
                return False
            if ttl is not None:
                e["ttl"] = ttl
            e["heartbeat_at"] = time.monotonic()
            return True

    def release(self, resource: str, owner: str = None) -> bool:
        """释放锁；owner 为 None 时不校验归属（总司令/系统用）。"""
        with self._lock:
            e = self._table.get(resource)
            if not e:
                return False
            if owner is not None and e["owner"] != owner:
                return False
            del self._table[resource]
            return True

    def force_release(self, resource: str) -> dict:
        """UI 强制解锁（锁卡 2 分钟场景）：物理删除并返回被删条目（供系统提示）。"""
        with self._lock:
            e = self._table.pop(resource, None)
        return e or {}

    def reap(self) -> list:
        """Reaper（刽子手）：纯扫描清理过期锁，返回 [(resource, 条目)]。零 LLM。"""
        now = time.monotonic()
        dead = []
        with self._lock:
            for res in list(self._table):
                e = self._table[res]
                if e["heartbeat_at"] + e["ttl"] < now:
                    del self._table[res]
                    dead.append((res, e))
        return dead

    def snapshot(self) -> list:
        """锁表快照（UI 锁状态区用）：含已持锁时长，供 2 分钟强制解锁判断。"""
        now = time.monotonic()
        with self._lock:
            return [{
                "resource": res, "owner": e["owner"],
                "ttl": e["ttl"], "age_s": round(now - e["acquired_at"], 1),
                "expires_in_s": round(e["heartbeat_at"] + e["ttl"] - now, 1),
            } for res, e in sorted(self._table.items())]

    # ---------------- 文件所有权 ----------------
    def declare_files(self, expert_id: str, files: list) -> list:
        """专家唤醒时申报管理文件清单（相对工作区路径）。

        返回被拒绝的文件列表：这些文件已被其它专家申报，本专家不会获得其写权，
        从而从锁层面杜绝「两个专家并发写同一文件」导致的覆盖/丢更新（lost update）。

        v8.7：`*` 通配申报（总司令放开写权，过副驾驶后）记录放行，但与其它专家现有
        申报的重叠以 ["*"] 信息性返回——总司令授权语义高于冲突，不阻断授权本身。
        """
        conflicts = []
        if isinstance(files, str):
            files = [files]
        elif not isinstance(files, (list, tuple, set)):
            files = []
        with self._lock:
            cur = self._owners.setdefault(expert_id, set())
            normed = [_norm(str(f)) for f in files if f is not None]
            if "*" in normed:
                cur.add("*")
                others = [eid for eid, s in self._owners.items()
                          if eid != expert_id and s and s != {"*"}]
                return ["*"] if others else []
            for norm in normed:
                if norm in cur:
                    continue
                # v8.7 审查修复：其它专家持有 "*"（总司令放开全部写权）时也视为冲突，
                # 否则「具体申报 vs 通配放开」两名专家可同时写同一文件
                other = next((eid for eid, s in self._owners.items()
                              if eid != expert_id and ("*" in s or norm in s)), None)
                if other is not None:
                    conflicts.append(norm)
                    continue
                cur.add(norm)
        return conflicts

    def can_write(self, expert_id: str, rel_path: str) -> bool:
        """写权校验：总司令（'__commander__'）永远可写；'*' = 总司令临时放开；其余仅限申报清单。"""
        if expert_id == COMMANDER_ID:
            return True
        with self._lock:
            owned = self._owners.get(expert_id, set())
            if "*" in owned:
                return True
            return _norm(rel_path) in owned

    def owned_files(self, expert_id: str) -> set:
        with self._lock:
            return set(self._owners.get(expert_id, set()))

    def owner_of(self, rel_path: str):
        """v8.3 缺口5：反向查询某个相对路径的归属专家（用于 tree 问题定位）。

        "*" 通配（临时写权）为最低优先级：先精确匹配，找不到再回退通配。"""
        norm = _norm(rel_path)
        star_owner = None
        with self._lock:
            for eid, files in self._owners.items():
                if norm in files:
                    return eid
                if "*" in files and star_owner is None:
                    star_owner = eid
            return star_owner

    def release_expert(self, expert_id: str) -> None:
        """专家退出：清所有权 + 释放其全部资源锁。"""
        with self._lock:
            self._owners.pop(expert_id, None)
            for res in [r for r, e in self._table.items() if e["owner"] == expert_id]:
                del self._table[res]


COMMANDER_ID = "__commander__"

_global: LockManager = None
_glock = threading.Lock()


def get_locks() -> LockManager:
    global _global
    with _glock:
        if _global is None:
            _global = LockManager()
        return _global


def reset_locks() -> None:
    """测试用：重置全局锁表。"""
    global _global
    with _glock:
        _global = LockManager()


def _norm(p: str) -> str:
    """路径规范化：反斜杠转斜杠、折叠 `./`、内部 `..` 逐段消解（前导 .. 保留，逃逸路径与正常路径不同键）。

    v8.7 审查修复：`a/../b` 与 `b` 规范化后相同，杜绝「同一物理文件两种写法
    各自申报」绕过写权冲突检测（lost update）。
    """
    s = str(p).replace("\\", "/").strip("/")
    parts = []
    for seg in s.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append("..")  # 前导越界 .. 保留（resolve 层会拦）
        else:
            parts.append(seg)
    return "/".join(parts)


# ---------------------------------------------------------------- CoW 原子写

def atomic_write(path, text: str, encoding: str = "utf-8") -> None:
    """写时复制：写同目录唯一命名 .tmp 再 os.replace 原子替换。专家唯一合法写路径。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 唯一 tmp 名：并发写同一文件互不覆盖对方的临时文件
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(text, encoding=encoding, newline="")
        os.replace(tmp, p)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
