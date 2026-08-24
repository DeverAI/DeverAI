"""网页版快照桥辅助：依赖树专家报告应用到激活轮 meta。"""
from __future__ import annotations


def apply_expert_report(rid: str, rel: str, deps: list, min_size: int) -> tuple:
    """把专家上报的依赖/大小下限写入激活轮的 meta.tree。

    复用 desktop.session_snap 的 meta 读写 + desktop.dep_tree.expert_report。
    """
    if not rid:
        return False, "无激活轮次"
    try:
        from desktop import session_snap, dep_tree
        with session_snap._META_LOCK:
            m = session_snap._load_meta(rid)
            tree = m.get("tree") or {}
            # locked 由 dep_tree.expert_report 内部强制置 True（P2-9：已移除外部死参数）
            tree = dep_tree.expert_report(tree, rel, deps or [], min_size)
            m["tree"] = tree
            session_snap._save_meta(rid, m)
        return True, "已应用专家报告"
    except Exception as e:
        return False, f"应用失败: {e}"
