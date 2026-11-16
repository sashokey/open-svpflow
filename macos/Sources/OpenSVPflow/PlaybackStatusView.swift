import SwiftUI
import OpenSVPflowCore

struct PlaybackStatus: Equatable {
    enum Quality: String {
        case checking, excellent, good, attention
    }

    let title: String?
    let paused: Bool
    let interpolation: Bool?
    let targetFPS: Double?
    let quality: Quality
    let outputDrops: Int?
    let decoderDrops: Int?
    let displayDelays: Int?
    let avOffsetMilliseconds: Double?
    let windowSeconds: Int?
    let displayLimited: Bool

    init?(_ value: JSONValue) {
        guard case .object(let fields) = value, !fields.isEmpty else { return nil }
        let title = value["title"]?.string?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.title = title?.isEmpty == false ? title : nil
        paused = value["paused"]?.boolean == true
        interpolation = value["interpolation"]?.boolean
        targetFPS = Self.finite(value["target_fps"]?.number).flatMap { $0 > 0 ? $0 : nil }
        quality = value["quality"]?.string.flatMap(Quality.init(rawValue:)) ?? .checking
        outputDrops = Self.count(value["output_drops"])
        decoderDrops = Self.count(value["decoder_drops"])
        displayDelays = Self.count(value["display_delays"])
        avOffsetMilliseconds = Self.finite(value["av_offset_ms"]?.number)
        windowSeconds = Self.count(value["window_seconds"]).flatMap { $0 > 0 ? $0 : nil }
        displayLimited = value["display_limited"]?.boolean == true
    }

    var hasMetrics: Bool {
        outputDrops != nil || decoderDrops != nil || displayDelays != nil || avOffsetMilliseconds != nil
    }

    private static func count(_ value: JSONValue?) -> Int? {
        guard let number = value?.number, number >= 0 else { return nil }
        return Int(exactly: number)
    }

    private static func finite(_ value: Double?) -> Double? {
        guard let value, value.isFinite else { return nil }
        return value
    }
}

struct PlaybackStatusView: View {
    let status: PlaybackStatus?
    let adjust: () -> Void
    @Environment(\.locale) private var locale

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .center, spacing: 14) {
                Image(systemName: "film")
                    .font(.title2)
                    .foregroundStyle(.tint)
                    .padding(12)
                    .background(.tint.opacity(0.12), in: .rect(cornerRadius: 12))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    if let status {
                        Text(AppText(status.paused ? "Paused" : "Playing"))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Text(status?.title ?? AppText("Now Playing"))
                        .font(.title2.weight(.semibold))
                        .lineLimit(2)
                        .accessibilityAddTraits(.isHeader)
                    let summary = interpolationSummary
                    if !summary.isEmpty {
                        Text(summary).font(.callout).foregroundStyle(.secondary)
                    }
                }
            }
            healthPanel
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var interpolationSummary: String {
        guard let status else { return "" }
        let mode: String? = status.interpolation.map { AppText($0 ? "Interpolation On" : "Original Playback") }
        let target = status.targetFPS.map { String(format: AppText("Target: %@ FPS"), decimal($0)) }
        return [mode, target].compactMap { $0 }.joined(separator: " · ")
    }

    private var healthPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let status, !status.paused {
                HStack(alignment: .top, spacing: 9) {
                    Image(systemName: qualityIcon(status.quality))
                        .foregroundStyle(qualityColor(status.quality))
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(qualityTitle(status.quality)).font(.headline)
                        Text(qualityMessage(status.quality)).foregroundStyle(.secondary)
                    }
                }
                if status.hasMetrics {
                    DisclosureGroup(AppText("Details")) {
                        VStack(alignment: .leading, spacing: 5) {
                            if let count = status.outputDrops {
                                LabeledContent(AppText("Output Drops"), value: integer(count))
                            }
                            if let count = status.decoderDrops {
                                LabeledContent(AppText("Decoder Drops"), value: integer(count))
                            }
                            if let count = status.displayDelays {
                                LabeledContent(AppText("Display Delays"), value: integer(count))
                            }
                            if let offset = status.avOffsetMilliseconds {
                                LabeledContent(AppText("Audio/Video Offset"), value: decimal(offset) + " ms")
                            }
                            if let seconds = status.windowSeconds {
                                Text(String(format: AppText("Recent Playback: %@ s"), integer(seconds)))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.top, 6)
                    }
                }
                if status.quality == .attention {
                    Divider()
                    Text(AppText(status.displayLimited
                        ? "The video frame rate exceeds the display refresh rate. Try a lower target frame rate."
                        : "Try a lower target frame rate or lighter interpolation settings."))
                    Button(AppText("Adjust Settings…"), action: adjust)
                }
            } else {
                Text(AppText(status?.paused == true
                    ? "Quality monitoring resumes with playback."
                    : "Playback details are unavailable."))
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(15)
        .background(Color.secondary.opacity(0.08), in: .rect(cornerRadius: 12))
    }

    private func qualityTitle(_ quality: PlaybackStatus.Quality) -> String {
        let key = switch quality {
        case .checking: "Checking…"
        case .excellent: "Excellent"
        case .good: "Good"
        case .attention: "Needs Attention"
        }
        return AppText(key)
    }

    private func qualityMessage(_ quality: PlaybackStatus.Quality) -> String {
        let key = switch quality {
        case .checking: "Collecting playback statistics."
        case .excellent: "No recent playback issues detected."
        case .good: "Occasional playback issues detected."
        case .attention: "Repeated playback issues detected."
        }
        return AppText(key)
    }

    private func qualityIcon(_ quality: PlaybackStatus.Quality) -> String {
        switch quality {
        case .checking: "ellipsis.circle"
        case .excellent, .good: "checkmark.circle.fill"
        case .attention: "exclamationmark.circle.fill"
        }
    }

    private func qualityColor(_ quality: PlaybackStatus.Quality) -> Color {
        switch quality {
        case .checking: .secondary
        case .excellent: .green
        case .good: .yellow
        case .attention: .orange
        }
    }

    private func integer(_ value: Int) -> String {
        value.formatted(.number.locale(locale))
    }

    private func decimal(_ value: Double) -> String {
        value.formatted(.number.precision(.fractionLength(0...2)).locale(locale))
    }
}
