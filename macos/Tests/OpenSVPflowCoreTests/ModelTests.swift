import Foundation
import Testing
@testable import OpenSVPflowCore

@Test func editingNestedSettingPreservesUnrelatedAdvancedValues() throws {
    var document = try JSONValue.parse(Data(#"{"schema":2,"interpolation":{"shader":23,"native":{"mask":7}},"preferences":{"after_start":"quit_app"}}"#.utf8))
    try document.set(.number(21), at: "interpolation.shader")
    #expect(document.value(at: "interpolation.shader") == .number(21))
    #expect(document.value(at: "interpolation.native.mask") == .number(7))
    #expect(document.value(at: "preferences.after_start") == .string("quit_app"))
    #expect(try JSONValue.parse(document.encoded()) == document)
}

@Test func editRejectsPathCrossingScalarInsteadOfDiscardingData() throws {
    var document = try JSONValue.parse(Data(#"{"interpolation":{"shader":23}}"#.utf8))
    #expect(throws: JSONValueError.self) {
        try document.set(.number(1), at: "interpolation.shader.value")
    }
    #expect(document.value(at: "interpolation.shader") == .number(23))
}

@Test func pendingFinderFilesSurviveDependencyFailureInOrder() {
    var state = PlaybackPresentation()
    state.enqueue(["/Movies/One film.mkv", "/Movies/Другой фильм.mkv"])
    state.setReady(false)
    #expect(state.takeReadyFiles().isEmpty)
    state.enqueue(["/Movies/Third.mkv"])
    state.setReady(true)
    #expect(state.takeReadyFiles() == ["/Movies/One film.mkv", "/Movies/Другой фильм.mkv", "/Movies/Third.mkv"])
    #expect(state.takeReadyFiles().isEmpty)
}

@Test func quitAppSuccessRequestsProcessTerminationOnlyAfterConfirmedPlayback() {
    var state = PlaybackPresentation()
    #expect(state.handle(status: "starting", afterStart: "quit_app", userInitiated: true) == .none)
    #expect(state.handle(status: "started", afterStart: "quit_app", userInitiated: true) == .terminateGUI)
    #expect(state.playbackActive)
}

@Test func menuBarAndOpenWindowHaveDifferentPresentationEffects() {
    var menu = PlaybackPresentation()
    #expect(menu.handle(status: "started", afterStart: "menu_bar", userInitiated: true) == .showMenuBar)
    #expect(menu.menuBarActive)
    #expect(menu.closeWindow() == .hideWindow)
    var window = PlaybackPresentation()
    #expect(window.handle(status: "started", afterStart: "keep_open", userInitiated: true) == .keepWindow)
    #expect(!window.menuBarActive)
    #expect(window.closeWindow() == .terminateGUI)
}

@Test func naturalPlaylistAdvanceDoesNotDismissSettings() {
    var state = PlaybackPresentation()
    #expect(state.handle(status: "started", afterStart: "quit_app", userInitiated: false) == .none)
    #expect(state.playbackActive)
}

@Test func queueQuestionDoesNotApplyPostStartBehavior() {
    var state = PlaybackPresentation()
    #expect(state.handle(status: "ask", afterStart: "quit_app", userInitiated: true) == .none)
    #expect(!state.playbackActive)
    #expect(state.handle(status: "queued", afterStart: "keep_open", userInitiated: true) == .keepWindow)
    #expect(state.playbackActive)
}

@Test func backendFailureAlwaysShowsErrorWithoutQuittingGUI() {
    var state = PlaybackPresentation()
    _ = state.handle(status: "started", afterStart: "menu_bar", userInitiated: true)
    #expect(state.handle(status: "error", afterStart: "quit_app", userInitiated: false) == .showError)
    #expect(!state.playbackActive)
}

@Test func descriptorsPreserveTypedChoiceValuesFromPython() throws {
    let value = try JSONValue.parse(Data(#"{"path":"interpolation.shader","label":"SVP shader","kind":"choice","group":"Interpolation","supported":true,"options":[{"value":23,"label":"Complicated"},{"value":21,"label":"Simple"}]}"#.utf8))
    let descriptor = try SettingDescriptor(value)
    #expect(descriptor.id == "interpolation.shader")
    #expect(descriptor.options.map(\.value) == [.number(23), .number(21)])
    #expect(descriptor.supported)
}
