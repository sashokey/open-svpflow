import argparse
from array import array
from fractions import Fraction
from pathlib import Path
import runpy


def check_intermediate(previous, following, intermediate):
    assert len(previous) == len(following) == len(intermediate) > 0, "Intermediate plane dimensions differ"
    source_delta = min(sum(abs(a - b) for a, b in zip(previous, intermediate)), sum(abs(a - b) for a, b in zip(following, intermediate))) / len(intermediate)
    blend_delta = sum(abs(actual - (a + b + 1) // 2) for a, b, actual in zip(previous, following, intermediate)) / len(intermediate)
    assert source_delta > 0.25, "Intermediate repeats a source frame"
    assert blend_delta > 0.25, "Intermediate is only a simple frame blend"
    return {"source_delta": source_delta, "blend_delta": blend_delta}


def luma(frame):
    if frame.format.bits_per_sample == 8:
        return bytes(frame[0])
    samples = array("H")
    samples.frombytes(bytes(frame[0]))
    return samples


def main():
    import vapoursynth as vs

    parser = argparse.ArgumentParser(description="Run a small real CPU VapourSynth interpolation smoke test.")
    parser.add_argument("--plugin-dir", type=Path, required=True)
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    graph = runpy.run_path(str(root / "player" / "graph.vpy"))
    core = vs.core
    core.max_cache_size = 128
    source = core.std.BlankClip(width=320, height=192, format=vs.YUV420P8, length=4, fpsnum=24, fpsden=1, color=[32, 128, 128])
    def moving_bar(n, f):
        out = f.copy()
        pixels = out[0]
        for y in range(out.height):
            for x in range(out.width):
                pixels[y, x] = (180 if 64 + n * 4 <= x < 112 + n * 4 else 32) + n
        return out
    source = core.std.ModifyFrame(source, clips=source, selector=moving_bar)
    output = graph["interpolate"](source, graph["policy"].DEFAULT_SETTINGS, Fraction(24), Fraction(48), options.plugin_dir, gpu=False)
    assert (output.width, output.height, output.format.id, output.fps, output.num_frames) == (320, 192, vs.YUV420P8, Fraction(48), 7)
    source_luma = []
    for index in range(source.num_frames):
        with source.get_frame(index) as frame:
            source_luma.append(bytes(frame[0]))
    rendered = []
    failures = []
    for index in range(output.num_frames):
        with output.get_frame(index) as frame:
            assert (frame.props["_DurationNum"], frame.props["_DurationDen"]) == (1, 48)
            if index % 2:
                try:
                    rendered.append(check_intermediate(source_luma[index // 2], source_luma[index // 2 + 1], bytes(frame[0])))
                except AssertionError as error:
                    failures.append(str(error))
    assert rendered, "No motion-compensated intermediate was observed: " + "; ".join(failures)
    print("PASS: native plugins loaded; 4 source frames -> 7 output frames at 48 FPS; CPU intermediate pixels and durations verified; " + str(rendered))
    for format in (vs.YUV422P8, vs.YUV420P10, vs.YUV420P12, vs.YUV444P16, vs.RGB24, vs.RGB48, vs.GRAY16):
        resize = {"format": format}
        if vs.core.get_video_format(format).color_family == vs.RGB:
            resize["matrix_in_s"] = "709"
        converted_source = core.resize.Spline36(source, **resize)
        settings = graph["policy"].validate_settings({"output_bits": "source"})
        expected, _ = graph["prepare_clip"](converted_source, "source")
        result = graph["interpolate"](converted_source, settings, Fraction(24), Fraction(48), options.plugin_dir, gpu=False)
        assert (result.width, result.height, result.format.id, result.fps, result.num_frames) == (320, 192, expected.format.id, Fraction(48), 7)
        with expected.get_frame(0) as previous, expected.get_frame(1) as following, result.get_frame(1) as intermediate:
            metrics = check_intermediate(luma(previous), luma(following), luma(intermediate))
            assert (intermediate.props["_DurationNum"], intermediate.props["_DurationDen"]) == (1, 48)
        print("PASS: native converted " + core.get_video_format(format).name + " -> " + result.format.name + "; " + str(metrics))
    fractional = graph["interpolate"](source, graph["policy"].DEFAULT_SETTINGS, Fraction(120), Fraction(165), options.plugin_dir, gpu=False)
    assert fractional.fps == Fraction(165)
    with fractional.get_frame(1) as frame:
        assert (frame.props["_DurationNum"], frame.props["_DurationDen"]) == (1, 165)
    print("PASS: native 120 -> 165 FPS interpolation below a 2x multiplier")
    odd_source = core.std.CropAbs(core.resize.Spline36(source, format=vs.RGB48, matrix_in_s="709"), width=319, height=191)
    odd_output = graph["interpolate"](odd_source, {"output_bits": "source"}, Fraction(24), Fraction(48), options.plugin_dir, gpu=False)
    assert (odd_output.width, odd_output.height, odd_output.format.id) == (319, 191, vs.YUV444P16)
    with odd_output.get_frame(1) as frame:
        assert (frame.width, frame.height) == (319, 191)
    print("PASS: odd RGB48 dimensions restored after internal chroma alignment")
    payload = graph["policy"].encode_payload({"schema": 1, "plugins": str(options.plugin_dir.resolve()), "settings": graph["policy"].DEFAULT_SETTINGS, "fps": "auto"})
    runpy.run_path(str(root / "player" / "graph.vpy"), init_globals={"video_in": source, "container_fps": 24, "display_fps": 48, "display_res": [160, 90], "video_in_dw": 320, "video_in_dh": 192, "user_data": payload})
    registered = vs.get_output(0)
    unchanged = registered.clip if hasattr(registered, "clip") else registered
    assert (unchanged.width, unchanged.height) == (320, 192), "Display metadata changed processing geometry"
    vs.clear_output(0)
    print("PASS: graph initialization preserves source dimensions despite smaller reported display")


if __name__ == "__main__":
    main()
