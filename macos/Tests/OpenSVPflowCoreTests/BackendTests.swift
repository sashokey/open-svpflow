import Foundation
import Testing
@testable import OpenSVPflowCore

@Test func nativeClientReadsAndEditsRealPythonSettingsWithoutPlayback() async throws {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    let state = FileManager.default.temporaryDirectory.appendingPathComponent("open-svpflow-test-" + UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: state) }
    let python = RuntimeDiscovery.python() ?? URL(fileURLWithPath: "/usr/bin/python3")
    let client = BackendClient(python: python, resources: root, stateDirectory: state)
    let description = try await client.call("describe")
    let fields = try #require(description["description"]?["fields"]?.array)
    let descriptors = try fields.map(SettingDescriptor.init)
    #expect(descriptors.contains(where: { $0.path == "preferences.after_start" && $0.options.contains(where: { $0.value == .string("menu_bar") }) }))
    #expect(descriptors.contains(where: { $0.path == "playback.rate" && $0.options.contains(where: { $0.value == .string("monitor_half") }) }))
    let loaded = try await client.call("settings-get")
    var document = try #require(loaded["document"])
    let originalInterpolation = document["interpolation"]
    try document.set(.string("keep_open"), at: "preferences.after_start")
    let saved = try await client.call("settings-save", request: .object(["document": document]))
    #expect(saved["document"]?.value(at: "preferences.after_start") == .string("keep_open"))
    #expect(saved["document"]?["interpolation"] == originalInterpolation)
    let reloaded = try await client.call("settings-get")
    #expect(reloaded["document"]?.value(at: "preferences.after_start") == .string("keep_open"))
}
