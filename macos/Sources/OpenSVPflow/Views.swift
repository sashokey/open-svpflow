import SwiftUI
import OpenSVPflowCore

struct MainView: View {
    @Bindable var model: AppModel
    var registerSettingsAction: ((OpenSettingsAction) -> Void)?
    @State private var targeted = false
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        Group {
            if model.phase == .ready { playbackView }
            else { setupView }
        }
        .frame(minWidth: 460, minHeight: 350)
        .padding(28)
        .alert(AppText("Unable to Continue"), isPresented: $model.showError) {
            Button(AppText("OK"), role: .cancel) {}
        } message: {
            Text(model.errorMessage ?? AppText("An unexpected error occurred."))
        }
        .alert(AppText("A Movie Is Already Playing"), isPresented: $model.showAsk) {
            Button(AppText("Add to Queue")) { model.resolveAsk("append") }
            Button(AppText("Replace Playlist"), role: .destructive) { model.resolveAsk("replace") }
            Button(AppText("Cancel"), role: .cancel) { model.resolveAsk("cancel") }
        } message: {
            Text(AppText("Add the new files to the queue or start them instead of the current movie."))
        }
        .onChange(of: model.showAsk) { _, showing in
            if !showing { model.cancelDismissedAsk() }
        }
        .onAppear { registerSettingsAction?(openSettings) }
    }

    private var playbackView: some View {
        VStack(spacing: 22) {
            if model.playbackActive {
                PlaybackStatusView(status: model.playbackStatus) {
                    model.settingsGroup = "Interpolation"
                    model.revealTargetRate = true
                    openSettings()
                }
                .overlay {
                    if targeted {
                        RoundedRectangle(cornerRadius: 12)
                            .strokeBorder(Color.accentColor, lineWidth: 2)
                            .allowsHitTesting(false)
                    }
                }
            } else {
                openArea
                VStack(spacing: 10) {
                    ForEach(model.playbackFields) { field in
                        SettingRow(field: field, model: model)
                    }
                }
            }
            HStack {
                if model.playbackActive {
                    Button(AppText("Open File…"), systemImage: "folder") { model.openPanel() }
                        .disabled(model.busy)
                    if model.busy {
                        ProgressView().controlSize(.small)
                            .accessibilityLabel(AppText("Opening Movie…"))
                    }
                }
                Spacer()
                SettingsLink { Label(AppText("Settings…"), systemImage: "slider.horizontal.3") }
                    .buttonStyle(.borderless)
            }
        }
        .contentShape(Rectangle())
        .dropDestination(for: URL.self) { urls, _ in
            model.enqueue(urls)
            return urls.contains(where: \.isFileURL)
        } isTargeted: { targeted = $0 }
    }

    private var openArea: some View {
        VStack(spacing: 12) {
            Image(systemName: "play.rectangle.on.rectangle")
                .font(.system(size: 52, weight: .light))
                .foregroundStyle(.tint)
                .accessibilityHidden(true)
            Text(AppText(model.busy ? "Opening Movie…" : "Drop Movies Here"))
                .font(.title2.weight(.semibold))
            Text(AppText("Smooth playback with your installed mpv."))
                .font(.callout)
                .foregroundStyle(.secondary)
            Button(AppText("Open File…")) { model.openPanel() }
                .buttonStyle(.glassProminent)
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)
                .disabled(model.busy)
            if model.busy { ProgressView().controlSize(.small) }
        }
        .frame(maxWidth: .infinity, minHeight: 200)
        .background(targeted ? Color.accentColor.opacity(0.08) : Color.clear, in: .rect(cornerRadius: 18))
        .overlay {
            RoundedRectangle(cornerRadius: 18)
                .strokeBorder(targeted ? Color.accentColor : Color.secondary.opacity(0.25), style: StrokeStyle(lineWidth: 1, dash: [6, 4]))
                .allowsHitTesting(false)
        }
    }

    private var setupView: some View {
        VStack(spacing: 20) {
            Image(systemName: model.checking ? "play.rectangle" : "shippingbox")
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(.tint)
                .accessibilityHidden(true)
            Text(AppText(model.checking ? "Preparing Playback" : "Set Up Playback"))
                .font(.title2.weight(.semibold))
            Text(model.setupMessage)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .textSelection(.enabled)
            if model.checking {
                ProgressView()
            } else {
                VStack(spacing: 12) {
                    if model.needsHomebrew || model.needsDependencies {
                        Button(AppText(model.needsHomebrew ? "Install Homebrew…" : "Install Dependencies…")) {
                            model.install(model.needsHomebrew ? "homebrew" : "dependencies")
                        }
                        .buttonStyle(.glassProminent)
                        .controlSize(.large)
                        Text(AppText("Installation uses the internet and may update required related Homebrew packages. System password prompts appear in Terminal."))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    Button(AppText("Retry")) { Task { await model.prepare() } }
                }
            }
        }
        .frame(maxWidth: 410)
    }
}

struct SettingsView: View {
    @Bindable var model: AppModel

    private let groups = ["Interpolation", "Advanced", "Behavior"]

    var body: some View {
        Group {
            if model.phase == .ready {
                TabView(selection: $model.settingsGroup) {
                    ForEach(groups, id: \.self) { group in
                        settingsPane(group)
                            .tabItem { Label(AppText(group), systemImage: symbol(group)) }
                            .tag(group)
                    }
                }
                .safeAreaInset(edge: .bottom) {
                    HStack {
                        Text(AppText(model.saving ? "Saving…" : "Changes apply to the next opened movie."))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Spacer()
                    }
                    .padding()
                }
            } else {
                ContentUnavailableView(AppText("Playback Is Not Ready"), systemImage: "shippingbox", description: Text(AppText("Complete setup in the main window before changing playback settings.")))
            }
        }
        .frame(width: 650, height: 560)
        .alert(AppText("Unable to Save Settings"), isPresented: $model.showError) {
            Button(AppText("OK"), role: .cancel) {}
        } message: { Text(model.errorMessage ?? AppText("The settings could not be saved.")) }
    }

    private func settingsPane(_ group: String) -> some View {
        ScrollViewReader { proxy in
            Form {
                if group == "Interpolation" {
                    Section(AppText("Playback")) {
                        ForEach(model.playbackFields) { field in
                            SettingRow(field: field, model: model)
                                .id(field.path)
                        }
                    }
                }
                Section {
                    ForEach(model.descriptors.filter { $0.group == group }) { field in
                        SettingRow(field: field, model: model)
                    }
                }
                if group == "Behavior" {
                    LabeledContent(AppText("App Language")) {
                        Button(AppText("Change App Language…"), action: model.openLanguageSettings)
                    }
                }
                if group == "Advanced" && !model.limitations.isEmpty {
                    Section(AppText("Supported Features")) {
                        ForEach(model.limitations, id: \.self) { limitation in
                            Text(AppText(limitation)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .formStyle(.grouped)
            .onChange(of: model.revealTargetRate, initial: true) { _, reveal in
                guard group == "Interpolation", reveal else { return }
                Task { @MainActor in
                    proxy.scrollTo("playback.rate", anchor: .top)
                    model.revealTargetRate = false
                }
            }
        }
    }

    private func symbol(_ group: String) -> String {
        switch group {
        case "Interpolation": "film.stack"
        case "Advanced": "slider.horizontal.3"
        default: "app.badge"
        }
    }
}

struct SettingRow: View {
    let field: SettingDescriptor
    @Bindable var model: AppModel

    private var value: JSONValue { model.document.value(at: field.path) ?? .null }
    private var binding: Binding<JSONValue> {
        Binding(get: { value }, set: { model.edit(field.path, value: $0) })
    }
    private var label: String { AppText(field.label) }
    private var help: String { AppText(field.help) }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            control.disabled(!field.supported || model.busy)
            if !field.help.isEmpty {
                Text(help).font(.caption).foregroundStyle(.secondary)
            }
        }
        .help(help)
    }

    @ViewBuilder private var control: some View {
        switch field.kind {
        case "choice":
            Picker(label, selection: binding) {
                ForEach(field.options) { option in Text(AppText(option.label)).tag(option.value) }
                if !field.options.contains(where: { $0.value == value }) {
                    Text(String(format: AppText("Custom (%@)"), value.text)).tag(value)
                }
            }
        case "boolean":
            Toggle(label, isOn: Binding(get: { value.boolean ?? false }, set: { model.edit(field.path, value: .bool($0)) }))
        case "integer":
            HStack {
                Text(label)
                Spacer()
                TextField(label, value: Binding(get: { Int(value.number ?? 0) }, set: { model.edit(field.path, value: .number(Double($0))) }), format: .number)
                    .labelsHidden().multilineTextAlignment(.trailing).frame(width: 140)
            }
        case "number":
            HStack {
                Text(label)
                Spacer()
                TextField(label, value: Binding(get: { value.number ?? 0 }, set: { model.edit(field.path, value: .number($0)) }), format: .number)
                    .labelsHidden().multilineTextAlignment(.trailing).frame(width: 140)
            }
        default:
            TextField(label, text: Binding(get: { value.text }, set: { model.edit(field.path, value: .string($0)) }))
        }
    }
}
