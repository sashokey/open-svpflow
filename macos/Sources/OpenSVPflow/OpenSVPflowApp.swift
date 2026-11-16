import AppKit
import SwiftUI
import OpenSVPflowCore

@main
struct OpenSVPflowApp: App {
    @NSApplicationDelegateAdaptor(ApplicationDelegate.self) private var delegate
    private var appLayoutDirection: LayoutDirection {
        let language = Bundle.main.preferredLocalizations.first ?? Locale.current.identifier
        return Locale.Language(identifier: language).characterDirection == .rightToLeft ? .rightToLeft : .leftToRight
    }

    var body: some Scene {
        Window("Open SVPflow", id: "main") {
            MainView(model: delegate.model)
                .environment(\.locale, Locale.current)
                .environment(\.layoutDirection, appLayoutDirection)
                .onAppear { delegate.connectWindow() }
        }
        .defaultSize(width: 520, height: 430)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button(AppText("Open File…")) { delegate.model.openPanel() }
                    .keyboardShortcut("o")
                    .disabled(delegate.model.phase != .ready)
            }
        }

        Settings {
            SettingsView(model: delegate.model)
                .environment(\.locale, Locale.current)
                .environment(\.layoutDirection, appLayoutDirection)
        }

        MenuBarExtra(isInserted: Binding(get: { delegate.model.menuBarInserted }, set: { delegate.model.menuBarInserted = $0 })) {
            Button(AppText("Show Window")) { delegate.showMain() }
            Button(AppText("Open File…")) { delegate.model.openPanel() }
            SettingsLink { Text(AppText("Settings…")) }
            Divider()
            Button(AppText("Quit")) { NSApplication.shared.terminate(nil) }
                .keyboardShortcut("q")
        } label: {
            Group {
                if let icon = NSImage(named: "StatusIconTemplate") {
                    Image(nsImage: icon)
                } else {
                    Image(systemName: "play.rectangle")
                }
            }
            .accessibilityLabel("Open SVPflow")
        }
    }
}

@MainActor
final class ApplicationDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let model = AppModel()
    private weak var mainWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        model.performEffect = { [weak self] effect in self?.apply(effect) }
        model.showMainWindow = { [weak self] in self?.showMain() }
        NSApplication.shared.setActivationPolicy(.regular)
        Task {
            await model.prepare()
            let arguments = ProcessInfo.processInfo.arguments
            if let index = arguments.firstIndex(of: "--show-error"), arguments.indices.contains(index + 1) {
                model.present(arguments[index + 1])
            }
        }
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        model.enqueue(urls)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMain()
        Task { await model.refreshStatus() }
        return true
    }

    func connectWindow() {
        if let window = NSApplication.shared.windows.first(where: { $0.title == "Open SVPflow" }) {
            mainWindow = window
            window.delegate = self
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        Task {
            guard await model.prepareToClose() else { return }
            if model.presentation.closeWindow() == .hideWindow {
                sender.orderOut(nil)
            } else {
                NSApplication.shared.terminate(nil)
            }
        }
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard model.hasUnsavedChanges || model.showAsk else { return .terminateNow }
        Task {
            let saved = await model.prepareToClose()
            sender.reply(toApplicationShouldTerminate: saved)
        }
        return .terminateLater
    }

    func showMain() {
        NSApplication.shared.setActivationPolicy(model.menuBarInserted ? .accessory : .regular)
        connectWindow()
        mainWindow?.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    private func apply(_ effect: PresentationEffect) {
        switch effect {
        case .terminateGUI: NSApplication.shared.terminate(nil)
        case .showMenuBar:
            for window in NSApplication.shared.windows { window.orderOut(nil) }
            NSApplication.shared.setActivationPolicy(.accessory)
        case .keepWindow: showMain()
        case .showError: showMain()
        case .hideWindow: mainWindow?.orderOut(nil)
        case .none: break
        }
    }
}
