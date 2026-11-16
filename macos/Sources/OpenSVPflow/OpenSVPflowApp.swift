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
            MainView(model: delegate.model, registerSettingsAction: delegate.registerSettingsAction)
                .environment(\.locale, Locale.current)
                .environment(\.layoutDirection, appLayoutDirection)
                .onAppear { delegate.connectWindow() }
        }
        .defaultSize(width: 540, height: 420)
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
    }
}

@MainActor
final class ApplicationDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let model = AppModel()
    private weak var mainWindow: NSWindow?
    private var statusItem: NSStatusItem?
    private var settingsAction: OpenSettingsAction?

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
        return false
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func registerSettingsAction(_ action: OpenSettingsAction) {
        settingsAction = action
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
        NSApplication.shared.setActivationPolicy(model.presentation.menuBarActive ? .accessory : .regular)
        connectWindow()
        mainWindow?.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    private func showStatusItem() {
        guard statusItem == nil else { return }
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        let icon = NSImage(named: "StatusIconTemplate")
            ?? NSImage(systemSymbolName: "play.rectangle", accessibilityDescription: "Open SVPflow")
        icon?.size = NSSize(width: 18, height: 18)
        icon?.isTemplate = true
        item.button?.image = icon
        item.button?.setAccessibilityLabel("Open SVPflow")

        let menu = NSMenu()
        menu.addItem(statusMenuItem("Show Window", action: #selector(showStatusWindow)))
        menu.addItem(statusMenuItem("Open File…", action: #selector(openStatusFile), keyEquivalent: "o"))
        menu.addItem(statusMenuItem("Settings…", action: #selector(showStatusSettings), keyEquivalent: ","))
        menu.addItem(.separator())
        menu.addItem(statusMenuItem("Quit", action: #selector(quitFromStatusMenu), keyEquivalent: "q"))
        item.menu = menu
        statusItem = item
    }

    private func statusMenuItem(_ title: String, action: Selector, keyEquivalent: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: AppText(title), action: action, keyEquivalent: keyEquivalent)
        item.target = self
        return item
    }

    private func removeStatusItem() {
        if let statusItem {
            NSStatusBar.system.removeStatusItem(statusItem)
            self.statusItem = nil
        }
    }

    @objc private func showStatusWindow() {
        showMain()
        Task { await model.refreshStatus() }
    }

    @objc private func openStatusFile() {
        NSApplication.shared.activate(ignoringOtherApps: true)
        model.openPanel()
    }

    @objc private func showStatusSettings() {
        NSApplication.shared.activate(ignoringOtherApps: true)
        if let settingsAction { settingsAction() }
        else { showMain() }
    }

    @objc private func quitFromStatusMenu() {
        NSApplication.shared.terminate(nil)
    }

    private func apply(_ effect: PresentationEffect) {
        switch effect {
        case .terminateGUI: NSApplication.shared.terminate(nil)
        case .showMenuBar:
            let application = NSApplication.shared
            let statusWindow = statusItem?.button?.window
            let contentWindows = application.windows.filter { $0.isVisible && $0 !== statusWindow }
            showStatusItem()
            application.setActivationPolicy(.accessory)
            for window in contentWindows { window.orderOut(nil) }
        case .keepWindow:
            removeStatusItem()
            showMain()
        case .showError: showMain()
        case .hideWindow: mainWindow?.orderOut(nil)
        case .none: break
        }
    }
}
