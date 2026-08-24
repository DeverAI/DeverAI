"""Embedding 级别匹配引擎（v8.1）。

三级方案（config.embedding_level）：
- char —— 字符 bigram 余弦 + 包含度（现状，零成本离线）
- bm25 —— 字符 bigram 词项 BM25（本地语义增强，零依赖离线）
- api  —— 外部 Embedding API（注册表中 kind=embedding 的独立条目，语义最强）

token 节省逻辑：匹配越准 → 复用拦截/工具裁剪/资产检索越准 → 喂给高级(S)模型
的冗余上下文与重复生成越少，最大化减小其 token 消耗。

统一入口：
- rank(query, docs, ...) 批量打分（推荐，API 模式一次请求多条）
- similarity(query, doc, ...) 单条打分（API 模式退化为 1v1 余弦）
"""
from __future__ import annotations

import math
import re
from collections import Counter, OrderedDict
from typing import List, Optional, Tuple

from .config import get_config

# API 模式向量缓存：(model_id, text) -> list[float]；容量上限 2000，超出淘汰最旧
_EMBED_CACHE: OrderedDict = OrderedDict()
_EMBED_CACHE_MAX = 2000
# api 级别参与向量化打分的最多文档数（超出自动降级 bm25，防超大集阻塞/超限）
_API_MAX_DOCS = 512
# api 单批请求条数
_API_BATCH = 128
# api 请求超时（秒）：优先可用性，避免 GUI/agent 长时间卡死
_API_TIMEOUT = 8.0


def re_ws(text: str) -> str:
    return re.sub(r"\s+", "", str(text).lower())


def _bigrams(text: str, n: int = 2):
    t = re_ws(text)
    return [t[i: i + n] for i in range(max(0, len(t) - n + 1))]


def char_similarity(doc: str, query: str) -> float:
    """字符 bigram 计数余弦 × 0.7 + 查询包含度 × 0.3（原 vault._similarity）。"""
    a = Counter(_bigrams(doc))
    b = Counter(_bigrams(query))
    if not a or not b:
        return 0.0
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    dot = sum(a[g] * b[g] for g in set(a) | set(b))
    cos = dot / (na * nb)
    inter = sum((a & b).values())
    containment = inter / sum(b.values()) if b else 0.0
    return 0.7 * cos + 0.3 * containment


def _bm25_scores(query: str, docs: List[str], k1: float = 1.5, b: float = 0.75) -> List[float]:
    """归一化 BM25（词项=字符 bigram）：分数 ∈ [0,1]，与 char 量纲兼容。

    score = Σ_q idf(q)·qtf·tf_norm(q) / Σ_q idf(q)·qtf
    分子分母同为 query 词项加权，天然有界；tf_norm = tf(k1+1)/(tf+k1(1-b+b·dl/avgdl))。
    单文档（<2 篇）时退化为字符 bigram 余弦。"""
    if len(docs) < 2:
        return [char_similarity(d, query) for d in docs]
    qb = Counter(_bigrams(query))
    if not qb:
        return [0.0] * len(docs)
    tfs = [Counter(_bigrams(d)) for d in docs]
    dl = [sum(t.values()) for t in tfs]
    avgdl = sum(dl) / len(docs)
    n = float(len(docs))
    df: dict = {}
    for t in tfs:
        for g in t:
            df[g] = df.get(g, 0) + 1
    idf = {g: math.log((n - df[g] + 0.5) / (df[g] + 0.5) + 1.0) for g in df}
    denom = 0.0
    for g, qtf in qb.items():
        denom += idf.get(g, 0.0) * qtf
    if denom <= 0:
        return [0.0] * len(docs)
    out = []
    for i, t in enumerate(tfs):
        dl_denom = k1 * (1 - b + b * dl[i] / avgdl) if avgdl else 0.0
        s = 0.0
        for g, qtf in qb.items():
            tf = t.get(g, 0)
            if not tf:
                continue
            tf_norm = tf * (k1 + 1) / (tf + dl_denom) if dl_denom else 1.0
            s += idf[g] * qtf * tf_norm
        out.append(s / denom)
    return out


def _resolve_api_model(cfg):
    """取注册表 kind=embedding 的模型（优先 cfg.embedding_model）。开关关闭返回 None。"""
    if not getattr(cfg, "ENABLE_EMBEDDING_API", True):
        return None
    from . import models as models_mod
    try:
        return models_mod.get_embedding_model(cfg)
    except Exception:
        return None


def _cos(a, b) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _cache_get(model_id, text):
    k = (model_id, text)
    if k in _EMBED_CACHE:
        _EMBED_CACHE.move_to_end(k)
        return _EMBED_CACHE[k]
    return None


def _cache_put(model_id, text, vec):
    k = (model_id, text)
    _EMBED_CACHE[k] = vec
    _EMBED_CACHE.move_to_end(k)
    while len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
        _EMBED_CACHE.popitem(last=False)


def api_embed(texts, model, cfg=None, timeout: float = _API_TIMEOUT,
              batch: int = _API_BATCH) -> Optional[List[List[float]]]:
    """同步调用 OpenAI 兼容 /embeddings；失败/未配置返回 None；结果按 (model,text) 缓存。

    - 按 index 对齐回填（不依赖响应顺序）
    - 分批请求（每批 batch 条）
    - cfg 透传：base/key 取 model.url/api_key 回退 cfg 全局
    """
    cfg = cfg or get_config()
    base = (model.url or cfg.api_base_url or "").rstrip("/")
    if not base:
        return None
    key = (model.api_key or cfg.api_key or "").strip()
    if not key:
        return None
    result: List[Optional[List[float]]] = [None] * len(texts)
    missing = []
    for i, t in enumerate(texts):
        v = _cache_get(model.id, t)
        if v is not None:
            result[i] = v
        else:
            missing.append((i, t))
    if not missing:
        return result
    import httpx
    try:
        for start in range(0, len(missing), batch):
            chunk = missing[start:start + batch]
            r = httpx.post(
                base + "/embeddings",
                json={"model": model.id, "input": [t for _, t in chunk]},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                timeout=timeout,
            )
            r.raise_for_status()
            data = r.json().get("data") or []
            for item in data:
                idx = item.get("index")
                if idx is None or idx < 0 or idx >= len(chunk):
                    continue
                i, t = chunk[idx]
                vec = item["embedding"]
                _cache_put(model.id, t, vec)
                result[i] = vec
    except Exception:
        return None
    return result


def rank(query, docs, cfg=None, min_score: float = 0.0,
         limit: Optional[int] = None) -> List[Tuple[int, float]]:
    """批量打分：docs 为 str 列表，返回 [(idx, score)] 按分降序，过滤 < min_score。

    三级分数均归一化到 [0,1]（bm25 为归一化 BM25），阈值语义一致。
    API 失败/未配置/超 _API_MAX_DOCS 自动降级 bm25（再降级 char），链路永不中断。"""
    cfg = cfg or get_config()
    level = getattr(cfg, "embedding_level", "char")
    n = len(docs)
    scores: List[float]
    if level == "api" and n <= _API_MAX_DOCS:
        m = _resolve_api_model(cfg)
        vecs = api_embed([query] + list(docs), m, cfg) if m else None
        if vecs and vecs[0] is not None and all(v is not None for v in vecs[1:]):
            qv = vecs[0]
            scores = [_cos(qv, v) for v in vecs[1:]]
        else:  # API 不可用 → 降级 bm25
            scores = _bm25_scores(query, docs)
    elif level == "api":  # 超 _API_MAX_DOCS → 降级 bm25
        scores = _bm25_scores(query, docs)
    elif level == "bm25":
        scores = _bm25_scores(query, docs)
    else:
        scores = [char_similarity(d, query) for d in docs]
    out = [(i, s) for i, s in enumerate(scores) if s >= min_score]
    out.sort(key=lambda x: (-x[1], x[0]))
    if limit is not None:
        out = out[:limit]
    return out


def similarity(query, doc, cfg=None) -> float:
    """单条相似度。api 级别为 1v1 向量余弦；bm25 级别单条退化为 char（与 rank 语义一致）；
    api 失败/未配置自动降级。"""
    cfg = cfg or get_config()
    level = getattr(cfg, "embedding_level", "char")
    if level == "api":
        m = _resolve_api_model(cfg)
        if m:
            vecs = api_embed([query, doc], m, cfg)
            if vecs and vecs[0] is not None and vecs[1] is not None:
                return _cos(vecs[0], vecs[1])
    return char_similarity(doc, query)
