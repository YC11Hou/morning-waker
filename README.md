# MorningWaker

A macOS daemon that makes you check in after your alarm. If you do not, it escalates: your Mac rings, your phone gets nudged, and finally your friends get a message saying you are still asleep.

## How it works

```
23:30  🌙 bedtime reminder (optional)
09:00  ⏰ Mac alarm loops + iMessage to your phone
09:05  ⏰ second nudge
09:10  ⏰ last warning
09:15  📣 iMessage to your friends
       ...until you check in: alarm stops, lateness is recorded, confirmation is sent
```

Times, texts, and actions are all in `config.local.toml`. Actions: `notify` (message yourself), `tattle` (message a list of numbers), `shell` (run a command).

## Check-in channels

Any one of these counts, only after the wake session has started:

1. **Reminders**: a reminder is created in a Reminders list ahead of time and fires on your iPhone locally. Completing it is the check-in; its completion time is the check-in time. MorningWaker never completes or deletes reminders.
2. **iMessage**: reply to your own number with a keyword (`up` by default).
3. **Mac**: `morningwaker checkin`.

## Requirements

- macOS 14+, Python 3.11+, Xcode Command Line Tools. No third-party packages.
- Messages.app signed in with an Apple ID whose iMessage address is your phone number.
- Terminal permissions: Automation for Messages, Full Disk Access (read-only access to `chat.db`), Reminders.
- The Mac must be awake at wake time. See [SETUP.md](SETUP.md).

## Install

```bash
git clone https://github.com/YC11Hou/morning-waker.git && cd morning-waker
cp config.example.toml config.local.toml   # set your number, friends, times
bin/morningwaker test-msg                   # verify the iMessage channel
bin/morningwaker doctor                     # verify permissions
bin/morningwaker on                         # start the daemon
bin/morningwaker drill                      # dry run at 10x speed; tattles go to you
```

## Commands

| Command | Description |
|---|---|
| `on` / `off` | Start or stop the daemon. `on --launchd` uses launchd (Python itself needs Full Disk Access). |
| `status` | Daemon state, today's progress, escalation steps. |
| `checkin` | Check in from the Mac. |
| `drill [scale]` | Run the full escalation now with minutes scaled (default 0.1). |
| `doctor` | Check config, permissions, Reminders list, scheduled wake, sleep prevention. |
| `test-msg` | Send yourself a test iMessage. |
| `probe [date]` | Read-only: print `1` if that day has a check-in, else `0`. |
| `log` | Follow the daemon log. |
| `stats [days]` | Check-in table for the last N days. |
| `dash [port]` | Local dashboard at http://127.0.0.1:5180. |
| `run` | Run the daemon in the foreground. |

## Layout

```
morningwaker/        config, imsg, reminders, alarm, journal, daemon, cli, web, probe
web/                 dashboard page, no build step
helpers/             reminders.swift, compiled to build/ on first use
tests/               python3 -m unittest discover -s tests
launchd/             plist template
config.example.toml  the only file you need to edit (copy to config.local.toml)
```

`config.local.toml`, `state/`, and `logs/` hold personal data and are gitignored. The code has no default numbers or paths. Design notes: [ARCHITECTURE.md](ARCHITECTURE.md).

## License

MIT
