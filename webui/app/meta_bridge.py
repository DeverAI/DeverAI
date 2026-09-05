"""网页版元能力桥（v8.5 对齐批次2）— 模型注册表 / 三级匹配。

复用 desktop 模块（同机进程内）：
- desktop/models.py   模型注册表（data/models.json：读写 / 打分 a/b.1/b.2 / 性能榜 / 性价比榜 / 未定队列）
- desktop/matcher.py  三级匹配引擎（char / bm25 / api embedding，分数统一 [0,1]）

鉴权：所有端点要求登录；模型注册表为应用本机数据（与桌面版共享 data/models.json）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import current_user
from .config import get_config
from .errors import log_error

router = APIRouter(prefix="/api/bridge", tags=["meta"])

# P2-6/P2-8（查修）：注册表读-改-写串行化。用 asyncio.Lock——threading.Lock 跨 await
# 持有会阻塞整个事件循环（锁竞争时第二个请求卡死 loop）。
_MODELS_LOCK = asyncio.Lock()


def _models():
    try:
        from desktop import models
        return models
    except Exception as e:
        # P2-1：异常详情（含本机绝对路径）落 Err.log，对外只回泛化文案
        log_error("[web] 模型注册表模块导入失败", e)
        raise HTTPException(501, "当前环境不支持模型注册表")


def _matcher():
    try:
        from desktop import matcher
        return matcher
    except Exception as e:
        log_error("[web] 匹配引擎模块导入失败", e)
        raise HTTPException(501, "当前环境不支持匹配引擎")


def _to_dict(m) -> dict:
    """ModelInfo → JSON 可序列化 dict（含展示用摘要）。"""
    return {
        "id": m.id, "name": m.name, "url": m.url,
        "kind": getattr(m, "kind", "chat") or "chat",
        "caps": getattr(m, "caps", {}) or {},
        "intro": getattr(m, "intro", "") or "",
        "manual": getattr(m, "manual", "") or "",
        "context_size": int(getattr(m, "context_size", 32000) or 32000),
        "max_output": int(getattr(m, "max_output", 4096) or 4096),
        "scores": getattr(m, "scores", {}) or {},
        "prices": getattr(m, "prices", {}) or {},
        "score_summary": (getattr(m, "score_summary", lambda: "")() or ""),
    }


# ---------------------------------------------------------------------------
# 模型注册表
# ---------------------------------------------------------------------------
@router.get("/models")
async def list_models(user: dict = Depends(current_user)):
    models = _models()
    try:
        ms = await asyncio.to_thread(models.load_models)
    except Exception as e:
        log_error("[web] 读取模型注册表失败", e)
        raise HTTPException(500, "读取模型注册表失败")
    return {"ok": True, "models": [_to_dict(m) for m in ms]}


class ModelBody(BaseModel):
    id: str
    name: str = ""
    url: str = ""
    kind: str = "chat"
    caps: dict = {}
    intro: str = ""
    manual: str = ""
    context_size: int = 0
    max_output: int = 0
    prices: dict = {}


@router.post("/models")
async def upsert_model(body: ModelBody, user: dict = Depends(current_user)):
    models = _models()
    mid = str(body.id or "").strip()
    if not mid:
        raise HTTPException(400, "id 不能为空")
    # v8.13：注册表字段长度/类型上限（防单请求把 data/models.json 写成巨物）
    if len(mid) > 100 or len(body.name or "") > 200 or len(body.url or "") > 500:
        raise HTTPException(400, "模型字段过长")
    if body.kind not in ("chat", "embedding"):
        raise HTTPException(400, "kind 仅支持 chat/embedding")
    if not 0 <= int(body.context_size or 0) <= 10_000_000 or not 0 <= int(body.max_output or 0) <= 10_000_000:
        raise HTTPException(400, "context_size/max_output 非法")
    if len(body.intro or "") > 5000 or len(body.manual or "") > 5000:
        raise HTTPException(400, "intro/manual 过长")
    if len(body.caps or {}) > 200 or len(body.prices or {}) > 200:
        raise HTTPException(400, "caps/prices 字段过多")
    async with _MODELS_LOCK:  # P2-6：读-改-写原子化，防并发丢更新（asyncio.Lock 不阻塞 loop）
        try:
            ms = await asyncio.to_thread(models.load_models)
            existing = next((m for m in ms if m.id == mid), None)
            if existing is not None:
                info = existing
                if body.name:
                    info.name = body.name
                if body.url:
                    # v8.15 检修：换端点必须作废已存密钥——否则条目可被指向外部 URL 后
                    # 沿用原密钥外带（proxy 覆盖链的另一半封堵）
                    if info.api_key and body.url != info.url:
                        info.api_key = ""
                    info.url = body.url
                if body.intro:
                    info.intro = body.intro
                if body.manual:
                    info.manual = body.manual
                if body.context_size:
                    info.context_size = int(body.context_size)
                if body.max_output:
                    info.max_output = int(body.max_output)
                if body.prices:
                    info.prices = dict(body.prices)
                if body.caps:
                    info.caps = {**info.caps, **dict(body.caps)}
            else:
                try:
                    info = models.model_from_dict({
                        "id": mid, "name": body.name or mid, "url": body.url or "",
                        "kind": body.kind or "chat", "caps": dict(body.caps or {}),
                        "intro": body.intro, "manual": body.manual,
                        "context_size": int(body.context_size or 32000),
                        "max_output": int(body.max_output or 4096),
                        "prices": dict(body.prices or {}),
                    })
                except Exception:
                    raise HTTPException(400, "模型字段无效")
                ms.append(info)  # 新增模型必须加入列表再保存
            await asyncio.to_thread(models.save_models, ms)
        except HTTPException:
            raise
        except Exception as e:
            log_error("[web] 保存模型失败", e)
            raise HTTPException(500, "保存模型失败")
    return {"ok": True, "model": _to_dict(info)}


@router.delete("/models/{mid}")
async def delete_model(mid: str, user: dict = Depends(current_user)):
    models = _models()
    async with _MODELS_LOCK:  # P2-11：删除同样持锁，防与 upsert 并发丢更新
        try:
            ok = await asyncio.to_thread(models.delete_model, str(mid))
        except Exception as e:
            log_error("[web] 删除模型失败", e)
            raise HTTPException(500, "删除模型失败")
    if not ok:
        raise HTTPException(404, "模型不存在")
    return {"ok": True}


class ScoreBody(BaseModel):
    model_id: str
    metric: str
    value: float | None = None   # None=清除分数（未定）
    fixed: bool = True


@router.post("/models/score")
async def set_model_score(body: ScoreBody, user: dict = Depends(current_user)):
    models = _models()
    metric = str(body.metric or "").strip()
    if not metric:
        raise HTTPException(400, "metric 不能为空")
    # v8.13：元数据长度上限（防 models.json 被写成巨物）
    if len(metric) > 100 or len(str(body.model_id or "")) > 100:
        raise HTTPException(400, "metric/model_id 过长")
    # P2-5：打分限制 0-10；None 表示未定（清除分数）
    value = None
    if body.value is not None:
        try:
            fval = float(body.value)
        except (TypeError, ValueError):
            raise HTTPException(400, "value 必须为数字或 null")
        # v8.13：NaN/Inf 拒绝（min/max 对 NaN 的返回顺序不可依赖）
        import math
        if not math.isfinite(fval):
            raise HTTPException(400, "value 必须为有限数字")
        value = max(0.0, min(10.0, fval))
    async with _MODELS_LOCK:  # P2-11：打分持锁，防与 upsert/delete 并发丢更新
        try:
            ok = await asyncio.to_thread(
                models.set_score, str(body.model_id), metric, value, body.fixed)
        except Exception as e:
            log_error("[web] 写入打分失败", e)
            raise HTTPException(500, "写入打分失败")
    if not ok:
        raise HTTPException(404, "模型不存在")
    return {"ok": True}


# 自动打分互斥标记（单进程单事件循环下同步读写，无竞态）
_AUTOSCORE_RUNNING = {"flag": False}


class AutoScoreBody(BaseModel):
    force: bool = True
    # 浏览器端模型配置（localStorage 不写 config.json）——纯网页部署时随请求透传
    api_base: str = ""
    api_key: str = ""
    model: str = ""


@router.post("/models/auto_score")
async def auto_score(body: AutoScoreBody, request: Request, user: dict = Depends(current_user)):
    """重搜 Benchmark 自动打分（复用 desktop/auto_score.py，桌面版「重搜打分」对等）。

    对注册表全部 chat 模型：web_search 搜 Benchmark → judge 模型打分 → 更新
    scores/intro/caps。可能耗时数分钟；同一时间只允许一个任务。
    """
    try:
        from dataclasses import replace as _replace
        from desktop import auto_score as as_mod
        from pyqt.desktop.config import get_config as desktop_cfg
    except Exception as e:
        log_error("[web] 自动打分模块导入失败", e)
        raise HTTPException(501, "当前环境不支持自动打分")
    if _AUTOSCORE_RUNNING["flag"]:
        raise HTTPException(409, "已有打分任务进行中，请稍候")
    # v8.13：先占互斥标记再 await（含 SSRF 校验）——此前标记在 await 之后置位，
    # 两个并发请求可同时通过检查并同时进入后台任务。
    _AUTOSCORE_RUNNING["flag"] = True
    try:
        dcfg = desktop_cfg()
        # P2-5：浏览器端透传的凭据覆盖 config.json（纯网页部署无本地凭据）
        if body.api_base:
            # v8.13：透传的 api_base 与 LLM 代理同一套 SSRF 校验（此前直入桌面 httpx，可打内网）
            from .proxy import _validate_base
            validated = await _validate_base(str(body.api_base), request)
            dcfg = _replace(dcfg, api_base_url=validated)
        if body.api_key:
            if len(str(body.api_key)) > 4096:
                raise HTTPException(400, "api_key 过长")
            dcfg = _replace(dcfg, api_key=str(body.api_key).strip())
        if body.model:
            if len(str(body.model)) > 200:
                raise HTTPException(400, "model 过长")
            dcfg = _replace(dcfg, judge_model=str(body.model).strip())
        if not getattr(dcfg, "api_key", ""):
            raise HTTPException(400, "缺少模型凭据：请在请求中携带 api_key（或先在桌面版设置中配置）")
        # auto_score_all 是 async：在后台线程的新事件循环中执行，避免阻塞本服务事件循环
        res = await asyncio.to_thread(
            lambda: asyncio.run(as_mod.auto_score_all(dcfg, force=bool(body.force))))
    except HTTPException:
        raise
    except Exception as e:
        log_error("[web] 自动打分失败", e)
        raise HTTPException(500, "自动打分失败")
    finally:
        _AUTOSCORE_RUNNING["flag"] = False
    return res


@router.get("/models/ranks")
async def model_ranks(mode: str = "b1", top: int = 3,
                      user: dict = Depends(current_user)):
    models = _models()
    # v8.13：mode 白名单 + top 夹取（防负 top/超大 top 造成异常排序量）
    if mode not in ("b1", "b2"):
        raise HTTPException(400, "mode 仅支持 b1/b2")
    top = min(max(int(top), 1), 50)
    # P1-1：权重键必须与 models.json 的 scores 键同源（desktop.config.score_metrics，
    # 而非 app ServerConfig——后者无该字段导致榜单恒空）
    try:
        from pyqt.desktop.config import get_config as desktop_cfg
        dcfg = desktop_cfg()
        metrics = getattr(dcfg, "score_metrics", None) or []
    except Exception:
        dcfg = None
        metrics = []
    try:
        ms = await asyncio.to_thread(models.load_models)
        weights = {k: 1.0 for k in metrics}
        if mode == "b2":
            ranked = await asyncio.to_thread(
                models.value_rank, ms, weights, dcfg or get_config(), top)
        else:
            ranked = await asyncio.to_thread(models.weighted_rank, ms, weights, top)
        unscored = await asyncio.to_thread(models.unscored_queue, ms)
    except Exception as e:
        log_error(f"[web] 模型榜单计算失败 mode={mode}", e)
        raise HTTPException(500, "榜单计算失败")
    # weighted_rank/value_rank 返回 [(ModelInfo, score)]——连同分值一起回传，前端榜单直接展示。
    # P1-2：免费模型 value_rank 给 inf，JSON 序列化会 500（allow_nan=False）——归一化为
    # score=None + free=True，前端显示「免费」。
    ranked_out = []
    for m, s in ranked:
        free = isinstance(s, float) and s == float("inf")
        # v8.13：NaN 分数同样按 null 输出（JSON 序列化 NaN 会 500）
        if isinstance(s, float) and s != s:
            s = None
        ranked_out.append({"model": _to_dict(m), "score": None if free else s, "free": free})
    return {
        "ok": True,
        "metrics": metrics,
        "ranked": ranked_out,
        "unscored": [_to_dict(m) for m in unscored],
    }


# ---------------------------------------------------------------------------
# 三级匹配（char / bm25 / api embedding）
# ---------------------------------------------------------------------------
class RankBody(BaseModel):
    query: str
    docs: list = []
    min_score: float = 0.0
    limit: int = 0
    embedding_level: str = ""   # char | bm25 | api（空=用桌面 config 默认）
    embedding_model: str = ""


@router.post("/matcher/rank")
async def matcher_rank(body: RankBody, user: dict = Depends(current_user)):
    matcher = _matcher()
    q = str(body.query or "").strip()
    docs = [str(d) for d in (body.docs or [])]
    if not q or not docs:
        return {"ok": True, "results": []}
    if len(q) > 2000 or len(docs) > 5000 or sum(len(d) for d in docs) > 1_000_000:
        raise HTTPException(400, "输入过大")
    if body.embedding_level and body.embedding_level not in ("char", "bm25", "api"):
        raise HTTPException(400, "embedding_level 仅支持 char/bm25/api")
    # v8.13：min_score/limit 有限性校验（NaN 会使排序比较异常）
    import math
    min_score = float(body.min_score or 0.0)
    if not math.isfinite(min_score) or not -1.0 <= min_score <= 1.0:
        raise HTTPException(400, "min_score 非法")
    limit = int(body.limit) if body.limit else None
    if limit is not None and not 1 <= limit <= 1000:
        raise HTTPException(400, "limit 非法")
    # P1-2：api 级别需要 api_base_url/api_key（embedding 模型 url 空时用全局兜底；
    # 都空则 matcher.api_embed 返回 None 走 bm25 降级链，而不是 AttributeError 500）
    try:
        from pyqt.desktop.config import get_config as desktop_cfg
        dcfg = desktop_cfg()
        g_base = getattr(dcfg, "api_base_url", "") or ""
        g_key = getattr(dcfg, "api_key", "") or ""
        d_embed = getattr(dcfg, "embedding_model", "") or ""
    except Exception:
        g_base = g_key = d_embed = ""
    cfg = SimpleNamespace(
        embedding_level=body.embedding_level or "char",
        embedding_model=body.embedding_model or d_embed,
        ENABLE_EMBEDDING_API=bool(body.embedding_level == "api"),
        api_base_url=g_base,
        api_key=g_key,
    )
    try:
        res = await asyncio.to_thread(
            matcher.rank, q, docs, cfg, min_score, limit)
    except Exception as e:
        log_error("[web] 匹配失败", e)
        raise HTTPException(500, "匹配失败")
    return {"ok": True, "results": [{"idx": i, "score": round(s, 4)} for i, s in res]}
