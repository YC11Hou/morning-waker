# WakeUpCall ☎️ 叫早服务

到点必须**打卡**，不打卡就一路升级：手机催 → 再催 → **给朋友发消息告状**。
像酒店的 wake-up call：电话一直打到你接为止。也像上班打卡：没人替你按。

解决的问题不是"闹钟不够响"，而是**闹钟被手一挥就掐了**。WakeUpCall 要求你
清醒地做一个动作（在手机上回一个字），否则后果会发生在你身上之外的地方。

## 它怎么叫你

```
23:30  🌙 该睡了                       ← 睡前提醒（可关）；回「睡」记一笔上床时间
09:00  ⏰ Mac 循环响铃 + 手机 iMessage    ← 起床流程开始
09:05  ⏰ 再催
09:10  ⏰ 最后通牒
09:15  📣 给朋友发「他还没起」          ← 告状
       …直到你回「起」：停铃、记录迟到几分钟、发确认
```

每一步的时间、文案、动作都在 `config.local.toml` 里改。动作有三种：
`notify`（推给自己）、`tattle`（发给朋友列表）、`shell`（跑任意命令，自己加更狠的）。

## 打卡方式（三个口，任一即可）

1. **提醒事项（主）**：每天提前一天在 macOS 提醒事项里建好一条「☀️ 起床打卡」，iPhone 到点本地响。
   你在手机上**勾掉它**就是打卡，勾的那一刻就是打卡时间。WakeUpCall 只建、只读，永远不替你勾。
2. **iMessage**：给自己的号码回一条，内容是关键词之一（默认 `起` / `起了` / `up` / `打卡`）。
3. **Mac**：`wakeupcall checkin`。

只认起床流程开始之后的信号。

## 前提（macOS）

1. Mac 已登录 iMessage，且登录的 Apple ID 号码就是你的手机号（消息是"自发自收"，你的 iPhone 会收到横幅）。
2. 终端/Python 有 **自动化 → Messages** 权限，以及 **完全磁盘访问**（读 `~/Library/Messages/chat.db`，只读）。首次运行会弹授权框。
3. **早上那个点 Mac 不能在睡眠**。合盖或系统睡眠时什么都不会发生。锁屏可以，睡眠不行：
   `caffeinate -ism` 挂着，或 `sudo pmset repeat wakeorpoweron MTWRFSU 08:58:00`（注意这是本机时区）。
4. Python ≥ 3.11（用了 `tomllib`）。没有第三方依赖。

## 安装

```bash
git clone <this repo> ~/Desktop/Projects/wakeupcall && cd ~/Desktop/Projects/wakeupcall
cp config.example.toml config.local.toml     # 填自己的手机号、朋友号码、时间
bin/wakeupcall test-msg                      # 先验证 iMessage 通道：手机应收到一条
bin/wakeupcall on                            # 后台常驻（从终端 fork，继承权限；重启 Mac 后要再跑一次）
bin/wakeupcall drill                         # 演习：整条链路走一遍（15 分钟压成 90 秒，告状只发给自己）
```

把 `bin/` 加进 PATH 或 `ln -s $PWD/bin/wakeupcall ~/bin/`。

## 命令

| 命令 | 作用 |
|---|---|
| `on` / `off` | 启动 / 停止守护进程。`on --launchd` 用 launchd 开机自启，但要先在「隐私与安全性 → 完全磁盘访问」里加上 Python 本体，否则 launchd 起的进程读不了 chat.db |
| `status` | 守护进程状态、今天的进度、升级阶梯 |
| `checkin` | 本机打卡 |
| `drill [倍率]` | 立刻演习，倍率缩放升级分钟数（默认 0.1） |
| `doctor` | 体检：配置、chat.db 权限、提醒事项列表、定时唤醒、防睡眠、守护进程、告状收件人 |
| `test-msg` | 给自己发一条测试消息 |
| `log` | 跟踪守护进程日志 |
| `stats [天]` | 最近 N 天打卡表：目标 / 打卡 / 迟到 / 告状 / 上床 |
| `run` | 前台跑守护进程（调试） |

## 目录

```
wakeupcall/          代码（config / imsg / reminders / alarm / journal / daemon / cli）
helpers/             reminders.swift（EventKit 帮手，首次运行自动编译到 build/）
tests/               状态机单元测试（python3 -m unittest discover -s tests）
bin/wakeupcall       入口
launchd/             plist 模板，`on` 时填入路径生成到 ~/Library/LaunchAgents
config.example.toml  配置样例 —— 唯一需要你改的东西
config.local.toml    你的真实配置（.gitignore，含号码）
state/               当天进度（.gitignore）
logs/                打卡日志 journal.jsonl + 守护进程日志（.gitignore）
```

代码里没有任何个人默认值：号码、朋友、路径全部只从 `config.local.toml` 读。
仓库可以直接公开。

设计细节见 `ARCHITECTURE.md`。

## 设计要点

- **时区显式配置**，与 Mac 系统时区无关（作者的 Mac 是纽约时区、人在新加坡）。
- **自发自收通道防自激**：自己发的消息都以 ⏰ ✅ ❌ 🌙 📣 🎯 开头，读 chat.db 时跳过；关键词匹配整条消息，`起床打卡！` 不会误当成 `起`。
- **守护进程重启不丢状态**：当天进度在 `state/day.json`；重启时若流程还在进行，继续响铃。
- **启动太晚不误触**：守护进程在起床窗口（起床时刻 + `hard_timeout_min`）之后才启动，当天直接跳过。
- **演习不伤人**：`drill` 里 `tattle` 改发给自己，`shell` 只记日志。
- 响铃期间把输出音量拉到配置值，停铃后还原。

## 和其他 iMessage 自动化共存

如果你还有别的程序在读同一个 chat.db 并回复消息（比如一个 iMessage 遥控 agent），
让它忽略以上六个前缀开头的消息，否则它会把 WakeUpCall 的催促当成提问。

## License

MIT
