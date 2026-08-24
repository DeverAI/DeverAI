"""DeverAI Host Server：只做三件事——
1. 下发 UI（static/）
2. 身份验证（注册/登录/会话）——v6.2 扩展：邮箱注册 + 验证码 + 限速 + 审计
3. 无状态 LLM 转发代理 + 本地资源桥（被 UI 调用）

v6.2 新增：
- 邮箱注册：POST /api/auth/send_code → POST /api/auth/register_email
- 账户查看：GET /api/users（登录后）
- 项目下载：GET /api/projects、GET /api/projects/{id}/download（v8.12 起打包真实源码 ZIP）
- 安全：登录限速、异常 IP 检测、审计日志

所有 Agent 逻辑（对话、工具编排、规划、资产、模式）都在浏览器端执行。
官方服务器不接触用户代码，仅负责身份验证与项目下发。
"""
import asyncio
import datetime
import hashlib
import hmac
import io
import zipfile
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, bridge, mailer, meta_bridge, proxy, security, snap_bridge, users, voice_pet_host
from .config import APP_DIR, DATA_DIR, get_config, init_config
from .errors import log_error, read_errors, clear_errors

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"  # webui/static/
VERSION = "2.2.0"

app = FastAPI(title="DeverAI Host Server", version=VERSION)

# P2-1（查修）：Web 端未处理异常统一落 Err.log（此前仅桌面写，web 错误无审计）
@app.exception_handler(Exception)
async def _unhandled_exc(request: Request, exc: Exception):
    log_error(f"[web] {request.method} {request.url.path}", exc)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


@app.on_event("startup")
async def _startup():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "history").mkdir(exist_ok=True)
    init_config()


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(proxy.router)
app.include_router(bridge.router)
app.include_router(snap_bridge.router)
app.include_router(meta_bridge.router)
app.include_router(voice_pet_host.router)


def _client_ip(request: Request) -> str:
    """获取客户端 IP。

    v6.2 安全：不无条件信任 X-Forwarded-For（防伪造绕过限速）。
    仅当直连 IP 是 127.0.0.1/localhost（本地反向代理场景）时才解析 XFF。
    """
    direct = request.client.host if request.client else "unknown"
    # 仅本地代理场景信任 XFF（生产部署在 nginx 后时直连 IP 是 127.0.0.1）
    if direct in ("127.0.0.1", "::1", "localhost"):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return direct


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/server/info")
async def server_info():
    cfg = get_config()
    return {
        "name": "DeverAI",
        "version": VERSION,
        "allow_register": cfg.allow_register,
        "has_users": users.user_count() > 0,
        "bridge_authorized": bool(cfg.bridge_workspace),
        "email_enabled": mailer.is_configured(),
    }


# ------------------------------------------------------------------ #
# 身份验证
# ------------------------------------------------------------------ #
class AuthBody(BaseModel):
    username: str = ""
    password: str = ""


class EmailRegisterBody(BaseModel):
    email: str = ""
    password: str = ""
    code: str = ""
    username: str = ""


class SendCodeBody(BaseModel):
    email: str = ""


@app.post("/api/auth/register")
async def register(body: AuthBody, response: Response, request: Request):
    cfg = get_config()
    if not cfg.allow_register:
        raise HTTPException(403, "注册已关闭")
    ip = _client_ip(request)
    if security.is_ip_suspicious(ip):
        security.audit("register_blocked_suspicious_ip", ip=ip)
        raise HTTPException(403, "请求过于频繁，请稍后再试")
    if not security.check_login_rate(f"reg:{ip}"):
        security.audit("register_rate_limited", ip=ip)
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    try:
        await asyncio.to_thread(users.create_user, body.username, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = auth.set_session(response, body.username.strip(), cfg.secret, cfg.session_ttl_hours, request)
    security.audit("register", user=body.username.strip(), ip=ip)
    return {"ok": True, "username": body.username.strip(), "token": token}


@app.post("/api/auth/login")
async def login(body: AuthBody, response: Response, request: Request):
    cfg = get_config()
    ip = _client_ip(request)
    if security.is_ip_suspicious(ip):
        security.audit("login_blocked_suspicious_ip", user=body.username, ip=ip)
        raise HTTPException(403, "登录尝试过多，请稍后再试")
    if not security.check_login_rate(f"login:{ip}"):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    u = await asyncio.to_thread(users.verify_user, body.username.strip(), body.password)
    if u is None:
        security.record_login_failure(ip, body.username)
        raise HTTPException(401, "用户名或密码错误")
    token = auth.set_session(response, u["username"], cfg.secret, cfg.session_ttl_hours, request)
    security.audit("login", user=u["username"], ip=ip)
    return {"ok": True, "username": u["username"], "token": token}


@app.post("/api/auth/logout")
async def logout(response: Response):
    auth.clear_session(response)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user: dict = Depends(auth.current_user)):
    return {"ok": True, "username": user["username"], "email": user.get("email", "")}


# ------------------------------------------------------------------ #
# v6.2 邮箱注册（POP3/SMTP 验证码）
# ------------------------------------------------------------------ #
@app.post("/api/auth/send_code")
async def send_code(body: SendCodeBody, request: Request):
    """发送验证码到指定邮箱（SMTP SSL）。"""
    email = (body.email or "").strip().lower()
    if not email:
        raise HTTPException(400, "邮箱不能为空")
    # v8.14：注册关闭时同样不发验证码（与注册开关语义一致）
    if not get_config().allow_register:
        raise HTTPException(403, "注册已关闭")
    if len(email) > 254:
        raise HTTPException(400, "邮箱格式不正确")
    # v6.2 P1-3：邮箱格式校验（防邮件头注入）
    if not users.is_valid_email(email):
        raise HTTPException(400, "邮箱格式不正确")
    ip = _client_ip(request)
    # v6.2 P2-8：异常 IP 检测
    if security.is_ip_suspicious(ip):
        security.audit("send_code_blocked_suspicious_ip", ip=ip)
        raise HTTPException(403, "请求过于频繁，请稍后再试")
    # v6.2 P1-2：IP 维度限速（防邮件轰炸）
    if not security.check_send_ip_rate(ip):
        security.audit("send_code_ip_rate_limited", ip=ip)
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    # 邮箱维度限速
    ok, reason = security.check_code_rate(email)
    if not ok:
        security.audit("send_code_rate_limited", ip=ip, detail=email)
        raise HTTPException(429, reason)
    if not mailer.is_configured():
        raise HTTPException(503, "邮件服务未配置，无法发送验证码")
    # v6.2 P2-2：不泄露已注册状态（已注册也静默返回，不实际发邮件）
    already_registered = bool(await asyncio.to_thread(users.get_user_by_email, email))
    if already_registered:
        security.audit("send_code_already_registered", ip=ip, detail=email)
        return {"ok": True, "ttl": 600}  # 静默：不报错也不发邮件
    code = security.generate_code()
    users.store_code(email, code)
    # v6.2 P1-1：SMTP 是同步阻塞调用，放到线程池避免卡事件循环
    ok, err = await asyncio.to_thread(mailer.send_verification_code, email, code)
    if not ok:
        users.clear_code(email)  # v6.2 P1-6：发送失败清理 code
        raise HTTPException(500, err)
    security.audit("send_code", ip=ip, detail=email)
    return {"ok": True, "ttl": 600}


@app.post("/api/auth/register_email")
async def register_email(body: EmailRegisterBody, response: Response, request: Request):
    """邮箱注册：校验验证码后创建账户。"""
    cfg = get_config()
    if not cfg.allow_register:
        raise HTTPException(403, "注册已关闭")
    ip = _client_ip(request)
    if security.is_ip_suspicious(ip):
        security.audit("register_email_blocked_suspicious_ip", ip=ip)
        raise HTTPException(403, "请求过于频繁，请稍后再试")
    if not security.check_login_rate(f"reg:{ip}"):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    email = (body.email or "").strip().lower()
    code = (body.code or "").strip()
    if not email or not code:
        raise HTTPException(400, "邮箱和验证码不能为空")
    if not users.verify_code(email, code):
        security.audit("register_email_code_fail", ip=ip, detail=email)
        raise HTTPException(400, "验证码错误或已过期")
    try:
        u = await asyncio.to_thread(
            users.create_user_with_email, email, body.password, body.username)
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = auth.set_session(response, u["username"], cfg.secret, cfg.session_ttl_hours, request)
    security.audit("register_email", user=u["username"], ip=ip, detail=email)
    return {"ok": True, "username": u["username"], "email": u["email"], "token": token}


# ------------------------------------------------------------------ #
# v6.2 账户查看（登录后）
# ------------------------------------------------------------------ #
@app.get("/api/users")
async def list_users(user: dict = Depends(auth.current_user)):
    """查看已注册账户列表（仅公开信息，不含密码哈希）。"""
    return {"ok": True, "users": users.list_users_public(), "count": users.user_count()}


# ------------------------------------------------------------------ #
# v6.2 项目下载（真实项目包：打包源码目录，排除敏感/运行时数据）
# ------------------------------------------------------------------ #
# 项目根目录（app/ 的上一级，即工作区根）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 公开元数据（响应体只含这些字段，打包清单单独存放不泄露）
_PROJECTS = [
    {"id": "deverai-desktop", "name": "DeverAI 桌面版", "version": "8.13.0",
     "description": "PyQt5 桌面 AI 编程助手（Agent 运行在本机 Python 进程内）", "size": ""},
    {"id": "deverai-web", "name": "DeverAI 网页版", "version": "2.2.0",
     "description": "浏览器端 Agent，FastAPI 后端（本地资源桥 + LLM 代理）", "size": ""},
]

# 打包清单：项目 id → 源文件/目录（相对项目根）
_PROJECT_INCLUDE = {
    "deverai-desktop": ["desktop", "main.py", "cli_main.py", "requirements.txt", "README.md", "AGENT.txt",
                        "Design.md", "Techniques.md", "Fact.md", "Future.md", "FreqErr.md"],
    "deverai-web": ["app", "static", "web_main.py", "lite_main.py", "sync_server.py",
                    "requirements.txt", "README.md", "AGENT.txt",
                    "Design.md", "Techniques.md", "Fact.md", "Future.md", "FreqErr.md"],
}

# 排除：敏感数据（data/ 含 API Key）、备份、运行时缓存、大体积开发资料
_EXCLUDE_DIR_NAMES = {"data", "backups", "__pycache__", ".pytest_cache", ".git",
                      "dev_log", "updates", "tools", "image_API_tools", "参考图", "tests"}
_EXCLUDE_FILE_NAMES = {"Err.log", "todo.md", "done.md"}
_EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".log", ".bak"}
_EXCLUDE_DOT = {".git"}


_SIZE_CACHE: dict = {}
_MAX_ZIP_FILE = 30 * 1024 * 1024   # 单文件进入分发包的大小上限（超限跳过，防超大文件撑爆内存）


def _path_excluded(p: Path) -> bool:
    """打包排除判定：敏感文件/目录/二进制资源不进入分发包。

    只对「项目相对路径」判排除（P2-7：此前用绝对路径 parts，检出目录名撞上
    data/tools/tests 等排除名时会把整包排空）。
    """
    if p.name in _EXCLUDE_FILE_NAMES or p.suffix.lower() in _EXCLUDE_SUFFIXES:
        return True
    try:
        parts = set(p.relative_to(_PROJECT_ROOT).parts)
    except ValueError:
        parts = set(p.parts)
    return bool(parts & (_EXCLUDE_DIR_NAMES | _EXCLUDE_DOT))


def _zip_ok(f: Path) -> bool:
    """文件是否可进包：非符号链接（防越界泄露工作区外内容）+ 大小上限。"""
    if f.is_symlink():
        return False
    try:
        if f.stat().st_size > _MAX_ZIP_FILE:
            return False
    except OSError:
        return False
    return True


def _project_size_mb(include: list) -> str:
    """打包内容体积（供列表展示，惰性计算 + 缓存；与 _build_project_zip 同规则）。"""
    cache_key = tuple(include)
    cached = _SIZE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    total = 0
    for rel in include:
        p = _PROJECT_ROOT / rel
        try:
            if p.is_file() and _zip_ok(p):
                total += p.stat().st_size
            elif p.is_dir():
                for f in p.rglob("*"):
                    try:
                        if f.is_file() and not _path_excluded(f) and _zip_ok(f):
                            total += f.stat().st_size
                    except OSError:
                        continue
        except OSError:
            continue
    mb = total / 1048576.0
    text = f"约 {mb:.1f}MB" if mb >= 1.0 else f"约 {max(total // 1024, 1)}KB"
    _SIZE_CACHE[cache_key] = text
    return text


def _build_project_zip(include: list) -> io.BytesIO:
    """把打包清单压成内存 ZIP（单个文件失败跳过，不整体 500；符号链接/超大文件跳过）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in include:
            p = _PROJECT_ROOT / rel
            if p.is_file() and _zip_ok(p):
                try:
                    zf.write(p, rel)
                except OSError:
                    continue
            elif p.is_dir():
                for f in sorted(p.rglob("*")):
                    if not f.is_file() or _path_excluded(f) or not _zip_ok(f):
                        continue
                    try:
                        zf.write(f, str(f.relative_to(_PROJECT_ROOT)).replace("\\", "/"))
                    except OSError:
                        continue
    buf.seek(0)
    return buf


# 下载链接签名密钥（从 config secret 派生，自动轮换）
_DL_TTL = 900  # 下载链接 15 分钟时效


def _sign_download(project_id: str, expire_ts: int) -> str:
    """HMAC-SHA256 签名下载链接。"""
    cfg = get_config()
    msg = f"{project_id}:{expire_ts}"
    sig = hmac.new(cfg.secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return sig


def _verify_download(project_id: str, expire_ts: int, sig: str) -> bool:
    if datetime.datetime.now().timestamp() > expire_ts:
        return False
    expected = _sign_download(project_id, expire_ts)
    return hmac.compare_digest(expected, sig)


@app.get("/api/projects")
async def list_projects(user: dict = Depends(auth.current_user)):
    """项目列表（登录后可见）。"""
    now = datetime.datetime.now().timestamp()
    items = []
    for p in _PROJECTS:
        expire = int(now + _DL_TTL)
        meta = dict(p)
        meta["size"] = await asyncio.to_thread(_project_size_mb, _PROJECT_INCLUDE.get(p["id"], []))
        meta["download_url"] = f"/api/projects/{p['id']}/download?expire={expire}&sig={_sign_download(p['id'], expire)}"
        meta["expires_in"] = _DL_TTL
        items.append(meta)
    return {"ok": True, "projects": items}


@app.get("/api/projects/{project_id}/download")
async def download_project(project_id: str, expire: int, sig: str,
                           user: dict = Depends(auth.current_user),
                           request: Request = None):
    """下载项目 ZIP（签名时效 15 分钟）——打包真实源码，排除 data/密钥、backups、缓存等。"""
    ip = _client_ip(request) if request else ""
    if not _verify_download(project_id, expire, sig):
        security.audit("download_invalid_sig", user=user["username"], ip=ip, detail=project_id)
        raise HTTPException(403, "下载链接无效或已过期")
    meta = next((p for p in _PROJECTS if p["id"] == project_id), None)
    include = _PROJECT_INCLUDE.get(project_id)
    if meta is None or not include:
        raise HTTPException(404, "项目不存在")
    security.audit("download", user=user["username"], ip=ip, detail=project_id)
    try:
        buf = await asyncio.to_thread(_build_project_zip, include)
    except Exception:
        log_error(f"[web] project zip {project_id}", None)
        raise HTTPException(500, "打包失败")
    fname = f"{project_id}_v{meta['version']}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/errs")
async def api_errs(user: dict = Depends(auth.current_user)):
    from .errors import read_errors
    return {"content": read_errors()}


@app.post("/api/errs/clear")
async def api_errs_clear(user: dict = Depends(auth.current_user)):
    from .errors import clear_errors
    clear_errors()
    return {"ok": True}
