"""Gate-mode tests: real question-bank plugin and location check, fake clock, fake position, fake Reminders."""
from __future__ import annotations

import http.client
import json
import unittest
from datetime import datetime, timedelta
from unittest import mock

from test_daemon import C, D, FRIEND, J, TMP, TZ, Harness  # noqa: F401

import morningwaker.gate as G  # noqa: E402

G.STATE_DIR = C.STATE_DIR
G.STATE_FILE = C.STATE_DIR / "gate.json"
HERE = (1.0, 2.0)
GATE_CFG = TMP / "gate.toml"
GATE_CFG.write_text((TMP / "cfg.toml").read_text().split("[[escalation]]")[0].replace("hard_timeout_min = 60", "hard_timeout_min = 120") + f'''
[checkin]
mode = "gate"
[gate]
port = 5199
[[gate.check]]
name = "question"
type = "plugin"
module = "plugins/question_bank.py"
bank = "plugins/questions.example.toml"
min_chars = 20
silences_alarm = true
[[gate.check]]
name = "campus"
type = "location"
lat = {HERE[0]}
lon = {HERE[1]}
radius_m = 1000
waive_if_reminder = ["interview"]
waive_exclude = ["confirm"]
[[escalation]]
after_min = 0
action = "notify"
text = "wake"
[[escalation]]
after_min = 15
action = "tattle"
unless_passed = ["question"]
to = ["{FRIEND}"]
text = "still in bed"
[[escalation]]
after_min = 90
action = "tattle"
unless_passed = ["campus"]
to = ["{FRIEND}"]
text = "not at school"
[tattle]
name = "N"
''')
GOOD = {"Two Sum": "Use a hash map from value to index and look up target - x in one pass, O(n).",
        "Valid Parentheses": "Push opening brackets on a stack, pop and compare the top on a closing one. O(n)."}


class GateHarness(Harness):
    def __init__(self, start: datetime, pos=HERE, due=()):
        super().__init__(start)
        self.pos, self.due = pos, list(due)
        G.STATE_FILE.unlink(missing_ok=True)
        for p in (mock.patch.object(G.location, "current", lambda timeout=20: {"lat": self.pos[0], "lon": self.pos[1], "acc": 30, "age": 0}),
                  mock.patch.object(G.reminders, "due", lambda lst, date, tz: self.due),
                  mock.patch.object(D, "open_page", lambda url: None)):
            p.start(); self.patches.append(p)
        self.d = D.Daemon(C.load(GATE_CFG), now_fn=lambda: self.now)

    def answer(self, text=None):
        g, date = self.d.gate, self.d.day.date
        q = g.checks[0].today(g.context(date, self.now))
        return g.submit(date, self.now, "question", GOOD[q["title"]] if text is None else text)

    def locate(self):
        return self.d.gate.submit(self.d.day.date, self.now, "campus", "")


class GateTests(unittest.TestCase):
    def setUp(self): self.h = GateHarness(datetime(2026, 9, 14, 7, 0, tzinfo=TZ))
    def tearDown(self): self.h.close()

    def test_completed_reminder_does_not_count(self):
        h = self.h
        h.tick(120)
        h.rem["2026-09-14"] = {"exists": True, "completed": True, "completion": h.now}
        h.tick(1)
        self.assertEqual(h.phase, "active"); self.assertTrue(h.d.alarm.ringing)

    def test_question_silences_alarm_but_is_not_a_checkin(self):
        h = self.h
        h.tick(120); h.tick(5)
        self.assertEqual(h.answer(), (True, mock.ANY))
        h.tick(1)
        self.assertFalse(h.d.alarm.ringing); self.assertEqual(h.phase, "active")
        h.tick(10)
        self.assertNotIn(FRIEND, [t for t, _ in h.sent])

    def test_both_checks_check_in_at_last_pass_time(self):
        h = self.h
        h.tick(120); h.answer(); h.tick(40)
        ok, msg = h.locate()
        self.assertTrue(ok, msg)
        h.tick(1)
        self.assertEqual(h.phase, "done")
        rec = [r for r in J.read_all() if r["kind"] == "checkin"][-1]
        self.assertEqual((rec["via"], rec["ts"]), ("gate", "2026-09-14 09:40:00"))

    def test_wrong_or_short_answer_fails(self):
        h = self.h
        h.tick(120)
        self.assertFalse(h.answer("no idea")[0])
        self.assertFalse(h.answer("I would think about this problem really hard and carefully")[0])
        h.tick(1); self.assertTrue(h.d.alarm.ringing)

    def test_far_away_fails_and_school_tattle_fires(self):
        h = self.h
        h.pos = (HERE[0] + 0.03, HERE[1])
        h.tick(120); h.answer()
        ok, msg = h.locate()
        self.assertFalse(ok); self.assertIn("km away", msg)
        h.tick(89); self.assertEqual([b for t, b in h.sent if t == FRIEND], [])
        h.tick(1)
        self.assertEqual([b for t, b in h.sent if t == FRIEND], ["not at school"])

    def test_gate_page_reopens_on_later_steps(self):
        h = self.h
        opened = []
        with mock.patch.object(D, "open_page", opened.append):
            h.tick(120); h.tick(15)
        self.assertEqual(len(opened), 2)

    def test_bed_tattle_fires_without_answer(self):
        h = self.h
        h.tick(120); h.tick(15)
        self.assertEqual([b for t, b in h.sent if t == FRIEND], ["still in bed"])

    def test_interview_reminder_waives_location(self):
        h = self.h
        h.due = ["Interview with Acme 14:00"]
        h.tick(120); h.answer(); h.tick(1)
        self.assertEqual(h.phase, "done")

    def test_excluded_word_does_not_waive(self):
        h = self.h
        h.due = ["Confirm interview slot"]
        h.tick(120); h.answer(); h.tick(1)
        self.assertEqual(h.phase, "active")

    def test_manual_waiver_checks_in_after_question(self):
        h = self.h
        h.tick(120); h.d.gate.waive("2026-09-14", "campus", "by hand"); h.answer(); h.tick(1)
        self.assertEqual(h.phase, "done")

    def test_one_missing_idea_is_tolerated_and_named(self):
        h = self.h
        h.tick(120)
        q = h.d.gate.checks[0].today(h.d.gate.context(h.d.day.date, h.now))
        ok, msg = h.answer(GOOD[q["title"]].replace("O(n)", "linear").replace("O(n).", "linear."))
        self.assertTrue(ok, msg)
        ok, msg = h.d.gate.checks[0].verify(h.d.gate.context(h.d.day.date, h.now), "I would think about this problem really hard")
        self.assertFalse(ok); self.assertIn("Missing:", msg)

    def test_passed_before_wake_never_rings(self):
        h = self.h
        h.tick(90); self.assertTrue(h.d.gate_open(h.now))
        h.answer(); h.locate(); h.tick(30)
        self.assertEqual(h.phase, "done"); self.assertEqual(h.d.alarm.starts, 0)

    def test_gate_closed_outside_window(self):
        h = self.h
        self.assertFalse(h.d.gate_open(h.now))
        h.now = datetime(2026, 9, 14, 12, 0, tzinfo=TZ)
        self.assertFalse(h.d.gate_open(h.now))

    def test_local_checkin_ignored_in_gate_mode(self):
        h = self.h
        h.tick(120); h.tick(1); D.CHECKIN_FILE.touch()
        h.tick(1)
        self.assertEqual(h.phase, "active")

    def test_http_server_rejects_foreign_host_and_accepts_answer(self):
        h = self.h
        h.tick(120)
        srv = G.serve(h.d.gate, h.d.gate_status, 5199)
        try:
            con = http.client.HTTPConnection("127.0.0.1", 5199, timeout=5)
            con.request("GET", "/api/gate", headers={"Host": "evil.example:5199"})
            self.assertEqual(con.getresponse().status, 403)
            con.request("GET", "/api/gate")
            st = json.loads(con.getresponse().read())
            self.assertEqual([c["name"] for c in st["checks"]], ["question", "campus"])
            q = h.d.gate.checks[0].today(h.d.gate.context(h.d.day.date, h.now))
            con.request("POST", "/api/gate/question", body=json.dumps({"answer": GOOD[q["title"]]}),
                        headers={"Content-Type": "application/json"})
            r = json.loads(con.getresponse().read())
            self.assertTrue(r["ok"]); self.assertEqual(r["checks"][0]["passed"], "09:00")
        finally:
            srv.shutdown(); srv.server_close()


if __name__ == "__main__":
    unittest.main()
