import copy
from fractions import Fraction
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import tempfile


_spec = importlib.util.spec_from_file_location("open_svpflow_legacy_policy", Path(__file__).with_name("policy.py"))
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)

INTERPOLATION_DEFAULTS = {
    "fi_mode": 4, "fi_shader": 23, "fi_masking": 0, "fi_precision": 2,
    "mv_grid": 12, "mv_refine": 0, "mv_radius": 2, "mv_wide": 1,
    "mv_coarsewidth": 1050, "fi_scene_changes": 1, "fi_duplicates": 0,
    "output_bits": "auto", "limit_10bit": 72, "dither": "random",
    "backend": "gpu", "gpu_device": 0, "gpu_queues": 2,
    "processing_threads": 0, "buffered_frames": "auto", "concurrent_frames": "auto",
    "scene_adaptive": 210, "scene_blocks": 20, "scene_blocks13": 0,
    "scene_ignore": 4, "scene_zero": 200, "scene_m1": 1600,
    "scene_m2": 2800, "scene_threshold": 4000, "scene_luma": 1.5,
    "mask_area_sharp": 1.0, "mask_area_blend": 0.4,
    "search_lambda": 10.0, "search_plevel": 1.5,
    "native": {"super": {}, "analyse": {}, "smooth": {}},
}
GRID = {32: (32, 0), 28: (32, 1), 24: (32, 2), 16: (16, 0), 14: (16, 1), 12: (16, 2), 8: (8, 0), 7: (8, 1), 6: (8, 2)}
PLAYBACK_DEFAULTS = {"profile": "auto", "rate": "monitor", "fps": "120"}
PREFERENCE_DEFAULTS = {"new_files": "append", "after_start": "quit_app"}
RATES = {"monitor": "auto", "monitor_half": "monitor/2", "monitor_third": "monitor/3", "source_2": "source*2", "source_2_5": "source*5/2", "source_3": "source*3", "source_4": "source*4", "source_5": "source*5"}
LIMITATIONS = [
    "The fixed film preset is an application preset, not a recovered universal SVP Manager factory or Automatic profile. Parameter mappings follow the documented SVPflow API and inspected generator behavior.",
    "Automatic duplicate-cadence removal from SVP Manager is not reproduced. It is unavailable in the new preset; legacy native dedup settings remain preserved and are not represented as equivalent.",
    "The automatic 10-bit limit uses a 4K-equivalent pixel-rate budget. This is an explicit compatibility policy; the closed manager's exact selection formula has not been verified.",
    "Analysis remains 8-bit. New presets prepare YUV 4:2:0; odd dimensions use edge padding which is cropped after synthesis. Legacy presets retain their conversion behavior.",
    "Automatic thread selection and GPU queue defaults use the installed components' behavior, not SVP Manager's hardware performance assessment. Existing HDR metadata is preserved without adding HDR tone mapping or claiming identical HDR rendering.",
]


def default_document():
    return {"schema": 2, "interpolation": copy.deepcopy(INTERPOLATION_DEFAULTS), "playback": copy.deepcopy(PLAYBACK_DEFAULTS), "preferences": copy.deepcopy(PREFERENCE_DEFAULTS)}


def _one_of(value, choices, label):
    policy.need(any(type(value) is type(item) and value == item for item in choices), "Unsupported " + label)


def validate_interpolation(value):
    policy.need(type(value) is dict, "Interpolation settings must be an object")
    policy.need("schema" not in value or type(value["schema"]) is int and value["schema"] == 2, "Unsupported interpolation schema")
    policy.need(set(value) <= set(INTERPOLATION_DEFAULTS) | {"schema", "legacy"}, "Unknown interpolation settings")
    result = policy.merged(INTERPOLATION_DEFAULTS, {key: item for key, item in value.items() if key not in ("schema", "legacy")})
    for key, choices in (("fi_mode", (1, 2, 3, 4, 5)), ("fi_shader", (1, 2, 10, 11, 13, 21, 22, 23, *range(90, 101))), ("fi_precision", (0, 1, 2, 4)), ("mv_grid", GRID), ("mv_radius", (0, 1, 2, 3, "legacy")), ("mv_wide", (0, 1, 2, 3)), ("fi_scene_changes", (0, 1)), ("fi_duplicates", (0,)), ("output_bits", ("auto", "source", 8, 10, 16)), ("dither", policy.DITHER_TYPES), ("backend", ("gpu", "cpu"))):
        _one_of(result[key], choices, key)
    if type(result["mv_refine"]) is str and result["mv_refine"].isascii() and result["mv_refine"].isdigit():
        result["mv_refine"] = int(result["mv_refine"])
    if result["mv_refine"] != "legacy":
        policy.bounded_int(result["mv_refine"], 0, policy.NATIVE_INT_MAX, "mv_refine")
    for key in ("fi_masking", "mv_coarsewidth", "limit_10bit", "gpu_device", "scene_adaptive", "scene_blocks", "scene_blocks13", "scene_ignore", "scene_zero", "scene_m1", "scene_m2", "scene_threshold"):
        policy.bounded_int(result[key], 0, policy.NATIVE_INT_MAX, key)
    policy.bounded_int(result["gpu_queues"], 1, 8, "GPU queues")
    policy.bounded_int(result["processing_threads"], 0, 99, "processing threads")
    for key, maximum in (("buffered_frames", 9999), ("concurrent_frames", 99)):
        if type(result[key]) is str and result[key].isascii() and result[key].isdigit():
            result[key] = int(result[key])
        if result[key] != "auto":
            policy.bounded_int(result[key], 1, maximum, key)
    for key, maximum in (("scene_luma", 5), ("mask_area_sharp", 100), ("mask_area_blend", 1), ("search_lambda", 1000), ("search_plevel", 100)):
        item = result[key]
        policy.need(type(item) in (int, float) and math.isfinite(item) and 0 <= item <= maximum, "Invalid " + key)
    legacy_probe = dict(policy.DEFAULT_SETTINGS)
    legacy_probe["native"] = result["native"]
    policy.validate_settings(legacy_probe)
    if "legacy" in value:
        legacy = value["legacy"]
        policy.closed(legacy, ("settings", "controls"), "legacy settings")
        result["legacy"] = {"settings": policy.validate_settings(legacy["settings"]), "controls": validate_interpolation(legacy["controls"])}
    result["schema"] = 2
    return result


def _migrate(value):
    legacy = policy.validate_settings(value)
    document = default_document()
    controls = document["interpolation"]
    controls.update(fi_mode={0: 5, 1: 3, 2: 1, 3: 4}[legacy["interpolation_mode"]], fi_shader=legacy["shader"], fi_masking=legacy["artifacts_masking"]["area"], fi_precision=legacy["motion_precision"], mv_wide=1 if legacy["wide_search"] else 0, mv_coarsewidth=legacy["coarse_width"], fi_scene_changes=0 if legacy["scene_changes"] == "blend" else 1, output_bits=legacy["output_bits"], dither=legacy["dither"], backend=legacy["backend"], gpu_device=legacy["gpu_device"], buffered_frames=legacy["buffered_frames"], concurrent_frames=legacy["concurrent_frames"])
    for step, (block, overlap) in GRID.items():
        if (legacy["motion_grid"]["block"], legacy["motion_grid"]["overlap"]) == (block, overlap):
            controls["mv_grid"] = step
            break
    options = {key: json.loads(item) for key, item in policy.engine_options(legacy, Fraction(24), Fraction(120), 8).items()}
    stages = options["analyse"]["refine"]
    controls["mv_refine"] = 0 if not stages else "legacy"
    if len(stages) == 1 and set(stages[0]) <= {"thsad"} and stages[0].get("thsad", 200) > 0:
        controls["mv_refine"] = stages[0].get("thsad", 200)
    controls["mv_radius"] = "legacy"
    for radius in range(4):
        candidate = _raw_options(dict(controls, mv_radius=radius), Fraction(24), Fraction(120), 8, legacy["backend"] == "gpu")
        if _radius_fields(options["analyse"]["main"]["search"]) == _radius_fields(candidate["analyse"]["main"]["search"]):
            controls["mv_radius"] = radius
            break
    document["legacy"] = {"settings": legacy, "controls": copy.deepcopy(controls)}
    return document


def normalize_document(value):
    policy.need(type(value) is dict, "Settings document must be an object")
    if value.get("schema", 1) == 1:
        return _migrate(value)
    policy.need(value.get("schema") == 2 and type(value.get("schema")) is int, "Unsupported settings schema")
    policy.need(set(value) <= {"schema", "interpolation", "playback", "preferences", "legacy"}, "Unknown settings document fields")
    result = default_document()
    for section, defaults in (("playback", PLAYBACK_DEFAULTS), ("preferences", PREFERENCE_DEFAULTS)):
        supplied = value.get(section, {})
        policy.need(type(supplied) is dict and set(supplied) <= set(defaults), "Unknown " + section + " settings")
        result[section].update(supplied)
    interpolation = validate_interpolation(value.get("interpolation", {}))
    interpolation.pop("schema")
    policy.need("legacy" not in interpolation, "Legacy preservation belongs to the document")
    result["interpolation"] = interpolation
    _one_of(result["playback"]["profile"], ("auto", "smooth", "original"), "playback profile")
    _one_of(result["playback"]["rate"], (*RATES, "fixed"), "frame rate mode")
    policy.need(type(result["playback"]["fps"]) is str, "Fixed FPS must be a rational string")
    policy.target_rate(result["playback"]["fps"])
    _one_of(result["preferences"]["new_files"], ("append", "replace", "ask"), "new-file behavior")
    _one_of(result["preferences"]["after_start"], ("quit_app", "menu_bar", "keep_open"), "after-start behavior")
    if "legacy" in value:
        legacy = value["legacy"]
        policy.closed(legacy, ("settings", "controls"), "legacy preservation")
        baseline = validate_interpolation(legacy["controls"])
        baseline.pop("schema")
        policy.need("legacy" not in baseline, "Nested legacy settings are unavailable")
        result["legacy"] = {"settings": policy.validate_settings(legacy["settings"]), "controls": baseline}
    policy.need("legacy" in result or all(result["interpolation"][key] != "legacy" for key in ("mv_refine", "mv_radius")), "Legacy control values require preserved legacy settings")
    policy.need(len(json.dumps(result, allow_nan=False).encode("utf-8")) <= 131072, "Settings document is too large")
    return result


def load_document(path=None):
    if path is None or not Path(path).exists():
        return default_document()
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        policy.need(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "Settings must be a regular file")
        raw = stream.read(131073)
    policy.need(0 < len(raw) <= 131072, "Settings document is too large or empty")
    def pairs(items):
        result = {}
        for key, item in items:
            policy.need(key not in result, "Duplicate settings key")
            result[key] = item
        return result
    return normalize_document(json.loads(raw.decode("utf-8"), object_pairs_hook=pairs))


def save_document(path, document):
    result = normalize_document(document)
    settings = playback_settings(result)
    depth = settings["output_bits"] if type(settings["output_bits"]) is int else 8
    policy.engine_options(settings, Fraction(24), Fraction(120), depth)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    filename = None
    try:
        descriptor, filename = tempfile.mkstemp(prefix=".settings-", dir=destination.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, destination)
        filename = None
    finally:
        if filename is not None:
            os.unlink(filename)
    return result


def playback_settings(document):
    value = normalize_document(document)
    if "legacy" in value and value["interpolation"] == value["legacy"]["controls"]:
        return copy.deepcopy(value["legacy"]["settings"])
    settings = dict(value["interpolation"], schema=2)
    if "legacy" in value:
        settings["legacy"] = copy.deepcopy(value["legacy"])
    return validate_interpolation(settings)


def rate_argument(document):
    value = normalize_document(document)["playback"]
    return value["fps"] if value["rate"] == "fixed" else RATES[value["rate"]]


def synthesis_bits(settings, width, height, source_bits, target):
    value = validate_interpolation(settings)
    selected = value["output_bits"]
    if selected == "auto":
        limit = value["limit_10bit"]
        return 10 if source_bits >= 10 and (limit >= 1000 or limit > 0 and width * height * target <= 3840 * 2160 * limit) else 8
    if selected == "source":
        return 8 if source_bits <= 8 else 10 if source_bits <= 10 else 16
    return selected


def _raw_options(value, source, target, bits, gpu):
    pel = max(1, value["fi_precision"])
    step = value["mv_grid"]
    block, overlap = GRID[step]
    if step == 7 and value["mv_refine"]:
        overlap = 0
    if not gpu and step // (2 if value["mv_refine"] else 1) % 2:
        overlap = 0
    scene_mode = {1: 2, 2: 3, 3: 1, 4: 3, 5: 0}[value["fi_mode"]]
    if scene_mode == 2 and target / source < 2:
        scene_mode = 1
    legacy = dict(policy.DEFAULT_SETTINGS)
    legacy.update(interpolation_mode=scene_mode, shader=11 if value["fi_shader"] == 10 else value["fi_shader"], artifacts_masking={"cover": 80 if value["fi_shader"] >= 21 else 100, "area": value["fi_masking"]}, motion_precision=pel, motion_grid={"block": block, "overlap": overlap}, decrease_grid_step=1 if value["mv_refine"] else 0, coarse_width=value["mv_coarsewidth"] if value["mv_coarsewidth"] > 100 else 1050, scene_changes="blend" if value["fi_scene_changes"] == 0 else "nearest", backend="gpu" if gpu else "cpu", gpu_device=value["gpu_device"], native={"super": {}, "analyse": {}, "smooth": {}})
    options = {key: json.loads(item) for key, item in policy.engine_options(legacy, source, target, bits, gpu=gpu).items()}
    options["super"]["full"] = value["fi_precision"] >= 2
    analyse, smooth = options["analyse"], options["smooth"]
    analyse["exact"] = True
    analyse["vectors"] = 2 if value["fi_shader"] == 1 else 3
    search = analyse["main"]["search"]
    search.update(type=4 if value["fi_precision"] == 0 else 2, distance=0 if value["fi_precision"] == 0 else -2 * pel)
    radius = 2 if value["mv_radius"] == "legacy" else value["mv_radius"]
    search["coarse"].update(type=2 if radius < 2 else 4, distance=(-6, -6, -8, -12)[radius], satd=radius != 0)
    wide = value["mv_wide"]
    search["coarse"]["bad"] = {"sad": 2000 if wide >= 2 else 1000, "range": (0, -24, 24, -24)[wide]}
    analyse["main"]["penalty"] = {"lambda": value["search_lambda"], "plevel": value["search_plevel"]}
    threshold = 200 if value["mv_refine"] == "legacy" else value["mv_refine"]
    analyse["refine"] = [{"thsad": threshold}] if threshold else []
    smooth["gpu_qn"] = value["gpu_queues"]
    smooth["block"] = value["fi_shader"] == 10 and not gpu
    smooth["scene"]["adaptive"] = value["scene_adaptive"]
    smooth["scene"]["luma"] = value["scene_luma"]
    for field, key in (("blocks", "scene_blocks"), ("blocks13", "scene_blocks13"), ("ignore", "scene_ignore"), ("zero", "scene_zero"), ("m1", "scene_m1"), ("m2", "scene_m2"), ("scene", "scene_threshold")):
        smooth["scene"]["limits"][field] = value[key]
    if value["fi_mode"] == 2:
        smooth["scene"]["limits"].update(m1=0, m2=0)
    smooth["mask"].update(area_sharp=value["mask_area_sharp"], area_blend=value["mask_area_blend"])
    return policy.merged(options, value["native"])


def _radius_fields(search):
    return {"type": search["type"], "distance": search["distance"], "coarse": {key: search["coarse"][key] for key in ("type", "distance", "satd")}}


def _changed_options(before, after, base):
    result = copy.deepcopy(base)
    for key, value in after.items():
        if key not in before or before[key] != value:
            if type(value) is dict and type(before.get(key)) is dict and type(result.get(key)) is dict:
                result[key] = _changed_options(before[key], value, result[key])
            else:
                result[key] = copy.deepcopy(value)
    return result


def native_options(settings, source, target, bits, gpu=None):
    value = validate_interpolation(settings)
    source, target = policy.source_rate(source), policy.target_rate(target)
    policy.need(policy.native_timing(source, target), "Interpolation rates exceed native phase timing")
    policy.need(bits in (8, 10, 16), "Unsupported synthesis depth")
    policy.need(gpu is None or type(gpu) is bool, "GPU override must be boolean")
    gpu = value["backend"] == "gpu" if gpu is None else gpu
    policy.need("legacy" in value or all(value[key] != "legacy" for key in ("mv_refine", "mv_radius")), "Legacy control values require preserved legacy settings")
    options = _raw_options(value, source, target, bits, gpu)
    if "legacy" in value:
        legacy = value["legacy"]
        original = {key: json.loads(item) for key, item in policy.engine_options(legacy["settings"], source, target, bits, gpu=gpu).items()}
        baseline = _raw_options(validate_interpolation(legacy["controls"]), source, target, bits, gpu)
        current = options
        options = _changed_options(baseline, current, original)
        if value["mv_refine"] == "legacy" or legacy["controls"]["mv_refine"] == "legacy":
            selected = original if value["mv_refine"] == "legacy" else current
            options["analyse"]["refine"] = copy.deepcopy(selected["analyse"]["refine"])
        if value["mv_radius"] == "legacy" or legacy["controls"]["mv_radius"] == "legacy":
            selected = original if value["mv_radius"] == "legacy" else current
            search = options["analyse"]["main"]["search"]
            options["analyse"]["main"]["search"] = policy.merged(search, _radius_fields(selected["analyse"]["main"]["search"]))
        options = policy.merged(options, value["native"])
    policy.validate_engine_options(options, bits, gpu, target / source)
    return {key: json.dumps(item, separators=(",", ":"), allow_nan=False) for key, item in options.items()}


def describe_settings():
    fields = []
    def field(path, label, kind, group="Interpolation", options=None, **extra):
        item = {"path": path, "label": label, "kind": kind, "group": group, "supported": True}
        if options is not None:
            item["options"] = [{"value": value, "label": text} for value, text in options]
        item.update(extra)
        fields.append(item)
    choices = {
        "fi_mode": ("Frames interpolation mode", [(1, "2m — Minimum artifacts"), (2, "1.5m — Less artifacts"), (3, "1m — Average"), (4, "Adaptive"), (5, "Uniform — Maximum fluidity")]),
        "fi_shader": ("SVP shader", [(1, "1 — Fastest"), (2, "2 — Sharp"), (10, "10 — Blocks (CPU)"), (11, "11 — Simple Lite"), (13, "13 — Standard"), (21, "21 — Simple"), (22, "22 — Native 22"), (23, "23 — Complicated"), *[(value, str(value) + " — Extended native algorithm") for value in range(90, 101)]]),
        "fi_precision": ("Motion vectors precision", [(0, "Fast pixel — Reduced super"), (1, "Pixel"), (2, "Half pixel"), (4, "Quarter pixel")]),
        "mv_grid": ("Motion vectors grid", [(value, str(value) + " pixel step") for value in GRID]),
        "mv_radius": ("Search radius", [*enumerate(("Small and fast", "Small", "Average", "Large")), ("legacy", "Legacy/custom — Preserve imported search")]),
        "mv_wide": ("Wide search", list(enumerate(("Disabled", "Standard", "Wide UMH", "Wide exhaustive")))),
        "fi_scene_changes": ("Processing of scene changes", [(0, "Blend adjacent frames"), (1, "Repeat frame")]),
        "output_bits": ("Synthesis depth", [("auto", "Automatic 10-bit policy"), (8, "8-bit"), (10, "10-bit"), (16, "16-bit"), ("source", "Preserve source depth")]),
        "dither": ("Conversion dithering", [(item, item.replace("_", " ").title()) for item in policy.DITHER_TYPES]),
        "backend": ("Rendering device", [("gpu", "GPU — OpenCL"), ("cpu", "CPU")]),
    }
    for key, (label, options) in choices.items():
        field("interpolation." + key, label, "choice", options=options)
    field("interpolation.fi_masking", "Artifacts masking", "integer", min=0, max=1000, step=1, help="Native mask area; 0 disables additional area masking. Official named-level thresholds have not been established.")
    field("interpolation.mv_refine", "Decrease grid step", "text", help="A positive integer SAD threshold enables one refinement stage; 0 disables refinement. The value legacy explicitly preserves imported custom or multiple stages and is available only for migrated settings.")
    field("interpolation.mv_coarsewidth", "Width of top coarse level", "integer", min=0, max=16384, step=1, help="Values up to 100 use the default 1050-pixel coarse width.")
    field("interpolation.fi_duplicates", "Duplicate frames removal", "choice", options=[(0, "Unavailable — Off")], supported=False, help=LIMITATIONS[1])
    labels = {"limit_10bit": "Automatic 10-bit limit", "gpu_device": "GPU device index", "gpu_queues": "GPU queues", "processing_threads": "Processing threads", "scene_adaptive": "Adaptive mode thresholds", "scene_blocks": "Scene block threshold", "scene_blocks13": "Scene block threshold 13", "scene_ignore": "Ignored scene blocks", "scene_zero": "Zero-vector scene threshold", "scene_m1": "Scene threshold m1", "scene_m2": "Scene threshold m2", "scene_threshold": "Scene change threshold", "scene_luma": "Scene luma", "mask_area_sharp": "Mask area sharpness", "mask_area_blend": "Mask area blend", "search_lambda": "Search coherence", "search_plevel": "Search coherence scaling"}
    for key, label in labels.items():
        integer = type(INTERPOLATION_DEFAULTS[key]) is int
        maximum = 8 if key == "gpu_queues" else 99 if key == "processing_threads" else 1 if key == "mask_area_blend" else 5 if key == "scene_luma" else 100 if key in ("mask_area_sharp", "search_plevel") else 1000 if key == "search_lambda" else 100000
        field("interpolation." + key, label, "integer" if integer else "number", "Advanced", min=1 if key == "gpu_queues" else 0, max=maximum, step=1 if integer else 0.1)
    for key, label in (("buffered_frames", "mpv buffered frames"), ("concurrent_frames", "mpv concurrent frames")):
        field("interpolation." + key, label, "text", "Advanced", help="auto or a positive integer; use auto unless tuning against the installed mpv.")
    field("playback.profile", "Playback mode", "choice", "Playback", [("auto", "Interpolate when possible"), ("smooth", "Require interpolation"), ("original", "Original")])
    field("playback.rate", "Target frame rate", "choice", "Playback", [(key, label) for key, label in (("monitor", "Monitor refresh rate"), ("monitor_half", "Half monitor refresh"), ("monitor_third", "One-third monitor refresh"), ("source_2", "Source ×2"), ("source_2_5", "Source ×2.5"), ("source_3", "Source ×3"), ("source_4", "Source ×4"), ("source_5", "Source ×5"), ("fixed", "Fixed FPS"))])
    field("playback.fps", "Fixed FPS", "text", "Playback", help="A positive integer, decimal or fraction, such as 120000/1001.")
    field("preferences.new_files", "New files during playback", "choice", "Behavior", [("append", "Add to Queue"), ("replace", "Replace Playlist"), ("ask", "Ask Every Time")])
    field("preferences.after_start", "After Playback Starts", "choice", "Behavior", [("quit_app", "Quit App"), ("menu_bar", "Move to Menu Bar"), ("keep_open", "Keep Window Open")])
    return {"schema": 2, "defaults": default_document(), "fields": fields, "limitations": list(LIMITATIONS)}
