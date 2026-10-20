// CoreLocation helper: prints {"lat","lon","acc","age"} for the Mac's current position, or exits non-zero.
import CoreLocation
import Foundation

func fail(_ msg: String, _ code: Int32) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!); exit(code)
}

final class Locator: NSObject, CLLocationManagerDelegate {
    let manager = CLLocationManager()
    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }
    func locationManagerDidChangeAuthorization(_ m: CLLocationManager) {
        switch m.authorizationStatus {
        case .notDetermined: m.requestWhenInUseAuthorization()
        case .denied, .restricted: fail("location access denied (System Settings > Privacy & Security > Location Services)", 77)
        default: m.requestLocation()
        }
    }
    func locationManager(_ m: CLLocationManager, didUpdateLocations locs: [CLLocation]) {
        guard let l = locs.last else { return }
        let out: [String: Double] = ["lat": l.coordinate.latitude, "lon": l.coordinate.longitude,
                                     "acc": l.horizontalAccuracy, "age": -l.timestamp.timeIntervalSinceNow]
        let d = try! JSONSerialization.data(withJSONObject: out, options: [.sortedKeys])
        print(String(data: d, encoding: .utf8)!); exit(0)
    }
    func locationManager(_ m: CLLocationManager, didFailWithError e: Error) { fail("location failed: \(e)", 1) }
}

guard CLLocationManager.locationServicesEnabled() else { fail("Location Services is off", 69) }
let timeout = Double(CommandLine.arguments.dropFirst().first ?? "") ?? 20
let locator = Locator()
DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { fail("location timed out after \(Int(timeout)) s", 2) }
RunLoop.main.run()
