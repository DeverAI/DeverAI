# -*- coding: utf-8 -*-
"""DeverAI 三版本服务端全量冒烟（v8.15/v8.16 轮）。

对 lite 与 webui 完整版各起一个真实 uvicorn 实例（独立端口 + 独立 DEVERAI_DATA_DIR
+ 独立工作区），跑注册/登录/鉴权/工作区授权/fs 中文读写/tree/grep/run_command SSE/
危险命令审批门（含严格布尔与新正则精度）等检查。桌面 GUI 由 tests/test_desktop_offscreen.py
覆盖，不在本文件范围。

v8.24：新增 sync_server.py 冒烟段——mock LLM 端到端续聊（/push → /chat → /pull →
/drift 状态机）、桌面 zlib 与网页 web-cold 双格式快照兼容、加密主快照永不降级、
远程指挥 /cmd/* 全链路（register → dispatch → poll → result → nodes/results）。

运行方式（仓库根目录）：python tests/test_smoke_servers.py
退出码纪律：任何一项失败 → 退出码 1；全部通过 → 0。
"""
import base64
import os
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


class _Res:
    def __init__(self):
        self.failures = []

    def check(self, name, cond, extra=""):
        print(f"{'[OK]' if cond else '[X]'} {name}" + (f" | {extra}" if extra and not cond else ""))
        if not cond:
            self.failures.append(name)
        return cond


def wait_ready(url: str, timeout_s: float = 25.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = httpx.get(url + "/api/server/info", timeout=2.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def sse_run(client: httpx.Client, url_base: str, payload: dict, cap_s: float = 30.0):
    """POST run_command 并收集 SSE 事件直到 done/error。返回 (events, http_status)。"""
    events = []
    status = None
    with client.stream("POST", url_base + "/api/bridge/run_command",
                       json=payload, timeout=cap_s) as r:
        status = r.status_code
        if r.status_code != 200:
            return events, status
        event_name = ""
        for line in r.iter_lines():
            if line is None:
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:"):
                try:
                    data = json.loads(line.split(":", 1)[1].strip())
                except Exception:
                    data = line.split(":", 1)[1].strip()
                events.append((event_name or "message", data))
                if event_name in ("done", "error") or (isinstance(data, dict) and data.get("rc") is not None):
                    break
    return events, status


def smoke_version(res: _Res, tag: str, entry_dir: Path, port: int,
                  username: str, password: str, expect_bearer_token: bool,
                  expect_term: bool = False):
    tmp = Path(tempfile.mkdtemp(prefix=f"deverai_smoke_{tag}_"))
    data_dir = tmp / "data"
    ws = tmp / "ws"
    ws_path = tmp / "ws"   # v8.29：raw-image 冒烟用原始 Path（ws 变量后文被 Response 覆写）
    ws.mkdir(parents=True, exist_ok=True)
    # v8.21：把测试工作区初始化为 git 仓库（worktree/list 等端点依赖 git）
    try:
        subprocess.run(["git", "init"], cwd=str(ws), capture_output=True, timeout=10)
        subprocess.run(["git", "config", "user.email", "smoke@test.local"], cwd=str(ws),
                       capture_output=True, timeout=5)
        subprocess.run(["git", "config", "user.name", "Smoke"], cwd=str(ws),
                       capture_output=True, timeout=5)
        (ws / "README").write_text("smoke", encoding="utf-8")
        subprocess.run(["git", "add", "README"], cwd=str(ws), capture_output=True, timeout=5)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), capture_output=True, timeout=10)
    except Exception:
        pass

    env = os.environ.copy()
    env["DEVERAI_DATA_DIR"] = str(data_dir)
    env["DEVERAI_RUNTIME_PORT"] = str(port)
    proc = None
    client = None
    try:
        # v8.16.1 复检：Popen 移入 try，消除"启动即异常→子进程泄漏"窗口
        proc = subprocess.Popen(
            [PY, "lite_main.py" if tag == "lite" else "web_main.py",
             "--port", str(port), "--no-browser"],
            cwd=str(entry_dir), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        if not res.check(f"{tag}: 服务就绪", wait_ready(base)):
            return

        # S1 server/info
        r = httpx.get(base + "/api/server/info", timeout=5)
        info = {}
        try:
            info = r.json()
        except Exception:
            pass
        res.check(f"{tag}: server/info 200", r.status_code == 200)
        res.check(f"{tag}: allow_register 字段", isinstance(info.get("allow_register"), bool))

        client = httpx.Client(base_url=base, timeout=15)

        # S2 注册 → 会话 Cookie
        r = client.post("/api/auth/register", json={"username": username, "password": password})
        set_cookie = "deverai_session" in (r.headers.get("set-cookie") or "") or \
            any("deverai_session" in c for c in (r.headers.get_list("set-cookie")
                                                 if hasattr(r.headers, "get_list") else []))
        res.check(f"{tag}: 注册成功且回会话Cookie", r.status_code == 200 and set_cookie,
                  f"status={r.status_code}")
        token = ""
        try:
            token = str((r.json() or {}).get("token") or "")
        except Exception:
            pass

        # S3 Bearer 令牌访问 me（v8.12 契约）——
        # Fact.md v8.13 刻意差异：完整版返回 curl 可复现 session token，Lite 不返回。
        # 两种契约在此都被断言锁死，防止单侧漂移。
        has_token = bool(token)
        res.check(f"{tag}: 注册令牌契约({ '应下发' if expect_bearer_token else '不下发' })",
                  has_token == expect_bearer_token, f"token={'有' if has_token else '无'}")
        authed = False
        if token:
            r = httpx.get(base + "/api/auth/me",
                          headers={"Authorization": f"Bearer {token}"}, timeout=5)
            authed = r.status_code == 200 and (r.json() or {}).get("username") == username
        if expect_bearer_token:
            res.check(f"{tag}: Bearer me 鉴权", authed)
        else:
            res.check(f"{tag}: Lite 无令牌（Cookie 路径兜底）", not has_token)

        # S4 cookie 访问 me
        r = client.get("/api/auth/me")
        res.check(f"{tag}: Cookie me 鉴权", r.status_code == 200 and
                  (r.json() or {}).get("username") == username)

        # S5 工作区授权
        r = client.post("/api/bridge/workspace", json={"path": str(ws)})
        ok_body = {}
        try:
            ok_body = r.json()
        except Exception:
            pass
        res.check(f"{tag}: 工作区授权", r.status_code == 200 and ok_body.get("authorized") is not False,
                  f"status={r.status_code} body={str(ok_body)[:120]}")

        # S6 中文写入 → 读取往返
        rel = "你好目录/说明.md"
        content = "# 冒烟测试\n中文内容 ABC123 ✓ 结尾"
        w = client.post("/api/bridge/fs/write", json={"path": rel, "content": content})
        res.check(f"{tag}: fs/write 中文路径与内容", w.status_code == 200 and
                  (w.json() or {}).get("ok") is True, f"status={w.status_code}")
        rd = client.get("/api/bridge/fs/read", params={"path": rel})
        body = {}
        try:
            body = rd.json()
        except Exception:
            pass
        got = body.get("content") if isinstance(body, dict) else None
        res.check(f"{tag}: fs/read 往返一致", got == content,
                  f"got={repr(got)[:80]}")

        # S7 tree 为单层列举设计：根列表含新目录；进入该目录列出文件
        t = client.get("/api/bridge/fs/tree")
        root_ok = t.status_code == 200 and ("你好目录" in t.text)
        t2 = client.get("/api/bridge/fs/tree", params={"path": "你好目录"})
        leaf_ok = t2.status_code == 200 and ("说明.md" in t2.text)
        res.check(f"{tag}: fs/tree 根含目录且子层含文件", root_ok and leaf_ok,
                  f"root={t.status_code}/{root_ok} sub={t2.status_code}/{leaf_ok}")

        # S8 grep 中文命中
        g = client.get("/api/bridge/fs/grep", params={"pattern": "ABC123"})
        res.check(f"{tag}: fs/grep 命中", g.status_code == 200 and ("说明.md" in g.text))

        # S8.5 v8.25/v8.26 用户文件保护 + 工作副本（禁碰=拷贝出去改）。
        # v8.26 裁决：Lite 独立版不接（守卫在 lite.html 客户端），server 级断言仅 webui。
        if tag != "lite":
            asset_rel = "资料/答辩.pptx"
            asset_bytes = b"PK\x03\x04 smoke fake pptx"
            (ws / "资料").mkdir(parents=True, exist_ok=True)
            (ws / "资料" / "答辩.pptx").write_bytes(asset_bytes)
            wblk = client.post("/api/bridge/fs/write",
                               json={"path": asset_rel, "content": "x"})
            blk_text = ""
            try:
                blk_text = wblk.text
            except Exception:
                pass
            res.check(f"{tag}: 用户资产 fs/write 被拦(403+引导)",
                      wblk.status_code == 403 and "copy_user_asset" in blk_text,
                      f"status={wblk.status_code}")
            wc = client.post("/api/bridge/fs/workcopy",
                             json={"path": asset_rel, "actor": "AI"})
            wc_body = {}
            try:
                wc_body = wc.json()
            except Exception:
                pass
            copy_rel = str(wc_body.get("copy") or "")
            res.check(f"{tag}: fs/workcopy 创建副本(时间-作者-内容)",
                      wc.status_code == 200 and wc_body.get("ok") is True
                      and copy_rel.startswith("workcopy/") and "-AI-" in copy_rel,
                      f"status={wc.status_code} body={str(wc_body)[:120]}")
            if copy_rel:
                try:
                    res.check(f"{tag}: fs/workcopy 原文件不动且副本内容一致",
                              (ws / "资料" / "答辩.pptx").read_bytes() == asset_bytes
                              and (ws / copy_rel).read_bytes() == asset_bytes)
                except OSError as e:
                    res.check(f"{tag}: fs/workcopy 读取异常: {e}", False)
                we = client.post("/api/bridge/fs/write",
                                 json={"path": copy_rel, "content": "ai edited copy"})
                res.check(f"{tag}: 工作副本可写（豁免保护）", we.status_code == 200,
                          f"status={we.status_code}")
            wtrav = client.post("/api/bridge/fs/workcopy", json={"path": "../x.pptx"})
            # _resolve 层 403 / make_workcopy 层 400 均为正确拒绝
            res.check(f"{tag}: fs/workcopy 越界拒绝",
                      wtrav.status_code in (400, 403),
                      f"status={wtrav.status_code}")

        # S8.6 v8.32 Lite 后端用户资产保护 + 行尾保持（此前仅前端拦截/无行尾保持）
        if tag == "lite":
            wblk2 = client.post("/api/bridge/fs/write",
                                json={"path": "deck.pptx", "content": "junk"})
            res.check(f"{tag}: Lite 后端用户资产 fs/write 被拦(403)",
                      wblk2.status_code == 403, f"status={wblk2.status_code}")
            (ws_path / "deck2.pptx").write_bytes(b"PK\x03\x04 lite fake")
            ev3, st3 = sse_run(client, base, {"command": "copy deck2.pptx deck3.pptx"})
            res.check(f"{tag}: Lite 后端命令触碰用户资产被拦(403)",
                      st3 == 403, f"status={st3}")
            (ws_path / "crlf.txt").write_bytes(b"a\r\nb\r\n")
            wcrlf = client.post("/api/bridge/fs/write",
                                json={"path": "crlf.txt", "content": "x\ny"})
            crlf_ok = False
            if wcrlf.status_code == 200:
                try:
                    crlf_ok = (ws_path / "crlf.txt").read_bytes() == b"x\r\ny"
                except OSError:
                    crlf_ok = False
            res.check(f"{tag}: Lite fs/write 保持 CRLF 行尾", crlf_ok)

        # S9 run_command SSE 安全命令
        events, status = sse_run(client, base, {"command": "echo smoke_ok_marker_9001"})
        rc_val = None
        outs = []
        for name, data in events:
            if isinstance(data, dict):
                if "line" in data:
                    outs.append(str(data["line"]))
                if data.get("rc") is not None:
                    rc_val = data.get("rc")
                if name == "done":
                    rc_val = data.get("rc", rc_val)
        joined = "\n".join(outs)
        res.check(f"{tag}: run_command SSE 完成 rc=0", status == 200 and rc_val == 0,
                  f"status={status} events={len(events)} rc={rc_val}")
        res.check(f"{tag}: run_command 输出含标记", "smoke_ok_marker_9001" in joined,
                  f"outs={joined[:120]}")

        # S10 危险命令无确认 → 403（旧模式 rm -rf 兜底）
        ev, st = sse_run(client, base, {"command": "rm -rf 未确认目录"})
        res.check(f"{tag}: 危险命令未确认被拒", st in (403, 400), f"status={st}")

        # S11 危险命令 danger_ok:"false" 字符串 → 仍拒绝（严格布尔）
        ev, st = sse_run(client, base, {"command": "rm -rf 未确认目录", "danger_ok": "false"})
        res.check(f"{tag}: danger_ok='false' 严格拒绝", st in (403, 400), f"status={st}")

        # S12 v8.15 新正则精度：PowerShell 别名短参递归删除同样拦截；
        # 而 git clean -n（无 -f）不属危险、应放行执行（不出现 403）
        ev, st = sse_run(client, base, {"command": "ri 未确认目录 -Recurse"})
        res.check(f"{tag}: 新增别名递归删除拦截", st in (403, 400), f"status={st}")
        ev, st = sse_run(client, base, {"command": "git clean -n"})
        res.check(f"{tag}: git clean -n 不误拦", st == 200, f"status={st}")

        # S13 只读保护：读取 data/server_config.json 应拒绝
        rd = client.get("/api/bridge/fs/read", params={"path": "../data/server_config.json"})
        res.check(f"{tag}: 只读保护拒绝配置文件", rd.status_code in (403, 400),
                  f"status={rd.status_code}")


        # v8.18 终端环境池 + run_command 新参数（仅 webui 完整版；lite 刻意不实现）
        if expect_term:
            tc = client.post("/api/bridge/term/create", json={"name": "k210", "env_vars": {"BAUD": "115200"}})
            res.check("term_create 200", tc.status_code == 200, f"status={tc.status_code}")
            tenv = (tc.json() or {}).get("env", {})
            res.check("term_create 返回 id", bool(tenv.get("id")), f"env={tenv}")
            tid = tenv.get("id")
            tl = client.get("/api/bridge/term/list")
            res.check("term_list 含 1 个环境", (tl.json() or {}).get("envs") and len(tl.json()["envs"]) == 1)
            # run_command 带 env + update_interval + kill_after + 默认值 warning
            ev2, st2 = sse_run(client, base, {"command": "echo term_smoke_ok", "env": tid,
                                               "update_interval": 1, "kill_after": True})
            rc2 = None
            has_warn = False
            for _name, data in ev2:
                if isinstance(data, dict):
                    if data.get("warnings"):
                        has_warn = True
                    if data.get("rc") is not None:
                        rc2 = data.get("rc")
            res.check("run_command+env 完成 rc=0", st2 == 200 and rc2 == 0, f"rc={rc2}")
            res.check("run_command 默认值 warning", has_warn, f"events={[n for n,_ in ev2]}")
            # 删除环境
            td = client.post("/api/bridge/term/delete", json={"id": tid})
            res.check("term_delete 200", td.status_code == 200 and (td.json() or {}).get("ok") is True)
            tl2 = client.get("/api/bridge/term/list")
            res.check("term_list 删除后为空", len((tl2.json() or {}).get("envs", [])) == 0)
            # v8.21 WorkTree 端点（仅 webui；list 只读冒烟，switch/remove 走 danger_ok 拒绝路径）
            wl = client.get("/api/bridge/worktree/list")
            wl_data = {}
            try:
                wl_data = wl.json() or {}
            except Exception:
                pass
            res.check(f"{tag}: worktree/list 200", wl.status_code == 200 and "items" in wl_data,
                      f"status={wl.status_code}")
            res.check(f"{tag}: worktree/list 含 count",
                      "count" in wl_data and isinstance(wl_data.get("count"), int),
                      f"data={wl_data}")
            # switch 未带 danger_ok → 403
            ws = client.post("/api/bridge/worktree/switch", json={"name": "smoke_nonexist"})
            res.check(f"{tag}: worktree/switch 未确认被拒", ws.status_code in (403, 400, 404),
                      f"status={ws.status_code}")
            # remove 未带 danger_ok → 403
            wr = client.post("/api/bridge/worktree/remove", json={"name": "smoke_nonexist"})
            res.check(f"{tag}: worktree/remove 未确认被拒", wr.status_code in (403, 400, 404),
                      f"status={wr.status_code}")

            # v8.29 /fs/image raw 模式（仅 webui）：变更栏引用卡片 <img> 直链契约
            if tag == "webui":
                (ws_path / "smoke_px.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
                (ws_path / "smoke_px.gif").write_bytes(b"GIF89a" + b"0" * 24)
                (ws_path / "smoke_tx.txt").write_text("not-image", encoding="utf-8")
                r_png = client.get("/api/bridge/fs/image?path=smoke_px.png&raw=1")
                ct_png = (r_png.headers.get("content-type") or "")
                res.check(f"{tag}: fs/image raw png 回图片字节",
                          r_png.status_code == 200 and ct_png.startswith("image/png")
                          and r_png.content[:4] == b"\x89PNG",
                          f"status={r_png.status_code} ct={ct_png}")
                r_gif = client.get("/api/bridge/fs/image?path=smoke_px.gif&raw=1")
                res.check(f"{tag}: fs/image raw gif 扩展魔数",
                          r_gif.status_code == 200
                          and (r_gif.headers.get("content-type") or "").startswith("image/gif"),
                          f"status={r_gif.status_code}")
                r_txt = client.get("/api/bridge/fs/image?path=smoke_tx.txt&raw=1")
                res.check(f"{tag}: fs/image raw 非图片 415", r_txt.status_code == 415,
                          f"status={r_txt.status_code}")
                r_json = client.get("/api/bridge/fs/image?path=smoke_px.png")
                res.check(f"{tag}: fs/image JSON 契约不变(ui_review)",
                          r_json.status_code == 200 and bool((r_json.json() or {}).get("base64")),
                          f"status={r_json.status_code}")

                # v8.32 meta_bridge 死导入修复：模型注册表端点复活（此前恒 501）
                gm = client.get("/api/bridge/models")
                res.check(f"{tag}: /api/bridge/models 200（死导入修复）",
                          gm.status_code == 200 and isinstance((gm.json() or {}).get("models"), list),
                          f"status={gm.status_code} body={str(gm.text)[:100]}")
                pm = client.post("/api/bridge/models",
                                 json={"id": "smoke-model", "name": "Smoke", "kind": "chat"})
                res.check(f"{tag}: /api/bridge/models 新增 200",
                          pm.status_code == 200 and (pm.json() or {}).get("ok") is True,
                          f"status={pm.status_code} body={str(pm.text)[:100]}")
                # desktop.config 认 DEVERAI_DATA_DIR：注册表落隔离数据目录而非真实 pyqt/data
                res.check(f"{tag}: desktop.config 隔离（models.json 落 DEVERAI_DATA_DIR）",
                          (data_dir / "models.json").is_file(),
                          f"data_dir={data_dir}")
                pdm = client.delete("/api/bridge/models/smoke-model")
                res.check(f"{tag}: /api/bridge/models 清理 200",
                          pdm.status_code == 200, f"status={pdm.status_code}")

            # v8.22 外部 API 代理：SSRF 校验拒绝路径（不做真实外网请求）
            ep0 = client.post("/api/llm/ext_proxy", json={})
            res.check("ext_proxy 缺 base_url 拒绝", ep0.status_code == 400, f"status={ep0.status_code}")
            ep1 = client.post("/api/llm/ext_proxy",
                              json={"base_url": "http://192.168.1.1:9/v1", "path": "/x", "method": "GET"})
            res.check("ext_proxy 内网地址拒绝", ep1.status_code == 400, f"status={ep1.status_code}")
            ep2 = client.post("/api/llm/ext_proxy",
                              json={"base_url": "https://api.example.invalid", "path": "/x", "method": "TRACE"})
            res.check("ext_proxy 非法方法拒绝", ep2.status_code == 400, f"status={ep2.status_code}")
            ep3 = client.post("/api/llm/ext_proxy",
                              json={"base_url": "https://api.example.invalid", "path": "no-slash", "method": "GET"})
            res.check("ext_proxy path 非法拒绝", ep3.status_code == 400, f"status={ep3.status_code}")

            # v8.22 SMTP 状态端点（不依赖真实 SMTP 配置，只断言契约）
            sm = client.get("/api/security/smtp_status")
            sm_data = {}
            try:
                sm_data = sm.json() or {}
            except Exception:
                pass
            res.check("smtp_status 200 契约", sm.status_code == 200 and isinstance(sm_data.get("configured"), bool),
                      f"status={sm.status_code}")

            # v8.22 泄露检查：先写一个假密钥文件 → 扫描应发现；厂商心跳内网地址应标非法
            fake_key = "sk-smokefake" + "a1b2c3d4e5" * 6
            fk = client.post("/api/bridge/fs/write", json={"path": "leak_test.cfg", "content": "key = " + fake_key})
            res.check("泄露检查前置写入假密钥", fk.status_code == 200, f"status={fk.status_code}")
            ls = client.post("/api/security/leak_scan",
                             json={"vendors": [{"name": "t", "base_url": "http://192.168.1.1"}],
                                   "notify_email": ""})
            ls_data = {}
            try:
                ls_data = ls.json() or {}
            except Exception:
                pass
            findings = ls_data.get("findings") or []
            vendors = ls_data.get("vendors") or []
            res.check("leak_scan 200 契约", ls.status_code == 200 and "findings" in ls_data,
                      f"status={ls.status_code}")
            res.check("leak_scan 发现假密钥", any(f.get("file") == "leak_test.cfg" for f in findings),
                      f"findings={str(findings)[:160]}")
            res.check("leak_scan 命中不回显完整密钥",
                      all(fake_key not in str(f.get("preview", "")) for f in findings),
                      "preview 泄露了完整密钥")
            res.check("leak_scan 厂商内网地址标非法",
                      len(vendors) == 1 and "地址非法" in str(vendors[0].get("error", "")),
                      f"vendors={str(vendors)[:160]}")
            res.check("leak_scan SMTP 未配置不发信", ls_data.get("smtp_sent") is False)
            # 清理假密钥文件
            client.post("/api/bridge/fs/delete", json={"path": "leak_test.cfg"})

            # v8.23 浏览器 CDP 直控桥（安全路径断言——不真实启动浏览器）
            bc0 = client.post("/api/bridge/browser_ctl", json={"action": "bogus"})
            res.check("browser_ctl 未知操作 400", bc0.status_code == 400, f"status={bc0.status_code}")
            bc1 = client.post("/api/bridge/browser_ctl", json={"action": "navigate", "url": "http://example.com"})
            res.check("browser_ctl 未启动时 navigate 失败但 200",
                      bc1.status_code == 200 and (bc1.json() or {}).get("ok") is False,
                      f"status={bc1.status_code}")
            bc2 = client.post("/api/bridge/browser_ctl", json={"action": "type", "text": "hi"})
            res.check("browser_ctl 未启动时 type 失败但 200",
                      bc2.status_code == 200 and (bc2.json() or {}).get("ok") is False)
            bc3 = client.post("/api/bridge/browser_ctl", json={"action": "close"})
            res.check("browser_ctl close 未启动 200", bc3.status_code == 200)

            # v8.23 外部软件（exe）自动化桥（安全路径断言——不真实操作桌面）
            ex0 = client.post("/api/bridge/exe", json={"action": "bogus"})
            res.check("exe 未知操作 400", ex0.status_code == 400, f"status={ex0.status_code}")
            ex1 = client.post("/api/bridge/exe", json={"action": "list_windows"})
            ex1d = ex1.json() or {}
            res.check("exe list_windows 200 契约", ex1.status_code == 200 and ex1d.get("ok") is True
                      and isinstance(ex1d.get("windows"), list), f"status={ex1.status_code}")
            ex2 = client.post("/api/bridge/exe", json={"action": "launch", "path": "C:\\nonexistent\\no_such.exe"})
            res.check("exe launch 不存在路径失败", (ex2.json() or {}).get("ok") is False)
            ex3 = client.post("/api/bridge/exe", json={"action": "click", "x": 99999, "y": 0})
            res.check("exe click 越界拒绝", (ex3.json() or {}).get("ok") is False)
            ex4 = client.post("/api/bridge/exe", json={"action": "close", "pid": 4})
            res.check("exe close 白名单外 pid 拒绝",
                      (ex4.json() or {}).get("ok") is False and "拒绝" in str((ex4.json() or {}).get("output", "")))
            ex5 = client.post("/api/bridge/exe", json={"action": "screenshot", "path": "x.png",
                                                        "title": "无此窗口xyz_nonexistent"})
            res.check("exe screenshot 无窗口失败", (ex5.json() or {}).get("ok") is False)
            ex6 = client.post("/api/bridge/exe", json={"action": "journal", "tail": 5})
            res.check("exe journal 200 契约", ex6.status_code == 200 and (ex6.json() or {}).get("ok") is True)
            ex7 = client.post("/api/bridge/exe", json={"action": "type"})
            res.check("exe type 缺 text 400", ex7.status_code == 400)
        # v8.17 安全审计端点
        ra = client.get("/api/security/audit")
        audit_data = {}
        try:
            audit_data = ra.json()
        except Exception:
            pass
        res.check(f"{tag}: 审计日志读取 200", ra.status_code == 200 and "entries" in audit_data,
                  f"status={ra.status_code}")
        # 写入一条审计记录（通过注册触发），再读取应含该记录
        ra2 = client.get("/api/security/audit")
        entries2 = (ra2.json() or {}).get("entries", [])
        has_register = any(e.get("action") == "register" for e in entries2)
        res.check(f"{tag}: 审计日志含注册记录", has_register, f"entries={len(entries2)}")
        # 清空
        rc = client.post("/api/security/audit/clear")
        res.check(f"{tag}: 审计日志清空", rc.status_code == 200 and (rc.json() or {}).get("ok") is True)
        ra3 = client.get("/api/security/audit")
        res.check(f"{tag}: 清空后审计为空", len((ra3.json() or {}).get("entries", [])) == 0)

    finally:
        if client is not None:
            client.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:
                proc.kill()
        time.sleep(0.2)
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------- v8.24 sync_server 冒烟 -----------------------------

MOCK_LLM_REPLY = "MOCK_LLM_REPLY_OK_7f3d"
_MOCK_LLM_STATE = {"last_body": None, "hits": 0}


class _MockLLMHandler(BaseHTTPRequestHandler):
    """OpenAI 兼容 mock：POST */chat/completions 固定回复，并记录最近一次请求体。"""

    def log_message(self, *args):  # 静音
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        _MOCK_LLM_STATE["last_body"] = body
        _MOCK_LLM_STATE["hits"] += 1
        resp = json.dumps({"choices": [{"message": {"content": MOCK_LLM_REPLY}}]},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


def _desk_snapshot(payload: dict) -> str:
    """构造桌面无口令冷备格式：urlsafe b64(zlib(b"obfuscated\\x00"+JSON))。"""
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(b"obfuscated\x00" + raw, 9)).decode("ascii")


def _web_cold_snapshot(payload: dict) -> str:
    """构造网页无口令冷备格式：{"v":2,"kind":"web-cold","data":b64(JSON)}。"""
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return json.dumps({"v": 2, "kind": "web-cold",
                       "data": base64.b64encode(raw).decode("ascii")}, ensure_ascii=False)


def _parse_desk_snapshot(text: str):
    plain = zlib.decompress(base64.urlsafe_b64decode(text.strip().encode("ascii")))
    return json.loads(plain.split(b"\x00", 1)[1].decode("utf-8"))


def _wait_sync_ready(url: str, token: str, timeout_s: float = 25.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = httpx.get(url + "/drift/status", headers={"X-Sync-Token": token}, timeout=2.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def smoke_sync_server(res: _Res, port: int):
    tmp = Path(tempfile.mkdtemp(prefix="deverai_smoke_sync_"))
    data_dir = tmp / "sync_data"
    sync_ws = tmp / "drift_ws"
    token = "smoke_sync_" + uuid.uuid4().hex[:12]
    mock = ThreadingHTTPServer(("127.0.0.1", 0), _MockLLMHandler)
    mock_port = mock.server_address[1]
    mt = threading.Thread(target=mock.serve_forever, daemon=True)
    mt.start()
    proc = None
    try:
        env = os.environ.copy()
        env["SYNC_DATA_DIR"] = str(data_dir)
        env["SYNC_WS_DIR"] = str(sync_ws)
        env["SYNC_TOKEN"] = token
        env["DRIFT_API_BASE"] = f"http://127.0.0.1:{mock_port}/v1"
        env["DRIFT_API_KEY"] = "sk-mock-smoke-key"
        env["DRIFT_ALLOW_LOOPBACK"] = "1"
        proc = subprocess.Popen([PY, "sync_server.py", "--port", str(port)],
                               cwd=str(ROOT), env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        H = {"X-Sync-Token": token}
        if not res.check("sync: 服务就绪", _wait_sync_ready(base, token)):
            return

        # C1 令牌门：无令牌 401、错令牌 401
        r = httpx.get(base + "/drift/status", timeout=5)
        res.check("sync: 无令牌 401", r.status_code == 401, f"status={r.status_code}")
        r = httpx.get(base + "/drift/status", headers={"X-Sync-Token": "wrong"}, timeout=5)
        res.check("sync: 错令牌 401", r.status_code == 401, f"status={r.status_code}")

        # C2 未推冷备时 /chat → 400
        c = httpx.post(base + "/chat", json={"message": "hi"}, headers=H, timeout=10)
        res.check("sync: 无冷备 /chat 拒绝", c.status_code == 400, f"status={c.status_code}")

        # C3 /push 桌面双格式（data+cold 同为无口令桌面格式）
        payload = {"config": {"model": "smoke-model-a"},
                   "history": [{"role": "user", "content": "冒烟初始消息"}],
                   "ts": "2026-08-31 00:00:00"}
        snap_desk = _desk_snapshot(payload)
        r = httpx.post(base + "/push", json={"data": snap_desk, "cold": snap_desk},
                       headers=H, timeout=15)
        res.check("sync: /push 桌面格式 200", r.status_code == 200 and (r.json() or {}).get("ok") is True,
                  f"status={r.status_code}")

        # C4 /chat 续聊：回复为 mock 固定文案，且模型名取自快照 config
        c = httpx.post(base + "/chat", json={"message": "云端续聊消息"}, headers=H, timeout=30)
        cdata = {}
        try:
            cdata = c.json() or {}
        except Exception:
            pass
        res.check("sync: /chat 续聊 200", c.status_code == 200 and cdata.get("reply") == MOCK_LLM_REPLY,
                  f"status={c.status_code} body={str(cdata)[:120]}")
        last = _MOCK_LLM_STATE.get("last_body") or {}
        res.check("sync: LLM 收到快照内模型名", last.get("model") == "smoke-model-a",
                  f"model={last.get('model')}")
        msgs = last.get("messages") or []
        res.check("sync: LLM 收到云端续聊消息", any("云端续聊消息" in str(m.get("content", "")) for m in msgs))

        # C5 漂移计数 +1
        st = httpx.get(base + "/drift/status", headers=H, timeout=5).json()
        res.check("sync: 续聊后 unacked_count=1", st.get("unacked_count") == 1, f"st={st}")

        # C6 /pull 回传冷备（历史超集：含 2 条 from_server 云端消息）
        p = httpx.get(base + "/pull", headers=H, timeout=10).json()
        drift_cold = str(p.get("drift_cold") or "")
        res.check("sync: /pull 回传冷备", bool(drift_cold))
        try:
            cold_payload = _parse_desk_snapshot(drift_cold)
            from_srv = [m for m in cold_payload.get("history", []) if m.get("from_server")]
        except Exception:
            cold_payload, from_srv = {}, []
        res.check("sync: 冷备含 2 条云端消息", len(from_srv) == 2, f"from_server={len(from_srv)}")

        # C7 /chat/history 契约
        h = httpx.get(base + "/chat/history", headers=H, timeout=5).json()
        res.check("sync: /chat/history ok", h.get("ok") is True and
                  isinstance(h.get("history"), list) and len(h.get("history")) >= 3,
                  f"h={str(h)[:120]}")

        # C8 网页 web-cold 格式兼容：推 web-cold 冷备 → 续聊 → 原格式写回
        wc = _web_cold_snapshot({"config": {"model": "smoke-model-b"},
                                 "history": [{"role": "user", "content": "web cold"}]})
        httpx.post(base + "/push", json={"data": wc, "cold": wc}, headers=H, timeout=15)
        c2 = httpx.post(base + "/chat", json={"message": "web 续聊"}, headers=H, timeout=30)
        res.check("sync: web-cold 续聊 200", c2.status_code == 200 and
                  (c2.json() or {}).get("reply") == MOCK_LLM_REPLY, f"status={c2.status_code}")
        p2 = httpx.get(base + "/pull", headers=H, timeout=10).json()
        try:
            wc_back = json.loads(str(p2.get("drift_cold") or ""))
            wc_kind = wc_back.get("kind") if isinstance(wc_back, dict) else None
        except Exception:
            wc_kind = None
        res.check("sync: web-cold 原格式写回", wc_kind == "web-cold",
                  f"drift_cold={str(p2.get('drift_cold'))[:80]}")

        # C9 加密主快照永不降级：data=AES 信封形状 → 续聊后 data 原样
        enc = json.dumps({"salt": "AA", "iv": "BB", "data": "CC"})
        snap_desk2 = _desk_snapshot({"config": {"model": "m"}, "history": [{"role": "user", "content": "x"}]})
        httpx.post(base + "/push", json={"data": enc, "cold": snap_desk2}, headers=H, timeout=15)
        httpx.post(base + "/chat", json={"message": "加密主快照测试"}, headers=H, timeout=30)
        p3 = httpx.get(base + "/pull", headers=H, timeout=10).json()
        res.check("sync: 加密主快照原样保留", p3.get("data") == enc,
                  f"data={str(p3.get('data'))[:80]}")

        # C10 v8.27 真·算力漂移：工作区/data 子集/密钥信封素材化到 APPDATA 对应位
        def _mk_zip(files: dict) -> str:
            import io as _io
            import zipfile as _zipf
            buf = _io.BytesIO()
            with _zipf.ZipFile(buf, "w", _zipf.ZIP_DEFLATED) as zf:
                for name, content in files.items():
                    zf.writestr(name, content)
            return base64.standard_b64encode(buf.getvalue()).decode("ascii")

        ws_zip = _mk_zip({"main.py": "print('drift')", "docs/readme.md": "# ws"})
        data_zip = _mk_zip({"audit.jsonl": "{}\n", "checkpoints/task/x.bak": "bak"})
        keys_enc = json.dumps({"v": 1, "kind": "deverai-keys", "salt": "AA", "tok": "BB"})
        r4 = httpx.post(base + "/push", json={
            "data": snap_desk, "cold": snap_desk, "pid": "smoke_proj_1",
            "ws_zip": ws_zip, "data_zip": data_zip, "keys_enc": keys_enc,
            "upload_mode": "full"}, headers=H, timeout=20)
        mat = (r4.json() or {}).get("materialized") or {}
        ws_dir = sync_ws / "smoke_proj_1" / "workspace"
        res.check("sync: 工作区素材化到 APPDATA 位",
                  r4.status_code == 200 and (ws_dir / "main.py").is_file()
                  and (ws_dir / "docs" / "readme.md").is_file(),
                  f"mat={str(mat)[:140]}")
        res.check("sync: data 子集落位（WorkTree/审计不入工作区）",
                  (sync_ws / "smoke_proj_1" / "data" / "audit.jsonl").is_file()
                  and (sync_ws / "smoke_proj_1" / "data" / "checkpoints" / "task" / "x.bak").is_file())
        res.check("sync: 密钥信封仅密文落盘",
                  (sync_ws / "smoke_proj_1" / "keys.enc").is_file())
        st4 = httpx.get(base + "/drift/status", headers=H, timeout=5).json()
        dw = st4.get("drift_ws") or {}
        res.check("sync: /drift/status 返回素材化落位与续算指引",
                  dw.get("latest") == "smoke_proj_1" and bool(dw.get("resume_hint")),
                  f"dw={str(dw)[:140]}")

        # C11 v8.27 zip-slip 防护：../ 穿越成员不得逃出目标目录
        evil = _mk_zip({"../evil.txt": "x"})
        httpx.post(base + "/push", json={
            "data": snap_desk, "cold": snap_desk, "pid": "evil_proj", "ws_zip": evil},
            headers=H, timeout=20)
        res.check("sync: zip-slip 穿越拒绝",
                  not (sync_ws / "evil.txt").exists()
                  and not (sync_ws / "evil_proj" / "workspace" / "evil.txt").exists())

        # C10 /drift/finish 复位
        f = httpx.post(base + "/drift/finish", json={}, headers=H, timeout=5)
        st2 = httpx.get(base + "/drift/status", headers=H, timeout=5).json()
        res.check("sync: drift/finish 复位计数", f.status_code == 200 and st2.get("unacked_count") == 0,
                  f"st={st2}")

        # C11 /chat 空消息 → 400
        c3 = httpx.post(base + "/chat", json={"message": "  "}, headers=H, timeout=5)
        res.check("sync: 空消息 400", c3.status_code == 400, f"status={c3.status_code}")

        # C12 远程指挥全链路：register → nodes → dispatch(ping) → poll → result → results
        reg = httpx.post(base + "/cmd/register",
                         json={"name": "smoke节点", "workspace": "ws", "agent_mode": "builder"},
                         headers=H, timeout=5).json()
        node_id = str(reg.get("node_id") or "")
        res.check("sync: cmd/register 返回 node_id", bool(node_id), f"reg={reg}")
        nodes = httpx.get(base + "/cmd/nodes", headers=H, timeout=5).json()
        res.check("sync: cmd/nodes 含节点且存活", any(n.get("node_id") == node_id and n.get("alive")
                  for n in (nodes.get("nodes") or [])), f"nodes={str(nodes)[:120]}")
        dp = httpx.post(base + "/cmd/dispatch",
                        json={"node_id": node_id, "type": "ping", "payload": {}},
                        headers=H, timeout=5).json()
        cmd_id = str(dp.get("cmd_id") or "")
        res.check("sync: cmd/dispatch ping 下发", bool(cmd_id), f"dp={dp}")
        pl = httpx.get(base + f"/cmd/poll/{node_id}?wait=2", headers=H, timeout=10).json()
        cmds = pl.get("commands") or []
        res.check("sync: cmd/poll 取到命令", any(c.get("cmd_id") == cmd_id and c.get("type") == "ping"
                  for c in cmds), f"cmds={cmds}")
        hb = httpx.post(base + f"/cmd/heartbeat/{node_id}", json={"status": "alive"},
                        headers=H, timeout=5)
        res.check("sync: cmd/heartbeat 200", hb.status_code == 200)
        rs = httpx.post(base + f"/cmd/result/{node_id}/{cmd_id}",
                        json={"ok": True, "output": "pong"}, headers=H, timeout=5)
        res.check("sync: cmd/result 200", rs.status_code == 200, f"status={rs.status_code}")
        rr = httpx.get(base + f"/cmd/results/{node_id}", headers=H, timeout=5).json()
        res.check("sync: cmd/results 含回传结果", any(x.get("cmd_id") == cmd_id and x.get("ok") is True
                  for x in (rr.get("results") or [])), f"rr={str(rr)[:120]}")
        # dispatch 未知类型 → 400；未注册节点 → 404
        d0 = httpx.post(base + "/cmd/dispatch",
                       json={"node_id": node_id, "type": "bogus", "payload": {}}, headers=H, timeout=5)
        res.check("sync: dispatch 未知类型 400", d0.status_code == 400, f"status={d0.status_code}")
        d1 = httpx.post(base + "/cmd/dispatch",
                       json={"node_id": "nosuch", "type": "ping", "payload": {}}, headers=H, timeout=5)
        res.check("sync: dispatch 未注册节点 404", d1.status_code == 404, f"status={d1.status_code}")

        # C13 HTML 状态页（零 CDN，无 emoji）
        pg = httpx.get(base + "/", headers=H, timeout=5)
        res.check("sync: 首页 HTML 200", pg.status_code == 200 and "DeverAI" in pg.text)
        cp = httpx.get(base + "/chat/page", headers=H, timeout=5)
        res.check("sync: 续聊页 HTML 200", cp.status_code == 200 and "DeverAI" in cp.text)

    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:
                proc.kill()
        mock.shutdown()
        time.sleep(0.2)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    res = _Res()
    u = "smoke_" + uuid.uuid4().hex[:8]
    pw = "Smon!_" + uuid.uuid4().hex[:6]
    smoke_version(res, "lite", ROOT / "lite", 8791, u, pw, expect_bearer_token=False,
                  expect_term=False)
    smoke_version(res, "webui", ROOT / "webui", 8792, u, pw, expect_bearer_token=True,
                  expect_term=True)
    smoke_sync_server(res, 8793)

    print("-" * 46)
    if res.failures:
        print(f"[FAIL] 共 {len(res.failures)} 项失败:")
        for f in res.failures:
            print("  -", f)
        sys.exit(1)
    print("[OK] 服务冒烟全部通过")


if __name__ == "__main__":
    main()
