"""Check-in gate: a page served only on 127.0.0.1 whose checks must all pass on this Mac before the day counts."""
from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from . import location, reminders
from .config import ROOT, STATE_DIR, Config

STATE_FILE = STATE_DIR / "gate.json"
WEB_DIR = ROOT / "web"
WAIVER_TTL_SEC = 60
_LOCK = threading.Lock()


@dataclass
class Context:
    date: str
    now: datetime
    state_dir: Path


class Check:
    """Base class for a gate check; plugins subclass it and override prompt() and verify()."""

    title = "Check"

    def __init__(self, name: str, options: dict):
        self.name, self.options = name, options
        self.title = options.get("title", self.title)

    def prompt(self, ctx: Context) -> dict:
        """Return {"text": str, "input": bool, "button": str} for the page."""
        return {"text": "", "input": False, "button": "Verify"}

    def verify(self, ctx: Context, answer: str) -> tuple[bool, str]:
        raise NotImplementedError


class LocationCheck(Check):
    """Pass when the Mac is within radius_m of (lat, lon)."""

    title = "Location"

    def prompt(self, ctx: Context) -> dict:
        r = float(self.options.get("radius_m", 1000))
        return {"text": f"Be within {r:g} m of your check-in place, then verify on this Mac.",
                "input": False, "button": "Verify location"}

    def verify(self, ctx: Context, answer: str) -> tuple[bool, str]:
        try:
            here = location.current(float(self.options.get("timeout_sec", 20)))
        except location.LocationError as e:
            return False, f"Could not read the Mac's location: {e}"
        max_acc = float(self.options.get("max_accuracy_m", 500))
        if here["acc"] < 0 or here["acc"] > max_acc:
            return False, f"Location too imprecise ({here['acc']:.0f} m); connect to Wi-Fi and retry."
        d = location.distance_m(here["lat"], here["lon"], float(self.options["lat"]), float(self.options["lon"]))
        r = float(self.options.get("radius_m", 1000))
        if d > r:
            return False, f"You are {d / 1000:.2f} km away; the limit is {r / 1000:.2f} km."
        return True, f"Location verified ({d:.0f} m from the target)."


def load_check(spec: dict) -> Check:
    kind, name = spec.get("type", ""), spec.get("name") or spec.get("type", "")
    if kind == "location":
        if "lat" not in spec or "lon" not in spec:
            raise ValueError(f"gate check {name!r}: location needs lat and lon")
        return LocationCheck(name, spec)
    if kind == "plugin":
        path = Path(spec.get("module", ""))
        path = path if path.is_absolute() else ROOT / path
        mod_spec = importlib.util.spec_from_file_location(f"morningwaker_gate_{name}", path)
        if not mod_spec or not path.exists():
            raise ValueError(f"gate check {name!r}: plugin module not found: {path}")
        mod = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
        check = mod.make(name, spec)
        if not isinstance(check, Check):
            raise ValueError(f"gate check {name!r}: make() must return a morningwaker.gate.Check")
        return check
    raise ValueError(f"gate check {name!r}: unknown type {kind!r} (use 'location' or 'plugin')")


class Gate:
    def __init__(self, cfg: Config, checks: list[Check] | None = None):
        self.cfg = cfg
        self.checks = checks if checks is not None else [load_check(s) for s in cfg.gate_checks]
        self._waivers: tuple[str, float, dict] = ("", 0.0, {})

    def context(self, date: str, now: datetime) -> Context:
        return Context(date=date, now=now, state_dir=STATE_DIR)

    def _read(self, date: str) -> dict:
        try:
            st = json.loads(STATE_FILE.read_text())
            if st.get("date") == date:
                return st
        except (OSError, ValueError):
            pass
        return {"date": date, "passed": {}}

    def _write(self, st: dict) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1))
        os.replace(tmp, STATE_FILE)

    def passed(self, date: str) -> dict[str, datetime]:
        with _LOCK:
            st = self._read(date)
        return {k: datetime.fromisoformat(v) for k, v in st["passed"].items()}

    def reset(self, date: str) -> None:
        with _LOCK:
            self._write({"date": date, "passed": {}, "waived": self._read(date).get("waived", {})})

    def waive(self, date: str, name: str, reason: str) -> None:
        """Skip one check for one day by hand."""
        with _LOCK:
            st = self._read(date)
            st.setdefault("waived", {})[name] = reason
            self._write(st)

    def waivers(self, date: str) -> dict[str, str]:
        """Checks waived that day, by hand or by a matching reminder due that day: {name: reason}."""
        with _LOCK:
            manual = self._read(date).get("waived", {})
        return {**self._reminder_waivers(date), **manual}

    def _reminder_waivers(self, date: str) -> dict[str, str]:
        cached_date, at, val = self._waivers
        if cached_date == date and time.monotonic() - at < WAIVER_TTL_SEC:
            return val
        out: dict[str, str] = {}
        for c in self.checks:
            words = [w.lower() for w in c.options.get("waive_if_reminder", [])]
            skip = [w.lower() for w in c.options.get("waive_exclude", [])]
            if not words:
                continue
            try:
                titles = reminders.due(c.options.get("waive_list", self.cfg.rem_list), date, self.cfg.tz.key)
            except reminders.RemindersError:
                continue
            hits = (t for t in titles if any(w in t.lower() for w in words) and not any(w in t.lower() for w in skip))
            if hit := next(hits, None):
                out[c.name] = hit
        self._waivers = (date, time.monotonic(), out)
        return out

    def complete(self, date: str) -> datetime | None:
        """Time of the last required pass when every non-waived check has passed, else None."""
        waived, passed = self.waivers(date), self.passed(date)
        required = [c.name for c in self.checks if c.name not in waived]
        if not required or any(n not in passed for n in required):
            return None
        return max(passed[n] for n in required)

    def silenced(self, date: str) -> bool:
        passed = self.passed(date)
        return any(c.options.get("silences_alarm") and c.name in passed for c in self.checks)

    def submit(self, date: str, now: datetime, name: str, answer: str) -> tuple[bool, str]:
        check = next((c for c in self.checks if c.name == name), None)
        if check is None:
            return False, f"Unknown check {name!r}"
        if name in self.passed(date):
            return True, "Already passed today."
        try:
            ok, msg = check.verify(self.context(date, now), answer)
        except Exception as e:  # noqa: BLE001
            return False, f"Check failed with an error: {e}"
        if ok:
            with _LOCK:
                st = self._read(date)
                st["passed"][name] = now.isoformat()
                self._write(st)
        return ok, msg

    def snapshot(self, date: str, now: datetime) -> dict:
        waived, passed = self.waivers(date), self.passed(date)
        ctx = self.context(date, now)
        items = []
        for c in self.checks:
            item = {"name": c.name, "title": c.title, "passed": passed[c.name].strftime("%H:%M") if c.name in passed else None,
                    "waived": waived.get(c.name), "silences_alarm": bool(c.options.get("silences_alarm"))}
            if not item["passed"] and not item["waived"]:
                try:
                    item["prompt"] = c.prompt(ctx)
                except Exception as e:  # noqa: BLE001
                    item["prompt"] = {"text": f"Prompt failed: {e}", "input": False, "button": "Retry"}
            items.append(item)
        return {"checks": items, "complete": self.complete(date) is not None}


def serve(gate: Gate, status: Callable[[], dict], port: int, on_submit: Callable[[], None] | None = None) -> ThreadingHTTPServer:
    """Start the gate page on 127.0.0.1 in a background thread; status() returns {date, now, phase, open}."""
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(WEB_DIR), **kw)

        def log_message(self, *a):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def _json(self, obj, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _state(self) -> dict:
            st = status()
            return {**st, "now": st["now"].strftime("%H:%M"), **gate.snapshot(st["date"], st["now"])}

        def do_GET(self):
            # Reject other Host headers so a web page cannot reach the gate through DNS rebinding.
            if self.headers.get("Host") not in allowed_hosts:
                return self._json({"error": "forbidden"}, 403)
            if self.path.startswith("/api/gate"):
                return self._json(self._state())
            if self.path in ("/", "/gate"):
                self.path = "/gate.html"
            return super().do_GET()

        def do_POST(self):
            if self.headers.get("Host") not in allowed_hosts or \
                    not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json({"error": "forbidden"}, 403)
            name = self.path.removeprefix("/api/gate/")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            except ValueError:
                body = {}
            st = status()
            if not st["open"]:
                return self._json({"ok": False, "message": "The gate is closed right now."})
            ok, msg = gate.submit(st["date"], st["now"], name, str(body.get("answer", ""))[:5000])
            if on_submit:
                on_submit()
            return self._json({"ok": ok, "message": msg, **self._state()})

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
