"""DeverAI v8.16 多会话标签：会话索引与历史持久化（轻量自包含）。

数据形态：
- 索引 ``data/chat_sessions.json``：
  ``{"version": 1, "active_id": "default", "sessions": [{id,name,created_at,updated_at}]}``
- 历史 ``data/chat_history/{id}.json``：list[dict]，与旧 ``data/desktop_history.json`` 同构。
- 首启迁移：索引不存在且旧 desktop_history.json 存在 → 全部迁入 id=default 会话；
  原文件保留不动（回退到旧版本仍然可读，安全方向）。

开关纪律：``cfg.ENABLE_MULTI_SESSION=False`` 时 GUI 不实例化本模块，完全回到单会话行为。
并发说明：仅 GUI 线程读写本模块，无锁；save/load 复用 storage 原子写。
"""
from __future__ import annotations

import datetime
import uuid
from pathlib import Path

from .storage import load_json, save_json

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INDEX_PATH = DATA_DIR / "chat_sessions.json"
HISTORY_DIR = DATA_DIR / "chat_history"
LEGACY_HISTORY_PATH = DATA_DIR / "desktop_history.json"

DEFAULT_SESSION_ID = "default"


def _now_text() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


class SessionStore:
    """会话索引 + 分会话历史的内存态与磁盘存取。"""

    def __init__(self):
        self._index: dict = {"version": 1, "active_id": DEFAULT_SESSION_ID, "sessions": []}
        self._loaded = False

    # ---------- 索引 ----------
    def ensure(self) -> None:
        """加载索引；首次运行时迁移旧单会话历史并保证至少存在一个会话。"""
        if self._loaded:
            return
        data = load_json(INDEX_PATH, None)
        if isinstance(data, dict) and isinstance(data.get("sessions"), list) and data["sessions"]:
            sessions = []
            for s in data["sessions"]:
                if not isinstance(s, dict) or not s.get("id"):
                    continue
                sessions.append({
                    "id": str(s["id"]),
                    "name": str(s.get("name") or "会话")[:60],
                    "created_at": str(s.get("created_at") or _now_text()),
                    "updated_at": str(s.get("updated_at") or _now_text()),
                })
            if sessions:
                active = str(data.get("active_id") or "")
                self._index = {
                    "version": 1,
                    "active_id": active if any(s["id"] == active for s in sessions) else sessions[0]["id"],
                    "sessions": sessions,
                }
                self._loaded = True
                return
        # 首启/损坏：尝试从旧单会话文件迁移
        legacy: list = []
        try:
            raw = load_json(LEGACY_HISTORY_PATH, [])
            if isinstance(raw, list):
                legacy = [m for m in raw if isinstance(m, dict) and m.get("role")]
        except Exception:
            legacy = []
        ts = _now_text()
        first = {"id": DEFAULT_SESSION_ID,
                 "name": "默认会话",
                 "created_at": ts, "updated_at": ts}
        self._index = {"version": 1, "active_id": DEFAULT_SESSION_ID, "sessions": [first]}
        self._loaded = True
        if legacy:
            # 只有确实存在旧数据才落历史文件，避免凭空生成空 JSON
            self.save_history(DEFAULT_SESSION_ID, legacy)

    def _save_index(self) -> None:
        save_json(INDEX_PATH, self._index)

    def sessions(self) -> list:
        self.ensure()
        return list(self._index["sessions"])

    @property
    def active_id(self) -> str:
        self.ensure()
        return self._index["active_id"]

    def find(self, sid: str):
        self.ensure()
        for s in self._index["sessions"]:
            if s["id"] == sid:
                return s
        return None

    def set_active(self, sid: str) -> None:
        self.ensure()
        if self.find(sid) is not None and sid != self._index["active_id"]:
            self._index["active_id"] = sid
            self._save_index()

    # ---------- 会话操作 ----------
    def create(self, name: str = "") -> dict:
        """新建会话并置为活跃。重名自动追加序号。"""
        self.ensure()
        base = (name or "").strip()[:60] or f"新会话 {len(self._index['sessions']) + 1}"
        taken = {s["name"] for s in self._index["sessions"]}
        final, n = base, 2
        while final in taken:
            final = f"{base} ({n})"
            n += 1
        ts = _now_text()
        # v8.16：毫秒时间戳 + 短随机尾，防同毫秒连建撞 id
        sid = f"s_{int(datetime.datetime.now().timestamp() * 1000)}_{uuid.uuid4().hex[:4]}"
        item = {"id": sid, "name": final, "created_at": ts, "updated_at": ts}
        self._index["sessions"].append(item)
        self._index["active_id"] = sid
        self._save_index()
        save_json(self.history_path(sid), [])   # 立即落一个空历史，切走时内容可回写
        return dict(item)

    def rename(self, sid: str, name: str) -> bool:
        self.ensure()
        s = self.find(sid)
        new_name = (name or "").strip()[:60]
        if s is None or not new_name:
            return False
        s["name"] = new_name
        self._save_index()
        return True

    def remove(self, sid: str) -> None:
        """移除会话条目（不删历史文件）。关闭最后一个会话被拒绝（GUI 层用清空替代）。

        若移除的是活跃会话，自动切换到相邻会话（优先右侧，其次左侧）。
        返回无值——调用方读取 .active_id 获得切换结果。
        """
        self.ensure()
        items = self._index["sessions"]
        if len(items) <= 1:
            return
        idx = next((i for i, s in enumerate(items) if s["id"] == sid), -1)
        if idx < 0:
            return
        removed_active = self._index["active_id"] == sid
        items.pop(idx)
        if removed_active:
            nxt = items[min(idx, len(items) - 1)]
            self._index["active_id"] = nxt["id"]
        self._save_index()

    def touch(self, sid: str) -> None:
        self.ensure()
        s = self.find(sid)
        if s is not None:
            s["updated_at"] = _now_text()
            self._save_index()

    def autotitle_if_blank(self, sid: str, first_text: str) -> bool:
        """「新会话」类空白标题且尚无历史时，用首条消息摘要自动命名。

        返回 True 表示发生了改名（GUI 需刷新标签文字）。
        """
        self.ensure()
        s = self.find(sid)
        if s is None or not (s["name"] == "新会话" or s["name"].startswith("新会话 ")):
            return False
        if self.load_history(sid):
            return False
        title = " ".join(str(first_text or "").split())
        if not title:
            return False
        s["name"] = title[:18] + ("…" if len(title) > 18 else "")
        self._save_index()
        return True

    # ---------- 历史 ----------
    def history_path(self, sid: str) -> Path:
        HISTORY_DIR.mkdir(exist_ok=True)
        # sid 由本模块生成的受限字符集构成，仍做防御性白名单过滤防路径注入
        safe = "".join(c for c in str(sid) if c.isalnum() or c in "-_") or DEFAULT_SESSION_ID
        return HISTORY_DIR / f"{safe}.json"

    def load_history(self, sid: str) -> list:
        data = load_json(self.history_path(sid), [])
        return data if isinstance(data, list) else []

    def save_history(self, sid: str, msgs: list) -> None:
        save_json(self.history_path(sid), msgs)
