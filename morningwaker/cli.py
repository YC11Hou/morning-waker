"""Command-line entry point for morningwaker."""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import journal
from .config import CONFIG_PATH, ROOT, STATE_DIR, load
from .daemon import CHECKIN_FILE, DAY_FILE, DRILL_FILE

PIDFILE = STATE_DIR / "daemon.pid"

LABEL = f"com.{getpass.getuser()}.morningwaker"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
PY = sys.executable

USAGE = """usage: morningwaker <command>
  on            start the daemon in the background (forked from this terminal, inherits its permissions)
  on --launchd  run under launchd instead (starts at login; Python itself needs Full Disk Access)
  off           stop the daemon (either mode)
  status        daemon state and today's progress
  checkin       check in from this Mac
  drill [scale] run the whole escalation now; scale shrinks the minutes (default 0.1); tattles go to you
  test-msg      send yourself a test iMessage
  probe [date]  read-only: was there a check-in that day? prints 1 or 0 (default today)
  doctor        check config, chat.db access, Reminders list, scheduled wake, sleep assertions, daemon
  log           follow the daemon log
  stats [days]  check-in table for the last N days (default 14)
  dash [port]   open the local dashboard (default 5180)
  run           run the daemon in the foreground"""


def _plist() -> str:
    tmpl = (ROOT / "launchd" / "morningwaker.plist.template").read_text()
    return (tmpl.replace("{{LABEL}}", LABEL).replace("{{PYTHON}}", PY)
                .replace("{{ROOT}}", str(ROOT)).replace("{{LOG}}", str(journal.DAEMON_LOG)))


def _loaded() -> bool:
    p = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    return LABEL in p.stdout


def _pid() -> int | None:
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def cmd_on(launchd: bool = False) -> int:
    # Validate the config first so placeholder values are rejected before anything starts.
    load()
    journal.LOG_DIR.mkdir(exist_ok=True)
    STATE_DIR.mkdir(exist_ok=True)
    if not launchd:
        if pid := _pid():
            print(f"Already running (pid {pid})")
            return 0
        env = {**os.environ, "PYTHONPATH": str(ROOT), "MORNINGWAKER_DAEMON": "1"}
        with open(journal.DAEMON_LOG, "a") as out:
            p = subprocess.Popen([PY, "-m", "morningwaker.daemon"], cwd=ROOT, env=env,
                                 stdout=out, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
        PIDFILE.write_text(str(p.pid))
        time.sleep(2)
        if _pid():
            print(f"MorningWaker started (pid {p.pid}). Logs: morningwaker log / status: morningwaker status")
            return 0
        print("Failed to start; see morningwaker log", file=sys.stderr)
        return 1
    if _loaded():
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    PLIST.write_text(_plist())
    subprocess.run(["launchctl", "load", str(PLIST)], check=True)
    time.sleep(1.5)
    print(f"MorningWaker started under launchd ({LABEL}). Logs: morningwaker log / status: morningwaker status")
    return 0


def cmd_off() -> int:
    if pid := _pid():
        os.kill(pid, 15)
        time.sleep(0.5)
    PIDFILE.unlink(missing_ok=True)
    if PLIST.exists():
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        PLIST.unlink()
    print("MorningWaker stopped")
    return 0


def cmd_status() -> int:
    cfg = load()
    now = datetime.now(cfg.tz)
    if pid := _pid():
        print(f"daemon: running (pid {pid})")
    elif _loaded():
        print(f"daemon: running under launchd ({LABEL})")
    else:
        print("daemon: not running")
    print(f"config: {CONFIG_PATH}")
    print(f"wake {'%02d:%02d' % cfg.wake_time}  bedtime "
          f"{'%02d:%02d' % cfg.bed_time if cfg.bed_enabled else 'off'}  tz {cfg.tz.key}"
          f"  now {now:%Y-%m-%d %H:%M}")
    print("escalation: " + "  ".join(f"{s.after_min:g}m->{s.action}" for s in cfg.steps))
    if DAY_FILE.exists():
        import json
        d = json.loads(DAY_FILE.read_text())
        print(f"today ({d.get('date')}): wake {d.get('wake_phase')}"
              f"  fired {d.get('fired')}  bed {d.get('bed_checkin') or '-'}")
    return 0


def cmd_checkin() -> int:
    STATE_DIR.mkdir(exist_ok=True)
    CHECKIN_FILE.touch()
    print("Checked in locally; the daemon confirms within 10 s and stops the alarm")
    return 0


def cmd_drill(scale: str = "0.1") -> int:
    if not (_pid() or _loaded()):
        print("Daemon is not running; run morningwaker on first", file=sys.stderr)
        return 1
    STATE_DIR.mkdir(exist_ok=True)
    DRILL_FILE.write_text(scale)
    print(f"Drill triggered (scale {scale}). Run morningwaker checkin to end it.")
    return 0


def cmd_test_msg() -> int:
    from . import imsg
    cfg = load()
    v = imsg.send(f"🎯 MorningWaker channel test {datetime.now(cfg.tz):%H:%M}", cfg.self_number)
    print(f"Sent (variant {v}); check your phone for the banner.")
    return 0


def cmd_doctor() -> int:
    from . import imsg, reminders
    ok_all = True

    def row(ok: bool, name: str, detail: str = "") -> None:
        nonlocal ok_all
        ok_all &= ok
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))

    print("MorningWaker doctor")
    try:
        cfg = load()
        row(True, "config", f"{CONFIG_PATH.name}, wake {'%02d:%02d' % cfg.wake_time} {cfg.tz.key}")
    except SystemExit as e:
        row(False, "config", str(e))
        return 1
    try:
        row(True, "chat.db readable", f"latest rowid {imsg.max_rowid()}")
    except Exception as e:  # noqa: BLE001
        row(False, "chat.db readable", f"{e} (grant Full Disk Access to your terminal)")
    if cfg.rem_enabled:
        try:
            ls = reminders.lists()
            row(cfg.rem_list in ls, "Reminders list", f"'{cfg.rem_list}' " +
                ("exists" if cfg.rem_list in ls else f"not found; available: {ls}"))
        except Exception as e:  # noqa: BLE001
            row(False, "Reminders access", str(e))
    sched = subprocess.run(["pmset", "-g", "sched"], capture_output=True, text=True).stdout
    row("wakepoweron" in sched or "wake at" in sched, "scheduled wake (pmset)",
        next((l.strip() for l in sched.splitlines() if "wake" in l), "none; the Mac will not wake from sleep"))
    asr = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True).stdout
    row("PreventSystemSleep" in asr or "PreventUserIdleSystemSleep" in asr, "sleep prevention",
        "held by a process" if "Prevent" in asr else "none (use caffeinate -ism or pmset repeat)")
    row(bool(_pid() or _loaded()), "daemon", f"pid {_pid()}" if _pid() else "not running")
    tattle = [s for s in cfg.steps if s.action == "tattle"]
    row(all(s.to for s in tattle), "tattle recipients",
        "set" if all(s.to for s in tattle) else "tattle.to is empty; that step sends to nobody")
    print("  result: " + ("all good" if ok_all else "some checks failed"))
    return 0 if ok_all else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd == "on":
        return cmd_on(launchd="--launchd" in argv)
    if cmd == "off":
        return cmd_off()
    if cmd == "status":
        return cmd_status()
    if cmd == "checkin":
        return cmd_checkin()
    if cmd == "drill":
        return cmd_drill(argv[1] if len(argv) > 1 else "0.1")
    if cmd == "test-msg":
        return cmd_test_msg()
    if cmd == "doctor":
        return cmd_doctor()
    if cmd == "probe":
        from .probe import main as probe_main
        return probe_main(argv[1:])
    if cmd == "log":
        return subprocess.call(["tail", "-n", "30", "-f", str(journal.DAEMON_LOG)])
    if cmd == "stats":
        print(journal.stats(int(argv[1]) if len(argv) > 1 else 14))
        return 0
    if cmd == "dash":
        from .web import serve
        port = int(argv[1]) if len(argv) > 1 else 5180
        subprocess.Popen(["sh", "-c", f"sleep 0.8 && open http://127.0.0.1:{port}"])
        serve(port)
        return 0
    if cmd == "run":
        from .daemon import main as run
        run()
        return 0
    print(USAGE)
    return 64
