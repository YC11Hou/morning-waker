"""Read-only probe: did a check-in happen on a given day? Prints 1 or 0, sends nothing."""
from __future__ import annotations

import json
import sys
from datetime import datetime

from . import reminders
from .config import Config, load
from .daemon import DAY_FILE


def probe(cfg: Config, date: str) -> tuple[bool, list[str]]:
    notes: list[str] = []
    hit = False
    if cfg.rem_enabled and cfg.checkin_mode == "reminder":
        try:
            r = reminders.status(cfg.rem_list, cfg.rem_title, date, cfg.tz.key)
            if not r.get("exists"):
                notes.append("reminder: none for that day")
            elif r.get("completed") and r.get("completion"):
                hit = True
                notes.append(f"reminder: completed at {r['completion'].astimezone(cfg.tz):%H:%M}")
            else:
                notes.append("reminder: exists, not completed")
        except reminders.RemindersError as e:
            notes.append(f"reminder: read failed {e}")
    try:
        d = json.loads(DAY_FILE.read_text())
        if d.get("date") == date:
            notes.append(f"daemon: wake_phase={d.get('wake_phase')}")
            if d.get("wake_phase") == "done":
                hit = True
    except (OSError, ValueError):
        pass
    return hit, notes


def main(argv: list[str]) -> int:
    cfg = load()
    date = argv[0] if argv else datetime.now(cfg.tz).strftime("%Y-%m-%d")
    hit, notes = probe(cfg, date)
    for n in notes:
        print(f"  {date} {n}", file=sys.stderr)
    print(1 if hit else 0)
    return 0 if hit else 1
