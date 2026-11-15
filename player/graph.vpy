from fractions import Fraction
import importlib.util
from pathlib import Path
import sys

import vapoursynth as vs


location = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("open_svpflow_policy", location.with_name("policy.py"))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def load_plugins(core, directory):
    for index, path in enumerate(policy.plugin_paths(directory), 1):
        identifier = "com.svp-team.flow" + str(index)
        loaded = [plugin for plugin in core.plugins() if plugin.identifier == identifier]
        if loaded:
            policy.need(len(loaded) == 1 and loaded[0].plugin_path and Path(loaded[0].plugin_path).resolve() == path.resolve(), "Another SVP implementation is already loaded")
        else:
            core.std.LoadPlugin(path=str(path))


def interpolate(clip, settings, source, target, directory, output_size=(2560, 1440), gpu=True):
    core = vs.core
    policy.need(clip.format is not None and clip.format.id in (vs.YUV420P8, vs.YUV420P10), "Interpolation requires constant-format YUV420P8 or YUV420P10")
    bits = clip.format.bits_per_sample
    options = policy.engine_options(settings, source, target, bits, gpu=gpu)
    load_plugins(core, directory)
    ratio = target / source
    max_source = ((2147483647 - 1) * ratio.denominator) // ratio.numerator + 1
    if clip.num_frames > max_source:
        clip = clip[:max_source]
    normalized = core.std.AssumeFPS(clip, fpsnum=source.numerator, fpsden=source.denominator)
    synthesis = core.resize.Spline36(normalized, width=output_size[0], height=output_size[1])
    analysis = core.resize.Point(synthesis, format=vs.YUV420P8, dither_type="none")
    super_result = core.svp1.Super(analysis, options["super"])
    vectors = core.svp1.Analyse(super_result["clip"], super_result["data"], analysis, options["analyse"])
    output = core.svp2.SmoothFps(synthesis, super_result["clip"], super_result["data"], vectors["clip"], vectors["data"], options["smooth"])
    return core.std.AssumeFPS(output, fpsnum=target.numerator, fpsden=target.denominator)


def bootstrap(clip, nominal_fps, refresh, payload):
    source = policy.source_rate(nominal_fps)
    value = policy.decode_payload(payload)
    target = policy.select_rate(source, refresh, value["profile"], value["fps"])
    if target is None:
        print("Open SVPflow: original-rate playback selected because no compatible refresh was reported", file=sys.stderr)
        return clip
    print("Open SVPflow: interpolation target " + str(target) + " FPS at 2560x1440", file=sys.stderr)
    return interpolate(clip, value["settings"], source, target, value["plugins"])


if "video_in" in globals():
    bootstrap(video_in, container_fps, display_fps, user_data).set_output()
