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
    def test_fractional_movie_prefers_exact_fivefold_rate(self):
        p = policy()
        self.assertEqual(p.select_rate(Fraction(24000, 1001), 240, "auto"), Fraction(120000, 1001))

    def test_integer_movie_uses_120_instead_of_monitor_240(self):
        self.assertEqual(policy().select_rate(Fraction(24), 240, "auto"), Fraction(120))

    def test_explicit_rate_supports_bbb_30_to_60_without_display(self):
        self.assertEqual(policy().select_rate(Fraction(30), 0, "smooth", "60"), Fraction(60))

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
        for value in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                p.source_rate(value)
        for value in ("0", "-60", "1/0", "nan", "241"):
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
        self.assertEqual({case[0] for case in cases}, set(p.DEFAULT_SETTINGS) - {"schema", "rate_mode"})
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
        for change in ({"unexpected": 1}, {"wide_search": "false"}, {"shader": 99}):
            value = copy.deepcopy(p.DEFAULT_SETTINGS)
            value.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                p.validate_settings(value)

    def test_unsupported_10_bit_dedup_and_refinement_are_rejected(self):
        p = policy()
        settings = copy.deepcopy(p.DEFAULT_SETTINGS)
        settings["duplicate_frames_removal"] = True
        with self.assertRaises(ValueError):
            p.engine_options(settings, Fraction(24), Fraction(120), 10)
        settings = copy.deepcopy(p.DEFAULT_SETTINGS)
        settings["decrease_grid_step"] = 1
        settings["motion_grid"]["block"] = 8
        with self.assertRaises(ValueError):
            p.engine_options(settings, Fraction(24), Fraction(120), 8)

    def test_noninteger_output_ratio_uses_absolute_rate(self):
        options = policy().engine_options(policy().DEFAULT_SETTINGS, Fraction(25), Fraction(120), 8)
        self.assertEqual(json.loads(options["smooth"])["rate"], {"num": 120, "den": 1, "abs": True})

    def test_cpu_smoke_options_do_not_initialize_opencl(self):
        options = policy().engine_options(policy().DEFAULT_SETTINGS, Fraction(24), Fraction(48), 8, gpu=False)
        self.assertEqual(json.loads(options["super"])["gpu"], 0)
        self.assertTrue(json.loads(options["analyse"])["exact"])
        self.assertEqual(json.loads(options["smooth"])["gpuid"], -1)


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
                    result = subprocess.run(argv[:-2] + ["--vo=null", "--ao=null", "--idle=no"], capture_output=True, text=True, timeout=10)
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
            argv = p.build_command("mpv", movie, ROOT / "player" / "graph.vpy", plugins, p.DEFAULT_SETTINGS, profile="smooth", fps="60")
            self.assertEqual(argv[0], "mpv")
            self.assertEqual(argv[-2:], ["--", str(movie.resolve())])
            self.assertIn("--hr-seek-framedrop=no", argv)
            self.assertIn("--framedrop=no", argv)
            vf = next(arg for arg in argv if arg.startswith("--vf="))
            token = vf.split(":user-data=", 1)[1]
            length, token = token[1:].split("%", 1)
            self.assertEqual(int(length), len(token))
            payload = json.loads(base64.urlsafe_b64decode(token))
            self.assertEqual(payload["plugins"], str(plugins.resolve()))
            self.assertEqual(payload["fps"], "60")
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
            vf = next(arg for arg in argv if arg.startswith("--vf="))
            token = vf.split(":user-data=", 1)[1].split("%", 2)[2]
            payload = p.decode_payload(token)
            self.assertEqual(payload["plugins"], str(plugins.resolve()))

    def test_original_profile_does_not_require_plugins(self):
        p = policy()
        with tempfile.TemporaryDirectory() as temp:
            movie = Path(temp) / "movie.mp4"
            movie.write_bytes(b"movie")
            argv = p.build_command("mpv", movie, ROOT / "player" / "graph.vpy", Path(temp) / "missing", p.DEFAULT_SETTINGS, profile="original")
            self.assertFalse(any(arg.startswith("--vf=") for arg in argv))

    def test_time_bounds_are_forwarded_and_invalid_durations_are_rejected(self):
        p = policy()
        with tempfile.TemporaryDirectory() as temp:
            movie = Path(temp) / "movie.mp4"
            movie.write_bytes(b"movie")
            arguments = ("mpv", movie, ROOT / "player" / "graph.vpy", Path(temp) / "missing", p.DEFAULT_SETTINGS)
            argv = p.build_command(*arguments, profile="original", start=10.5, duration=300)
            self.assertIn("--start=10.5", argv)
            self.assertIn("--length=300", argv)
            for duration in (0, -1, 301, float("inf"), True):
                with self.subTest(duration=duration), self.assertRaises(ValueError):
                    p.build_command(*arguments, profile="original", duration=duration)

    def test_cli_rejects_urls_before_spawning_mpv(self):
        completed = subprocess.run([sys.executable, "-B", str(ROOT / "player" / "launch.py"), "--", "https://example.invalid/movie.mkv"], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("local", completed.stderr.lower())

    def test_cli_help_needs_no_runtime_or_plugins(self):
        completed = subprocess.run([sys.executable, "-B", str(ROOT / "player" / "launch.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0)
        self.assertIn("--plugin-dir", completed.stdout)


if __name__ == "__main__":
    unittest.main()
