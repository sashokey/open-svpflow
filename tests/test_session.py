import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "player"))
try:
    import session
except ImportError:
    session = None

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(session, "The independent playback session is missing")
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.addCleanup(self.directory.cleanup)
        self.files = []
        for name in ("first movie.mkv", "second ü.mkv", "third.mkv"):
            path = self.root / name
            path.write_bytes(b"fixture")
            self.files.append(str(path))

    def test_queue_append_replace_and_cancel_preserve_current_playback(self):
        commands = []
        active = {"value": False}
        def send(command):
            commands.append(command)
            if command[0] == "loadfile":
                active["value"] = True
        queue = session.PlaylistController(send, lambda: active["value"])
        self.assertEqual(queue.open(self.files[:2], "append", "append"), "started")
        self.assertEqual(commands, [["loadfile", self.files[0], "replace"], ["loadfile", self.files[1], "append"]])
        self.assertEqual(queue.open(self.files[2:], "auto", "ask"), "ask")
        self.assertEqual(queue.pending, self.files[2:])
        before = list(commands)
        self.assertEqual(queue.open([], "cancel", "ask"), "queued")
        self.assertEqual(commands, before)
        self.assertEqual(queue.pending, [])
        self.assertEqual(queue.open(self.files[2:], "replace", "append"), "started")
        self.assertEqual(commands[-1], ["loadfile", self.files[2], "replace"])

    def test_new_file_validation_rejects_a_batch_without_modifying_playlist(self):
        commands = []
        queue = session.PlaylistController(commands.append, lambda: True)
        with self.assertRaisesRegex(ValueError, "readable"):
            queue.open([self.files[0], str(self.root / "missing.mkv")], "append", "append")
        self.assertEqual(commands, [])

    def test_literal_media_names_reach_mpv_without_shell_expansion(self):
        path = self.root / "a `touch nope` $(whoami).mkv"
        path.write_bytes(b"fixture")
        commands = []
        queue = session.PlaylistController(commands.append, lambda: False)
        queue.open([str(path)], "auto", "append")
        self.assertEqual(commands, [["loadfile", str(path), "replace"]])
        self.assertFalse((self.root / "nope").exists())

    def test_ipc_matches_response_ids_and_retains_interleaved_events(self):
        address = str(self.root / "mpv.sock")
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        server.bind(address)
        server.listen(1)
        observed = []
        def serve():
            peer, _ = server.accept()
            with peer, peer.makefile("rwb") as stream:
                request = json.loads(stream.readline())
                observed.append(request["command"])
                stream.write(b'{"event":"file-loaded"}\n')
                stream.write((json.dumps({"request_id": request["request_id"], "error": "success", "data": {"w": 320}}) + "\n").encode())
                stream.flush()
        thread = threading.Thread(target=serve)
        thread.start()
        client = session.MpvClient(address)
        self.addCleanup(client.close)
        self.assertEqual(client.command(["get_property", "video-out-params"]), {"w": 320})
        self.assertEqual(client.events.get(timeout=1), {"event": "file-loaded"})
        self.assertEqual(observed, [["get_property", "video-out-params"]])
        thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_private_state_rejects_symlink_and_creates_owner_only_files(self):
        state = self.root / "state"
        session.private_directory(state)
        self.assertEqual(state.stat().st_mode & 0o777, 0o700)
        session.atomic_json(state / "event.json", {"status": "error", "message": "failed"})
        self.assertEqual((state / "event.json").stat().st_mode & 0o777, 0o600)
        link = self.root / "linked"
        link.symlink_to(state, target_is_directory=True)
        with self.assertRaises(ValueError):
            session.private_directory(link)

    def test_stale_rendezvous_is_not_a_live_session_and_does_not_signal_pid(self):
        state = self.root / "state"
        session.private_directory(state)
        session.atomic_json(state / "session.json", {"socket": str(self.root / "missing.sock"), "token": "not-live", "pid": 1})
        self.assertIsNone(session.connect_existing(state))
        self.assertEqual(session.status(state)["status"], "idle")
        self.assertFalse((state / "session.json").exists())

    def test_timed_out_live_probe_does_not_remove_rendezvous_or_spawn_a_worker(self):
        state = self.root / "state"
        session.private_directory(state)
        record = {"socket": str(self.root / "control.sock"), "token": "busy", "pid": 1}
        session.atomic_json(state / "session.json", record)
        with patch.object(session, "rpc", side_effect=TimeoutError("busy")), patch.object(session.subprocess, "Popen") as process:
            with self.assertRaises(TimeoutError):
                session.status(state)
            with self.assertRaises(TimeoutError):
                session.open_files(state, self.root, self.files[:1])
        self.assertEqual(session.read_json(state / "session.json"), record)
        process.assert_not_called()

if __name__ == "__main__":
    unittest.main()

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.addCleanup(self.directory.cleanup)
        self.assertTrue(hasattr(session, "PlaybackSession"), "Playback ownership is missing")

    def test_lua_load_bridge_configures_before_continuing_the_file(self):
        effects = []
        class Client:
            def command(self, command, **kwargs):
                effects.append(command)
        owner = session.PlaybackSession(self.root / "state", self.root, self.root / "runtime", notify=lambda root: None)
        owner.client = Client()
        owner.configure_file = lambda: effects.append("configured")
        owner.process_event({"event": "client-message", "args": ["open-svpflow-before-load"]})
        self.assertEqual(effects, ["configured", ["script-message", "open-svpflow-continue"]])

    def test_each_new_file_uses_saved_settings_without_touching_unrelated_filters(self):
        import settings_api
        import policy
        plugins = self.root / "lib"
        plugins.mkdir()
        for name in ("libsvpflow1_vs.dylib", "libsvpflow2_vs.dylib"):
            (plugins / name).write_bytes(b"fixture")
        player = self.root / "player"
        player.mkdir()
        (player / "graph.vpy").write_text("fixture")
        movie = self.root / "video ü.mkv"
        movie.write_bytes(b"fixture")
        commands = []
        class Client:
            def get(self, name, default=None):
                return str(movie) if name == "path" else [{"label": "user-filter"}, {"label": "svp-input"}, {"label": "svp-interpolate"}]
            def command(self, command, **kwargs):
                commands.append(command)
        owner = session.PlaybackSession(self.root / "state", self.root, self.root / "runtime", notify=lambda root: None)
        owner.client = Client()
        owner.runtime = {"mpv": "/external/mpv"}
        document = settings_api.default_document()
        settings_api.save_document(owner.state_dir / "settings.json", document)
        owner.configure_file()
        first = next(command[2] for command in commands if command[:2] == ["vf", "add"])
        self.assertIn("@svp-interpolate:vapoursynth=", first)
        self.assertNotIn(["vf", "remove", "@user-filter"], commands)
        document["playback"]["profile"] = "original"
        before = list(commands)
        settings_api.save_document(owner.state_dir / "settings.json", document)
        self.assertEqual(commands, before)
        owner.configure_file()
        self.assertFalse(owner.filter_required)
        self.assertEqual(commands[len(before):], [["vf", "remove", "@svp-input"], ["vf", "remove", "@svp-interpolate"]])

    def test_playback_log_cannot_overwrite_a_symlink_target(self):
        target = self.root / "preserved.txt"
        target.write_text("preserved")
        owner = session.PlaybackSession(self.root / "state", self.root, self.root / "runtime", notify=lambda root: None)
        (owner.state_dir / "playback.log").symlink_to(target)
        runtime = {"ready": True, "runtime": {"mpv": sys.executable, "vsscript": None}}
        with patch("dependencies.inspect_environment", return_value=runtime), patch("launch.runtime_environment", return_value={}), patch.object(session.subprocess, "Popen") as process:
            with self.assertRaises((OSError, RuntimeError)):
                owner.start()
        process.assert_not_called()
        self.assertEqual(target.read_text(), "preserved")

    def test_missing_or_disabled_interpolation_is_fatal_before_started(self):
        class Client:
            def get(self, name, default=None):
                return [{"name": "vapoursynth", "label": "svp-interpolate", "enabled": False}] if name == "vf" else default
            def command(self, command, **kwargs):
                return None
        owner = session.PlaybackSession(self.root / "state", self.root, self.root / "runtime", notify=lambda root: None)
        owner.client = Client()
        owner.filter_required = True
        owner.process_event({"event": "playback-restart"})
        self.assertFalse(owner.active)
        self.assertIn("interpolation", owner.error.lower())
        self.assertTrue(owner.ready.is_set())

    def test_normal_mpv_shutdown_does_not_reopen_the_gui(self):
        notified = []
        owner = session.PlaybackSession(self.root / "state", self.root, self.root / "runtime", notify=lambda root: notified.append(root))
        owner.active = True
        owner.process_event({"event": "shutdown"})
        self.assertTrue(owner.stopped.is_set())
        self.assertFalse(owner.active)
        self.assertEqual(notified, [])

    def test_lost_control_channel_is_fatal_while_player_is_still_running(self):
        from types import SimpleNamespace
        notified = []
        owner = session.PlaybackSession(self.root / "state", self.root, self.root / "runtime", notify=notified.append)
        owner.process = SimpleNamespace(poll=lambda: None)
        owner.active = True
        owner.process_event({"event": "connection-closed"})
        self.assertFalse(owner.active)
        self.assertIn("control channel", owner.error)
        self.assertEqual(notified, [self.root])

    def test_original_playback_can_start_without_an_interpolation_filter(self):
        class Client:
            def get(self, name, default=None):
                return [] if name == "vf" else {"w": 320, "h": 180}
            def command(self, command, **kwargs):
                return None
        owner = session.PlaybackSession(self.root / "state", self.root, self.root / "runtime", notify=lambda root: None)
        owner.client = Client()
        owner.filter_required = False
        owner.process_event({"event": "playback-restart"})
        self.assertTrue(owner.active)
        self.assertTrue(owner.ready.is_set())
        self.assertEqual(owner.snapshot()["status"], "started")

    def test_native_subtitle_drop_does_not_mutate_playlist(self):
        commands = []
        class Client:
            def get(self, name, default=None):
                return ["srt", "ass"] if name == "options/sub-auto-exts" else default
            def command(self, command, **kwargs):
                commands.append(command)
        owner = session.PlaybackSession(self.root / "state", self.root, self.root / "runtime", notify=lambda root: None)
        owner.client = Client()
        owner.process_event({"event": "property-change", "name": "dropped-files", "data": {"files": ["/tmp/captions.srt"], "time": 1}})
        self.assertEqual(commands, [["sub-add", "/tmp/captions.srt", "select"]])

    def test_replacement_waits_for_its_own_start_instead_of_reusing_previous_ready(self):
        import time
        class Client:
            def get(self, name, default=None):
                return {"w": 320} if name == "video-out-params" else []
            def command(self, command, **kwargs):
                return None
        owner = session.PlaybackSession(self.root / "state", self.root, self.root / "runtime", notify=lambda root: None)
        owner.client = Client()
        owner.filter_required = False
        owner.active = True
        owner.ready.set()
        owner._document = lambda: {"preferences": {"after_start": "quit_app", "new_files": "append"}}
        movie = self.root / "replacement.mkv"
        movie.write_bytes(b"fixture")
        results = []
        thread = threading.Thread(target=lambda: results.append(owner.open([str(movie)], "replace")))
        thread.start()
        time.sleep(0.05)
        waiting = thread.is_alive()
        owner.process_event({"event": "playback-restart"})
        thread.join(2)
        self.assertTrue(waiting, "Replacement was reported ready before its first frame")
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["status"], "started")

    def test_fatal_error_is_persisted_after_worker_exit_for_reopened_gui(self):
        notified = []
        owner = session.PlaybackSession(self.root / "state", self.root, self.root / "runtime", notify=lambda root: notified.append(root))
        owner.fail("Unable to load the interpolation graph")
        self.assertEqual(session.status(self.root / "state")["message"], "Unable to load the interpolation graph")
        self.assertEqual(session.status(self.root / "state")["status"], "idle")
        self.assertEqual(notified, [self.root])

class ControlServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.addCleanup(self.directory.cleanup)
        self.assertTrue(hasattr(session, "ControlServer"), "The independent control server is missing")

    def test_authenticated_unix_rpc_reconnects_and_rejects_unknown_commands(self):
        class Worker:
            def __init__(self, *args):
                self.stopped = threading.Event()
                self.calls = []
            def snapshot(self):
                return {"ok": True, "status": "started", "after_start": "quit_app"}
            def open(self, files, action):
                self.calls.append((files, action))
                return {"ok": True, "status": "queued", "after_start": "quit_app"}
            def close(self):
                self.stopped.set()
        control = session.ControlServer(self.root / "state", self.root, worker_factory=Worker)
        self.addCleanup(control.close)
        thread = threading.Thread(target=control.serve)
        thread.start()
        self.addCleanup(lambda: thread.join(2))
        record = session.connect_existing(self.root / "state")
        self.assertIsNotNone(record)
        self.assertEqual(session.rpc(record, "status")["status"], "started")
        self.assertEqual(session.rpc(record, "open", {"files": ["movie"], "action": "append"})["status"], "queued")
        self.assertEqual(control.worker.calls, [(["movie"], "append")])
        self.assertFalse(session.rpc(record, "exec", {"command": ["touch", "bad"]})["ok"])
        wrong = dict(record, token="incorrect")
        self.assertFalse(session.rpc(wrong, "open", {"files": ["bad"]})["ok"])
        self.assertEqual(control.worker.calls, [(["movie"], "append")])
        control.close()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse((self.root / "state" / "session.json").exists())

    def test_rejected_start_does_not_disable_idle_worker_cleanup(self):
        class Worker:
            def __init__(self, *args):
                self.stopped = threading.Event()
            def open(self, files, action):
                raise ValueError("Dependencies are missing")
            def close(self):
                self.stopped.set()
        control = session.ControlServer(self.root / "state", self.root, worker_factory=Worker)
        self.addCleanup(control.close)
        with self.assertRaisesRegex(ValueError, "Dependencies"):
            control.dispatch({"token": control.token, "command": "open", "payload": {"files": ["fixture"]}})
        self.assertFalse(control.used)

    def test_slow_open_keeps_live_worker_discoverable_for_status_and_second_open(self):
        class Worker:
            def __init__(self, *args):
                self.stopped = threading.Event()
                self.entered = threading.Event()
                self.release = threading.Event()
                self.calls = []
                self.active = False
                self.overlap = False
                self.opening = False
            def snapshot(self):
                return {"ok": True, "status": "started" if self.active else "idle"}
            def open(self, files, action):
                self.overlap = self.overlap or self.opening
                self.opening = True
                self.calls.append((files, action))
                if len(self.calls) == 1:
                    self.entered.set()
                    self.release.wait(3)
                self.active = True
                self.opening = False
                return {"ok": True, "status": "started" if len(self.calls) == 1 else "queued"}
            def close(self):
                self.release.set()
                self.stopped.set()
        control = session.ControlServer(self.root / "state", self.root, worker_factory=Worker)
        server = threading.Thread(target=control.serve)
        server.start()
        responses = []
        first = threading.Thread(target=lambda: responses.append(session.rpc(control.record, "open", {"files": ["first"], "action": "append"})))
        second = None
        try:
            first.start()
            self.assertTrue(control.worker.entered.wait(1))
            started = time.monotonic()
            self.assertTrue(session.status(self.root / "state")["ok"])
            self.assertTrue((self.root / "state" / "session.json").exists(), "A slow open erased the live rendezvous")
            self.assertLess(time.monotonic() - started, 0.75)
            movie = self.root / "second movie.mkv"
            movie.write_bytes(b"fixture")
            with patch.object(session.subprocess, "Popen") as process:
                second = threading.Thread(target=lambda: responses.append(session.open_files(self.root / "state", self.root, [str(movie)], "append")))
                second.start()
                self.assertEqual(session.connect_existing(self.root / "state"), control.record)
                time.sleep(0.05)
                self.assertEqual(len(control.worker.calls), 1)
                control.worker.release.set()
                first.join(2)
                second.join(2)
                process.assert_not_called()
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertFalse(control.worker.overlap)
            self.assertEqual([value[0] for value in control.worker.calls], [["first"], [str(movie)]])
            self.assertEqual(sorted(value["status"] for value in responses), ["queued", "started"])
            self.assertEqual(session.connect_existing(self.root / "state"), control.record)
        finally:
            control.worker.release.set()
            first.join(2)
            if second is not None:
                second.join(2)
            control.close()
            server.join(2)

    def test_worker_survives_transient_clients_and_reconnects_to_the_same_process(self):
        source = """
import signal, sys, threading
sys.path.insert(0, sys.argv[1])
import session
class Worker:
    def __init__(self, *args):
        self.stopped = threading.Event()
    def snapshot(self):
        return {"ok": True, "status": "started", "after_start": "quit_app"}
    def open(self, files, action):
        return self.snapshot()
    def close(self):
        self.stopped.set()
control = session.ControlServer(sys.argv[2], sys.argv[3], worker_factory=Worker)
signal.signal(signal.SIGTERM, lambda *args: control.close())
control.serve()
"""
        state = self.root / "state"
        child = subprocess.Popen([sys.executable, "-B", "-c", source, str(ROOT / "player"), str(state), str(ROOT)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 3
            record = None
            while time.monotonic() < deadline and child.poll() is None:
                record = session.connect_existing(state)
                if record:
                    break
                time.sleep(0.02)
            self.assertIsNotNone(record)
            arguments = [sys.executable, "-B", str(ROOT / "player" / "application.py"), "status", "--state-dir", str(state), "--bundle-root", str(ROOT)]
            for _ in range(2):
                response = subprocess.run(arguments, input="{}", capture_output=True, text=True, timeout=3)
                self.assertEqual(response.returncode, 0, response.stderr)
                self.assertEqual(json.loads(response.stdout)["status"], "started")
                self.assertEqual(session.connect_existing(state)["pid"], child.pid)
                self.assertIsNone(child.poll())
        finally:
            child.terminate()
            _, error = child.communicate(timeout=3)
        self.assertEqual(child.returncode, 0, error.decode())
        self.assertFalse((state / "session.json").exists())
