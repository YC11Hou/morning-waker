"""wakeupcall 命令行：on / off / status / checkin / drill / test-msg / log / stats / run"""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import journal
from .config import CONFIG_PATH, ROOT, STATE_DIR, load
from .daemon import CHECKIN_FILE, DAY_FILE, DRILL_FILE

LABEL = f"com.{getpass.getuser()}.wakeupcall"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
PY = sys.executable

USAGE = """用法: wakeupcall <命令>
  on          安装并启动常驻守护进程（launchd，开机自启、崩了自动拉起）
  off         停止并卸载
  status      守护进程状态 + 今天的进度
  checkin     本机打卡（等价于手机回「起」）
  drill [倍率] 立刻演习一遍完整流程；倍率把升级分钟数缩放，默认 0.1（15 分钟→90 秒）。
              演习里「告状」只发给你自己，不发朋友。
  test-msg    给自己发一条测试 iMessage，验证通道
  log         跟踪守护进程日志
  stats [天]  最近 N 天打卡表（默认 14）
  run         前台跑守护进程（调试用）"""


def _plist() -> str:
    tmpl = (ROOT / "launchd" / "wakeupcall.plist.template").read_text()
    return (tmpl.replace("{{LABEL}}", LABEL).replace("{{PYTHON}}", PY)
                .replace("{{ROOT}}", str(ROOT)).replace("{{LOG}}", str(journal.DAEMON_LOG)))


def _loaded() -> bool:
    p = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    return LABEL in p.stdout


def cmd_on() -> int:
    load()                                             # 先校验配置，占位值直接拒
    journal.LOG_DIR.mkdir(exist_ok=True)
    STATE_DIR.mkdir(exist_ok=True)
    if _loaded():
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    PLIST.write_text(_plist())
    subprocess.run(["launchctl", "load", str(PLIST)], check=True)
    time.sleep(1.5)
    print(f"✅ WakeUpCall 已启动（launchd {LABEL}）\n   日志：wakeupcall log ／ 状态：wakeupcall status")
    return 0


def cmd_off() -> int:
    if PLIST.exists():
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        PLIST.unlink()
    print("🛑 WakeUpCall 已停止并卸载")
    return 0


def cmd_status() -> int:
    cfg = load()
    now = datetime.now(cfg.tz)
    print(f"守护进程：{'✅ 运行中' if _loaded() else '🛑 未运行'}（{LABEL}）")
    print(f"配置：{CONFIG_PATH}")
    print(f"起床 {'%02d:%02d' % cfg.wake_time}  睡前提醒 "
          f"{'%02d:%02d' % cfg.bed_time if cfg.bed_enabled else '关'}  时区 {cfg.tz.key}"
          f"  现在 {now:%Y-%m-%d %H:%M}")
    print("升级阶梯：" + "  ".join(f"{s.after_min:g}m→{s.action}" for s in cfg.steps))
    if DAY_FILE.exists():
        import json
        d = json.loads(DAY_FILE.read_text())
        print(f"今天({d.get('date')})：起床 {d.get('wake_phase')}"
              f"  已升级 {d.get('fired')}  上床 {d.get('bed_checkin') or '-'}")
    return 0


def cmd_checkin() -> int:
    STATE_DIR.mkdir(exist_ok=True)
    CHECKIN_FILE.touch()
    print("✅ 已本机打卡（守护进程 10 秒内确认并停铃）")
    return 0


def cmd_drill(scale: str = "0.1") -> int:
    if not _loaded():
        print("守护进程没在跑，先 wakeupcall on", file=sys.stderr)
        return 1
    STATE_DIR.mkdir(exist_ok=True)
    DRILL_FILE.write_text(scale)
    print(f"🎯 演习已触发（倍率 {scale}）。手机会收到消息，Mac 会响铃；回「起」或 wakeupcall checkin 结束。")
    return 0


def cmd_test_msg() -> int:
    from . import imsg
    cfg = load()
    v = imsg.send(f"🎯 WakeUpCall 通道测试 {datetime.now(cfg.tz):%H:%M}", cfg.self_number)
    print(f"✅ 已发（变体 {v}），看手机有没有横幅。")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd == "on":
        return cmd_on()
    if cmd == "off":
        return cmd_off()
    if cmd == "status":
        return cmd_status()
    if cmd == "checkin":
        return cmd_checkin()
    if cmd == "drill":
        return cmd_drill(argv[1] if len(argv) > 1 else "0.1")
    if cmd == "test-msg":
        return cmd_test_msg()
    if cmd == "log":
        return subprocess.call(["tail", "-n", "30", "-f", str(journal.DAEMON_LOG)])
    if cmd == "stats":
        print(journal.stats(int(argv[1]) if len(argv) > 1 else 14))
        return 0
    if cmd == "run":
        from .daemon import main as run
        run()
        return 0
    print(USAGE)
    return 64
