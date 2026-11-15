import base64
import copy
import importlib.util
import json
import os
import shutil
from fractions import Fraction
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "player" / "policy.py"


def policy():
    if not POLICY_PATH.is_file():
        raise AssertionError("Standalone player policy has not been implemented")
    spec = importlib.util.spec_from_file_location("player_policy", POLICY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RateTests(unittest.TestCase):
    def test_fractional_movie_follows_monitor_instead_of_fivefold_limit(self):
        p = policy()
        self.assertEqual(p.select_rate(Fraction(24000, 1001), 240, "auto"), Fraction(240))

    def test_integer_movie_uses_full_monitor_refresh(self):
        self.assertEqual(policy().select_rate(Fraction(24), 240, "auto"), Fraction(240))

    def test_automatic_rate_is_not_limited_to_a_fixed_refresh_list(self):
        p = policy()
        for display, expected in ((144, Fraction(144)), (165, Fraction(165)), (240.0024, Fraction(300003, 1250)), (360, Fraction(360))):
            for profile in ("auto", "smooth"):
                with self.subTest(display=display, profile=profile):
                    self.assertEqual(p.select_rate(Fraction(24), display, profile), expected)

    def test_fractional_monitor_refresh_keeps_a_usable_rational_rate(self):
        target = policy().select_rate(Fraction(24000, 1001), 239.76023976023976)
        self.assertEqual(target, Fraction(240000, 1001))

    def test_precise_monitor_refresh_keeps_native_phase_denominator_in_range(self):
        p = policy()
        source = Fraction(24000, 1001)
        for display in (239.999994, 144.000001, 59.999999):
            with self.subTest(display=display):
                target = p.select_rate(source, display)
                self.assertLessEqual(abs(float(target) - display), 0.00001)
                self.assertLessEqual((target / source).denominator, 2147483647)

    def test_automatic_monitor_rate_reaches_native_options(self):
        p = policy()
        target = p.select_rate(Fraction(24000, 1001), 240.0024)
        smooth = json.loads(p.engine_options(p.DEFAULT_SETTINGS, Fraction(24000, 1001), target, 10)["smooth"])
        self.assertEqual(smooth["rate"], {"num": 300003, "den": 1250, "abs": True})

    def test_monitor_above_source_accepts_less_than_double_rate(self):
        p = policy()
        for profile in ("auto", "smooth"):
            self.assertEqual(p.select_rate(Fraction(60), 100, profile), Fraction(100))

    def test_monitor_at_or_below_source_preserves_profile_behavior(self):
        p = policy()
        for display in (60, 59.94):
            with self.subTest(display=display):
                self.assertIsNone(p.select_rate(Fraction(60), display, "auto"))
                with self.assertRaises(ValueError):
                    p.select_rate(Fraction(60), display, "smooth")

    def test_invalid_or_unrepresentable_monitor_rate_preserves_profile_behavior(self):
        p = policy()
        for display in (None, "unknown", 0, -1, float("nan"), float("inf"), 2147483648):
            with self.subTest(display=display):
                self.assertIsNone(p.select_rate(Fraction(24), display, "auto"))
                with self.assertRaises(ValueError):
                    p.select_rate(Fraction(24), display, "smooth")

    def test_explicit_rate_supports_bbb_30_to_60_without_display(self):
        self.assertEqual(policy().select_rate(Fraction(30), 0, "smooth", "60"), Fraction(60))

    def test_high_source_and_explicit_target_rates_have_no_test_ceiling(self):
        p = policy()
        self.assertEqual(p.source_rate(120), Fraction(120))
        self.assertEqual(p.select_rate(Fraction(120), 0, "smooth", "360"), Fraction(360))
        self.assertEqual(p.select_rate(Fraction(120), 0, "smooth", "144"), Fraction(144))

    def test_precise_automatic_refresh_is_native_safe_for_high_source_rates(self):
        p = policy()
        source = Fraction(120000, 1001)
        target = p.select_rate(source, 240.000001, "smooth")
        self.assertLessEqual(abs(float(target) - 240.000001), 0.00001)
        self.assertLessEqual((target / source).denominator, 2147483647)
        self.assertIsInstance(p.engine_options(p.DEFAULT_SETTINGS, source, target, 16), dict)

    def test_explicit_rate_rejects_native_phase_denominator_overflow(self):
        p = policy()
        with self.assertRaisesRegex(ValueError, "native"):
            p.select_rate(Fraction(24000, 1001), 0, "smooth", "239000001/1000000")

    def test_native_phase_shift_overflow_is_rejected(self):
        p = policy()
        with self.assertRaisesRegex(ValueError, "native"):
            p.select_rate(Fraction(1), 0, "smooth", "8388608")

    def test_unknown_display_does_not_silently_claim_interpolation(self):
        p = policy()
        self.assertIsNone(p.select_rate(Fraction(24), 0, "auto"))
        with self.assertRaises(ValueError):
            p.select_rate(Fraction(24), 0, "smooth")

    def test_exact_119_display_is_not_promoted_to_120(self):
        self.assertEqual(policy().select_rate(Fraction(24), 119, "auto"), Fraction(119))

    def test_source_fps_normalizes_decimal_ntsc_metadata(self):
        self.assertEqual(policy().source_rate(23.976), Fraction(24000, 1001))
        self.assertEqual(policy().source_rate(29.97), Fraction(30000, 1001))

    def test_invalid_rates_are_rejected(self):
        p = policy()
        for value in (0, -1, float("nan"), float("inf"), True, 2147483648):
            with self.subTest(value=value), self.assertRaises(ValueError):
                p.source_rate(value)
        for value in ("0", "-60", "1/0", "nan", "24", "2147483648"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                p.select_rate(Fraction(24), 240, "smooth", value)

    def test_unrepresentable_source_rate_is_not_rounded_to_zero(self):
        with self.assertRaises(ValueError):
            policy().source_rate(0.000000001)


class SettingsTests(unittest.TestCase):
    def test_example_settings_match_the_launcher_defaults(self):
        p = policy()
        self.assertEqual(p.read_settings(ROOT / "player" / "settings.json"), p.DEFAULT_SETTINGS)

    def test_all_eleven_settings_reach_the_native_options(self):
        p = policy()
        cases = (
            ("interpolation_mode", 1, "smooth", ("scene", "mode"), 1),
            ("shader", 22, "smooth", ("algo",), 22),
            ("artifacts_masking", {"cover": 65, "area": 10}, "smooth", ("mask",), {"cover": 65, "area": 10, "still": False, "peel": False}),
            ("motion_precision", 1, "super", ("pel",), 1),
            ("motion_grid", {"block": 8, "overlap": 1}, "analyse", ("block",), {"w": 8, "h": 8, "overlap": 1}),
            ("decrease_grid_step", 1, "analyse", ("refine",), [{"thsad": 200}]),
            ("search_radius", {"fine": 0, "coarse": -6}, "analyse", ("main", "search", "coarse", "distance"), -6),
            ("wide_search", False, "analyse", ("main", "search", "coarse", "bad", "range"), 0),
            ("coarse_width", 1024, "analyse", ("main", "search", "coarse", "width"), 1024),
            ("scene_changes", "blend", "smooth", ("scene", "blend"), True),
            ("duplicate_frames_removal", True, "smooth", ("dedup",), 1),
        )
        self.assertEqual({case[0] for case in cases}, set(p.DEFAULT_SETTINGS) - {"schema", "rate_mode", "output_bits", "backend", "gpu_device", "buffered_frames", "concurrent_frames", "native", "dither"})
        for key, value, section, path, expected in cases:
            with self.subTest(setting=key):
                settings = copy.deepcopy(p.DEFAULT_SETTINGS)
                settings[key] = value
                actual = json.loads(p.engine_options(settings, Fraction(24), Fraction(48), 8)[section])
                for part in path:
                    actual = actual[part]
                self.assertEqual(actual, expected)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "Requires a POSIX named pipe")
    def test_nonregular_settings_file_is_rejected_without_waiting_for_a_writer(self):
        p = policy()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            os.mkfifo(path)
            try:
                completed = subprocess.run([sys.executable, "-B", str(ROOT / "player" / "launch.py"), "--settings", str(path), "--", str(path)], capture_output=True, text=True, timeout=2)
            except subprocess.TimeoutExpired:
                self.fail("Settings input blocked waiting for a named-pipe writer")
            self.assertEqual(completed.returncode, 2)
            self.assertIn("regular", completed.stderr.lower())

    def test_default_options_keep_analysis_and_protection_settings(self):
        p = policy()
        options = p.engine_options(p.DEFAULT_SETTINGS, Fraction(24), Fraction(120), 10)
        sup, analyse, smooth = (json.loads(options[key]) for key in ("super", "analyse", "smooth"))
        self.assertEqual(sup["pel"], 2)
        self.assertEqual(analyse["block"], {"w": 16, "h": 16, "overlap": 2})
        self.assertEqual(smooth["rate"], {"num": 5, "den": 1, "abs": False})
        self.assertEqual(smooth["algo"], 23)
        self.assertEqual(smooth["mask"]["cover"], 80)
        self.assertTrue(smooth["scene"]["force13"])
        self.assertFalse(smooth["scene"]["blend"])

    def test_settings_reject_unknown_keys_and_non_boolean_values(self):
        p = policy()
        for change in ({"unexpected": 1}, {"wide_search": "false"}, {"shader": 12}):
            value = copy.deepcopy(p.DEFAULT_SETTINGS)
            value.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                p.validate_settings(value)

    def test_high_depth_dedup_is_rejected(self):
        p = policy()
        settings = copy.deepcopy(p.DEFAULT_SETTINGS)
        settings["duplicate_frames_removal"] = True
        for bits in (10, 16):
            with self.subTest(bits=bits), self.assertRaisesRegex(ValueError, "8-bit"):
                p.engine_options(settings, Fraction(24), Fraction(120), bits)

    def test_native_supported_coarse_widths_are_forwarded(self):
        p = policy()
        for width in (0, 64, 3840, 2147483647):
            with self.subTest(width=width):
                settings = copy.deepcopy(p.DEFAULT_SETTINGS)
                settings["coarse_width"] = width
                options = p.engine_options(settings, Fraction(24), Fraction(120), 8)
                self.assertEqual(json.loads(options["analyse"])["main"]["search"]["coarse"]["width"], width)
        for width in (-1, 2147483648, True):
            settings = copy.deepcopy(p.DEFAULT_SETTINGS)
            settings["coarse_width"] = width
            with self.subTest(width=width), self.assertRaises(ValueError):
                p.validate_settings(settings)

    def test_half_pixel_precision_supports_nonzero_fine_search(self):
        p = policy()
        settings = copy.deepcopy(p.DEFAULT_SETTINGS)
        settings["search_radius"]["fine"] = -4
        options = p.engine_options(settings, Fraction(24), Fraction(120), 8)
        self.assertEqual(json.loads(options["super"])["pel"], 2)
        self.assertEqual(json.loads(options["analyse"])["main"]["search"]["distance"], -4)

    def test_refinement_accepts_native_supported_grids_and_fine_search(self):
        p = policy()
        for block, overlap in ((8, 0), (8, 2), (16, 1), (32, 3)):
            with self.subTest(block=block, overlap=overlap):
                settings = copy.deepcopy(p.DEFAULT_SETTINGS)
                settings["decrease_grid_step"] = 1
                settings["motion_grid"] = {"block": block, "overlap": overlap}
                settings["search_radius"]["fine"] = -2
                options = p.engine_options(settings, Fraction(24), Fraction(120), 8)
                self.assertEqual(json.loads(options["analyse"])["refine"], [{"thsad": 200}])

    def test_refinement_rejects_native_one_pixel_overlap(self):
        p = policy()
        settings = copy.deepcopy(p.DEFAULT_SETTINGS)
        settings["decrease_grid_step"] = 1
        settings["motion_grid"] = {"block": 8, "overlap": 1}
        with self.assertRaisesRegex(ValueError, "overlap"):
            p.engine_options(settings, Fraction(24), Fraction(120), 8)

    def test_cpu_synthesis_accepts_native_high_depth_reference_rendering(self):
        p = policy()
        for bits in (10, 16):
            with self.subTest(bits=bits):
                options = p.engine_options(p.DEFAULT_SETTINGS, Fraction(24), Fraction(120), bits, gpu=False)
                self.assertEqual(json.loads(options["super"])["gpu"], 0)
                self.assertTrue(json.loads(options["analyse"])["exact"])
                self.assertEqual(json.loads(options["smooth"])["gpuid"], -1)

    def test_cpu_synthesis_rejects_native_odd_overlap(self):
        p = policy()
        for block, overlap, refinement in ((8, 1, 0), (16, 1, 1)):
            settings = copy.deepcopy(p.DEFAULT_SETTINGS)
            settings["motion_grid"] = {"block": block, "overlap": overlap}
            settings["decrease_grid_step"] = refinement
            with self.subTest(block=block, refinement=refinement), self.assertRaisesRegex(ValueError, "overlap"):
                p.engine_options(settings, Fraction(24), Fraction(120), 8, gpu=False)

    def test_gpu_synthesis_accepts_native_16_bit_depth(self):
        p = policy()
        options = p.engine_options(p.DEFAULT_SETTINGS, Fraction(120), Fraction(144), 16)
        self.assertEqual(json.loads(options["smooth"])["rate"], {"num": 144, "den": 1, "abs": True})

    def test_double_rate_is_required_only_for_native_scene_mode_two(self):
        p = policy()
        for mode in (0, 1, 3):
            settings = copy.deepcopy(p.DEFAULT_SETTINGS)
            settings["interpolation_mode"] = mode
            options = p.engine_options(settings, Fraction(60), Fraction(100), 8)
            self.assertEqual(json.loads(options["smooth"])["rate"], {"num": 100, "den": 1, "abs": True})
        settings["interpolation_mode"] = 2
        with self.assertRaisesRegex(ValueError, "mode 2"):
            p.engine_options(settings, Fraction(60), Fraction(100), 8)

    def test_unsafe_relative_rate_products_use_native_absolute_options(self):
        p = policy()
        source = Fraction(2147483647, 1000000)
        target = p.select_rate(source, 0, "smooth", "2147483647")
        options = p.engine_options(p.DEFAULT_SETTINGS, source, target, 8)
        self.assertEqual(json.loads(options["smooth"])["rate"], {"num": 2147483647, "den": 1, "abs": True})

    def test_direct_engine_options_reject_native_unsafe_rates(self):
        p = policy()
        for source, target in ((Fraction(24000, 1001), Fraction(239000001, 1000000)), (Fraction(1), Fraction(8388608)), (Fraction(24), Fraction(2147483648))):
            with self.subTest(source=source, target=target), self.assertRaisesRegex(ValueError, "native"):
                p.engine_options(p.DEFAULT_SETTINGS, source, target, 8)

    def test_noninteger_output_ratio_uses_absolute_rate(self):
        options = policy().engine_options(policy().DEFAULT_SETTINGS, Fraction(25), Fraction(120), 8)
        self.assertEqual(json.loads(options["smooth"])["rate"], {"num": 120, "den": 1, "abs": True})

    def test_cpu_smoke_options_do_not_initialize_opencl(self):
        options = policy().engine_options(policy().DEFAULT_SETTINGS, Fraction(24), Fraction(48), 8, gpu=False)
        self.assertEqual(json.loads(options["super"])["gpu"], 0)
        self.assertTrue(json.loads(options["analyse"])["exact"])
        self.assertEqual(json.loads(options["smooth"])["gpuid"], -1)

    def test_partial_settings_fill_defaults_without_mutating_the_input(self):
        p = policy()
        value = {"search_radius": {"fine": -12}, "native": {"smooth": {"scene": {"adaptive": 123}}}}
        settings = p.validate_settings(value)
        self.assertEqual(settings["search_radius"], {"fine": -12, "coarse": -8})
        self.assertEqual(settings["output_bits"], 8)
        self.assertEqual(settings["backend"], "gpu")
        self.assertEqual(settings["gpu_device"], 0)
        self.assertEqual(settings["buffered_frames"], "auto")
        self.assertEqual(settings["concurrent_frames"], "auto")
        self.assertEqual(settings["native"]["super"], {})
        settings["native"]["smooth"]["scene"]["adaptive"] = 0
        self.assertEqual(value["native"]["smooth"]["scene"]["adaptive"], 123)

    def test_output_depth_and_backend_controls_reject_invalid_choices(self):
        p = policy()
        for bits in (8, 10, 16, "source"):
            with self.subTest(bits=bits):
                self.assertEqual(p.validate_settings({"output_bits": bits})["output_bits"], bits)
        for change in ({"output_bits": 12}, {"output_bits": True}, {"backend": "auto"}, {"gpu_device": -1}, {"gpu_device": True}, {"buffered_frames": 0}, {"concurrent_frames": -1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                p.validate_settings(change)

    def test_configured_backend_and_gpu_device_reach_native_options(self):
        p = policy()
        gpu = p.engine_options({"gpu_device": 12}, Fraction(24), Fraction(120), 8)
        self.assertEqual(json.loads(gpu["super"])["gpu"], 1)
        self.assertEqual(json.loads(gpu["smooth"])["gpuid"], 12)
        cpu = p.engine_options({"backend": "cpu"}, Fraction(24), Fraction(120), 8)
        self.assertEqual(json.loads(cpu["super"])["gpu"], 0)
        self.assertTrue(json.loads(cpu["analyse"])["exact"])
        self.assertEqual(json.loads(cpu["smooth"])["gpuid"], -1)
        forced = p.engine_options({"backend": "cpu"}, Fraction(24), Fraction(120), 8, gpu=True)
        self.assertEqual(json.loads(forced["super"])["gpu"], 1)

    def test_all_native_algorithms_and_quarter_pixel_precision_are_available(self):
        p = policy()
        for shader in (1, 2, 11, 13, 21, 22, 23, 90, 100):
            with self.subTest(shader=shader):
                options = p.engine_options({"shader": shader, "motion_precision": 4}, Fraction(24), Fraction(120), 8)
                self.assertEqual(json.loads(options["smooth"])["algo"], shader)
                self.assertEqual(json.loads(options["super"])["pel"], 4)

    def test_native_overrides_merge_last_and_keep_other_default_options(self):
        p = policy()
        settings = {"native": {
            "super": {"scale": {"down": 2}},
            "analyse": {"block": {"w": 16, "h": 8}, "main": {"search": {"type": 2, "distance": -16, "coarse": {"trymany": True}}, "penalty": {"lambda": 2.5, "plevel": 1.0}}, "refine": [{"thsad": 100, "search": {"type": 3, "distance": 4}}]},
            "smooth": {"algo": 2, "scene": {"adaptive": 123, "luma": 2.0, "limits": {"m1": 1200}}, "mask": {"cover": 0, "area": 200, "area_sharp": 0.8}, "debug": {"vectors": True, "qmap": True, "tt": True}, "gpu_qn": 4, "linear": False, "dither": True},
        }}
        options = {key: json.loads(value) for key, value in p.engine_options(settings, Fraction(24), Fraction(120), 8).items()}
        self.assertEqual(options["super"]["scale"], {"up": 0, "down": 2})
        self.assertEqual(options["analyse"]["block"], {"w": 16, "h": 8, "overlap": 2})
        self.assertEqual(options["analyse"]["main"]["search"]["type"], 2)
        self.assertEqual(options["analyse"]["main"]["search"]["coarse"]["distance"], -8)
        self.assertTrue(options["analyse"]["main"]["search"]["coarse"]["trymany"])
        self.assertEqual(options["analyse"]["main"]["penalty"], {"lambda": 2.5, "plevel": 1.0})
        self.assertEqual(options["analyse"]["refine"], [{"thsad": 100, "search": {"type": 3, "distance": 4}}])
        self.assertEqual(options["smooth"]["scene"]["limits"]["m1"], 1200)
        self.assertEqual(options["smooth"]["scene"]["limits"]["m2"], 2800)
        self.assertEqual(options["smooth"]["mask"]["cover"], 0)
        self.assertEqual(options["smooth"]["mask"]["area"], 200)
        self.assertTrue(options["smooth"]["debug"]["vectors"])
        self.assertTrue(options["smooth"]["debug"]["tt"])
        self.assertEqual(options["smooth"]["gpu_qn"], 4)
        self.assertFalse(options["smooth"]["linear"])
        self.assertTrue(options["smooth"]["dither"])

    def test_flat_controls_allow_native_masks_search_ranges_and_refinement_counts(self):
        p = policy()
        settings = {"artifacts_masking": {"cover": 0, "area": 200}, "search_radius": {"fine": -16, "coarse": 32}, "motion_grid": {"block": 32, "overlap": 0}, "decrease_grid_step": 3}
        options = {key: json.loads(value) for key, value in p.engine_options(settings, Fraction(24), Fraction(120), 8).items()}
        self.assertEqual(options["smooth"]["mask"]["cover"], 0)
        self.assertEqual(options["smooth"]["mask"]["area"], 200)
        self.assertEqual(options["analyse"]["main"]["search"]["distance"], -16)
        self.assertEqual(len(options["analyse"]["refine"]), 3)

    def test_native_structural_timing_and_backend_keys_are_reserved(self):
        p = policy()
        for section, key, value in (("super", "gpu", 0), ("analyse", "gpu", 0), ("smooth", "rate", {"num": 100000000000}), ("smooth", "api", 2), ("smooth", "gpuid", -1), ("smooth", "render", "null")):
            with self.subTest(section=section, key=key), self.assertRaisesRegex(ValueError, "reserved"):
                p.validate_settings({"native": {section: {key: value}}})

    def test_native_overrides_cannot_bypass_scene_depth_and_overlap_guards(self):
        p = policy()
        cases = (
            ({"native": {"smooth": {"scene": {"mode": 2}}}}, 8, "gpu", "mode 2"),
            ({"native": {"smooth": {"dedup": 0.25}}}, 16, "gpu", "8-bit"),
            ({"native": {"analyse": {"block": {"w": 8, "h": 8, "overlap": 1}}}}, 8, "cpu", "overlap"),
            ({"native": {"analyse": {"refine": [{}, {}, {}]}}}, 8, "gpu", "refinement"),
            ({"native": {"super": {"pel": 4}}}, 16, "cpu", "precision"),
            ({"native": {"smooth": {"cubic": 1}}}, 8, "cpu", "cubic"),
            ({"native": {"smooth": {"block": True}}}, 8, "gpu", "block"),
        )
        for settings, bits, backend, message in cases:
            settings["backend"] = backend
            with self.subTest(settings=settings), self.assertRaisesRegex(ValueError, message):
                p.engine_options(settings, Fraction(60), Fraction(100), bits)

    def test_native_overrides_are_validated_after_replacing_flat_controls(self):
        p = policy()
        options = p.engine_options({"interpolation_mode": 2, "duplicate_frames_removal": True, "native": {"smooth": {"scene": {"mode": 0}, "dedup": 0}}}, Fraction(60), Fraction(100), 16)
        self.assertEqual(json.loads(options["smooth"])["scene"]["mode"], 0)
        self.assertEqual(json.loads(options["smooth"])["dedup"], 0)

    def test_native_search_overrides_cannot_bypass_integer_arithmetic_guards(self):
        p = policy()
        for analyse in ({"main": {"search": {"distance": -2147483647}}}, {"main": {"search": {"coarse": {"distance": 2147483648}}}}, {"refine": [{"search": {"distance": -2147483648}}]}):
            with self.subTest(analyse=analyse), self.assertRaisesRegex(ValueError, "search"):
                p.engine_options({"native": {"analyse": analyse}}, Fraction(24), Fraction(120), 8)

    def test_native_metric_scaling_rejects_overflow_after_overrides(self):
        p = policy()
        cases = (
            {"main": {"penalty": {"lsad": 4194304}}},
            {"main": {"penalty": {"lsad": -4194305}}},
            {"main": {"search": {"coarse": {"bad": {"sad": 4194304}}}}},
            {"main": {"search": {"coarse": {"bad": {"sad": -4194305}}}}},
            {"refine": [{"thsad": 16777216}]},
            {"refine": [{"thsad": -16777217}]},
        )
        for analyse in cases:
            with self.subTest(analyse=analyse), self.assertRaisesRegex(ValueError, "scaled"):
                p.engine_options({"native": {"analyse": analyse}}, Fraction(24), Fraction(120), 8)

    def test_native_metric_bounds_follow_rectangular_blocks_and_refinement_stage(self):
        p = policy()
        for width, height, low, high in ((16, 16, -4194304, 4194303), (8, 4, -33554432, 33554431), (32, 32, -1048576, 1048575)):
            for metric in (low, high):
                with self.subTest(width=width, height=height, metric=metric):
                    settings = {"native": {"analyse": {"block": {"w": width, "h": height, "overlap": 0}, "main": {"penalty": {"lsad": metric}, "search": {"coarse": {"bad": {"sad": metric}}}}}}}
                    analyse = json.loads(p.engine_options(settings, Fraction(24), Fraction(120), 8)["analyse"])
                    self.assertEqual(analyse["main"]["penalty"]["lsad"], metric)
                    self.assertEqual(analyse["main"]["search"]["coarse"]["bad"]["sad"], metric)
        settings = {"motion_grid": {"block": 32, "overlap": 0}, "native": {"analyse": {"refine": [{"thsad": 4194303}, {"thsad": 16777215}, {"thsad": 67108863}]}}}
        analyse = json.loads(p.engine_options(settings, Fraction(24), Fraction(120), 8)["analyse"])
        self.assertEqual(analyse["refine"], [{"thsad": 4194303}, {"thsad": 16777215}, {"thsad": 67108863}])
        for index, value in ((0, 4194304), (1, 16777216), (2, 67108864)):
            invalid = copy.deepcopy(settings)
            invalid["native"]["analyse"]["refine"][index]["thsad"] = value
            with self.subTest(stage=index), self.assertRaisesRegex(ValueError, "scaled"):
                p.engine_options(invalid, Fraction(24), Fraction(120), 8)

    def test_larger_native_settings_and_graph_payload_round_trip(self):
        p = policy()
        settings = {"native": {"smooth": {"debug": {"label": "x" * 20000}}}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            path.write_text(json.dumps(settings))
            loaded = p.read_settings(path)
        token = p.encode_payload({"schema": 1, "plugins": "/plugins", "settings": loaded, "profile": "smooth", "fps": "120"})
        self.assertEqual(p.decode_payload(token)["settings"]["native"]["smooth"]["debug"]["label"], "x" * 20000)
        with self.assertRaises(ValueError):
            p.validate_settings({"native": {"smooth": {"linear": float("nan")}}})

    def test_dither_defaults_fast_and_preserves_explicit_quality_choice(self):
        p = policy()
        self.assertEqual(p.validate_settings({})["dither"], "ordered")
        for name in ("none", "ordered", "random", "error_diffusion"):
            self.assertEqual(p.validate_settings({"dither": name})["dither"], name)
        for invalid in ("invalid", None, 1, [], {}):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                p.validate_settings({"dither": invalid})


class LauncherTests(unittest.TestCase):
    def launcher(self):
        path = ROOT / "player" / "launch.py"
        spec = importlib.util.spec_from_file_location("external_launch", path)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(path.parent))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        if not callable(getattr(module, "runtime_environment", None)):
            self.fail("Installed VapourSynth child-environment support is missing")
        return module

    def test_explicit_external_vsscript_is_set_only_in_child_environment(self):
        launch = self.launcher()
        original = {"PATH": "/usr/bin", "KEEP": "value"}
        with tempfile.TemporaryDirectory(prefix="external VS ") as temp:
            library = Path(temp) / "libvsscript.dylib"
            library.write_bytes(b"library")
            result = launch.runtime_environment(library, original)
            self.assertEqual(result["VSSCRIPT_PATH"], str(library.resolve()))
            self.assertEqual(result["KEEP"], "value")
            self.assertNotIn("VSSCRIPT_PATH", original)
            self.assertNotIn("PYTHONHOME", result)

    def test_existing_external_vsscript_override_is_preserved(self):
        launch = self.launcher()
        result = launch.runtime_environment(None, {"VSSCRIPT_PATH": "/custom/libvsscript.dylib"})
        self.assertEqual(result["VSSCRIPT_PATH"], "/custom/libvsscript.dylib")

    def test_temporary_runtime_keeps_existing_xdg_mpv_config_and_scripts_visible(self):
        launch = self.launcher()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "user-config" / "mpv"
            scripts = config / "scripts"
            scripts.mkdir(parents=True)
            (config / "mpv.conf").write_text("volume=40\n")
            (scripts / "example.lua").write_text("return true\n")
            environment = {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(config.parent), "PATH": "/usr/bin"}
            child_root = root / "child-config"
            result = launch.runtime_environment(None, environment, child_root)
            child_mpv = Path(result["XDG_CONFIG_HOME"]) / "mpv"
            self.assertTrue(child_mpv.is_symlink())
            self.assertEqual((child_mpv / "mpv.conf").read_text(), "volume=40\n")
            self.assertEqual((child_mpv / "scripts" / "example.lua").read_text(), "return true\n")
            self.assertEqual((config / "mpv.conf").read_text(), "volume=40\n")
            self.assertEqual(environment["XDG_CONFIG_HOME"], str(config.parent))

    def test_temporary_runtime_uses_home_config_when_xdg_is_unset(self):
        launch = self.launcher()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            config = home / ".config" / "mpv"
            config.mkdir(parents=True)
            (config / "mpv.conf").write_text("keep-open=yes\n")
            child_root = root / "child-config"
            child_root.mkdir()
            result = launch.runtime_environment(None, {"HOME": str(home), "PATH": "/usr/bin"}, child_root)
            self.assertEqual((Path(result["XDG_CONFIG_HOME"]) / "mpv" / "mpv.conf").read_text(), "keep-open=yes\n")

    def test_temporary_runtime_does_not_create_missing_user_mpv_configuration(self):
        launch = self.launcher()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "absent-user-config"
            child_root = root / "child-config"
            child_root.mkdir()
            result = launch.runtime_environment(None, {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(original), "PATH": "/usr/bin"}, child_root)
            self.assertFalse(original.exists())
            self.assertFalse((Path(result["XDG_CONFIG_HOME"]) / "mpv").exists())

    def test_temporary_runtime_preserves_explicit_mpv_home(self):
        launch = self.launcher()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child_root = root / "child-config"
            child_root.mkdir()
            environment = {"HOME": str(root / "home"), "MPV_HOME": str(root / "legacy-mpv"), "PATH": "/usr/bin"}
            result = launch.runtime_environment(None, environment, child_root)
            self.assertEqual(result["MPV_HOME"], str(root / "legacy-mpv"))
            self.assertEqual(environment["MPV_HOME"], str(root / "legacy-mpv"))

    def test_runtime_discovery_does_not_execute_a_local_shadow_module(self):
        launch = self.launcher()
        environment = dict(os.environ)
        environment.pop("VSSCRIPT_PATH", None)
        with tempfile.TemporaryDirectory(prefix="VS discovery ") as temp:
            root = Path(temp)
            marker = root / "shadow-executed"
            (root / "vapoursynth.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unexpected import')\nraise ImportError('local shadow')\n")
            previous = Path.cwd()
            try:
                os.chdir(root)
                launch.runtime_environment(None, environment, root / "config")
            finally:
                os.chdir(previous)
            self.assertFalse(marker.exists(), "Installed-runtime discovery imported the working directory")

    @unittest.skipUnless(shutil.which("vspipe"), "External VapourSynth is not installed")
    def test_installed_vapoursynth_configuration_uses_owned_temporary_directory(self):
        launch = self.launcher()
        with tempfile.TemporaryDirectory(prefix="VS child config ") as temp:
            try:
                result = launch.runtime_environment(None, os.environ, temp)
            except TypeError:
                self.fail("Temporary VapourSynth runtime configuration is not implemented")
            library = result.get("VSSCRIPT_PATH")
            if not library or not (Path(library).parent / "__main__.py").is_file():
                self.skipTest("Installed VapourSynth does not use the Python-package configuration")
            self.assertEqual(result["XDG_CONFIG_HOME"], temp)
            config = Path(temp) / "vapoursynth" / "vapoursynth.toml"
            self.assertTrue(config.is_file())
            self.assertIn(library, config.read_text())

    @unittest.skipUnless(shutil.which("mpv"), "External mpv is not installed")
    def test_real_mpv_parser_accepts_padded_graph_payload_and_special_paths(self):
        p = policy()
        with tempfile.TemporaryDirectory(prefix="mpv parser ") as temp:
            root = Path(temp)
            movie = root / "movie.mkv"
            movie.write_bytes(b"movie")
            graph = root / "graph, with space.vpy"
            graph.write_text("video_in.set_output()\n")
            for suffix in ("lib", "libs", "libss"):
                plugins = root / suffix
                plugins.mkdir()
                for name in ("libsvpflow1_vs.dylib", "libsvpflow2_vs.dylib"):
                    (plugins / name).write_bytes(b"plugin")
                payload = p.encode_payload({"schema": 1, "plugins": str(plugins.resolve()), "settings": p.DEFAULT_SETTINGS, "profile": "smooth", "fps": "48"})
                if payload.endswith("="):
                    argv = p.build_command(shutil.which("mpv"), movie, graph, plugins, p.DEFAULT_SETTINGS, profile="smooth", fps="48")
                    result = subprocess.run(argv[:-2] + ["--no-config", "--load-scripts=no", "--vo=null", "--audio=no", "--idle=no"], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    break
            else:
                self.fail("No padded transport fixture was constructed")

    def test_launch_command_preserves_spaces_and_passes_graph_data_without_shell(self):
        p = policy()
        with tempfile.TemporaryDirectory(prefix="player space ") as temp:
            root = Path(temp)
            movie = root / "local movie.mkv"
            movie.write_bytes(b"movie")
            plugins = root / "lib"
            plugins.mkdir()
            for name in ("libsvpflow1_vs.dylib", "libsvpflow2_vs.dylib"):
                (plugins / name).write_bytes(b"plugin")
            argv = p.build_command("mpv", movie, ROOT / "player" / "graph.vpy", plugins, p.DEFAULT_SETTINGS, profile="smooth", fps="360")
            self.assertEqual(argv[0], "mpv")
            self.assertEqual(argv[-2:], ["--", str(movie.resolve())])
            self.assertIn("--hr-seek-framedrop=no", argv)
            self.assertFalse(any(arg.startswith("--framedrop=") for arg in argv))
            self.assertNotIn("--no-config", argv)
            self.assertNotIn("--load-scripts=no", argv)
            vf = next(arg for arg in argv if arg.startswith("--vf-add="))
            self.assertTrue(vf.startswith("--vf-add=@svp-input:lavfi="))
            self.assertNotIn(":buffered-frames=", vf)
            self.assertNotIn(":concurrent-frames=", vf)
            token = vf.split(":user-data=", 1)[1]
            length, token = token[1:].split("%", 1)
            self.assertEqual(int(length), len(token))
            payload = json.loads(base64.urlsafe_b64decode(token))
            self.assertEqual(payload["plugins"], str(plugins.resolve()))
            self.assertEqual(payload["fps"], "360")
            self.assertNotIn(".app", vf)

    def test_missing_plugins_fail_before_player_execution(self):
        p = policy()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            movie = root / "local movie.mkv"
            movie.write_bytes(b"movie")
            with self.assertRaises(ValueError):
                p.build_command("mpv", movie, ROOT / "player" / "graph.vpy", root / "missing", p.DEFAULT_SETTINGS)

    def test_quoted_tilde_plugin_directory_matches_the_validated_payload_path(self):
        p = policy()
        with tempfile.TemporaryDirectory(prefix="quoted plugin path ") as temp:
            root = Path(temp)
            movie = root / "movie.mkv"
            movie.write_bytes(b"movie")
            plugins = root / "native plugins"
            plugins.mkdir()
            for name in p.PLUGIN_NAMES:
                (plugins / name).write_bytes(b"plugin")
            quoted_plugins = "~/" + os.path.relpath(plugins, Path.home())
            argv = p.build_command("mpv", movie, ROOT / "player" / "graph.vpy", quoted_plugins, p.DEFAULT_SETTINGS)
            vf = next(arg for arg in argv if arg.startswith("--vf-add="))
            token = vf.split(":user-data=", 1)[1].split("%", 2)[2]
            payload = p.decode_payload(token)
            self.assertEqual(payload["plugins"], str(plugins.resolve()))

    def test_original_profile_does_not_require_plugins(self):
        p = policy()
        with tempfile.TemporaryDirectory() as temp:
            movie = Path(temp) / "movie.mp4"
            movie.write_bytes(b"movie")
            argv = p.build_command("mpv", movie, ROOT / "player" / "graph.vpy", Path(temp) / "missing", p.DEFAULT_SETTINGS, profile="original")
            self.assertFalse(any(arg.startswith("--vf-add=") for arg in argv))

    def test_time_bounds_are_forwarded_and_invalid_durations_are_rejected(self):
        p = policy()
        with tempfile.TemporaryDirectory() as temp:
            movie = Path(temp) / "movie.mp4"
            movie.write_bytes(b"movie")
            arguments = ("mpv", movie, ROOT / "player" / "graph.vpy", Path(temp) / "missing", p.DEFAULT_SETTINGS)
            argv = p.build_command(*arguments, profile="original", start=10.5, duration=7200)
            self.assertIn("--start=10.5", argv)
            self.assertIn("--length=7200", argv)
            for duration in (0, -1, float("inf"), True):
                with self.subTest(duration=duration), self.assertRaises(ValueError):
                    p.build_command(*arguments, profile="original", duration=duration)

    def test_remote_protocol_sources_and_stdin_are_forwarded_verbatim_to_fake_player(self):
        p = policy()
        for source in ("https://example.invalid/movie name.mkv?token=a%2Fb#part", "ytdl://example.invalid/watch?v=test", "av://lavfi:testsrc", "fd://3", "-"):
            with self.subTest(source=source):
                command = p.build_command("fake-mpv", source, "/unused.vpy", "/unused", {}, profile="original")
                self.assertEqual(command, ["fake-mpv", "--", source])

    def test_explicit_buffer_controls_reach_only_the_vapoursynth_suboptions(self):
        p = policy()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            movie = root / "movie.mkv"
            movie.write_bytes(b"movie")
            for name in p.PLUGIN_NAMES:
                (root / name).write_bytes(b"plugin")
            command = p.build_command("fake-mpv", movie, ROOT / "player" / "graph.vpy", root, {"buffered_frames": 12, "concurrent_frames": 6})
            vf = next(arg for arg in command if arg.startswith("--vf-add="))
            self.assertIn(":buffered-frames=12:concurrent-frames=6:", vf)

    def test_cli_help_needs_no_runtime_or_plugins(self):
        completed = subprocess.run([sys.executable, "-B", str(ROOT / "player" / "launch.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0)
        self.assertIn("--plugin-dir", completed.stdout)


if __name__ == "__main__":
    unittest.main()
