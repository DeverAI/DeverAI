"""资产银行（第三章 3.1/3.2）：本地复用资产 + 多维说明书 + 语义检索。

- 生成物评估：AI 轻量级评估是否入库；入库必须由 AI 撰写说明书
  （标题/描述/视觉风格标签/适用场景/生成 Prompt）。
- 语义检索：字符 bigram TF-IDF + 余弦相似度（纯 Python，无重依赖）。
  注：设计文档预设阈值 85%，但纯 TF-IDF 对中文短文本的余弦值通常显著低于此值，
  故默认阈值设为可配置（config.vault_threshold，当前默认 0.45），机制完全等价。
- v8.1：检索打分改走 matcher 三级匹配引擎（char/bm25/api），_similarity 保留为 char 级兼容别名。
"""
import datetime
import json
import uuid
from typing import List, Optional

from .config import DATA_DIR, Config
from .llm import chat_complete, extract_json
from .matcher import char_similarity, rank as match_rank
from .storage import load_json, save_json

VAULT_PATH = DATA_DIR / "vault" / "assets.json"
EVAL_SYSTEM = (
    "你是资产银行管理员（子Agent，全新上下文）。请评估以下生成物是否值得入库复用。"
    "入库标准：具有通用语义（不依赖当前特定上下文）、脱离当前上下文仍可被理解、"
    "生成成本较高（耗时/耗token）。请输出一个 JSON 对象，含 assets 数组，每项含："
    '{"assets": [{"keep": bool, "title": "简短标题", "description": "一段中文说明", '
    '"tags": ["视觉风格标签，如扁平化、蓝色系"], "scene": "适用场景", '
    '"prompt": "生成时使用的关键指令/参数"}]}。'
    "assets 数组长度必须与输入生成物数量一致、顺序一致。只输出 JSON，不要其他文字。"
)


def _similarity(doc: str, query: str) -> float:
    """v8.1：兼容别名，等价 matcher.char_similarity（旧调用点走三级引擎后移除）。"""
    return char_similarity(doc, query)


class Vault:
    def __init__(self):
        self._assets: List[dict] = []
        self.reload()

    def reload(self) -> None:
        data = load_json(VAULT_PATH, [])
        # 防御损坏/旧格式（dict 等非列表结构）：按空库处理，避免后续遍历崩溃
        if not isinstance(data, list):
            data = []
        self._assets = data

    def _save(self) -> None:
        save_json(VAULT_PATH, self._assets)

    # ---------- 基础 CRUD ----------
    def list(self) -> List[dict]:
        return list(self._assets)

    def add(self, asset: dict) -> dict:
        asset = dict(asset)
        asset.setdefault("id", uuid.uuid4().hex[:12])
        asset.setdefault("created_at", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self._assets.insert(0, asset)
        self._save()
        return asset

    def delete(self, asset_id: str) -> bool:
        before = len(self._assets)
        self._assets = [a for a in self._assets if a.get("id") != asset_id]
        if len(self._assets) != before:
            self._save()
            return True
        return False

    def get(self, asset_id: str) -> Optional[dict]:
        for a in self._assets:
            if a.get("id") == asset_id:
                return a
        return None

    # ---------- 语义检索（3.2 复用拦截，v8.1 三级匹配） ----------
    def search(self, query: str, limit: int = 5, threshold: float = None) -> List[dict]:
        if not self._assets:
            return []
        # v8.14：默认阈值从 config.vault_threshold 读取（原硬编码 0.0 导致复用拦截过噪）
        if threshold is not None:
            thr = threshold
        else:
            try:
                from .config import get_config
                thr = get_config().vault_threshold
            except Exception:
                thr = 0.45
        # v8.15 检修：search 在 worker 线程执行，而 add()/delete() 在事件循环线程
        # 改列表（insert(0)/重建）——按索引回查 self._assets 会错位。对快照建索引。
        assets = list(self._assets)
        docs = []
        for asset in assets:
            tags = asset.get("tags") or []
            if not isinstance(tags, list):
                tags = [tags]
            docs.append(" ".join(
                [str(asset.get(k, "")) for k in ("title", "description", "scene")]
                + [str(t) for t in tags]
                + [str(asset.get("prompt", ""))]
            ))
        results = []
        for idx, score in match_rank(query, docs, min_score=thr):
            results.append({"asset": assets[idx], "score": round(score, 4)})
        return results[:limit]

    # ---------- 生成物评估入库（3.1） ----------
    async def evaluate_and_store(
        self, cfg: Config, artifacts: List[dict], emit=None
    ) -> List[dict]:
        """对一批生成物做轻量级评估，通过则入库（说明书由 AI 撰写）。"""
        if not artifacts or not cfg.ENABLE_VAULT:
            return []
        # 控制单次评估成本：最多评估 2 个
        artifacts = artifacts[:2]
        try:
            payload = [
                {
                    "path": a.get("path", ""),
                    "kind": a.get("kind", ""),
                    "size": a.get("size", 0),
                    "snippet": (a.get("content") or "")[:1500],
                }
                for a in artifacts
            ]
            prompt = {"artifacts": payload}
            msg = await chat_complete(
                cfg,
                [
                    {"role": "system", "content": EVAL_SYSTEM},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                temperature=0.2,
                max_tokens=1500,
                timeout=120.0,
            )
            results = extract_json(msg.get("content") or "")
            if isinstance(results, dict):
                results = results.get("assets") or results.get("results") or []
            if not isinstance(results, list):
                return []
        except Exception:
            return []
        stored = []
        for item, artifact in zip(results, artifacts):
            if not isinstance(item, dict) or not item.get("keep"):
                continue
            asset = {
                "kind": artifact.get("kind", "code"),
                "title": item.get("title") or artifact.get("path", "未命名资产"),
                "description": item.get("description", ""),
                "tags": item.get("tags") or [],
                "scene": item.get("scene", ""),
                "prompt": item.get("prompt", ""),
                "content": (artifact.get("content") or "")[:50000],
                "source_path": artifact.get("path", ""),
                "size": artifact.get("size", 0),
            }
            stored.append(self.add(asset))
            if emit is not None:
                try:
                    await emit({"type": "vault_stored", "asset": asset})
                except Exception:
                    pass
        return stored

    # ---------- 资料（资产）检修 ----------
    def inspect_assets(self) -> dict:
        """检查资产银行记录完整性：缺失/空字段、非法 tags、超长内容、重复 id。

        返回 {ok, total, problems:[{id,title,problems:[...]}], healthy}。
        """
        problems = []
        ids = set()
        required = ["title", "content"]
        for a in self._assets:
            probs = []
            if not isinstance(a, dict):
                problems.append({"id": "?", "title": f"(非对象记录: {str(a)[:40]})",
                                 "problems": ["记录不是对象（JSON 损坏或旧格式）"]})
                continue
            aid = str(a.get("id", ""))
            title = str(a.get("title") or "")
            content = str(a.get("content") or "")
            if not title.strip():
                probs.append("缺少标题")
            if not content.strip():
                probs.append("缺少内容（空资产无法复用）")
            for k in required:
                if k not in a:
                    probs.append(f"缺少字段 {k}")
            tags = a.get("tags")
            if tags is not None and not isinstance(tags, list):
                probs.append("tags 不是数组")
            elif isinstance(tags, list) and any(not isinstance(t, str) for t in tags):
                probs.append("tags 含非字符串项")
            for k in ("description", "scene", "prompt"):
                if k in a and len(str(a.get(k) or "")) > 5000:
                    probs.append(f"{k} 过长（>5000）")
            if aid:
                if aid in ids:
                    probs.append(f"id 重复: {aid}")
                ids.add(aid)
            else:
                probs.append("缺少 id")
            if probs:
                problems.append({"id": aid or "?", "title": title[:80] or "(无标题)", "problems": probs})
        return {
            "ok": True,
            "total": len(self._assets),
            "healthy": len(self._assets) - len(problems),
            "problems": problems,
        }

    def repair_assets(self, dry_run: bool = False) -> dict:
        """批量修复可自动修复的问题：补 id/时间戳、tags 规范化、超长字段截断、
        重复 id 重建；标题/内容为空的记录按缺失字段报告但不删除（宁留勿删）。
        返回 {ok, repaired, problems_remaining}。

        dry_run=True 时在深拷贝副本上修复，绝不污染内存中的 _assets（防后续 save 把试运行写盘）。
        """
        import copy
        work = copy.deepcopy(self._assets)
        repaired = 0
        ids = set()
        for idx, a in enumerate(work):
            if not isinstance(a, dict):
                work[idx] = {"title": str(a)[:80], "content": "", "repaired_note": "原记录非对象，已转为对象"}
                a = work[idx]
                changed = True
            else:
                changed = False
            aid = str(a.get("id", ""))
            # 缺 id / 重复 id：统一重建
            if not aid or aid in ids:
                a["id"] = uuid.uuid4().hex[:12]
                changed = True
            ids.add(str(a["id"]))
            if "created_at" not in a:
                a["created_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                changed = True
            tags = a.get("tags")
            if tags is not None and not isinstance(tags, list):
                a["tags"] = [str(tags)]
                changed = True
            elif isinstance(tags, list):
                fixed = [str(t) for t in tags]
                if fixed != tags:
                    a["tags"] = fixed
                    changed = True
            for k in ("description", "scene", "prompt"):
                if k in a and len(str(a.get(k) or "")) > 5000:
                    a[k] = str(a[k])[:5000]
                    changed = True
            if changed:
                repaired += 1
        if not dry_run and repaired:
            self._assets = work
            self._save()
        # 用修复后的 work 重新体检，报告剩余问题
        old_assets = self._assets
        self._assets = work
        try:
            problems_remaining = self.inspect_assets()["problems"]
        finally:
            self._assets = old_assets
        return {
            "ok": True,
            "dry_run": dry_run,
            "repaired": repaired,
            "problems_remaining": problems_remaining,
        }
