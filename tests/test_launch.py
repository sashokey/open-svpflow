import importlib.util
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

    def test_original_protocol_playback_bypasses_graph_and_temporary_environment(self):
        with patch.object(launch.shutil, "which", return_value="/external/mpv"), patch.object(launch, "runtime_environment") as environment, patch.object(launch.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(launch.main(["--profile", "original", "--", "memory://fixture"]), 0)
        environment.assert_not_called()
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["--", "memory://fixture"])
        self.assertTrue(any(value.endswith("/player/compat.lua") for value in command if value.startswith("--script=")))
        self.assertFalse(any(value.startswith("--vf") for value in command))


if __name__ == "__main__":
    unittest.main()
