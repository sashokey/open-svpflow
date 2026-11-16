import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "player"))
spec = importlib.util.spec_from_file_location("player_launch", ROOT / "player" / "launch.py")
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class LaunchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.plugins = Path(directory.name)
        for name in ("libsvpflow1_vs.dylib", "libsvpflow2_vs.dylib"):
            (self.plugins / name).write_bytes(b"fixture")
        environment = patch.object(launch, "runtime_environment", return_value={})
        environment.start()
        self.addCleanup(environment.stop)
        self.plugin_options = ["--plugin-dir", str(self.plugins)]

    def test_cli_preserves_url_and_overrides_json_depth_and_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            plugins = Path(directory)
            for name in ("libsvpflow1_vs.dylib", "libsvpflow2_vs.dylib"):
                (plugins / name).write_bytes(b"fixture")
            url = "https://example.invalid/video%20file.mkv?value=a+b"
            with patch.object(launch.shutil, "which", return_value="/external/mpv"), patch.object(launch, "runtime_environment", return_value={}), patch.object(launch.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(launch.main(["--plugin-dir", directory, "--bits", "10", "--backend", "cpu", "--mpv-option=--vf=lavfi=[crop=319:191:0:0:exact=1]", "--mpv-option=--vf-add=hflip", "--", url]), 0)
            command = run.call_args.args[0]
            self.assertEqual(command[-2:], ["--", url])
            self.assertTrue(any(value.endswith("/player/compat.lua") for value in command if value.startswith("--script=")))
            vf = next(value for value in command if value.startswith("--vf-add="))
            self.assertLess(command.index("--vf=lavfi=[crop=319:191:0:0:exact=1]"), command.index(vf))
            self.assertLess(command.index(vf), command.index("--vf-add=hflip"))
            token = re.search(r":user-data=%[0-9]+%(.+)$", vf).group(1)
            payload = launch.build_command.__globals__["decode_payload"](token)
            self.assertEqual(payload["settings"]["output_bits"], 10)
            self.assertEqual(payload["settings"]["backend"], "cpu")

    def test_versioned_application_settings_can_be_used_by_the_source_cli(self):
        import settings_api
        with tempfile.TemporaryDirectory() as directory:
            document = settings_api.default_document()
            document["playback"]["profile"] = "original"
            configuration = Path(directory) / "settings.json"
            configuration.write_text(json.dumps(document))
            with patch.object(launch.shutil, "which", return_value="/external/mpv"), patch.object(launch.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(launch.main(self.plugin_options + ["--settings", str(configuration), "--", "memory://fixture"]), 0)
            self.assertTrue(any(arg.startswith("--vf-add=") for arg in run.call_args.args[0]))

    def test_multiple_sources_form_one_ordered_external_playlist(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first movie.mkv"
            second = Path(directory) / "second ü.mkv"
            first.write_bytes(b"fixture")
            second.write_bytes(b"fixture")
            with patch.object(launch.shutil, "which", return_value="/external/mpv"), patch.object(launch.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(launch.main(self.plugin_options + ["--", str(first), str(second)]), 0)
            self.assertEqual(run.call_args.args[0][-3:], ["--", str(first.resolve()), str(second.resolve())])

    def test_invalid_later_source_prevents_any_player_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.mkv"
            first.write_bytes(b"fixture")
            error = io.StringIO()
            with patch.object(launch.subprocess, "run") as run, patch.object(sys, "stderr", error), self.assertRaises(SystemExit) as stopped:
                launch.main(self.plugin_options + ["--", str(first), str(Path(directory) / "missing.mkv")])
            self.assertEqual(stopped.exception.code, 2)
            self.assertIn("readable local movie", error.getvalue())
            run.assert_not_called()

    def test_removed_profile_cannot_select_original_playback(self):
        error = io.StringIO()
        with patch.object(launch, "subprocess") as player, patch.object(sys, "stderr", error), self.assertRaises(SystemExit) as stopped:
            launch.main(self.plugin_options + ["--profile", "original", "--", "memory://fixture"])
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("unrecognized arguments: --profile", error.getvalue())
        player.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
