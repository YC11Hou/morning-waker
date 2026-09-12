"""Mac 本机响铃：afplay 循环播放，直到 stop()。响铃前拉音量、停铃后还原。"""
from __future__ import annotations

import subprocess
import threading


def _get_volume() -> int:
    p = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"],
                       capture_output=True, text=True)
    try:
        return int(p.stdout.strip())
    except ValueError:
        return 50


def _set_volume(v: int) -> None:
    subprocess.run(["osascript", "-e", f"set volume output volume {v}"],
                   capture_output=True)


def wake_screen(seconds: int = 30) -> None:
    """caffeinate -u = 模拟一次用户活动，屏幕点亮但停在锁屏，不绕过密码。"""
    subprocess.Popen(["caffeinate", "-u", "-t", str(seconds)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Alarm:
    def __init__(self, sound: str, volume: int, interval: float):
        self.sound, self.volume, self.interval = sound, volume, interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._saved_volume: int | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._saved_volume = _get_volume()
        _set_volume(self.volume)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            subprocess.run(["afplay", self.sound], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self._saved_volume is not None:
            _set_volume(self._saved_volume)
            self._saved_volume = None

    @property
    def ringing(self) -> bool:
        return bool(self._thread and self._thread.is_alive())
