"""常驻循环：每 10 秒看一眼时间和 chat.db，跑「睡前提醒 → 起床打卡 → 升级」状态机。

状态存 state/day.json（按日期），进程重启不丢：
  wake_phase: idle | active | done | missed
一天只跑一次起床流程。打卡 = 手机回关键词（iMessage）或本机 `wakeupcall checkin`。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timedelta

from . import imsg, journal
from .alarm import Alarm, wake_screen
from .config import STATE_DIR, Config, Step, load

DAY_FILE = STATE_DIR / "day.json"
CHECKIN_FILE = STATE_DIR / "checkin"       # `wakeupcall checkin` touch 这个文件
DRILL_FILE = STATE_DIR / "drill"           # `wakeupcall drill` 写入：演习倍率
POLL_SEC = 10
# 自己发出去的消息一律以这些开头；读 chat.db 时见到就跳过（自发自收通道防自激）
SELF_MARKS = ("⏰", "✅", "❌", "🌙", "📣", "🎯")


class Day:
    def __init__(self, date: str):
        self.date = date
        self.bed_reminded = False
        self.bed_checkin: str | None = None
        self.wake_phase = "idle"
        self.wake_started: str | None = None     # ISO
        self.target: str = ""
        self.fired: list[int] = []               # 已执行的 escalation 下标
        self.rowid_base = 0                      # 只认此 rowid 之后的回复
        self.drill_scale: float | None = None

    @classmethod
    def load(cls, today: str) -> "Day":
        try:
            raw = json.loads(DAY_FILE.read_text())
            if raw.get("date") == today:
                d = cls(today)
                d.__dict__.update(raw)
                return d
        except (OSError, ValueError):
            pass
        return cls(today)

    def save(self) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        tmp = DAY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__, ensure_ascii=False, indent=1))
        os.replace(tmp, DAY_FILE)


class Daemon:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.alarm = Alarm(cfg.alarm_sound, cfg.alarm_volume, cfg.alarm_interval)
        self.day = Day.load(self.now().strftime("%Y-%m-%d"))
        self.last_rowid = imsg.max_rowid()       # 启动时只看以后的消息，不翻旧账

    # ── 基础 ──────────────────────────────────────────────────────────
    def now(self) -> datetime:
        return datetime.now(self.cfg.tz)

    def log(self, msg: str) -> None:
        journal.log(msg, self.now())

    def notify(self, text: str) -> None:
        try:
            imsg.send(text, self.cfg.self_number)
        except RuntimeError as e:
            self.log(f"⚠️ {e}")

    # ── 收消息 ────────────────────────────────────────────────────────
    def incoming(self) -> list[str]:
        """新到的、不是自己发的消息正文（小写、去空白）。"""
        try:
            rows = imsg.fetch_since(self.last_rowid)
        except Exception as e:                       # noqa: BLE001  chat.db 忙就下轮再读
            self.log(f"⚠️ chat.db 读取失败：{e}")
            return []
        out = []
        for rowid, text in rows:
            self.last_rowid = max(self.last_rowid, rowid)
            t = (text or "").strip()
            if not t or t.startswith(SELF_MARKS):
                continue
            if rowid <= self.day.rowid_base and self.day.wake_phase == "active":
                continue
            out.append(t.lower())
        return out

    @staticmethod
    def matches(text: str, keywords: list[str]) -> bool:
        t = re.sub(r"[\s!！。.,，~～]+$", "", text)
        return t in keywords

    # ── 起床流程 ──────────────────────────────────────────────────────
    def wake_due(self, now: datetime) -> bool:
        """到点了、且还在 hard_timeout 窗口内。守护进程若在窗口之后才启动（比如
        下午装的），今天直接跳过，不能一启动就响铃。"""
        h, m = self.cfg.wake_time
        if now.isoweekday() not in self.cfg.weekdays:
            return False
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return target <= now < target + timedelta(minutes=self.cfg.hard_timeout_min)

    def wake_passed(self, now: datetime) -> bool:
        h, m = self.cfg.wake_time
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return now >= target + timedelta(minutes=self.cfg.hard_timeout_min)

    def start_wake(self, now: datetime, drill_scale: float | None = None) -> None:
        d = self.day
        d.wake_phase, d.wake_started = "active", now.isoformat()
        d.target = now.strftime("%H:%M") if drill_scale else "%02d:%02d" % self.cfg.wake_time
        d.fired, d.rowid_base, d.drill_scale = [], imsg.max_rowid(), drill_scale
        d.save()
        journal.event("wake_start", now, target=d.target, drill=bool(drill_scale))
        self.log(f"⏰ 起床流程开始（目标 {d.target}{'，演习' if drill_scale else ''}）")
        wake_screen()
        if self.cfg.alarm_enabled:
            self.alarm.start()

    def elapsed_min(self, now: datetime) -> float:
        started = datetime.fromisoformat(self.day.wake_started)
        return (now - started).total_seconds() / 60

    def run_step(self, i: int, step: Step, now: datetime) -> None:
        d = self.day
        drill = bool(d.drill_scale)
        prefix = "🎯[演习] " if drill else ""
        fmt = {"name": self.cfg.tattle_name, "time": d.target,
               "late": f"{self.elapsed_min(now):.0f}"}
        text = step.text.format(**fmt) if step.text else ""
        if step.action == "notify":
            self.notify(prefix + text)
        elif step.action == "tattle":
            targets = [self.cfg.self_number] if drill else step.to
            for to in targets:
                try:
                    imsg.send(("📣[演习，本应发给朋友] " if drill else "") + text, to)
                except RuntimeError as e:
                    self.log(f"⚠️ 告状发送失败 {to}: {e}")
            if not drill:
                self.notify(f"📣 已告状：发给 {len(step.to)} 人。")
        elif step.action == "shell" and step.cmd:
            if drill:
                self.log(f"🎯[演习] 跳过 shell: {step.cmd}")
            else:
                subprocess.Popen(shlex.split(step.cmd), cwd=STATE_DIR.parent)
        d.fired.append(i)
        d.save()
        journal.event("escalate", now, step=i, action=step.action, after_min=step.after_min,
                      drill=drill)
        self.log(f"↑ 升级第 {i} 步：{step.action}（{step.after_min} 分钟）")

    def finish(self, now: datetime, via: str) -> None:
        d = self.day
        self.alarm.stop()
        late = self.elapsed_min(now)
        d.wake_phase = "done"
        d.save()
        journal.event("checkin", now, via=via, late_min=round(late, 1), target=d.target,
                      drill=bool(d.drill_scale))
        self.log(f"✅ 打卡成功（{via}，晚 {late:.0f} 分钟）")
        self.notify(f"✅ 打卡成功 {now:%H:%M}，比目标 {d.target} 晚 {late:.0f} 分钟。"
                    + ("（演习）" if d.drill_scale else ""))

    def miss(self, now: datetime) -> None:
        d = self.day
        self.alarm.stop()
        d.wake_phase = "missed"
        d.save()
        journal.event("missed", now, target=d.target, drill=bool(d.drill_scale))
        self.log("❌ 超时未打卡，记 missed")
        self.notify(f"❌ 今天 {d.target} 的打卡超时未完成，已记 missed。")

    # ── 主循环 ────────────────────────────────────────────────────────
    def tick(self) -> None:
        now = self.now()
        today = now.strftime("%Y-%m-%d")
        if self.day.date != today:
            if self.day.wake_phase == "active":
                self.miss(now)
            self.day = Day(today)
            self.day.save()
        d, cfg = self.day, self.cfg

        # 演习触发
        if DRILL_FILE.exists():
            try:
                scale = float(DRILL_FILE.read_text().strip() or "1")
            except ValueError:
                scale = 1.0
            DRILL_FILE.unlink()
            if d.wake_phase != "active":
                self.start_wake(now, drill_scale=scale)

        # 睡前提醒
        if cfg.bed_enabled and not d.bed_reminded and (now.hour, now.minute) >= cfg.bed_time:
            d.bed_reminded = True
            d.save()
            self.notify(f"🌙 {'%02d:%02d' % cfg.bed_time} 了，该上床了。躺下回「睡」记一笔。")
            self.log("🌙 已发睡前提醒")

        msgs = self.incoming()

        # 上床打卡（任何时候都认）
        if cfg.bed_keywords and any(self.matches(m, cfg.bed_keywords) for m in msgs):
            d.bed_checkin = now.strftime("%H:%M")
            d.save()
            journal.event("bed_checkin", now)
            self.notify(f"🌙 记下了：{now:%H:%M} 上床。")
            self.log("🌙 上床打卡")

        # 起床
        if d.wake_phase == "idle":
            if self.wake_due(now):
                self.start_wake(now)
            elif self.wake_passed(now):
                d.wake_phase = "skipped"          # 窗口已过（守护进程启动太晚）
                d.save()
                self.log("⏭ 今天的起床窗口已过，跳过")
        if d.wake_phase == "active":
            if cfg.alarm_enabled and not self.alarm.ringing:
                self.alarm.start()                       # 进程重启后恢复响铃
            started = datetime.fromisoformat(d.wake_started)
            local_ok = CHECKIN_FILE.exists() and \
                datetime.fromtimestamp(CHECKIN_FILE.stat().st_mtime, cfg.tz) >= started
            if local_ok:
                CHECKIN_FILE.unlink()
                self.finish(now, "本机")
                return
            if any(self.matches(m, cfg.wake_keywords) for m in msgs):
                self.finish(now, "iMessage")
                return
            el = self.elapsed_min(now)
            scale = d.drill_scale or 1.0
            for i, step in enumerate(cfg.steps):
                if i not in d.fired and el >= step.after_min * scale:
                    self.run_step(i, step, now)
            if el >= cfg.hard_timeout_min * scale:
                self.miss(now)

    def run(self) -> None:
        self.log(f"▶️ WakeUpCall 启动。起床 {'%02d:%02d' % self.cfg.wake_time}，"
                 f"时区 {self.cfg.tz.key}，从 rowid={self.last_rowid} 之后监听。")
        try:
            while True:
                try:
                    self.tick()
                except Exception as e:                   # noqa: BLE001  不让一次异常杀掉守护进程
                    self.log(f"💥 tick 异常：{e!r}")
                time.sleep(POLL_SEC)
        finally:
            self.alarm.stop()


def main() -> None:
    Daemon(load()).run()


if __name__ == "__main__":
    main()
