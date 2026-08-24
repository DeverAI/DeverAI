"""v8.3 依赖树 dep_tree：记录文件依赖关系与大小下限，快速判断文件是否缺漏。

- scan_workspace(ws)：自动扫描工作区 .py/.js/.ts，正则解析 import/from/require
- check_tree(tree, ws)：校验被依赖文件存在 + 大小 >= 下限，返回问题列表
- update_on_file_op(tree, rel, size)：文件操作（写/删/大减）后增量更新
- expert_report(tree, rel, deps, min_size)：专家上报依赖与大小下限（覆盖自动值）

设计要点：
- 零依赖：纯正则 + pathlib，不做真实语法解析（够用即可）
- 依赖归一化：相对路径 → 工作区内 rel；模块路径仅记录（外部库不校验）
- 大小下限：自动值 = 当前大小 × 0.3（容差防误报），专家可覆盖为明确值
- tree 随每轮快照序列化保存（meta 内嵌）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

# 需扫描的源文件后缀（轻量）
SCAN_SUFFIXES = {".py", ".js", ".ts", ".jsx", ".tsx", ".pyw", ".mjs", ".cjs"}

# 每行单引号导入
_IMPORT_RE = re.compile(
    r"""^\s*(?:import\s+[\w_*{}\s,]+from\s+['"]([^'"]+)['"]|import\s+['"]([^'"]+)['"]|from\s+([\w.]+)\s+import|require\s*\(\s*['"]([^'"]+)['"]\s*\)|import\s+([\w.]+))""",
    re.MULTILINE,
)


def _norm_module(spec: str) -> str:
    """模块说明 → 可能的相对文件路径（非相对/外部库返回空串）。"""
    spec = (spec or "").strip()
    if not spec:
        return ""
    if spec.startswith("/"):  # 绝对路径（罕见，原样保留，最终会因越界被过滤）
        return spec
    if spec.startswith("."):
        # v8.5.x 审查修复：相对导入的 '.mod'/'..common' 是"相对当前/上级目录"，
        # 不是文件名 ".mod"；拆为目录分量，否则 p=base/.mod 被当单个文件名误判缺失。
        dots = len(spec) - len(spec.lstrip("."))
        rest = spec[dots:]
        if dots == 1:
            return rest or "."
        return ("../" * (dots - 1)) + rest
    return ""  # 裸模块名视为外部库，不校验


def _resolve_rel(spec: str, base_rel: str, root: Optional[str] = None,
                 require_exists: bool = False) -> Optional[str]:
    """导入说明 → 工作区内 rel 路径。

    root 为工作区根目录（存在性判断基准）；require_exists=True 时仅在
    root 下候选文件真实存在才返回（裸模块名 import x），否则视为外部库，
    不记依赖（避免把第三方库误报为缺失）。
    """
    root = Path(root) if root else None
    rel = _norm_module(spec)
    if not rel:
        if not require_exists:
            return None
        rel = spec  # 裸模块名，尝试同目录解析
    base_dir = (root or Path(".")) / Path(base_rel).parent
    p = base_dir / rel
    cands = [p, p.with_suffix(".py"), p.with_suffix(".js"), p.with_suffix(".ts"),
             p.with_suffix(".pyw"), p.with_suffix(".mjs"),
             p / "index.py", p / "index.js", p / "index.ts"]
    for c in cands:
        c = Path(str(c))
        # P3-23：允许 .. 向上引用（from ..common import x），但最终路径须仍在 root 内
        try:
            c = c.resolve()
        except OSError:
            continue
        if root is not None:
            try:
                if c != root and root not in c.parents:
                    continue  # 逃逸工作区，视为外部库
            except Exception:
                continue
        try:
            rel_c = str(c.relative_to(root)) if root is not None else str(c)
        except ValueError:
            continue
        rel_c = rel_c.replace("\\", "/")
        if require_exists:
            if root is None:
                return rel_c if c.exists() else None
            if not c.exists():
                continue
        return rel_c
    return None


def scan_workspace(ws: str, tree: Optional[dict] = None) -> dict:
    """扫描工作区，构建 {rel: {deps:[rel...], size:int, min_size:int}}。

    若传入已有 tree，保留 expert 上报字段（如 lock/min_size 覆盖）。
    """
    root = Path(ws)
    tree = tree if tree is not None else {}
    if not root.exists():
        return tree
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in SCAN_SUFFIXES:
            continue
        try:
            rel = str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
        # 跳过快照/备份/数据目录（P2-18：不排除 static，网页端代码需纳入依赖树校验）
        # v8.14：排除表对齐 tools.SKIP_DIRS 全集（.venv/venv/.idea/.vscode/dist/build），
        # 此前虚拟机环境工作区会被逐文件读取 200KB，后台扫描长时间占盘
        top = rel.split("/", 1)[0]
        if top in ("backups", "checkpoints", "data", "sessions", "dev_log",
                   "updates", ".git", "node_modules", "__pycache__",
                   ".venv", "venv", ".idea", ".vscode", "dist", "build"):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        entry = tree.get(rel, {})
        deps = set(entry.get("deps") or [])
        text = ""
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")[:200000]
        except OSError:
            pass
        for m in _IMPORT_RE.finditer(text):
            spec = next((g for g in m.groups() if g), "")
            # 相对导入（./x）缺失由 check_tree 报；裸模块名仅在文件存在时记依赖
            is_rel = (spec or "").lstrip().startswith((".", "/"))
            resolved = _resolve_rel(spec, rel, root=ws,
                                    require_exists=not is_rel)
            if resolved and resolved != rel:
                deps.add(resolved)
        entry["deps"] = sorted(deps)
        entry["size"] = size
        # 大小下限：expert 上报锁定的 min_size 优先；自动值始终随当前 size 浮动
        # （可增可减：只增不减会把外部/手工合法缩容误报 too_small 并锁死应用）
        if entry.get("locked"):
            pass
        else:
            entry["min_size"] = int(size * 0.3) if size else 0
            entry["_auto_min"] = True
        tree[rel] = entry
    return tree


def update_on_file_op(tree: dict, rel: str, size: int, op: str = "write") -> dict:
    """文件操作后增量更新：write=更新大小与下限；delete=移除节点并标记引用它的文件。"""
    rel = (rel or "").strip().replace("\\", "/")
    if op == "delete":
        tree.pop(rel, None)
        for e in tree.values():
            e["deps"] = [d for d in e.get("deps", []) if d != rel]
        return tree
    entry = tree.get(rel, {"deps": []})
    entry["size"] = size
    # P1-11：写操作（经 diff 审批的有意修改）重置自动下限为新状态；
    # locked（expert 锁定）值保持不自动覆盖
    if not entry.get("locked"):
        entry["min_size"] = int(size * 0.3) if size else 0
        entry["_auto_min"] = True
    tree[rel] = entry
    return tree


def expert_report(tree: dict, rel: str, deps: Optional[list] = None,
                  min_size: Optional[int] = None) -> dict:
    """专家上报：覆盖依赖与大小下限（lock 标记防自动覆盖）。"""
    rel = (rel or "").strip().replace("\\", "/")
    if not rel:
        return tree
    entry = tree.get(rel, {"deps": [], "size": 0})
    if deps:
        entry["deps"] = sorted(set(entry.get("deps", [])) | {str(d).strip() for d in deps})
    if min_size is not None:
        entry["min_size"] = int(min_size)
        entry["locked"] = True
    tree[rel] = entry
    return tree


def check_tree(tree: dict, ws: str) -> list:
    """校验：被依赖文件缺失 / 大小低于下限。返回问题列表 [{rel, kind, detail}]。"""
    root = Path(ws)
    issues = []
    for rel, entry in (tree or {}).items():
        p = root / rel
        # 1) 大小下限
        min_size = int(entry.get("min_size") or 0)
        if min_size > 0:
            try:
                size = p.stat().st_size if p.exists() else 0
            except OSError:
                size = 0
            if size < min_size:
                issues.append({"rel": rel, "kind": "too_small",
                               "detail": f"size {size} < min {min_size}"})
        # 2) 被依赖文件缺失
        for d in entry.get("deps", []):
            if not (root / d).exists():
                issues.append({"rel": rel, "kind": "missing_dep",
                               "detail": f"依赖缺失: {d}"})
    return issues


def serialize(tree: dict) -> str:
    return json.dumps(tree, ensure_ascii=False, indent=1)
