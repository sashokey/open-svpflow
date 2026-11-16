import base64
import copy
from fractions import Fraction
from functools import lru_cache
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile


DEFAULT_SETTINGS = {
    "schema": 1,
    "rate_mode": "monitor_auto",
    "interpolation_mode": 3,
    "shader": 23,
    "artifacts_masking": {"cover": 80, "area": 0},
    "motion_precision": 2,
    "motion_grid": {"block": 16, "overlap": 2},
    "decrease_grid_step": 0,
    "search_radius": {"fine": 0, "coarse": -8},
    "wide_search": True,
    "coarse_width": 1050,
    "scene_changes": "nearest",
    "duplicate_frames_removal": False,
    "output_bits": 8,
    "dither": "ordered",
    "backend": "gpu",
    "gpu_device": 0,
    "buffered_frames": "auto",
    "concurrent_frames": "auto",
    "native": {"super": {}, "analyse": {}, "smooth": {}},
}
PLUGIN_NAMES = ("libsvpflow1_vs.dylib", "libsvpflow2_vs.dylib")
DITHER_TYPES = ("none", "ordered", "random", "error_diffusion")
NATIVE_INT_MAX = 2147483647
NATIVE_ALGORITHMS = (1, 2, 11, 13, 21, 22, 23, *range(90, 101))
NATIVE_BLOCKS = ((4, 4), (8, 4), (8, 8), (16, 8), (16, 16), (32, 16), (32, 32))
NATIVE_RESERVED = {"super": ("gpu",), "analyse": ("gpu",), "smooth": ("rate", "api", "gpuid", "render")}


@lru_cache(maxsize=1)
def settings_api():
    spec = importlib.util.spec_from_file_location("open_svpflow_settings_api", Path(__file__).with_name("settings_api.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def need(value, message):
    if not value:
        raise ValueError(message)


def bounded_int(value, low, high, name):
    need(type(value) is int and low <= value <= high, "Invalid " + name)


def scaled_metric(value, multiplier, name):
    bounded_int(value, -(NATIVE_INT_MAX + 1) // multiplier, NATIVE_INT_MAX // multiplier, "scaled native " + name)


def closed(value, keys, name):
    need(type(value) is dict and set(value) == set(keys), "Invalid " + name + " fields")


def merged(base, change):
    result = copy.deepcopy(base)
    for key, value in change.items():
        result[key] = merged(result[key], value) if type(value) is dict and type(result.get(key)) is dict else copy.deepcopy(value)
    return result


def validate_settings(value):
    if type(value) is dict and value.get("schema") == 2:
        return settings_api().validate_interpolation(value)
    need(type(value) is dict and set(value) <= set(DEFAULT_SETTINGS), "Invalid settings fields")
    value = merged(DEFAULT_SETTINGS, value)
    need(type(value["schema"]) is int and value["schema"] == 1 and value["rate_mode"] == "monitor_auto", "Unsupported settings schema")
    bounded_int(value["interpolation_mode"], 0, 3, "interpolation mode")
    need(type(value["shader"]) is int and value["shader"] in NATIVE_ALGORITHMS, "Unsupported shader")
    need(type(value["motion_precision"]) is int and value["motion_precision"] in (1, 2, 4), "Unsupported motion precision")
    bounded_int(value["decrease_grid_step"], 0, 3, "grid refinement")
    bounded_int(value["coarse_width"], 0, NATIVE_INT_MAX, "coarse width")
    need(type(value["wide_search"]) is bool and type(value["duplicate_frames_removal"]) is bool, "Boolean settings must use JSON booleans")
    need(value["scene_changes"] in ("nearest", "blend"), "Unsupported scene-change handling")
    closed(value["artifacts_masking"], ("cover", "area"), "artifacts masking")
    bounded_int(value["artifacts_masking"]["cover"], 0, NATIVE_INT_MAX, "mask cover")
    bounded_int(value["artifacts_masking"]["area"], 0, NATIVE_INT_MAX, "mask area")
    closed(value["motion_grid"], ("block", "overlap"), "motion grid")
    need(type(value["motion_grid"]["block"]) is int and value["motion_grid"]["block"] in (4, 8, 16, 32), "Unsupported motion block")
    bounded_int(value["motion_grid"]["overlap"], 0, 3, "motion overlap")
    closed(value["search_radius"], ("fine", "coarse"), "search radius")
    for item in value["search_radius"].values():
        bounded_int(item, -(NATIVE_INT_MAX // 256), NATIVE_INT_MAX // 256, "search radius")
    need(type(value["output_bits"]) is int and value["output_bits"] in (8, 10, 16) or value["output_bits"] == "source", "Unsupported output depth")
    need(type(value["dither"]) is str and value["dither"] in DITHER_TYPES, "Unsupported dither method")
    need(value["backend"] in ("gpu", "cpu"), "Unsupported synthesis backend")
    bounded_int(value["gpu_device"], 0, NATIVE_INT_MAX, "GPU device")
    for key, maximum in (("buffered_frames", 9999), ("concurrent_frames", 99)):
        if value[key] != "auto":
            bounded_int(value[key], 1, maximum, key)
    closed(value["native"], NATIVE_RESERVED, "native options")
    for section, reserved in NATIVE_RESERVED.items():
        need(type(value["native"][section]) is dict, "Native " + section + " options must be an object")
        need(not set(value["native"][section]).intersection(reserved), "Native " + section + " timing/backend keys are reserved: " + ", ".join(reserved))
    try:
        encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("Settings must contain finite JSON values") from error
    need(len(encoded) <= 65536, "Settings must contain at most 65536 bytes")
    return value


def read_settings(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        need(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "Settings must be a regular file")
        data = stream.read(65537)
    need(0 < len(data) <= 65536, "Settings must contain at most 65536 bytes")
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "Duplicate settings key")
            result[key] = value
        return result
    return validate_settings(json.loads(data.decode("utf-8"), object_pairs_hook=pairs))


def source_rate(value):
    need(type(value) in (int, float, str, Fraction) and type(value) is not bool, "Invalid source frame rate")
    if type(value) is float:
        need(math.isfinite(value), "Invalid source frame rate")
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("Invalid source frame rate") from error
    need(rate > 0, "Source frame rate must be positive")
    for nominal in (Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)):
        if abs(rate - nominal) < Fraction(1, 10000):
            return nominal
    result = rate.limit_denominator(1001000)
    need(result > 0, "Source frame rate is too small to represent")
    need(result.numerator <= NATIVE_INT_MAX and result.denominator <= NATIVE_INT_MAX, "Source frame rate exceeds native timing fields")
    return result


def target_rate(value):
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("Invalid target frame rate") from error
    need(0 < rate.numerator <= NATIVE_INT_MAX and rate.denominator <= NATIVE_INT_MAX, "Target frame rate exceeds native timing fields")
    return rate


def native_timing(source, target):
    ratio = target / source
    return 1 < ratio <= NATIVE_INT_MAX // 256 and ratio.denominator <= NATIVE_INT_MAX


def select_rate(source, display, explicit="auto"):
    source = source_rate(source)
    if type(explicit) is str and explicit.startswith("source*"):
        target = target_rate(source * target_rate(explicit[7:]))
    elif explicit not in (None, "auto", "monitor/2", "monitor/3"):
        target = target_rate(explicit)
    else:
        try:
            hz = float(display)
        except (TypeError, ValueError, OverflowError):
            hz = 0
        need(math.isfinite(hz) and hz > 0, "Display refresh rate is unavailable.")
        if explicit in ("monitor/2", "monitor/3"):
            hz /= int(explicit[-1])
        need(hz > source, "The target frame rate must be higher than the video's frame rate.")
        need(hz <= NATIVE_INT_MAX, "The target frame rate exceeds native timing limits.")
        denominator_limit = min(1001000, NATIVE_INT_MAX // source.numerator, NATIVE_INT_MAX // math.ceil(hz))
        target = Fraction(str(hz)).limit_denominator(denominator_limit)
    need(target > source, "The target frame rate must be higher than the video's frame rate.")
    need(native_timing(source, target), "The target frame rate exceeds native timing limits.")
    return target


def validate_engine_options(options, bits, gpu, ratio):
    sup, analyse, smooth = (options[key] for key in ("super", "analyse", "smooth"))
    need(type(sup["pel"]) is int and sup["pel"] in (1, 2, 4), "Unsupported native motion precision")
    need(type(sup["full"]) is bool and type(sup.get("lean", False)) is bool, "Native full/lean options must be booleans")
    need(sup["full"] or sup["pel"] == 1, "Native reduced super clips require pixel precision")
    need(type(sup["scale"]) is dict, "Native super scale must be an object")
    need(not sup.get("lean", False) or gpu and sup["pel"] <= 2 and sup["full"] and sup["scale"].get("down", 4) == 4, "Native lean super requires GPU, precision 1/2, full data and default downscaling")
    need(type(analyse["block"]) is dict, "Native motion block must be an object")
    block = analyse["block"]
    width, height = block["w"], block["h"]
    need(type(width) is int and type(height) is int and (width, height) in NATIVE_BLOCKS, "Unsupported native motion block dimensions")
    bounded_int(block["overlap"], 0, 3, "native motion overlap")
    divisor = (0, 8, 4, 2)[block["overlap"]]
    overlap = [0, 0] if divisor == 0 or width == 4 else [width // divisor, height // divisor]
    need(type(analyse["refine"]) is list and all(type(item) is dict for item in analyse["refine"]), "Native refinement must be an array of objects")
    need(width != 4 or not analyse["refine"], "Native refinement is unavailable for 4-pixel blocks")
    multiplier = width * height * 2
    for item in analyse["refine"]:
        need(1 not in overlap, "Native refinement cannot halve a one-pixel overlap")
        need(width >= 8 and height >= 8, "Native refinement requires blocks of at least 8x8 before division")
        width, height = width // 2, height // 2
        overlap = [item // 2 for item in overlap]
        multiplier //= 4
        scaled_metric(item.get("thsad", 200), multiplier, "refinement thsad")
    main = analyse.get("main", {})
    need(type(main) is dict and type(main.get("search", {})) is dict, "Native main search must be an object")
    search = main.get("search", {})
    searches = [search, search.get("coarse", {})] + [item.get("search", {}) for item in analyse["refine"]]
    for search in searches:
        need(type(search) is dict, "Native search options must be objects")
        if "distance" in search:
            bounded_int(search["distance"], -(NATIVE_INT_MAX // 256), NATIVE_INT_MAX // 256, "native search radius")
    coarse = main.get("search", {}).get("coarse", {})
    if "width" in coarse:
        bounded_int(coarse["width"], -NATIVE_INT_MAX, NATIVE_INT_MAX, "native coarse width")
    penalty, bad = main.get("penalty", {}), coarse.get("bad", {})
    need(type(penalty) is dict and type(bad) is dict, "Native penalty and coarse bad options must be objects")
    multiplier = block["w"] * block["h"] * 2
    scaled_metric(penalty.get("lsad", 8000), multiplier, "penalty lsad")
    scaled_metric(bad.get("sad", 1000), multiplier, "coarse bad sad")
    need(gpu or all(item % 2 == 0 for item in overlap), "CPU synthesis requires an even pixel overlap")
    bounded_int(analyse.get("vectors", 3), 1, 3, "native vector directions")
    special = analyse.get("special", {})
    need(type(special) is dict and special.get("delta", 1) == 1, "Native synthesis requires adjacent-frame vectors (delta 1)")
    need(type(smooth["algo"]) is int and smooth["algo"] in NATIVE_ALGORITHMS, "Unsupported native shader")
    need(type(smooth["scene"]) is dict, "Native scene options must be an object")
    bounded_int(smooth["scene"]["mode"], 0, 3, "native interpolation mode")
    need(smooth["scene"]["mode"] != 2 or ratio >= 2, "Interpolation mode 2 requires at least twice the source frame rate")
    need(type(smooth["dedup"]) in (int, float, bool), "Native dedup must be numeric")
    need(smooth["dedup"] <= 0 or bits == 8, "Duplicate-frame removal requires 8-bit synthesis")
    need(type(smooth["block"]) is bool, "Native block mode must be boolean")
    need(not gpu or not smooth["block"], "Native block mode is unavailable with GPU synthesis")
    bounded_int(smooth.get("cubic", 0), -NATIVE_INT_MAX, NATIVE_INT_MAX, "native cubic mode")
    need(gpu or smooth.get("cubic", 0) <= 0, "Native cubic mode is unavailable with CPU synthesis")
    need(type(smooth["debug"]) is dict, "Native debug options must be an object")
    flags = smooth["debug"]["flags"]
    bounded_int(flags, -9223372036854775808, 9223372036854775807, "native debug flags")
    if not gpu and bits > 8:
        need(sup["pel"] <= 2, "High-depth CPU synthesis requires precision 1 or 2")
        need(smooth["algo"] in (1, 2, 11, 13, 21, 22, 23) and flags & 7 == 0, "High-depth CPU synthesis requires a native reference shader and compatible debug flags")


def engine_options(settings, source, target, bits, gpu=None):
    if type(settings) is dict and settings.get("schema") == 2:
        return settings_api().native_options(settings, source, target, bits, gpu)
    value = validate_settings(settings)
    source = source_rate(source)
    target = target_rate(target)
    need(native_timing(source, target), "Interpolation rates exceed native phase timing")
    need(bits in (8, 10, 16), "Only 8-bit, 10-bit and 16-bit synthesis is supported")
    need(gpu is None or type(gpu) is bool, "GPU override must be boolean")
    gpu = value["backend"] == "gpu" if gpu is None else gpu
    ratio = target / source
    grid, radius = value["motion_grid"], value["search_radius"]
    relative = ratio.denominator == 1 and source.numerator * ratio.numerator <= 9223372036854775807 and source.numerator * ratio.numerator * source.denominator <= 18446744073709551615
    rate = {"num": ratio.numerator, "den": 1, "abs": False} if relative else {"num": target.numerator, "den": target.denominator, "abs": True}
    options = {
        "super": {"pel": value["motion_precision"], "gpu": 1 if gpu else 0, "full": True, "scale": {"up": 0}, "rc": True},
        "analyse": {
            "block": {"w": grid["block"], "h": grid["block"], "overlap": grid["overlap"]},
            "exact": not gpu,
            "main": {"search": {"type": 4, "distance": radius["fine"], "coarse": {"type": 4, "distance": radius["coarse"], "width": value["coarse_width"], "satd": True, "bad": {"sad": 1000, "range": -24 if value["wide_search"] else 0}}}},
            "refine": [{"thsad": 200} for _ in range(value["decrease_grid_step"])],
        },
        "smooth": {
            "rate": rate, "algo": value["shader"], "api": 0, "gpuid": value["gpu_device"] if gpu else -1, "gpu_qn": 2,
            "scene": {"mode": value["interpolation_mode"], "blend": value["scene_changes"] == "blend", "adaptive": 210, "force13": True, "luma": 1.5, "limits": {"blocks": 20, "blocks13": 0, "ignore": 4, "zero": 200, "m1": 1600, "m2": 2800, "scene": 4000}},
            "mask": {"cover": value["artifacts_masking"]["cover"], "area": value["artifacts_masking"]["area"], "still": False, "peel": False},
            "dedup": int(value["duplicate_frames_removal"]), "block": False,
            "debug": {"flags": 0, "vectors": False, "qmap": False, "qmode": False, "zerox": False, "zeroy": False, "tt": False},
        },
    }
    options = merged(options, value["native"])
    validate_engine_options(options, bits, gpu, ratio)
    return {key: json.dumps(item, separators=(",", ":"), allow_nan=False) for key, item in options.items()}


def plugin_paths(directory):
    directory = Path(directory).expanduser().resolve()
    paths = [directory / name for name in PLUGIN_NAMES]
    need(all(path.is_file() for path in paths), "Both native plugins are required; pass --plugin-dir")
    return paths


def encode_payload(value):
    token = base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")).decode("ascii")
    need(len(token) <= 131072, "Graph settings payload exceeds 131072 bytes")
    return token


def decode_payload(value):
    need(type(value) is str and 0 < len(value) <= 131072, "Invalid graph settings payload")
    data = json.loads(base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True))
    need(type(data) is dict, "Invalid graph payload")
    data.pop("profile", None)
    fields = ("schema", "plugins", "settings", "fps")
    if "attempt" in data:
        bounded_int(data["attempt"], 1, NATIVE_INT_MAX, "playback attempt")
        fields += ("attempt",)
    closed(data, fields, "graph payload")
    need(type(data["schema"]) is int and data["schema"] == 1, "Unsupported graph settings payload")
    data["settings"] = validate_settings(data["settings"])
    need(type(data["plugins"]) is str and type(data["fps"]) is str, "Invalid graph paths or rate")
    return data


def graph_attempt(attempt):
    directory = os.environ.get("OPEN_SVPFLOW_GRAPH_DIR")
    if attempt is None and directory is not None:
        attempt = int((Path(directory) / "cli-attempt").read_text())
        bounded_int(attempt, 1, NATIVE_INT_MAX, "playback attempt")
    return attempt


def write_graph_report(attempt, result):
    directory = os.environ.get("OPEN_SVPFLOW_GRAPH_DIR")
    if directory is None or attempt is None:
        return
    path = Path(directory) / f"graph-{attempt}.json"
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(result, stream, allow_nan=False)
            stream.flush()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def build_command(mpv, movie, graph, plugins, settings, fps="auto", extra=(), start=None, duration=None, *, attempt=None):
    need(type(mpv) is str and mpv, "External mpv executable is required")
    text = str(movie)
    need(text and not any(ord(char) < 32 for char in text), "A movie source without control characters is required")
    media = text
    if text != "-" and re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text) is None:
        media = Path(text).expanduser().resolve()
        need(media.is_file(), "A readable local movie file is required")
    settings = validate_settings(settings)
    argv = [mpv, "--hr-seek-framedrop=no"]
    plugins = plugin_paths(plugins)[0].parent
    graph = Path(graph).resolve()
    need(graph.is_file(), "VapourSynth graph is unavailable")
    if fps != "auto" and fps not in ("monitor/2", "monitor/3") and not str(fps).startswith("source*"):
        target_rate(fps)
    payload = {"schema": 1, "plugins": str(plugins), "settings": settings, "fps": str(fps)}
    if attempt is not None:
        payload["attempt"] = attempt
    token = encode_payload(payload)
    filename = str(graph)
    quoted = "%" + str(len(filename.encode("utf-8"))) + "%" + filename
    quoted_token = "%" + str(len(token)) + "%" + token
    formats = "|".join("yuv" + chroma + "p" + depth for chroma in ("420", "422", "444") for depth in ("", "9le", "10le", "12le", "14le", "16le"))
    matrices = "bt709|bt2020nc|bt2020c|smpte170m|bt470bg|smpte240m|fcc|ycgco|ictcp"
    normalizer = "@svp-input:lavfi=[scale=w=iw:h=ih,format=pix_fmts=" + formats + ":color_spaces=" + matrices + ":color_ranges=tv|pc]"
    buffer_options = "".join(":" + option.replace("_", "-") + "=" + str(settings[option]) for option in ("buffered_frames", "concurrent_frames") if settings[option] != "auto")
    wrapper_filter = "--vf-add=" + normalizer + ",@svp-interpolate:vapoursynth=file=" + quoted + buffer_options + ":user-data=" + quoted_token
    for value, name in ((start, "start"), (duration, "length")):
        if value is not None:
            need(type(value) in (int, float) and math.isfinite(value) and value >= 0 and (name != "length" or value > 0), "Invalid playback time bound")
            argv.append("--" + name + "=" + str(value))
    extra = tuple(extra)
    need(all(type(arg) is str and arg.startswith("--") and not any(ord(char) < 32 for char in arg) for arg in extra), "Extra mpv options must begin with --")
    insertion = max((index + 1 for index, arg in enumerate(extra) if arg.startswith("--vf=")), default=0)
    argv.extend(extra[:insertion])
    argv.append(wrapper_filter)
    argv.extend(extra[insertion:])
    return argv + ["--", str(media)]
