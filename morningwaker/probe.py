"""Read-only probe: did a check-in happen on a given day? Prints 1 or 0, sends nothing."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta

from . import imsg, reminders
from .config import Config, load
from .daemon import DAY_FILE

# chat.db stores dates as nanoseconds since 2001-01-01.
APPLE_EPOCH = 978307200


def _clean(t: str) -> str:
    return re.sub(r"[\s!\uff01\u3002.,\uff0c~\uff5e]+$", "", t.strip().lower())


def keyword_reply(cfg: Config, date: str) -> datetime | None:
    """Time of the first keyword reply inside that day's wake window, if any."""
    h, m = cfg.wake_time
    start = datetime.strptime(date, "%Y-%m-%d").replace(hour=h, minute=m, tzinfo=cfg.tz)
    end = start + timedelta(minutes=cfg.hard_timeout_min)
    lo = (start.timestamp() - APPLE_EPOCH) * 1e9
    hi = (end.timestamp() - APPLE_EPOCH) * 1e9
    con = sqlite3.connect(f"file:{imsg.CHAT_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT date, text FROM message WHERE date BETWEEN ? AND ? AND text IS NOT NULL "
            "ORDER BY date ASC", (lo, hi)).fetchall()
    finally:
        con.close()
    for d, text in rows:
        if _clean(text) in cfg.wake_keywords:
            return datetime.fromtimestamp(d / 1e9 + APPLE_EPOCH, cfg.tz)
    return None


def probe(cfg: Config, date: str) -> tuple[bool, list[str]]:
    notes: list[str] = []
    hit = False
    if cfg.rem_enabled:
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
        at = keyword_reply(cfg, date)
        if at:
            hit = True
            notes.append(f"iMessage: keyword reply at {at:%H:%M}")
        else:
            notes.append("iMessage: no keyword reply in the window")
    except sqlite3.Error as e:
        notes.append(f"iMessage: chat.db read failed {e}")
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
