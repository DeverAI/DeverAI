"""文件分区规划并发调度（v8.9）。

同一 DAG 层内的任务按申报文件（files）分组：
- files 为空（只读/无文件任务）可与任意任务并行；
- 两个任务申报了同一规范化路径（含 `*` 通配）时，必须分到不同波次串行执行，
  从调度层杜绝「同层并发写同一文件」的 lost update；
- 不声明 files 时退化为原同层并行（与历史行为一致）。

开关：ENABLE_FILE_PARTITION=False 时 wave_partition 返回 [level]（原样一波）。
"""
from .config import get_config
from .locks import _norm


def _enabled() -> bool:
    try:
        return bool(getattr(get_config(), "ENABLE_FILE_PARTITION", True))
    except Exception:
        return True


def _task_files(task) -> set:
    """提取任务申报的文件集合（规范化，去空）。兼容 dict / dataclass / object。

    LLM 可能返回字符串（files: "a.py"）：统一按单文件处理，避免按字符拆解。
    """
    if isinstance(task, dict):
        files = task.get("files") or []
    else:
        files = getattr(task, "files", None) or []
    if isinstance(files, str):
        files = [files]
    elif not isinstance(files, (list, tuple, set)):
        files = []
    out = set()
    for f in files:
        if f is None:
            continue
        norm = _norm(str(f))
        if norm:
            out.add(norm)
    return out


def _overlap(a: set, b: set) -> bool:
    """两个文件集合是否冲突：包含 `*`（全工作区写权）或存在交集。"""
    if not a or not b:
        return False
    if "*" in a or "*" in b:
        return True
    return not a.isdisjoint(b)


def wave_partition(level: list) -> list:
    """把同一 DAG 层拆成多个执行波次。

    返回 [[task, ...], ...]：每个波次内部文件无冲突，可按原并行方式执行；
    波次之间顺序执行。ENABLE_FILE_PARTITION=False 或 level 为空时返回 [level]。
    """
    if not level:
        return []
    if not _enabled():
        return [level]
    waves = []
    remaining = list(level)
    while remaining:
        wave = []
        next_remaining = []
        for t in remaining:
            fs = _task_files(t)
            if not fs:
                # 无文件申报 = 只读/无关任务，与任意任务并行（加速）
                wave.append(t)
            elif any(_overlap(fs, _task_files(w)) for w in wave):
                next_remaining.append(t)
            else:
                wave.append(t)
        if not wave:  # 理论上不可达；防御：整层退回一波
            return [level]
        waves.append(wave)
        remaining = next_remaining
    return waves
