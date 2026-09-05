"""v6.3 AI Checkpoint + v8.2 版本快照回退（防 rm -rf / 误编辑丢文件救不回）。

- save_checkpoint(rel_path, content, task_id, source)：把原文件内容写到
  data/checkpoints/{task_id}/{rel_path}.{ts}.{suffix}.bak + .meta（含 source）
- list_checkpoints(limit)：返回最近 N 条（按 ts 倒序）
- list_files(limit)：按文件聚合（人类/模型/AI 各版本），供版本回退对话框
- list_versions(rel_path)：单文件全部版本
- restore_checkpoint(bak_path, current_rel_path)：读 bak 内容（写回由上层做）

source ∈ ai（Agent 工具写入）/ human（编辑器人类保存）/ model（模型要求）/ restore（回退操作）

设计要点：
- 只快照"已存在的原文件"（新文件无原版可快照）
- task_id 缺省用 "default"（同一轮多文件改动共用 task_id 便于整组回滚）
- 恢复操作也先快照当前内容（防用户后悔，可再次回滚）
- 文件名带时间戳防覆盖；rel_path 中的 / 替换为 _ 避免目录穿透
- 保留策略：每文件最多 _MAX_PER_FILE 版，全局最多 _MAX_TOTAL 版（超量删最旧）
"""
from __future__ import annotations
import datetime
import secrets
from pathlib import Path

from .config import DATA_DIR
from .errors import log_error
from .storage import save_text

CHECKPOINT_DIR: Path = DATA_DIR / "checkpoints"
_MAX_LIST = 50          # list_checkpoints 默认返回条数上限
_MAX_PER_FILE = 20      # 每个文件最多保留的版本数
_MAX_TOTAL = 600        # 磁盘上最多保留 checkpoint 文件数（超出删最旧）

_SOURCE_NAMES = {"ai": "AI 编辑", "human": "人类编辑", "model": "模型要求", "restore": "回退操作"}


def source_name(source: str) -> str:
    return _SOURCE_NAMES.get(str(source or "").strip() or "ai", str(source or "ai"))


def _gc_orphans(max_age_s: float = 86400.0) -> int:
    """v8.15 检修：清理无 meta 的孤儿 .bak。

    save_checkpoint 先写 .bak 后写 meta，进程在两步之间崩溃会留下永久孤儿
    （_scan 只认带 meta 条目，_gc 永远清不到）。超过 max_age_s（默认 1 天）
    的无 meta .bak 判定为写入中断残留，直接删除。返回删除数。
    """
    if not CHECKPOINT_DIR.exists():
        return 0
    removed = 0
    now = datetime.datetime.now().timestamp()
    try:
        for bak in CHECKPOINT_DIR.rglob("*.bak"):
            try:
                meta = bak.with_suffix(bak.suffix + ".meta")
                if not meta.exists() and (now - bak.stat().st_mtime) > max_age_s:
                    bak.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
    except Exception as e:
        log_error("checkpoint 孤儿回收失败", e)
    return removed


def _safe_name(rel_path: str) -> str:
    """把相对路径转成安全文件名（/ → __），防目录穿透。

    额外拦截 "." / ".."：task_id 会作为 checkpoints 下的子目录名，
    若为 ".." 会把快照写到 checkpoints 之外（真实路径穿越）。
    """
    name = str(rel_path or "").strip().replace("/", "__").replace("\\", "__").replace(":", "_")
    if not name or name == "." or name == "..":
        return "unnamed"
    return name


def save_checkpoint(rel_path: str, content: str, task_id: str = "", source: str = "ai") -> str | None:
    """快照原文件内容。返回 .bak 文件路径，失败返回 None。

    content 是"原文件当前内容"（写入新内容前调用）。
    若原文件不存在（新文件），返回 None（无可快照内容）。
    """
    rel_path = str(rel_path or "").strip().replace("\\", "/")  # 统一分隔符，AI/human 同组
    if not rel_path:
        return None
    if content is None:
        return None
    task_id = _safe_name(task_id or "default")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = secrets.token_hex(2)  # 防同秒多改撞名
    base = _safe_name(rel_path)
    bak_name = f"{base}.{ts}.{suffix}.bak"
    bak_dir = CHECKPOINT_DIR / task_id
    try:
        bak_dir.mkdir(parents=True, exist_ok=True)
        bak_path = bak_dir / bak_name
        # v8.14：newline="" 精确写（canonical LF）——此前隐式翻译会把快照内容
        # 行尾改写，回退后与原文件字节不一致
        with open(bak_path, "w", encoding="utf-8", newline="") as f:
            f.write(str(content))
        # 写一条 meta（rel_path + ts + source），便于 list/restore 反查原路径
        meta_path = bak_dir / f"{bak_name}.meta"
        try:
            meta_path.write_text(
                f"rel_path={rel_path}\nts={ts}\ntask_id={task_id}\nsource={source or 'ai'}\n",
                encoding="utf-8")
        except Exception:
            bak_path.unlink(missing_ok=True)  # meta 失败则回滚 .bak，避免孤儿
            return None
        # 只读报警状态机：停止一切清理/淘汰（FreqErr #81）
        try:
            from .session_snap import is_readonly
            if not is_readonly():
                _gc()
        except Exception:
            _gc()
        return str(bak_path)
    except Exception as e:
        log_error(f"checkpoint 保存失败 {rel_path}", e)
        return None


def _scan() -> list:
    """读全部 checkpoint（不截断）。每条 {bak_path, rel_path, ts, task_id, source}。"""
    if not CHECKPOINT_DIR.exists():
        return []
    _gc_orphans()
    out = []
    try:
        for meta in CHECKPOINT_DIR.rglob("*.bak.meta"):
            try:
                txt = meta.read_text(encoding="utf-8")
                info = {}
                for line in txt.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        info[k.strip()] = v.strip()
                rel_path = info.get("rel_path", "").strip()
                if not rel_path:
                    continue  # 无路径的孤儿条目不参与聚合（旧格式脏数据）
                out.append({
                    "bak_path": str(meta)[:-len(".meta")],  # 仅去尾部后缀，防 .meta 子串误替换
                    "rel_path": rel_path,
                    "ts": info.get("ts", ""),
                    "task_id": info.get("task_id", ""),
                    "source": info.get("source", "ai"),
                })
            except Exception:
                continue
    except Exception:
        pass
    out.sort(key=lambda x: (x.get("ts", ""), x.get("bak_path", "")))
    return out


def list_checkpoints(limit: int = _MAX_LIST) -> list:
    """列出最近 N 条 checkpoint（按 ts 倒序）。"""
    items = _scan()
    items.reverse()  # _scan 升序 → 这里取最新在前
    return items[: max(0, min(limit, _MAX_LIST))]


def list_files(limit: int = 200) -> list:
    """按文件聚合所有版本：返回 [{rel_path, last_ts, count, versions:[...]}] 按 last_ts 倒序。

    供版本回退对话框左侧列表使用。"""
    items = _scan()
    by_file: dict = {}
    for it in items:
        by_file.setdefault(it["rel_path"], []).append(it)
    out = []
    for rel, vers in by_file.items():
        vers.reverse()  # 最新在前
        out.append({"rel_path": rel, "last_ts": vers[0]["ts"],
                    "count": len(vers), "versions": vers})
    out.sort(key=lambda x: x["last_ts"], reverse=True)
    return out[:limit]


def list_versions(rel_path: str) -> list:
    """单文件全部版本（按 ts 倒序）。"""
    items = [it for it in _scan() if it.get("rel_path") == rel_path]
    items.reverse()
    return items


def restore_checkpoint(bak_path: str, current_rel_path: str = "") -> tuple[bool, str]:
    """把 .bak 内容读回（不写盘）。返回 (ok, bak_content)。

    恢复前先对"当前原文件内容"做一次快照（防用户后悔，可二次回滚）由上层负责。
    current_rel_path 用于回写目标；若空则从 .meta 读 rel_path。
    """
    bak = Path(bak_path)
    if not bak.exists():
        return False, "checkpoint 文件不存在"
    # 读 meta 拿原 rel_path
    rel_path = current_rel_path
    if not rel_path:
        meta = Path(str(bak_path) + ".meta")
        if meta.exists():
            try:
                for line in meta.read_text(encoding="utf-8").splitlines():
                    if line.startswith("rel_path="):
                        rel_path = line.split("=", 1)[1].strip()
                        break
            except Exception:
                pass
    if not rel_path:
        return False, "无法确定原文件路径"
    # 读 bak 内容
    try:
        bak_content = bak.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"读取 checkpoint 失败: {e}"
    # 本函数只负责读 bak 内容返回，实际写回由上层处理（避免本模块依赖 workspace）
    return True, bak_content


def _gc() -> None:
    """保留策略：每文件最多 _MAX_PER_FILE 版，全局最多 _MAX_TOTAL 版，超量删最旧。"""
    try:
        items = _scan()
        by_file: dict = {}
        for it in items:
            by_file.setdefault(it["rel_path"], []).append(it)
        doomed: list[Path] = []
        for rel, vers in by_file.items():
            vers.sort(key=lambda x: x["ts"])
            for it in vers[: -_MAX_PER_FILE]:
                doomed.append(Path(it["bak_path"]))
        kept = [it for it in items if Path(it["bak_path"]) not in doomed]
        kept.sort(key=lambda x: x["ts"], reverse=True)
        for it in kept[_MAX_TOTAL:]:
            doomed.append(Path(it["bak_path"]))
        for old in doomed:
            try:
                old.unlink(missing_ok=True)
                Path(str(old) + ".meta").unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass
