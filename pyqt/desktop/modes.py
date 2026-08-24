"""三大环境硬开关（第四章 4.3）：流量模式 / 肝完睡觉模式 / Token 计费模式。

- 流量模式：仅压缩传输内容（输出精简、停同步），绝不切换本地模型。
- 肝完睡觉：目标完成 → 全屏倒计时 5 分钟 → 每分钟强提醒 → 归零且无交互 → 关机/休眠（需授权）。
- Token 计费：AOE 规划器取最小调用次数路径 + 激进压缩（见 planner/context 对应分支）。
"""
import subprocess
import sys


def is_mode_active(cfg) -> list:
    """返回当前生效的模式名列表（供 UI/状态栏展示）。"""
    modes = []
    if cfg.traffic_mode:
        modes.append("traffic")
    if cfg.token_mode:
        modes.append("token")
    if cfg.sleep_enabled:
        modes.append("sleep")
    return modes


def goal_reached(goal: str, final_text: str) -> bool:
    """判断肝完睡觉模式的目标是否达成。

    v8.14：移除「已完成/done/完成」等子串兜底判定——「任务完成了一半」
    「第一步已完成」之类表述此前会误触发关机倒计时。现在唯一达成判据是
    配置的目标原文出现在最终回复中（AI 明确围绕该目标宣布结果）。
    倒计时本身仍有 5 分钟可取消窗口兜底。
    """
    if not goal or not final_text:
        return False
    # M-1: 排除否定表达，防止"未完成/无法完成"被误判为完成
    negative = ["未完成", "还没完成", "尚未完成", "没有完成", "无法完成", "不能完成",
                "未全部完成", "尚未全部完成", "没有全部", "未能完成", "未达成"]
    if any(m in final_text for m in negative):
        return False
    return goal in final_text


def do_power_action(action: str) -> dict:
    """执行关机/休眠（仅当用户显式授权后调用）。返回结果描述。"""
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        if action == "hibernate":
            subprocess.Popen(["shutdown", "/h"], creationflags=creationflags)
            return {"ok": True, "message": "已执行休眠"}
        if action == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", "15"], creationflags=creationflags)
            return {"ok": True, "message": "已执行关机（15 秒后）"}
        return {"ok": False, "message": f"不支持的电源操作: {action}"}
    except Exception as e:
        return {"ok": False, "message": f"执行失败: {e}"}
