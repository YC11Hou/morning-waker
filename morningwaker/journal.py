"""Event journal (logs/journal.jsonl, one event per line) and daemon log; both are gitignored."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .config import LOG_DIR

JOURNAL = LOG_DIR / "journal.jsonl"
DAEMON_LOG = LOG_DIR / "daemon.log"


def event(kind: str, now: datetime, **fields) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    rec = {"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "date": now.strftime("%Y-%m-%d"),
           "kind": kind, **fields}
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def log(msg: str, now: datetime) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    line = f"{now:%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    # Under launchd stdout already goes to the log file, so skip the second write.
    if os.environ.get("MORNINGWAKER_DAEMON") == "1":
        return
    with open(DAEMON_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_all() -> list[dict]:
    if not JOURNAL.exists():
        return []
    out = []
    for line in JOURNAL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def days(include_drills: bool = False) -> dict[str, dict]:
    """Aggregate events per date: {date: {target, checkin, late, via, missed, tattled, steps, events}}."""
    out: dict[str, dict] = {}
    for r in read_all():
        if r.get("drill") and not include_drills:
            continue
        d = out.setdefault(r["date"], {"target": None, "checkin": None, "late": None, "via": None,
                                       "missed": False, "tattled": False, "steps": 0, "events": []})
        k = r["kind"]
        d["events"].append({"ts": r["ts"][11:19], "kind": k, "action": r.get("action"),
                            "via": r.get("via"), "after_min": r.get("after_min")})
        if k == "wake_start":
            d["target"] = r.get("target")
        elif k == "checkin":
            d["checkin"], d["late"], d["via"] = r["ts"][11:16], r.get("late_min"), r.get("via")
        elif k == "missed":
            d["missed"] = True
        elif k == "escalate":
            d["steps"] += 1
            if r.get("action") == "tattle":
                d["tattled"] = True
    return out


def stats(days: int = 14) -> str:
    """Text table for the last N days: target / check-in / late / tattled / bedtime."""
    recs = read_all()
    by_date: dict[str, dict] = {}
    for r in recs:
        if r.get("drill"):
            continue
        d = by_date.setdefault(r["date"], {})
        k = r["kind"]
        if k == "wake_start":
            d["target"] = r.get("target", "")
        elif k == "checkin":
            d["checkin"] = r["ts"][11:16]
            d["late"] = r.get("late_min")
            d["via"] = r.get("via", "")
        elif k == "missed":
            d["checkin"] = "MISSED"
        elif k == "escalate" and r.get("action") == "tattle":
            d["tattled"] = True
        elif k == "bed_checkin":
            d["bed"] = r["ts"][11:16]
    if not by_date:
        return "No records yet."
    dates = sorted(by_date)[-days:]
    lines = [f"{'date':<12}{'target':<8}{'checkin':<9}{'late':<7}{'tattled':<9}{'bed'}"]
    for dt in dates:
        d = by_date[dt]
        late = d.get("late")
        late_s = "" if late is None else f"+{late:.0f}m"
        lines.append(f"{dt:<12}{d.get('target',''):<8}{d.get('checkin',''):<9}"
                     f"{late_s:<7}{'yes' if d.get('tattled') else '':<9}{d.get('bed','')}")
    return "\n".join(lines)
