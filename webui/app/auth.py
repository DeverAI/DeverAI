"""身份验证：HMAC 签名会话令牌 + HttpOnly Cookie。令牌无状态，服务端不存会话。"""
import base64
import hashlib
import hmac
import json
import time

from fastapi import Depends, HTTPException, Request, Response

from .config import get_config

SESSION_COOKIE = "deverai_session"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(payload: dict, secret: str) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"


def verify_token(token: str, secret: str) -> dict | None:
    try:
        body, sig = token.rsplit(".", 1)
        expect = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(sig, _b64(expect)):
            return None
        payload = json.loads(_unb64(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def session_token(username: str, secret: str, ttl_hours: int = 72) -> str:
    """生成会话令牌（供登录响应体回传；curl 复现请求时用 Authorization: Bearer 携带）。"""
    payload = {"u": username, "exp": time.time() + ttl_hours * 3600}
    return make_token(payload, secret)


def set_session(response: Response, username: str, secret: str, ttl_hours: int = 72,
                request: Request | None = None) -> str:
    token = session_token(username, secret, ttl_hours)
    # v6.2 P1-1：secure 属性随请求 scheme 动态决定（HTTPS 时启用）
    # 优先看 X-Forwarded-Proto（反向代理场景），再看 url.scheme
    secure = False
    if request is not None:
        fwd_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        secure = (fwd_proto == "https") or (request.url.scheme == "https")
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, samesite="strict", max_age=ttl_hours * 3600, path="/",
        secure=secure,
    )
    return token


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def current_user(request: Request) -> dict:
    """FastAPI 依赖：解析并校验会话，未登录抛 401。

    鉴权顺序：Cookie（HttpOnly）→ Authorization: Bearer <token>（供 curl 复现请求）。
    Cookie 校验失败会回退 Bearer（浏览器残留过期 Cookie 时，合法 curl 仍可工作）。
    """
    cfg = get_config()
    token = request.cookies.get(SESSION_COOKIE, "")
    payload = verify_token(token, cfg.secret) if token else None
    if not payload:
        authz = (request.headers.get("authorization") or "").strip()
        if authz.lower().startswith("bearer "):
            payload = verify_token(authz[7:].strip(), cfg.secret)
    if not payload:
        raise HTTPException(401, "未登录或会话已过期")
    from .users import get_user
    user = get_user(payload.get("u", ""))
    if user is None:
        raise HTTPException(401, "用户不存在")
    # v6.2 P2-7：返回前剥离敏感字段（hash/salt），defense-in-depth 防未来路由误泄露
    return {k: v for k, v in user.items() if k not in ("hash", "salt")}
