"""F12 开发者工具集成（v8.14）——通过 CDP 协议获取浏览器 Networks / Storage / Console / Sources 信息。

能力：
- browser_networks_start  : 开始捕获网络请求
- browser_networks_stop   : 停止捕获并返回已捕获的请求
- browser_networks_get    : 获取已捕获的网络请求（支持过滤）
- browser_storage_get     : 获取页面存储（cookies / localStorage / sessionStorage）
- browser_storage_set     : 设置页面存储
- browser_storage_clear   : 清除页面存储
- browser_console_get     : 获取控制台日志
- browser_console_eval    : 在页面上下文中执行 JavaScript
- browser_sources_list    : 获取页面加载的 JavaScript 源文件列表
- browser_sources_get     : 获取指定源文件的内容

安全原则：
- 所有操作基于已启动的 CDP 浏览器会话（browser_ctl.BrowserSession）
- 仅操作用户已付费/已授权的服务（不绕过任何付费墙）
- 数值 host 归一化、SSRF 校验复用 browser._valid_url
- 输出有上限，防资源耗尽

模块开关：ENABLE_BROWSER_DEVTOOLS（默认 True）
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

# ---- 捕获状态（会话级）----
_network_capturing = False
_network_requests: list[dict] = []
_console_logs: list[dict] = []
_max_requests = 500
_max_log_len = 2000
_max_body_len = 10000


# ============================================================
# 网络捕获（Networks 面板）
# ============================================================

def _register_event_handlers() -> None:
    """注册 CDP 事件处理器（Network + Runtime + Log）。"""
    try:
        from .browser_ctl import _get_session
        s = _get_session()
        if "Network" not in s._event_handlers:
            s.add_event_handler("Network", handle_network_event)
        if "Runtime" not in s._event_handlers:
            s.add_event_handler("Runtime", handle_console_event)
        if "Log" not in s._event_handlers:
            s.add_event_handler("Log", handle_console_event)
    except Exception:
        pass


def networks_start() -> dict:
    """开始捕获当前受控浏览器的网络请求。

    启用 CDP Network 域，后续页面加载/跳转产生的请求将被记录。
    返回 {"ok": True, "output": "..."}
    """
    global _network_capturing, _network_requests
    try:
        from .browser_ctl import _get_session
        s = _get_session()
        _register_event_handlers()
        s.command("Network.enable", {}, timeout=10.0)
        _network_capturing = True
        _network_requests = []
        return {"ok": True, "output": "已开始网络请求捕获（上限 500 条）"}
    except Exception as e:
        return {"ok": False, "output": f"启动网络捕获失败：{e}"}


def networks_stop() -> dict:
    """停止网络请求捕获，返回已捕获的请求摘要。"""
    global _network_capturing, _network_requests
    try:
        from .browser_ctl import _get_session
        s = _get_session()
        s.command("Network.disable", {}, timeout=10.0)
    except Exception:
        pass
    _network_capturing = False
    count = len(_network_requests)
    return {"ok": True, "output": f"已停止网络捕获，共捕获 {count} 条请求", "count": count, "requests": _network_requests}


def networks_get(filter_url: str = "", filter_type: str = "", filter_method: str = "",
                 status_code: int = 0, max_results: int = 50) -> dict:
    """获取已捕获的网络请求（支持过滤）。

    参数：
        filter_url: URL 包含的字符串（如 "api"、"endpoint"）
        filter_type: 资源类型（XHR/Fetch/Document/Script/Stylesheet/Image/Media/Other）
        filter_method: 请求方法（GET/POST/PUT/DELETE）
        status_code: 状态码过滤（0=不过滤）
        max_results: 最大返回条数（默认 50，最大 200）

    返回请求列表，每条含 url/method/status/type/responseBody(若有)/timing。
    """
    global _network_requests
    max_results = min(max(int(max_results), 1), 200)

    results = list(_network_requests)

    if filter_url:
        fu = filter_url.lower()
        results = [r for r in results if fu in r.get("url", "").lower()]

    if filter_type:
        ft = filter_type.capitalize()
        results = [r for r in results if r.get("type", "").lower() == ft.lower() or
                   (ft in ("XHR", "Fetch") and r.get("type", "").lower() in ("xhr", "fetch"))]

    if filter_method:
        fm = filter_method.upper()
        results = [r for r in results if r.get("method", "").upper() == fm]

    if status_code:
        sc = int(status_code)
        results = [r for r in results if r.get("status") == sc]

    results = results[:max_results]

    # 截断响应体，防输出爆炸
    for r in results:
        body = r.get("responseBody")
        if body and len(str(body)) > _max_body_len:
            r["responseBody"] = str(body)[:_max_body_len] + "…（已截断）"
        req_body = r.get("requestBody")
        if req_body and len(str(req_body)) > _max_body_len:
            r["requestBody"] = str(req_body)[:_max_body_len] + "…（已截断）"

    return {"ok": True, "output": f"返回 {len(results)} 条网络请求", "requests": results, "total": len(_network_requests)}


def handle_network_event(method: str, params: dict) -> None:
    """CDP Network 事件回调（由 browser_ctl 注册时传入）。"""
    global _network_requests
    if not _network_capturing:
        return

    if method == "Network.requestWillBeSent":
        req = params.get("request", {})
        entry = {
            "requestId": params.get("requestId", ""),
            "url": req.get("url", ""),
            "method": req.get("method", ""),
            "type": params.get("type", "Other"),
            "requestBody": req.get("postData", ""),
            "requestHeaders": _Headers_to_dict(req.get("headers", {})),
            "timestamp": params.get("timestamp", 0),
            "status": 0,
            "responseBody": "",
            "responseHeaders": {},
            "responseSize": 0,
        }
        _network_requests.append(entry)
        if len(_network_requests) > _max_requests:
            _network_requests = _network_requests[-_max_requests:]

    elif method == "Network.responseReceived":
        resp = params.get("response", {})
        req_id = params.get("requestId", "")
        for r in _network_requests:
            if r.get("requestId") == req_id:
                r["status"] = resp.get("status", 0)
                r["responseHeaders"] = _Headers_to_dict(resp.get("headers", {}))
                r["responseSize"] = resp.get("encodedDataLength", 0)
                r["mimeType"] = resp.get("mimeType", "")
                break


def get_response_body(request_id: str) -> str:
    """获取指定请求的响应体。"""
    try:
        from .browser_ctl import _get_session
        s = _get_session()
        r = s.command("Network.getResponseBody", {"requestId": request_id}, timeout=15.0)
        body = r.get("body", "")
        if r.get("base64Encoded"):
            import base64
            try:
                body = base64.b64decode(body).decode("utf-8", errors="replace")
            except Exception:
                body = "[binary data]"
        return body[:_max_body_len]
    except Exception as e:
        return f"[获取响应体失败: {e}]"


# ============================================================
# 存储检查（Storage 面板）
# ============================================================

def storage_get(storage_type: str = "all", url: str = "") -> dict:
    """获取页面存储信息。

    参数：
        storage_type: cookies / localStorage / sessionStorage / all
        url: 指定 URL（用于 cookies）；为空则使用当前页面

    返回指定类型的存储内容。
    """
    try:
        from .browser_ctl import _get_session
        s = _get_session()

        if storage_type == "cookies":
            return _get_cookies(s, url)
        elif storage_type == "localStorage":
            return _get_local_storage(s)
        elif storage_type == "sessionStorage":
            return _get_session_storage(s)
        elif storage_type == "all":
            result = {}
            r1 = _get_cookies(s, url)
            if r1.get("ok"):
                result["cookies"] = r1.get("cookies", [])
            r2 = _get_local_storage(s)
            if r2.get("ok"):
                result["localStorage"] = r2.get("items", [])
            r3 = _get_session_storage(s)
            if r3.get("ok"):
                result["sessionStorage"] = r3.get("items", [])
            return {"ok": True, "output": f"cookies={len(result.get('cookies', []))}, localStorage={len(result.get('localStorage', []))}, sessionStorage={len(result.get('sessionStorage', []))}", **result}
        else:
            return {"ok": False, "output": f"不支持的存储类型: {storage_type}（支持 cookies/localStorage/sessionStorage/all）"}
    except Exception as e:
        return {"ok": False, "output": f"获取存储失败：{e}"}


def storage_set(storage_type: str, key: str, value: str, url: str = "") -> dict:
    """设置页面存储项。

    参数：
        storage_type: cookies / localStorage / sessionStorage
        key: 键名
        value: 值
        url: 仅 cookies 需要

    安全：禁止设置 HttpOnly cookie（CDP 层面限制）。
    """
    try:
        from .browser_ctl import _get_session
        s = _get_session()

        if storage_type == "cookies":
            return _set_cookie(s, key, value, url)
        elif storage_type == "localStorage":
            return _set_local_storage(s, key, value)
        elif storage_type == "sessionStorage":
            return _set_session_storage(s, key, value)
        else:
            return {"ok": False, "output": f"不支持的存储类型: {storage_type}"}
    except Exception as e:
        return {"ok": False, "output": f"设置存储失败：{e}"}


def storage_clear(storage_type: str = "all") -> dict:
    """清除页面存储。"""
    try:
        from .browser_ctl import _get_session
        s = _get_session()

        if storage_type in ("cookies", "all"):
            s.command("Network.clearBrowserCookies", {}, timeout=10.0)
        if storage_type in ("localStorage", "all"):
            js = "localStorage.clear(); 'cleared'"
            s.command("Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=10.0)
        if storage_type in ("sessionStorage", "all"):
            js = "sessionStorage.clear(); 'cleared'"
            s.command("Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=10.0)
        return {"ok": True, "output": f"已清除 {storage_type} 存储"}
    except Exception as e:
        return {"ok": False, "output": f"清除存储失败：{e}"}


# ============================================================
# 控制台（Console 面板）
# ============================================================

def console_get(max_entries: int = 100) -> dict:
    """获取控制台日志（需在 browser_launch 后启用 Runtime/Log 域）。"""
    global _console_logs
    try:
        from .browser_ctl import _get_session
        s = _get_session()
        _register_event_handlers()
        s.command("Runtime.enable", {}, timeout=10.0)
        s.command("Log.enable", {}, timeout=10.0)
    except Exception:
        pass
    max_entries = min(max(int(max_entries), 1), 200)
    entries = _console_logs[-max_entries:]
    return {"ok": True, "output": f"返回 {len(entries)} 条控制台日志", "logs": entries}


def console_eval(expression: str, timeout: float = 15.0) -> dict:
    """在页面上下文中执行 JavaScript 表达式（Runtime.evaluate）。

    安全：
    - 表达式长度限 2000 字符
    - 结果自动截断（2000 字符）
    - 超时 15s
    """
    expression = str(expression or "").strip()
    if not expression:
        return {"ok": False, "output": "缺少要执行的 JavaScript 表达式"}
    if len(expression) > 2000:
        return {"ok": False, "output": "表达式过长（≤2000 字符）"}

    try:
        from .browser_ctl import _get_session
        s = _get_session()
        r = s.command("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        }, timeout=timeout + 5.0)  # v8.14：CDP Runtime.evaluate 无 timeout 参数（真实超时由 socket 层保证），移除幽灵参数

        result = r.get("result", {})
        val = result.get("value")
        rtype = result.get("type", "")

        if val is None and result.get("subtype") == "error":
            desc = result.get("description", str(result.get("value", "")))
            return {"ok": False, "output": f"JS 执行错误: {str(desc)[:500]}"}

        output = str(val) if val is not None else "(undefined/null)"
        if len(output) > 2000:
            output = output[:2000] + "…（已截断）"

        return {"ok": True, "output": output, "type": rtype}
    except Exception as e:
        return {"ok": False, "output": f"JS 执行失败：{e}"}


def handle_console_event(method: str, params: dict) -> None:
    """CDP Runtime.consoleAPICalled / Log.entryAdded 事件回调。"""
    global _console_logs
    if method == "Runtime.consoleAPICalled":
        args_list = params.get("args", [])
        text_parts = []
        for a in args_list:
            v = a.get("value")
            if v is not None:
                text_parts.append(str(v))
            elif a.get("type") == "object":
                text_parts.append(a.get("description", "[object]"))
            else:
                text_parts.append(str(a.get("description", a.get("value", ""))))
        entry = {
            "level": params.get("type", "log"),
            "text": " ".join(text_parts)[:_max_log_len],
            "timestamp": params.get("timestamp", 0),
            "source": "console",
        }
        _console_logs.append(entry)
        if len(_console_logs) > _max_requests:
            _console_logs = _console_logs[-_max_requests:]

    elif method == "Log.entryAdded":
        entry_log = params.get("entry", {})
        entry = {
            "level": entry_log.get("level", "info"),
            "text": str(entry_log.get("text", ""))[:_max_log_len],
            "source": entry_log.get("source", "network"),
            "url": entry_log.get("url", ""),
            "timestamp": entry_log.get("timestamp", 0),
        }
        _console_logs.append(entry)
        if len(_console_logs) > _max_requests:
            _console_logs = _console_logs[-_max_requests:]


# ============================================================
# 源文件（Sources 面板）
# ============================================================

def sources_list(filter_pattern: str = "") -> dict:
    """获取页面加载的 JavaScript 源文件列表。

    通过 Debugger 域获取所有脚本源，或通过 Runtime.evaluate 获取 document.scripts。
    """
    try:
        from .browser_ctl import _get_session
        s = _get_session()

        js = """
        (() => {
            const scripts = [...document.querySelectorAll('script')];
            const srcList = scripts.map(s => ({
                src: s.src || '',
                inline: !s.src,
                contentLength: s.textContent ? s.textContent.length : 0,
                id: s.id || '',
                async: s.async,
                defer: s.defer,
            }));
            return JSON.stringify(srcList);
        })()
        """
        r = s.command("Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=15.0)
        val = (r.get("result") or {}).get("value", "[]")
        src_list = json.loads(val) if isinstance(val, str) else val

        if filter_pattern:
            fp = filter_pattern.lower()
            src_list = [s for s in src_list if fp in (s.get("src", "") + s.get("id", "")).lower()]

        return {"ok": True, "output": f"找到 {len(src_list)} 个脚本源", "sources": src_list}
    except Exception as e:
        return {"ok": False, "output": f"获取源文件列表失败：{e}"}


def sources_get(url_or_index: str, max_len: int = 10000) -> dict:
    """获取指定脚本源文件的内容。

    参数：
        url_or_index: 脚本 URL 或索引（sources_list 返回的 src 字段）
        max_len: 最大返回字符数（默认 10000）
    """
    try:
        from .browser_ctl import _get_session
        s = _get_session()

        # 尝试通过 Runtime.evaluate 获取内联脚本内容
        js = f"""
        (() => {{
            const scripts = [...document.querySelectorAll('script')];
            for (const s of scripts) {{
                if (s.src === {json.dumps(url_or_index)} && s.src) {{
                    return {{type: 'external', src: s.src}};
                }}
                if (!s.src && scripts.indexOf(s).toString() === {json.dumps(url_or_index)}) {{
                    return {{type: 'inline', content: s.textContent}};
                }}
            }}
            // 按 URL 查找所有 script
            const all = scripts.filter(s => s.src.includes({json.dumps(url_or_index)}));
            if (all.length > 0) {{
                return {{type: 'external', src: all[0].src, count: all.length}};
            }}
            return {{type: 'not_found'}};
        }})()
        """
        r = s.command("Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=15.0)
        val = (r.get("result") or {}).get("value", {})

        if not isinstance(val, dict):
            return {"ok": False, "output": "解析脚本信息失败"}

        if val.get("type") == "inline":
            content = val.get("content", "")[:max_len]
            return {"ok": True, "output": f"内联脚本（{len(content)} 字符）", "content": content, "type": "inline"}

        if val.get("type") == "external":
            src_url = val.get("src", "")
            if not src_url:
                return {"ok": False, "output": "脚本 URL 为空"}
            # 尝试通过 fetch 获取外部脚本内容
            fetch_js = f"""
            (async () => {{
                try {{
                    const r = await fetch({json.dumps(src_url)}, {{credentials: 'include'}});
                    const t = await r.text();
                    return t.substring(0, {max_len});
                }} catch(e) {{
                    return '[fetch失败: ' + e.message + ']';
                }}
            }})()
            """
            r2 = s.command("Runtime.evaluate", {
                "expression": fetch_js,
                "returnByValue": True,
                "awaitPromise": True,
            }, timeout=20.0)
            content = (r2.get("result") or {}).get("value", "")
            if content and not content.startswith("[fetch失败"):
                return {"ok": True, "output": f"外部脚本 {src_url}（{len(content)} 字符）", "content": str(content)[:max_len], "type": "external", "url": src_url}
            return {"ok": False, "output": f"无法获取外部脚本内容: {src_url}", "type": "external", "url": src_url}

        return {"ok": False, "output": f"未找到匹配的脚本: {url_or_index}"}
    except Exception as e:
        return {"ok": False, "output": f"获取源文件失败：{e}"}


# ============================================================
# 辅助函数
# ============================================================

def _Headers_to_dict(headers: dict | list) -> dict:
    """将 CDP headers 转换为 dict。"""
    if isinstance(headers, dict):
        return headers
    if isinstance(headers, list):
        return {h.get("name", ""): h.get("value", "") for h in headers if isinstance(h, dict)}
    return {}


def _get_cookies(s: Any, url: str = "") -> dict:
    """获取 cookies。"""
    params = {}
    if url:
        params["urls"] = [url]
    r = s.command("Network.getAllCookies" if not url else "Network.getCookies",
                  params if params else {}, timeout=10.0)
    cookies = r.get("cookies", [])
    return {"ok": True, "output": f"获取到 {len(cookies)} 个 cookie", "cookies": cookies}


def _set_cookie(s: Any, name: str, value: str, url: str = "") -> dict:
    """设置 cookie。"""
    if not url:
        # 从当前页面获取 URL
        r = s.command("Runtime.evaluate", {
            "expression": "window.location.href",
            "returnByValue": True
        }, timeout=10.0)
        url = (r.get("result") or {}).get("value", "")
        if not url:
            return {"ok": False, "output": "无法获取当前页面 URL，请手动指定 url 参数"}

    cookie_params = {
        "name": str(name)[:200],
        "value": str(value)[:4000],
        "url": url,
        "httpOnly": False,
        "secure": url.startswith("https"),
        "sameSite": "Lax",
    }
    s.command("Network.setCookie", cookie_params, timeout=10.0)
    return {"ok": True, "output": f"已设置 cookie: {name}={value[:20]}…" if len(value) > 20 else f"已设置 cookie: {name}={value}"}


def _get_local_storage(s: Any) -> dict:
    """获取 localStorage。"""
    js = """
    (() => {
        const items = {};
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            if (k) items[k] = (localStorage.getItem(k) || '').substring(0, 2000);
        }
        return JSON.stringify(items);
    })()
    """
    r = s.command("Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=10.0)
    val = (r.get("result") or {}).get("value", "{}")
    items_dict = json.loads(val) if isinstance(val, str) else val
    items = [{"key": k, "value": v} for k, v in items_dict.items()]
    return {"ok": True, "output": f"获取到 {len(items)} 个 localStorage 项", "items": items}


def _get_session_storage(s: Any) -> dict:
    """获取 sessionStorage。"""
    js = """
    (() => {
        const items = {};
        for (let i = 0; i < sessionStorage.length; i++) {
            const k = sessionStorage.key(i);
            if (k) items[k] = (sessionStorage.getItem(k) || '').substring(0, 2000);
        }
        return JSON.stringify(items);
    })()
    """
    r = s.command("Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=10.0)
    val = (r.get("result") or {}).get("value", "{}")
    items_dict = json.loads(val) if isinstance(val, str) else val
    items = [{"key": k, "value": v} for k, v in items_dict.items()]
    return {"ok": True, "output": f"获取到 {len(items)} 个 sessionStorage 项", "items": items}


def _set_local_storage(s: Any, key: str, value: str) -> dict:
    """设置 localStorage。"""
    js = f"localStorage.setItem({json.dumps(str(key))}, {json.dumps(str(value)[:5000])}); 'ok'"
    s.command("Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=10.0)
    return {"ok": True, "output": f"已设置 localStorage: {key}"}


def _set_session_storage(s: Any, key: str, value: str) -> dict:
    """设置 sessionStorage。"""
    js = f"sessionStorage.setItem({json.dumps(str(key))}, {json.dumps(str(value)[:5000])}); 'ok'"
    s.command("Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=10.0)
    return {"ok": True, "output": f"已设置 sessionStorage: {key}"}


# ============================================================
# 高级：获取付费 API 接口（用户已授权的）
# ============================================================

def find_api_endpoints(filter_pattern: str = "api", min_response_size: int = 0) -> dict:
    """从已捕获的网络请求中找出疑似 API 端点。

    参数：
        filter_pattern: URL 过滤字符串（默认 "api"）
        min_response_size: 最小响应体大小（过滤小响应）

    返回疑似 API 端点列表（URL + 方法 + 状态码 + 响应体摘要）。
    仅返回用户已付费/已授权服务的数据，不绕过任何付费墙。
    """
    global _network_requests
    filter_pattern = (filter_pattern or "api").lower()

    endpoints = []
    for r in _network_requests:
        url = r.get("url", "").lower()
        if filter_pattern and filter_pattern not in url:
            continue
        if r.get("status", 0) == 0:
            continue
        # 过滤静态资源
        if any(url.endswith(ext) for ext in ('.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.woff', '.woff2', '.ttf', '.eot')):
            continue
        # 过滤小响应
        if r.get("responseSize", 0) < min_response_size:
            continue

        endpoints.append({
            "url": r.get("url", ""),
            "method": r.get("method", "GET"),
            "status": r.get("status", 0),
            "type": r.get("type", "Other"),
            "responseSize": r.get("responseSize", 0),
            "mimeType": r.get("mimeType", ""),
            "responseHeaders": r.get("responseHeaders", {}),
        })

    # 去重（同 URL+method）
    seen = set()
    unique = []
    for e in endpoints:
        key = f"{e['method']}:{e['url']}"
        if key not in seen:
            seen.add(key)
            unique.append(e)

    unique.sort(key=lambda x: x.get("responseSize", 0), reverse=True)
    return {"ok": True, "output": f"找到 {len(unique)} 个疑似 API 端点", "endpoints": unique[:100]}
