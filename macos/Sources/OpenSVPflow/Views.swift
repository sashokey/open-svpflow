import SwiftUI
import OpenSVPflowCore

struct MainView: View {
    @Bindable var model: AppModel
    @State private var targeted = false

    var body: some View {
        Group {
            if model.phase == .ready { playbackView }
            else { setupView }
        }
        .frame(minWidth: 460, minHeight: 350)
        .padding(28)
        .alert("Unable to Continue", isPresented: $model.showError) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.errorMessage ?? "An unexpected error occurred.")
        }
        .alert("A Movie Is Already Playing", isPresented: $model.showAsk) {
            Button("Add to Queue") { model.resolveAsk("append") }
            Button("Replace Playlist", role: .destructive) { model.resolveAsk("replace") }
            Button("Cancel", role: .cancel) { model.resolveAsk("cancel") }
        } message: {
            Text("Add the new files to the queue or start them instead of the current movie.")
        }
        .onChange(of: model.showAsk) { _, showing in
            if !showing { model.cancelDismissedAsk() }
        }
    }

    private var playbackView: some View {
        VStack(spacing: 22) {
            VStack(spacing: 12) {
                Image(systemName: "play.rectangle.on.rectangle")
                    .font(.system(size: 52, weight: .light))
                    .foregroundStyle(.tint)
                    .accessibilityHidden(true)
                Text(model.busy ? "Opening Movie…" : "Drop Movies Here")
                    .font(.title2.weight(.semibold))
                Text("Smooth playback with your installed mpv.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Button("Open File…") { model.openPanel() }
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
            }
            .dropDestination(for: URL.self) { urls, _ in
                model.enqueue(urls)
                return urls.contains(where: \.isFileURL)
            } isTargeted: { targeted = $0 }

            VStack(spacing: 10) {
                ForEach(model.descriptors.filter { $0.group == "Playback" }) { field in
                    SettingRow(field: field, model: model)
                }
            }
            HStack {
                if model.playbackActive {
                    Label("Connected to Playback", systemImage: "checkmark.circle")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                SettingsLink { Label("Settings…", systemImage: "slider.horizontal.3") }
                    .buttonStyle(.borderless)
            }
        }
    }

    private var setupView: some View {
        VStack(spacing: 20) {
            Image(systemName: model.checking ? "play.rectangle" : "shippingbox")
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(.tint)
                .accessibilityHidden(true)
            Text(model.checking ? "Preparing Playback" : "Set Up Playback")
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
                        Button(model.needsHomebrew ? "Install Homebrew…" : "Install Dependencies…") {
                            model.install(model.needsHomebrew ? "homebrew" : "dependencies")
                        }
                        .buttonStyle(.glassProminent)
                        .controlSize(.large)
                        Text("Installation uses the internet and may update required related Homebrew packages. System password prompts appear in Terminal.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    Button("Retry") { Task { await model.prepare() } }
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
                TabView {
                    ForEach(groups, id: \.self) { group in
                        Form {
                            Section {
                                ForEach(model.descriptors.filter { $0.group == group }) { field in
                                    SettingRow(field: field, model: model)
                                }
                            }
                            if group == "Advanced" && !model.limitations.isEmpty {
                                Section("Supported Features") {
                                    ForEach(model.limitations, id: \.self) { limitation in
                                        Text(limitation).font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                        .formStyle(.grouped)
                        .tabItem { Label(group, systemImage: symbol(group)) }
                    }
                }
                .safeAreaInset(edge: .bottom) {
                    HStack {
                        Text(model.saving ? "Saving…" : "Changes apply to the next opened movie.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Spacer()
                    }
                    .padding()
                }
            } else {
                ContentUnavailableView("Playback Is Not Ready", systemImage: "shippingbox", description: Text("Complete setup in the main window before changing playback settings."))
            }
        }
        .frame(width: 650, height: 560)
        .alert("Unable to Save Settings", isPresented: $model.showError) {
            Button("OK", role: .cancel) {}
        } message: { Text(model.errorMessage ?? "The settings could not be saved.") }
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

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            control.disabled(!field.supported || model.busy)
            if !field.help.isEmpty {
                Text(field.help).font(.caption).foregroundStyle(.secondary)
            }
        }
        .help(field.help)
    }

    @ViewBuilder private var control: some View {
        switch field.kind {
        case "choice":
            Picker(field.label, selection: binding) {
                ForEach(field.options) { option in Text(option.label).tag(option.value) }
                if !field.options.contains(where: { $0.value == value }) {
                    Text("Custom (\(value.text))").tag(value)
                }
            }
        case "boolean":
            Toggle(field.label, isOn: Binding(get: { value.boolean ?? false }, set: { model.edit(field.path, value: .bool($0)) }))
        case "integer":
            HStack {
                Text(field.label)
                Spacer()
                TextField(field.label, value: Binding(get: { Int(value.number ?? 0) }, set: { model.edit(field.path, value: .number(Double($0))) }), format: .number)
                    .labelsHidden().multilineTextAlignment(.trailing).frame(width: 140)
            }
        case "number":
            HStack {
                Text(field.label)
                Spacer()
                TextField(field.label, value: Binding(get: { value.number ?? 0 }, set: { model.edit(field.path, value: .number($0)) }), format: .number)
                    .labelsHidden().multilineTextAlignment(.trailing).frame(width: 140)
            }
        default:
            TextField(field.label, text: Binding(get: { value.text }, set: { model.edit(field.path, value: .string($0)) }))
        }
    }
}
