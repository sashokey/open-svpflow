import AppKit
import OpenSVPflowCore
import SwiftUI
import Testing
@testable import OpenSVPflow

@Test(.enabled(if: ProcessInfo.processInfo.environment["GITHUB_ACTIONS"] == "true" && ProcessInfo.processInfo.environment["OPEN_SVPFLOW_RENDER_PREVIEWS"] != nil))
@MainActor func renderNativeViewsOnIsolatedRunner() async throws {
    let application = NSApplication.shared
    application.setActivationPolicy(.accessory)
    application.finishLaunching()
    let output = URL(fileURLWithPath: ProcessInfo.processInfo.environment["OPEN_SVPFLOW_RENDER_PREVIEWS"]!)
    try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
    let model = AppModel()
    model.phase = .ready
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    let state = FileManager.default.temporaryDirectory.appendingPathComponent("open-svpflow-preview-" + UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: state) }
    let client = BackendClient(python: RuntimeDiscovery.python() ?? URL(fileURLWithPath: "/usr/bin/python3"), resources: root, stateDirectory: state)
    let description = try await client.call("describe")
    let settings = try #require(description["description"])
    model.document = try #require(settings["defaults"])
    model.descriptors = try settings["fields"]?.array.map(SettingDescriptor.init) ?? []
    model.limitations = settings["limitations"]?.array.compactMap(\.string) ?? []
    for (name, scheme) in [("light", ColorScheme.light), ("dark", ColorScheme.dark)] {
        try await capture(MainView(model: model).environment(\.colorScheme, scheme), size: NSSize(width: 540, height: 480), scheme: scheme, to: output.appendingPathComponent("main-" + name + ".png"))
        try await capture(SettingsView(model: model).environment(\.colorScheme, scheme), size: NSSize(width: 650, height: 560), scheme: scheme, to: output.appendingPathComponent("settings-" + name + ".png"))
    }
    model.phase = .setup
    model.needsHomebrew = true
    model.setupMessage = "Install Homebrew to prepare external playback components."
    try await capture(MainView(model: model).environment(\.colorScheme, .light), size: NSSize(width: 540, height: 480), scheme: .light, to: output.appendingPathComponent("setup-light.png"))
}

@MainActor private func capture<V: View>(_ view: V, size: NSSize, scheme: ColorScheme, to url: URL) async throws {
    let window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: [.titled, .closable], backing: .buffered, defer: false)
    window.isReleasedWhenClosed = false
    window.appearance = NSAppearance(named: scheme == .dark ? .darkAqua : .aqua)
    let hosting = NSHostingView(rootView: view.frame(width: size.width, height: size.height).background(Color(nsColor: .windowBackgroundColor)))
    hosting.frame = NSRect(origin: .zero, size: size)
    window.contentView = hosting
    window.makeKeyAndOrderFront(nil)
    defer {
        window.orderOut(nil)
        window.close()
    }
    try await Task.sleep(for: .milliseconds(300))
    hosting.layoutSubtreeIfNeeded()
    window.displayIfNeeded()
    hosting.displayIfNeeded()
    let bitmap = try #require(hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds))
    hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
    #expect(bitmap.pixelsWide >= Int(size.width))
    #expect(bitmap.pixelsHigh >= Int(size.height))
    let png = try #require(bitmap.representation(using: .png, properties: [:]))
    try png.write(to: url)
    var colors = Set<Int>()
    var samples = 0
    var opaqueSamples = 0
    var placeholderSamples = 0
    for y in stride(from: 0, to: bitmap.pixelsHigh, by: 8) {
        for x in stride(from: 0, to: bitmap.pixelsWide, by: 8) {
            guard let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB) else { continue }
            samples += 1
            if color.alphaComponent > 0.5 { opaqueSamples += 1 }
            let red = color.redComponent
            let green = color.greenComponent
            let blue = color.blueComponent
            colors.insert((Int(red * 255) << 16) | (Int(green * 255) << 8) | Int(blue * 255))
            if red > 0.65 && blue < 0.3 && (green > 0.55 || green < 0.35) { placeholderSamples += 1 }
        }
    }
    #expect(samples > 0)
    #expect(opaqueSamples > samples / 2)
    #expect(colors.count > 16)
    #expect(placeholderSamples * 100 < samples * 15)
}
