"""Alarm tests with every external command faked: ramp, pinned volume, built-in output, cap, restore."""
from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

import test_daemon  # noqa: F401

import morningwaker.alarm as A
from morningwaker.gate import ButtonCheck, load_check


class FakePlayer:
    def __init__(self, argv, **kw): self.argv, self.done = argv, threading.Event()
    def wait(self): self.done.wait(0.05)
    def poll(self): return 0 if self.done.is_set() else None
    def terminate(self): self.done.set()


class AlarmTest(unittest.TestCase):
    def setUp(self):
        self.calls: list[list[str]] = []
        self.volume = [55, False]
        self.patches = [
            mock.patch.object(A, "_osascript", self._osa),
            mock.patch.object(A, "_audio", lambda *a: self.calls.append(["audio", *a]) or "77"),
            mock.patch.object(A.subprocess, "Popen", lambda argv, **kw: self.calls.append(argv) or FakePlayer(argv)),
        ]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()

    def _osa(self, script):
        if script.startswith("set volume"):
            self.volume = [int(script.split()[4]), "with output muted" in script]
            self.calls.append(["setvol", str(self.volume[0])])
            return ""
        return str(self.volume[0]) if "output volume of" in script else str(self.volume[1]).lower()

    def settings(self, **kw):
        return A.AlarmSettings(sound=A.FALLBACK_SOUND, volume=80, interval=0.01, ramp_sec=60,
                               start_level=0.15, end_level=0.5, pin_sec=0.02, **kw)

    def test_ramp(self):
        s = self.settings()
        self.assertEqual(A.level_at(0, s), 0.15)
        self.assertEqual(A.level_at(30, s), 0.32)
        self.assertEqual(A.level_at(60, s), 0.5)
        self.assertEqual(A.level_at(600, s), 0.5)

    def test_rings_pins_and_restores(self):
        a = A.Alarm(self.settings())
        a.start(); time.sleep(0.1)
        self.assertTrue(a.ringing)
        self.assertIn(["audio", "builtin"], self.calls)
        self.assertTrue(any(c[0] == "caffeinate" for c in self.calls))
        plays = [c for c in self.calls if c[0] == "afplay"]
        self.assertTrue(plays and plays[0][1:3] == ["-v", "0.15"])
        self.volume = [10, True]
        time.sleep(0.1)
        self.assertEqual(self.volume, [80, False])
        a.stop()
        self.assertFalse(a.ringing)
        self.assertEqual(self.volume, [55, False])
        self.assertIn(["audio", "set", "77"], self.calls)

    def test_stops_after_cap_and_stays_stopped(self):
        a = A.Alarm(self.settings(max_ring_min=0.002))
        a.start(); time.sleep(0.4)
        self.assertFalse(a.ringing); self.assertTrue(a.expired)
        n = len(self.calls)
        a.start(); self.assertEqual(len(self.calls), n)
        a.stop(); self.assertFalse(a.expired)

    def test_missing_sound_falls_back(self):
        a = A.Alarm(A.AlarmSettings(sound="/nonexistent.m4r"))
        self.assertEqual(a.sound, A.FALLBACK_SOUND)

    def test_plan_has_no_side_effects(self):
        with mock.patch.object(A, "ensure_helper", lambda: None):
            plan = A.Alarm(self.settings()).plan()
        self.assertEqual(plan["volume"], 80); self.assertEqual(plan["ramp"][0], 0.15)
        self.assertFalse(any(c[0] in ("afplay", "setvol") for c in self.calls))


class ButtonTest(unittest.TestCase):
    def test_button_passes_without_input(self):
        c = load_check({"name": "wake", "type": "button", "button": "Up"})
        self.assertIsInstance(c, ButtonCheck)
        p = c.prompt(None)
        self.assertFalse(p["input"]); self.assertTrue(p["big"]); self.assertEqual(p["button"], "Up")
        self.assertTrue(c.verify(None, "")[0])


if __name__ == "__main__":
    unittest.main()
