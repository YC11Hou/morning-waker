"""Main loop: every 10 s run the bedtime-reminder / wake-check-in / escalation state machine."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
from datetime import datetime, timedelta

from . import imsg, journal, reminders
from .alarm import Alarm, AlarmSettings, wake_screen
from .config import STATE_DIR, Config, Step, load
from .gate import Gate
from .gate import serve as serve_gate

DAY_FILE = STATE_DIR / "day.json"
CHECKIN_FILE = STATE_DIR / "checkin"
DRILL_FILE = STATE_DIR / "drill"
POLL_SEC = 10
# Every outgoing message starts with one of these; incoming rows with them are ours and skipped.
SELF_MARKS = ("⏰", "✅", "❌", "🌙", "📣", "🎯")


def open_page(url: str) -> None:
    subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def alarm_settings(cfg: Config) -> AlarmSettings:
    return AlarmSettings(sound=cfg.alarm_sound, volume=cfg.alarm_volume, interval=cfg.alarm_interval,
                         ramp_sec=cfg.alarm_ramp_sec, start_level=cfg.alarm_start_level, end_level=cfg.alarm_end_level,
                         max_ring_min=cfg.alarm_max_ring_min, builtin_speakers=cfg.alarm_builtin)


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
        self.alarm = Alarm(alarm_settings(cfg))
        self.poke = threading.Event()
        self.day = Day.load(self.now().strftime("%Y-%m-%d"))
        self.gate = Gate(cfg) if cfg.checkin_mode == "gate" else None
        # Only messages newer than this rowid are considered; old ones are never replayed.
        self.last_rowid = self.rowid()

    def rowid(self) -> int:
        """Latest chat.db rowid, or 0 when chat.db is unreadable (no Full Disk Access); only bedtime keywords need it."""
        try:
            return imsg.max_rowid()
        except Exception:  # noqa: BLE001
            return 0

    @property
    def gate_url(self) -> str:
        return f"http://127.0.0.1:{self.cfg.gate_port}/"

    def gate_status(self) -> dict:
        now = self.now()
        return {"date": self.day.date, "now": now, "phase": self.day.wake_phase, "open": self.gate_open(now)}

    def gate_open(self, now: datetime) -> bool:
        """The gate accepts answers from open_before_min before wake time until the day is settled."""
        d, cfg = self.day, self.cfg
        if d.wake_phase == "active":
            return True
        if d.wake_phase != "idle" or now.isoweekday() not in cfg.weekdays or self.wake_passed(now):
            return False
        h, m = cfg.wake_time
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return now >= target - timedelta(minutes=cfg.gate_open_before_min)

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
        d.fired, d.rowid_base, d.drill_scale = [], self.rowid(), drill_scale
        d.save()
        if self.gate and drill_scale:
            self.gate.reset(d.date)
        journal.event("wake_start", now, target=d.target, drill=bool(drill_scale))
        self.log(f"Wake session started (target {d.target}{', drill' if drill_scale else ''})")
        wake_screen()
        if self.gate:
            open_page(self.gate_url)
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
            # Keyed by date and time, so a changed wake time re-runs ensure and moves the series.
            if f"{date} {hm}" in d.reminder_ids:
                continue
            if datetime.strptime(date, "%Y-%m-%d").isoweekday() not in cfg.weekdays:
                continue
            try:
                notes = (f"Alarm only: completing this reminder does not check you in. Check in on the Mac at {self.gate_url}."
                         if self.gate else f"Complete this reminder after waking up = check-in. Target {hm}.")
                r = reminders.ensure(cfg.rem_list, cfg.rem_title, date, hm, cfg.tz.key, notes, repeat)
            except reminders.RemindersError as e:
                self.log(f"WARN reminder create failed: {e}")
                return
            d.reminder_ids[f"{date} {hm}"] = r["id"]
            d.save()
            state = "created" if r["created"] else "retimed" if r.get("retimed") else "exists"
            self.log(f"Reminder {state}: {cfg.rem_title} {date} {hm}")

    def reminder_completion(self) -> datetime | None:
        cfg, d = self.cfg, self.day
        if not cfg.rem_enabled or d.drill_scale or self.gate:
            return None
        try:
            r = reminders.status(cfg.rem_list, cfg.rem_title, d.date, cfg.tz.key)
        except reminders.RemindersError as e:
            self.log(f"WARN reminder read failed: {e}")
            return None
        if r.get("completed") and r.get("completion"):
            return r["completion"].astimezone(cfg.tz)
        return None

    def gate_completion(self) -> datetime | None:
        if not self.gate:
            return None
        at = self.gate.complete(self.day.date)
        return at.astimezone(self.cfg.tz) if at else None

    def completion(self) -> tuple[str, datetime] | None:
        """The day's check-in from whichever channel the mode allows: (via, time)."""
        if at := self.gate_completion():
            return "gate", at
        if at := self.reminder_completion():
            return "reminder", at
        return None

    def elapsed_min(self, now: datetime) -> float:
        started = datetime.fromisoformat(self.day.wake_started)
        return (now - started).total_seconds() / 60

    def run_step(self, i: int, step: Step, now: datetime) -> None:
        d = self.day
        drill = bool(d.drill_scale)
        settled = {**self.gate.passed(d.date), **self.gate.waivers(d.date)} if self.gate else {}
        if step.unless_passed and self.gate and all(n in settled for n in step.unless_passed):
            d.fired.append(i)
            d.save()
            self.log(f"Escalation step {i} skipped: {', '.join(step.unless_passed)} already passed or waived")
            return
        prefix = "🎯[drill] " if drill else ""
        # The first open may land behind a locked screen, so every step brings the gate page back to the front.
        if self.gate and i > 0:
            open_page(self.gate_url)
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
        if self.gate and d.drill_scale:
            self.gate.reset(d.date)
        # The event is stamped with the check-in time itself (reminder completion), not the detection time.
        journal.event("checkin", at, via=via, late_min=round(late, 1), target=d.target,
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
                # A check-in already made today (reminder or gate, per mode) never rings.
                if done := self.completion():
                    d.wake_started = now.replace(hour=cfg.wake_time[0], minute=cfg.wake_time[1],
                                                 second=0, microsecond=0).isoformat()
                    d.target = "%02d:%02d" % cfg.wake_time
                    self.finish(now, done[0], at=done[1])
                    return
                self.start_wake(now)
            elif self.wake_passed(now):
                # The daemon started after the window closed; do not ring today.
                d.wake_phase = "skipped"
                d.save()
                self.log("Wake window already passed today, skipping")
        if d.wake_phase == "active":
            # In gate mode a check marked silences_alarm stops the Mac alarm before the full check-in.
            if self.gate and self.gate.silenced(d.date):
                if self.alarm.ringing:
                    self.alarm.stop()
                    self.log("Alarm silenced by a gate check")
            elif cfg.alarm_enabled and not self.alarm.ringing:
                self.alarm.start()
            started = datetime.fromisoformat(d.wake_started)
            local_ok = CHECKIN_FILE.exists() and \
                datetime.fromtimestamp(CHECKIN_FILE.stat().st_mtime, cfg.tz) >= started
            if local_ok:
                CHECKIN_FILE.unlink()
                # The gate is the only real check-in in gate mode; the local file only ends drills there.
                if not self.gate or d.drill_scale:
                    self.finish(now, "local")
                    return
                self.log("Ignoring local check-in: gate mode requires the gate page")
            if done := self.completion():
                self.finish(now, done[0], at=done[1])
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
                 f"{self.cfg.tz.key}, check-in mode {self.cfg.checkin_mode}, listening after rowid={self.last_rowid}")
        if self.gate:
            serve_gate(self.gate, self.gate_status, self.cfg.gate_port, on_submit=self.poke.set)
            self.log(f"Gate page at {self.gate_url}")
        try:
            while True:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001
                    self.log(f"ERROR tick failed: {e!r}")
                # A gate submit pokes the loop so the alarm stops right away instead of on the next poll.
                self.poke.wait(POLL_SEC)
                self.poke.clear()
        finally:
            self.alarm.stop()


def main() -> None:
    Daemon(load()).run()


if __name__ == "__main__":
    main()
