"""Local Mac alarm: loop a tone with a gentle ramp on the built-in speakers, pin the volume, keep the Mac awake, stop after a cap."""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT

SRC = ROOT / "helpers" / "audio.swift"
BIN = ROOT / "build" / "audio"
FALLBACK_SOUND = "/System/Library/Sounds/Sosumi.aiff"


@dataclass
class AlarmSettings:
    sound: str
    volume: int = 80
    interval: float = 1.0
    ramp_sec: float = 60.0
    start_level: float = 0.35
    end_level: float = 1.0
    max_ring_min: float = 30.0
    builtin_speakers: bool = True
    pin_sec: float = 2.0


def _osascript(script: str) -> str:
    p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    return p.stdout.strip()


def _get_volume() -> tuple[int, bool]:
    try:
        return int(_osascript("output volume of (get volume settings)")), \
            _osascript("output muted of (get volume settings)") == "true"
    except (ValueError, subprocess.SubprocessError, OSError):
        return 50, False


def _set_volume(v: int, muted: bool = False) -> None:
    try:
        _osascript(f"set volume output volume {v} {'with' if muted else 'without'} output muted")
    except (subprocess.SubprocessError, OSError):
        pass


def ensure_helper() -> Path | None:
    """Build the CoreAudio helper if needed; None when swiftc is unavailable, so ringing never depends on it."""
    try:
        if not BIN.exists() or BIN.stat().st_mtime < SRC.stat().st_mtime:
            BIN.parent.mkdir(exist_ok=True)
            p = subprocess.run(["swiftc", "-O", str(SRC), "-o", str(BIN)], capture_output=True, timeout=300)
            if p.returncode != 0:
                return None
        return BIN
    except (OSError, subprocess.SubprocessError):
        return None


def _audio(*args: str) -> str | None:
    helper = ensure_helper()
    if not helper:
        return None
    try:
        p = subprocess.run([str(helper), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def wake_screen(seconds: int = 30) -> None:
    """Simulate user activity so the screen lights up; stays on the lock screen."""
    subprocess.Popen(["caffeinate", "-u", "-t", str(seconds)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def level_at(elapsed: float, s: AlarmSettings) -> float:
    """afplay gain for a loop starting `elapsed` seconds into ringing: linear from start_level to end_level over ramp_sec."""
    if s.ramp_sec <= 0 or elapsed >= s.ramp_sec:
        return s.end_level
    return round(s.start_level + (s.end_level - s.start_level) * elapsed / s.ramp_sec, 2)


class Alarm:
    def __init__(self, settings: AlarmSettings):
        self.s = settings
        self.sound = settings.sound if Path(settings.sound).exists() else FALLBACK_SOUND
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._saved: tuple[int, bool] | None = None
        self._saved_device: str | None = None
        self._player: subprocess.Popen | None = None
        self._awake: subprocess.Popen | None = None
        self.expired = False

    def plan(self) -> dict:
        """What start() would do, without doing any of it."""
        s = self.s
        return {"sound": self.sound, "volume": s.volume, "builtin_speakers": s.builtin_speakers,
                "ramp": [level_at(t, s) for t in range(0, int(s.ramp_sec) + 1, 15)] or [s.end_level],
                "max_ring_min": s.max_ring_min, "helper": str(ensure_helper() or "unavailable")}

    def _pin(self) -> None:
        if self.s.builtin_speakers:
            _audio("builtin")
        _set_volume(self.s.volume)

    def start(self) -> None:
        if self.ringing or self.expired:
            return
        self._saved = _get_volume()
        if self.s.builtin_speakers:
            self._saved_device = _audio("current")
        self._pin()
        self._stop.clear()
        self._awake = subprocess.Popen(["caffeinate", "-dims", "-t", str(int(self.s.max_ring_min * 60) + 60)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._threads = [threading.Thread(target=self._loop, daemon=True),
                         threading.Thread(target=self._pin_loop, daemon=True)]
        for t in self._threads:
            t.start()

    def _pin_loop(self) -> None:
        while not self._stop.wait(self.s.pin_sec):
            self._pin()

    def _loop(self) -> None:
        began = time.monotonic()
        while not self._stop.is_set():
            elapsed = time.monotonic() - began
            if elapsed >= self.s.max_ring_min * 60:
                self.expired = True
                self._stop.set()
                self._join()
                self._restore()
                return
            try:
                self._player = subprocess.Popen(["afplay", "-v", str(level_at(elapsed, self.s)), self.sound],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._player.wait()
            except OSError:
                pass
            self._stop.wait(self.s.interval)

    def _restore(self) -> None:
        for p in (self._player, self._awake):
            if p and p.poll() is None:
                p.terminate()
        if self._saved_device:
            _audio("set", self._saved_device)
            self._saved_device = None
        if self._saved is not None:
            _set_volume(*self._saved)
            self._saved = None

    def _join(self) -> None:
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=5)

    def stop(self) -> None:
        self._stop.set()
        if self._player and self._player.poll() is None:
            self._player.terminate()
        self._join()
        self._restore()
        self.expired = False

    @property
    def ringing(self) -> bool:
        return any(t.is_alive() for t in self._threads)
