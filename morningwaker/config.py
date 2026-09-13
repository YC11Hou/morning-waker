"""Load config.local.toml; all personal data comes from there, the code has no defaults for it."""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("MORNINGWAKER_CONFIG", ROOT / "config.local.toml"))
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"
PLACEHOLDER_SELF = "+10000000000"
PLACEHOLDER_FRIEND = "+10000000001"


@dataclass
class Step:
    after_min: float
    action: str
    text: str = ""
    to: list[str] = field(default_factory=list)
    cmd: str = ""


@dataclass
class Config:
    tz: ZoneInfo
    self_number: str
    wake_time: tuple[int, int]
    weekdays: set[int]
    wake_keywords: list[str]
    hard_timeout_min: float
    bed_enabled: bool
    bed_time: tuple[int, int]
    bed_keywords: list[str]
    alarm_enabled: bool
    alarm_sound: str
    alarm_volume: int
    alarm_interval: float
    steps: list[Step]
    tattle_name: str
    rem_enabled: bool
    rem_list: str
    rem_title: str
    rem_ahead_min: float


def _hm(s: str) -> tuple[int, int]:
    h, m = s.strip().split(":")
    return int(h), int(m)


def load(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        sys.exit(f"Missing config: {path}\nRun: cp config.example.toml config.local.toml")
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    self_number = raw.get("imessage", {}).get("self", "").strip()
    if not self_number or self_number == PLACEHOLDER_SELF:
        sys.exit("[imessage].self in config.local.toml is still the placeholder; set your own number.")
    wake, bed, alarm = raw.get("wake", {}), raw.get("bedtime", {}), raw.get("alarm", {})
    rem = raw.get("reminders", {})
    steps = [Step(after_min=float(s.get("after_min", 0)), action=s.get("action", "notify"),
                  text=s.get("text", ""), to=list(s.get("to", [])), cmd=s.get("cmd", ""))
             for s in raw.get("escalation", [])]
    steps.sort(key=lambda s: s.after_min)
    for s in steps:
        if s.action == "tattle" and PLACEHOLDER_FRIEND in s.to:
            sys.exit("tattle.to still contains the placeholder number; set real numbers or remove the step.")
    return Config(
        tz=ZoneInfo(raw.get("general", {}).get("timezone", "UTC")),
        self_number=self_number,
        wake_time=_hm(wake.get("time", "09:00")),
        weekdays=set(wake.get("weekdays", [1, 2, 3, 4, 5, 6, 7])),
        wake_keywords=[k.lower() for k in wake.get("keywords", ["up"])],
        hard_timeout_min=float(wake.get("hard_timeout_min", 60)),
        bed_enabled=bool(bed.get("enabled", False)),
        bed_time=_hm(bed.get("remind_at", "23:30")),
        bed_keywords=[k.lower() for k in bed.get("keywords", [])],
        alarm_enabled=bool(alarm.get("enabled", True)),
        alarm_sound=alarm.get("sound", "/System/Library/Sounds/Sosumi.aiff"),
        alarm_volume=int(alarm.get("volume", 80)),
        alarm_interval=float(alarm.get("interval_sec", 2)),
        steps=steps,
        tattle_name=raw.get("tattle", {}).get("name", "me"),
        rem_enabled=bool(rem.get("enabled", False)),
        rem_list=rem.get("list", "Reminders"),
        rem_title=rem.get("title", "Wake-up check-in"),
        rem_ahead_min=float(rem.get("create_ahead_min", 60)),
    )
