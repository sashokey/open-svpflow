import copy
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def api():
    filename = ROOT / "player" / "settings_api.py"
    if not filename.is_file():
        raise AssertionError("The shared settings API is missing")
    spec = importlib.util.spec_from_file_location("app_settings", filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SettingsApiTests(unittest.TestCase):
    def options(self, **change):
        module = api()
        document = module.default_document()
        document["interpolation"].update(change)
        return {key: json.loads(value) for key, value in module.policy.engine_options(module.playback_settings(document), Fraction(24), Fraction(120), 8).items()}

    def test_defaults_are_independent_and_preferences_survive_save(self):
        module = api()
        first = module.default_document()
        first["preferences"]["after_start"] = "menu_bar"
        self.assertEqual(module.default_document()["preferences"], {"new_files": "append", "after_start": "quit_app"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "settings.json"
            saved = module.save_document(path, first)
            self.assertEqual(module.load_document(path), saved)
            self.assertEqual(saved["preferences"]["after_start"], "menu_bar")
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_saved_playback_profiles_are_discarded_without_resetting_other_values(self):
        module = api()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for profile in ("auto", "smooth", "original"):
                with self.subTest(profile=profile):
                    document = module.default_document()
                    document["playback"] = {"profile": profile, "rate": "fixed", "fps": "144"}
                    document["interpolation"]["fi_precision"] = 4
                    document["preferences"]["after_start"] = "keep_open"
                    path.write_text(json.dumps(document))
                    loaded = module.load_document(path)
                    self.assertEqual(loaded["playback"], {"rate": "fixed", "fps": "144"})
                    self.assertEqual(loaded["interpolation"]["fi_precision"], 4)
                    self.assertEqual(loaded["preferences"]["after_start"], "keep_open")
                    self.assertEqual(module.save_document(path, loaded), json.loads(path.read_text()))
            self.assertNotIn("playback.profile", [field["path"] for field in module.describe_settings()["fields"]])

    def test_invalid_save_preserves_existing_settings(self):
        module = api()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            module.save_document(path, module.default_document())
            before = path.read_bytes()
            invalid = module.default_document()
            invalid["interpolation"]["fi_precision"] = 3
            with self.assertRaises(ValueError):
                module.save_document(path, invalid)
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_native_combination_is_rejected_before_settings_are_written(self):
        module = api()
        document = module.default_document()
        document["interpolation"]["native"]["super"]["full"] = False
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with self.assertRaisesRegex(ValueError, "reduced super"):
                module.save_document(path, document)
            self.assertFalse(path.exists())

    def test_legacy_custom_settings_roundtrip_without_changing_native_options(self):
        module = api()
        legacy = {"shader": 22, "decrease_grid_step": 2, "search_radius": {"fine": -16}, "native": {"analyse": {"main": {"penalty": {"lambda": 2.5}}}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps(legacy))
            migrated = module.load_document(path)
            module.save_document(path, migrated)
            migrated = module.load_document(path)
        actual = module.policy.engine_options(module.playback_settings(migrated), Fraction(24), Fraction(120), 8)
        expected = module.policy.engine_options(legacy, Fraction(24), Fraction(120), 8)
        self.assertEqual(actual, expected)
        self.assertEqual(module.playback_settings(migrated)["dither"], "ordered")

    def test_editing_legacy_behavior_keeps_its_interpolation_untouched(self):
        module = api()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"motion_grid":{"block":4},"native":{"smooth":{"mask":{"cover":17}}}}')
            document = module.load_document(path)
        document["preferences"]["new_files"] = "ask"
        settings = module.playback_settings(document)
        self.assertEqual(settings["motion_grid"]["block"], 4)
        self.assertEqual(settings["native"]["smooth"]["mask"]["cover"], 17)

    def test_legacy_custom_refinement_is_explicit_and_can_be_disabled(self):
        module = api()
        legacy = {"decrease_grid_step": 2, "native": {"smooth": {"mask": {"cover": 17}}}}
        document = module.normalize_document(legacy)
        self.assertEqual(document["interpolation"]["mv_refine"], "legacy")
        self.assertEqual(module.policy.engine_options(module.playback_settings(document), 24, 120, 8), module.policy.engine_options(legacy, 24, 120, 8))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for threshold, expected in (("200", [{"thsad": 200}]), ("0", []), (200, [{"thsad": 200}]), (0, [])):
                document["interpolation"]["mv_refine"] = threshold
                document = module.save_document(path, document)
                document = module.load_document(path)
                options = {key: json.loads(value) for key, value in module.policy.engine_options(module.playback_settings(document), 24, 120, 8).items()}
                self.assertEqual(options["analyse"]["refine"], expected)
                self.assertEqual(options["smooth"]["mask"]["cover"], 17)

    def test_legacy_custom_radius_can_be_replaced_by_an_ordinary_preset(self):
        module = api()
        legacy = {"search_radius": {"fine": -16, "coarse": -48}, "native": {"analyse": {"main": {"penalty": {"lambda": 2.5}, "search": {"coarse": {"bad": {"range": 12}}}}}, "smooth": {"mask": {"cover": 17}}}}
        document = module.normalize_document(legacy)
        self.assertEqual(document["interpolation"]["mv_radius"], "legacy")
        self.assertEqual(module.policy.engine_options(module.playback_settings(document), 24, 120, 8), module.policy.engine_options(legacy, 24, 120, 8))
        document["interpolation"]["mv_radius"] = 2
        options = {key: json.loads(value) for key, value in module.policy.engine_options(module.playback_settings(document), 24, 120, 8).items()}
        search = options["analyse"]["main"]["search"]
        self.assertEqual((search["type"], search["distance"]), (2, -4))
        self.assertEqual((search["coarse"]["type"], search["coarse"]["distance"], search["coarse"]["satd"]), (4, -8, True))
        self.assertEqual(search["coarse"]["bad"]["range"], 12)
        self.assertEqual(options["analyse"]["main"]["penalty"]["lambda"], 2.5)
        self.assertEqual(options["smooth"]["mask"]["cover"], 17)

    def test_equivalent_legacy_refinement_and_radius_display_their_real_values(self):
        module = api()
        legacy = {"search_radius": {"fine": -4, "coarse": -6}, "native": {"analyse": {"refine": [{"thsad": 75}], "main": {"search": {"type": 2, "coarse": {"type": 2, "satd": False}}}}}}
        document = module.normalize_document(legacy)
        self.assertEqual(document["interpolation"]["mv_refine"], 75)
        self.assertEqual(document["interpolation"]["mv_radius"], 0)
        document["interpolation"]["mv_refine"] = 0
        options = module.policy.engine_options(module.playback_settings(document), 24, 120, 8)
        self.assertEqual(json.loads(options["analyse"])["refine"], [])

    def test_legacy_control_values_require_a_preservation_payload(self):
        module = api()
        for key in ("mv_refine", "mv_radius"):
            document = module.default_document()
            document["interpolation"][key] = "legacy"
            with self.assertRaisesRegex(ValueError, "Legacy"):
                module.playback_settings(document)

    def test_descriptors_explain_explicit_legacy_controls(self):
        fields = {item["path"]: item for item in api().describe_settings()["fields"]}
        self.assertEqual(fields["interpolation.mv_refine"]["kind"], "text")
        self.assertIn("legacy", fields["interpolation.mv_refine"]["help"].lower())
        self.assertIn("legacy", [item["value"] for item in fields["interpolation.mv_radius"]["options"]])

    def test_all_five_interpolation_modes_map_to_correct_native_modes(self):
        for mode, native_mode, limits in ((1, 2, (1600, 2800)), (2, 3, (0, 0)), (3, 1, (1600, 2800)), (4, 3, (1600, 2800)), (5, 0, (1600, 2800))):
            with self.subTest(mode=mode):
                scene = self.options(fi_mode=mode)["smooth"]["scene"]
                self.assertEqual(scene["mode"], native_mode)
                self.assertEqual((scene["limits"]["m1"], scene["limits"]["m2"]), limits)

    def test_minimum_artifacts_mode_degrades_for_ratios_below_two(self):
        module = api()
        document = module.default_document()
        document["interpolation"]["fi_mode"] = 1
        options = module.policy.engine_options(module.playback_settings(document), Fraction(60), Fraction(100), 8)
        self.assertEqual(json.loads(options["smooth"])["scene"]["mode"], 1)
        almost_two = module.policy.engine_options(module.playback_settings(document), Fraction(10000), Fraction(19995), 8)
        self.assertEqual(json.loads(almost_two["smooth"])["scene"]["mode"], 1)

    def test_coarse_width_automatic_sentinel_keeps_the_official_default(self):
        self.assertEqual(self.options(mv_coarsewidth=0)["analyse"]["main"]["search"]["coarse"]["width"], 1050)
        self.assertEqual(self.options(mv_coarsewidth=530)["analyse"]["main"]["search"]["coarse"]["width"], 530)

    def test_shader_adjusts_vector_direction_and_cpu_block_mode(self):
        self.assertEqual(self.options(fi_shader=1)["analyse"]["vectors"], 2)
        options = self.options(fi_shader=10, backend="cpu")
        self.assertEqual(options["smooth"]["algo"], 11)
        self.assertTrue(options["smooth"]["block"])
        self.assertFalse(self.options(fi_shader=10)["smooth"]["block"])

    def test_nine_grid_steps_use_official_block_overlap_pairs(self):
        for step, block, overlap in ((32, 32, 0), (28, 32, 1), (24, 32, 2), (16, 16, 0), (14, 16, 1), (12, 16, 2), (8, 8, 0), (7, 8, 1), (6, 8, 2)):
            with self.subTest(step=step):
                self.assertEqual(self.options(mv_grid=step)["analyse"]["block"], {"w": block, "h": block, "overlap": overlap})

    def test_refinement_is_one_threshold_stage_not_a_stage_count(self):
        self.assertEqual(self.options(mv_grid=7, mv_refine=150)["analyse"]["refine"], [{"thsad": 150}])
        self.assertEqual(self.options(mv_grid=7, mv_refine=150)["analyse"]["block"]["overlap"], 0)
        self.assertEqual(self.options(mv_grid=7, mv_refine=150, backend="cpu")["analyse"]["block"]["overlap"], 0)

    def test_fast_precision_uses_reduced_super_and_no_finest_search(self):
        options = self.options(fi_precision=0)
        self.assertEqual(options["super"]["pel"], 1)
        self.assertFalse(options["super"]["full"])
        self.assertEqual(options["analyse"]["main"]["search"]["distance"], 0)
        regular = self.options(fi_precision=2)
        self.assertEqual(regular["analyse"]["main"]["search"]["type"], 2)
        self.assertEqual(regular["analyse"]["main"]["search"]["distance"], -4)

    def test_radius_variants_control_type_range_and_satd(self):
        for radius, kind, distance, satd in ((0, 2, -6, False), (1, 2, -6, True), (2, 4, -8, True), (3, 4, -12, True)):
            with self.subTest(radius=radius):
                coarse = self.options(mv_radius=radius)["analyse"]["main"]["search"]["coarse"]
                self.assertEqual((coarse["type"], coarse["distance"], coarse["satd"]), (kind, distance, satd))

    def test_wide_search_has_all_four_native_variants(self):
        for wide, sad, distance in ((0, 1000, 0), (1, 1000, -24), (2, 2000, 24), (3, 2000, -24)):
            with self.subTest(wide=wide):
                self.assertEqual(self.options(mv_wide=wide)["analyse"]["main"]["search"]["coarse"]["bad"], {"sad": sad, "range": distance})

    def test_scene_changes_and_masking_reach_the_correct_native_fields(self):
        options = self.options(fi_scene_changes=0, fi_masking=20, fi_shader=23)
        self.assertTrue(options["smooth"]["scene"]["blend"])
        self.assertEqual(options["smooth"]["mask"]["area"], 20)
        self.assertEqual(options["smooth"]["mask"]["cover"], 80)
        self.assertEqual(self.options(fi_shader=11)["smooth"]["mask"]["cover"], 100)

    def test_rate_modes_resolve_against_source_or_monitor(self):
        module = api()
        document = module.default_document()
        for mode, display, expected in (("monitor_half", 240, Fraction(120)), ("monitor_third", 240, Fraction(80)), ("source_2_5", 0, Fraction(60)), ("source_5", 0, Fraction(120))):
            with self.subTest(mode=mode):
                document["playback"]["rate"] = mode
                self.assertEqual(module.policy.select_rate(Fraction(24), display, module.rate_argument(document)), expected)
        document["playback"].update(rate="fixed", fps="120000/1001")
        self.assertEqual(module.rate_argument(document), "120000/1001")
        with self.assertRaises(ValueError):
            module.policy.select_rate(Fraction(2000000000), 0, "source*2")

    def test_metadata_contains_controls_and_honest_limitations(self):
        description = api().describe_settings()
        paths = {field["path"]: field for field in description["fields"]}
        for key in ("fi_mode", "fi_shader", "fi_masking", "fi_precision", "mv_grid", "mv_refine", "mv_radius", "mv_wide", "mv_coarsewidth", "fi_scene_changes", "fi_duplicates"):
            self.assertIn("interpolation." + key, paths)
        self.assertIn("preferences.after_start", paths)
        self.assertIn("playback.rate", paths)
        self.assertTrue(description["limitations"])
        self.assertIn("factory", " ".join(description["limitations"]).lower())

    def test_automatic_depth_uses_a_documented_pixel_budget_without_changing_analysis(self):
        module = api()
        settings = module.playback_settings(module.default_document())
        for width, height, rate, expected in ((3840, 2160, 60, 10), (3840, 2160, 120, 8), (1920, 1080, 240, 10)):
            self.assertEqual(module.synthesis_bits(settings, width, height, 10, Fraction(rate)), expected)
        settings["limit_10bit"] = 1000
        self.assertEqual(module.synthesis_bits(settings, 3840, 2160, 10, Fraction(240)), 10)

    def test_editing_a_legacy_shader_preserves_custom_search_and_conversion(self):
        module = api()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"search_radius":{"fine":-16},"native":{"smooth":{"mask":{"cover":17}}}}')
            document = module.load_document(path)
        document["interpolation"]["fi_shader"] = 22
        settings = module.playback_settings(document)
        options = {key: json.loads(value) for key, value in module.policy.engine_options(settings, Fraction(24), Fraction(120), 8).items()}
        self.assertEqual(options["smooth"]["algo"], 22)
        self.assertEqual(options["analyse"]["main"]["search"]["distance"], -16)
        self.assertEqual(options["smooth"]["mask"]["cover"], 17)

    def test_text_buffer_controls_are_normalized_and_numeric_choices_are_strict(self):
        module = api()
        document = module.default_document()
        document["interpolation"]["buffered_frames"] = "8"
        self.assertEqual(module.playback_settings(document)["buffered_frames"], 8)
        document["interpolation"]["fi_mode"] = 3.0
        with self.assertRaises(ValueError):
            module.playback_settings(document)

    def test_unknown_fields_and_invalid_preferences_are_rejected(self):
        module = api()
        for section, key, value in (("preferences", "after_start", "daemon"), ("preferences", "new_files", "delete"), ("playback", "rate", "hardware_auto"), ("interpolation", "mv_wide", 4), ("interpolation", "typo", 1)):
            with self.subTest(section=section, key=key):
                document = module.default_document()
                document[section][key] = value
                with self.assertRaises(ValueError):
                    module.playback_settings(document)


if __name__ == "__main__":
    unittest.main()
