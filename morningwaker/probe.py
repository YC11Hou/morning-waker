"""探针：判断某一天「有没有打卡」，只读、不发任何消息，stdout 只打印 0 或 1。

依据（任一为真即 1）：
  1. 提醒事项：那天的「起床打卡」已勾（completionDate 不为空）
  2. iMessage：那天起床时刻之后，chat.db 里出现过自己回的关键词（整条精确匹配）
  3. 本机：state/day.json 记录那天 wake_phase == done
细节写到 stderr，方便人看；exit code 与输出一致（0=有打卡，1=无打卡，2=出错）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta

from . import imsg, reminders
from .config import Config, load
from .daemon import DAY_FILE

APPLE_EPOCH = 978307200  # chat.db 的 date 字段：自 2001-01-01 起的纳秒


def _clean(t: str) -> str:
    return re.sub(r"[\s!！。.,，~～]+$", "", t.strip().lower())


def keyword_reply(cfg: Config, date: str) -> datetime | None:
    """那天起床时刻之后，自己回的第一条关键词消息的时间。"""
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
                notes.append("提醒事项：那天没有这条")
            elif r.get("completed") and r.get("completion"):
                hit = True
                notes.append(f"提醒事项：已勾 {r['completion'].astimezone(cfg.tz):%H:%M}")
            else:
                notes.append("提醒事项：存在，未勾")
        except reminders.RemindersError as e:
            notes.append(f"提醒事项：读取失败 {e}")
    try:
        at = keyword_reply(cfg, date)
        if at:
            hit = True
            notes.append(f"iMessage：{at:%H:%M} 回了关键词")
        else:
            notes.append("iMessage：窗口内没有关键词回复")
    except sqlite3.Error as e:
        notes.append(f"iMessage：chat.db 读取失败 {e}")
    try:
        d = json.loads(DAY_FILE.read_text())
        if d.get("date") == date:
            notes.append(f"守护进程：wake_phase={d.get('wake_phase')}")
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
