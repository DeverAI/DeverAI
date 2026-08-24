"""用户存储：PBKDF2-SHA256 密码哈希，data/users.json。

v6.2 扩展：
- 邮箱注册：email 字段 + 验证码确认邮箱所有权。
- 验证码：内存 TTL（10 分钟过期），不落盘。
- 安全：密码哈希 PBKDF2-SHA256（200k 迭代）；邮箱唯一约束。

不使用 AES-256-GCM 存储密码——PBKDF2 是密码哈希的正确做法；
AES-256-GCM 用于快照加密（desktop/sync.py 已实现），不用于密码。
"""
import datetime
import hashlib
import re
import secrets
import threading
import time

from .config import DATA_DIR
from .storage import load_json, save_json

USERS_PATH = DATA_DIR / "users.json"
_ITERATIONS = 200_000
_CODE_TTL = 600  # 验证码 10 分钟过期
_MAX_PENDING = 1000  # _pending_codes 容量上限（防内存 DoS）

# 邮箱格式（简单校验，不引入大包）
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# 验证码内存存储：{email: (code, expire_ts)}
_pending_codes: dict[str, tuple[str, float]] = {}
_codes_lock = threading.Lock()
# 用户存储读-改-写锁（防 TOCTOU 竞态丢用户）
_users_lock = threading.Lock()


def _hash(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS)
    return dk.hex()


def _load_users() -> list:
    data = load_json(USERS_PATH, [])
    if isinstance(data, list):
        # v8.13：顶层是 list 但元素畸形（{} / null）会令 /api/users、注册 500——过滤掉
        return [u for u in data if isinstance(u, dict) and isinstance(u.get("username"), str)]
    # v8.13：users.json 顶层类型非法时回退空列表（合法 JSON 数组/null 不再令各端点 500）
    return []


def _save_users(users: list) -> None:
    save_json(USERS_PATH, users)


def is_valid_email(email: str) -> bool:
    """公开的邮箱格式校验（供 server.py send_code 使用）。"""
    return bool(_EMAIL_RE.match(email or ""))


# ------------------------------------------------------------------
# 验证码管理（内存 TTL，不落盘）
# ------------------------------------------------------------------
def _gc_pending() -> None:
    """清理过期验证码，防止 dict 无上限增长。"""
    now = time.time()
    expired = [k for k, (_, exp) in _pending_codes.items() if now > exp]
    for k in expired:
        _pending_codes.pop(k, None)


def store_code(email: str, code: str) -> None:
    """存验证码（覆盖旧码），10 分钟过期。"""
    with _codes_lock:
        _gc_pending()
        if len(_pending_codes) >= _MAX_PENDING:
            # 超上限时驱逐最早过期的一条（近似 LRU），而非清空全部
            #（P2-12：一次洪峰清空所有用户待用验证码属误伤）
            oldest = min(_pending_codes, key=lambda k: _pending_codes[k][1])
            _pending_codes.pop(oldest, None)
        _pending_codes[email.lower()] = (code, time.time() + _CODE_TTL)


def verify_code(email: str, code: str) -> bool:
    """校验验证码：匹配且未过期。成功后删除（一次性）。

    v6.2 安全：错误时记录失败次数，达 5 次删除 code 强制重发（防暴力破解）。
    """
    from . import security
    with _codes_lock:
        entry = _pending_codes.get(email.lower())
        if entry is None:
            return False
        stored, expire = entry
        if time.time() > expire:
            _pending_codes.pop(email.lower(), None)
            return False
        if not secrets.compare_digest(stored, code):
            # 失败：记录计数，达上限删除 code
            should_invalidate = security.record_code_fail(email)
            if should_invalidate:
                _pending_codes.pop(email.lower(), None)
                security.clear_code_fail(email)
            return False
        _pending_codes.pop(email.lower(), None)
    security.clear_code_fail(email)
    return True


def clear_code(email: str) -> None:
    """主动清除验证码（发送失败时调用）。"""
    with _codes_lock:
        _pending_codes.pop(email.lower(), None)


def has_pending_code(email: str) -> bool:
    """是否有待验证的码（未过期）。"""
    with _codes_lock:
        entry = _pending_codes.get(email.lower())
        if entry is None:
            return False
        if time.time() > entry[1]:
            _pending_codes.pop(email.lower(), None)
            return False
        return True


# ------------------------------------------------------------------
# 用户 CRUD
# ------------------------------------------------------------------
def create_user(username: str, password: str, email: str = "") -> dict:
    username = (username or "").strip()
    email = (email or "").strip().lower()
    if not username or not password:
        raise ValueError("用户名和密码不能为空")
    if len(username) > 32 or len(password) < 4:
        raise ValueError("用户名最长 32 字符，密码至少 4 位")
    if len(password) > 128:
        raise ValueError("密码最长 128 字符")
    if email and not is_valid_email(email):
        raise ValueError("邮箱格式不正确")
    with _users_lock:
        users = _load_users()
        if any(u["username"] == username for u in users):
            raise ValueError("用户名已存在")
        if email and any(u.get("email", "").lower() == email for u in users):
            raise ValueError("该邮箱已注册")
        salt = secrets.token_hex(16)  # 128-bit salt（NIST SP 800-132）
        user = {
            "username": username,
            "email": email,
            "salt": salt,
            "hash": _hash(password, salt),
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        users.append(user)
        _save_users(users)
        return user


def create_user_with_email(email: str, password: str, username: str = "") -> dict:
    """邮箱注册：邮箱为主标识，用户名可选（空则用邮箱前缀）。"""
    email = (email or "").strip().lower()
    if not email or not is_valid_email(email):
        raise ValueError("邮箱格式不正确")
    if len(password) < 4:
        raise ValueError("密码至少 4 位")
    if len(password) > 128:
        raise ValueError("密码最长 128 字符")
    username = (username or "").strip()
    if len(username) > 32:
        raise ValueError("用户名最长 32 字符")
    if not username:
        username = email.split("@")[0][:32]
    with _users_lock:
        users = _load_users()
        if any(u.get("email", "").lower() == email for u in users):
            raise ValueError("该邮箱已注册")
        if any(u["username"] == username for u in users):
            # 同名则加随机后缀；截断保证总长仍 ≤32
            # v8.14：加后缀后循环复查，防极小概率后缀仍撞名产生重复账号
            for _ in range(8):
                suffix = secrets.token_hex(2)
                candidate = f"{username[:max(0, 32 - len(suffix) - 1)]}_{suffix}"
                if not any(u["username"] == candidate for u in users):
                    username = candidate
                    break
            else:
                raise ValueError("用户名生成失败，请重试")
        salt = secrets.token_hex(16)  # 128-bit salt
        user = {
            "username": username,
            "email": email,
            "salt": salt,
            "hash": _hash(password, salt),
            "email_verified": True,  # 邮箱注册必须验证码确认，故为 True
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        users.append(user)
        _save_users(users)
        return user


def verify_user(username: str, password: str) -> dict | None:
    for u in _load_users():
        if u["username"] == username or u.get("email", "").lower() == username.lower():
            stored_hash = u.get("hash", "")
            salt = u.get("salt", "")
            if not stored_hash or not salt:
                return None
            if secrets.compare_digest(stored_hash, _hash(password, salt)):
                return u
            return None
    return None


def get_user(username: str) -> dict | None:
    for u in _load_users():
        if u["username"] == username:
            return u
    return None


def get_user_by_email(email: str) -> dict | None:
    email = (email or "").strip().lower()
    for u in _load_users():
        if u.get("email", "").lower() == email:
            return u
    return None


def user_count() -> int:
    return len(_load_users())


def delete_user(username: str) -> bool:
    """删除指定用户名（测试/运维用；不提供匿名 API 端点）。"""
    name = (username or "").strip()
    if not name:
        return False
    with _users_lock:
        users = _load_users()
        kept = [u for u in users if u["username"] != name]
        if len(kept) == len(users):
            return False
        _save_users(kept)
        return True


def delete_users_by_prefix(prefix: str) -> int:
    """按用户名前缀清理（测试账号用，如 audit/snap85/meta85/v813）。"""
    p = str(prefix or "")
    if not p:
        return 0
    with _users_lock:
        users = _load_users()
        kept = [u for u in users if not str(u.get("username", "")).startswith(p)]
        n = len(users) - len(kept)
        if n:
            _save_users(kept)
        return n


def _mask_email(email: str) -> str:
    """邮箱打码：ab***@example.com。

    无管理员角色体系下，防任意登录用户经 /api/users 枚举全量邮箱（PII）。
    """
    e = (email or "").strip()
    if not e or "@" not in e:
        return e or "-"
    local, _, domain = e.partition("@")
    head = local[:2] if len(local) > 2 else (local[:1] or "*")
    return f"{head}***@{domain}"


def list_users_public() -> list:
    """公开信息（不含 hash/salt，邮箱打码），供账户查看页使用。"""
    return [
        {
            "username": u["username"],
            "email": _mask_email(u.get("email", "")),
            "email_verified": u.get("email_verified", False),
            "created_at": u.get("created_at", ""),
        }
        for u in _load_users()
    ]
