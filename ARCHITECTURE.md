# WakeUpCall 架构

## 一句话

一个常驻守护进程跑一台**每日状态机**：到点开启"起床流程"，从三个打卡口收信号，
没信号就沿着可配置的阶梯升级，直到打卡或超时。所有个人数据只在 `config.local.toml`、
`state/`、`logs/`，代码不含任何号码或路径。

```
                 ┌────────────────────────────────────────────────┐
                 │  daemon.py  每 10 秒 tick 一次                  │
                 │                                                │
  config.local ─▶│  idle ──(到点)──▶ active ──(打卡)──▶ done        │
                 │            │            └──(超时)──▶ missed     │
                 │            └──(窗口已过才启动)──▶ skipped        │
                 └───────┬──────────────┬──────────────┬──────────┘
                         │ 通知/升级     │ 打卡信号      │ 记录
              ┌──────────▼───┐  ┌───────▼────────┐  ┌──▼──────────┐
              │ imsg.send    │  │ reminders 完成  │  │ journal.jsonl│
              │ alarm (afplay)│  │ imsg 关键词     │  │ state/day.json│
              │ tattle → 朋友 │  │ state/checkin   │  │              │
              └──────────────┘  └────────────────┘  └─────────────┘
```

## 模块

| 文件 | 职责 | 依赖系统 |
|---|---|---|
| `config.py` | 读 TOML，校验占位值，产出不可变 `Config` | — |
| `daemon.py` | 状态机 + 主循环；`Day` 是当天进度，落盘 `state/day.json` | — |
| `reminders.py` + `helpers/reminders.swift` | 提醒事项通道：新建当天/明天那条；读 `completionDate` | EventKit（只加、只读） |
| `imsg.py` | 发 iMessage（AppleScript，正文走 argv）；只读 chat.db 取新消息 | Messages.app、`chat.db` |
| `alarm.py` | Mac 本机循环响铃；拉音量、停铃还原；`caffeinate -u` 点亮屏幕 | afplay、osascript |
| `journal.py` | 事件日志、守护进程日志、`stats` 表 | — |
| `web.py` + `web/index.html` | 本地仪表盘：只读 `/api/days`（journal 按日聚合）与 `/api/status`；月历 + 统计 + 每日时间线 | 127.0.0.1 |
| `cli.py` | `on/off/status/checkin/drill/doctor/test-msg/log/stats/run` | launchctl（可选） |

## 三个打卡口，优先级从上到下

1. **提醒事项（主）**：守护进程提前一天在指定 list 里新建「☀️ 起床打卡」（due=起床时刻，带 alarm）。
   iPhone 到点**本地**响提醒，不依赖 Mac。用户勾掉 → iCloud 同步到 Mac → `completionDate` 即打卡时刻。
   守护进程永远不勾、不删。
2. **iMessage 关键词**：用户在手机上回「起」等词。整条消息精确匹配，自己发出的消息（六个前缀）跳过。
3. **本机文件**：`wakeupcall checkin` touch `state/checkin`，mtime 必须晚于流程开始。

打卡时间：提醒事项用它自己的 `completionDate`；另两种用守护进程读到的时刻。

## 为什么这样设计

- **时区显式**：`[general].timezone`，用 `zoneinfo`，不信系统时区。
- **守护进程从终端 fork 而不是 launchd**：launchd 起的 Python 没有「完全磁盘访问」和「自动化」
  权限，读不了 chat.db、发不了 iMessage；从终端 fork 继承终端的 TCC 授权。代价是重启 Mac 后要
  手动 `wakeupcall on`。`on --launchd` 保留给愿意给 Python 授权的人。
- **Mac 睡着怎么办**：提醒事项那条前一天就建好，iPhone 自己会响；`pmset repeat wakeorpoweron`
  让 Mac 在起床前醒来接管响铃和升级。两条腿。
- **重启不丢**：`state/day.json` 记录阶段、已升级步骤、rowid 基线；重启后 active 继续响铃。
- **启动太晚不误触**：窗口 = [起床时刻, 起床时刻 + hard_timeout]，之外启动当天 skipped。
- **演习安全**：`drill` 把阶梯分钟数缩放；tattle 改发自己、shell 只记日志、不建提醒事项。
- **防自激**：自发自收通道里自己发的消息也会落库；前缀黑名单 + 整条精确匹配。

## 状态文件

```
state/day.json      {"date", "wake_phase", "wake_started", "target", "fired", "rowid_base",
                     "drill_scale", "reminder_ids": {date: id}}
state/checkin       本机打卡触发（mtime）
state/drill         演习触发（内容 = 缩放倍率）
state/daemon.pid
logs/journal.jsonl  事件：wake_start / escalate / checkin / missed
logs/daemon.log     守护进程日志
```

## 测试

`python3 -m unittest discover -s tests`：假时钟、假 iMessage、假提醒事项、假闹铃，
覆盖：提前建提醒、到点开启、三种打卡口、防自激、告状时机、超时 missed、启动太晚跳过、重启续响。
