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
        VStack(alignment: .leading, spacing: 22) {
            HStack(alignment: .center, spacing: 14) {
                Image(systemName: "film")
                    .font(.system(size: 22, weight: .light))
                    .foregroundStyle(.tint)
                    .frame(width: 46, height: 46)
                    .background(.tint.opacity(0.12), in: .rect(cornerRadius: 12))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    if let status {
                        Text(AppText(status.paused ? "Paused" : "Playing"))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Text(status?.title ?? AppText("Now Playing"))
                        .font(.system(size: 21, weight: .semibold))
                        .lineLimit(2)
                        .accessibilityAddTraits(.isHeader)
                    let summary = interpolationSummary
                    if !summary.isEmpty {
                        Text(summary).font(.callout).foregroundStyle(.secondary)
                    }
                }
            }
            .padding(.top, 5)
            healthPanel
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var interpolationSummary: String {
        guard let status else { return "" }
        let mode = status.interpolation == true ? AppText("Interpolation On") : nil
        let target = status.targetFPS.map { String(format: AppText("Target: %@ FPS"), decimal($0)) }
        return [mode, target].compactMap { $0 }.joined(separator: " · ")
    }

    private var healthPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let status {
                HStack(alignment: .top, spacing: 10) {
                    Circle()
                        .fill(status.paused ? Color.secondary : qualityColor(status.quality))
                        .frame(width: 9, height: 9)
                        .padding(.top, 5)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(status.paused ? AppText("Paused") : qualityTitle(status.quality))
                            .font(.system(size: 15, weight: .semibold))
                        Text(status.paused ? AppText("Quality monitoring resumes with playback.") : qualityMessage(status.quality))
                            .foregroundStyle(.secondary)
                    }
                }
                if !status.paused && status.hasMetrics {
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
                if !status.paused && status.quality == .attention {
                    Divider()
                    let advice = AppText(status.displayLimited
                        ? "The video frame rate exceeds the display refresh rate. Try a lower target frame rate."
                        : "Try a lower target frame rate or lighter interpolation settings.")
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 10) {
                            Text(advice)
                            Spacer(minLength: 0)
                            Button(AppText("Adjust Settings…"), action: adjust).fixedSize()
                        }
                        VStack(alignment: .leading, spacing: 10) {
                            Text(advice)
                            Button(AppText("Adjust Settings…"), action: adjust)
                        }
                    }
                }
            } else {
                Text(AppText("Playback details are unavailable."))
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Color(nsColor: .controlBackgroundColor), in: .rect(cornerRadius: 12))
        .overlay {
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(Color.primary.opacity(0.12))
                .allowsHitTesting(false)
        }
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
