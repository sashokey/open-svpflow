import AppKit
import CoreGraphics
import Foundation

guard ProcessInfo.processInfo.environment["GITHUB_ACTIONS"] == "true",
      CommandLine.arguments.count == 2,
      let identifier = Int32(CommandLine.arguments[1]) else {
    FileHandle.standardError.write(Data("This metadata probe runs only on isolated GitHub Actions.\n".utf8))
    exit(2)
}

let application = NSRunningApplication(processIdentifier: identifier)
let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]]
guard let windows else {
    FileHandle.standardError.write(Data("No Quartz window session is available.\n".utf8))
    exit(3)
}
let visible = windows.filter { window in
    guard window[kCGWindowOwnerPID as String] as? Int32 == identifier,
          window[kCGWindowLayer as String] as? Int == 0,
          let bounds = window[kCGWindowBounds as String] as? [String: Double] else { return false }
    return bounds["Width", default: 0] >= 100 && bounds["Height", default: 0] >= 100
}.count
let policy: String
switch application?.activationPolicy {
case .regular: policy = "regular"
case .accessory: policy = "accessory"
case .prohibited: policy = "prohibited"
default: policy = "unknown"
}
let value: [String: Any] = ["running": application != nil && application?.isTerminated == false, "policy": policy, "visibleWindows": visible]
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]))
FileHandle.standardOutput.write(Data([10]))
