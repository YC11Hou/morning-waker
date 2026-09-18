// EventKit helper: `ensure <list> <title> <date> <HH:MM> <tz> [notes] [repeat]`, `status`, `lists`; creates and reads only.
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

func candidates() -> [EKReminder] {
    store.refreshSourcesIfNecessary()
    let s2 = DispatchSemaphore(value: 0); var out: [EKReminder] = []
    store.fetchReminders(matching: store.predicateForReminders(in: [cal])) { rs in
        out = (rs ?? []).filter { $0.title == title }; s2.signal()
    }
    s2.wait()
    return out
}

// A reminder covers the day if it is due that day, or it is an open repeating series that started on or before it.
func existing(tzId: String) -> EKReminder? {
    let (start, end) = dayRange(tzId: tzId)
    var series: EKReminder?
    for r in candidates() {
        guard let due = r.dueDateComponents?.date else { continue }
        if due >= start && due < end { return r }
        guard !r.isCompleted, let rules = r.recurrenceRules, !rules.isEmpty, due < end else { continue }
        if let last = rules[0].recurrenceEnd?.endDate, last < start { continue }
        series = r
    }
    return series
}

// The occurrence completed on that day, if any; the Reminders app keeps each completed occurrence as its own item.
func completedOn(tzId: String) -> EKReminder? {
    let (start, end) = dayRange(tzId: tzId)
    return candidates().first { r in
        guard let c = r.completionDate else { return false }
        return c >= start && c < end
    }
}

func rule(_ spec: String) -> EKRecurrenceRule {
    if spec == "daily" { return EKRecurrenceRule(recurrenceWith: .daily, interval: 1, end: nil) }
    let days = spec.split(separator: ",").compactMap { Int($0) }.map { EKRecurrenceDayOfWeek(EKWeekday(rawValue: $0 % 7 + 1)!) }
    return EKRecurrenceRule(recurrenceWith: .weekly, interval: 1, daysOfTheWeek: days, daysOfTheMonth: nil,
                            monthsOfTheYear: nil, weeksOfTheYear: nil, daysOfTheYear: nil, setPositions: nil, end: nil)
}

let iso = ISO8601DateFormatter()
switch args[1] {
case "status":
    let tz = args[5]
    let done = completedOn(tzId: tz), open = existing(tzId: tz)
    if let r = done ?? open {
        json(["exists": true, "completed": done != nil,
              "completion": done?.completionDate.map { iso.string(from: $0) } ?? NSNull(),
              "id": r.calendarItemIdentifier])
    } else {
        json(["exists": false, "completed": false, "completion": NSNull()])
    }
case "ensure":
    guard args.count >= 7 else { fail("ensure needs HH:MM and tz", 64) }
    let hm = args[5].split(separator: ":").compactMap { Int($0) }, tz = args[6]
    guard hm.count == 2 else { fail("bad time", 64) }
    if let r = existing(tzId: tz) { json(["id": r.calendarItemIdentifier, "created": false]); exit(0) }
    let r = EKReminder(eventStore: store)
    r.calendar = cal; r.title = title
    if args.count >= 8 { r.notes = args[7] }
    r.addRecurrenceRule(rule(args.count >= 9 ? args[8] : "daily"))
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
