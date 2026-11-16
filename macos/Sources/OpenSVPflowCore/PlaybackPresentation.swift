import Foundation

public enum PresentationEffect: Equatable, Sendable {
    case none
    case terminateGUI
    case showMenuBar
    case keepWindow
    case hideWindow
    case showError
}

public struct PlaybackPresentation: Sendable {
    public private(set) var ready = false
    public private(set) var playbackActive = false
    public private(set) var menuBarActive = false
    private var pendingFiles: [String] = []

    public init() {}

    public mutating func enqueue(_ files: [String]) {
        pendingFiles.append(contentsOf: files)
    }

    public mutating func setReady(_ value: Bool) {
        ready = value
    }

    public mutating func takeReadyFiles() -> [String] {
        guard ready else { return [] }
        let files = pendingFiles
        pendingFiles.removeAll()
        return files
    }

    public mutating func handle(status: String, afterStart: String, userInitiated: Bool) -> PresentationEffect {
        if status == "error" {
            playbackActive = false
            return .showError
        }
        if status == "idle" {
            playbackActive = false
            return .none
        }
        guard status == "started" || status == "queued" else { return .none }
        playbackActive = true
        guard userInitiated else { return .none }
        switch afterStart {
        case "menu_bar": menuBarActive = true; return .showMenuBar
        case "keep_open": menuBarActive = false; return .keepWindow
        default: menuBarActive = false; return .terminateGUI
        }
    }

    public func closeWindow() -> PresentationEffect {
        menuBarActive ? .hideWindow : .terminateGUI
    }
}
