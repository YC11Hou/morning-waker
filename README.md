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

Times, texts, and actions are all in `config.local.toml`. Actions: `notify` (message yourself), `tattle` (message a list of numbers; `[tattle] enabled = false` turns every tattle off), `shell` (run a command).

## Check-in modes

`[checkin].mode` picks what counts as a check-in.

1. **`reminder`** (default): a daily repeating reminder fires on your iPhone; completing the day's occurrence, or running `morningwaker checkin`, is the check-in. Its completion time is the check-in time, even if it was completed before the Mac woke up.
2. **`gate`**: only the gate page on the Mac counts (`http://127.0.0.1:5181/`, opened at wake time, unreachable from the phone). Every configured check must pass; the reminder becomes a plain alarm.

MorningWaker never completes or deletes reminders. iMessage is used only for outgoing notifications and tattles; replies are not read as check-ins.

## Gate checks

| Type | Passes when | Options |
|---|---|---|
| `location` | the Mac is within `radius_m` of `lat`/`lon` (CoreLocation) | `radius_m`, `max_accuracy_m` |
| `plugin` | the plugin's `verify()` accepts the answer | `module`, plus any options the plugin reads |

Any check takes `silences_alarm = true` (its pass stops the Mac alarm before the full check-in) and `waive_if_reminder = [words]` / `waive_exclude = [words]` (skip it on days with a matching reminder due in the Reminders list). An escalation step with `unless_passed = [names]` is skipped once those checks have passed or are waived.

`plugins/question_bank.py` asks one question a day from a TOML bank and passes when the answer misses at most `allowed_missing` key ideas (default 1), naming the missing ones otherwise; see `plugins/questions.example.toml`. A plugin is any Python file with `make(name, options)` returning a `morningwaker.gate.Check`. Keep your own banks and plugins in `private/`, which is gitignored.

## Requirements

- macOS 14+, Python 3.11+, Xcode Command Line Tools. No third-party packages.
- Messages.app signed in with an Apple ID whose iMessage address is your phone number.
- Permissions: Automation for Messages, Reminders, Full Disk Access (only for bedtime keywords), Location Services (only for `location` gate checks).
- The Mac must be awake at wake time. See [SETUP.md](SETUP.md).

## Install

```bash
git clone https://github.com/YC11Hou/morning-waker.git && cd morning-waker
cp config.example.toml config.local.toml   # set your number, friends, times
bin/morningwaker test-msg                   # verify the iMessage channel
bin/morningwaker doctor                     # verify permissions
bin/morningwaker on                         # start the daemon under launchd
bin/morningwaker drill                      # dry run at 10x speed; tattles go to you
```

## Commands

| Command | Description |
|---|---|
| `on` / `off` | Start or stop the daemon under launchd (restarted if it exits). `on --fork` forks it from the terminal instead. |
| `status` | Daemon state, today's progress, escalation steps. |
| `checkin` | Check in from the Mac (reminder mode; in gate mode it only ends drills). |
| `gate` | Open the gate page (gate mode). |
| `waive <check> [date]` | Skip one gate check for one day (default today). |
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
morningwaker/        config, imsg, reminders, location, gate, alarm, journal, daemon, cli, web, probe
web/                 dashboard and gate pages, no build step
helpers/             reminders.swift and location.swift, compiled to build/ on first use
plugins/             gate plugins and an example question bank
tests/               python3 -m unittest discover -s tests
launchd/             plist template
config.example.toml  the only file you need to edit (copy to config.local.toml)
```

`config.local.toml`, `private/`, `state/`, and `logs/` hold personal data and are gitignored. The code has no default numbers or paths. Design notes: [ARCHITECTURE.md](ARCHITECTURE.md).

## License

MIT
