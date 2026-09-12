// WakeUpCall 的 EventKit 帮手（编译成 build/reminders）。只做三件事：
//   ensure <list> <title> <yyyy-mm-dd> <HH:MM> <tz> [notes]   当天没有同名提醒就新建；输出 JSON {id}
//   status <list> <title> <yyyy-mm-dd> <tz>                    输出 JSON {exists, completed, completion}
//   lists                                                      输出所有提醒列表名
// 权限边界：只新增、只读。绝不标记完成、绝不删除（那是用户本人的动作）。
import EventKit
import Foundation

func fail(_ msg: String, _ code: Int32 = 1) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!); exit(code)
}
func json(_ obj: Any) {
    let d = try! JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])
    print(String(data: d, encoding: .utf8)!)
}

let args = CommandLine.arguments
guard args.count >= 2 else { fail("usage: reminders ensure|status|lists ...", 64) }

let store = EKEventStore()
let sem = DispatchSemaphore(value: 0); var granted = false
store.requestFullAccessToReminders { g, _ in granted = g; sem.signal() }
sem.wait()
guard granted else { fail("no reminders access (grant it in System Settings > Privacy > Reminders)", 77) }

if args[1] == "lists" {
    json(store.calendars(for: .reminder).map { $0.title }); exit(0)
}

guard args.count >= 6 else { fail("missing arguments", 64) }
let listName = args[2], title = args[3], dateStr = args[4]
guard let cal = store.calendars(for: .reminder).first(where: { $0.title == listName }) else {
    fail("list not found: \(listName)", 66)
}
let dayParts = dateStr.split(separator: "-").compactMap { Int($0) }
guard dayParts.count == 3 else { fail("bad date \(dateStr)", 64) }

func dayRange(tzId: String) -> (Date, Date) {
    var c = DateComponents(); c.calendar = Calendar(identifier: .gregorian)
    c.timeZone = TimeZone(identifier: tzId)
    c.year = dayParts[0]; c.month = dayParts[1]; c.day = dayParts[2]; c.hour = 0; c.minute = 0
    let start = c.date!
    return (start, start.addingTimeInterval(86400))
}

// 找到该日期、该标题的提醒（含已完成）
func find(tzId: String) -> EKReminder? {
    let (start, end) = dayRange(tzId: tzId)
    let s2 = DispatchSemaphore(value: 0); var found: EKReminder?
    store.fetchReminders(matching: store.predicateForReminders(in: [cal])) { rs in
        for r in rs ?? [] where r.title == title {
            if let due = r.dueDateComponents?.date, due >= start, due < end { found = r; break }
        }
        s2.signal()
    }
    s2.wait()
    return found
}

let iso = ISO8601DateFormatter()
switch args[1] {
case "status":
    let tz = args[5]
    if let r = find(tzId: tz) {
        json(["exists": true, "completed": r.isCompleted,
              "completion": r.completionDate.map { iso.string(from: $0) } ?? NSNull(),
              "id": r.calendarItemIdentifier])
    } else {
        json(["exists": false, "completed": false, "completion": NSNull()])
    }
case "ensure":
    guard args.count >= 7 else { fail("ensure needs HH:MM and tz", 64) }
    let hm = args[5].split(separator: ":").compactMap { Int($0) }, tz = args[6]
    guard hm.count == 2 else { fail("bad time", 64) }
    if let r = find(tzId: tz) { json(["id": r.calendarItemIdentifier, "created": false]); exit(0) }
    let r = EKReminder(eventStore: store)
    r.calendar = cal; r.title = title
    if args.count >= 8 { r.notes = args[7] }
    var c = DateComponents(); c.calendar = Calendar(identifier: .gregorian)
    c.timeZone = TimeZone(identifier: tz)
    c.year = dayParts[0]; c.month = dayParts[1]; c.day = dayParts[2]; c.hour = hm[0]; c.minute = hm[1]
    r.dueDateComponents = c
    r.addAlarm(EKAlarm(absoluteDate: c.date!))
    do { try store.save(r, commit: true) } catch { fail("save failed: \(error)", 74) }
    json(["id": r.calendarItemIdentifier, "created": true])
default:
    fail("unknown command \(args[1])", 64)
}
