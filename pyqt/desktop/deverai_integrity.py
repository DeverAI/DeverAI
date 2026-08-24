"""DeveraiIntegrityService — HKDF + HMAC-SHA256 防篡改签名服务。

设计原则：
1. 密钥派生：使用 HKDF（RFC 5869）从三个熵源派生主密钥：
   - 机器身份（machine.json 中的 machineId）
   - 用户盐值（integrity_salt.bin，首次运行时生成）
   - 随机数（integrity_nonce.bin，首次运行时生成）
2. 上下文绑定：每种受保护类型使用不同的上下文字符串，防止跨类型重放攻击。
3. HMAC-SHA256 签名：HMAC(master_secret, context || data)，使用 timing-safe 比较。
4. 生产循环封装：签名操作完全封装在服务类内部，私钥永远不会离开服务边界。

> 本模块为桌面版独占服务。网页版通过 bridge 暴露 signIntegrity / verifyIntegrity / getContentHash 三个 RPC。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Optional

from .storage import load_json, save_json, save_bytes, load_bytes

# ---- 受保护类型上下文字符串（防跨类型重放）----
_CONTEXT_TOOL = b"deverai:integrity:tool:v1"
_CONTEXT_ASSET = b"deverai:integrity:asset:v1"
_CONTEXT_DOC = b"deverai:integrity:doc:v1"
_CONTEXT_VOICE_TASK = b"deverai:integrity:voice-task:v1"
_CONTEXT_VOICE_CONSTRAINT = b"deverai:integrity:voice-constraint:v1"

_CONTEXT_MAP = {
    "tool": _CONTEXT_TOOL,
    "asset": _CONTEXT_ASSET,
    "doc": _CONTEXT_DOC,
    "voice-task": _CONTEXT_VOICE_TASK,
    "voice-constraint": _CONTEXT_VOICE_CONSTRAINT,
}

# ---- 熵源文件路径 ----
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MACHINE_FILE = DATA_DIR / "machine.json"
SALT_FILE = DATA_DIR / "integrity_salt.bin"
NONCE_FILE = DATA_DIR / "integrity_nonce.bin"

# HKDF 参数
HKDF_HASH = hashlib.sha256
HKDF_HASH_LEN = 32
HKDF_INFO = b"deverai:integrity:master-key:v1"
HKDF_SALT_LEN = 32
NONCE_LEN = 16


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    """HKDF-Extract: PRK = HMAC-Hash(salt, IKM)"""
    return hmac.new(salt, ikm, HKDF_HASH).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """HKDF-Expand: OKM = T(1) || T(2) || ... 截断到 length"""
    n = (length + HKDF_HASH_LEN - 1) // HKDF_HASH_LEN
    if n > 255:
        raise ValueError("HKDF expand length too large")
    okm = b""
    t = b""
    for i in range(1, n + 1):
        t = hmac.new(prk, t + info + bytes([i]), HKDF_HASH).digest()
        okm += t
    return okm[:length]


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    """完整 HKDF：Extract + Expand"""
    prk = _hkdf_extract(salt, ikm)
    return _hkdf_expand(prk, info, length)


def _timing_safe_compare(a: bytes, b: bytes) -> bool:
    """Timing-safe 比较，防时序攻击"""
    return hmac.compare_digest(a, b)


def _get_or_create_file(path: Path, size: int) -> bytes:
    """读取二进制文件，不存在则生成随机内容并写入"""
    data = load_bytes(path)
    if data is not None and len(data) == size:
        return data
    data = secrets.token_bytes(size)
    save_bytes(path, data)
    return data


def _get_machine_id() -> bytes:
    """获取机器身份 ID（从 machine.json 读取，不存在则生成）"""
    data = load_json(MACHINE_FILE, {})
    mid = data.get("machineId", "")
    if not mid:
        mid = secrets.token_hex(16)
        data["machineId"] = mid
        save_json(MACHINE_FILE, data)
    return mid.encode("utf-8")


class DeveraiIntegrityService:
    """防篡改签名服务（单例封装）。

    使用方式：
        service = DeveraiIntegrityService()
        sig = service.sign(data_bytes, "tool")
        ok = service.verify(data_bytes, sig, "tool")
    """

    _instance: Optional["DeveraiIntegrityService"] = None
    _master_secret: Optional[bytes] = None

    def __new__(cls) -> "DeveraiIntegrityService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _derive_master_secret(self) -> bytes:
        """从三个熵源派生主密钥（HKDF）。"""
        if self._master_secret is not None:
            return self._master_secret

        # 熵源 1：机器身份
        machine_id = _get_machine_id()

        # 熵源 2：用户盐值（首次运行生成）
        salt = _get_or_create_file(SALT_FILE, HKDF_SALT_LEN)

        # 熵源 3：随机数（首次运行生成）
        nonce = _get_or_create_file(NONCE_FILE, NONCE_LEN)

        # 合并熵源作为 IKM
        ikm = machine_id + nonce

        # HKDF 派生主密钥
        self._master_secret = _hkdf(salt, ikm, HKDF_INFO, HKDF_HASH_LEN)
        return self._master_secret

    def _get_context(self, ctx_type: str) -> bytes:
        """获取受保护类型的上下文字符串"""
        ctx = _CONTEXT_MAP.get(ctx_type)
        if ctx is None:
            raise ValueError(f"未知的完整性上下文类型: {ctx_type}，可用: {list(_CONTEXT_MAP.keys())}")
        return ctx

    def getContentHash(self, data: bytes) -> str:
        """快速内容哈希（SHA-256），用于快速比对"""
        if isinstance(data, str):
            data = data.encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def signIntegrity(self, data: bytes, ctx_type: str = "tool") -> str:
        """对数据生成 HMAC-SHA256 签名。

        Args:
            data: 待签名数据（bytes 或 str）
            ctx_type: 受保护类型（tool/asset/doc/voice-task/voice-constraint）

        Returns:
            十六进制编码的 HMAC-SHA256 签名
        """
        if isinstance(data, str):
            data = data.encode("utf-8")
        master = self._derive_master_secret()
        ctx = self._get_context(ctx_type)
        # HMAC(master_secret, context || data)
        sig = hmac.new(master, ctx + data, HKDF_HASH).hexdigest()
        return sig

    def verifyIntegrity(self, data: bytes, signature: str, ctx_type: str = "tool") -> bool:
        """验证数据的 HMAC-SHA256 签名。

        Args:
            data: 原始数据（bytes 或 str）
            signature: 十六进制编码的签名
            ctx_type: 受保护类型（tool/asset/doc/voice-task/voice-constraint）

        Returns:
            签名是否有效（timing-safe 比较）
        """
        if isinstance(data, str):
            data = data.encode("utf-8")
        expected = self.signIntegrity(data, ctx_type)
        return _timing_safe_compare(expected.encode("utf-8"), signature.encode("utf-8"))

    def self_check(self) -> dict:
        """自检：验证服务能正常签名和验证。

        Returns:
            {ok: bool, message: str, types: list}
        """
        results = {}
        all_ok = True
        for ctx_type in _CONTEXT_MAP:
            try:
                test_data = f"self-check:{ctx_type}:test".encode("utf-8")
                sig = self.signIntegrity(test_data, ctx_type)
                ok = self.verifyIntegrity(test_data, sig, ctx_type)
                results[ctx_type] = ok
                if not ok:
                    all_ok = False
            except Exception as e:
                results[ctx_type] = False
                all_ok = False

        # 跨类型重放测试：用 tool 签名验证 asset 应该失败
        try:
            test_data = b"cross-context-replay-test"
            sig_tool = self.signIntegrity(test_data, "tool")
            cross_ok = not self.verifyIntegrity(test_data, sig_tool, "asset")
            results["cross-context-replay-blocked"] = cross_ok
            if not cross_ok:
                all_ok = False
        except Exception:
            results["cross-context-replay-blocked"] = False
            all_ok = False

        return {
            "ok": all_ok,
            "message": "完整性自检通过" if all_ok else "完整性自检失败",
            "types": results,
        }


# ---- 模块级便捷函数 ----
_service_instance: Optional[DeveraiIntegrityService] = None


def get_integrity_service() -> DeveraiIntegrityService:
    """获取 DeveraiIntegrityService 单例"""
    global _service_instance
    if _service_instance is None:
        _service_instance = DeveraiIntegrityService()
    return _service_instance