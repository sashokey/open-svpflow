import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent


def load_smoke():
    path = ROOT / "smoke_application.py"
    if not path.is_file():
        raise AssertionError("The application integration smoke is missing")
    spec = importlib.util.spec_from_file_location("smoke_application", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ApplicationSmokeTests(unittest.TestCase):
    def test_gui_smoke_refuses_user_machine_before_starting_processes(self):
        with self.assertRaisesRegex(RuntimeError, "isolated GitHub Actions"):
            load_smoke().require_isolated_gui({})

    def test_headless_fixture_uses_external_mpv_configuration(self):
        module = load_smoke()
        with tempfile.TemporaryDirectory() as directory:
            files, configuration = module.make_fixtures(Path(directory))
            self.assertEqual(len(files), 3)
            self.assertTrue(all(path.is_file() for path in files))
            self.assertIn("ü", files[1].name)
            text = (configuration / "mpv.conf").read_text()
            self.assertIn("vo=null\n", text)
            self.assertIn("ao=null\n", text)
            self.assertIn("force-window=no\n", text)
            self.assertIn("loop-file=inf\n", text)
            self.assertEqual(files[0].read_bytes().splitlines()[0], b"YUV4MPEG2 W320 H192 F24:1 Ip A1:1 C420jpeg")

    def test_bundle_resolution_rejects_loose_distribution(self):
        module = load_smoke()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "application bundle"):
                module.bundle_resources(Path(directory))

    def test_after_start_probe_requires_matching_policy_and_window_presence(self):
        module = load_smoke()
        self.assertTrue(module.matches_gui_mode({"running": True, "policy": "regular", "visibleWindows": 1}, "keep_open"))
        self.assertFalse(module.matches_gui_mode({"running": True, "policy": "accessory", "visibleWindows": 1}, "keep_open"))
        self.assertTrue(module.matches_gui_mode({"running": True, "policy": "accessory", "visibleWindows": 0}, "menu_bar"))
        self.assertFalse(module.matches_gui_mode({"running": True, "policy": "accessory", "visibleWindows": 1}, "menu_bar"))
        self.assertFalse(module.matches_gui_mode({"running": False, "policy": "regular", "visibleWindows": 1}, "keep_open"))
        self.assertFalse(module.matches_gui_mode({"running": True, "policy": "regular", "visibleWindows": 1}, "unknown"))

    def test_early_rendezvous_waits_for_started_playback_and_available_ipc(self):
        module = load_smoke()
        self.assertTrue(hasattr(module, "wait_for_started_session"), "GUI startup needs an actual playback readiness wait")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "session.json").write_text('{"pid":123,"socket":"pending"}')
            statuses = iter(("idle", "started", "started"))
            current = {"status": None, "ipc_attempts": 0}
            record, client = {"pid": 123}, object()
            def status(*args, **kwargs):
                current["status"] = next(statuses)
                return {"ok": True, "status": current["status"], "after_start": "keep_open"}
            def connect(path):
                self.assertEqual(current["status"], "started")
                current["ipc_attempts"] += 1
                if current["ipc_attempts"] == 1:
                    raise FileNotFoundError("mpv.sock is not available yet")
                return record, client
            with patch.object(module, "backend", side_effect=status), patch.object(module, "client_for", side_effect=connect):
                actual_record, actual_client = module.wait_for_started_session(Path("resources"), state, {}, timeout=1)
            self.assertEqual(actual_record, {"pid": 123})
            self.assertIs(actual_client, client)

    def test_readiness_wait_preserves_explicit_backend_failure(self):
        module = load_smoke()
        self.assertTrue(hasattr(module, "wait_for_started_session"), "GUI startup needs an actual playback readiness wait")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "session.json").write_text('{}')
            with patch.object(module, "backend", side_effect=AssertionError("fatal graph failure")):
                with self.assertRaisesRegex(AssertionError, "fatal graph failure"):
                    module.wait_for_started_session(Path("resources"), state, {}, timeout=1)

    def test_early_rendezvous_without_playback_reaches_bounded_failure(self):
        module = load_smoke()
        self.assertTrue(hasattr(module, "wait_for_started_session"), "GUI startup needs an actual playback readiness wait")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "session.json").write_text('{}')
            with patch.object(module, "backend", return_value={"ok": True, "status": "idle", "after_start": "keep_open"}), patch.object(module, "client_for", side_effect=AssertionError("IPC was requested before playback")):
                with self.assertRaisesRegex(AssertionError, "start playback"):
                    module.wait_for_started_session(Path("resources"), state, {}, timeout=0.06)


if __name__ == "__main__":
    unittest.main()
