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
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

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
# 项目根目录（app/ 的上一级，即 webui/；v8.14：三版本分目录后打包基准随之修正）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PROJECT_ROOT.parent          # 仓库根（DeverAI/，存放共享文档与 lite 入口）

# 各项目打包基准目录：桌面版在 pyqt/，网页版在 webui/
_PROJECT_BASE = {
    "deverai-desktop": _REPO_ROOT / "pyqt",
    "deverai-web": _PROJECT_ROOT,
}

# 公开元数据（响应体只含这些字段，打包清单单独存放不泄露）
_PROJECTS = [
    {"id": "deverai-desktop", "name": "DeverAI 桌面版", "version": "8.13.0",
     "description": "PyQt5 桌面 AI 编程助手（Agent 运行在本机 Python 进程内）", "size": ""},
    {"id": "deverai-web", "name": "DeverAI 网页版", "version": "2.2.0",
     "description": "浏览器端 Agent，FastAPI 后端（本地资源桥 + LLM 代理）", "size": ""},
]

# 打包清单：项目 id → 源文件/目录。
# v8.14：相对各自基准目录解析；"../" 前缀 = 相对仓库根（共享文档 / Lite 入口）。
# 归档名 = 清单项去掉 "../" 前缀后的路径。不存在的项静默跳过（向前兼容）。
_PROJECT_INCLUDE = {
    "deverai-desktop": ["desktop", "main.py", "cli_main.py",
                        "requirements.txt", "README.md", "AGENT.txt",
                        "../Design.md", "../Techniques.md", "../Fact.md", "../Future.md", "../FreqErr.md"],
    "deverai-web": ["app", "static", "web_main.py",
                    "../lite_main.py", "../sync_server.py",
                    "requirements.txt", "README.md", "AGENT.txt",
                    "../Design.md", "../Techniques.md", "../Fact.md", "../Future.md", "../FreqErr.md"],
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

    只对「仓库相对路径」判排除（P2-7：不用绝对路径 parts，检出目录名撞上
    data/tools/tests 等排除名时会把整包排空）；仓库外的路径退化为仅判文件名。
    """
    if p.name in _EXCLUDE_FILE_NAMES or p.suffix.lower() in _EXCLUDE_SUFFIXES:
        return True
    try:
        parts = set(p.relative_to(_REPO_ROOT).parts)
    except ValueError:
        parts = {p.name}
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


def _iter_include(project_id: str, include: list):
    """展开打包清单为 (源路径, 归档名) 序列。

    v8.14：清单项按项目基准目录解析，"../" 前缀相对仓库根；
    此前全部按 webui/ 解析——桌面版三项代码与 lite_main.py/sync_server.py
    实际不存在于该目录，ZIP 静默缺文件。
    """
    base = _PROJECT_BASE.get(project_id, _PROJECT_ROOT)
    for rel in include:
        if rel.startswith("../"):
            yield _REPO_ROOT / rel[3:], rel[3:]
        else:
            yield base / rel, rel


def _project_size_mb(project_id: str, include: list) -> str:
    """打包内容体积（供列表展示，惰性计算 + 缓存；与 _build_project_zip 同规则）。"""
    cache_key = (project_id, tuple(include))
    cached = _SIZE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    total = 0
    for src, _arc in _iter_include(project_id, include):
        try:
            if src.is_file() and _zip_ok(src):
                total += src.stat().st_size
            elif src.is_dir():
                for f in src.rglob("*"):
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


def _build_project_zip(project_id: str, include: list) -> io.BytesIO:
    """把打包清单压成内存 ZIP（单个文件失败跳过，不整体 500；符号链接/超大文件跳过）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arc in _iter_include(project_id, include):
            if src.is_file() and _zip_ok(src):
                try:
                    zf.write(src, arc)
                except OSError:
                    continue
            elif src.is_dir():
                for f in sorted(src.rglob("*")):
                    if not f.is_file() or _path_excluded(f) or not _zip_ok(f):
                        continue
                    try:
                        zf.write(f, (Path(arc) / f.relative_to(src)).as_posix())
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
        meta["size"] = await asyncio.to_thread(_project_size_mb, p["id"], _PROJECT_INCLUDE.get(p["id"], []))
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
        buf = await asyncio.to_thread(_build_project_zip, project_id, include)
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


# v8.17 安全中心：审计日志读取与清空
@app.get("/api/security/audit")
async def api_security_audit(user: dict = Depends(auth.current_user)):
    from .security import read_audit
    return {"entries": read_audit(200)}


@app.post("/api/security/audit/clear")
async def api_security_audit_clear(user: dict = Depends(auth.current_user)):
    from .security import clear_audit
    clear_audit()
    return {"ok": True}


# v8.22：SMTP 发件状态（泄露报告邮件通知的前置探测）
@app.get("/api/security/smtp_status")
async def api_smtp_status(user: dict = Depends(auth.current_user)):
    from . import mailer
    return {"configured": mailer.is_configured()}


# v8.22：heartbeat 泄露检查——本地项目敏感凭证扫描 + 外部 API 厂商存活心跳 + 可选 SMTP 报告
# 密钥模式（命中即视为疑似泄露，回显时截断脱敏）
_LEAK_PATTERNS = [
    (r"sk-ant-[A-Za-z0-9_-]{20,}", "Anthropic Key"),
    (r"sk-[A-Za-z0-9_-]{32,}", "OpenAI 风格 Key"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}", "GitHub Token"),
    (r"AIza[0-9A-Za-z_-]{30,}", "Google API Key"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "Slack Token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥文件"),
    (r"(?i)(api_key|apikey|secret|token|password)\s*[:=]\s*['\"][^'\"\s]{20,}['\"]", "配置中的凭证"),
]
_LEAK_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv",
                   ".idea", ".vscode", "dist", "build", "backups", "data", ".worktrees"}
_LEAK_MAX_FILES = 20000       # 扫描文件数上限
_LEAK_MAX_FILE_SIZE = 2 * 1024 * 1024  # 单文件读取上限 2MB
_LEAK_MAX_FINDINGS = 200      # 发现条数上限
_LEAK_MAX_VENDORS = 20        # 心跳厂商数上限
_LEAK_TEXT_EXTS = {".txt", ".md", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
                   ".conf", ".env", ".py", ".js", ".ts", ".html", ".css", ".xml",
                   ".sh", ".bat", ".ps1", ".sql", ".go", ".rs", ".java"}


def _redact_secret(s: str) -> str:
    return s[:8] + "..." if len(s) > 12 else "***"


def _scan_workspace_secrets() -> list[dict]:
    """扫描已授权工作区中的疑似泄露凭证。路径以相对路径回显（不暴露绝对路径）。"""
    import os
    from .bridge import _workspace
    try:
        root = _workspace()
    except Exception:
        return []

    findings: list[dict] = []
    files_checked = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _LEAK_SKIP_DIRS]
        for fn in filenames:
            if files_checked >= _LEAK_MAX_FILES or len(findings) >= _LEAK_MAX_FINDINGS:
                return findings
            ext = os.path.splitext(fn)[1].lower()
            p = Path(dirpath) / fn
            try:
                if p.stat().st_size > _LEAK_MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            if ext and ext not in _LEAK_TEXT_EXTS and not fn.startswith(".env"):
                continue
            files_checked += 1
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if len(findings) >= _LEAK_MAX_FINDINGS:
                    break
                for pat, label in _LEAK_PATTERNS:
                    m = re.search(pat, line)
                    if m:
                        rel = p.relative_to(root).as_posix()
                        findings.append({
                            "file": rel[:200], "line": lineno, "kind": label,
                            "preview": _redact_secret(m.group(0)),
                        })
                        break
    return findings


@app.post("/api/security/leak_scan")
async def api_security_leak_scan(body: dict, user: dict = Depends(auth.current_user)):
    import httpx
    from .security import audit as audit_log
    from .bridge import _workspace

    vendors_in = body.get("vendors")
    vendors = []
    if isinstance(vendors_in, list):
        for v in vendors_in[:_LEAK_MAX_VENDORS]:
            if isinstance(v, dict) and str(v.get("base_url") or "").startswith(("http://", "https://")):
                vendors.append({"name": str(v.get("name") or "")[:60],
                                "base_url": str(v["base_url"])[:500]})
    notify_email = str(body.get("notify_email") or "").strip()[:200]

    # 1) 本地项目泄露扫描（工作区未授权时返回提示而非报错，检查仍可跑厂商心跳）
    findings: list[dict] = []
    ws_note = ""
    try:
        _workspace()
        findings = await asyncio.to_thread(_scan_workspace_secrets)
    except Exception:
        ws_note = "本地工作区未授权，跳过本地文件扫描（仅执行厂商心跳）"

    # 2) 厂商心跳：HEAD 探活（SSRF 约束——只允许公网地址，复用 proxy 的校验器）
    from .proxy import _is_private_nonloopback, _is_loopback
    vendor_results = []
    for v in vendors:
        entry = {"name": v["name"], "base_url": v["base_url"], "status": None, "latency_ms": None, "error": ""}
        try:
            host = urlparse(v["base_url"]).hostname or ""
            if not host or _is_loopback(host) or _is_private_nonloopback(host):
                entry["error"] = "地址非法（内网/环回）"
            else:
                t0 = time.time()
                async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=8.0)) as client:
                    resp = await client.head(v["base_url"], follow_redirects=False)
                    entry["status"] = resp.status_code
                entry["latency_ms"] = int((time.time() - t0) * 1000)
        except Exception as e:
            entry["error"] = type(e).__name__
        vendor_results.append(entry)

    # 3) SMTP 报告（仅在有问题且填了收件邮箱时发送）
    smtp_sent = False
    smtp_note = ""
    vendor_down = [v for v in vendor_results if v["error"] or (v["status"] and v["status"] >= 500)]
    if notify_email and (findings or vendor_down):
        lines = ["DeverAI heartbeat 泄露检查报告", "=" * 40, ""]
        if findings:
            lines.append(f"[!] 本地疑似泄露 {len(findings)} 处：")
            for f in findings[:50]:
                lines.append(f"  - {f['file']}:{f['line']} ({f['kind']}) {f['preview']}")
            lines.append("")
        if vendor_down:
            lines.append(f"[!] 厂商异常 {len(vendor_down)} 个：")
            for v in vendor_down:
                lines.append(f"  - {v['name']} {v['base_url']} → {v['error'] or ('HTTP ' + str(v['status']))}")
            lines.append("")
        if not findings and not vendor_down:
            lines.append("[OK] 未发现问题。")
        ok, reason = mailer.send_text_email(
            notify_email, "DeverAI 泄露检查报告 " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "\n".join(lines))
        smtp_sent = ok
        if not ok:
            smtp_note = reason

    audit_log("leak_scan", user=user.get("name", ""), detail=f"findings={len(findings)} vendors={len(vendor_results)}")
    return {
        "findings": findings,
        "vendors": vendor_results,
        "ws_note": ws_note,
        "smtp_sent": smtp_sent,
        "smtp_note": smtp_note,
        "smtp_configured": mailer.is_configured(),
    }
