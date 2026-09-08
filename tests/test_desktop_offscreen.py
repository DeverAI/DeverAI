# -*- coding: utf-8 -*-
"""DeverAI 桌面版离屏回归：模块导入、主窗口实例化、v8.16 多会话标签全链路。

运行方式（仓库根目录）：
    python tests/test_desktop_offscreen.py

退出码纪律（FreqErr「测试绝不允许吞异常假绿」）：任何断言失败/异常 → 非零退出。
数据隔离（诚实边界，v8.16.1 复检修正）：会话索引/分会话历史/旧档路径全部重定向到临时目录；
同步外发（sync_q 推送）在窗口构造后立即停用——配置了 sync_server_url 的机器不会外发历史。
残余边界：desktop.config 在模块导入期可能于真实 data/ 首次生成默认 config.json（幂等、
仅含启动密钥与默认值）；除此之外测试不写任何键值到真实 data/。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pyqt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILED = []


def check(name, cond):
    status = "[OK]" if cond else "[X]"
    print(f"{status} {name}")
    if not cond:
        FAILED.append(name)


# ------------------------------------------------------------------
# 1) 会话存储单元检查（无 Qt）
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# 0) 终端环境池单元检查（无 Qt）
# ------------------------------------------------------------------
def test_term_envs():
    from desktop import terms as tm

    with tempfile.TemporaryDirectory() as td:
        tm.TERM_ENVS_PATH = Path(td) / "term_envs.json"
        check("term_envs init empty", tm.list_envs() == [])
        env = tm.create_env(name="k210", cwd_rel="firmware", env_vars={"BAUD": "115200"})
        check("create_env returns id", bool(env.get("id")))
        check("create_env name", env.get("name") == "k210")
        check("create_env cwd_rel", env.get("cwd_rel") == "firmware")
        check("create_env env_vars", env.get("env_vars") == {"BAUD": "115200"})
        lst = tm.list_envs()
        check("list_envs count 1", len(lst) == 1)
        check("get_env by id", tm.get_env(env["id"]) is not None)
        check("get_env by name", tm.get_env("k210") is not None)
        check("get_env missing", tm.get_env("nope") is None)
        try:
            tm.create_env(name="k210")
            check("create_env dup name should raise", False)
        except ValueError:
            check("create_env dup name rejected", True)
        try:
            tm.create_env(name="x", env_vars={"123BAD": "v"})
            check("create_env bad var should raise", False)
        except ValueError:
            check("create_env bad var rejected", True)
        check("delete_env hit", tm.delete_env(env["id"]) is True)
        check("delete_env miss", tm.delete_env(env["id"]) is False)
        check("list_envs count 0", len(tm.list_envs()) == 0)
        env2 = tm.create_env(name="persist")
        check("persistence readable", tm.get_env(env2["id"]) is not None)


# ------------------------------------------------------------------
# 1.5) v8.26 工作副本单元检查（无 Qt）：make_workcopy + 豁免 + 阻断文案
# ------------------------------------------------------------------
def test_file_protect_workcopy():
    from desktop import file_protect as fp

    with tempfile.TemporaryDirectory() as td:
        # 状态文件重定向到临时目录，不污染真实 data/file_protect.json
        fp.STATE_PATH = Path(td) / "fp_state.json"
        src = Path(td) / "报告.pptx"
        src.write_bytes(b"PK\x03\x04fake-pptx")
        ok, info = fp.make_workcopy(td, "报告.pptx", "AI")
        check("workcopy 创建成功", ok and isinstance(info, dict))
        if ok:
            copy_rel = str(info.get("rel") or "")
            check("workcopy 命名=时间-作者-内容",
                  copy_rel.startswith("workcopy/") and "-AI-" in copy_rel)
            try:
                check("workcopy 内容一致且原文件不动",
                      (Path(td) / copy_rel).read_bytes() == b"PK\x03\x04fake-pptx"
                      and src.read_bytes() == b"PK\x03\x04fake-pptx")
            except OSError as e:
                check(f"workcopy 内容读取异常: {e}", False)
        check("workcopy/ 豁免用户资产判定",
              not fp.is_user_asset("workcopy/20260905-AI-报告.pptx"))
        check("workcopy/.. 穿越不豁免（阶段3 修复）",
              fp.is_user_asset("workcopy/../报告.pptx"))
        check("workcopy/.. 穿越仍被阻断",
              bool(fp.check_ai_write_block(td, "workcopy/../报告.pptx")))
        check("原件仍判用户资产", fp.is_user_asset("报告.pptx"))
        reason = fp.check_ai_write_block(td, "报告.pptx")
        check("阻断文案引导 copy_user_asset",
              bool(reason) and "copy_user_asset" in reason)
        ok2, err2 = fp.make_workcopy(td, "不存在.pptx")
        check("不存在的文件拒绝", (not ok2) and isinstance(err2, str) and bool(err2))
        ok3, err3 = fp.make_workcopy(td, "../outside.pptx")
        check("越界路径拒绝", (not ok3) and isinstance(err3, str) and bool(err3))
        ok4, err4 = fp.make_workcopy(td, "workcopy/20260905-AI-报告.pptx")
        check("副本目录内不再拷贝", (not ok4) and isinstance(err4, str) and bool(err4))
        (Path(td) / "config.json").write_text("{}", encoding="utf-8")
        ok5, err5 = fp.make_workcopy(td, "config.json")
        check("敏感文件拒绝拷贝（阶段3）", (not ok5) and isinstance(err5, str) and bool(err5))
        (Path(td) / "data").mkdir()
        (Path(td) / "data" / "x.json").write_text("{}", encoding="utf-8")
        ok6, err6 = fp.make_workcopy(td, "data/x.json")
        check("敏感目录拒绝拷贝（阶段3）", (not ok6) and isinstance(err6, str) and bool(err6))


def test_session_store():
    from desktop import sessions as sm

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        sm.DATA_DIR = tmp
        sm.INDEX_PATH = tmp / "chat_sessions.json"
        sm.HISTORY_DIR = tmp / "chat_history"
        sm.LEGACY_HISTORY_PATH = tmp / "desktop_history.json"
        # 首启迁移：旧单会话文件存在 → 迁入 default
        sm.LEGACY_HISTORY_PATH.write_text(
            '[{"role": "user", "content": "旧会话消息"}]', encoding="utf-8")
        store = sm.SessionStore()
        store.ensure()
        check("迁移：默认会话存在", any(s["id"] == "default" for s in store.sessions()))
        hist = store.load_history("default")
        check("迁移：旧历史可读", len(hist) == 1 and hist[0]["content"] == "旧会话消息")

        # 新建/自动命名
        s2 = store.create("")
        check("新建：活跃切换", store.active_id == s2["id"])
        long_text = ("帮我修复登录页面的空指针异常，用户在未填写邮箱字段时点击提交会直接崩溃")
        check("自动命名：空白名命中", store.autotitle_if_blank(s2["id"], long_text))
        s2b = store.find(s2["id"])
        check("自动命名：标题取摘要", s2b["name"].startswith("帮我修复") and s2b["name"].endswith("…"))
        # 非空白名不再改名
        check("自动命名：已有名字不动", not store.autotitle_if_blank(s2["id"], "第二条"))

        # 重命名 / 逐个移除到底 / 最后一个保护（v8.16.1 复检修正原恒真断言）
        store.create("")                     # 第三个会话
        store.rename(s2["id"], "重构任务")
        check("重命名生效", store.find(s2["id"])["name"] == "重构任务")
        ids_all = [s["id"] for s in store.sessions()]
        for sid in ids_all[1:]:
            store.remove(sid)
            check("移除后活跃仍有效", store.find(store.active_id) is not None)
        check("逐个移除后仅剩一个", len(store.sessions()) == 1)
        last_id = store.sessions()[0]["id"]
        store.remove(last_id)
        check("最后一个不可移除", len(store.sessions()) == 1 and store.active_id == last_id)

        # 重启（重新 ensure）读回一致
        store2 = sm.SessionStore()
        store2.ensure()
        check("重启恢复索引", [x["id"] for x in store2.sessions()] == [x["id"] for x in store.sessions()])
        check("重启恢复活跃", store2.active_id == store.active_id)


# ------------------------------------------------------------------
# 2) Qt 离屏：导入全部模块 + 主窗口 + 多会话切换链路
# ------------------------------------------------------------------
def test_gui():
    os.environ.setdefault("DEVERAI_OFFSCREEN_TEST", "1")
    from PyQt6.QtWidgets import QApplication

    import importlib
    pkg = ROOT / "pyqt" / "desktop"
    mods = sorted(p.stem for p in pkg.glob("*.py")
                  if p.stem not in ("__init__",) and not p.name.startswith("_"))
    app = QApplication([])
    ok, bad = [], []
    for m in mods:
        try:
            importlib.import_module("desktop." + m)
            ok.append(m)
        except Exception as e:  # noqa: BLE001 —— 必须逐个可见，不允许整体吞掉
            bad.append((m, repr(e)))
    check(f"模块导入 {len(ok)}/{len(mods)}", not bad)
    for m, e in bad:
        print(f"    导入失败 {m}: {e}")

    # 数据目录隔离
    from desktop import sessions as sm
    from desktop import gui as gm
    td = tempfile.TemporaryDirectory()
    tmp = Path(td.name)
    sm.DATA_DIR = tmp
    sm.INDEX_PATH = tmp / "chat_sessions.json"
    sm.HISTORY_DIR = tmp / "chat_history"
    sm.LEGACY_HISTORY_PATH = tmp / "desktop_history.json"
    gm.HISTORY_PATH = tmp / "desktop_history.json"

    win = gm.DeverAIApp()
    # v8.16.1 复检：构造后立即停用同步外发（mark_dirty 只记账不外发，flush 可能联网推送）
    win.sync_q.mark_dirty = lambda *a, **k: None
    win.sync_q.flush = lambda *a, **k: None
    win.show()
    app.processEvents()
    check("主窗口构造", win is not None and win.chat is not None)
    check("多会话存储可用", win.sessions is not None)
    n0 = len(win.sessions.sessions())
    check("标签条与索引同步", win.chat.session_tabs.count() == n0)
    check("标签条可见", not win.chat.session_bar.isHidden())

    # 新建会话链路
    origin_active = win.sessions.active_id
    # 先在当前（初始）会话写两条消息并存盘——验证内容归属正确会话
    win.history.append({"role": "user", "content": "多会话冒烟内容甲"})
    win.history.append({"role": "assistant", "content": "收到，这是回复乙。"})
    win._save_history()

    win._new_session()
    app.processEvents()
    check("新建后数量+1", len(win.sessions.sessions()) == n0 + 1)
    check("新建后切为活跃", win.sessions.active_id != origin_active)
    check("新会话视图为空(欢迎区)", win.chat.hero.isVisible())

    # 切回初始会话：历史、聊天区、轨迹按会话还原
    check("切回原会话成功", win._switch_session(origin_active))
    app.processEvents()
    loaded = win.sessions.load_history(win.sessions.active_id)
    ok_pair = len(loaded) >= 2 and loaded[-2]["content"] == "多会话冒烟内容甲" \
        and loaded[-1]["content"] == "收到，这是回复乙。"
    check("历史写盘并按会话读取", ok_pair)
    html_txt = win.chat.view.toPlainText()
    check("聊天区回放包含双方消息", ("多会话冒烟内容甲" in html_txt)
          and ("收到，这是回复乙。" in html_txt))
    check("轨迹面板随切换重建", win.trace_panel is not None)

    # v8.29 变更栏：渲染 + 去重 + 点击引用打开（信号改接捕获，避免真实打开副作用）
    from PyQt6.QtCore import QUrl as _QUrl
    win.chat.render_change_log([{"path": "smoke_chg.txt", "action": "write_file"},
                                {"path": "smoke_chg.txt", "action": "edit_file"},
                                {"path": "smoke_chg.txt", "action": "write_file"}])
    app.processEvents()
    check("变更栏渲染且去重", "变更栏 · 2 个文件" in win.chat.view.toPlainText())
    check("变更栏引用锚存在", "act:openfile:smoke_chg.txt" in win.chat.view.toHtml())
    win.chat.open_file_requested.disconnect()
    _opened = []
    win.chat.open_file_requested.connect(lambda rel: _opened.append(rel))
    win.chat._on_anchor(_QUrl("act:openfile:smoke_chg.txt"))
    app.processEvents()
    check("变更栏点击引用发出打开信号", _opened == ["smoke_chg.txt"])
    win.chat.open_file_requested.disconnect()
    win.chat.open_file_requested.connect(win._open_file)

    # v8.30 方案1：文件入库（拖拽/粘贴共用 _ingest_paths）→ uploads/ + 引用条
    from desktop import uploads_ingest as _uing  # noqa: F401 确认可导入
    _wsdir = tmp / "ws30"
    _wsdir.mkdir(parents=True, exist_ok=True)
    _old_ws = win.cfg.workspace
    win.cfg.workspace = str(_wsdir)
    _drop = tmp / "drop_src.txt"
    _drop.write_text("dropped", encoding="utf-8")
    win.chat.take_quotes()
    win._ingest_paths([str(_drop)])
    app.processEvents()
    _q = win.chat._quotes
    check("入库：文件落 uploads/ 且引用条登记",
          bool(_q) and str(_q[0]["label"]).startswith("uploads/")
          and (_wsdir / _q[0]["label"]).is_file()
          and (_wsdir / _q[0]["label"]).read_text(encoding="utf-8") == "dropped")
    _sec = tmp / "config.json"
    _sec.write_text("{}", encoding="utf-8")
    win._ingest_paths([str(_sec)])
    app.processEvents()
    check("入库：敏感文件名拒绝（引用条不增加）", len(win.chat._quotes) == 1)
    win.chat.take_quotes()
    win.cfg.workspace = _old_ws

    # busy 守卫：运行中禁止切换
    win._busy = True
    other = next(s["id"] for s in win.sessions.sessions() if s["id"] != win.sessions.active_id)
    check("运行中切换被拒绝", win._switch_session(other) is False)
    win._busy = False

    # 开关即时生效链路（v8.16.1 修复锁定）：关→镜像旧档回落；开→恢复槽位与标签条
    win.cfg.ENABLE_MULTI_SESSION = False
    win._apply_multi_session_switch()
    app.processEvents()
    check("关闭开关：标签条隐藏", win.chat.session_bar.isHidden())
    check("关闭开关：存储已脱离", win.sessions is None)
    check("关闭开关：内容为镜像旧档", "收到，这是回复乙。" in win.chat.view.toPlainText())
    win.cfg.ENABLE_MULTI_SESSION = True
    win._apply_multi_session_switch()
    app.processEvents()
    check("重开开关：标签条恢复", win.sessions is not None and not win.chat.session_bar.isHidden())
    check("重开开关：回放活跃槽位", "收到，这是回复乙。" in win.chat.view.toPlainText())

    # busy 时开关切换被暂缓（保护流式视图不被 replay 冲掉）
    win._busy = True
    win.cfg.ENABLE_MULTI_SESSION = False
    win._apply_multi_session_switch()
    check("busy 中开关不动", win.sessions is not None)
    win._busy = False
    win.cfg.ENABLE_MULTI_SESSION = True

    # 关闭非活跃标签：数量减一、活跃不变
    n_before = len(win.sessions.sessions())
    win._close_session(other)
    app.processEvents()
    check("关闭非活跃标签数量-1", len(win.sessions.sessions()) == n_before - 1)
    check("关闭非活跃不影响活跃", win.sessions.active_id == origin_active)

    # v8.17 安全中心：验证 SettingsDialog Security Tab 可构造
    from desktop.settings_dialog import SettingsDialog
    dlg = SettingsDialog(win.cfg, win)
    check("安全中心 Tab 存在", dlg.tabs.count() >= 8)
    sec_idx = dlg.tabs.currentIndex()
    # 遍历 tab 寻找 Security
    for i in range(dlg.tabs.count()):
        if dlg.tabs.tabText(i) == "Security":
            sec_idx = i
            break
    dlg.tabs.setCurrentIndex(sec_idx)
    app.processEvents()
    check("Security Tab 切换成功", dlg.tabs.tabText(dlg.tabs.currentIndex()) == "Security")
    check("审计日志控件可加载", hasattr(dlg, 'audit_log'))
    dlg.close()

    # v8.17 audit 模块单元检查
    from desktop.audit import list_audits
    audits = list_audits(tail=5)
    check("audit.list_audits 返回列表", isinstance(audits, list))

    # v8.34（H10）：协调看板走真实 gui 入口——非模态可见 + 二次打开复用同一实例
    win._open_coordination_board()
    app.processEvents()
    cb = getattr(win, "_coord_board", None)
    check("协调看板H10：gui 入口打开且非模态可见",
          cb is not None and cb.isVisible() and not cb.isModal())
    win._open_coordination_board()
    check("协调看板H10：二次打开复用同一实例", getattr(win, "_coord_board", None) is cb)
    cb.close()
    app.processEvents()

    win.hide()
    # 收尾：停 AgentThread（后台 asyncio 常驻线程），失败也有界放弃
    try:
        win.agent_thread.stop()
        win.agent_thread.wait(2500)
    except Exception:
        pass
    td.cleanup()


# ------------------------------------------------------------------
# 1.6) v8.27 不看守模式 + 密钥信封 + 漂移上传打包（无 Qt）
# ------------------------------------------------------------------
def test_unattended_and_keys():
    import asyncio
    from desktop import sync as _sync
    from desktop.config import Config
    from desktop.tools import ToolContext, _request_approval

    with tempfile.TemporaryDirectory() as td:
        # drift_last_push 重定向：build/mark 走 sync.DATA_DIR，临时化防污染真实 data/
        real_data = _sync.DATA_DIR
        _sync.DATA_DIR = Path(td) / "data"
        try:
            cfg = Config()
            cfg.workspace = td
            cfg.ENABLE_UNATTENDED = True
            ctx = ToolContext(cfg=cfg, workspace=td)
            ok = asyncio.run(_request_approval(ctx, "run_command", {"command": "echo hi"}))
            check("不看守：非危险自动放行", ok is True)
            ok2 = asyncio.run(_request_approval(
                ctx, "run_command", {"command": "rm -rf x", "dangerous": True}))
            check("不看守：危险动作跳过", ok2 is False)
            check("不看守：保留进度台账落工作区",
                  (Path(td) / "unattended_progress.json").is_file())
            cfg.ENABLE_UNATTENDED = False
            ok3 = asyncio.run(_request_approval(
                ctx, "run_command", {"command": "rm -rf x", "dangerous": True}))
            check("关闭不看守：危险动作走原审批路径拒绝", ok3 is False)

            cfg2 = Config()
            cfg2.workspace = td
            cfg2.api_key = "sk-test-123"
            cfg2.api_base_url = "https://api.test/v1"
            cfg2.model = "test-model"
            # 密钥信封需要 cryptography（requirements.txt 已含）；缺失时 fail-closed 断言
            try:
                import cryptography  # noqa: F401
                has_crypto = True
            except ImportError:
                has_crypto = False
            if has_crypto:
                enc = _sync.export_keys_envelope(cfg2, "pw123")
                check("密钥信封非空且带类型标记", bool(enc) and '"deverai-keys"' in enc)
                keys = _sync.import_keys_envelope(enc, "pw123")
                check("密钥信封解密回环", keys.get("api_key") == "sk-test-123"
                      and keys.get("model") == "test-model")
                try:
                    _sync.import_keys_envelope(enc, "wrong-pw")
                    check("密钥信封错口令拒绝", False)
                except ValueError:
                    check("密钥信封错口令拒绝", True)
                check("无口令不上传密钥", _sync.export_keys_envelope(cfg2, "") == "")
            else:
                check("无 cryptography 时 fail-closed：密钥不上传（不降级混淆）",
                      _sync.export_keys_envelope(cfg2, "pw123") == "")

            (Path(td) / "a.txt").write_text("hello", encoding="utf-8")
            (Path(td) / "sub").mkdir()
            (Path(td) / "sub" / "b.py").write_text("x=1", encoding="utf-8")
            up_full = _sync.build_drift_upload(cfg2, "full")
            check("完整上传：工作区 zip 非空", bool(up_full.get("ws_b64")))
            up_min = _sync.build_drift_upload(cfg2, "minimal")
            check("最简上传：首次无基准=全量", bool(up_min.get("ws_b64")))
            _sync.mark_drift_pushed()
            up_min2 = _sync.build_drift_upload(cfg2, "minimal")
            check("最简上传：推送后无变更则增量为空", up_min2.get("ws_b64") == "")
            # 阶段3：敏感名不入包 + cutoff 基准 + manifest
            (Path(td) / "data").mkdir(exist_ok=True)
            (Path(td) / "data" / "config.json").write_text("{}", encoding="utf-8")
            up_s = _sync.build_drift_upload(cfg2, "full")
            import base64 as _b64
            import io as _io
            import zipfile as _zipf
            names = set()
            if up_s.get("ws_b64"):
                with _zipf.ZipFile(_io.BytesIO(
                        _b64.standard_b64decode(up_s["ws_b64"]))) as zf:
                    names = set(zf.namelist())
            check("敏感文件不入上传包（data/config.json）",
                  bool(names) and "data/config.json" not in names
                  and "config.json" not in names)
            check("上传含完整清单 manifest", bool(up_s.get("manifest"))
                  and "a.txt" in str(up_s.get("manifest")))
            import os as _os
            import time as _time
            _os.utime(Path(td) / "a.txt", (_time.time() - 3600,) * 2)
            cut = _time.time()
            _sync.mark_drift_pushed(cut)
            (Path(td) / "c.txt").write_text("new", encoding="utf-8")
            # v8.32：c.txt 的 mtime 显式锚定到 cut 之后——墙钟（time.time）与 NTFS
            # mtime 可能被 NTP 步进/时钟回拨反转，令本断言间歇性红（FreqErr #193
            # 同族：测试不对墙钟做时序假设）。产品侧 drift 增量跨机比较必须用墙钟，
            # 语义不动，只修测试的确定性。
            _os.utime(Path(td) / "c.txt", (_time.time() + 60,) * 2)
            up_inc = _sync.build_drift_upload(cfg2, "minimal")
            inc_names = set()
            if up_inc.get("ws_b64"):
                with _zipf.ZipFile(_io.BytesIO(
                        _b64.standard_b64decode(up_inc["ws_b64"]))) as zf:
                    inc_names = set(zf.namelist())
            check("增量基准：cutoff 前旧文件不再上传", "a.txt" not in inc_names)
            check("增量基准：cutoff 后新文件入包", "c.txt" in inc_names)
        finally:
            _sync.DATA_DIR = real_data


# ------------------------------------------------------------------
# 1.7) v8.28 checkpoint 版本保留可配（默认每文件上两版，C 盘友好）
# ------------------------------------------------------------------
def test_checkpoint_keep():
    from desktop import checkpoint as ck

    with tempfile.TemporaryDirectory() as td:
        old_dir, old_keep = ck.CHECKPOINT_DIR, ck._KEEP_OVERRIDE
        ck.CHECKPOINT_DIR = Path(td)
        ck._KEEP_OVERRIDE = 2
        try:
            for i in range(4):
                ck.save_checkpoint("a.txt", f"v{i}", task_id="t", source="ai")
            vers = ck.list_versions("a.txt")
            check("checkpoint 每文件保留 2 版（v8.28）", isinstance(vers, list) and len(vers) <= 2)
        finally:
            ck.CHECKPOINT_DIR, ck._KEEP_OVERRIDE = old_dir, old_keep


# ------------------------------------------------------------------
# 1.8) v8.29 变更栏：桌面 Agent 文件改动采集（无 Qt；渲染冒烟在 test_gui 内）
# ------------------------------------------------------------------
def test_changes_bar():
    import asyncio
    import json as _json
    from desktop.config import Config
    from desktop.agent import Agent

    with tempfile.TemporaryDirectory() as td:
        cfg = Config()
        cfg.workspace = td
        cfg.ENABLE_DIFF_PREVIEW = False   # 免 diff 审批阻塞
        cfg.ENABLE_CHECKPOINT = False     # 不写真实 data/checkpoints
        cfg.ENABLE_SESSION_SNAP = False
        cfg.ENABLE_UNATTENDED = True      # 审批门自动放行（非危险），测试不挂起
        events = []

        async def _cap(ev):
            events.append(ev)

        ag = Agent(cfg, emit=_cap)

        async def _go():
            await ag._execute_tools([{
                "id": "c1", "name": "write_file",
                "arguments": _json.dumps({"path": "a.txt", "content": "hi"}),
            }], [])
            await ag._execute_tools([{
                "id": "c2", "name": "edit_file",
                "arguments": _json.dumps({"path": "a.txt", "old_string": "hi",
                                          "new_string": "ho"}),
            }], [])

        asyncio.run(_go())
        check("变更采集：write/edit 依次入列",
              len(ag._changes) == 2
              and ag._changes[0] == {"path": "a.txt", "action": "write_file"}
              and ag._changes[1] == {"path": "a.txt", "action": "edit_file"})
        check("变更采集：工具结果事件正常发出",
              any(e.get("type") == "tool_result" and e.get("ok") for e in events))

        # v8.37：取消/异常轮的 file_changes 由 run wrapper 兜底透出（工具副作用已发生须可见）
        async def _fake_inner(*a, **k):
            ag._changes.append({"path": "cancel.txt", "action": "write_file"})
            raise asyncio.CancelledError()

        ag._run_inner = _fake_inner
        events.clear()
        try:
            asyncio.run(ag.run("触发取消"))
        except asyncio.CancelledError:
            pass
        check("取消轮：wrapper 兜底透出 file_changes",
              any(e.get("type") == "file_changes"
                  and any(c.get("path") == "cancel.txt" for c in (e.get("changes") or []))
                  for e in events))


# ------------------------------------------------------------------
# 1.9) v8.30 上传入库（方案1）：ingest 单元 + checkpoint 跳过 uploads/
# ------------------------------------------------------------------
def test_uploads_ingest():
    from desktop import uploads_ingest as ui
    from desktop import checkpoint as ck

    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "ws"
        ws.mkdir(parents=True)
        src = Path(td) / "报告.txt"
        src.write_text("内容甲", encoding="utf-8")
        ok, rel = ui.ingest_file(str(ws), str(src))
        check("入库：落 uploads/ 且内容一致",
              ok and rel.startswith("uploads/")
              and (ws / rel).is_file()
              and (ws / rel).read_text(encoding="utf-8") == "内容甲")
        ok2, rel2 = ui.ingest_file(str(ws), str(src))
        check("入库：同名自动加序号",
              ok2 and rel2 != rel and Path(rel2).name != Path(rel).name)
        sec = Path(td) / "config.json"
        sec.write_text("{}", encoding="utf-8")
        ok3, info3 = ui.ingest_file(str(ws), str(sec))
        check("入库：敏感文件名拒绝", not ok3 and "敏感" in info3
              and not list((ws / "uploads").rglob("config.json")))
        key = Path(td) / "server.pem"
        key.write_text("k", encoding="utf-8")
        ok4, info4 = ui.ingest_file(str(ws), str(key))
        check("入库：密钥后缀拒绝", not ok4 and "敏感" in info4)
        check("入库：源不存在拒绝", ui.ingest_file(str(ws), str(Path(td) / "nope.txt"))[0] is False)
        old_cap = ui.MAX_UPLOAD_BYTES
        ui.MAX_UPLOAD_BYTES = 4
        try:
            ok5, info5 = ui.ingest_file(str(ws), str(src))
            check("入库：超上限拒绝", not ok5 and "上限" in info5)
        finally:
            ui.MAX_UPLOAD_BYTES = old_cap
        # checkpoint 跳过 uploads/（C 盘约束），其余路径照常
        old_dir = ck.CHECKPOINT_DIR
        ck.CHECKPOINT_DIR = Path(td) / "ck"
        try:
            r1 = ck.save_checkpoint("uploads/a.txt", "x", task_id="t", source="ai")
            r2 = ck.save_checkpoint("other/a.txt", "x", task_id="t", source="ai")
            check("checkpoint：uploads/ 前缀跳过", r1 is None and r2 is not None)
            # v8.31 审计：Windows 大小写不敏感，Uploads/ 变体同样跳过
            r3 = ck.save_checkpoint("Uploads/a.txt", "x", task_id="t", source="ai")
            check("checkpoint：Uploads/ 大小写变体同样跳过", r3 is None)
            # v8.31 审计：入库文件必须拿到新 mtime（drift minimal 按 mtime 筛增量），
            # copy2 保留源 mtime 会让老文件永远进不了增量包
            old_src = Path(td) / "old.bin"
            old_src.write_bytes(b"old")
            os.utime(old_src, (1577836800, 1577836800))  # 2020-01-01
            ok6, rel6 = ui.ingest_file(str(ws), str(old_src))
            check("入库：mtime 刷新（drift 增量可见）",
                  ok6 and (ws / rel6).stat().st_mtime > 1700000000)
        finally:
            ck.CHECKPOINT_DIR = old_dir


# ------------------------------------------------------------------
# 1.10) v8.32 修复回归：note_ai_write 指纹登记闭环（此前全仓库零调用）
# ------------------------------------------------------------------
def test_note_ai_write_flow():
    from desktop import file_protect as fpm

    with tempfile.TemporaryDirectory() as td:
        old_state = fpm.STATE_PATH
        fpm.STATE_PATH = Path(td) / "file_protect.json"
        try:
            ws = Path(td) / "ws"
            ws.mkdir()
            (ws / "report.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            r1 = fpm.check_ai_write_block(str(ws), "report.csv")
            check("指纹：无记录的用户资产默认拦截", bool(r1))
            # AI 写入登记后放行（actor=ai，用户没碰过）——修复前此路径恒被锁死
            fpm.note_ai_write(str(ws), "report.csv")
            r2 = fpm.check_ai_write_block(str(ws), "report.csv")
            check("指纹：AI 写入登记后放行（actor=ai）", r2 is None)
            # 用户外部修改（size 变）→ 重新识别为用户修改并锁死
            (ws / "report.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
            changed = fpm.sync_user_modified(str(ws))
            check("指纹：用户外部修改被识别", "report.csv" in changed)
            r3 = fpm.check_ai_write_block(str(ws), "report.csv")
            check("指纹：用户改过后重新锁死", bool(r3))
            # 非资产后缀不受影响
            (ws / "main.py").write_text("print(1)\n", encoding="utf-8")
            r4 = fpm.check_ai_write_block(str(ws), "main.py")
            check("指纹：普通文本文件不受资产保护影响", r4 is None)
        finally:
            fpm.STATE_PATH = old_state


# ------------------------------------------------------------------
# 1.11) v8.32 修复回归：drift 上传补密钥后缀排除（.pem/.key/.pfx/.p12）
# ------------------------------------------------------------------
def test_sync_excl_key_suffix():
    from desktop import sync as _syncm
    from desktop.config import Config

    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "ok.txt").write_text("fine", encoding="utf-8")
        (Path(td) / "server.pem").write_text("-----BEGIN", encoding="utf-8")
        sub = Path(td) / "sub"
        sub.mkdir()
        (sub / "id_rsa.key").write_text("k", encoding="utf-8")
        cfg2 = Config()
        cfg2.workspace = td
        real_data = _syncm.DATA_DIR
        _syncm.DATA_DIR = Path(td) / "data"
        try:
            up = _syncm.build_drift_upload(cfg2, "full")
        finally:
            _syncm.DATA_DIR = real_data
        manifest = str(up.get("manifest") or "")
        check("drift 排除：.pem 不入清单", "server.pem" not in manifest)
        check("drift 排除：子目录 .key 不入清单", "id_rsa.key" not in manifest)
        check("drift 排除：普通文件照常入清单", "ok.txt" in manifest)
        import base64 as _b64
        import io as _io
        import zipfile as _zipf
        names = set()
        if up.get("ws_b64"):
            with _zipf.ZipFile(_io.BytesIO(
                    _b64.standard_b64decode(up["ws_b64"]))) as zf:
                names = set(zf.namelist())
        check("drift 排除：zip 内无密钥文件",
              bool(names) and all(not n.endswith((".pem", ".key")) for n in names))


# ------------------------------------------------------------------
# 1.12) v8.32：四端危险正则静态一致性锁（FreqErr「危险正则三端手工复制漂移」）
# 从四份源码抽取清单做集合比对——任何一端单独加/改正则，测试立即红。
# ------------------------------------------------------------------
def _norm_js_pattern(p: str) -> str:
    # JS 源码与 Python pattern 的等价写法归一：\/ → /，[\s\S] → .（配 DOTALL）
    return p.replace("\\/", "/").replace("[\\s\\S]", ".")


def _extract_py_patterns(path: Path) -> list:
    import ast as _ast
    tree = _ast.parse(path.read_text(encoding="utf-8"))
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Assign):
            for t in node.targets:
                if getattr(t, "id", "") == "DANGEROUS_PATTERNS":
                    return [n.value for n in node.value.elts]
    return []


def _extract_js_patterns(path: Path) -> list:
    import re as _re
    src = path.read_text(encoding="utf-8")
    m = _re.search(r"const DANGEROUS_PATTERNS = \[(.*?)\n\];", src, _re.S)
    body = "\n".join(l for l in m.group(1).split("\n")
                      if not l.strip().startswith("//"))
    out, i = [], 0
    while i < len(body):
        if body[i] == "/":
            j = i + 1
            while j < len(body):
                if body[j] == "\\":
                    j += 2
                    continue
                if body[j] == "/":
                    break
                j += 1
            k = j + 1
            while k < len(body) and body[k].isalpha():
                k += 1
            out.append(body[i + 1:j])
            i = k
        else:
            i += 1
    return out


def _extract_lite_danger(path: Path) -> list:
    import re as _re
    src = path.read_text(encoding="utf-8")
    m = _re.search(r"const DANGER_RE = /(.*?)/i;", src, _re.S)
    body = m.group(1)
    alts, buf, in_cls, depth, i = [], "", False, 0, 0
    while i < len(body):
        c = body[i]
        if c == "\\":
            buf += body[i:i + 2]
            i += 2
            continue
        if in_cls:
            if c == "]":
                in_cls = False
            buf += c
            i += 1
            continue
        if c == "[":
            in_cls = True
            buf += c
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "|" and depth == 0:
            alts.append(buf)
            buf = ""
            i += 1
            continue
        buf += c
        i += 1
    alts.append(buf)
    return alts


def test_dangerous_patterns_four_end_sync():
    repo = Path(__file__).resolve().parent.parent
    py_desktop = _extract_py_patterns(repo / "pyqt" / "desktop" / "tools.py")
    py_webui = _extract_py_patterns(repo / "webui" / "app" / "security.py")
    py_lite = _extract_py_patterns(repo / "lite" / "app" / "security.py")
    js_webui = _extract_js_patterns(repo / "webui" / "static" / "js" / "tools.js")
    lite_html = _extract_lite_danger(repo / "lite" / "static" / "lite.html")
    check("四端正则：desktop 清单抽取非空（当前 28 条）", len(py_desktop) >= 20)
    base = set(py_desktop)
    check("四端正则：webui security 一致", set(py_webui) == base)
    check("四端正则：lite security 一致", set(py_lite) == base)
    check("四端正则：js tools.js 一致（等价写法归一后）",
          set(map(_norm_js_pattern, js_webui)) == base)
    check("四端正则：lite.html DANGER_RE 一致（顶层交替切分后）",
          set(map(_norm_js_pattern, lite_html)) == base)
    # 孪生副本第二份 lite.html 同步核对（webui/static/lite.html）
    lite_html2 = _extract_lite_danger(repo / "webui" / "static" / "lite.html")
    check("四端正则：webui 孪生 lite.html 一致",
          set(map(_norm_js_pattern, lite_html2)) == base)


# ------------------------------------------------------------------
# 1.8) v8.33 全局协调协议：注册/冲突/收件箱/TTL 回收/命令嗅探（无 Qt）
# ------------------------------------------------------------------
def test_coordination():
    from desktop import coordination as coord

    with tempfile.TemporaryDirectory() as td:
        old_path = coord.STATE_PATH
        coord.STATE_PATH = Path(td) / "coordination.json"
        try:
            a = coord.self_agent_id(td)
            coord.touch(a, td, status="running", task="部署到 mindog",
                        resources=["ssh:mindog"])
            b = "other@999"
            v2 = coord.touch(b, td + "/other", status="running", task="上传日志",
                             resources=["ssh://mindog"], next_action="重启 nginx")
            check("协调：跨工作区同资源识别冲突（ssh: 前缀归一）",
                  any(c.get("key") == "mindog" for c in v2.get("conflicts") or []))
            snap = coord.snapshot(a)
            check("协调：看板含两个存活 Agent", len(snap.get("agents") or []) == 2)
            check("协调：冲突对列出双方",
                  any(set(c.get("agents") or []) == {a, b}
                      for c in snap.get("conflicts") or []))
            inbox = coord.pop_inbox(a)
            check("协调：收件箱含协议消息", len(inbox) >= 1
                  and "上传日志" in str((inbox[0] or {}).get("text")))
            check("协调：收件箱读后清空", coord.pop_inbox(a) == [])
            check("协调：命令嗅探 ssh 主机（含 sshpass 混杂命令）",
                  "ssh:1.2.3.4" in coord.resources_from_command(
                      "sshpass -p x ssh root@1.2.3.4 'ls'"))
            check("协调：嗅探忽略本机回环",
                  coord.resources_from_command("ssh localhost echo hi") == [])
            data = coord._load()
            for e in (data.get("agents") or {}).values():
                e["last_seen"] = (e.get("last_seen") or 0) - 99999
            coord._save(data)
            snap2 = coord.snapshot(a)
            check("协调：TTL 过期回收离线 Agent", not (snap2.get("agents") or []))
        finally:
            coord.STATE_PATH = old_path


# ------------------------------------------------------------------
# 1.13) v8.34 修复回归：协调注册表落盘语义（纯心跳/收件箱回收/idle）
# ------------------------------------------------------------------
def test_coordination_registry():
    from desktop import coordination as coord

    with tempfile.TemporaryDirectory() as td:
        old_path, old_persist = coord.STATE_PATH, dict(coord._LAST_PERSIST)
        coord.STATE_PATH = Path(td) / "coordination.json"
        coord._LAST_PERSIST.clear()
        try:
            aid = "plain@1"
            # H2：轮开始的纯心跳（只带 status/model）——v8.33 此前完全不落盘
            coord.touch(aid, td, status="running", model="gpt-x")
            snap = coord.snapshot(aid)
            ent = [a for a in (snap.get("agents") or []) if a.get("agent_id") == aid]
            check("协调H2：纯心跳 Agent 也进注册表（看板可见）", bool(ent))
            check("协调H2：status/model 落盘",
                  bool(ent) and ent[0].get("status") == "running" and ent[0].get("model") == "gpt-x")
            # H2b：带 task 的心跳把"在干什么"写进看板
            coord.touch(aid, td, status="running", task="部署到 mindog")
            tasks = [a.get("task") for a in (coord.snapshot(aid).get("agents") or [])
                     if a.get("agent_id") == aid]
            check("协调H2b：task 落盘（看板不再恒为未声明）", tasks == ["部署到 mindog"])
            # H4：死 Agent 的收件箱随 TTL 一并回收（此前 inbox 键永久残留）
            coord.send(aid, "other@2", "协议消息")
            data = coord._load()
            check("协调H4 前置：收件箱有消息", bool((data.get("inbox") or {}).get(aid)))
            for e in (data.get("agents") or {}).values():
                e["last_seen"] = (e.get("last_seen") or 0) - 99999
            coord._save(data)
            coord.snapshot(aid)   # 触发 _purge
            data2 = coord._load()
            check("协调H4：TTL 回收时死 Agent 收件箱一并清理",
                  aid not in (data2.get("inbox") or {}) and not (data2.get("agents") or {}))
            # H11：轮结束置 idle
            coord.touch(aid, td, status="idle", throttle=False)
            st = [a.get("status") for a in (coord.snapshot(aid).get("agents") or [])
                  if a.get("agent_id") == aid]
            check("协调H11：轮结束 idle 状态生效", st == ["idle"])
        finally:
            coord.STATE_PATH = old_path
            coord._LAST_PERSIST.clear()
            coord._LAST_PERSIST.update(old_persist)


# ------------------------------------------------------------------
# 1.14) v8.34 修复回归：协调看板对话框真的能打开（v8.33 因 QTableWidgetItem
#       未导入，一点开就 NameError；py_compile 与 coordination 单元测试都抓不到）
#       必须排在 test_gui 之后：复用其 QApplication，避免二次构造单例崩溃。
# ------------------------------------------------------------------
def test_coordination_board_dialog():
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    from desktop import coordination as coord
    from desktop.ide_extras import CoordinationBoardDialog

    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as td:
        old_path = coord.STATE_PATH
        coord.STATE_PATH = Path(td) / "coordination.json"
        try:
            coord.touch("ws@1", td, status="running", task="看板冒烟")
            dlg = CoordinationBoardDialog(None, td)
            dlg.show()          # 必须先 show：隐藏态 close() 不触发 Close 事件/销毁
            app.processEvents()
            check("看板H1：对话框可构造并刷出数据行", dlg.table.rowCount() >= 1)
            first = dlg.table.item(0, 0)
            check("看板H1：Agent 列有内容", first is not None and bool(first.text()))
            check("看板H7：Agent 列 tooltip 带完整工作区路径",
                  first is not None and td in (first.toolTip() or ""))
            check("看板H3：关闭即销毁属性已设",
                  bool(dlg.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)))
            timer = dlg._timer
            check("看板H3：自动刷新定时器在跑", timer.isActive())
            dlg.close()
            app.processEvents()
            # 关闭后 C++ 对象（含子定时器）应已销毁；访问已删包装器会抛 RuntimeError，
            # 因此按 sip.isdeleted 判定，取不到 sip 时退回"定时器已停/已删"两条弱证据。
            gone = False
            try:
                from PyQt6 import sip
                gone = bool(sip.isdeleted(dlg))
            except Exception:
                gone = False
            if not gone:
                try:
                    gone = not timer.isActive()
                except RuntimeError:
                    gone = True
            check("看板H3：关闭后对话框销毁（定时器随之消失）", gone)
        finally:
            coord.STATE_PATH = old_path


# ------------------------------------------------------------------
# 1.15) v8.35：专家逐个上看板 + 冲突给人类发消息 + 嗅探不污染意图字段
# ------------------------------------------------------------------
def test_coordination_experts_and_notify():
    import asyncio
    from desktop import coordination as coord
    from desktop.config import Config
    from desktop.agent import Agent

    with tempfile.TemporaryDirectory() as td:
        old_path, old_persist = coord.STATE_PATH, dict(coord._LAST_PERSIST)
        coord.STATE_PATH = Path(td) / "coordination.json"
        coord._LAST_PERSIST.clear()
        try:
            # A：uid 语义
            check("协调A：无后缀 uid = 基 id", coord.agent_uid(td) == coord.self_agent_id(td))
            check("协调A：身份后缀拼接",
                  coord.agent_uid(td, "专家·部署").endswith("/专家·部署"))
            base = coord.self_agent_id(td)
            check("协调A：后缀内斜杠消毒（不产生层级）",
                  coord.agent_uid(td, "a/b") == base + "/a_b")
            # 对端（另一工作区）占用 ssh:mindog
            other = coord.agent_uid(td + "_other", None)
            coord.touch(other, td + "_other", status="running", task="上传日志",
                        resources=["ssh:mindog"])
            cfg = Config()
            cfg.workspace = td
            cfg.ENABLE_DIFF_PREVIEW = False
            cfg.ENABLE_CHECKPOINT = False
            cfg.ENABLE_SESSION_SNAP = False
            cfg.ENABLE_UNATTENDED = True
            events = []

            async def _cap(ev):
                events.append(ev)

            ag = Agent(cfg, emit=_cap)
            # 主 Agent 上一轮嗅探过 ssh:mindog（注册表已有资源键）
            coord.touch(coord.self_agent_id(td), td, resources=["ssh:mindog"])
            ag._todo_text = "重启 mindog 上的服务"
            ag._build_system_prompt()   # 轮开始：touch + 冲突挂实例
            check("协调B：轮开始检测到冲突并挂实例",
                  bool(getattr(ag, "_coord_conflicts", None)))
            asyncio.run(ag._coord_notify())
            ev1 = [e for e in events if e.get("type") == "coordination"]
            check("协调B：冲突转成人类可见事件（含对方准备做什么）",
                  bool(ev1) and "mindog" in str(ev1[0].get("note"))
                  and "上传日志" in str(ev1[0].get("note")))
            asyncio.run(ag._coord_notify())
            check("协调B：同一组冲突不重复发",
                  len([e for e in events if e.get("type") == "coordination"]) == 1)
            # A：专家 Agent 单独成行，task=其准备做的事
            ag_sub = Agent(cfg, emit=_cap, is_sub=True, sub_label="专家·部署", expert_id="Deployer")
            ag_sub._todo_text = "部署新版"
            ag_sub._build_system_prompt()
            ids = [a.get("agent_id") for a in (coord.snapshot(ag._coord_uid()).get("agents") or [])]
            check("协调A：顶层与专家各自成行",
                  ag._coord_uid() in ids and ag_sub._coord_uid() in ids)
            rows = [a for a in (coord.snapshot(ag_sub._coord_uid()).get("agents") or [])
                    if a.get("agent_id") == ag_sub._coord_uid()]
            check("协调A：专家行 task=其准备做的事", bool(rows) and rows[0].get("task") == "部署新版")
            # 资源登记不污染 task（意图字段留给 declare/轮开始）
            coord.touch(ag_sub._coord_uid(), td, resources=["ssh:mindog"])
            rows2 = [a for a in (coord.snapshot(ag_sub._coord_uid()).get("agents") or [])
                     if a.get("agent_id") == ag_sub._coord_uid()]
            check("协调A：资源登记不污染 task", bool(rows2) and rows2[0].get("task") == "部署新版")
            # 完成态：子=done / 顶层=idle
            ag_sub._coord_idle()
            st = [a.get("status") for a in (coord.snapshot(ag_sub._coord_uid()).get("agents") or [])
                  if a.get("agent_id") == ag_sub._coord_uid()]
            check("协调A：子 Agent 完成置 done", st == ["done"])
            ag._coord_idle()
            st2 = [a.get("status") for a in (coord.snapshot(ag._coord_uid()).get("agents") or [])
                   if a.get("agent_id") == ag._coord_uid()]
            check("协调A：顶层完成置 idle", st2 == ["idle"])
        finally:
            coord.STATE_PATH = old_path
            coord._LAST_PERSIST.clear()
            coord._LAST_PERSIST.update(old_persist)


def main():
    failures = 0
    try:
        test_term_envs()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"term_envs error: {e}")
    try:
        test_session_store()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"session_store 异常: {e}")
    try:
        test_file_protect_workcopy()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"file_protect_workcopy 异常: {e}")
    try:
        test_unattended_and_keys()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"unattended_and_keys 异常: {e}")
    try:
        test_checkpoint_keep()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"checkpoint_keep 异常: {e}")
    try:
        test_coordination()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"coordination 异常: {e}")
    try:
        test_changes_bar()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"changes_bar 异常: {e}")
    try:
        test_uploads_ingest()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"uploads_ingest 异常: {e}")
    try:
        test_note_ai_write_flow()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"note_ai_write_flow 异常: {e}")
    try:
        test_sync_excl_key_suffix()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"sync_excl_key_suffix 异常: {e}")
    try:
        test_dangerous_patterns_four_end_sync()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"dangerous_patterns_four_end_sync 异常: {e}")
    try:
        test_gui()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"gui 异常: {e}")
    try:
        test_coordination_registry()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"coordination_registry 异常: {e}")
    try:
        test_coordination_experts_and_notify()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"coordination_experts_and_notify 异常: {e}")
    try:
        # 必须排在 test_gui 之后（复用其 QApplication 单例）
        test_coordination_board_dialog()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"coordination_board_dialog 异常: {e}")

    print("-" * 46)
    if FAILED or failures:
        print(f"[FAIL] 共 {len(FAILED)} 项失败/异常")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    print("[OK] 全部通过")
    sys.stdout.flush()
    # FreqErr「Qt 析构挂起」：AgentThread/QApplication 存活时解释器退出会无限等待，
    # 此处仅在"全部通过"后用 os._exit 直接收口（绝不吞掉失败的假绿路径）。
    os._exit(0)


if __name__ == "__main__":
    main()
