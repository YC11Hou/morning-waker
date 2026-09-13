# MorningWaker 配置教程（从零到跑起来）

全程只在 macOS 上做。不需要任何第三方 Python 包，不需要 MCP，不需要服务器。
**所有个人信息只写进 `config.local.toml` 这一个文件**，它已在 `.gitignore` 里，永远不会进 git。

## 0. 它是怎么碰到你的短信和提醒事项的（先看懂再配）

| 通道 | 发 | 收 | 用的系统机制 |
|---|---|---|---|
| iMessage | `osascript` 调 Messages.app，给**你自己的号码**发一条 | 只读打开 `~/Library/Messages/chat.db`（SQLite），每 10 秒看有没有新行 | AppleScript 自动化 + 完全磁盘访问 |
| 提醒事项 | EventKit 新建一条「☀️ 起床打卡」（`helpers/reminders.swift`） | EventKit 读它的 `completionDate` | 提醒事项访问权限 |
| 本机 | — | `morningwaker checkin` touch 一个文件 | 无 |

要点：

- **不是 MCP、不是短信网关**。Messages.app 登录的 Apple ID 就是你的手机号，Mac 给这个号码发 iMessage，
  你的 iPhone 会像收到别人消息一样弹横幅（"自发自收"）。你在手机上回「起」，这条消息同步回 Mac 的
  `chat.db`，守护进程读到就算打卡。
- 读 `chat.db` 走 `sqlite3` 的 `mode=ro`，**只读**，不写、不删、不改。
- 提醒事项那条由程序**新建**，但**永远不替你勾、不删**。勾是你本人的动作，勾的时刻就是打卡时间。
- 告状（tattle）就是给朋友的号码也发一条 iMessage，同一条发送通道。

## 1. 前提

- macOS 14+，Python ≥ 3.11（用了标准库 `tomllib`），Xcode Command Line Tools（编译 `reminders.swift`：`xcode-select --install`）。
- Messages.app 已用你的 Apple ID 登录，且 **设置 → iMessage → 可通过以下方式联系** 里勾了你的手机号。
- iPhone 用同一 Apple ID，提醒事项开了 iCloud 同步（勾掉那条才能同步回 Mac）。

## 2. 拿代码、填配置

```bash
git clone https://github.com/YC11Hou/morning-waker.git
cd morning-waker
cp config.example.toml config.local.toml
```

打开 `config.local.toml`，必填的只有三处：

| 键 | 填什么 | 备注 |
|---|---|---|
| `[imessage].self` | 你自己的手机号，E.164 格式，如 `+6591234567` | 就是 iMessage 登记的那个号。占位值 `+6500000000` 不改程序会拒绝启动 |
| `[[escalation]] tattle.to` | 朋友的号码列表 | 不想告状就把整段 `action = "tattle"` 删掉 |
| `[tattle].name` | 你的名字 | 告状文案里的 `{name}` |

其他都有合理默认：`timezone`（按你人在哪填，与 Mac 系统时区无关）、`wake.time`、`reminders.list`
（提醒事项里**已存在**的列表名，不会自动建列表）。

也可以不放在项目目录：`export MORNINGWAKER_CONFIG=~/.config/morningwaker.toml`。

## 3. 授权（三个弹窗，各点一次「允许」）

按顺序跑，每一步会触发对应的授权框：

```bash
bin/morningwaker test-msg     # ① 自动化 → Messages：手机应收到一条测试消息
bin/morningwaker doctor       # ② 提醒事项访问；③ 完全磁盘访问（读 chat.db）
```

- ③ 不会自动弹：到 **系统设置 → 隐私与安全性 → 完全磁盘访问**，把你跑命令的**终端 App**
  （Terminal / iTerm / VS Code）加进去，重开终端再跑 `doctor`。
- `doctor` 全绿才往下走。它会逐项告诉你缺哪一个。

## 4. 演习一遍

```bash
bin/morningwaker on           # 后台守护进程（从当前终端 fork，继承上面的授权）
bin/morningwaker drill        # 15 分钟的升级阶梯压成 90 秒；告状只发给你自己，不发朋友
```

Mac 会响铃、手机会连收几条；在手机上回「起」或跑 `bin/morningwaker checkin` 结束。
想只验证"能不能读到打卡"而不发任何消息：`bin/morningwaker probe`，输出 `1`（已打卡）或 `0`。

## 5. 让它每天早上真的醒着

- 提醒事项那条前一天就建好，**iPhone 到点自己响，不依赖 Mac**。
- 但响铃和升级告状要 Mac 醒着。二选一：
  - `sudo pmset repeat wakeorpoweron MTWRFSU 08:58:00`（**本机时区**，不是配置里的时区）
  - 或一直 `caffeinate -ism` 挂着。
- 重启 Mac 后要再跑一次 `bin/morningwaker on`（从终端 fork 才有权限）。
  想开机自启用 `on --launchd`，但要先在「完全磁盘访问」里加上 **Python 解释器本体**
  （`which python3` 那个路径），否则 launchd 起的进程读不了 chat.db。

## 6. 哪些东西是你的、不能公开

| 路径 | 内容 | 状态 |
|---|---|---|
| `config.local.toml` | 你和朋友的号码、名字、时间 | `.gitignore` |
| `state/` | 当天进度、pid、提醒事项 id | `.gitignore` |
| `logs/` | 打卡日志、守护进程日志 | `.gitignore` |
| `build/` | 编译出的 EventKit 帮手 | `.gitignore` |

代码里没有任何默认号码或绝对路径；缺了配置直接报错，不会悄悄用作者的值。
fork 之后想确认没夹带私货：`git grep -nE '\+[0-9]{8,}'` 应只命中 `config.example.toml` 和测试里的假号。
