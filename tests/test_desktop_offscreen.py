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

    win.hide()
    # 收尾：停 AgentThread（后台 asyncio 常驻线程），失败也有界放弃
    try:
        win.agent_thread.stop()
        win.agent_thread.wait(2500)
    except Exception:
        pass
    td.cleanup()


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
        test_gui()
    except Exception as e:  # noqa: BLE001
        failures += 1
        import traceback
        traceback.print_exc()
        FAILED.append(f"gui 异常: {e}")

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
