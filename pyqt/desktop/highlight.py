"""轻量语法高亮器：支持 Python / JS/TS / JSON / YAML / HTML / CSS / Markdown / Shell。
用于编辑器 QPlainTextEdit。基于 QSyntaxHighlighter，零第三方依赖。
"""
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtCore import QRegularExpression

KEYWORDS = {
    "python": {
        "kw": r"\b(?:def|class|if|elif|else|for|while|return|import|from|as|try|except|finally|with|lambda|yield|global|nonlocal|pass|break|continue|raise|assert|del|in|is|not|and|or|None|True|False|async|await|match|case)\b",
        "builtins": r"\b(?:print|len|range|str|int|float|list|dict|set|tuple|open|type|isinstance|enumerate|zip|map|filter|sum|min|max|abs|round|sorted|reversed|super|self|cls|Exception|ValueError|TypeError|KeyError|RuntimeError|OSError)\b",
        "string": r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|f"(?:[^"\\]|\\.)*"|f\'(?:[^\'\\]|\\.)*\'',
        "comment": r"#[^\n]*",
        "number": r"\b\d+(?:\.\d+)?[jJ]?\b",
    },
    "javascript": {
        "kw": r"\b(?:const|let|var|function|return|if|else|for|while|switch|case|break|continue|new|class|extends|super|import|export|from|async|await|try|catch|finally|throw|typeof|instanceof|this|null|undefined|true|false|of|in|delete|yield)\b",
        "builtins": r"\b(?:console|document|window|Math|JSON|Object|Array|String|Number|Promise|Set|Map|require|module)\b",
        "string": r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`',
        "comment": r"//[^\n]*",
        "number": r"\b\d+(?:\.\d+)?\b",
    },
    "json": {
        "kw": r"\b(?:true|false|null)\b",
        "string": r'"(?:[^"\\]|\\.)*"',
        "number": r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?",
        "comment": r"//[^\n]*",
    },
    "yaml": {
        "kw": r"(?:^|\s)(?:true|false|null|yes|no|on|off)(?:\s|$)",
        "string": r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'',
        "comment": r"#[^\n]*",
        "number": r"\b\d+(?:\.\d+)?\b",
    },
    "html": {
        "tag": r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^>]*)?/?>",
        "string": r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'',
        "comment": r"<!--[\s\S]*?-->",
    },
    "css": {
        "property": r"[a-zA-Z-]+(?=\s*:)",
        "string": r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'',
        "comment": r"/\*[\s\S]*?\*/",
        "number": r"\b\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw|s|ms)?\b",
    },
    "markdown": {
        "header": r"^#{1,6}\s.*$",
        "bold": r"\*\*.*?\*\*|__.*?__",
        "code": r"`[^`]*`",
        "string": r"\[[^\]]*\]\([^)]*\)",
    },
    "shell": {
        "kw": r"\b(?:echo|cd|ls|pwd|mkdir|rm|cp|mv|cat|grep|find|git|npm|pip|python|curl|wget|export|source|if|then|else|fi|for|while|do|done|function|exit|sudo)\b",
        "string": r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'',
        "comment": r"#[^\n]*",
    },
}

_COLORS = {
    "kw": "#c792ea",
    "builtins": "#7ee787",
    "string": "#c3e88d",
    "comment": "#5c6774",
    "number": "#f78c6c",
    "tag": "#ff7b72",
    "property": "#79c0ff",
    "header": "#ffa657",
    "bold": "#dbe2f0",
}


class BaseHighlighter(QSyntaxHighlighter):
    def __init__(self, doc, lang="python"):
        super().__init__(doc)
        self.set_lang(lang)

    def set_lang(self, lang):
        self._rules = []
        spec = KEYWORDS.get(lang)
        if not spec:
            spec = KEYWORDS["python"]
        for kind, pattern in spec.items():
            if kind == "string":
                color = _COLORS["string"]
            elif kind == "comment":
                color = _COLORS["comment"]
            elif kind == "number":
                color = _COLORS["number"]
            elif kind == "kw":
                color = _COLORS["kw"]
            elif kind in ("tag", "property", "header", "bold"):
                color = _COLORS.get(kind, _COLORS["builtins"])
            else:
                color = _COLORS["builtins"]
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if kind in ("kw", "builtins"):
                fmt.setFontWeight(QFont.Weight.Bold)
            try:
                rx = QRegularExpression(pattern)
            except Exception:
                continue
            self._rules.append((rx, fmt))
        self.rehighlight()

    def highlightBlock(self, text):
        for rx, fmt in self._rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)


def lang_for(path: str) -> str:
    ext = (path or "").rsplit(".", 1)[-1].lower() if "." in (path or "") else ""
    mapping = {
        "py": "python", "js": "javascript", "ts": "javascript", "jsx": "javascript", "tsx": "javascript",
        "json": "json", "yaml": "yaml", "yml": "yaml", "html": "html", "htm": "html",
        "css": "css", "scss": "css", "md": "markdown", "sh": "shell", "bash": "shell",
        "bat": "shell", "ps1": "shell",
    }
    return mapping.get(ext, "python")
