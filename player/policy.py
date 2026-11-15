import base64
import copy
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import stat


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
}
TARGETS = tuple(Fraction(value) for value in ("48000/1001", "48", "50", "60000/1001", "60", "119", "120000/1001", "120"))
PLUGIN_NAMES = ("libsvpflow1_vs.dylib", "libsvpflow2_vs.dylib")


def need(value, message):
    if not value:
        raise ValueError(message)


def bounded_int(value, low, high, name):
    need(type(value) is int and low <= value <= high, "Invalid " + name)


def closed(value, keys, name):
    need(type(value) is dict and set(value) == set(keys), "Invalid " + name + " fields")


def validate_settings(value):
    closed(value, DEFAULT_SETTINGS, "settings")
    need(type(value["schema"]) is int and value["schema"] == 1 and value["rate_mode"] == "monitor_auto", "Unsupported settings schema")
    bounded_int(value["interpolation_mode"], 0, 3, "interpolation mode")
    need(type(value["shader"]) is int and value["shader"] in (21, 22, 23), "Unsupported shader")
    bounded_int(value["motion_precision"], 1, 2, "motion precision")
    bounded_int(value["decrease_grid_step"], 0, 1, "grid refinement")
    bounded_int(value["coarse_width"], 128, 2560, "coarse width")
    need(type(value["wide_search"]) is bool and type(value["duplicate_frames_removal"]) is bool, "Boolean settings must use JSON booleans")
    need(value["scene_changes"] in ("nearest", "blend"), "Unsupported scene-change handling")
    closed(value["artifacts_masking"], ("cover", "area"), "artifacts masking")
    bounded_int(value["artifacts_masking"]["cover"], 1, 100, "mask cover")
    bounded_int(value["artifacts_masking"]["area"], 0, 100, "mask area")
    closed(value["motion_grid"], ("block", "overlap"), "motion grid")
    need(type(value["motion_grid"]["block"]) is int and value["motion_grid"]["block"] in (8, 16, 32), "Unsupported motion block")
    bounded_int(value["motion_grid"]["overlap"], 0, 3, "motion overlap")
    closed(value["search_radius"], ("fine", "coarse"), "search radius")
    for item in value["search_radius"].values():
        bounded_int(item, -10, 10, "search radius")
    return copy.deepcopy(value)


def read_settings(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        need(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "Settings must be a regular file")
        data = stream.read(4097)
    need(0 < len(data) <= 4096, "Settings must contain at most 4096 bytes")
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
    need(0 < rate <= 60, "Source frame rate must be positive and at most 60 FPS")
    for nominal in (Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)):
        if abs(rate - nominal) < Fraction(1, 10000):
            return nominal
    result = rate.limit_denominator(1001000)
    need(result > 0, "Source frame rate is too small to represent")
    return result


def select_rate(source, display, profile="auto", explicit="auto"):
    need(profile in ("auto", "smooth", "original"), "Unsupported playback profile")
    source = source_rate(source)
    if profile == "original":
        return None
    if explicit not in (None, "auto"):
        try:
            target = Fraction(str(explicit))
        except (ValueError, ZeroDivisionError) as error:
            raise ValueError("Invalid target frame rate") from error
        need(source * 2 <= target <= 240 and target.numerator <= 2147483647 and target.denominator <= 2147483647, "Target rate must be at least twice the source and at most 240 FPS")
        return target
    try:
        hz = float(display)
    except (TypeError, ValueError):
        hz = 0
    if not math.isfinite(hz) or hz <= 0:
        need(profile == "auto", "Display refresh is unavailable; pass --fps explicitly")
        return None
    limit = Fraction(str(hz))
    for nominal in TARGETS:
        if abs(limit - nominal) <= Fraction(1, 100000):
            limit = nominal
            break
    candidates = [target for target in TARGETS if source * 2 <= target <= limit]
    if limit >= Fraction(60000, 1001):
        candidates = [target for target in candidates if target >= Fraction(60000, 1001)]
    if limit >= 119:
        candidates = [target for target in candidates if target >= 119]
    if not candidates:
        need(profile == "auto", "Display refresh cannot support the interpolation target")
        return None
    return max(candidates, key=lambda target: ((target / source).denominator == 1, target))


def engine_options(settings, source, target, bits, gpu=True):
    value = validate_settings(settings)
    need(bits in (8, 10), "Only 8-bit and 10-bit synthesis is supported")
    need(gpu or bits == 8, "CPU synthesis only supports 8-bit input")
    ratio = target / source
    need(ratio >= 2, "Interpolation requires at least twice the source frame rate")
    grid, radius = value["motion_grid"], value["search_radius"]
    need(value["motion_precision"] == 1 or radius["fine"] == 0, "Subpixel precision requires zero fine search distance")
    need(not value["decrease_grid_step"] or grid == {"block": 16, "overlap": 2} and radius["fine"] == 0, "Refinement requires block 16, overlap 2 and zero fine search")
    need(not value["duplicate_frames_removal"] or bits == 8, "Duplicate-frame removal is unavailable for 10-bit synthesis")
    rate = {"num": ratio.numerator, "den": 1, "abs": False} if ratio.denominator == 1 else {"num": target.numerator, "den": target.denominator, "abs": True}
    options = {
        "super": {"pel": value["motion_precision"], "gpu": 1 if gpu else 0, "full": True, "scale": {"up": 0}, "rc": True},
        "analyse": {
            "block": {"w": grid["block"], "h": grid["block"], "overlap": grid["overlap"]},
            "exact": not gpu,
            "main": {"search": {"type": 4, "distance": radius["fine"], "coarse": {"type": 4, "distance": radius["coarse"], "width": value["coarse_width"], "satd": True, "bad": {"sad": 1000, "range": -24 if value["wide_search"] else 0}}}},
            "refine": [{"thsad": 200}] if value["decrease_grid_step"] else [],
        },
        "smooth": {
            "rate": rate, "algo": value["shader"], "api": 0, "gpuid": 0 if gpu else -1, "gpu_qn": 2,
            "scene": {"mode": value["interpolation_mode"], "blend": value["scene_changes"] == "blend", "adaptive": 210, "force13": True, "luma": 1.5, "limits": {"blocks": 20, "blocks13": 0, "ignore": 4, "zero": 200, "m1": 1600, "m2": 2800, "scene": 4000}},
            "mask": {"cover": value["artifacts_masking"]["cover"], "area": value["artifacts_masking"]["area"], "still": False, "peel": False},
            "dedup": int(value["duplicate_frames_removal"]), "block": False,
            "debug": {"flags": 0, "vectors": False, "qmap": False, "qmode": False, "zerox": False, "zeroy": False, "tt": False},
        },
    }
    return {key: json.dumps(item, separators=(",", ":"), allow_nan=False) for key, item in options.items()}


def plugin_paths(directory):
    directory = Path(directory).expanduser().resolve()
    paths = [directory / name for name in PLUGIN_NAMES]
    need(all(path.is_file() for path in paths), "Both native plugins are required; pass --plugin-dir")
    return paths


def encode_payload(value):
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")).decode("ascii")


def decode_payload(value):
    need(type(value) is str and 0 < len(value) <= 16384, "Invalid graph settings payload")
    data = json.loads(base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True))
    closed(data, ("schema", "plugins", "settings", "profile", "fps"), "graph payload")
    need(type(data["schema"]) is int and data["schema"] == 1, "Unsupported graph settings payload")
    data["settings"] = validate_settings(data["settings"])
    need(data["profile"] in ("auto", "smooth"), "Unsupported graph profile")
    need(type(data["plugins"]) is str and type(data["fps"]) is str, "Invalid graph paths or rate")
    return data


def build_command(mpv, movie, graph, plugins, settings, profile="auto", fps="auto", extra=(), start=None, duration=None):
    need(type(mpv) is str and mpv, "External mpv executable is required")
    need(profile in ("auto", "smooth", "original"), "Unsupported playback profile")
    text = str(movie)
    need("://" not in text and not any(ord(char) < 32 for char in text), "A local movie file is required")
    media = Path(text).expanduser().resolve()
    need(media.is_file(), "A readable local movie file is required")
    settings = validate_settings(settings)
    argv = [mpv, "--no-config", "--load-scripts=no", "--framedrop=no", "--hr-seek-framedrop=no"]
    if profile != "original":
        plugins = plugin_paths(plugins)[0].parent
        graph = Path(graph).resolve()
        need(graph.is_file(), "VapourSynth graph is unavailable")
        if fps != "auto":
            try:
                rate = Fraction(str(fps))
            except (ValueError, ZeroDivisionError) as error:
                raise ValueError("Invalid target frame rate") from error
            need(0 < rate <= 240, "Target frame rate must be positive and at most 240 FPS")
        token = encode_payload({"schema": 1, "plugins": str(plugins), "settings": settings, "profile": profile, "fps": str(fps)})
        filename = str(graph)
        quoted = "%" + str(len(filename.encode("utf-8"))) + "%" + filename
        quoted_token = "%" + str(len(token)) + "%" + token
        argv.append("--vf=vapoursynth=file=" + quoted + ":buffered-frames=8:concurrent-frames=8:user-data=" + quoted_token)
    for value, name in ((start, "start"), (duration, "length")):
        if value is not None:
            need(type(value) in (int, float) and math.isfinite(value) and value >= 0 and (name != "length" or 0 < value <= 300), "Invalid playback time bound")
            argv.append("--" + name + "=" + str(value))
    need(all(type(arg) is str and arg.startswith("--") and not any(ord(char) < 32 for char in arg) for arg in extra), "Extra mpv options must begin with --")
    argv.extend(extra)
    return argv + ["--", str(media)]
