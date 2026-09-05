"""状态同步（第四章 4.2）：加密压缩快照导出/导入 + 可选推送到用户自有服务器。

快照内容：会话历史 + 配置 + 资产银行 + 防呆库（不含工作区文件，文档已说明限制）。
加密：口令 → PBKDF2-SHA256 → Fernet(AES128-CBC)。未设口令时仅 zlib 压缩 + base64。
开发者不持有任何副本：数据只存在于用户本机与用户自有服务器。
"""
import base64
import datetime
import hashlib
import json
import os
import zlib
from pathlib import Path

import httpx

from .config import DATA_DIR, get_config
from .storage import load_json

# v8.14：PBKDF2 盐值改为每安装随机生成（原硬编码盐值导致所有用户使用相同密钥派生）
_SALT_PATH = DATA_DIR / "sync_salt.bin"
_FALLBACK_SALT = b"deverai-sync-v1"  # 兼容旧快照


def _load_salt() -> bytes:
    """加载每安装随机盐值；首次使用生成并落盘。"""
    try:
        if _SALT_PATH.exists():
            data = _SALT_PATH.read_bytes()
            if len(data) == 16:
                return data
    except OSError:
        pass
    # 首次使用或文件损坏：生成新盐值
    salt = os.urandom(16)
    try:
        _SALT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SALT_PATH.write_bytes(salt)
    except OSError:
        pass
    return salt


def _derive_key(password: str, salt: bytes = None) -> bytes:
    """PBKDF2 密钥派生。salt 默认使用每安装随机盐值；可指定兼容旧快照。"""
    if salt is None:
        salt = _load_salt()
    return base64.urlsafe_b64encode(
        hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000, dklen=32)
    )


def _fernet():
    from cryptography.fernet import Fernet, InvalidToken  # noqa: F401
    return Fernet


def build_snapshot(history: list, vault=None, extra: dict = None, runtime: dict = None) -> dict:
    cfg = get_config()
    # M13: 快照不含密钥明文
    payload = {
        "version": 1,
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            k: v
            for k, v in cfg.__dict__.items()
            if not k.startswith("_") and k not in (
                "api_key", "sync_password", "sync_token",
                "drift_api_key", "search_api_key", "dashscope_api_key",
                "sync_server_url")
        },
        "history": history,
        "vault": (vault.list() if vault is not None else []),
        "err_mirror": load_json(DATA_DIR / "err_mirror.json", []),
        "extra": extra or {},
        # v6 算力漂移：运行时状态（后台任务/Agent 忙闲/未完成 DAG 线索），服务器冷备 Agent 续跑参考
        "runtime": runtime or {},
    }
    return payload


def export_snapshot_bytes(payload: dict, password: str = "") -> str:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if password:
        try:
            f = _fernet()(_derive_key(password))
            data = f.encrypt(zlib.compress(raw, 9))
        except ImportError:
            # 降级：标记混淆（非密码学强度），导入端按标记解包
            data = zlib.compress(b"obfuscated\x00" + raw, 9)
    else:
        data = zlib.compress(b"obfuscated\x00" + raw, 9)
    return base64.urlsafe_b64encode(data).decode("ascii")


# ---------------- v8.27 真·算力漂移：密钥信封 + 工作区上传 ----------------
_KEYS_FIELDS = ("api_key", "api_base_url", "model",
                "search_api_key", "dashscope_api_key", "drift_api_key")


def export_keys_envelope(cfg, password: str = "") -> str:
    """v8.27 密钥信封：随漂移上传用户自有服务器（服务器仅存密文，续算端凭口令解密到内存）。

    格式 {"v":1,"kind":"deverai-keys","salt":标准b64,"tok":Fernet token}——
    盐值随信封内嵌（不依赖本机 sync_salt.bin，服务器端可独立解密）。
    password 为空返回 ""（不上传密钥，服务器端走既有 drift_api_key 兜底）。
    """
    if not password:
        return ""
    try:
        f_cls = _fernet()
    except ImportError:
        # v8.27 fail-closed：无 cryptography 时拒绝降级混淆（假加密=更危险），密钥不上传，
        # 服务器端改用既有 drift_api_key 兜底。桌面会提示安装 cryptography（requirements.txt 已含）。
        return ""
    keys = {k: str(getattr(cfg, k, "") or "") for k in _KEYS_FIELDS}
    raw = json.dumps(keys, ensure_ascii=False).encode("utf-8")
    salt = os.urandom(16)
    f = f_cls(_derive_key(password, salt))
    tok = f.encrypt(zlib.compress(raw, 9))
    return json.dumps({
        "v": 1, "kind": "deverai-keys",
        "salt": base64.standard_b64encode(salt).decode("ascii"),
        "tok": tok.decode("ascii"),
    }, ensure_ascii=False)


def import_keys_envelope(text: str, password: str) -> dict:
    """解密密钥信封（服务器端续算用，解密结果只进内存）。口令错误/格式非法抛 ValueError。"""
    try:
        obj = json.loads(str(text or ""))
        salt = base64.standard_b64decode(str((obj or {}).get("salt") or ""))
    except Exception as e:
        raise ValueError(f"密钥信封格式非法: {e}")
    if not isinstance(obj, dict) or obj.get("kind") != "deverai-keys":
        raise ValueError("不是 DeverAI 密钥信封")
    tok = str(obj.get("tok") or "").encode("ascii")
    try:
        f = _fernet()(_derive_key(password, salt))
        raw = zlib.decompress(f.decrypt(tok))
    except Exception as e:
        raise ValueError(f"密钥信封解密失败（口令错误或数据损坏）")
    keys = json.loads(raw.decode("utf-8"))
    return keys if isinstance(keys, dict) else {}


def build_drift_upload(cfg, mode: str, since: float = None) -> dict:
    """v8.27 漂移上传打包（推送线程内调用，纯阻塞 IO）。

    mode: minimal（最简：WorkTree 即时变更增量——工作区内 mtime 晚于上次漂移推送的文件，
          恒含 unattended_progress.json）/ full（完整：全工作区）。
    since: 增量基准时间戳；None 时读 data/drift_last_push.json。
    两者都附 data 子集（AI 不可乱动数据，落服务器 data 位，绝不入工作区）：
      checkpoints/、audit.jsonl、agent_memory.json、workspaces/、file_protect.json、
      term_envs.json、drift_state.json。
    排除集与一键备份一致 + 敏感防线（v8.27 阶段3）：工作区局部 data/ 目录与
    config.json/Err.log/api.txt/api_keys.py/.env 等敏感名一律不入包——
    漂移 DeverAI 仓库自身时，明文密钥绝不能混进工作区 zip 击穿密钥信封模型。
    单包上限 64MB（与快照端点限额一致）。
    返回 {"ws_b64": str, "data_b64": str, "mode": mode, "cutoff": 打包开始时刻}。
    """
    import io as _io
    import zipfile as _zipf
    from pathlib import Path as _P
    out = {"ws_b64": "", "data_b64": "", "mode": str(mode or "minimal"),
           "cutoff": datetime.datetime.now().timestamp()}
    cap = 64 * 1024 * 1024
    if since is None:
        try:
            st = load_json(DATA_DIR / "drift_last_push.json", {}) or {}
            since = float(st.get("ts") or 0)
        except Exception:
            since = 0.0
    excl_top = {"backups", "data", ".git", "__pycache__", "node_modules", ".venv",
                "venv", ".idea", ".vscode", ".mypy_cache", ".pytest_cache", "dist",
                "build", ".worktrees"}
    excl_suf = {".pyc", ".pyo", ".tmp"}
    excl_name = {"config.json", "err.log", "api.txt", "api_keys.py", ".env"}

    def _wanted(p, ws: _P):
        rel = p.relative_to(ws)
        if rel.parts and str(rel.parts[0]).lower() in excl_top:
            return False
        if p.suffix.lower() in excl_suf:
            return False
        # v8.27 阶段3：任意层级命中敏感名不入包（防明文密钥/运行数据外带）
        if any(str(part).lower() in excl_name for part in rel.parts):
            return False
        return True

    ws = _P(str(getattr(cfg, "workspace", "") or ""))
    if ws and ws.exists():
        wanted = []
        for p in ws.rglob("*"):
            try:
                if not p.is_file() or p.is_symlink():
                    continue
                if not _wanted(p, ws):
                    continue
                wanted.append(p)
            except (OSError, ValueError):
                continue
        # 清单 = 客户端期望的完整工作区状态（服务器据此差集清理僵尸文件，阶段3）
        out["manifest"] = "\n".join(
            sorted(str(p.relative_to(ws)).replace("\\", "/") for p in wanted))
        files = []
        for p in wanted:
            try:
                if mode == "full" or p.stat().st_mtime > since:
                    files.append(p)
            except OSError:
                continue
        if files:
            try:
                buf = _io.BytesIO()
                with _zipf.ZipFile(buf, "w", _zipf.ZIP_DEFLATED, compresslevel=6) as zf:
                    for p in files:
                        if buf.tell() > cap:
                            break
                        try:
                            zf.write(p, str(p.relative_to(ws)).replace("\\", "/"))
                        except (OSError, ValueError):
                            continue
                if buf.tell():
                    out["ws_b64"] = base64.standard_b64encode(buf.getvalue()).decode("ascii")
            except Exception:
                pass
    try:
        buf = _io.BytesIO()
        n = 0
        with _zipf.ZipFile(buf, "w", _zipf.ZIP_DEFLATED, compresslevel=6) as zf:
            for name in ("audit.jsonl", "agent_memory.json", "file_protect.json",
                         "term_envs.json", "drift_state.json"):
                p = DATA_DIR / name
                try:
                    if p.is_file():
                        zf.write(p, name)
                        n += 1
                except OSError:
                    continue
            for sub in ("checkpoints", "workspaces"):
                d = DATA_DIR / sub
                if d.is_dir():
                    for p in d.rglob("*"):
                        if buf.tell() > cap:
                            break
                        try:
                            if p.is_file() and not p.is_symlink():
                                zf.write(p, f"{sub}/{p.relative_to(d)}")
                                n += 1
                        except (OSError, ValueError):
                            continue
        if n:
            out["data_b64"] = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        pass
    return out


def mark_drift_pushed(ts: float = None) -> None:
    """v8.27：漂移推送成功后记录增量基准时间戳。

    ts 应传「打包开始时刻」（build_drift_upload 返回的 cutoff）而非上传完成时刻——
    否则上传期间被修改的文件会掉出下一次增量（阶段3 修复的竞态）。
    """
    try:
        from .storage import save_json as _sj
        _sj(DATA_DIR / "drift_last_push.json",
            {"ts": float(ts) if ts else datetime.datetime.now().timestamp()})
    except Exception:
        pass


def _import_web_envelope(text: str, password: str = "") -> dict:
    """导入网页版快照格式（v8.13 跨端互通）。

    网页版 /push 使用 JSON 信封：
      {"v":2,"salt":标准b64,"iv":标准b64,"data":标准b64(AES-GCM密文)}   —— 加密主快照
      {"v":2,"kind":"web-cold","data":标准b64(UTF-8 JSON)}               —— 无口令冷备
    桌面版识别后等价还原成 payload；无密码或密钥不对时抛 ValueError。
    """
    obj = json.loads(text)
    if not isinstance(obj, dict) or not isinstance(obj.get("v"), int) or obj.get("v") != 2:
        raise ValueError("网页版快照信封格式不正确。")
    if obj.get("kind") == "web-cold":
        data = obj.get("data") or ""
        try:
            raw = base64.b64decode(data, validate=True)
        except Exception:
            raise ValueError("网页版冷备副本损坏。")
        if not raw:
            raise ValueError("网页版冷备副本为空。")
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("网页版冷备副本内容无法解析。")
    salt = obj.get("salt") or ""
    iv = obj.get("iv") or ""
    data = obj.get("data") or ""
    if not password:
        raise ValueError("该快照来自网页版加密推送，请填写相同的同步加密口令后重试。")
    try:
        salt_b = base64.b64decode(salt, validate=True)
        iv_b = base64.b64decode(iv, validate=True)
        ct_b = base64.b64decode(data, validate=True)
        if len(iv_b) != 12:
            raise ValueError("网页版快照 IV 长度非法。")
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt_b,
                         iterations=200_000)
        key = kdf.derive(password.encode("utf-8"))
        plain = AESGCM(key).decrypt(iv_b, ct_b, None)
    except ValueError:
        raise
    except ImportError:
        raise ValueError("导入网页版加密快照需要安装 cryptography。")
    except Exception:
        raise ValueError("口令错误或网页版快照数据损坏，无法解密。")
    try:
        return json.loads(plain.decode("utf-8"))
    except Exception:
        raise ValueError("网页版快照内容无法解析。")


def import_snapshot_bytes(text: str, password: str = "") -> dict:
    text = str(text or "").strip()
    # v8.13：网页版 JSON 信封（加密主快照 / web-cold 冷备）先识别
    if text.startswith("{"):
        return _import_web_envelope(text, password)
    try:
        data = base64.urlsafe_b64decode(text.encode("ascii"))
    except Exception:
        raise ValueError("快照数据不是有效的 base64。")
    if password:
        try:
            f = _fernet()(_derive_key(password))
            data = f.decrypt(data)
        except ImportError:
            pass  # 未安装 cryptography → 数据为混淆流，继续解压
        except Exception:
            # 兼容旧版本硬编码盐值导出的加密快照：新盐失败后按旧盐再试一次
            try:
                f = _fernet()(_derive_key(password, _FALLBACK_SALT))
                data = f.decrypt(data)
            except ImportError:
                pass
            except Exception:
                raise ValueError("口令错误或数据损坏，无法解密。")
    try:
        plain = zlib.decompress(data)
    except Exception:
        # L-5: 加密导出的快照在无 cryptography 环境导入时给出可操作的提示
        raise ValueError("数据损坏，无法解压。若快照来自加密导出，请先安装 cryptography 库后再导入。")
    if plain.startswith(b"obfuscated"):
        parts = plain.split(b"\x00", 1)
        if len(parts) < 2:
            raise ValueError("快照内容损坏，无法解析。")
        plain = parts[1]
    try:
        return json.loads(plain.decode("utf-8"))
    except Exception:
        raise ValueError("快照内容损坏，无法解析。")


def _sync_headers(cfg) -> dict:
    """同步请求头：配置了 sync_token 时携带 X-Sync-Token。

    v8.11 修复：此前 push/pull/drift 五接口都不发令牌——服务器设置 SYNC_TOKEN 后
    桌面端同步全部 401。令牌与服务器环境变量 SYNC_TOKEN 对应。
    """
    tok = str(getattr(cfg, "sync_token", "") or "").strip()
    return {"X-Sync-Token": tok} if tok else {}


async def push_to_server(cfg, payload: dict, include_cold: bool = False,
                         upload: str = "") -> dict:
    """推送快照到用户自有服务器 /push。

    v6 算力漂移：include_cold=True 时附带一份可读冷备副本（无口令压缩），
    供服务器冷备 Agent 直接解包续聊；加密主副本仍由 sync_password 保护。
    v8.27 真·算力漂移：upload 传 "auto"/"minimal"/"full" 时随推工作区（minimal=WorkTree
    即时变更增量 / full=全工作区）+ data 子集 + 密钥信封（仅密文）；传 "" 不带上传
    （sync_queue 普通冲刷不走上传，避免周期全量推送）。密钥信封仅在配置了 sync_password
    时生成；否则服务器端走既有 drift_api_key 兜底。
    """
    if not cfg.sync_server_url:
        return {"ok": False, "message": "未配置同步服务器地址。"}
    data = export_snapshot_bytes(payload, cfg.sync_password)
    body = {"data": data}
    if include_cold:
        body["cold"] = export_snapshot_bytes(payload, "")
    try:
        from .drift import project_id as _pid
        body["pid"] = str(_pid() or "")
    except Exception:
        body["pid"] = ""
    keys_enc = export_keys_envelope(cfg, cfg.sync_password)
    if keys_enc:
        body["keys_enc"] = keys_enc
    up = None
    if upload:
        mode = upload if upload in ("minimal", "full") \
            else str(getattr(cfg, "drift_upload_mode", "") or "minimal")
        try:
            up = build_drift_upload(cfg, mode, since=None)
        except Exception:
            up = None
    if up:
        # 阶段3：body 总预算（b64 后）≈144MB（服务器上限 192MB），超限丢 ws_zip
        # 保 data 子集+快照（工作区保留服务器上次副本，下轮重传）
        total_b64 = len(up.get("ws_b64") or "") + len(up.get("data_b64") or "")
        if total_b64 > 144 * 1024 * 1024:
            up = dict(up)
            up["ws_b64"] = ""
        if up.get("ws_b64"):
            body["ws_zip"] = up["ws_b64"]
        if up.get("data_b64"):
            body["data_zip"] = up["data_b64"]
        body["ws_manifest"] = str(up.get("manifest") or "")
        body["upload_mode"] = str(up.get("mode") or "")
    url = cfg.sync_server_url.rstrip("/") + "/push"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=body, headers=_sync_headers(cfg))
    except httpx.HTTPError:
        return {"ok": False, "message": "无法连接同步服务器，请检查服务器地址与网络。"}
    if resp.status_code != 200:
        return {"ok": False, "message": f"推送失败 HTTP {resp.status_code}: {resp.text[:200]}"}
    if up:
        # 基准取打包开始时刻：上传期间的变更掉不出下一次增量（阶段3 竞态修复）
        mark_drift_pushed(float(up.get("cutoff") or 0) or None)
    return {"ok": True, "message": "已推送快照到服务器"}


async def pull_from_server(cfg) -> dict:
    """从用户自有服务器拉取最新快照。"""
    if not cfg.sync_server_url:
        return {"ok": False, "message": "未配置同步服务器地址。"}
    url = cfg.sync_server_url.rstrip("/") + "/pull"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=_sync_headers(cfg))
    except httpx.HTTPError:
        return {"ok": False, "message": "无法连接同步服务器，请检查服务器地址与网络。"}
    if resp.status_code != 200:
        return {"ok": False, "message": f"拉取失败 HTTP {resp.status_code}: {resp.text[:200]}"}
    # v8.13：服务器 200 但响应非 JSON（反代错误页/误填普通网址）时返回友好错误而非抛异常
    try:
        resp_data = resp.json()
        if not isinstance(resp_data, dict):
            raise ValueError("非对象")
    except Exception:
        return {"ok": False, "message": "服务器响应格式错误，请确认地址指向 DeverAI 同步服务器"}
    data = resp_data.get("data", "")
    if not data:
        return {"ok": False, "message": "服务器上暂无快照。"}
    try:
        payload = import_snapshot_bytes(data, cfg.sync_password)
    except ValueError:
        # v6 算力漂移：冷备 Agent 续聊后回写的是无口令流，降级按明文流解包
        try:
            payload = import_snapshot_bytes(data, "")
        except ValueError as e:
            return {"ok": False, "message": f"快照解析失败: {e}"}
    # v8.7：/pull 附带冷备副本时，历史以冷备为准（冷备含云端续聊消息，恒为超集；
    # 加密主快照不会被 /chat 更新，云端消息只能从冷备取到）
    cold_raw = resp_data.get("drift_cold", "")
    if cold_raw:
        try:
            cold_payload = import_snapshot_bytes(cold_raw, "")
            if isinstance(cold_payload.get("history"), list):
                payload = {**payload, "history": cold_payload["history"]}
        except ValueError:
            pass  # 冷备损坏不阻塞主快照
    return {"ok": True, "message": "拉取成功", "payload": payload}


async def drift_begin_to_server(cfg, project_id: str) -> dict:
    """登记「正在算力漂移」到服务器（复位云端未回传计数）。"""
    if not cfg.sync_server_url:
        return {"ok": False, "message": "未配置同步服务器地址。"}
    url = cfg.sync_server_url.rstrip("/") + "/drift/begin"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json={"project_id": project_id, "active": True},
                                     headers=_sync_headers(cfg))
    except httpx.HTTPError:
        return {"ok": False, "message": "无法连接同步服务器，请检查服务器地址与网络。"}
    if resp.status_code != 200:
        return {"ok": False, "message": f"登记失败 HTTP {resp.status_code}: {resp.text[:200]}"}
    # v8.15 检修：对齐 pull_from_server——200 但非 JSON（反代错误页）时友好报错而非抛异常
    try:
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("非对象")
    except Exception:
        return {"ok": False, "message": "服务器响应格式错误，请确认地址指向 DeverAI 同步服务器"}
    return {"ok": True, **data}


async def drift_status_from_server(cfg, project_id: str = "") -> dict:
    """查询服务器是否仍有未回传的云端产出（用于开机判断是否锁定项目）。"""
    if not cfg.sync_server_url:
        return {"ok": False, "message": "未配置同步服务器地址。"}
    url = cfg.sync_server_url.rstrip("/") + "/drift/status"
    params = {"project_id": project_id} if project_id else {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=_sync_headers(cfg))
    except httpx.HTTPError:
        return {"ok": False, "message": "无法连接同步服务器，请检查服务器地址与网络。"}
    if resp.status_code != 200:
        return {"ok": False, "message": f"查询失败 HTTP {resp.status_code}: {resp.text[:200]}"}
    # v8.15 检修：同上，防 200+HTML 反代页炸出未捕获异常
    try:
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("非对象")
    except Exception:
        return {"ok": False, "message": "服务器响应格式错误，请确认地址指向 DeverAI 同步服务器"}
    return {"ok": True, **data}


async def drift_finish_to_server(cfg, project_id: str) -> dict:
    """本地确认已把算力漂移回本地，复位服务器漂移状态。"""
    if not cfg.sync_server_url:
        return {"ok": False, "message": "未配置同步服务器地址。"}
    url = cfg.sync_server_url.rstrip("/") + "/drift/finish"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json={"project_id": project_id},
                                     headers=_sync_headers(cfg))
    except httpx.HTTPError:
        return {"ok": False, "message": "无法连接同步服务器，请检查服务器地址与网络。"}
    if resp.status_code != 200:
        return {"ok": False, "message": f"回收失败 HTTP {resp.status_code}: {resp.text[:200]}"}
    # v8.15 检修：同上，防 200+HTML 反代页炸出未捕获异常
    try:
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("非对象")
    except Exception:
        return {"ok": False, "message": "服务器响应格式错误，请确认地址指向 DeverAI 同步服务器"}
    return {"ok": True, **data}


def merge_drift_history(local: list, payload: dict) -> list:
    """v6 算力漂移：开机拉取后，把服务器冷备 Agent 期间追加的消息合并回本地历史。

    只合并带 from_server 标记的条目；以 (role, content 前 200 字, drift_ts) 去重，
    避免重复关机/开机循环造成消息翻倍。返回合并后的历史列表。
    """
    remote = payload.get("history") or []
    seen = set()
    for m in local:
        if isinstance(m, dict):
            seen.add((m.get("role"), str(m.get("content", ""))[:200], m.get("drift_ts", "")))
    added = []
    for m in remote:
        if not isinstance(m, dict) or not m.get("from_server"):
            continue
        key = (m.get("role"), str(m.get("content", ""))[:200], m.get("drift_ts", ""))
        if key in seen:
            continue
        seen.add(key)
        added.append(m)
    return list(local) + added
