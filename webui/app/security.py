"""安全增强（借鉴大厂实践）：登录限速 + 异常 IP 检测 + 审计日志。

设计原则（轻量化，零新依赖）：
- 限速：内存滑动窗口（每 IP/用户独立计数），进程级即可，无需 Redis。
- 审计：追加写 JSONL 到 data/audit.jsonl，不落盘敏感字段（密码哈希/Token）。
- IP 检测：简单规则（同 IP 短时高频失败即标记异常），不引入 GeoIP。

借鉴的大厂数据安全实践：
- 零信任：每次请求验证身份（auth.current_user 已实现）
- 最小权限：AI 只见代号/相对路径（codename 机制已实现）
- 数据最小化：审计日志只记必要字段
- 传输加密：SMTP SSL / HTTPS（部署层）
- 存储加密：密码 PBKDF2（users.py 已实现），快照 AES（desktop/sync.py 已实现）
"""
import datetime
import json
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from .config import DATA_DIR

_AUDIT_PATH = DATA_DIR / "audit.jsonl"
# P2-7：审计日志大小上限，超限轮转（旧日志保留一份 .old），防无限增长
_AUDIT_MAX_BYTES = 5 * 1024 * 1024
_lock = threading.Lock()

# 危险命令模式（与 desktop/tools.py DANGEROUS_PATTERNS 同源）：
# run_command 端口后端拦截用——命中任一模式且请求未带 danger_ok 显式确认时拒绝。
# v8.11 检修：--force 排除 --force-with-lease（合法安全命令）；补 rd /s（rmdir 别名）；
# taskkill 改顺序无关匹配（/f 与 /im|/pid 任意顺序）。
DANGEROUS_PATTERNS = [
    r"\brm\s+-[a-z]*[rf]", r"\bdel\s+/[sfqi]", r"\brmdir\s+/s", r"\brd\s+/s\b",
    r"\bformat\b", r"\bdiskpart\b",
    r"\bmkfs\b", r"\bdd\s+if=", r":\(\)\{", r"\breg\s+delete\b", r"\bshutdown\b", r"\breboot\b",
    r"\bpowershell\s+-enc", r"Invoke-Expression", r"\btaskkill\b(?=.*\s/f(?=\s|$))(?=.*\s/(?:im|pid)\b)",
    r">\s*/dev/", r"\bgit\s+push\s+.*--force(?!-with-lease)",
    r"\bdrop\s+(table|database)", r"\btruncate\s+table",
    # v8.13：执行代码/脚本的等价危险形式（黑名单之外的实际执行路径）
    r"\bpython(?:3)?\s+-c\b", r"\bpy\s+-c\b",
    r"\bpowershell\s+(?:-[a-z]+\s+)*-(?:command|enc)\b", r"\bcmd(?:\.exe)?\s+/[cq]\b",
    r"\bRemove-Item\b.*-Recurse\b", r"\bshutil\.rmtree\b", r"\bos\.remove\b",
    # v8.15 检修：PowerShell 短参数/别名递归删除（Remove-Item -r、ri -Recurse 等）
    # 此前只匹配全称 -Recurse，danger 模式下可免审批静默递归删除
    r"\b(?:Remove-Item|ri|rm|del|erase|rmdir|rd)\b[^|\n;&]*\s-(?:recurse|r|rf|fr)\b",
    # git clean 带 -f 才真正删文件（-fdx 清空全部未跟踪文件）
    r"\bgit\s+clean\b[^|\n;&]*\s-[a-z]*f",
]


def is_dangerous_cmd(command: str) -> bool:
    """危险命令检测（命令桥后端拦截用）。"""
    cmd = str(command or "")
    for pat in DANGEROUS_PATTERNS:
        try:
            # v8.13：DOTALL——与桌面/前端一致，防换行拆分绕过
            if re.search(pat, cmd, re.IGNORECASE | re.DOTALL):
                return True
        except re.error:
            continue
    return False

# 限速：{key: deque[timestamps]}
_login_attempts: dict[str, deque] = defaultdict(deque)
_code_sent: dict[str, deque] = defaultdict(deque)
# 验证码尝试计数（防暴力破解）：{email: [fail_count, expire_ts]}
_code_fails: dict[str, list] = {}

# 登录限速：5 分钟内最多 10 次尝试
_LOGIN_WINDOW = 300
_LOGIN_MAX = 10
# 验证码发送限速：同邮箱 1 分钟最多 1 次，10 分钟最多 3 次
_CODE_WINDOW = 600
_CODE_MAX = 3
_CODE_MIN_GAP = 60
# 验证码发送 IP 限速：同 IP 1 小时最多 10 次（防邮件轰炸）
_SEND_IP_WINDOW = 3600
_SEND_IP_MAX = 10
# 验证码错误尝试上限（达此值删除 code 强制重发）
_CODE_FAIL_MAX = 5
# 电源操作限速：1 小时最多 1 次（防误触/滥用）
_POWER_WINDOW = 3600
_POWER_MAX = 1
# 限速 dict 容量上限（防内存 DoS）
_MAX_KEYS = 10000


def _now() -> float:
    return time.time()


def _prune(dq: deque, window: float) -> int:
    """裁剪滑动窗口外的旧记录，返回窗口内剩余计数。"""
    cutoff = _now() - window
    while dq and dq[0] < cutoff:
        dq.popleft()
    return len(dq)


def _gc_dict(d: dict, default_window: float | None = None) -> None:
    """清理空/过期 deque 条目，防止 dict 无上限增长（内存 DoS 防护）。"""
    if len(d) < _MAX_KEYS:
        return
    empty = []
    for k, v in d.items():
        if isinstance(v, deque):
            # _login_attempts 中带前缀的键（fail:/send:/power:）按各自窗口裁剪；
            # 普通 login:{ip}/reg:{ip} 键按登录窗口裁剪（v8.13：此前普通键从不裁剪，
            # 满 10000 后 stale 键永久驻留）。
            if k.startswith("fail:"):
                _prune(v, _LOGIN_WINDOW)
            elif k.startswith("send:"):
                _prune(v, _SEND_IP_WINDOW)
            elif k.startswith("power:"):
                _prune(v, _POWER_WINDOW)
            else:
                _prune(v, default_window if default_window is not None else _LOGIN_WINDOW)
            if len(v) == 0:
                empty.append(k)
    for k in empty:
        del d[k]


def check_login_rate(key: str) -> bool:
    """检查登录/注册限速。key 通常是 IP 或用户名。返回 True=允许，False=超限。"""
    with _lock:
        _gc_dict(_login_attempts)
        cnt = _prune(_login_attempts[key], _LOGIN_WINDOW)
        if cnt >= _LOGIN_MAX:
            return False
        _login_attempts[key].append(_now())
        return True


def check_send_ip_rate(ip: str) -> bool:
    """验证码发送的 IP 维度限速（防邮件轰炸/SMTP 配额耗尽）。"""
    with _lock:
        _gc_dict(_login_attempts)
        cnt = _prune(_login_attempts[f"send:{ip}"], _SEND_IP_WINDOW)
        if cnt >= _SEND_IP_MAX:
            return False
        _login_attempts[f"send:{ip}"].append(_now())
        return True


def check_code_rate(email: str) -> tuple[bool, str]:
    """检查验证码发送限速。返回 (允许, 原因)。"""
    with _lock:
        _gc_dict(_code_sent, default_window=_CODE_WINDOW)
        dq = _code_sent[email]
        cnt = _prune(dq, _CODE_WINDOW)
        if cnt >= _CODE_MAX:
            return False, "请求过于频繁，请稍后再试"
        # 最小间隔检查
        if dq and (_now() - dq[-1]) < _CODE_MIN_GAP:
            return False, "请稍候再请求验证码"
        dq.append(_now())
        return True, ""


def record_code_fail(email: str) -> bool:
    """记录验证码校验失败。返回 True=已达上限应删除 code 强制重发。"""
    with _lock:
        key = email.lower()
        # v6.2 P2-1：GC 过期条目，防 _code_fails 无上限增长
        if len(_code_fails) > _MAX_KEYS:
            expired = [k for k, v in _code_fails.items() if _now() > v[1]]
            for k in expired:
                del _code_fails[k]
        entry = _code_fails.get(key)
        if entry is None:
            _code_fails[key] = [1, _now() + _CODE_WINDOW]
            return False
        if _now() > entry[1]:
            _code_fails[key] = [1, _now() + _CODE_WINDOW]
            return False
        entry[0] += 1
        return entry[0] >= _CODE_FAIL_MAX


def check_power_rate(user: str) -> bool:
    """电源操作限速：每用户 1 小时最多 1 次。返回 True=允许，False=超限。"""
    with _lock:
        _gc_dict(_login_attempts)
        cnt = _prune(_login_attempts[f"power:{user}"], _POWER_WINDOW)
        if cnt >= _POWER_MAX:
            return False
        _login_attempts[f"power:{user}"].append(_now())
        return True


def clear_code_fail(email: str) -> None:
    """验证码校验成功后清理失败计数。"""
    with _lock:
        _code_fails.pop(email.lower(), None)


def audit(action: str, user: str = "", ip: str = "", detail: str = "") -> None:
    """写审计日志（追加 JSONL）。不记录密码/Token 等敏感字段。"""
    entry = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "user": user[:32],
        "ip": (ip or "")[:45],  # IPv6 最长 45 字符
        "detail": detail[:200],
    }
    with _lock:
        try:
            _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
            # P2-7：大小轮转（>5MB 时旧日志改名 .old 后重新开始），防无限增长
            try:
                if _AUDIT_PATH.exists() and _AUDIT_PATH.stat().st_size > _AUDIT_MAX_BYTES:
                    old = _AUDIT_PATH.with_suffix(".jsonl.old")
                    old.unlink(missing_ok=True)
                    _AUDIT_PATH.replace(old)
            except OSError:
                pass
            with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # 审计失败不阻塞主流程


def is_ip_suspicious(ip: str) -> bool:
    """异常 IP 检测：同 IP 5 分钟内登录失败 ≥6 次视为异常。"""
    with _lock:
        cnt = _prune(_login_attempts[f"fail:{ip}"], _LOGIN_WINDOW)
        return cnt >= 6


def record_login_failure(ip: str, user: str = "") -> None:
    """记录登录失败（用于异常 IP 检测）。"""
    with _lock:
        _gc_dict(_login_attempts)
        key = f"fail:{ip}"
        _prune(_login_attempts[key], _LOGIN_WINDOW)
        _login_attempts[key].append(_now())
    audit("login_fail", user=user, ip=ip)


def generate_code() -> str:
    """生成 6 位数字验证码。"""
    return f"{secrets.randbelow(1000000):06d}"


def reset_rate_state() -> None:
    """清空所有限速状态（测试用）。"""
    with _lock:
        _login_attempts.clear()
        _code_sent.clear()
        _code_fails.clear()


def read_audit(tail: int = 200) -> list[dict]:
    """读取审计日志最后 N 条（v8.17 安全中心聚合用）。"""
    with _lock:
        if not _AUDIT_PATH.exists():
            return []
        try:
            lines = _AUDIT_PATH.read_text(encoding="utf-8").strip().split("\n")
            entries: list[dict] = []
            for line in lines[-tail:]:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return entries
        except OSError:
            return []


def clear_audit() -> None:
    """清空审计日志（v8.17 安全中心清空按钮）。"""
    with _lock:
        try:
            _AUDIT_PATH.write_text("", encoding="utf-8")
        except OSError:
            pass
