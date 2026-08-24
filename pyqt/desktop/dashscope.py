"""DashScope（通义万相）API 客户端：文生图 / 图生图 / 视频生成。

轻量化实现：直接使用 httpx 调用 DashScope REST API，不引入 dashscope SDK（避免新增依赖）。
支持模型列表见 model-library/docs/api-reference.md 与 data/models.json 中 series=wan/wanx/qwen-image 等。

能力：
- image_generate(prompt, model, size, n, style, negative_prompt)  —— 文生图（同步）
- image_edit(image_url, prompt, model)                           —— 图生图（同步）
- video_generate(prompt, model, image_url=None)                  —— 文生视频/图生视频（异步）
- task_status(task_id)                                           —— 查询异步任务状态
- list_models(category)                                          —— 列出可用模型（读 model-library/models.json）

安全：
- API Key 从 cfg.dashscope_api_key 读取，不落盘、不回传。
- 所有网络异常转为友好中文，不抛裸异常。
- 输入参数限长/限范围，防止 LLM 注入过大请求。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import httpx

from .config import APP_DIR, get_config

# API 端点
_API_BASE = "https://dashscope.aliyuncs.com/api/v1"
_IMAGE_ENDPOINT = f"{_API_BASE}/services/aigc/text2image/image-synthesis"
_VIDEO_ENDPOINT = f"{_API_BASE}/services/aigc/video-generation/video-synthesis"
_TASK_ENDPOINT = f"{_API_BASE}/tasks"

# 参数限制
_MAX_PROMPT_LEN = 2000       # prompt 最大字符
_MAX_NEGATIVE_LEN = 1000     # negative_prompt 最大字符
_MAX_N = 4                   # 单次最大生成数量
_TIMEOUT = 120.0             # 同步调用超时（秒）
_POLL_INTERVAL = 3.0         # 异步轮询间隔（秒）
_MAX_POLL_ATTEMPTS = 120     # 最大轮询次数（3s * 120 = 6min）

# model-library/models.json 路径（与本模块独立，缺失不报错）
_MODELS_JSON = APP_DIR.parent / "model-library" / "models.json"

# 模块级共享 httpx.Client（连接复用，避免每次调用新建 TCP 连接）
# 线程安全：httpx.Client 内部使用连接池，多线程共享安全
_http_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    """获取模块级共享 httpx.Client（懒初始化，连接复用）。"""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.Client(
            timeout=httpx.Timeout(_TIMEOUT, connect=15.0),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )
    return _http_client


def _get_api_key() -> str:
    """从配置读取 DashScope API Key。"""
    cfg = get_config()
    key = getattr(cfg, "dashscope_api_key", "") or ""
    return str(key).strip()


def _has_api_key() -> bool:
    return bool(_get_api_key())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }


def _truncate(s: str, max_len: int) -> str:
    s = str(s or "")
    return s[:max_len] if len(s) > max_len else s


# ---------------------------------------------------------------- 模型列表
def list_models(category: str = "") -> dict:
    """读取 model-library/models.json 列出可用模型。

    category 过滤：text-to-image / video-generation / image-edit / 留空返回全部。
    返回 {ok, models:[{id,name,series,category,quality,callType,freeQuota,status}], count}。
    """
    if not _MODELS_JSON.exists():
        return {"ok": False, "output": "model-library/models.json 不存在，无法列出模型。"}
    try:
        data = json.loads(_MODELS_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "output": f"读取 models.json 失败: {e}"}
    models = data.get("models", [])
    if category:
        models = [m for m in models if m.get("category") == category]
    # 只返回关键字段，减少 token
    slim = []
    for m in models:
        slim.append({
            "id": m.get("id", ""),
            "name": m.get("name", ""),
            "series": m.get("series", ""),
            "category": m.get("category", ""),
            "categoryLabel": m.get("categoryLabel", ""),
            "quality": m.get("quality", ""),
            "callType": m.get("callType", "sync"),
            "status": m.get("status", ""),
            "freeQuota": m.get("freeQuota", 0),
            "apiModelName": m.get("apiModelName", m.get("id", "")),
        })
    return {"ok": True, "models": slim, "count": len(slim)}


# ---------------------------------------------------------------- 文生图
def image_generate(
    prompt: str,
    model: str = "wan2.7-image-pro",
    size: str = "1024*1024",
    n: int = 1,
    style: str = "<auto>",
    negative_prompt: str = "",
) -> dict:
    """文生图（同步调用）。

    返回 {ok, output, images:[{url}], model, request_id}。
    images 中的 url 可直接浏览器打开查看。
    """
    if not _has_api_key():
        return {"ok": False, "output": "未配置 DashScope API Key（dashscope_api_key），请在设置中填写。"}
    prompt = _truncate(prompt, _MAX_PROMPT_LEN).strip()
    if not prompt:
        return {"ok": False, "output": "prompt 不能为空。"}
    try:
        n = max(1, min(int(n), _MAX_N))
    except (TypeError, ValueError):
        n = 1
    negative_prompt = _truncate(negative_prompt, _MAX_NEGATIVE_LEN)
    # 校验 size 格式（宽*高，数字*数字）
    size = str(size or "1024*1024")
    import re as _re
    if not _re.match(r"^\d+\*\d+$", size):
        size = "1024*1024"

    body = {
        "model": str(model or "wan2.7-image-pro"),
        "input": {"prompt": prompt},
        "parameters": {"size": str(size or "1024*1024"), "n": n, "style": str(style or "<auto>")},
    }
    if negative_prompt:
        body["input"]["negative_prompt"] = negative_prompt

    try:
        _client = _get_client()
        resp = _client.post(_IMAGE_ENDPOINT, json=body, headers=_headers())
        if resp.status_code == 401:
            return {"ok": False, "output": "API Key 无效（401），请检查 dashscope_api_key。"}
        if resp.status_code == 403:
            return {"ok": False, "output": "无权限或额度已用完（403），请检查 DashScope 控制台。"}
        if resp.status_code == 429:
            return {"ok": False, "output": "请求频率限制（429），请稍后再试。"}
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        return {"ok": False, "output": f"网络请求失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"ok": False, "output": f"请求异常: {type(e).__name__}: {e}"}

    # 解析输出
    output = data.get("output") or {}
    image_urls = []
    results = output.get("results") or []
    for r in results:
        url = r.get("url") or ""
        if url:
            image_urls.append({"url": url})

    if not image_urls:
        # 可能返回错误信息
        code = data.get("code", "")
        msg = data.get("message", "")
        return {"ok": False, "output": f"生成失败: [{code}] {msg}" if code else "生成失败，未返回图片 URL。"}

    return {
        "ok": True,
        "output": f"已生成 {len(image_urls)} 张图片（模型: {body['model']}）",
        "images": image_urls,
        "model": body["model"],
        "request_id": data.get("request_id", ""),
    }


# ---------------------------------------------------------------- 图生图
def image_edit(
    image_url: str,
    prompt: str,
    model: str = "qwen-image-edit-max",
) -> dict:
    """图生图（同步调用）：对输入图片按 prompt 描述进行编辑。

    image_url: 输入图片的 URL（http(s):// 或 data:image/...）。
    返回 {ok, output, images:[{url}], model, request_id}。
    """
    if not _has_api_key():
        return {"ok": False, "output": "未配置 DashScope API Key（dashscope_api_key），请在设置中填写。"}
    image_url = str(image_url or "").strip()
    if not image_url:
        return {"ok": False, "output": "缺少输入图片 URL（image_url）。"}
    prompt = _truncate(prompt, _MAX_PROMPT_LEN).strip()
    if not prompt:
        return {"ok": False, "output": "prompt 不能为空。"}

    body = {
        "model": str(model or "qwen-image-edit-max"),
        "input": {"image": image_url, "prompt": prompt},
    }

    try:
        _client = _get_client()
        resp = _client.post(_IMAGE_ENDPOINT, json=body, headers=_headers())
        if resp.status_code == 401:
            return {"ok": False, "output": "API Key 无效（401）。"}
        if resp.status_code == 403:
            return {"ok": False, "output": "无权限或额度已用完（403）。"}
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        return {"ok": False, "output": f"网络请求失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"ok": False, "output": f"请求异常: {type(e).__name__}: {e}"}

    output = data.get("output") or {}
    image_urls = []
    for r in (output.get("results") or []):
        url = r.get("url") or ""
        if url:
            image_urls.append({"url": url})

    if not image_urls:
        code = data.get("code", "")
        msg = data.get("message", "")
        return {"ok": False, "output": f"编辑失败: [{code}] {msg}" if code else "编辑失败，未返回图片 URL。"}

    return {
        "ok": True,
        "output": f"图片编辑完成（模型: {body['model']}）",
        "images": image_urls,
        "model": body["model"],
        "request_id": data.get("request_id", ""),
    }


# ---------------------------------------------------------------- 视频生成（异步）
def video_generate(
    prompt: str,
    model: str = "wan2.7-t2v",
    image_url: str = "",
) -> dict:
    """文生视频 / 图生视频（异步调用）。

    先提交任务，再轮询结果。返回 {ok, output, video_url, task_id, model}。
    image_url 非空时走图生视频（i2v），否则走文生视频（t2v）。
    """
    if not _has_api_key():
        return {"ok": False, "output": "未配置 DashScope API Key（dashscope_api_key），请在设置中填写。"}
    prompt = _truncate(prompt, _MAX_PROMPT_LEN).strip()
    if not prompt:
        return {"ok": False, "output": "prompt 不能为空。"}
    # 校验 image_url 格式（如果提供）
    if image_url:
        image_url = str(image_url).strip()
        if not image_url.startswith(("http://", "https://", "data:image/")):
            return {"ok": False, "output": "image_url 必须以 http://、https:// 或 data:image/ 开头。"}

    body = {
        "model": str(model or "wan2.7-t2v"),
        "input": {"prompt": prompt},
    }
    if image_url:
        body["input"]["image_url"] = image_url

    # Step 1: 提交任务
    try:
        _client = _get_client()
        resp = _client.post(_VIDEO_ENDPOINT, json=body, headers=_headers())
        if resp.status_code in (401, 403, 429):
            return {"ok": False, "output": f"提交任务失败（HTTP {resp.status_code}）。"}
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        return {"ok": False, "output": f"提交任务失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"ok": False, "output": f"提交任务异常: {type(e).__name__}: {e}"}

    output = data.get("output") or {}
    task_id = output.get("task_id") or ""
    if not task_id:
        return {"ok": False, "output": "提交任务失败，未返回 task_id。"}

    # Step 2: 轮询任务状态
    task_result = _poll_task(task_id)
    if not task_result.get("ok"):
        return task_result

    return {
        "ok": True,
        "output": f"视频生成完成（模型: {body['model']}）",
        "video_url": task_result.get("video_url", ""),
        "task_id": task_id,
        "model": body["model"],
    }


def _poll_task(task_id: str) -> dict:
    """轮询异步任务直到完成或超时。认证错误立即返回，不浪费轮询时间。"""
    for _attempt in range(_MAX_POLL_ATTEMPTS):
        time.sleep(_POLL_INTERVAL)
        try:
            _client = _get_client()
            resp = _client.get(f"{_TASK_ENDPOINT}/{task_id}", headers=_headers())
            if resp.status_code in (401, 403):
                return {"ok": False, "output": f"认证失败（HTTP {resp.status_code}），请检查 dashscope_api_key。", "task_id": task_id}
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            # 网络错误降级继续轮询（可能是临时抖动），但记录
            continue
        except Exception:
            continue
        output = data.get("output") or {}
        status = str(output.get("task_status", "")).upper()
        if status == "SUCCEEDED":
            return {"ok": True, "video_url": output.get("video_url", ""), "task_id": task_id}
        if status in ("FAILED", "CANCELED"):
            msg = output.get("message") or output.get("error_msg") or "未知错误"
            return {"ok": False, "output": f"任务 {status}: {msg}", "task_id": task_id}
        # PENDING / RUNNING → 继续轮询
    return {"ok": False, "output": f"轮询超时（{_MAX_POLL_ATTEMPTS * _POLL_INTERVAL}s），任务可能仍在运行，可用 task_status 查询 task_id={task_id}。", "task_id": task_id}


def task_status(task_id: str) -> dict:
    """查询异步任务状态（手动查询）。"""
    if not _has_api_key():
        return {"ok": False, "output": "未配置 DashScope API Key。"}
    task_id = str(task_id or "").strip()
    if not task_id:
        return {"ok": False, "output": "task_id 不能为空。"}
    try:
        _client = _get_client()
        resp = _client.get(f"{_TASK_ENDPOINT}/{task_id}", headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        return {"ok": False, "output": f"查询失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"ok": False, "output": f"查询异常: {type(e).__name__}: {e}"}

    output = data.get("output") or {}
    status = str(output.get("task_status", "")).upper()
    result = {
        "ok": True,
        "task_id": task_id,
        "status": status,
        "output": f"任务状态: {status}",
    }
    if status == "SUCCEEDED":
        result["video_url"] = output.get("video_url", "")
        result["output"] = f"任务已完成，视频 URL: {result['video_url']}"
    elif status in ("FAILED", "CANCELED"):
        msg = output.get("message") or output.get("error_msg") or "未知错误"
        result["output"] = f"任务 {status}: {msg}"
    return result
