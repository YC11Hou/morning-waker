"""iMessage 收发（macOS Messages.app + 只读 chat.db）。

发送：AppleScript，正文走 osascript 的 argv（UTF-8 干净，中文/emoji 不乱码，
      也不用管引号转义）。三个变体按可靠性降级。
接收：只读打开 ~/Library/Messages/chat.db，按 rowid 单调递增取新消息。
      「自发自收」通道里自己发的消息也会落库，方向靠内容判断，不靠 is_from_me。

需要的系统权限：自动化(终端→Messages)、完全磁盘访问(读 chat.db)。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile

CHAT_DB = os.path.expanduser("~/Library/Messages/chat.db")

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
    """发一条 iMessage。成功返回用的变体名；三个都失败抛 RuntimeError。"""
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
    raise RuntimeError("iMessage 发送失败：" + " | ".join(errors))


def max_rowid() -> int:
    con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COALESCE(MAX(rowid),0) FROM message").fetchone()[0]
    finally:
        con.close()


def fetch_since(rowid: int, limit: int = 100) -> list[tuple[int, str]]:
    """返回 (rowid, text)，rowid > 给定值、有正文的消息，升序。"""
    con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=3000")
    try:
        rows = con.execute(
            "SELECT rowid, text FROM message WHERE rowid > ? AND text IS NOT NULL "
            "AND text != '' ORDER BY rowid ASC LIMIT ?", (rowid, limit)).fetchall()
    finally:
        con.close()
    return [(r[0], r[1]) for r in rows]
