"""提醒事项通道（macOS Reminders / EventKit，经 build/reminders 二进制）。

主打卡口：每天在指定列表里新建一条「起床打卡」，用户在 iPhone 上勾掉，
completionDate 就是打卡时间。守护进程只新建、只读；**永远不标记完成、不删除**。
"""
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
            raise RemindersError(f"编译 reminders 帮手失败：{p.stderr[-400:]}")
    return BIN


def _run(*args: str) -> dict:
    p = subprocess.run([str(ensure_built()), *args], capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RemindersError(p.stderr.strip() or f"exit {p.returncode}")
    return json.loads(p.stdout)


def lists() -> list[str]:
    return _run("lists")


def ensure(list_name: str, title: str, date: str, hm: str, tz: str, notes: str = "") -> dict:
    """当天没有就新建。返回 {id, created}。"""
    return _run("ensure", list_name, title, date, hm, tz, notes)


def status(list_name: str, title: str, date: str, tz: str) -> dict:
    """返回 {exists, completed, completion(datetime|None), id}。"""
    r = _run("status", list_name, title, date, tz)
    if r.get("completion"):
        r["completion"] = datetime.fromisoformat(r["completion"].replace("Z", "+00:00"))
    return r
