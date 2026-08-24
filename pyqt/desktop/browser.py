"""超轻量浏览器控制（v6.6）：复用系统自带 Edge/Chrome 的 headless 模式，零第三方依赖。

能力：
- browser_open      真实打开浏览器（用户可见，可交互）—— 外交型任务：全放行但 AI 检测不符即拦截
- browser_read      无头抓取页面渲染后 DOM（JS 执行后的真实内容，比 httpx 强）
- browser_screenshot 无头截图保存到工作区

设计原则：
- 不引入 playwright/selenium 等重依赖（奥卡姆剃刀）。
- 浏览器可执行文件仅在 Windows 常见路径查找，找不到时给出明确错误。
- 子进程全部有超时；输出有上限，防资源耗尽。
"""
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import webbrowser
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

_BROWSER_EXE = ""


def find_browser() -> str:
    """返回系统可用的无头浏览器可执行文件路径（Edge/Chrome/Chromium），找不到返回 ""。"""
    global _BROWSER_EXE
    if _BROWSER_EXE:
        return _BROWSER_EXE
    candidates = [
        shutil.which("msedge"),
        shutil.which("chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            _BROWSER_EXE = c
            return c
    return ""


def _normalize_numeric_ip(host: str):
    """按浏览器 GURL 语义归一化「数值形态 host」为点分十进制；非数值形态返回 None。

    v8.7 审查修复：inet_aton 不识别十六进制/混合形式（0x7f000001、0x7f.0.0.1），
    而无头浏览器会按数值 IP 解析 → SSRF 旁路。此处先归一化再判环回/内网。
    """
    h = (host or "").lower()
    if not h or not re.fullmatch(r"[0-9a-fx.]+", h):
        return None
    parts = h.split(".")
    if len(parts) > 4 or any(not p for p in parts):
        return None
    vals = []
    for p in parts:
        try:
            v = int(p, 16) if "x" in p else int(p, 10)
        except ValueError:
            return None
        if not 0 <= v <= 0xFFFFFFFF:
            return None
        vals.append(v)
    try:
        if len(vals) == 1:
            v = vals[0]
            out = [(v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF]
        elif len(vals) == 2:
            a, b = vals
            if a > 0xFF or b > 0xFFFFFF:
                return None
            out = [a, (b >> 16) & 0xFF, (b >> 8) & 0xFF, b & 0xFF]
        elif len(vals) == 3:
            a, b, c = vals
            if a > 0xFF or b > 0xFF or c > 0xFFFF:
                return None
            out = [a, b, (c >> 8) & 0xFF, c & 0xFF]
        else:
            if any(v > 0xFF for v in vals):
                return None
            out = vals
    except Exception:
        return None
    return ".".join(str(v) for v in out)


def _host_ip(host: str):
    """把 host 文本解析为 ipaddress 对象；支持十六进制/旧式/缩写写法。"""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    norm = _normalize_numeric_ip(host)
    if norm:
        try:
            return ipaddress.ip_address(norm)
        except ValueError:
            pass
    try:
        return ipaddress.ip_address(socket.inet_aton(host))
    except OSError:
        return None


def _is_loopback(host: str) -> bool:
    h = (host or "").lower().strip()
    if h in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "::") or h.endswith(".localhost"):
        return True
    a = _host_ip(h)
    if a is not None:
        return a.is_loopback
    try:
        infos = socket.getaddrinfo(h, None)
    except OSError:
        return False
    # 混合解析安全：任一结果命中环回即拒绝（any 而非 all——all 语义下
    # 「公网 + 环回」双解析可绕过拦截，而浏览器可能连到环回那一条）
    return bool(infos) and any(ipaddress.ip_address(i[4][0]).is_loopback for i in infos)


def _is_private_nonloopback(host: str) -> bool:
    """判断 host 是否为非环回的私有/内网/链路本地/保留地址（DNS 解析任一命中即拒绝，防 rebinding）。"""
    a = _host_ip(host)
    if a is not None:
        return (a.is_private or a.is_link_local or a.is_unspecified or a.is_reserved
                or not a.is_global) and not a.is_loopback
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(infos) and any(
        (ipaddress.ip_address(i[4][0]).is_private
         or ipaddress.ip_address(i[4][0]).is_link_local
         or ipaddress.ip_address(i[4][0]).is_unspecified
         or ipaddress.ip_address(i[4][0]).is_reserved
         or not ipaddress.ip_address(i[4][0]).is_global)
        and not ipaddress.ip_address(i[4][0]).is_loopback
        for i in infos
    )


def _valid_url(url: str, allow_private: bool = False) -> bool:
    """URL 白名单校验：仅 http/https、长度受限、禁控制字符。

    v8.5.x 审查修复：无头抓取/截图默认拒绝回环/内网/链路本地/保留地址（防 SSRF——
    无头浏览器会执行 JS，可被诱导抓取云元数据 169.254.169.254 或内网服务）。
    browser_open 打开的是用户自己的可见浏览器，风险低，显式传 allow_private=True 放行。
    """
    url = (url or "").strip()
    if not url or len(url) > 2048:
        return False
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return False
    if " " in url or any(ch in url for ch in "\r\n\t"):
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if allow_private:
        return True
    if _is_private_nonloopback(host) or _is_loopback(host):
        return False
    return True


def _headless_args(exe: str, extra: list, user_data_dir: str = "") -> list:
    """组装 headless 启动参数：临时用户目录 + 静音 + 禁用 GPU，防止污染真实浏览器。

    v8.5.x 审查修复：user_data_dir 由调用方传入唯一临时目录（并发互斥 + 执行后清理），
    避免固定路径并发争用同一 profile 或长期膨胀。
    """
    udd = user_data_dir or os.path.join(os.environ.get("TEMP", "/tmp"), "deverai_headless")
    return [
        exe,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--mute-audio",
        "--disable-extensions",
        "--user-data-dir=" + udd,
    ] + extra


# v8.7 审查修复：--dump-dom 输出上限（防超大/恶意页面打爆内存）+ 非 Windows 常量兼容
_MAX_STDOUT_BYTES = 8 * 1024 * 1024
_MAX_STDERR_BYTES = 4 * 1024


def _kill_tree(proc) -> None:
    """终止浏览器进程树（headless Chrome 会派生 renderer/GPU 等孙进程，仅 kill 根进程会残留）。"""
    try:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_browser(args: list, timeout: int) -> tuple:
    """运行无头浏览器并限量读取输出。返回 (returncode, stdout_bytes, stderr_bytes)。

    流式读取且 stdout 超上限即 kill 浏览器（防内存尖峰）；超时抛 subprocess.TimeoutExpired；
    非 Windows 上 CREATE_NO_WINDOW 不存在时自动忽略。
    """
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    out_buf: list = []
    err_buf: list = []

    def _drain(stream, buf, cap):
        try:
            total = 0
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total <= cap:
                    buf.append(chunk)
                else:
                    try:  # 超出上限：终止浏览器进程树，防继续生成超大 DOM
                        _kill_tree(proc)
                    except Exception:
                        pass
                    break
        except Exception:
            pass

    t1 = threading.Thread(target=_drain, args=(proc.stdout, out_buf, _MAX_STDOUT_BYTES), daemon=True)
    t2 = threading.Thread(target=_drain, args=(proc.stderr, err_buf, _MAX_STDERR_BYTES), daemon=True)
    t1.start()
    t2.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            _kill_tree(proc)
        except Exception:
            pass
        raise
    finally:
        t1.join(5)
        t2.join(5)
    return (proc.returncode,
            b"".join(out_buf)[:_MAX_STDOUT_BYTES],
            b"".join(err_buf)[:_MAX_STDERR_BYTES])


class _TextExtractor(HTMLParser):
    """极简 HTML → 文本提取（只取正文可见文字，去脚本/样式/标签）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "head"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if t:
                self.parts.append(t)

    def text(self) -> str:
        return "\n".join(self.parts)


def _dom_to_text(html: str, limit: int = 20000) -> str:
    """HTML → 纯文本（压缩空白），超长截断。"""
    p = _TextExtractor()
    try:
        p.feed(html or "")
    except Exception:
        pass
    text = re.sub(r"[ \t\u3000]+", " ", "\n".join(l.strip() for l in "\n".join(p.parts).splitlines() if l.strip()))
    if len(text) > limit:
        text = text[:limit] + "\n...(截断)"
    return text


def browser_open(url: str) -> dict:
    """真实浏览器打开 URL（用户可见、可交互）。返回结果。"""
    url = (url or "").strip()
    if not _valid_url(url, allow_private=True):
        return {"ok": False, "output": "URL 无效：仅支持 http(s) 且长度 ≤2048。"}
    try:
        opened = webbrowser.open(url, new=2, autoraise=True)
        if not opened:
            return {"ok": False, "output": "未能打开系统浏览器（可能无默认浏览器关联）。"}
        return {"ok": True, "output": f"已在系统浏览器打开：{url}"}
    except Exception as e:
        return {"ok": False, "output": f"打开浏览器失败：{e}"}


def browser_read(url: str, timeout: int = 30) -> dict:
    """无头渲染页面并提取正文文本（JS 执行后的真实内容）。"""
    url = (url or "").strip()
    if not _valid_url(url):
        return {"ok": False, "output": "URL 无效：仅支持 http(s) 且长度 ≤2048。"}
    exe = find_browser()
    if not exe:
        return {"ok": False, "output": "未找到 Edge/Chrome/Chromium，无法无头抓取。"}
    try:
        timeout = min(max(int(timeout or 30), 5), 90)
    except (TypeError, ValueError):
        timeout = 30
    with tempfile.TemporaryDirectory(prefix="deverai_headless_") as udd:
        try:
            rc, out, err = _run_browser(
                _headless_args(exe, ["--virtual-time-budget=3000", "--dump-dom", url], udd),
                timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": f"页面加载超时（{timeout}s）。"}
        except OSError as e:
            return {"ok": False, "output": f"启动浏览器失败：{e}"}
    if rc != 0:
        return {"ok": False, "output": f"浏览器退出码 {rc}：{err.decode('utf-8', 'replace')[:400]}"}
    text = _dom_to_text(out.decode("utf-8", "replace"))
    if not text:
        return {"ok": False, "output": "页面无可提取文本（可能被 JS 阻断或反爬）。"}
    return {"ok": True, "output": f"[网页内容 {url}]\n{text}"}


def browser_screenshot(url: str, path: str, timeout: int = 30) -> dict:
    """无头截图保存到本地文件（path 为绝对路径或相对工作区路径，调用方负责 resolve）。"""
    url = (url or "").strip()
    if not _valid_url(url):
        return {"ok": False, "output": "URL 无效：仅支持 http(s) 且长度 ≤2048。"}
    if not (path or "").strip():
        return {"ok": False, "output": "缺少保存路径。"}
    exe = find_browser()
    if not exe:
        return {"ok": False, "output": "未找到 Edge/Chrome/Chromium，无法截图。"}
    try:
        timeout = min(max(int(timeout or 30), 5), 90)
    except (TypeError, ValueError):
        timeout = 30
    with tempfile.TemporaryDirectory(prefix="deverai_headless_") as udd:
        try:
            rc, out, err = _run_browser(
                _headless_args(exe, ["--virtual-time-budget=2000", "--window-size=1280,800",
                                     f"--screenshot={path}", url], udd),
                timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": f"截图超时（{timeout}s）。"}
        except OSError as e:
            return {"ok": False, "output": f"启动浏览器失败：{e}"}
    if rc != 0:
        return {"ok": False, "output": f"浏览器退出码 {rc}：{err.decode('utf-8', 'replace')[:400]}"}
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return {"ok": False, "output": "截图失败：未生成文件。"}
    return {"ok": True, "output": f"截图已保存：{p.name}（{p.stat().st_size} 字节）"}


# ---------------------------------------------------------------------------
# v8.6 界面转可点击点：无头抓取渲染后 DOM，抽取 a/button/input/select 等
# 可交互元素，返回带 index 的结构化列表，供 AI 理解"别人产品长什么样、能点什么"。
# ---------------------------------------------------------------------------
_VOID_TAGS = {"input", "img", "br", "hr", "meta", "link"}
# v8.7 审查修复：单元素内文本上限（防巨长按钮/链接文本撑爆工具输出与上下文）
_MAX_ELEM_TEXT = 500


def _is_clickable(tag: str, attrs: dict) -> bool:
    tag = (tag or "").lower()
    if tag in ("a", "button", "select", "textarea", "option"):
        return True
    if tag == "input":
        return (attrs.get("type") or "text").lower() in {
            "button", "submit", "text", "password", "email", "search",
            "tel", "url", "number", "checkbox", "radio", "file"}
    if attrs.get("onclick") or (attrs.get("role") or "").lower() == "button":
        return True
    return False


class _ClickableExtractor(HTMLParser):
    """抽取可交互元素（保留标签/属性/内文本），供 AI 生成可点击点清单。"""

    def __init__(self, limit: int = 300):
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.items = []
        self._cur = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if len(self.items) >= self.limit:
            return
        a = dict(attrs)
        t = (tag or "").lower()
        if not _is_clickable(t, a):
            return
        cap = _MAX_ELEM_TEXT

        def _s(k):
            return str(a.get(k) or "")[0:cap]

        item = {
            "tag": t,
            "type": _s("type").lower(),
            "href": _s("href"),
            "name": _s("name"),
            "id": _s("id"),
            "placeholder": _s("placeholder"),
            "aria_label": _s("aria-label"),
            "value": _s("value"),
            "text": "",
        }
        if t in _VOID_TAGS:
            item["text"] = item["value"] or item["placeholder"] or ""
            self.items.append(item)
            return
        self._cur = item
        self._buf = []

    def handle_data(self, data):
        if self._cur is not None:
            # v8.7 审查修复：单元素文本累计上限，防恶意页面巨长文本撑爆内存/上下文
            if sum(len(s) for s in self._buf) >= _MAX_ELEM_TEXT:
                return
            s = data.strip()
            if s:
                self._buf.append(s[:_MAX_ELEM_TEXT])

    def handle_endtag(self, tag):
        if self._cur is not None and (tag or "").lower() == self._cur["tag"]:
            self._cur["text"] = " ".join(self._buf).strip()
            self.items.append(self._cur)
            self._cur = None
            self._buf = []


def browser_elements(url: str, timeout: int = 30) -> dict:
    """无头渲染页面并抽取可交互元素（点击点），返回带 index 的结构化列表。"""
    url = (url or "").strip()
    if not _valid_url(url):
        return {"ok": False, "output": "URL 无效：仅支持 http(s) 且长度 ≤2048。"}
    exe = find_browser()
    if not exe:
        return {"ok": False, "output": "未找到 Edge/Chrome/Chromium，无法提取可点击元素。"}
    try:
        timeout = min(max(int(timeout or 30), 5), 90)
    except (TypeError, ValueError):
        timeout = 30
    with tempfile.TemporaryDirectory(prefix="deverai_headless_") as udd:
        try:
            rc, out, err = _run_browser(
                _headless_args(exe, ["--virtual-time-budget=3000", "--dump-dom", url], udd),
                timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": f"页面加载超时（{timeout}s）。"}
        except OSError as e:
            return {"ok": False, "output": f"启动浏览器失败：{e}"}
    if rc != 0:
        return {"ok": False, "output": f"浏览器退出码 {rc}：{err.decode('utf-8', 'replace')[:400]}"}
    ex = _ClickableExtractor()
    try:
        ex.feed(out.decode("utf-8", "replace"))
    except Exception:
        pass
    if not ex.items:
        return {"ok": False, "output": "页面未找到可交互元素（可能被 JS 阻断或需交互后才渲染）。"}
    out = []
    for i, it in enumerate(ex.items):
        label = (it["text"] or it["value"] or it["placeholder"] or it["aria_label"]
                 or it["href"] or it["name"] or it["id"] or "").strip()
        out.append({"index": i, **it, "label": label})
    return {"ok": True, "elements": out, "count": len(out),
            "output": f"共抽取 {len(out)} 个可交互元素（index 从 0 开始）。"}
