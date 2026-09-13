# Setup

Everything runs locally on macOS. No third-party packages, no servers. All personal values live in `config.local.toml`, which is gitignored.

## 1. How it reaches your phone

| Channel | Send | Receive | Permission |
|---|---|---|---|
| iMessage | `osascript` tells Messages.app to message your own number | read-only SQLite query on `~/Library/Messages/chat.db` every 10 s | Automation (Messages), Full Disk Access |
| Reminders | EventKit creates one reminder (`helpers/reminders.swift`) | EventKit reads its `completionDate` | Reminders |
| Local | none | `morningwaker checkin` touches a file | none |

Messaging your own number delivers a banner on every device signed in to the same Apple ID. Your reply syncs back into `chat.db`, where the daemon reads it. Tattling uses the same send path with a friend's number.

## 2. Prerequisites

- macOS 14+, Python 3.11+, Xcode Command Line Tools (`xcode-select --install`).
- Messages.app signed in, with your phone number enabled under Settings > iMessage.
- iPhone on the same Apple ID with Reminders iCloud sync on.

## 3. Configure

```bash
git clone https://github.com/YC11Hou/morning-waker.git && cd morning-waker
cp config.example.toml config.local.toml
```

Required keys in `config.local.toml`:

| Key | Value |
|---|---|
| `[general].timezone` | IANA zone, e.g. `Europe/Berlin`. Independent of the Mac's system timezone. |
| `[imessage].self` | Your number in E.164 form. The placeholder is rejected at startup. |
| `[reminders].list` | An existing Reminders list name. |
| `[[escalation]] tattle.to` | Friends' numbers. Remove the step if you do not want tattling. |
| `[tattle].name` | Your name, used as `{name}` in the tattle text. |

To keep the config elsewhere: `export MORNINGWAKER_CONFIG=~/.config/morningwaker.toml`.

## 4. Grant permissions

```bash
bin/morningwaker test-msg   # prompts for Automation > Messages
bin/morningwaker doctor     # prompts for Reminders; reports Full Disk Access
```

Full Disk Access does not prompt. Add your terminal app under System Settings > Privacy & Security > Full Disk Access, reopen the terminal, and run `doctor` again until every row is `ok`.

## 5. Dry run

```bash
bin/morningwaker on      # daemon forks from this terminal and inherits its permissions
bin/morningwaker drill   # escalation at 10x speed; tattles go to you, not your friends
```

End it by replying with a keyword or running `bin/morningwaker checkin`. `bin/morningwaker probe` reports whether a check-in was recorded without sending anything.

## 6. Keep the Mac awake

- The Reminders channel works even if the Mac is asleep, because the reminder is created a day ahead.
- The alarm and escalation need the Mac awake: `sudo pmset repeat wakeorpoweron MTWRFSU 08:58:00` (system timezone) or keep `caffeinate -ism` running.
- After a reboot run `bin/morningwaker on` again. `on --launchd` starts at login but requires Full Disk Access for the Python binary itself (`which python3`).

## 7. Private files

| Path | Contents |
|---|---|
| `config.local.toml` | your numbers, names, times |
| `state/` | today's progress, pid, reminder ids |
| `logs/` | check-in journal and daemon log |
| `build/` | compiled EventKit helper |

All are gitignored. Before publishing a fork, `git grep -nE '\+[0-9]{8,}'` should only match the placeholders in `config.example.toml` and the test file.
