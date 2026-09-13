"""Main loop: every 10 s run the bedtime-reminder / wake-check-in / escalation state machine."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timedelta

from . import imsg, journal, reminders
from .alarm import Alarm, wake_screen
from .config import STATE_DIR, Config, Step, load

DAY_FILE = STATE_DIR / "day.json"
CHECKIN_FILE = STATE_DIR / "checkin"
DRILL_FILE = STATE_DIR / "drill"
POLL_SEC = 10
# Every outgoing message starts with one of these; incoming rows with them are ours and skipped.
SELF_MARKS = ("⏰", "✅", "❌", "🌙", "📣", "🎯")


class Day:
    def __init__(self, date: str):
        self.date = date
        self.bed_reminded = False
        self.bed_checkin: str | None = None
        self.wake_phase = "idle"
        self.wake_started: str | None = None
        self.target: str = ""
        self.fired: list[int] = []
        self.rowid_base = 0
        self.drill_scale: float | None = None
        self.reminder_ids: dict = {}

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
    def __init__(self, cfg: Config, now_fn=None):
        self.cfg = cfg
        self._now_fn = now_fn
        self.alarm = Alarm(cfg.alarm_sound, cfg.alarm_volume, cfg.alarm_interval)
        self.day = Day.load(self.now().strftime("%Y-%m-%d"))
        # Only messages newer than this rowid are considered; old ones are never replayed.
        self.last_rowid = imsg.max_rowid()

    def now(self) -> datetime:
        return self._now_fn() if self._now_fn else datetime.now(self.cfg.tz)

    def log(self, msg: str) -> None:
        journal.log(msg, self.now())

    def notify(self, text: str) -> None:
        try:
            imsg.send(text, self.cfg.self_number)
        except RuntimeError as e:
            self.log(f"WARN {e}")

    def incoming(self) -> list[str]:
        """Return new inbound message bodies (lowercased, stripped), skipping our own."""
        try:
            rows = imsg.fetch_since(self.last_rowid)
        except Exception as e:  # noqa: BLE001
            self.log(f"WARN chat.db read failed: {e}")
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
        t = re.sub(r"[\s!\uff01\u3002.,\uff0c~\uff5e]+$", "", text)
        return t in keywords

    def wake_due(self, now: datetime) -> bool:
        """True when inside [wake time, wake time + hard_timeout) on an enabled weekday."""
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
        self.log(f"Wake session started (target {d.target}{', drill' if drill_scale else ''})")
        wake_screen()
        if self.cfg.alarm_enabled:
            self.alarm.start()

    def ensure_reminder(self, now: datetime) -> None:
        """Make sure a repeating reminder covers today (ahead of wake time) and tomorrow (after today ends)."""
        cfg, d = self.cfg, self.day
        if not cfg.rem_enabled:
            return
        h, m = cfg.wake_time
        hm = f"{h:02d}:{m:02d}"
        repeat = "daily" if len(cfg.weekdays) == 7 else ",".join(str(x) for x in sorted(cfg.weekdays))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        wanted: list[str] = []
        if now >= target - timedelta(minutes=cfg.rem_ahead_min) and not self.wake_passed(now):
            wanted.append(d.date)
        if d.wake_phase in ("done", "missed", "skipped") or self.wake_passed(now):
            wanted.append((now + timedelta(days=1)).strftime("%Y-%m-%d"))
        for date in wanted:
            if date in d.reminder_ids:
                continue
            if datetime.strptime(date, "%Y-%m-%d").isoweekday() not in cfg.weekdays:
                continue
            try:
                r = reminders.ensure(cfg.rem_list, cfg.rem_title, date, hm, cfg.tz.key,
                                     f"Complete this reminder after waking up = check-in. Target {hm}.", repeat)
            except reminders.RemindersError as e:
                self.log(f"WARN reminder create failed: {e}")
                return
            d.reminder_ids[date] = r["id"]
            d.save()
            self.log(f"Reminder {'created' if r['created'] else 'exists'}: {cfg.rem_title} {date} {hm}")

    def reminder_completion(self) -> datetime | None:
        cfg, d = self.cfg, self.day
        if not cfg.rem_enabled or d.drill_scale:
            return None
        try:
            r = reminders.status(cfg.rem_list, cfg.rem_title, d.date, cfg.tz.key)
        except reminders.RemindersError as e:
            self.log(f"WARN reminder read failed: {e}")
            return None
        if r.get("completed") and r.get("completion"):
            return r["completion"].astimezone(cfg.tz)
        return None

    def elapsed_min(self, now: datetime) -> float:
        started = datetime.fromisoformat(self.day.wake_started)
        return (now - started).total_seconds() / 60

    def run_step(self, i: int, step: Step, now: datetime) -> None:
        d = self.day
        drill = bool(d.drill_scale)
        prefix = "🎯[drill] " if drill else ""
        fmt = {"name": self.cfg.tattle_name, "time": d.target,
               "late": f"{self.elapsed_min(now):.0f}"}
        text = step.text.format(**fmt) if step.text else ""
        if step.action == "notify":
            self.notify(prefix + text)
        elif step.action == "tattle":
            # In a drill the tattle goes to yourself instead of your friends.
            targets = [self.cfg.self_number] if drill else step.to
            for to in targets:
                try:
                    imsg.send(("📣[drill, would go to a friend] " if drill else "") + text, to)
                except RuntimeError as e:
                    self.log(f"WARN tattle send failed {to}: {e}")
            if not drill:
                self.notify(f"📣 Tattled: sent to {len(step.to)} contact(s).")
        elif step.action == "shell" and step.cmd:
            if drill:
                self.log(f"[drill] skipping shell: {step.cmd}")
            else:
                subprocess.Popen(shlex.split(step.cmd), cwd=STATE_DIR.parent)
        d.fired.append(i)
        d.save()
        journal.event("escalate", now, step=i, action=step.action, after_min=step.after_min,
                      drill=drill)
        self.log(f"Escalation step {i}: {step.action} ({step.after_min} min)")

    def finish(self, now: datetime, via: str, at: datetime | None = None) -> None:
        d = self.day
        self.alarm.stop()
        at = at or now
        late = max(0.0, self.elapsed_min(at))
        d.wake_phase = "done"
        d.save()
        journal.event("checkin", now, via=via, late_min=round(late, 1), target=d.target,
                      drill=bool(d.drill_scale))
        self.log(f"Checked in via {via} at {at:%H:%M}, {late:.0f} min late")
        self.notify(f"✅ Checked in at {at:%H:%M} via {via}, {late:.0f} min after {d.target}."
                    + (" (drill)" if d.drill_scale else ""))

    def miss(self, now: datetime) -> None:
        d = self.day
        self.alarm.stop()
        d.wake_phase = "missed"
        d.save()
        journal.event("missed", now, target=d.target, drill=bool(d.drill_scale))
        self.log("Hard timeout reached, recorded as missed")
        self.notify(f"❌ No check-in for the {d.target} wake-up; recorded as missed.")

    def tick(self) -> None:
        now = self.now()
        today = now.strftime("%Y-%m-%d")
        if self.day.date != today:
            if self.day.wake_phase == "active":
                self.miss(now)
            self.day = Day(today)
            self.day.save()
        d, cfg = self.day, self.cfg

        if DRILL_FILE.exists():
            try:
                scale = float(DRILL_FILE.read_text().strip() or "1")
            except ValueError:
                scale = 1.0
            DRILL_FILE.unlink()
            if d.wake_phase != "active":
                self.start_wake(now, drill_scale=scale)

        if cfg.bed_enabled and not d.bed_reminded and (now.hour, now.minute) >= cfg.bed_time:
            d.bed_reminded = True
            d.save()
            self.notify(f"🌙 It is {'%02d:%02d' % cfg.bed_time}, time for bed. Reply with a bedtime keyword to log it.")
            self.log("Bedtime reminder sent")

        msgs = self.incoming()

        if cfg.bed_keywords and any(self.matches(m, cfg.bed_keywords) for m in msgs):
            d.bed_checkin = now.strftime("%H:%M")
            d.save()
            journal.event("bed_checkin", now)
            self.notify(f"🌙 Logged: in bed at {now:%H:%M}.")
            self.log("Bedtime check-in")

        self.ensure_reminder(now)
        if d.wake_phase == "idle":
            if self.wake_due(now):
                self.start_wake(now)
            elif self.wake_passed(now):
                # The daemon started after the window closed; do not ring today.
                d.wake_phase = "skipped"
                d.save()
                self.log("Wake window already passed today, skipping")
        if d.wake_phase == "active":
            if cfg.alarm_enabled and not self.alarm.ringing:
                self.alarm.start()
            started = datetime.fromisoformat(d.wake_started)
            local_ok = CHECKIN_FILE.exists() and \
                datetime.fromtimestamp(CHECKIN_FILE.stat().st_mtime, cfg.tz) >= started
            if local_ok:
                CHECKIN_FILE.unlink()
                self.finish(now, "local")
                return
            if any(self.matches(m, cfg.wake_keywords) for m in msgs):
                self.finish(now, "iMessage")
                return
            if (done_at := self.reminder_completion()) and done_at >= started - timedelta(minutes=30):
                self.finish(now, "reminder", at=done_at)
                return
            el = self.elapsed_min(now)
            scale = d.drill_scale or 1.0
            for i, step in enumerate(cfg.steps):
                if i not in d.fired and el >= step.after_min * scale:
                    self.run_step(i, step, now)
            if el >= cfg.hard_timeout_min * scale:
                self.miss(now)

    def run(self) -> None:
        self.log(f"MorningWaker started: wake {'%02d:%02d' % self.cfg.wake_time} "
                 f"{self.cfg.tz.key}, listening after rowid={self.last_rowid}")
        try:
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001
                    self.log(f"ERROR tick failed: {e!r}")
                time.sleep(POLL_SEC)
        finally:
            self.alarm.stop()


def main() -> None:
    Daemon(load()).run()


if __name__ == "__main__":
    main()
