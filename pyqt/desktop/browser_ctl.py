"""直接操控浏览器（v8.9）——最小 Chrome DevTools Protocol 客户端（纯标准库，零第三方依赖）。

设计原则：
- 启动系统 Edge/Chrome，`--remote-debugging-port=0` 让浏览器自选空闲端口，
  只监听 127.0.0.1（不对外网暴露调试口），使用独立临时 profile。
- 通过 RFC6455 WebSocket 与 CDP 通信，实现 navigate / click(selector) /
  type(text) / press_keys / close。
- 所有 AI 可给的 URL/selector 必须先过 browser._valid_url 与工具层 _diplomatic_guard；
  本模块只提供机械动作，不做业务放行判断。
- 找不到浏览器/连接失败时安全降级为明确错误，不抛裸异常。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class BrowserSession:
    """一个被直接操控的浏览器实例。"""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.tmpdir: tempfile.TemporaryDirectory | None = None
        self.port: int = 0
        self.ws_url: str = ""
        self.pid: int = 0
        self.sock: socket.socket | None = None
        self._cmd_id = 0
        # v8.14：改为实例属性——此前是类属性，所有会话共享同一 dict，
        # 处理器跨 launch 累积且无法注销，新会话会收到旧会话的事件回调
        self._event_handlers: dict[str, list] = {}

    def close(self):
        self._disconnect()
        try:
            if self.proc is not None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=10,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    self.proc.kill()
        except Exception:
            try:
                if self.proc is not None:
                    self.proc.kill()
            except Exception:
                pass
        self.proc = None
        if self.tmpdir is not None:
            try:
                self.tmpdir.cleanup()
            except Exception:
                pass
            self.tmpdir = None

    def _connect(self):
        if self.sock is not None:
            return
        if not self.ws_url:
            raise RuntimeError("浏览器调试端口不可用")
        # 解析 ws(s)://host:port/path
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)?$", self.ws_url)
        if not m:
            raise RuntimeError("调试地址格式无效")
        host, port, path = m.group(1), int(m.group(2)), m.group(3) or "/"
        s = socket.create_connection((host, port), timeout=10.0)
        s.settimeout(10.0)  # 握手阶段整体有界，防 _recv_http 无限阻塞
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        s.sendall(req.encode("ascii"))
        resp = _recv_http(s)
        if "101" not in resp.split("\r\n", 1)[0]:
            s.close()
            raise RuntimeError("WebSocket 握手失败")
        s.settimeout(30.0)
        self.sock = s

    def _disconnect(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _send_frame(self, text: str):
        self._send_masked(0x81, text.encode("utf-8"))

    def _send_masked(self, opcode: int, payload: bytes):
        """发送客户端帧：RFC6455 要求客户端→服务端所有帧必须 mask。"""
        s = self.sock
        mask = os.urandom(4)
        header = bytearray()
        header.append(0x80 | (opcode & 0x0F))
        n = len(payload)
        if n <= 125:
            header.append(0x80 | n)
        elif n <= 0xFFFF:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        s.sendall(header + masked)

    def _recv_frame(self) -> str:
        s = self.sock
        hdr = _recv_exact(s, 2)
        opcode = hdr[0] & 0x0F
        n = hdr[1] & 0x7F
        if n == 126:
            n = struct.unpack("!H", _recv_exact(s, 2))[0]
        elif n == 127:
            n = struct.unpack("!Q", _recv_exact(s, 8))[0]
        if n > 64 * 1024 * 1024:  # 防御异常长度声明
            raise RuntimeError("WebSocket 帧长度异常")
        payload = _recv_exact(s, n)
        if opcode == 0x9:  # ping -> pong（客户端方向同样必须 mask）
            self._send_masked(0x8A, payload)
            return self._recv_frame()
        if opcode == 0x8:
            raise RuntimeError("浏览器关闭了调试连接")
        return payload.decode("utf-8", "replace")

    def add_event_handler(self, domain: str, handler) -> None:
        """注册 CDP 事件回调（如 "Network"、"Runtime"、"Log"）。"""
        if domain not in self._event_handlers:
            self._event_handlers[domain] = []
        self._event_handlers[domain].append(handler)

    def _dispatch_event(self, method: str, params: dict) -> None:
        """分发 CDP 事件到已注册的处理器。"""
        domain = method.split(".")[0] if "." in method else ""
        handlers = self._event_handlers.get(domain, [])
        for h in handlers:
            try:
                h(method, params)
            except Exception:
                pass

    def command(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        self._connect()
        self._cmd_id += 1
        cid = self._cmd_id
        msg = json.dumps({"id": cid, "method": method, "params": params or {}}, ensure_ascii=False)
        old = self.sock.settimeout(timeout)
        try:
            self._send_frame(msg)
            while True:
                resp = json.loads(self._recv_frame())
                if resp.get("id") == cid:
                    if "error" in resp:
                        raise RuntimeError(str(resp["error"])[:300])
                    return resp.get("result") or {}
                # CDP 事件（无 id 字段）→ 分发到事件处理器
                if "method" in resp:
                    self._dispatch_event(resp["method"], resp.get("params", {}))
        finally:
            self.sock.settimeout(old)


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise RuntimeError("连接中断")
        buf += chunk
    return buf


def _recv_http(s: socket.socket) -> str:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.decode("latin-1", "replace")


def _find_browser() -> str:
    from .browser import find_browser
    return find_browser()


_SESSION: BrowserSession | None = None


def _valid_launch_url(url: str) -> bool:
    from .browser import _valid_url
    return _valid_url(url, allow_private=True)


def _valid_nav_url(url: str) -> bool:
    from .browser import _valid_url
    return _valid_url(url)


def launch(url: str, headed: bool = False, stealth: bool = False,
           window_width: int = 1280, window_height: int = 800) -> dict:
    """启动一个可被直接操控的浏览器窗口。

    参数：
        headed: 是否使用有头模式（默认 False=无头，被风控拦截时自动回退到有头）
        stealth: 是否启用反检测（仅在有头模式下有效）
        window_width/height: 有头模式窗口尺寸（默认 1280x800）

    返回 {ok, pid, port, mode}，mode 为 "headless" 或 "headed"。
    """
    global _SESSION
    if _SESSION is not None:
        try:
            _SESSION.close()
        except Exception:
            pass
        _SESSION = None
    url = (url or "").strip()
    if not _valid_launch_url(url):
        return {"ok": False, "output": "URL 无效：仅支持 http(s) 且长度 ≤2048。"}
    exe = _find_browser()
    if not exe:
        return {"ok": False, "output": "未找到 Edge/Chrome/Chromium，无法直接操控浏览器。"}

    mode = "headed" if headed else "headless"
    result = _do_launch(exe, url, mode, stealth, window_width, window_height)
    return result


def _do_launch(exe: str, url: str, mode: str, stealth: bool,
               window_width: int, window_height: int) -> dict:
    """执行实际的浏览器启动。"""
    tmpdir = tempfile.TemporaryDirectory(prefix="deverai_ctl_")
    try:
        # v8.14：user-data-dir 指向本会话专属临时目录——此前写死共享路径
        # deverai_browser_profile，cookie/存储跨"临时"会话持久化（与设计承诺相反），
        # 且崩溃残留锁目录会导致后续启动反复超时
        cmd = _build_launch_cmd(exe, url, mode, stealth, window_width, window_height,
                                profile_dir=tmpdir.name)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=creationflags)
    except OSError as e:
        tmpdir.cleanup()
        return {"ok": False, "output": f"启动浏览器失败：{e}"}

    port = 0
    ws_url = ""
    deadline = time.monotonic() + 10.0
    active = Path(tmpdir.name) / "DevToolsActivePort"
    while time.monotonic() < deadline:
        try:
            if active.exists():
                lines = active.read_text(encoding="utf-8").splitlines()
                if len(lines) >= 1:
                    port = int(lines[0].strip())
                    browser_path = lines[1].strip() if len(lines) >= 2 else ""
                    ws_url = _page_ws_url(port, browser_path)
                    if ws_url:
                        break
        except Exception:
            pass
        if proc.poll() is not None:
            tmpdir.cleanup()
            return {"ok": False, "output": f"浏览器启动失败（退出码 {proc.returncode}）。"}
        time.sleep(0.2)

    if port <= 0 or not ws_url:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                proc.kill()
        except Exception:
            pass
        tmpdir.cleanup()
        return {"ok": False, "output": "浏览器调试端口初始化超时（可能被安全软件拦截）。"}

    session = BrowserSession()
    session.proc = proc
    session.tmpdir = tmpdir
    session.port = port
    session.ws_url = ws_url
    session.pid = proc.pid
    _SESSION = session
    return {"ok": True, "pid": proc.pid, "port": port, "mode": mode,
            "output": f"已启动浏览器（{mode} 模式，pid={proc.pid}，调试端口 {port}，仅 127.0.0.1）。"}


def _build_launch_cmd(exe: str, url: str, mode: str, stealth: bool,
                      window_width: int, window_height: int,
                      profile_dir: str | None = None) -> list:
    """构建浏览器启动命令行。

    v8.14：profile_dir 为会话专属目录（随 TemporaryDirectory.cleanup() 回收）；
    缺省时退回随机临时目录，不再使用固定共享路径。
    """
    profile = profile_dir or tempfile.mkdtemp(prefix="deverai_profile_")
    cmd = [exe, "--remote-debugging-port=0", "--no-first-run",
           "--disable-extensions", "--mute-audio",
           f"--user-data-dir={profile}"]

    if mode == "headless":
        # 无头模式：快速、资源少，但易被风控检测
        cmd.append("--headless=new")
        cmd.append("--disable-gpu")
    else:
        # 有头模式：模拟真实客户端浏览器
        cmd.append(f"--window-size={window_width},{window_height}")

        if stealth:
            # 反检测措施：让浏览器看起来像普通用户
            cmd.append("--disable-blink-features=AutomationControlled")
            cmd.append("--disable-infobars")
            cmd.append("--no-sandbox")
            cmd.append("--disable-dev-shm-usage")
            # 使用真实用户代理（不含 HeadlessChrome）
            cmd.append("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36")
            # 禁用自动化指示器
            cmd.append("--disable-automation")
            # 禁用 WebRTC 指纹泄露
            cmd.append("--disable-webrtc")
            # 设置合理的语言
            cmd.append("--lang=zh-CN,zh,en")
            # 禁用翻译弹窗
            cmd.append("--disable-features=Translate")
            # 禁用密码管理器弹窗
            cmd.append("--disable-features=PasswordManager")
            # 禁用"是否保存密码"弹窗
            cmd.append("--disable-save-password-bubble")

    cmd.append(url)
    return cmd


def launch_with_fallback(url: str, max_retries: int = 2) -> dict:
    """智能启动浏览器：默认无头，被拦截时自动回退到有头+反检测模式。

    参数：
        url: 目标网址
        max_retries: 最大重试次数（默认 2）

    返回 {ok, pid, port, mode, fallback}，fallback 为 True 表示使用了回退。
    """
    # 第一次尝试：无头模式（快速）
    result = launch(url, headed=False)
    if result.get("ok"):
        result["fallback"] = False
        return result

    # 无头模式失败，尝试有头+反检测模式
    for attempt in range(max_retries):
        result = launch(url, headed=True, stealth=True)
        if result.get("ok"):
            result["fallback"] = True
            result["output"] = (f"无头模式被拦截，已自动回退到有头+反检测模式"
                                f"（第 {attempt + 1} 次重试）。{result['output']}")
            return result

    return {"ok": False, "output": f"浏览器启动失败（已尝试无头+有头共 {max_retries + 1} 次）。"}


def _http_json(port: int, path: str):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=8) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _page_ws_url(port: int, browser_path: str = "") -> str:
    """返回页面级 target（type=page）的 WebSocket 地址。

    注意：不使用浏览器级 target——Page/Runtime 域命令在浏览器级 target 上不可用
    （会报 domain not supported）。页面 target 尚未出现时返回空字符串，由调用方有界重试。
    """
    targets = _http_json(port, "/json") or []
    pages = [t for t in targets if t.get("type") == "page"]
    return pages[0].get("webSocketDebuggerUrl", "") if pages else ""


def _get_session() -> BrowserSession:
    if _SESSION is None or _SESSION.proc is None:
        raise RuntimeError("尚未启动浏览器（请先 browser_launch）")
    return _SESSION


def navigate(url: str, inject_stealth: bool = True) -> dict:
    """当前受控浏览器跳转到 URL。

    参数：
        inject_stealth: 是否在页面加载后注入反检测脚本（默认 True）
    """
    url = (url or "").strip()
    if not _valid_nav_url(url):
        return {"ok": False, "output": "URL 无效：仅支持 http(s) 且长度 ≤2048。"}
    try:
        s = _get_session()
        if inject_stealth:
            # 使用 Page.addScriptToEvaluateOnNewDocument 在页面脚本执行前注入
            # 这比页面加载后注入更有效（防检测脚本先于页面脚本运行）
            try:
                s.command("Page.addScriptToEvaluateOnNewDocument", {"source": _STEALTH_SCRIPT}, timeout=10.0)
            except Exception:
                pass
        s.command("Page.navigate", {"url": url}, timeout=15.0)
        return {"ok": True, "output": f"浏览器已跳转：{url}"}
    except Exception as e:
        return {"ok": False, "output": f"浏览器跳转失败：{e}"}


_STEALTH_SCRIPT = """
(() => {
    // 1. 删除 webdriver 标志
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    try { delete navigator.__proto__.webdriver; } catch(e) {}

    // 2. 伪造 chrome 对象
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) {
        window.chrome.runtime = { connect: () => {}, sendMessage: () => {}, id: undefined };
    }
    if (!window.chrome.loadTimes) window.chrome.loadTimes = function() { return {}; };
    if (!window.chrome.csi) window.chrome.csi = function() { return {}; };
    if (!window.chrome.app) {
        window.chrome.app = {
            isInstalled: false,
            InstallState: {DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed'},
            RunningState: {CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running'},
            getDetails: () => {}, getIsInstalled: () => {},
            installState: () => 'not_installed', runningState: () => 'cannot_run',
        };
    }

    // 3. 伪造 plugins
    try {
        if (navigator.plugins && navigator.plugins.length === 0) {
            const fakePlugins = [
                {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
                {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''},
                {name: 'Native Client', filename: 'internal-nacl-plugin', description: ''},
            ];
            Object.defineProperty(navigator, 'plugins', {
                get: () => fakePlugins, configurable: true,
            });
        }
    } catch(e) {}

    // 4. 伪造 mimeTypes
    try {
        if (navigator.mimeTypes && navigator.mimeTypes.length === 0) {
            const fakeMimeTypes = [
                {type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format'},
                {type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: ''},
                {type: 'application/x-nacl', suffixes: '', description: 'Native Client Executable'},
            ];
            Object.defineProperty(navigator, 'mimeTypes', {
                get: () => fakeMimeTypes, configurable: true,
            });
        }
    } catch(e) {}

    // 5. 伪造 permissions API
    try {
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({state: Notification.permission}) :
                origQuery(parameters)
        );
    } catch(e) {}

    // 6. 隐藏 CDP 调试特征
    try {
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
    } catch(e) {}

    // 7. 伪造 connection
    try {
        if (navigator.connection) {
            Object.defineProperty(navigator.connection, 'rtt', {get: () => 50});
        }
    } catch(e) {}

    return 'ok';
})()
"""


def click(selector: str = "", text: str = "") -> dict:
    """在受控浏览器页面中点击元素（selector=Css 选择器，text=链接/按钮可见文本）。

    安全：selector/text 一律 json.dumps 成 JS 字符串字面量后再嵌入表达式，
    绝不直接拼接进 JS 源码（防 selector 注入任意脚本到受控页面）。
    """
    try:
        s = _get_session()
        if text:
            js = ("(() => { const els=[...document.querySelectorAll('a,button,input[type=button],"
                  "input[type=submit],[role=button]')]; const t=" + json.dumps(text, ensure_ascii=False) +
                  "; const el=els.find(e=>(e.innerText||e.value||'').trim().includes(t)); "
                  "if(!el) return {ok:false,msg:'NOT_FOUND'}; "
                  "el.scrollIntoView({block:'center'}); el.click(); "
                  "return {ok:true,msg:'已点击: '+(el.innerText||el.value||'').slice(0,80)}; })()")
        elif selector:
            js = ("(() => { const sel=" + json.dumps(selector, ensure_ascii=False) +
                  "; const el=document.querySelector(sel); "
                  "if(!el) return {ok:false,msg:'NOT_FOUND'}; "
                  "el.scrollIntoView({block:'center'}); el.click(); "
                  "return {ok:true,msg:'已点击: '+el.tagName}; })()")
        else:
            return {"ok": False, "output": "需要 selector 或 text 之一。"}
        r = s.command("Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=15.0)
        val = (r.get("result") or {}).get("value") or {}
        if not val.get("ok"):
            if str(val.get("msg") or "") == "NOT_FOUND":
                what = f"文本「{text[:80]}」" if text else f"选择器「{selector[:80]}」"
                return {"ok": False, "output": f"未找到{what}对应的可点击元素"}
            return {"ok": False, "output": str(val.get("msg") or "点击失败")}
        return {"ok": True, "output": str(val.get("msg") or "已点击")}
    except Exception as e:
        return {"ok": False, "output": f"浏览器点击失败：{e}"}


def type_text(text: str) -> dict:
    """向当前焦点元素输入文本（CDP Input.insertText，Unicode）。"""
    text = str(text or "")
    if not text:
        return {"ok": False, "output": "缺少要输入的文本。"}
    if len(text) > 2000:
        return {"ok": False, "output": "输入文本过长（≤2000 字符）。"}
    try:
        s = _get_session()
        s.command("Input.insertText", {"text": text}, timeout=15.0)
        return {"ok": True, "output": f"已输入 {len(text)} 个字符"}
    except Exception as e:
        return {"ok": False, "output": f"浏览器输入失败：{e}"}


_KEY_CODES = {
    "enter": ("Enter", "Enter", 13), "return": ("Enter", "Enter", 13),
    "tab": ("Tab", "Tab", 9), "esc": ("Escape", "Escape", 27), "escape": ("Escape", "Escape", 27),
    "backspace": ("Backspace", "Backspace", 8), "delete": ("Delete", "Delete", 46),
    "space": (" ", "Space", 32),
    "up": ("ArrowUp", "ArrowUp", 38), "down": ("ArrowDown", "ArrowDown", 40),
    "left": ("ArrowLeft", "ArrowLeft", 37), "right": ("ArrowRight", "ArrowRight", 39),
    "home": ("Home", "Home", 36), "end": ("End", "End", 35),
    "pageup": ("PageUp", "PageUp", 33), "pagedown": ("PageDown", "PageDown", 34),
    "f1": ("F1", "F1", 112), "f2": ("F2", "F2", 113), "f3": ("F3", "F3", 114),
    "f4": ("F4", "F4", 115), "f5": ("F5", "F5", 116), "f6": ("F6", "F6", 117),
    "f7": ("F7", "F7", 118), "f8": ("F8", "F8", 119), "f9": ("F9", "F9", 120),
    "f10": ("F10", "F10", 121), "f11": ("F11", "F11", 122), "f12": ("F12", "F12", 123),
}
_MOD_BITS = {"ctrl": 2, "control": 2, "alt": 1, "shift": 8, "meta": 4}


def press_keys(keys: str) -> dict:
    """按键：单个键（enter/tab/方向键/f1-f12 等）或组合键（ctrl,c）。"""
    parts = [k.strip().lower() for k in str(keys or "").split(",") if k.strip()]
    if not parts:
        return {"ok": False, "output": "缺少按键。"}
    unknown = [k for k in parts if k not in _KEY_CODES and k not in _MOD_BITS]
    if unknown:
        return {"ok": False, "output": f"不支持的按键: {', '.join(unknown)}（支持 enter/tab/esc/方向键/f1-f12/ctrl/shift/alt/meta 组合）"}
    try:
        s = _get_session()
        if len(parts) == 1 and parts[0] in _MOD_BITS:
            return {"ok": False, "output": "修饰键必须与普通键组合使用（如 ctrl,c）。"}
        mods = 0
        for k in parts:
            if k in _MOD_BITS:
                mods |= _MOD_BITS[k]
        if parts[-1] not in _KEY_CODES:
            return {"ok": False, "output": "组合键最后必须是普通键（如 ctrl,c）。"}
        key, code, vk = _KEY_CODES[parts[-1]]
        params = {"key": key, "code": code, "windowsVirtualKeyCode": vk,
                  "nativeVirtualKeyCode": vk, "modifiers": mods}
        s.command("Input.dispatchKeyEvent", {**params, "type": "keyDown"}, timeout=10.0)
        s.command("Input.dispatchKeyEvent", {**params, "type": "keyUp"}, timeout=10.0)
        return {"ok": True, "output": f"已按键 {', '.join(parts)}"}
    except Exception as e:
        return {"ok": False, "output": f"浏览器按键失败：{e}"}


def close() -> dict:
    """关闭受控浏览器并清理临时 profile。"""
    global _SESSION
    if _SESSION is None:
        return {"ok": False, "output": "当前没有受控浏览器。"}
    pid = _SESSION.pid
    _SESSION.close()
    _SESSION = None
    return {"ok": True, "output": f"已关闭受控浏览器（pid={pid}）。"}
