# Architecture

A single daemon runs a daily state machine: open the wake session at wake time, collect a check-in from the channels its mode allows, escalate on a configurable ladder until check-in or timeout. Personal data lives only in `config.local.toml`, `private/`, `state/`, and `logs/`.

```
                 ┌────────────────────────────────────────────────┐
                 │  daemon.py  tick every 10 s                    │
                 │                                                │
  config.local ─▶│  idle ──(wake time)──▶ active ──(check-in)──▶ done   │
                 │            │                └──(timeout)──▶ missed   │
                 │            └──(started after window)──▶ skipped     │
                 └───────┬──────────────┬──────────────┬──────────┘
                         │ notify/escalate │ check-in signals │ record
              ┌──────────▼───┐  ┌────────▼─────────┐  ┌──▼──────────┐
              │ imsg.send    │  │ reminder done    │  │ journal.jsonl│
              │ alarm (afplay)│  │ state/checkin   │  │ state/day.json│
              │ tattle → friends│ │                 │  │              │
              └──────────────┘  └──────────────────┘  └─────────────┘
```

## Modules

| File | Role | System dependency |
|---|---|---|
| `config.py` | Parse TOML, reject placeholders, build an immutable `Config` | none |
| `daemon.py` | State machine and main loop; `Day` is today's progress, persisted to `state/day.json` | none |
| `reminders.py` + `helpers/reminders.swift` | Create the day's reminder; read its `completionDate`; list titles due on a day | EventKit (create and read only) |
| `gate.py` + `web/gate.html` | Gate checks, plugin loader, reminder-based waivers, `state/gate.json`, page server on 127.0.0.1 | none |
| `location.py` + `helpers/location.swift` | Build the location helper as an app bundle, read one position, haversine distance | CoreLocation, Location Services |
| `plugins/question_bank.py` | Gate plugin: one question a day, key-idea grading | none |
| `imsg.py` | Send iMessage via AppleScript; read new rows from `chat.db` (bedtime keywords only) | Messages.app, `chat.db` |
| `alarm.py` | Loop a sound on the Mac; set and restore volume; light the screen | afplay, osascript, caffeinate |
| `journal.py` | Event journal, daemon log, `stats` table | none |
| `web.py` + `web/index.html` | Local dashboard on 127.0.0.1 with `/api/days` and `/api/status` | none |
| `cli.py` | Commands `on/off/status/checkin/gate/drill/doctor/test-msg/probe/log/stats/dash/run` | launchctl |
| `probe.py` | Read-only check for a given day; prints `0` or `1` | same as above, read only |

## Check-in channels

In `reminder` mode:

1. **Reminders**: the daemon creates one daily repeating reminder with an alarm at wake time, so the iPhone fires it without the Mac. Completing an occurrence syncs back; the occurrence whose `completionDate` falls on that day is the check-in, and it is checked before a wake session starts so a Mac that slept through wake time never rings for a day already checked in. The helper calls `refreshSourcesIfNecessary()` before reading so a fresh iCloud completion is not missed. The helper treats an open series as covering every later day, so it never creates a second one.
2. **Local file**: `morningwaker checkin` touches `state/checkin`; its mtime must be after the session start.

In `gate` mode the only channel is the gate:

1. The daemon serves `web/gate.html` on 127.0.0.1 and opens it at wake time. Each check passes on its own and is stored in `state/gate.json` with its time; the day is checked in when every non-waived check has passed, stamped with the last pass time.
2. The gate accepts answers from `open_before_min` before wake time until the day is settled, so a gate completed early never rings.
3. A check with `silences_alarm` stops the Mac alarm on pass; escalation continues for the rest. Steps with `unless_passed` are skipped once their checks have passed or are waived.
4. Waivers come from Reminders: a reminder due that day whose title contains a `waive_if_reminder` word and no `waive_exclude` word.
5. Reminder completion and the local file are ignored, except that the file still ends drills.
6. The server binds only to 127.0.0.1 and rejects requests whose Host header is not the loopback address, so neither the phone nor a web page can submit.

## Design decisions

- **Explicit timezone** from `[general].timezone` via `zoneinfo`; the system timezone is ignored.
- **launchd by default** with `KeepAlive`, so the daemon survives reboots and crashes; a daemon that silently exits disables every escalation. `chat.db` is optional, so a missing Full Disk Access grant only disables bedtime keywords. `on --fork` inherits the terminal's permissions instead.
- **Location helper as an app bundle**: macOS does not prompt for location from a bare CLI binary, so the helper is an `LSUIElement` bundle launched with `open -W`; it is rebuilt only when its source changes, because a new binary needs a new grant.
- **Plugins by file path**: gate checks beyond `location` load from any Python file, so private question banks and checks live outside the repository.
- **Sleeping Mac**: the reminder repeats on the phone regardless of the Mac; `pmset repeat wakeorpoweron` wakes the Mac for the alarm and escalation.
- **Restart safety**: `state/day.json` stores phase, fired steps, and the rowid baseline; an active session resumes ringing after a restart.
- **Late start**: the window is `[wake time, wake time + hard_timeout]`; starting after it marks the day skipped instead of ringing.
- **Safe drills**: `drill` scales the ladder, sends tattles to yourself, and logs shell steps instead of running them.
- **No self-triggering**: sent messages also land in `chat.db`; a prefix blacklist plus exact-match bedtime keywords prevent loops.

## State files

```
state/day.json      {"date", "wake_phase", "wake_started", "target", "fired", "rowid_base",
                     "drill_scale", "reminder_ids": {date: id}}
state/gate.json     {"date", "passed": {check: iso time}}
state/checkin       local check-in trigger (mtime)
state/drill         drill trigger (content = scale)
state/daemon.pid
logs/journal.jsonl  events: wake_start / escalate / checkin / missed / bed_checkin
logs/daemon.log     daemon log
```

## Tests

`python3 -m unittest discover -s tests` uses a fake clock, fake iMessage, fake Reminders, and fake alarm. Covered: reminder created ahead, session start, reminder and local check-ins, no self-triggering, tattle timing, timeout, late start, restart resume. `tests/test_gate.py` runs the real question plugin and location check with a fake position: reminder ignored, alarm silenced, last-pass check-in time, wrong answers, distance, `unless_passed`, waivers and exclusions, early pass, closed window, Host check.
