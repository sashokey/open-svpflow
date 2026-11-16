import AppKit
import Observation
import OpenSVPflowCore

@MainActor
@Observable
final class AppModel {
    enum Phase { case checking, setup, ready }
    var phase: Phase = .checking
    var checking = false
    var busy = false
    var saving = false
    var needsHomebrew = false
    var needsDependencies = false
    var setupMessage = AppText("Checking playback components…")
    var errorMessage: String?
    var showError = false
    var showAsk = false
    var document: JSONValue = .object([:])
    var descriptors: [SettingDescriptor] = []
    var limitations: [String] = []
    var settingsGroup = "Interpolation"
    var revealTargetRate = false
    private(set) var playbackStatus: PlaybackStatus?
    var presentation = PlaybackPresentation()
    var performEffect: ((PresentationEffect) -> Void)?
    var showMainWindow: (() -> Void)?
    private var client: BackendClient?
    private var pendingAsk: [String] = []
    private var statusTask: Task<Void, Never>?
    private var saveTask: Task<Bool, Never>?
    private var revision = 0
    private var savedRevision = 0
    private var lastSessionError: String?
    private var statusGeneration = 0
    private var acceptedFixedFPS: JSONValue?
    let resources: URL
    let stateDirectory: URL

    init(resources: URL? = nil, stateDirectory: URL? = nil, backend: BackendClient? = nil) {
        self.resources = resources ?? Bundle.main.resourceURL ?? Bundle.main.bundleURL
        self.stateDirectory = stateDirectory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Open SVPflow", isDirectory: true)
        client = backend
    }

    var playbackActive: Bool { presentation.playbackActive }
    var hasUnsavedChanges: Bool { savedRevision != revision }
    var playbackFields: [SettingDescriptor] {
        let rate = document.value(at: "playback.rate")?.string
        return descriptors.filter { field in
            field.group == "Playback" && (field.path != "playback.fps" || rate == "fixed")
        }
    }

    func prepare() async {
        guard !checking else { return }
        checking = true
        defer { checking = false }
        presentation.setReady(false)
        needsHomebrew = RuntimeDiscovery.brew() == nil
        guard !needsHomebrew else {
            phase = .setup
            needsDependencies = true
            setupMessage = AppText("Install Homebrew to prepare the external playback components.")
            return
        }
        guard let python = RuntimeDiscovery.python() else {
            phase = .setup
            needsDependencies = true
            setupMessage = AppText("Install the external playback components before opening a movie.")
            return
        }
        let backend = BackendClient(python: python, resources: resources, stateDirectory: stateDirectory)
        client = backend
        do {
            let check = try await backend.call("check", timeout: 90)
            guard check["ready"]?.boolean == true else {
                phase = .setup
                needsHomebrew = check["needs_homebrew"]?.boolean ?? false
                needsDependencies = check["needs_dependencies"]?.boolean ?? true
                setupMessage = AppDiagnostic(check["message"]?.string ?? "Some playback components need attention.")
                return
            }
            let description = try await backend.call("describe")
            guard let schema = description["description"] else { throw ProcessFailure.backend("The settings description is missing.") }
            descriptors = try schema["fields"]?.array.map(SettingDescriptor.init) ?? []
            limitations = schema["limitations"]?.array.compactMap(\.string) ?? []
            let settings = try await backend.call("settings-get")
            guard let loaded = settings["document"] else { throw ProcessFailure.backend("The settings document is missing.") }
            document = loaded
            acceptedFixedFPS = loaded.value(at: "playback.fps")
            revision = 0
            savedRevision = 0
            phase = .ready
            presentation.setReady(true)
            await refreshStatus()
            startStatusUpdates()
            if !showAsk {
                let files = presentation.takeReadyFiles()
                if !files.isEmpty { await open(files) }
            }
        } catch {
            phase = .setup
            needsDependencies = false
            setupMessage = AppDiagnostic(error.localizedDescription)
        }
    }

    func install(_ kind: String) {
        do {
            let script = try InstallerTemplate.prepare(kind: kind, resources: resources, stateDirectory: stateDirectory)
            let configuration = NSWorkspace.OpenConfiguration()
            configuration.activates = true
            NSWorkspace.shared.open([script], withApplicationAt: URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app"), configuration: configuration) { _, error in
                if let error { Task { @MainActor in self.present(error.localizedDescription) } }
            }
            setupMessage = AppText("Complete the installation in Terminal, then return here to check again. Homebrew may update required related packages.")
        } catch { present(error.localizedDescription) }
    }

    func enqueue(_ urls: [URL]) {
        let files = urls.filter(\.isFileURL).map(\.path)
        guard !files.isEmpty else { return }
        presentation.enqueue(files)
        if phase == .ready && !busy && !showAsk {
            let pending = presentation.takeReadyFiles()
            Task { await open(pending) }
        }
    }

    func openPanel() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.prompt = AppText("Open")
        panel.begin { response in
            if response == .OK { self.enqueue(panel.urls) }
        }
    }

    func openLanguageSettings() {
        if let languagePane = URL(string: "x-apple.systempreferences:com.apple.Localization-Settings.extension"),
           NSWorkspace.shared.open(languagePane) { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/System Settings.app"))
    }

    func edit(_ path: String, value: JSONValue) {
        do {
            if acceptedFixedFPS == nil { acceptedFixedFPS = document.value(at: "playback.fps") }
            try document.set(value, at: path)
            revision += 1
            let previous = saveTask
            let snapshot = document
            let currentRevision = revision
            let fixedFPSIsHidden = path != "playback.fps" && snapshot.value(at: "playback.rate") != .string("fixed")
            saving = true
            saveTask = Task {
                _ = await previous?.value
                try? await Task.sleep(for: .milliseconds(250))
                guard currentRevision == self.revision else { return true }
                return await self.persist(snapshot, revision: currentRevision, fixedFPSIsHidden: fixedFPSIsHidden)
            }
        } catch { present("This setting could not be changed.") }
    }

    func flushSettings() async -> Bool {
        if let saveTask { return await saveTask.value }
        return true
    }

    func prepareToClose() async -> Bool {
        if !pendingAsk.isEmpty {
            let files = pendingAsk
            pendingAsk = []
            showAsk = false
            await open(files, action: "cancel")
        }
        _ = await flushSettings()
        return true
    }

    private func persist(_ snapshot: JSONValue, revision currentRevision: Int, fixedFPSIsHidden: Bool) async -> Bool {
        guard let client else { return false }
        do {
            let result: JSONValue
            do {
                result = try await client.call("settings-save", request: .object(["document": snapshot]))
            } catch ProcessFailure.backend(let message) {
                guard fixedFPSIsHidden,
                      ["Invalid target frame rate", "Target frame rate exceeds native timing fields", "Fixed FPS must be a rational string"].contains(message),
                      let acceptedFixedFPS,
                      acceptedFixedFPS != snapshot.value(at: "playback.fps") else { throw ProcessFailure.backend(message) }
                var repaired = snapshot
                try repaired.set(acceptedFixedFPS, at: "playback.fps")
                result = try await client.call("settings-save", request: .object(["document": repaired]))
            }
            let saved = result["document"]
            if let fps = saved?.value(at: "playback.fps") { acceptedFixedFPS = fps }
            if currentRevision == revision {
                if let saved { document = saved }
                savedRevision = currentRevision
                saving = false
            }
            return true
        } catch {
            saving = false
            present(error.localizedDescription)
            return false
        }
    }

    func resolveAsk(_ action: String) {
        let files = pendingAsk
        pendingAsk = []
        showAsk = false
        Task { await open(files, action: action) }
    }

    func cancelDismissedAsk() {
        if !pendingAsk.isEmpty { resolveAsk("cancel") }
    }

    private func open(_ files: [String], action: String = "auto") async {
        guard let client, !busy else {
            presentation.enqueue(files)
            return
        }
        guard !files.isEmpty || action == "cancel" else { return }
        statusGeneration &+= 1
        busy = true
        defer {
            busy = false
            if !showAsk {
                let pending = presentation.takeReadyFiles()
                if !pending.isEmpty { Task { await self.open(pending) } }
            }
        }
        guard await flushSettings() else { return }
        do {
            let result = try await client.call("open", request: .object(["files": .array(files.map(JSONValue.string)), "action": .string(action)]), timeout: 60)
            let pending = presentation.takeReadyFiles()
            presentation.enqueue(pending)
            handle(result, userInitiated: action != "cancel" && pending.isEmpty, fallbackFiles: files)
        } catch { present(error.localizedDescription) }
    }

    func refreshStatus() async {
        guard let client, !busy else { return }
        statusGeneration &+= 1
        let generation = statusGeneration
        do {
            let result = try await client.call("status", timeout: 5)
            guard generation == statusGeneration, !busy else { return }
            handle(result, userInitiated: false)
        } catch {
            if generation == statusGeneration, !busy, phase == .ready { present(error.localizedDescription) }
        }
    }

    private func handle(_ result: JSONValue, userInitiated: Bool, fallbackFiles: [String] = []) {
        let status = result["status"]?.string ?? "idle"
        if let reported = result["playback"].flatMap(PlaybackStatus.init) {
            playbackStatus = reported
        } else if status != "ask" && status != "queued" {
            playbackStatus = nil
        }
        if status == "ask" {
            if playbackStatus != nil {
                _ = presentation.handle(status: "started", afterStart: "keep_open", userInitiated: false)
            }
            if !showAsk {
                pendingAsk = result["files"]?.array.compactMap(\.string) ?? fallbackFiles
                if !pendingAsk.isEmpty {
                    showMainWindow?()
                    showAsk = true
                }
            }
            return
        }
        if status == "error" {
            let message = result["message"]?.string ?? "Playback could not continue."
            if lastSessionError != message {
                lastSessionError = message
                present(message)
            }
            _ = presentation.handle(status: status, afterStart: "keep_open", userInitiated: false)
            return
        }
        lastSessionError = nil
        let after = result["after_start"]?.string ?? document.value(at: "preferences.after_start")?.string ?? "quit_app"
        let effect = presentation.handle(status: status, afterStart: after, userInitiated: userInitiated)
        if effect != .none { performEffect?(effect) }
    }

    private func startStatusUpdates() {
        statusTask?.cancel()
        statusTask = Task { [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(2)) }
                catch { break }
                guard let self else { break }
                if !self.showAsk { await self.refreshStatus() }
            }
        }
    }

    func present(_ message: String) {
        errorMessage = AppDiagnostic(message)
        showError = true
        showMainWindow?()
    }
}
