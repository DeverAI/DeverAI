"""DeverAI CLI — 命令行 Agent（v8.4.1）。

纯标准库交互式：复用 desktop.Agent 内核（同一套工具/上下文压缩/专家团/审批门），
无需 GUI、无第三方依赖。适合 SSH/低资源/脚本环境。

运行：
    python cli_main.py                     # 交互式对话（默认加载 data/config.json）
    python cli_main.py --model gpt-4o      # 覆盖模型
    python cli_main.py --workspace .       # 覆盖工作区
    python cli_main.py --mode builder      # 启动形态 builder|chat|experts
    python cli_main.py --no-color          # 纯文本（管道/旧终端）

会话内命令：
    /help       帮助
    /mode <m>   切换形态 builder|chat|experts
    /clear      清空当前会话历史（不落盘新历史）
    /history    显示当前历史条数
    /quit       退出（或 Ctrl+D）
运行期间 Ctrl+C 取消当前回复。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop.config import Config, DATA_DIR, get_config  # noqa: E402
from desktop.agent import Agent, AgentResult  # noqa: E402
from desktop.vault import Vault  # noqa: E402
from desktop.tools import ApprovalGate  # noqa: E402
from desktop.storage import save_json  # noqa: E402

CLI_HISTORY = DATA_DIR / "cli_history.json"

# ---- ANSI 颜色（可禁用）----
_COLOR_KEYS = ("RST", "BOLD", "DIM", "GREEN", "CYAN", "YELLOW", "RED", "MAGENTA")


class _C:
    RST = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"


C = _C()


# ----------------------------------------------------------------------
# 历史持久化（与 GUI 的 desktop_history.json 分离，互不干扰）
# ----------------------------------------------------------------------
def _load_history() -> list:
    try:
        data = json.loads(CLI_HISTORY.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(history: list) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # 复用 storage 的唯一临时名 + os.replace 原子写（固定 .tmp 会并发互踩）
        save_json(CLI_HISTORY, history[-200:])
    except Exception:
        pass  # 历史落盘失败不阻塞会话


def _truncate(text: str, limit: int = 800) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "…"


# ----------------------------------------------------------------------
# 审批：异步读取用户 y/n
# ----------------------------------------------------------------------
async def _ask_approval(ev: dict) -> bool:
    """审批门交互：返回是否放行。ev 为 approval_needed 事件（或 {"command": ...} 简写）。"""
    print()
    print(f"{C.YELLOW}── 需要你确认 ──{C.RST}")
    inner = ev.get("payload") or {}
    tool = ev.get("tool")
    if tool:
        print(f"  工具: {tool}")
    cmd = ev.get("command") or inner.get("command")
    if cmd:
        print(f"  内容: {C.BOLD}{_truncate(cmd, 500)}{C.RST}")
    script = inner.get("script")
    if script:
        print(f"  脚本: {script} → {inner.get('path', '')}")
    # v8.7 审查修复：非命令类工具（exe_*/浏览器自动化）无 command/script 字段，
    # 列出关键参数避免"盲批"（与 GUI 审批弹窗对齐）
    if not cmd and not script:
        parts = [f"    {k}: {_truncate(str(v), 200)}"
                 for k in ("path", "url", "title", "text", "keys", "hwnd", "pid",
                           "x", "y", "args", "cwd")
                 if inner.get(k) not in (None, "", [], {})]
        print("\n".join(parts) if parts else "    参数: (无)")
    note = ev.get("note")
    if note:
        print(f"  说明: {_truncate(str(note), 200)}")
    try:
        answer = await asyncio.to_thread(
            input, f"  允许执行? [{C.GREEN}y{C.RST}/{C.RED}N{C.RST}] ")
    except (EOFError, KeyboardInterrupt):
        return False
    except asyncio.CancelledError:
        # P1-4：Ctrl+C 取消审批 = 拒绝；本层显式兜底，不依赖 tools.py 的 BaseException 分支
        return False
    return answer.strip().lower() in ("y", "yes", "是", "1")


# ----------------------------------------------------------------------
# 事件渲染
# ----------------------------------------------------------------------
class CliSession:
    """一次交互式会话：持有 Agent、审批门、历史。"""

    def __init__(self, cfg: Config, no_color: bool = False):
        self.cfg = cfg
        self.no_color = no_color
        global C
        if no_color:
            C = type("_C", (), {k: "" for k in _COLOR_KEYS if hasattr(_C, k)})()
        # P1-2：重定向/旧终端下输出特殊字符（✓/⛔/▶/…）会抛 UnicodeEncodeError；
        # 统一用 errors="replace" 兜底，保证事件渲染不整段丢失
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass
        self.approval = ApprovalGate()
        self._approval_lock = asyncio.Lock()  # P1-3：专家并行审批共享 stdin 竞争
        self._err_emitted = False  # P2-6：run_error 事件已渲染时，异常分支不重复报错
        try:
            self.vault = Vault() if getattr(cfg, "ENABLE_VAULT", True) else None
        except Exception as e:
            # P1-1：vault 数据损坏/权限问题时降级为 None（Agent 内部已短路），不崩溃启动
            self.vault = None
            print(f"{C.YELLOW}[警告] 资产银行加载失败，本次会话不启用: {e}{C.RST}")
        self.history = _load_history()
        self.agent: Agent | None = None

    # ---- 事件 -> 终端 ----
    async def _emit(self, ev: dict):
        # 子 Agent 事件为嵌套结构 {"type":"subagent","label":...,"event":{...}}，展开渲染
        if ev.get("type") == "subagent":
            inner = ev.get("event") or {}
            await self._render(inner)
            return
        await self._render(ev)

    async def _render(self, ev: dict) -> None:
        typ = ev.get("type")
        try:
            if typ == "text_delta":
                sys.stdout.write(str(ev.get("content", "")))
                sys.stdout.flush()
            elif typ == "tool_start":
                name = ev.get("name", "")
                # P2-4：先预截断大字段再序列化，避免 content 数十 KB 全量 dumps
                raw = ev.get("args", {}) or {}
                args = {k: (_truncate(v, 200) if isinstance(v, str) and len(v) > 200 else v)
                        for k, v in raw.items()}
                print(f"\n{C.CYAN}▶ {name}{C.RST} {_truncate(json.dumps(args, ensure_ascii=False), 300)}")
            elif typ == "tool_result":
                ok = bool(ev.get("ok"))
                name = ev.get("name", "")
                out = _truncate(ev.get("output", ""), 1200)
                mark = f"{C.GREEN}✓" if ok else f"{C.RED}✗"
                print(f"\n{mark} {name}{C.RST} {out}")
            elif typ == "copilot_block":
                print(f"\n{C.RED}[BLOCKED] 副驾驶拦截: {ev.get('note', '')}{C.RST}")
            elif typ == "guard":
                ok = bool(ev.get("ok"))
                print(f"\n{C.YELLOW}守卫: {'通过' if ok else '注意'}: {ev.get('note', '')}{C.RST}")
            elif typ == "coordination":
                print(f"\n{C.YELLOW}[全局协调] {ev.get('note', '')}{C.RST}")
            elif typ == "ctx_gate":
                s = ev.get("stats") or {}
                print(f"\n{C.DIM}[上下文守门] 保留 {s.get('kept', 0)} 块 / 禁 {len(ev.get('banned', []) or [])} 块{C.RST}")
            elif typ == "approval_needed":
                # future 在 _request_approval 的 emit 之前已创建，这里解析后可立即放行。
                # P1-3：专家并行审批共享 stdin，用会话级锁串行化，防输入被瓜分错乱。
                # P1-7：无论 _ask_approval 如何退出（异常/取消/正常）都必须 resolve，绝不悬挂 future。
                async with self._approval_lock:
                    try:
                        ok = await _ask_approval(ev)
                    except asyncio.CancelledError:
                        ok = False
                    except Exception:
                        ok = False
                    self.approval.resolve(str(ev.get("call_id") or ""), ok)
            elif typ == "diff_preview_needed":
                # P1-1（查修）：CLI 此前无此分支 → future 永不 resolve → 修改已有文件卡死
                # 600s 后自动拒绝。现在渲染 diff 摘要并复用审批问答，绝不悬挂。
                rel = ev.get("rel", "")
                print(f"\n{C.YELLOW}[Diff 预览] {rel}{C.RST}")
                try:
                    import difflib
                    diff = list(difflib.unified_diff(
                        str(ev.get("old", "")).splitlines(),
                        str(ev.get("new", "")).splitlines(), lineterm=""))
                    for line in diff[:24]:
                        print(" " + line)
                    if len(diff) > 24:
                        print(f" …（共 {len(diff)} 行，截断显示）")
                except Exception:
                    print(f" {C.DIM}（无法生成 diff，直接询问）{C.RST}")
                async with self._approval_lock:
                    try:
                        ok = await _ask_approval({"command": f"应用 diff 到 {rel}"})
                    except asyncio.CancelledError:
                        ok = False
                    except Exception:
                        ok = False
                    self.approval.resolve(str(ev.get("call_id") or ""), ok)
            elif typ == "unattended_skip":
                # v8.37：不看守跳过实时透出（与 GUI ai_note 同语义）
                print(f"\n{C.YELLOW}[不看守] 已跳过 {ev.get('tool', '')}: "
                      f"{ev.get('brief', '')}（已记入台账）{C.RST}")
            elif typ == "run_error":
                self._err_emitted = True
                print(f"\n{C.RED}运行错误: {ev.get('message', '')}{C.RST}")
            elif typ in ("run_start", "run_done", "run_cancelled", "gate_start",
                         "assistant_tool_calls", "compress"):
                pass
        except Exception as e:
            print(f"\n{C.RED}[CLI 事件渲染异常] {e}{C.RST}")

    # ---- 执行一轮对话 ----
    async def run_round(self, user_msg: str) -> AgentResult:
        # 新建 Agent，携带当前历史（复用与 GUI 一致的生命周期）
        self.agent = Agent(
            self.cfg, emit=self._emit, history=list(self.history),
            vault=self.vault, approval=self.approval,
        )
        self._err_emitted = False  # P2-6：每轮重置错误去重标志
        try:
            result = await self.agent.run(user_msg)
        except asyncio.CancelledError:
            # Ctrl+C 取消：事件循环 cancel 当前 run task（P1-5：取消也保存已产生历史）
            if self.agent is not None:
                self.agent._cancel.set()
            print(f"\n{C.YELLOW}[已取消当前回复]{C.RST}")
            result = AgentResult(cancelled=True)
        except Exception as e:
            # v8.7 审查修复：CLI 路径异常同样落根目录 Err.log（自动存错机制），不回显 traceback
            from desktop.errors import log_error
            log_error("CLI Agent 异常", e)
            if not self._err_emitted:
                print(f"\n{C.RED}[Agent 异常] {e}{C.RST}")
            result = AgentResult(text="", summary="", cancelled=False)
        finally:
            # 同步本轮历史（含取消/异常路径已产生的部分），内存侧截断防无限增长
            if self.agent is not None:
                self.history = list(self.agent.history)[-200:]
                _save_history(self.history)
        return result


async def _session_loop(cfg: Config, no_color: bool, initial_history: list = None) -> None:
    sess = CliSession(cfg, no_color)
    if initial_history:
        # v8.27 --from-drift：以服务器素材化快照中的会话历史续跑（同一会话跨机接力）
        sess.history = list(initial_history)[-200:]
    print(f"{C.BOLD}DeverAI CLI{C.RST} — 模型 {cfg.model} · 工作区 {cfg.workspace}"
          + (" · 不看守模式" if getattr(cfg, "ENABLE_UNATTENDED", False) else ""))
    print("输入 /help 查看命令；Ctrl+D 退出。")
    while True:
        try:
            line = await asyncio.to_thread(
                input, f"{C.GREEN}deverai>{C.RST} ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        except asyncio.CancelledError:
            # P1-6：Unix 3.11+ SIGINT 会 cancel 主任务；提示符处视为退出，运行中由 run_round 处理
            print()
            break
        text = (line or "").strip()
        if not text:
            continue
        if text.startswith("/"):
            if text in ("/quit", "/exit"):
                break
            elif text in ("/help", "/?"):
                print("命令: /mode <builder|chat|experts> 切换形态 | /clear 清空会话 | "
                      "/history 条数 | /quit 退出")
            elif text.startswith("/mode"):
                parts = text.split(None, 1)
                if len(parts) < 2:
                    print("用法: /mode <builder|chat|experts>")
                    continue
                m = parts[1].strip()
                if m in ("builder", "chat", "experts"):
                    cfg.agent_mode = m
                    print(f"已切换形态: {C.BOLD}{m}{C.RST}")
                else:
                    print("形态必须是 builder|chat|experts")
            elif text == "/clear":
                sess.history = []
                _save_history([])
                print("会话历史已清空。")
            elif text == "/history":
                print(f"当前历史 {len(sess.history)} 条消息。")
            else:
                print(f"未知命令: {text}（/help 查看）")
            continue
        # 正常对话
        await sess.run_round(text)
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="DeverAI 命令行 Agent")
    parser.add_argument("--model", default="", help="覆盖模型（默认读 data/config.json）")
    parser.add_argument("--workspace", default="", help="覆盖工作区路径")
    parser.add_argument("--mode", default="", help="启动形态 builder|chat|experts")
    parser.add_argument("--no-color", action="store_true", help="禁用 ANSI 颜色")
    parser.add_argument("--unattended", action="store_true",
                        help="v8.27 不看守模式：禁提问，非危险动作自动放行，危险动作跳过记台账")
    parser.add_argument("--preset", default="",
                        help="v8.28 Agent+ 模式预设（unattended=无人值守 / daily=日常）")
    parser.add_argument("--from-drift", metavar="DIR", default="",
                        help="v8.27 从算力漂移素材化目录续算（读 snapshot 历史 + keys.enc 密钥信封）")
    args = parser.parse_args()

    cfg = get_config()
    if args.model:
        cfg.model = args.model
    if args.workspace:
        cfg.workspace = str(Path(args.workspace).resolve())
    if args.mode in ("builder", "chat", "experts"):
        cfg.agent_mode = args.mode
    elif args.mode:
        print(f"未知形态 {args.mode}（builder|chat|experts）", file=sys.stderr)
        return 2
    if args.preset:
        # v8.28 Agent+ 模式预设。v8.29 修序：先套预设、--unattended 显式标志后置——
        # 原实现 --unattended 先置位后，daily 预设 dataclasses.replace 会把
        # ENABLE_UNATTENDED 重置回 False，显式标志被静默吃掉（与注释宣称相反）。
        from desktop.config import AGENT_PRESETS, apply_agent_preset
        if args.preset in AGENT_PRESETS:
            cfg = apply_agent_preset(cfg, args.preset)
            print(f"[预设] 已应用 Agent+ 预设: {args.preset}（{AGENT_PRESETS[args.preset]['desc']}）")
        else:
            print(f"未知预设 {args.preset}（可选: {', '.join(AGENT_PRESETS)}）", file=sys.stderr)
            return 2
    if args.unattended:
        cfg.ENABLE_UNATTENDED = True

    initial_history = None
    if args.from_drift:
        import getpass
        import shutil as _sh
        from desktop.sync import import_snapshot_bytes, import_keys_envelope
        d = Path(args.from_drift)
        # v8.27 阶段3：缺省工作区 = 素材化 workspace/——防止缺省回退到服务器仓库根，
        # 叠加 --unattended 后续算 AI 直接改服务器代码
        if not args.workspace:
            ws_default = d / "workspace"
            if not ws_default.is_dir():
                print(f"漂移目录缺少 workspace/（{ws_default}）——请用 --workspace 显式指定。",
                      file=sys.stderr)
                return 2
            cfg.workspace = str(ws_default)
        snap_path = d / "snapshot"
        if not snap_path.is_file():
            print(f"漂移目录缺少 snapshot：{snap_path}", file=sys.stderr)
            return 2
        text = snap_path.read_text(encoding="utf-8", errors="replace")
        pw = getpass.getpass("同步口令（sync_password，用于解密快照与密钥信封，可直接回车跳过）: ")
        payload = {}
        try:
            payload = import_snapshot_bytes(text, pw)
        except Exception:
            try:
                payload = import_snapshot_bytes(text, "")
            except Exception as e:
                print(f"快照解包失败: {e}", file=sys.stderr)
                return 2
        hist = payload.get("history")
        if isinstance(hist, list) and hist:
            initial_history = hist
            print(f"[漂移续算] 已载入服务器会话历史 {len(hist)} 条。")
        # 快照内非敏感配置接力（model/参数/形态；--model 显式覆盖优先）
        snap_cfg = payload.get("config") or {}
        if isinstance(snap_cfg, dict):
            for k in ("temperature", "max_tokens", "agent_mode"):
                if snap_cfg.get(k) not in (None, ""):
                    try:
                        setattr(cfg, k, snap_cfg[k])
                    except Exception:
                        pass
        # 密钥信封：仅解密到内存（不落盘）；无信封时走服务器本地 drift_api_key 兜底
        keys_path = d / "keys.enc"
        if keys_path.is_file():
            try:
                keys = import_keys_envelope(keys_path.read_text(encoding="utf-8"), pw)
                for k, v in (keys or {}).items():
                    if v:
                        setattr(cfg, k, v)
                print("[漂移续算] 密钥信封已解密到内存（不落盘）。")
            except Exception as e:
                print(f"[漂移续算] 密钥信封解密失败（{e}），将尝试服务器本地 LLM 配置。",
                      file=sys.stderr)
        else:
            print("[漂移续算] 无密钥信封，使用服务器本地 LLM 配置（drift_api_key / data/config.json）。")
        # data 位素材消费（裁决③）：WorkTree/记忆/设置同步到服务器普通端对应 data 位
        src_data = d / "data"
        if src_data.is_dir():
            copied = 0
            for name in ("agent_memory.json", "file_protect.json", "term_envs.json",
                         "audit.jsonl", "drift_state.json"):
                p = src_data / name
                if p.is_file():
                    try:
                        _sh.copy2(p, DATA_DIR / name)
                        copied += 1
                    except OSError:
                        pass
            for sub in ("checkpoints", "workspaces"):
                sd = src_data / sub
                if sd.is_dir():
                    try:
                        _sh.copytree(sd, DATA_DIR / sub, dirs_exist_ok=True)
                        copied += 1
                    except OSError:
                        pass
            if copied:
                print(f"[漂移续算] 素材化 data 位已合并到 {DATA_DIR}（{copied} 项）。")

    if not cfg.api_key:
        print("未配置 API Key（data/config.json）。请先运行桌面版并在设置中填写，"
              "或用 --model 覆盖；当前为空将无法调用模型。", file=sys.stderr)

    try:
        asyncio.run(_session_loop(cfg, args.no_color, initial_history))
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
