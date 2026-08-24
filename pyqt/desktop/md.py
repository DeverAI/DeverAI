"""迷你 Markdown → HTML 渲染器（供 QTextEdit 富文本使用，零第三方依赖）。
支持：标题 / 代码块(带语言) / 行内代码 / 粗体 / 斜体 / 列表 / 引用 / 分隔线 / 链接。
注意：输出内嵌到 Qt 富文本，不会执行脚本（安全）。
"""
import re
from html import escape

_HTML_ESCAPE = escape
_html_escape = escape  # 兼容别名


def _code_color(lang: str) -> str:
    return "#e3b341"


def _fence_to_html(code: str, lang: str) -> str:
    lang = (lang or "").strip().lower()
    # v4：Mermaid/HTML 专属卡片（本地无 JS 引擎，预览=样式化源码呈现，安全零依赖）
    if lang in ("mermaid", "html"):
        tag = "图表" if lang == "mermaid" else "页面"
        return (
            f'<div class="codeblock"><div class="codelang">{_html_escape(lang)} · {tag}源码（预览：本地不执行渲染）</div>'
            f'<pre style="color:#8be9fd;background:#0d1117;padding:8px 10px;'
            f'border-left:3px solid #6e5494;border-radius:4px;white-space:pre-wrap;'
            f'font-family:Consolas,monospace;font-size:12px;">{_html_escape(code)}</pre></div>'
        )
    color = _code_color(lang)
    return (
        f'<div class="codeblock"><div class="codelang">{_html_escape(lang)}</div>'
        f'<pre style="color:{color};background:#0d1117;padding:8px 10px;'
        f'border-left:3px solid #264f78;border-radius:4px;white-space:pre-wrap;'
        f'font-family:Consolas,monospace;font-size:12px;">{_html_escape(code)}</pre></div>'
    )


_INLINE_RE = re.compile(
    r"(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))"
)


def _fmt_mark(s: str) -> str:
    """处理一个已匹配的行内标记（内容未转义，返回的 HTML 直接使用）。"""
    if s.startswith("`"):  # 行内代码
        return f'<code style="background:#161c28;color:#c3e88d;padding:1px 5px;border-radius:4px;font-family:Consolas,monospace;">{_html_escape(s[1:-1])}</code>'
    if s.startswith("**"):  # 粗体
        return "<b>" + _inline(s[2:-2]) + "</b>"
    if s.startswith("*"):  # 斜体
        return "<i>" + _inline(s[1:-1]) + "</i>"
    if s.startswith("["):  # 链接 [t](url)
        t = s[s.find("[") + 1 : s.find("]")]
        u = s[s.find("(") + 1 : s.rfind(")")]
        return f'<a href="{_html_escape(u)}">{_html_escape(t)}</a>'
    return _html_escape(s)


def _inline(text: str) -> str:
    # 用 split 保留匹配段：未匹配的普通文本全部经 _html_escape，杜绝 HTML 注入
    parts = _INLINE_RE.split(text or "")
    out = []
    for i, part in enumerate(parts):
        if part is None:
            continue
        if i % 2 == 0:
            out.append(_html_escape(part))
        else:
            out.append(_fmt_mark(part))
    return "".join(out)


def _process_block(text: str) -> str:
    lines = text.split("\n")
    out = []
    i = 0
    list_tag = ""   # v6.1 P2 修正：记录实际列表标签（ul/ol），闭合时配对
    in_quote = False
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.lstrip()
        if stripped.startswith("```"):
            # 代码块
            lang = stripped[3:].strip()
            buf = []
            i += 1
            while i < n and not lines[i].lstrip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过闭合 ```
            if list_tag:
                out.append(f"</{list_tag}>")
                list_tag = ""
            if in_quote:
                out.append("</blockquote>")
                in_quote = False
            out.append(_fence_to_html("\n".join(buf), lang))
            continue
        if re.match(r"^#{1,6}\s", stripped):
            if list_tag:
                out.append(f"</{list_tag}>")
                list_tag = ""
            if in_quote:
                out.append("</blockquote>")
                in_quote = False
            level = len(stripped) - len(stripped.lstrip("#"))
            content = stripped[level:].strip()
            size = {1: "20px", 2: "17px", 3: "15px"}.get(level, "14px")
            out.append(f'<div style="font-size:{size};font-weight:bold;color:#7aa2ff;margin:8px 0 4px;">{_inline(content)}</div>')
            i += 1
            continue
        if re.match(r"^[-*+]\s", stripped):
            if list_tag != "ul":
                if list_tag:
                    out.append(f"</{list_tag}>")
                out.append("<ul style='margin:4px 0;'>")
                list_tag = "ul"
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            i += 1
            continue
        if re.match(r"^\d+[.)]\s", stripped):
            if list_tag != "ol":
                if list_tag:
                    out.append(f"</{list_tag}>")
                out.append("<ol style='margin:4px 0;'>")
                list_tag = "ol"
            _num_stripped = re.sub(r'^\d+[.)]\s', '', stripped)
            out.append(f"<li>{_inline(_num_stripped)}</li>")
            i += 1
            continue
        if stripped.startswith(">"):
            if not in_quote:
                out.append("<blockquote style='border-left:3px solid #5b8cff;margin:4px 0;padding:2px 10px;color:#8a94a8;'>")
                in_quote = True
            out.append(_inline(stripped.lstrip("> ")))
            i += 1
            continue
        if stripped in ("---", "***", "___"):
            if list_tag:
                out.append(f"</{list_tag}>")
                list_tag = ""
            if in_quote:
                out.append("</blockquote>")
                in_quote = False
            out.append("<hr style='border:none;border-top:1px solid #232c3d;margin:8px 0;'>")
            i += 1
            continue
        # 普通段落
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = ""
        if in_quote:
            out.append("</blockquote>")
            in_quote = False
        if stripped:
            out.append(f"<div style='margin:3px 0;'>{_inline(stripped)}</div>")
        i += 1
    if list_tag:
        out.append(f"</{list_tag}>")
    if in_quote:
        out.append("</blockquote>")
    return "\n".join(out)


def md_to_html(text: str) -> str:
    """将 Markdown 文本渲染为可嵌入 QTextEdit 的 HTML 片段。"""
    if not text:
        return ""
    return _process_block(str(text))
