"""状态机单元测试：假时钟、假 iMessage、假提醒事项、假闹铃。不碰真实系统。
    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["WAKEUPCALL_CONFIG"] = str(TMP / "cfg.toml")
(TMP / "cfg.toml").write_text('''
[general]
timezone = "Asia/Singapore"
[imessage]
self = "+6511111111"
[wake]
time = "09:00"
keywords = ["起", "up"]
hard_timeout_min = 60
[reminders]
enabled = true
list = "L"
title = "T"
create_ahead_min = 60
[alarm]
enabled = true
[[escalation]]
after_min = 0
action = "notify"
text = "wake"
[[escalation]]
after_min = 15
action = "tattle"
to = ["+6522222222"]
text = "{name} late {late}"
[tattle]
name = "N"
''')

import wakeupcall.config as C  # noqa: E402
C.STATE_DIR = TMP / "state"; C.LOG_DIR = TMP / "logs"
import wakeupcall.journal as J  # noqa: E402
J.LOG_DIR = C.LOG_DIR; J.JOURNAL = C.LOG_DIR / "journal.jsonl"; J.DAEMON_LOG = C.LOG_DIR / "d.log"
import wakeupcall.daemon as D  # noqa: E402
D.STATE_DIR = C.STATE_DIR
D.DAY_FILE = C.STATE_DIR / "day.json"; D.CHECKIN_FILE = C.STATE_DIR / "checkin"; D.DRILL_FILE = C.STATE_DIR / "drill"

TZ = ZoneInfo("Asia/Singapore")


class FakeAlarm:
    def __init__(self, *a): self.ringing = False; self.starts = 0
    def start(self): self.ringing = True; self.starts += 1
    def stop(self): self.ringing = False


class Harness:
    def __init__(self, start: datetime):
        self.now = start
        self.sent: list[tuple[str, str]] = []
        self.inbox: list[tuple[int, str]] = []
        self.rowid = 100
        self.rem: dict[str, dict] = {}          # date -> status
        self.created: list[str] = []
        self.patches = [
            mock.patch.object(D.imsg, "send", lambda body, to: self.sent.append((to, body)) or "v1"),
            mock.patch.object(D.imsg, "max_rowid", lambda: self.rowid),
            mock.patch.object(D.imsg, "fetch_since", self._fetch),
            mock.patch.object(D.reminders, "ensure", self._ensure),
            mock.patch.object(D.reminders, "status", self._status),
            mock.patch.object(D, "Alarm", FakeAlarm),
            mock.patch.object(D, "wake_screen", lambda *a: None),
        ]
        for p in self.patches: p.start()
        for f in (D.DAY_FILE, D.CHECKIN_FILE, D.DRILL_FILE):
            f.unlink(missing_ok=True)
        self.d = D.Daemon(C.load(), now_fn=lambda: self.now)

    def close(self):
        for p in self.patches: p.stop()

    def _fetch(self, since, limit=100):
        return [(r, t) for r, t in self.inbox if r > since]

    def _ensure(self, lst, title, date, hm, tz, notes=""):
        self.created.append(date); self.rem.setdefault(date, {"exists": True, "completed": False, "completion": None})
        return {"id": "id-" + date, "created": True}

    def _status(self, lst, title, date, tz):
        return self.rem.get(date, {"exists": False, "completed": False, "completion": None})

    def phone(self, text):
        self.rowid += 1; self.inbox.append((self.rowid, text))

    def tick(self, minutes=0.0):
        self.now += timedelta(minutes=minutes); self.d.tick()

    @property
    def phase(self): return self.d.day.wake_phase


class DaemonTests(unittest.TestCase):
    def setUp(self): self.h = Harness(datetime(2026, 9, 14, 7, 0, tzinfo=TZ))   # 周一 07:00
    def tearDown(self): self.h.close()

    def test_creates_todays_reminder_one_hour_ahead(self):
        h = self.h
        h.tick(); self.assertEqual(h.created, [])
        h.tick(60); self.assertEqual(h.created, ["2026-09-14"])
        h.tick(1); self.assertEqual(h.created, ["2026-09-14"])          # 幂等

    def test_wake_starts_at_target_and_first_step_fires(self):
        h = self.h
        h.tick(119); self.assertEqual(h.phase, "idle")
        h.tick(1)                                                        # 09:00
        self.assertEqual(h.phase, "active"); self.assertTrue(h.d.alarm.ringing)
        self.assertEqual([b for _, b in h.sent], ["wake"])

    def test_phone_reply_checks_in_and_stops_alarm(self):
        h = self.h
        h.tick(120); h.phone("起！"); h.tick(3)
        self.assertEqual(h.phase, "done"); self.assertFalse(h.d.alarm.ringing)
        self.assertIn("✅", h.sent[-1][1]); self.assertIn("晚 3 分钟", h.sent[-1][1])

    def test_reminder_completion_checks_in_with_its_own_time(self):
        h = self.h
        h.tick(120)
        h.rem["2026-09-14"] = {"exists": True, "completed": True,
                               "completion": datetime(2026, 9, 14, 9, 7, tzinfo=TZ)}
        h.tick(10)                                                       # 09:10 才读到
        self.assertEqual(h.phase, "done"); self.assertIn("09:07", h.sent[-1][1])
        self.assertIn("提醒事项", h.sent[-1][1])

    def test_own_messages_do_not_self_trigger(self):
        h = self.h
        h.tick(120); h.phone("⏰ 起床打卡！回复「起」结束闹铃。"); h.phone("起床打卡"); h.tick(1)
        self.assertEqual(h.phase, "active")

    def test_tattle_fires_at_15_and_goes_to_friend(self):
        h = self.h
        h.tick(120); h.tick(14); self.assertNotIn("+6522222222", [t for t, _ in h.sent])
        h.tick(1)
        friend = [b for t, b in h.sent if t == "+6522222222"]
        self.assertEqual(friend, ["N late 15"])

    def test_missed_after_hard_timeout_then_next_day_reminder(self):
        h = self.h
        h.tick(120); h.tick(60)
        self.assertEqual(h.phase, "missed"); self.assertFalse(h.d.alarm.ringing)
        self.assertIn("2026-09-15", h.created)                           # 明天那条已建

    def test_late_start_skips_today(self):
        h = self.h
        h.now = datetime(2026, 9, 14, 13, 0, tzinfo=TZ); h.tick()
        self.assertEqual(h.phase, "skipped"); self.assertEqual(h.sent, [])
        self.assertIn("2026-09-15", h.created)

    def test_restart_mid_session_resumes_ringing(self):
        h = self.h
        h.tick(120); h.tick(2)
        d2 = D.Daemon(C.load(), now_fn=lambda: h.now)                    # 模拟重启
        self.assertEqual(d2.day.wake_phase, "active"); self.assertEqual(d2.day.fired, [0])
        d2.tick(); self.assertTrue(d2.alarm.ringing)

    def test_local_checkin_file(self):
        h = self.h
        h.tick(120); h.tick(1); D.CHECKIN_FILE.touch()
        os.utime(D.CHECKIN_FILE, (h.now.timestamp(), h.now.timestamp())); h.tick(1)
        self.assertEqual(h.phase, "done")


if __name__ == "__main__":
    unittest.main()
