"""Mac position via a CoreLocation helper app bundle; launched with `open` so macOS can grant it Location access."""
from __future__ import annotations

import json
import math
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import ROOT

SRC = ROOT / "helpers" / "location.swift"
PLIST = ROOT / "helpers" / "location.plist"
APP = ROOT / "build" / "MorningWakerLocation.app"
EXE = APP / "Contents" / "MacOS" / "location"


class LocationError(RuntimeError):
    pass


def ensure_built() -> Path:
    # Rebuild only when the source changes: a new binary has a new code hash and needs Location access again.
    if EXE.exists() and EXE.stat().st_mtime >= max(SRC.stat().st_mtime, PLIST.stat().st_mtime):
        return APP
    shutil.rmtree(APP, ignore_errors=True)
    EXE.parent.mkdir(parents=True)
    info = plistlib.loads(PLIST.read_bytes())
    info.update(CFBundleExecutable="location", CFBundlePackageType="APPL", LSUIElement=True)
    (APP / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    p = subprocess.run(["swiftc", "-O", str(SRC), "-o", str(EXE)], capture_output=True, text=True)
    if p.returncode != 0:
        raise LocationError(f"failed to compile location helper: {p.stderr[-400:]}")
    subprocess.run(["codesign", "-s", "-", "-f", str(APP)], capture_output=True)
    return APP


def current(timeout: float = 20) -> dict:
    """Return {lat, lon, acc, age} for the Mac's position or raise LocationError."""
    app = ensure_built()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        out.touch()
        subprocess.run(["open", "-n", "-W", "--stdout", str(out), "--stderr", str(out), str(app),
                        "--args", str(int(timeout))], capture_output=True, timeout=timeout + 15)
        text = out.read_text().strip()
    try:
        return json.loads(text.splitlines()[-1])
    except (ValueError, IndexError):
        raise LocationError(text or "location helper produced no output") from None


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))
