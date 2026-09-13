"""iMessage send (AppleScript via Messages.app) and receive (read-only chat.db polling)."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile

CHAT_DB = os.path.expanduser("~/Library/Messages/chat.db")

# Three send variants, tried in order; the body is passed as an argv item to keep UTF-8 intact.
_SCRIPTS = [
    '''on run argv
	set msg to item 1 of argv
	tell application "Messages"
		set c to a reference to chat id "iMessage;-;{to}"
		send msg to c
	end tell
end run''',
    '''on run argv
	set msg to item 1 of argv
	tell application "Messages"
		set svc to 1st service whose service type = iMessage
		send msg to participant "{to}" of svc
	end tell
end run''',
    '''on run argv
	set msg to item 1 of argv
	tell application "Messages"
		send msg to buddy "{to}"
	end tell
end run''',
]


def send(body: str, to: str) -> str:
    """Send one iMessage; return the variant that worked or raise RuntimeError."""
    errors = []
    for i, tmpl in enumerate(_SCRIPTS):
        fd, path = tempfile.mkstemp(suffix=".applescript")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(tmpl.format(to=to))
            p = subprocess.run(["osascript", path, body], capture_output=True,
                               text=True, timeout=30)
            if p.returncode == 0:
                return f"v{i + 1}"
            errors.append(f"v{i + 1}: {p.stderr.strip()[:200]}")
        finally:
            os.unlink(path)
    raise RuntimeError("iMessage send failed: " + " | ".join(errors))


def max_rowid() -> int:
    con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COALESCE(MAX(rowid),0) FROM message").fetchone()[0]
    finally:
        con.close()


def fetch_since(rowid: int, limit: int = 100) -> list[tuple[int, str]]:
    """Return (rowid, text) for messages with rowid greater than the given one, ascending."""
    con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=3000")
    try:
        rows = con.execute(
            "SELECT rowid, text FROM message WHERE rowid > ? AND text IS NOT NULL "
            "AND text != '' ORDER BY rowid ASC LIMIT ?", (rowid, limit)).fetchall()
    finally:
        con.close()
    return [(r[0], r[1]) for r in rows]
