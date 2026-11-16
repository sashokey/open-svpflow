import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "player"))
try:
    import application
except ImportError:
    application = None

class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(application, "The application JSON entry point is missing")
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.addCleanup(self.directory.cleanup)

    def call(self, command, value):
        output = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(value))), patch.object(sys, "stdout", output):
            result = application.main([command, "--state-dir", str(self.root / "state"), "--bundle-root", str(ROOT)])
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        return result, json.loads(lines[0])

    def test_invalid_request_returns_one_json_error_without_starting_player(self):
        code, result = self.call("open", {"files": [str(self.root / "missing.mkv")]})
        self.assertNotEqual(code, 0)
        self.assertFalse(result["ok"])
        self.assertIn("readable", result["message"])
        self.assertFalse((self.root / "state" / "session.json").exists())

    def test_idle_status_does_not_start_a_session(self):
        code, result = self.call("status", {})
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "idle")
        self.assertFalse((self.root / "state" / "session.json").exists())

    def test_unrecognized_request_fields_are_rejected(self):
        code, result = self.call("open", {"files": [], "shell": "touch bad"})
        self.assertNotEqual(code, 0)
        self.assertFalse(result["ok"])
        self.assertFalse((self.root / "bad").exists())

    def test_settings_contract_round_trips_without_starting_playback(self):
        code, description = self.call("describe", {})
        self.assertEqual(code, 0)
        self.assertEqual(description["description"]["schema"], 2)
        code, initial = self.call("settings-get", {})
        self.assertEqual(code, 0)
        document = initial["document"]
        document["preferences"]["after_start"] = "menu_bar"
        document["playback"]["rate"] = "monitor_half"
        code, saved = self.call("settings-save", {"document": document})
        self.assertEqual(code, 0)
        code, loaded = self.call("settings-get", {})
        self.assertEqual(code, 0)
        self.assertEqual(loaded["document"], saved["document"])
        self.assertEqual(loaded["document"]["preferences"]["after_start"], "menu_bar")
        self.assertFalse((self.root / "state" / "session.json").exists())

    def test_invalid_open_action_cannot_start_the_worker(self):
        movie = self.root / "valid.mkv"
        movie.write_bytes(b"fixture")
        with patch.object(application.session.subprocess, "Popen") as process:
            code, result = self.call("open", {"files": [str(movie)], "action": "execute"})
        self.assertNotEqual(code, 0)
        self.assertIn("action", result["message"])
        process.assert_not_called()

if __name__ == "__main__":
    unittest.main()
