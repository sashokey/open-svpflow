import Foundation
import OpenSVPflowCore
import Testing
@testable import OpenSVPflow

@Test @MainActor func rejectedAutosaveDoesNotVetoQuitAndPreservesAcceptedSettings() async throws {
    let fixture = try await SettingsFixture()
    defer { fixture.remove() }
    fixture.model.edit("playback.fps", value: .string("144"))
    #expect(await fixture.model.flushSettings())
    fixture.model.edit("playback.fps", value: .string(""))
    #expect(await fixture.model.flushSettings() == false)
    #expect(fixture.model.hasUnsavedChanges)
    #expect(await fixture.model.prepareToClose())
    let saved = try await fixture.client.call("settings-get")
    #expect(saved["document"]?.value(at: "playback.fps") == .string("144"))
}

@Test @MainActor func windowCloseWaitsForRejectedPendingSaveWithoutVetoingExit() async throws {
    let fixture = try await SettingsFixture()
    defer { fixture.remove() }
    let original = fixture.model.document
    fixture.model.edit("playback.fps", value: .string(""))
    #expect(await fixture.model.prepareToClose())
    #expect(fixture.model.showError)
    let saved = try await fixture.client.call("settings-get")
    #expect(saved["document"] == original)
}

@Test @MainActor func closingPersistsValidPendingChangesBeforeExit() async throws {
    let fixture = try await SettingsFixture()
    defer { fixture.remove() }
    fixture.model.edit("preferences.after_start", value: .string("keep_open"))
    #expect(await fixture.model.prepareToClose())
    #expect(fixture.model.hasUnsavedChanges == false)
    let saved = try await fixture.client.call("settings-get")
    #expect(saved["document"]?.value(at: "preferences.after_start") == .string("keep_open"))
}

@Test @MainActor func leavingFixedRateKeepsLastAcceptedFPSWhenTheDraftIsInvalid() async throws {
    let fixture = try await SettingsFixture()
    defer { fixture.remove() }
    fixture.model.edit("playback.rate", value: .string("fixed"))
    fixture.model.edit("playback.fps", value: .string("144"))
    #expect(await fixture.model.flushSettings())
    fixture.model.edit("playback.fps", value: .string(""))
    fixture.model.edit("playback.rate", value: .string("monitor_half"))
    #expect(await fixture.model.flushSettings())
    let saved = try await fixture.client.call("settings-get")
    #expect(saved["document"]?.value(at: "playback.rate") == .string("monitor_half"))
    #expect(saved["document"]?.value(at: "playback.fps") == .string("144"))
}

@Test @MainActor func interpolationPresetsPersistOnlyTheirTabValues() async throws {
    let fixture = try await SettingsFixture()
    defer { fixture.remove() }
    let description = try await fixture.client.call("describe")
    let schema = try #require(description["description"])
    fixture.model.settingsDefaults = try #require(schema["defaults"])
    fixture.model.descriptors = try schema["fields"]?.array.map(SettingDescriptor.init) ?? []
    #expect(fixture.model.interpolationPreset == .factory)
    fixture.model.edit("interpolation.gpu_queues", value: .number(3))
    fixture.model.edit("preferences.after_start", value: .string("keep_open"))
    #expect(fixture.model.interpolationPreset == .factory)
    fixture.model.edit("playback.rate", value: .string("fixed"))
    fixture.model.edit("playback.fps", value: .string("144"))
    #expect(fixture.model.interpolationPreset == .custom)
    fixture.model.applyInterpolationPreset(.smooth)
    #expect(fixture.model.interpolationPreset == .smooth)
    #expect(await fixture.model.flushSettings())
    let saved = try #require(try await fixture.client.call("settings-get")["document"])
    #expect(saved.value(at: "interpolation.fi_mode") == .number(5))
    #expect(saved.value(at: "interpolation.fi_precision") == .number(4))
    #expect(saved.value(at: "interpolation.output_bits") == .number(8))
    #expect(saved.value(at: "playback.rate") == .string("monitor"))
    #expect(saved.value(at: "playback.fps") == .string("120"))
    #expect(saved.value(at: "interpolation.gpu_queues") == .number(3))
    #expect(saved.value(at: "preferences.after_start") == .string("keep_open"))
    fixture.model.document = saved
    #expect(fixture.model.interpolationPreset == .smooth)
    fixture.model.edit("interpolation.fi_precision", value: .number(2))
    #expect(fixture.model.interpolationPreset == .custom)
    fixture.model.edit("interpolation.fi_precision", value: .number(4))
    #expect(fixture.model.interpolationPreset == .smooth)
    fixture.model.applyInterpolationPreset(.factory)
    #expect(fixture.model.interpolationPreset == .factory)
    #expect(await fixture.model.flushSettings())
}

@Test @MainActor func playbackFailureSurfacesItsReasonOnce() async throws {
    let fixture = try await SettingsFixture()
    defer { fixture.remove() }
    let failure = fixture.state.appendingPathComponent("event.json")
    let event: JSONValue = .object([
        "ok": .bool(false),
        "status": .string("error"),
        "failure_id": .string("attempt-1"),
        "file_title": .string("Movie.mkv"),
        "message": .string("Display refresh rate is unavailable."),
        "rate_error": .bool(true)
    ])
    try event.encoded().write(to: failure)
    await fixture.model.refreshStatus()
    #expect(fixture.model.showInterpolationFailure)
    #expect(fixture.model.interpolationFailure?.title == "Movie.mkv")
    #expect(fixture.model.interpolationFailure?.message == "Display refresh rate is unavailable.")
    #expect(fixture.model.interpolationFailure?.rateError == true)

    fixture.model.showInterpolationFailure = false
    try event.encoded().write(to: failure)
    await fixture.model.refreshStatus()
    #expect(!fixture.model.showInterpolationFailure)
    #expect(!fixture.model.showError)
}

@Test @MainActor func overlappingStatusChecksStillShowTheConsumedFailure() async throws {
    let fixture = try await SettingsFixture()
    defer { fixture.remove() }
    let resources = fixture.state.appendingPathComponent("fixture")
    let player = resources.appendingPathComponent("player")
    try FileManager.default.createDirectory(at: player, withIntermediateDirectories: true)
    let script = """
    import json
    from pathlib import Path
    import sys
    import time
    first = Path(sys.argv[3]) / "first"
    second = first.with_name("second")
    if first.exists():
        second.touch()
        print(json.dumps({"ok": True, "status": "idle"}))
    else:
        first.touch()
        deadline = time.monotonic() + 3
        while not second.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        print(json.dumps({"ok": False, "status": "error", "failure_id": "consumed",
                          "message": "Display refresh rate is unavailable."}))
        sys.exit(1)
    """
    try Data(script.utf8).write(to: player.appendingPathComponent("application.py"))
    let client = BackendClient(python: fixture.client.python, resources: resources, stateDirectory: fixture.state)
    let model = AppModel(backend: client)
    let polling = Task { await model.refreshStatus() }
    for _ in 0..<100 where !FileManager.default.fileExists(atPath: fixture.state.appendingPathComponent("first").path) {
        try await Task.sleep(for: .milliseconds(20))
    }
    await model.refreshStatus()
    await polling.value
    #expect(model.interpolationFailure?.id == "consumed")
    #expect(model.showInterpolationFailure)
}

@MainActor private struct SettingsFixture {
    let client: BackendClient
    let model: AppModel
    let state: URL

    init() async throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        state = FileManager.default.temporaryDirectory.appendingPathComponent("open-svpflow-lifecycle-" + UUID().uuidString)
        client = BackendClient(python: RuntimeDiscovery.python() ?? URL(fileURLWithPath: "/usr/bin/python3"), resources: root, stateDirectory: state)
        let loaded = try await client.call("settings-get")
        model = AppModel(resources: root, stateDirectory: state, backend: client)
        model.document = try #require(loaded["document"])
    }

    func remove() {
        try? FileManager.default.removeItem(at: state)
    }
}
