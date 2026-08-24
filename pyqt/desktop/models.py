"""模型注册表（v5）：data/models.json 读写、调用配置解析、加权排名（b.1）与性价比排名（b.2）。

每个模型条目字段（用户拍板的全集）：
- 基础：id / name（展示名）/ url / api_key（空=用全局）/ context_size / max_output
- 能力 caps：attachment（附件输入）/ image（图片输入）/ web_search（自主搜索）/ crawl（自主爬取）
- 介绍 intro；操作说明 manual（支持的参数与范围，如 Kimi K 系 temperature 锁 1）
- 分数 scores：{指标名: {"value": float|None, "fixed": bool, "updated": "ISO日期"}}（value=None 为未定）
- 价格 prices：{"in": 每百万输入价, "out": 每百万输出价}（0 = 免费/未知）
- billing_coef：按 token 计费的计费系数 n（高级专家=输出为主，n 大）
- 高级专家展示：ctx_limit_expert（0=自动按 context_size）/ display_level（full|partial|minimal）
  / display_locked（True = 用户写死，总司令不可改，且规划初始上下文时被告知）
"""
from dataclasses import dataclass, field, asdict, replace
from datetime import date
import threading
from typing import Optional

from .config import DATA_DIR, Config, get_config
from .storage import load_json, save_json

MODELS_PATH = DATA_DIR / "models.json"

DISPLAY_LEVELS = ("full", "partial", "minimal")  # 全展示 / 部分展示 / 少量展示

# v8.12：models.json 读-改-写进程级互斥（桌面 GUI 线程 / web 端点线程 / auto_score
# 后台线程可能并发改注册表——此前依赖各自调用方的锁，跨上下文 lost update）
_io_lock = threading.Lock()


def _default_caps() -> dict:
    return {"attachment": False, "image": False, "web_search": False, "crawl": False}


def _default_scores() -> dict:
    cfg = get_config()
    return {m: {"value": None, "fixed": False, "updated": ""} for m in cfg.score_metrics}


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    kind: str = "chat"   # v8.1: chat | embedding（独立 Embedding 条目）
    url: str = ""
    api_key: str = ""
    context_size: int = 32000
    max_output: int = 4096
    caps: dict = field(default_factory=_default_caps)
    intro: str = ""
    manual: str = ""
    scores: dict = field(default_factory=_default_scores)
    prices: dict = field(default_factory=lambda: {"in": 0.0, "out": 0.0})
    billing_coef: float = 1.0
    ctx_limit_expert: int = 0
    display_level: str = "full"
    display_locked: bool = False

    def display_name(self) -> str:
        return self.name or self.id

    def score_summary(self) -> str:
        """定分数量摘要：如 4/6 已定。"""
        vals = list(self.scores.values())
        if not vals:
            return "无指标"
        fixed = sum(1 for s in vals if s.get("value") is not None)
        return f"{fixed}/{len(vals)} 已定"

    def effective_price(self, in_out_ratio: float = 9.0) -> float:
        """按输入输出比折算的加权单价（每百万 token）。免费模型按 0 处理。"""
        pin = float(self.prices.get("in") or 0)
        pout = float(self.prices.get("out") or 0)
        if pin <= 0 and pout <= 0:
            return 0.0
        r = max(0.1, in_out_ratio)
        return (pin * r + pout) / (r + 1)


# ---------------------------------------------------------------- 读写

def model_from_dict(d: dict) -> ModelInfo:
    """从 dict 构建注册表条目；字段不合法抛 KeyError/TypeError/ValueError（调用方跳过）。"""
    base = _default_scores()
    base.update(d.get("scores") or {})
    caps = _default_caps()
    caps.update(d.get("caps") or {})
    return ModelInfo(
        id=d["id"],
        name=d.get("name", ""),
        kind=d.get("kind", "chat"),
        url=d.get("url", ""),
        api_key=d.get("api_key", ""),
        context_size=int(d.get("context_size", 32000)),
        max_output=int(d.get("max_output", 4096)),
        caps=caps,
        intro=d.get("intro", ""),
        manual=d.get("manual", ""),
        scores=base,
        prices={"in": float((d.get("prices") or {}).get("in", 0)),
                "out": float((d.get("prices") or {}).get("out", 0))},
        billing_coef=float(d.get("billing_coef", 1.0)),
        ctx_limit_expert=int(d.get("ctx_limit_expert", 0)),
        display_level=d.get("display_level", "full"),
        display_locked=bool(d.get("display_locked", False)),
    )


def load_models() -> list:
    data = load_json(MODELS_PATH, [])
    out = []
    for d in data:
        try:
            out.append(model_from_dict(d))
        except (KeyError, TypeError, ValueError):
            continue  # 坏条目跳过，不让注册表整体崩
    return out


def save_models(models: list) -> None:
    save_json(MODELS_PATH, [asdict(m) for m in models])


def get_model(model_id: str) -> Optional[ModelInfo]:
    if not model_id:
        return None
    for m in load_models():
        if m.id == model_id:
            return m
    return None


def list_embedding_models() -> list:
    """v8.1：注册表中 kind=embedding 的独立 Embedding 条目。"""
    return [m for m in load_models() if m.kind == "embedding"]


def get_embedding_model(cfg) -> Optional[ModelInfo]:
    """v8.1：取当前 Embedding 模型（优先 cfg.embedding_model，否则第一个 embedding 条目）。"""
    try:
        if cfg.embedding_model:
            for m in load_models():
                if m.kind == "embedding" and m.id == cfg.embedding_model:
                    return m
        for m in load_models():
            if m.kind == "embedding":
                return m
    except Exception:
        return None
    return None


def list_vision_models() -> list:
    """v8.4：注册表中支持图像输入的模型（caps.image=True）。"""
    return [m for m in load_models() if (m.caps or {}).get("image")]


def get_visual_expert_model(cfg) -> Optional[ModelInfo]:
    """v8.4：视觉审查专家模型——配置优先（cfg.visual_expert_model），
    否则自动从注册表选第一个 caps.image=True 的模型。"""
    try:
        if getattr(cfg, "visual_expert_model", ""):
            for m in load_models():
                if m.id == cfg.visual_expert_model and (m.caps or {}).get("image"):
                    return m
        for m in list_vision_models():
            return m
    except Exception:
        return None
    return None


def upsert_model(info: ModelInfo) -> None:
    with _io_lock:
        models = load_models()
        for i, m in enumerate(models):
            if m.id == info.id:
                models[i] = info
                break
        else:
            models.append(info)
        save_models(models)


def delete_model(model_id: str) -> bool:
    with _io_lock:
        models = load_models()
        n = len(models)
        models = [m for m in models if m.id != model_id]
        save_models(models)
        return len(models) < n


def ensure_seed(cfg: Config) -> None:
    """首次使用：把当前主模型种进注册表（已存在同名则不动）。"""
    # v8.14: 写盘纳入 _io_lock（与 upsert/set_score 锁纪律一致）
    with _io_lock:
        models = load_models()
        if any(m.id == cfg.model for m in models):
            return
        models.append(ModelInfo(
            id=cfg.model,
            name=f"{cfg.model}（主模型）",
            url=cfg.api_base_url,
            context_size=32000,
            max_output=cfg.max_tokens or 4096,
            intro="当前主模型，自动导入。请补充介绍与分数。",
        ))
        save_models(models)


# ---------------------------------------------------------------- 调用配置

def get_llm_cfg(cfg: Config, model_id: str) -> Config:
    """按注册表条目生成可直接传给 llm.py 的调用配置；找不到条目回退全局。
    永远返回新对象（replace）：调用方可安全改参而不污染共享 cfg（P0）。"""
    m = get_model(model_id)
    if m is None:
        return replace(cfg)
    return replace(
        cfg,
        model=m.id,
        api_base_url=m.url or cfg.api_base_url,
        api_key=m.api_key or cfg.api_key,
        max_tokens=m.max_output or cfg.max_tokens,
    )


def resolve_model_id(cfg: Config, model_id: str) -> str:
    """空/无效 id 回退主模型；主模型为空则回退第一个可选 chat 模型（用户拍板）。

    注意：主模型非空但不在注册表（可能是手填的模型 id）时，仍返回主模型本身，
    用全局 base_url/key 调用，而不是强行回退到注册表第一个——否则测试/手填模型会连错服务。
    """
    if model_id and get_model(model_id) is not None:
        return model_id
    mid = getattr(cfg, "model", "") or ""
    if mid:
        return mid
    for m in load_models():
        if m.kind != "embedding":
            return m.id
    return ""


# ---------------------------------------------------------------- 排名

def weighted_rank(models: list, weights: dict, top: int = 3) -> list:
    """b.1 性能优先：按总司令赋权加权求和排名。返回 [(ModelInfo, 加权分)]，分数降序。
    无任何定分的模型不参与排名。"""
    ranked = []
    for m in models:
        total_w, total = 0.0, 0.0
        for metric, w in weights.items():
            s = (m.scores or {}).get(metric) or {}
            v = s.get("value")
            if v is None:
                continue
            total_w += float(w)
            total += float(v) * float(w)
        if total_w > 0:
            ranked.append((m, round(total / total_w, 3)))
    ranked.sort(key=lambda x: -x[1])
    return ranked[:top] if top else ranked


def value_rank(models: list, weights: dict, cfg: Config, top: int = 3) -> list:
    """b.2 性价比优先：加权分 ÷ 折算单价。免费模型（价格 0）按性价比最高处理。"""
    ratio = max(0.1, float(getattr(cfg, "cost_ratio_in_out", 9.0)))
    base = weighted_rank(models, weights, top=0)
    out = []
    for m, score in base:
        price = m.effective_price(ratio)
        value = float("inf") if price <= 0 else round(score / price, 4)
        out.append((m, value))
    out.sort(key=lambda x: -(x[1] if x[1] != float("inf") else 1e18))
    return out[:top] if top else out


def set_score(model_id: str, metric: str, value: Optional[float],
              fixed: Optional[bool] = None) -> bool:
    """打分写入（打分执行模型/手动）。fixed 缺省：有值即定。"""
    with _io_lock:
        models = load_models()
        for m in models:
            if m.id == model_id:
                s = (m.scores or {}).get(metric) or {"value": None, "fixed": False, "updated": ""}
                s["value"] = value
                s["updated"] = date.today().isoformat()
                s["fixed"] = (fixed if fixed is not None else (value is not None))
                m.scores[metric] = s
                save_models(models)
                return True
    return False


def unscored_queue(models: list) -> list:
    """未定队列：任一指标 value=None 的模型。"""
    return [m for m in models
            if any((s or {}).get("value") is None for s in (m.scores or {}).values())]
