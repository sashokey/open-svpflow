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
