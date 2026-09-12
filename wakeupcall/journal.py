"""打卡日志：logs/journal.jsonl，一行一个事件。个人数据，已 .gitignore。"""
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
    if os.environ.get("WAKEUPCALL_DAEMON") == "1":   # launchd 已把 stdout 接到同一个文件
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


def stats(days: int = 14) -> str:
    """最近 N 天一张表：目标 / 打卡 / 迟到分钟 / 告状 / 上床时间。"""
    recs = read_all()
    by_date: dict[str, dict] = {}
    for r in recs:
        if r.get("drill"):
            continue                                   # 演习不进统计
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
        return "还没有记录。"
    dates = sorted(by_date)[-days:]
    lines = [f"{'日期':<12}{'目标':<7}{'打卡':<8}{'迟到':<7}{'告状':<5}{'上床'}"]
    for dt in dates:
        d = by_date[dt]
        late = d.get("late")
        late_s = "" if late is None else f"+{late:.0f}m"
        lines.append(f"{dt:<12}{d.get('target',''):<7}{d.get('checkin',''):<8}"
                     f"{late_s:<7}{'是' if d.get('tattled') else '':<5}{d.get('bed','')}")
    return "\n".join(lines)
