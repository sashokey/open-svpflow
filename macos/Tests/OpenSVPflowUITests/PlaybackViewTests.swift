import OpenSVPflowCore
import Testing
@testable import OpenSVPflow

@Test @MainActor func fixedFrameRateIsEditableOnlyWhenFixedIsSelected() throws {
    let model = AppModel()
    model.descriptors = try ["playback.rate", "playback.fps"].map { path in
        try SettingDescriptor(.object([
            "path": .string(path),
            "label": .string(path),
            "kind": .string("text"),
            "group": .string("Playback")
        ]))
    }
    for (rate, paths) in [
        ("monitor", ["playback.rate"]),
        ("source_2", ["playback.rate"]),
        ("fixed", ["playback.rate", "playback.fps"]),
        ("monitor_half", ["playback.rate"])
    ] {
        try model.document.set(.string(rate), at: "playback.rate")
        #expect(model.playbackFields.map(\.path) == paths)
    }
}

@Test func partialPlaybackStatusDoesNotInventMetricsOrInterpolation() throws {
    let status = try #require(PlaybackStatus(.object([
        "title": .string("Example Movie.mkv"),
        "paused": .bool(true),
        "interpolation": .null,
        "target_fps": .null,
        "quality": .string("future-status"),
        "output_drops": .null,
        "decoder_drops": .number(-1),
        "display_delays": .number(1.5),
        "av_offset_ms": .null
    ])))
    #expect(status.title == "Example Movie.mkv")
    #expect(status.paused)
    #expect(status.interpolation == nil)
    #expect(status.targetFPS == nil)
    #expect(status.quality == .checking)
    #expect(status.outputDrops == nil)
    #expect(status.decoderDrops == nil)
    #expect(status.displayDelays == nil)
    #expect(status.avOffsetMilliseconds == nil)
}
