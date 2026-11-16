from fractions import Fraction
from functools import lru_cache
import importlib.util
import os
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


def prepare_clip(clip, output_bits=8, dither=policy.DEFAULT_SETTINGS["dither"], compatibility="legacy"):
    core = vs.core
    policy.need(dither in policy.DITHER_TYPES, "Unsupported dither method")
    policy.need(compatibility in ("legacy", "svp"), "Unsupported conversion policy")
    policy.need(clip.width > 0 and clip.height > 0, "The input must have fixed dimensions; reinitialize the filter after a size change")
    def bridge_metadata(n, f):
        properties = {}
        if "_Matrix" not in f.props and "_ColorSpace" in f.props:
            properties["_Matrix"] = f.props["_ColorSpace"]
        if hasattr(vs, "RANGE_FULL") and "_Range" not in f.props and "_ColorRange" in f.props:
            properties["_Range"] = int(vs.RANGE_FULL if f.props["_ColorRange"] == 0 else vs.RANGE_LIMITED)
        if not properties:
            return f
        output = f.copy()
        output.props.update(properties)
        return output
    clip = core.std.ModifyFrame(clip, clips=clip, selector=bridge_metadata)
    original = clip.format
    if original is not None:
        policy.need(original.color_family in (vs.YUV, vs.RGB, vs.GRAY), "Unsupported input color family")
    if output_bits == "source":
        bits = original.bits_per_sample if original is not None and original.sample_type == vs.INTEGER else 16
        output_bits = 8 if bits <= 8 else 10 if bits <= 10 else 16
    policy.need(output_bits in (8, 10, 16), "Output depth must be 8, 10, 16 or source")
    if output_bits == 8 and compatibility == "legacy":
        format_id = vs.YUV420P8 if original is not None and original.color_family == vs.YUV and original.subsampling_w == original.subsampling_h == 1 else vs.YUV444P8
    elif output_bits == 8:
        format_id = vs.YUV420P8
    elif output_bits == 10:
        format_id = vs.YUV420P10
    else:
        format_id = vs.YUV420P16
    format = core.get_video_format(format_id)
    unit_w, unit_h = 1 << format.subsampling_w, 1 << format.subsampling_h
    width, height = clip.width, clip.height
    native_width = (width + unit_w - 1) // unit_w * unit_w
    native_height = (height + unit_h - 1) // unit_h * unit_h
    padded = (native_width, native_height) != (width, height)

    @lru_cache(maxsize=16)
    def converted(matrix, input_rgb):
        intermediate = core.query_video_format(vs.YUV, vs.INTEGER, format.bits_per_sample, 0, 0).id if padded else format_id
        options = {"width": width, "height": height, "format": intermediate, "dither_type": dither}
        if matrix is not None:
            options.update(matrix=matrix, range_s="limited")
        if input_rgb:
            options["matrix_in_s"] = "rgb"
        converter = core.resize.Spline36
        if compatibility == "svp":
            point = original is not None and original.color_family == vs.YUV and original.subsampling_w == original.subsampling_h == 1 and not padded
            converter = core.resize.Point if point else core.resize.Bicubic
        source = core.std.SetFrameProps(clip, _Matrix=0) if input_rgb else clip
        result = converter(source, **options)
        if native_width != width:
            result = core.std.StackHorizontal([result, core.std.CropAbs(result, width=1, height=height, left=width - 1)])
        if native_height != height:
            result = core.std.StackVertical([result, core.std.CropAbs(result, width=native_width, height=1, top=height - 1)])
        if padded:
            converter = core.resize.Bicubic if compatibility == "svp" else core.resize.Spline36
            result = converter(result, format=format_id)
        return result

    def select(n, f):
        matrix = None
        if f.format.color_family in (vs.RGB, vs.GRAY):
            primaries = f.props.get("_Primaries", 1)
            matrix = {5: 5, 6: 6, 9: 9}.get(primaries, 1)
        return converted(matrix, f.format.color_family == vs.RGB)

    template = core.std.BlankClip(clip, width=native_width, height=native_height, format=format_id)
    synthesis = core.std.FrameEval(template, select, prop_src=clip)
    analysis_format = vs.YUV444P8 if format_id == vs.YUV444P8 else vs.YUV420P8
    analysis = core.resize.Point(synthesis, format=analysis_format, dither_type="none")
    return synthesis, analysis


def interpolate(clip, settings, source, target, directory, gpu=None):
    core = vs.core
    ratio = target / source
    max_source = ((2147483647 - 1) * ratio.denominator) // ratio.numerator + 1
    if clip.num_frames > max_source:
        clip = clip[:max_source]
    normalized = core.std.AssumeFPS(clip, fpsnum=source.numerator, fpsden=source.denominator)
    settings = policy.validate_settings(settings)
    bits, compatibility = settings["output_bits"], "legacy"
    if settings["schema"] == 2:
        source_bits = clip.format.bits_per_sample if clip.format is not None and clip.format.sample_type == vs.INTEGER else 16
        bits = policy.settings_api().synthesis_bits(settings, clip.width, clip.height, source_bits, target)
        compatibility = "legacy" if "legacy" in settings else "svp"
        core.num_threads = settings["processing_threads"] or os.cpu_count() or 1
    synthesis, analysis = prepare_clip(normalized, bits, settings["dither"], compatibility=compatibility)
    options = policy.engine_options(settings, source, target, synthesis.format.bits_per_sample, gpu=gpu)
    load_plugins(core, directory)
    super_result = core.svp1.Super(analysis, options["super"])
    vectors = core.svp1.Analyse(super_result["clip"], super_result["data"], analysis, options["analyse"])
    output = core.svp2.SmoothFps(synthesis, super_result["clip"], super_result["data"], vectors["clip"], vectors["data"], options["smooth"])
    desired = clip.width, clip.height
    if (output.width, output.height) != desired:
        full_chroma = core.query_video_format(vs.YUV, vs.INTEGER, output.format.bits_per_sample, 0, 0).id
        output = core.resize.Spline36(output, format=full_chroma)
        output = core.std.CropAbs(output, width=desired[0], height=desired[1])
    return core.std.AssumeFPS(output, fpsnum=target.numerator, fpsden=target.denominator)


def bootstrap(clip, nominal_fps, refresh, payload):
    source = policy.source_rate(nominal_fps)
    value = policy.decode_payload(payload)
    target = policy.select_rate(source, refresh, value["profile"], value["fps"])
    if target is None:
        print("Open SVPflow: original-rate playback selected because no compatible refresh was reported", file=sys.stderr)
        return clip
    output = interpolate(clip, value["settings"], source, target, value["plugins"])
    print("Open SVPflow: interpolation target " + str(target) + " FPS at " + str(output.width) + "x" + str(output.height), file=sys.stderr)
    return output


if "video_in" in globals():
    bootstrap(video_in, container_fps, display_fps, user_data).set_output()
