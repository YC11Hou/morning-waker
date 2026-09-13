"""Reminders channel via the EventKit helper binary; creates and reads only, never completes or deletes."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from .config import ROOT

SRC = ROOT / "helpers" / "reminders.swift"
BIN = ROOT / "build" / "reminders"


class RemindersError(RuntimeError):
    pass


def ensure_built() -> Path:
    if not BIN.exists() or BIN.stat().st_mtime < SRC.stat().st_mtime:
        BIN.parent.mkdir(exist_ok=True)
        p = subprocess.run(["swiftc", "-O", str(SRC), "-o", str(BIN)],
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise RemindersError(f"failed to compile reminders helper: {p.stderr[-400:]}")
    return BIN


def _run(*args: str) -> dict:
    p = subprocess.run([str(ensure_built()), *args], capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RemindersError(p.stderr.strip() or f"exit {p.returncode}")
    return json.loads(p.stdout)


def lists() -> list[str]:
    return _run("lists")


def ensure(list_name: str, title: str, date: str, hm: str, tz: str, notes: str = "") -> dict:
    """Create the reminder for that date if missing; return {id, created}."""
    return _run("ensure", list_name, title, date, hm, tz, notes)


def status(list_name: str, title: str, date: str, tz: str) -> dict:
    """Return {exists, completed, completion (datetime or None), id}."""
    r = _run("status", list_name, title, date, tz)
    if r.get("completion"):
        r["completion"] = datetime.fromisoformat(r["completion"].replace("Z", "+00:00"))
    return r
