import argparse
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
    output = graph["interpolate"](source, graph["policy"].DEFAULT_SETTINGS, Fraction(24), Fraction(48), options.plugin_dir, output_size=(320, 192), gpu=False)
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


if __name__ == "__main__":
    main()
