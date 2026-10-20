// CoreAudio helper: `builtin` makes the built-in speakers the default output and prints the previous device id; `set <id>` restores one.
import CoreAudio
import Foundation

func addr(_ sel: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: sel, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}
let system = AudioObjectID(kAudioObjectSystemObject)
let idSize = UInt32(MemoryLayout<AudioDeviceID>.size)

func current() -> AudioDeviceID {
    var a = addr(kAudioHardwarePropertyDefaultOutputDevice), id = AudioDeviceID(0), size = idSize
    AudioObjectGetPropertyData(system, &a, 0, nil, &size, &id)
    return id
}

func setDefault(_ id: AudioDeviceID) -> Bool {
    var dev = id, ok = true
    for sel in [kAudioHardwarePropertyDefaultOutputDevice, kAudioHardwarePropertyDefaultSystemOutputDevice] {
        var a = addr(sel)
        ok = AudioObjectSetPropertyData(system, &a, 0, nil, idSize, &dev) == noErr && ok
    }
    return ok
}

func builtinOutput() -> AudioDeviceID? {
    var a = addr(kAudioHardwarePropertyDevices), size: UInt32 = 0
    AudioObjectGetPropertyDataSize(system, &a, 0, nil, &size)
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / Int(idSize))
    AudioObjectGetPropertyData(system, &a, 0, nil, &size, &ids)
    return ids.first { id in
        var t: UInt32 = 0, ts = UInt32(MemoryLayout<UInt32>.size), ta = addr(kAudioDevicePropertyTransportType)
        AudioObjectGetPropertyData(id, &ta, 0, nil, &ts, &t)
        var sa = addr(kAudioDevicePropertyStreams, kAudioObjectPropertyScopeOutput), ss: UInt32 = 0
        AudioObjectGetPropertyDataSize(id, &sa, 0, nil, &ss)
        return t == kAudioDeviceTransportTypeBuiltIn && ss > 0
    }
}

let args = CommandLine.arguments
switch args.count > 1 ? args[1] : "" {
case "builtin":
    let before = current()
    guard let id = builtinOutput() else { print("no built-in output"); exit(1) }
    print(before)
    exit(id == before || setDefault(id) ? 0 : 1)
case "set":
    guard args.count > 2, let id = AudioDeviceID(args[2]) else { exit(64) }
    exit(setDefault(id) ? 0 : 1)
case "current":
    print(current())
default:
    FileHandle.standardError.write("usage: audio builtin|set <id>|current\n".data(using: .utf8)!); exit(64)
}
