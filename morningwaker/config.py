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
DEFAULT_SOUND = "/System/Library/PrivateFrameworks/ToneLibrary.framework/Versions/A/Resources/Ringtones/Radial-EncoreInfinitum.m4r"


@dataclass
class Step:
    after_min: float
    action: str
    text: str = ""
    to: list[str] = field(default_factory=list)
    cmd: str = ""
    unless_passed: list[str] = field(default_factory=list)


@dataclass
class Config:
    tz: ZoneInfo
    self_number: str
    wake_time: tuple[int, int]
    weekdays: set[int]
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
    checkin_mode: str = "reminder"
    gate_port: int = 5181
    gate_open_before_min: float = 60
    gate_checks: list[dict] = field(default_factory=list)
    alarm_ramp_sec: float = 60
    alarm_start_level: float = 0.35
    alarm_end_level: float = 1.0
    alarm_max_ring_min: float = 30
    alarm_builtin: bool = True


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
                  text=s.get("text", ""), to=list(s.get("to", [])), cmd=s.get("cmd", ""),
                  unless_passed=list(s.get("unless_passed", [])))
             for s in raw.get("escalation", [])]
    tattle = raw.get("tattle", {})
    if not tattle.get("enabled", True):
        steps = [s for s in steps if s.action != "tattle"]
    steps.sort(key=lambda s: s.after_min)
    for s in steps:
        if s.action == "tattle" and PLACEHOLDER_FRIEND in s.to:
            sys.exit("tattle.to still contains the placeholder number; set real numbers or remove the step.")
    mode = raw.get("checkin", {}).get("mode", "reminder")
    gate = raw.get("gate", {})
    if mode not in ("reminder", "gate"):
        sys.exit(f"[checkin].mode must be 'reminder' or 'gate', got {mode!r}")
    if mode == "gate" and not gate.get("check"):
        sys.exit("[checkin].mode is 'gate' but no [[gate.check]] is configured.")
    return Config(
        tz=ZoneInfo(raw.get("general", {}).get("timezone", "UTC")),
        self_number=self_number,
        wake_time=_hm(wake.get("time", "09:00")),
        weekdays=set(wake.get("weekdays", [1, 2, 3, 4, 5, 6, 7])),
        hard_timeout_min=float(wake.get("hard_timeout_min", 60)),
        bed_enabled=bool(bed.get("enabled", False)),
        bed_time=_hm(bed.get("remind_at", "23:30")),
        bed_keywords=[k.lower() for k in bed.get("keywords", [])],
        alarm_enabled=bool(alarm.get("enabled", True)),
        alarm_sound=alarm.get("sound", DEFAULT_SOUND),
        alarm_volume=max(0, min(100, int(alarm.get("volume", 80)))),
        alarm_interval=float(alarm.get("interval_sec", 1)),
        alarm_ramp_sec=float(alarm.get("ramp_sec", 60)),
        alarm_start_level=max(0.05, min(1.0, float(alarm.get("start_level", 0.35)))),
        alarm_end_level=max(0.05, min(1.0, float(alarm.get("end_level", 1.0)))),
        alarm_max_ring_min=float(alarm.get("max_ring_min", 30)),
        alarm_builtin=bool(alarm.get("builtin_speakers", True)),
        steps=steps,
        tattle_name=tattle.get("name", "me"),
        rem_enabled=bool(rem.get("enabled", False)),
        rem_list=rem.get("list", "Reminders"),
        rem_title=rem.get("title", "Wake-up check-in"),
        rem_ahead_min=float(rem.get("create_ahead_min", 60)),
        checkin_mode=mode,
        gate_port=int(gate.get("port", 5181)),
        gate_open_before_min=float(gate.get("open_before_min", 60)),
        gate_checks=[dict(c) for c in gate.get("check", [])],
    )
