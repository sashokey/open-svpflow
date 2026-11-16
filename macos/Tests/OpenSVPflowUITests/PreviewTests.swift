import Foundation
import ImageIO
import OpenSVPflowCore
import SwiftUI
import Testing
import UniformTypeIdentifiers
@testable import OpenSVPflow

@Test(.enabled(if: ProcessInfo.processInfo.environment["OPEN_SVPFLOW_RENDER_PREVIEWS"] != nil))
@MainActor func renderNativeViewsWithoutOpeningWindows() async throws {
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
        try capture(MainView(model: model).environment(\.colorScheme, scheme).frame(width: 540, height: 480), to: output.appendingPathComponent("main-" + name + ".png"))
        try capture(SettingsView(model: model).environment(\.colorScheme, scheme), to: output.appendingPathComponent("settings-" + name + ".png"))
    }
    model.phase = .setup
    model.needsHomebrew = true
    model.setupMessage = "Install Homebrew to prepare external playback components."
    try capture(MainView(model: model).environment(\.colorScheme, .light).frame(width: 540, height: 480), to: output.appendingPathComponent("setup-light.png"))
}

@MainActor private func capture<V: View>(_ view: V, to url: URL) throws {
    let renderer = ImageRenderer(content: view)
    renderer.scale = 2
    let image = try #require(renderer.cgImage)
    #expect(image.width >= 900)
    #expect(image.height >= 600)
    let destination = try #require(CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil))
    CGImageDestinationAddImage(destination, image, nil)
    #expect(CGImageDestinationFinalize(destination))
}
