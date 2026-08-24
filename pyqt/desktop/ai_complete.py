"""Cursor 式 AI 智能能力：Inline 自动补全 + 选区 AI 操作（解释/优化/找Bug/自定义）。
后台线程调 LLM，信号回主线程。零第三方 UI 依赖。
"""
import asyncio

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from .config import Config
from .llm import chat_complete, stream_chat

# 已停止但仍在线程中运行的 SelectionRunner 保活集合：
# 防止关闭对话框后 Python GC 活销毁仍在运行的 QThread
_STOPPED_RUNNERS = set()


# ---------------------------------------------------------------------------
# Inline 自动补全（v4：逐行流式 + 独立补全模型）
# ---------------------------------------------------------------------------
def _completion_cfg(cfg: Config) -> Config:
    """补全专用配置：complete_model 为空则用主模型；限小 max_tokens。"""
    c = Config()
    c.api_base_url = cfg.api_base_url
    c.api_key = cfg.api_key
    c.model = (getattr(cfg, "complete_model", "") or cfg.model or "").strip()
    c.temperature = 0.2
    c.max_tokens = 300
    return c


def build_completion_prompt(cfg: Config, lang: str, prefix: str) -> str:
    return (
        "你是代码自动补全引擎。根据【光标前代码】补全后续代码。\n"
        "规则：\n"
        "1. 只输出补全内容本身，不要解释、不要重复光标前已有的代码。\n"
        "2. 保持缩进与语言习惯，补全要自然、可编译/可运行。\n"
        "3. 逐行自然延续；如果无法合理补全，输出空内容。\n"
        f"语言: {lang or '未知'}\n\n【光标前代码】\n{prefix}"
    )


def request_completion(cfg: Config, lang: str, prefix: str, max_tokens: int = 80) -> str:
    """同步请求补全（在后台线程中调用，非流式）。"""
    try:
        # chat_complete 是 async 协程，必须在 QThread 内 asyncio.run 驱动
        msg = asyncio.run(
            chat_complete(
                _completion_cfg(cfg),
                [
                    {"role": "system", "content": build_completion_prompt(cfg, lang, prefix[:4000])},
                    {"role": "user", "content": "请补全"},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
                timeout=30.0,
            )
        )
        text = (msg.get("content") or "").strip()
        return text
    except Exception as e:
        # 补全失败不打断主流程，但必须落 Err.log（错误不得吞没）
        from .errors import log_error
        log_error("Inline 补全请求失败", e)
        return ""


async def stream_completion_lines(cfg: Config, lang: str, prefix: str):
    """逐行流式补全：异步生成器，每凑齐一行（含行尾） yield 一次，末尾残段最后 yield。

    调用方在文档无变化时不得打断展示（UI 层保证）。
    """
    buf = ""
    async for ev in stream_chat(
        _completion_cfg(cfg),
        [
            {"role": "system", "content": build_completion_prompt(cfg, lang, prefix[:4000])},
            {"role": "user", "content": "请补全"},
        ],
        temperature=0.2,
        max_tokens=300,
    ):
        if ev["type"] != "delta":
            continue
        buf += ev["content"]
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            yield line + "\n"
    if buf.strip():
        yield buf


# ---------------------------------------------------------------------------
# 选区 AI 操作
# ---------------------------------------------------------------------------
AI_ACTIONS = {
    "解释": "用简洁中文解释这段代码的功能、关键逻辑与潜在问题。",
    "优化": "优化这段代码：提高可读性与性能，保持行为一致。只输出优化后的完整代码，并在代码块后简要说明改了什么。",
    "找Bug": "审查这段代码，指出 bug、边界问题与风险，并给出修复建议与修复后的代码。",
    "写注释": "为这段代码补充清晰的中文注释。只输出注释后的完整代码。",
    "生成测试": "为这段代码生成单元测试（用 pytest）。只输出测试代码。",
    "改写成…": "按用户自定义指令改写。",
}


def build_selection_prompt(instruction: str, code: str) -> str:
    return (
        "你是资深工程师。根据用户的指令处理给定的代码。\n"
        "要求：\n"
        "1. 指令要求输出代码时，只输出完整代码（可带简短说明），不要多余解释。\n"
        "2. 指令要求解释/审查时，用简洁中文分点说明。\n"
        f"【用户指令】\n{instruction}\n\n【代码】\n```\n{code}\n```"
    )


class SelectionRunner(QThread):
    """选区 AI 操作执行线程：流式返回结果。"""

    delta = pyqtSignal(str)
    done = pyqtSignal()
    err = pyqtSignal(str)

    def __init__(self, cfg: Config, instruction: str, code: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.instruction = instruction
        self.code = code

    def run(self):
        try:
            asyncio.run(self._run())
        except Exception as e:
            self.err.emit(str(e))
        finally:
            self.done.emit()

    async def _run(self):
        msgs = [
            {"role": "system", "content": build_selection_prompt(self.instruction, self.code)},
            {"role": "user", "content": f"指令：{self.instruction}"},
        ]
        async for ev in stream_chat(self.cfg, msgs, temperature=0.2, max_tokens=1500):
            if ev["type"] == "delta":
                self.delta.emit(ev["content"])


class SelectionDialog(QObject):
    """选区 AI 对话框控制器（由 gui.py 使用，纯逻辑 + 信号）。"""

    def __init__(self, cfg: Config, instruction: str, code: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        # runner 不设 parent：避免随对话框销毁而被"活销毁"（QThread: Destroyed while running）
        self.runner = SelectionRunner(cfg, instruction, code)

    def start(self, on_delta, on_done, on_err):
        self.runner.delta.connect(on_delta)
        self.runner.done.connect(on_done)
        self.runner.err.connect(on_err)
        self.runner.start()

    def stop(self):
        """对话框关闭时调用：断开信号并等待线程结束，防止信号到达已销毁对象。"""
        try:
            self.runner.delta.disconnect()
            self.runner.done.disconnect()
            self.runner.err.disconnect()
        except TypeError:
            pass
        if self.runner.isRunning():
            # 有界等待避免网络挂起时卡死 UI；超时则转交模块级保活集合，线程结束时再清理
            self.runner.wait(3000)
        if self.runner.isRunning():
            r = self.runner

            def _cleanup():
                _STOPPED_RUNNERS.discard(r)
                try:
                    r.deleteLater()
                except Exception:
                    pass

            r.finished.connect(_cleanup)
            _STOPPED_RUNNERS.add(r)
        self.runner = None
