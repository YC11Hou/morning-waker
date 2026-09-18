# Architecture

A single daemon runs a daily state machine: open the wake session at wake time, collect a check-in from one of three channels, escalate on a configurable ladder until check-in or timeout. Personal data lives only in `config.local.toml`, `state/`, and `logs/`.

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
| `reminders.py` + `helpers/reminders.swift` | Create the day's reminder; read its `completionDate` | EventKit (create and read only) |
| `imsg.py` | Send iMessage via AppleScript; read new rows from `chat.db` (bedtime keywords only) | Messages.app, `chat.db` |
| `alarm.py` | Loop a sound on the Mac; set and restore volume; light the screen | afplay, osascript, caffeinate |
| `journal.py` | Event journal, daemon log, `stats` table | none |
| `web.py` + `web/index.html` | Local dashboard on 127.0.0.1 with `/api/days` and `/api/status` | none |
| `cli.py` | Commands `on/off/status/checkin/drill/doctor/test-msg/probe/log/stats/dash/run` | launchctl (optional) |
| `probe.py` | Read-only check for a given day; prints `0` or `1` | same as above, read only |

## Check-in channels, in priority order

1. **Reminders**: the daemon creates one daily repeating reminder with an alarm at wake time, so the iPhone fires it without the Mac. Completing an occurrence syncs back; the occurrence whose `completionDate` falls on that day is the check-in, and it is checked before a wake session starts so a Mac that slept through wake time never rings for a day already checked in. The helper calls `refreshSourcesIfNecessary()` before reading so a fresh iCloud completion is not missed. The helper treats an open series as covering every later day, so it never creates a second one.
2. **Local file**: `morningwaker checkin` touches `state/checkin`; its mtime must be after the session start.

## Design decisions

- **Explicit timezone** from `[general].timezone` via `zoneinfo`; the system timezone is ignored.
- **Fork from the terminal, not launchd**, so the daemon inherits the terminal's Full Disk Access and Automation permissions. Cost: rerun `on` after a reboot. `on --launchd` is available for users who grant Python those permissions.
- **Sleeping Mac**: the reminder repeats on the phone regardless of the Mac; `pmset repeat wakeorpoweron` wakes the Mac for the alarm and escalation.
- **Restart safety**: `state/day.json` stores phase, fired steps, and the rowid baseline; an active session resumes ringing after a restart.
- **Late start**: the window is `[wake time, wake time + hard_timeout]`; starting after it marks the day skipped instead of ringing.
- **Safe drills**: `drill` scales the ladder, sends tattles to yourself, and logs shell steps instead of running them.
- **No self-triggering**: sent messages also land in `chat.db`; a prefix blacklist plus exact-match bedtime keywords prevent loops.

## State files

```
state/day.json      {"date", "wake_phase", "wake_started", "target", "fired", "rowid_base",
                     "drill_scale", "reminder_ids": {date: id}}
state/checkin       local check-in trigger (mtime)
state/drill         drill trigger (content = scale)
state/daemon.pid
logs/journal.jsonl  events: wake_start / escalate / checkin / missed / bed_checkin
logs/daemon.log     daemon log
```

## Tests

`python3 -m unittest discover -s tests` uses a fake clock, fake iMessage, fake Reminders, and fake alarm. Covered: reminder created ahead, session start, all three check-in channels, no self-triggering, tattle timing, timeout, late start, restart resume.
