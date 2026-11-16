import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dependencies", root / "player/dependencies.py")
dependencies = None
if spec.origin and Path(spec.origin).exists():
    dependencies = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dependencies)


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(dependencies, "The dependency preparation API is missing")

    def fixture(self, directory):
        base = Path(directory)
        resources = base / "Open SVPflow.app/Contents/Resources"
        plugins = resources.parent / "Frameworks"
        plugins.mkdir(parents=True)
        (resources / "player").mkdir(parents=True)
        for name in ("libsvpflow1_vs.dylib", "libsvpflow2_vs.dylib"):
            (plugins / name).write_bytes(b"plugin")
        prefix = base / "Homebrew space"
        for relative in ("bin/brew", "bin/mpv", "bin/vspipe", "opt/python/bin/python3", "lib/libvsscript.dylib"):
            path = prefix / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
        (prefix / "bin/vspipe").write_text("#!" + str(prefix / "opt/python/bin/python3") + "\n")
        (prefix / "bin/mpv").write_bytes(bytes.fromhex("cffaedfe0c000001") + bytes(24))
        return resources, prefix

    def completed(self, args, text="", returncode=0):
        return subprocess.CompletedProcess(args, returncode, text, "")

    def test_missing_homebrew_returns_setup_without_running_commands(self):
        with patch.object(dependencies, "find_homebrew", return_value=None), patch.object(subprocess, "run") as run:
            result = dependencies.inspect_environment(root)
        self.assertFalse(result["ready"])
        self.assertTrue(result["needs_homebrew"])
        run.assert_not_called()

    def test_complete_environment_uses_matching_interpreter_and_loads_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            resources, prefix = self.fixture(directory)
            calls = []
            def execute(args, **kwargs):
                calls.append((args, kwargs))
                if args[1:] == ["--prefix"]:
                    return self.completed(args, str(prefix) + "\n")
                if "--list-properties" in args:
                    return self.completed(args, "playlist\ndropped-files\n")
                if "--vf=help" in args:
                    return self.completed(args, "vapoursynth\nlavfi\n")
                if "--idle=yes" in args:
                    return self.completed(args, "OSVP_LUA_READY")
                return self.completed(args, json.dumps({"vsscript": str(prefix / "lib/libvsscript.dylib"), "api": 4, "plugins": True, "machine": "arm64"}))
            with patch.object(dependencies, "find_homebrew", return_value=prefix / "bin/brew"), patch.object(subprocess, "run", side_effect=execute):
                result = dependencies.inspect_environment(resources)
            self.assertTrue(result["ready"], result)
            self.assertEqual(result["runtime"]["python"], str(prefix / "opt/python/bin/python3"))
            self.assertTrue(any(args[0] == str(prefix / "opt/python/bin/python3") and "-I" in args for args, _ in calls))
            self.assertTrue(all(kwargs["env"]["HOMEBREW_NO_AUTO_UPDATE"] == "1" for _, kwargs in calls))
            self.assertTrue(all(kwargs["timeout"] <= 15 for _, kwargs in calls))

    def test_old_mpv_requests_dependencies_instead_of_reporting_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            resources, prefix = self.fixture(directory)
            def execute(args, **kwargs):
                return self.completed(args, str(prefix) if args[1:] == ["--prefix"] else "playlist\n")
            with patch.object(dependencies, "find_homebrew", return_value=prefix / "bin/brew"), patch.object(subprocess, "run", side_effect=execute):
                result = dependencies.inspect_environment(resources)
            self.assertFalse(result["ready"])
            self.assertTrue(result["needs_dependencies"])

    def test_blocked_plugin_is_an_error_not_an_install_request(self):
        with tempfile.TemporaryDirectory() as directory:
            resources, prefix = self.fixture(directory)
            def execute(args, **kwargs):
                if args[1:] == ["--prefix"]:
                    return self.completed(args, str(prefix))
                if "--list-properties" in args:
                    return self.completed(args, "dropped-files\n")
                if "--vf=help" in args:
                    return self.completed(args, "vapoursynth lavfi")
                if "--idle=yes" in args:
                    return self.completed(args, "OSVP_LUA_READY")
                return subprocess.CompletedProcess(args, 1, "", "library load disallowed by system policy")
            with patch.object(dependencies, "find_homebrew", return_value=prefix / "bin/brew"), patch.object(subprocess, "run", side_effect=execute):
                result = dependencies.inspect_environment(resources)
            self.assertFalse(result["ready"])
            self.assertFalse(result["needs_dependencies"])
            self.assertIn("system policy", result["message"])

    def test_missing_vapoursynth_offers_dependency_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            resources, prefix = self.fixture(directory)
            def execute(args, **kwargs):
                if args[1:] == ["--prefix"]:
                    return self.completed(args, str(prefix))
                if "--list-properties" in args:
                    return self.completed(args, "dropped-files")
                if "--vf=help" in args:
                    return self.completed(args, "vapoursynth lavfi")
                if "--idle=yes" in args:
                    return self.completed(args, "OSVP_LUA_READY")
                return subprocess.CompletedProcess(args, 1, "", "ModuleNotFoundError: No module named 'vapoursynth'")
            with patch.object(dependencies, "find_homebrew", return_value=prefix / "bin/brew"), patch.object(subprocess, "run", side_effect=execute):
                result = dependencies.inspect_environment(resources)
            self.assertFalse(result["ready"])
            self.assertTrue(result["needs_dependencies"])

    def test_mpv_without_working_lua_does_not_pass_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            resources, prefix = self.fixture(directory)
            def execute(args, **kwargs):
                if args[1:] == ["--prefix"]:
                    return self.completed(args, str(prefix))
                if "--list-properties" in args:
                    return self.completed(args, "dropped-files")
                if "--vf=help" in args:
                    return self.completed(args, "vapoursynth lavfi")
                if "--idle=yes" in args:
                    return self.completed(args, "")
                return self.completed(args, json.dumps({"vsscript": str(prefix / "lib/libvsscript.dylib"), "api": 4, "plugins": True, "machine": "arm64"}))
            with patch.object(dependencies, "find_homebrew", return_value=prefix / "bin/brew"), patch.object(subprocess, "run", side_effect=execute):
                result = dependencies.inspect_environment(resources)
            self.assertFalse(result["ready"])
            self.assertTrue(result["needs_dependencies"])

    def test_failed_homebrew_download_exits_before_success_message(self):
        source = (root / "player/install-homebrew.command").read_text()
        self.assertEqual(source.count("/usr/bin/curl"), 1, "Refuse to execute an installer fixture unless its downloader was replaced")
        fixture = source.replace("/usr/bin/curl", "/usr/bin/false")
        self.assertNotIn("/usr/bin/curl", fixture)
        result = subprocess.run(["/bin/bash", "-c", fixture], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("preparation finished", result.stdout)

    def test_lua_execution_error_offers_dependency_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            resources, prefix = self.fixture(directory)
            def execute(args, **kwargs):
                if args[1:] == ["--prefix"]:
                    return self.completed(args, str(prefix))
                if "--list-properties" in args:
                    return self.completed(args, "dropped-files")
                if "--vf=help" in args:
                    return self.completed(args, "vapoursynth lavfi")
                return subprocess.CompletedProcess(args, 2, "", "Lua support is unavailable")
            with patch.object(dependencies, "find_homebrew", return_value=prefix / "bin/brew"), patch.object(subprocess, "run", side_effect=execute):
                result = dependencies.inspect_environment(resources)
            self.assertFalse(result["ready"])
            self.assertTrue(result["needs_dependencies"])

    def test_timeout_returns_actionable_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            resources, prefix = self.fixture(directory)
            with patch.object(dependencies, "find_homebrew", return_value=prefix / "bin/brew"), patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(["brew"], 5)):
                result = dependencies.inspect_environment(resources)
            self.assertFalse(result["ready"])
            self.assertIn("timed out", result["message"].lower())

    def test_prepare_installer_only_copies_owned_script_without_execution(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(subprocess, "run") as run:
            path = dependencies.prepare_install_script("homebrew", Path(directory))
            self.assertEqual(path.parent, Path(directory) / "setup")
            self.assertEqual(path.read_bytes(), (root / "player/install-homebrew.command").read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            run.assert_not_called()

    def test_unknown_installer_kind_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                dependencies.prepare_install_script("arbitrary", Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_symlinked_setup_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            other = Path(directory) / "other"
            state.mkdir()
            other.mkdir()
            (state / "setup").symlink_to(other, target_is_directory=True)
            with self.assertRaises((ValueError, OSError)):
                dependencies.prepare_install_script("homebrew", state)
            self.assertEqual(list(other.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
