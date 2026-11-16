import importlib.util
import plistlib
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
import zipfile


root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_release", root / "scripts/package_release.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PackageTests(unittest.TestCase):
    def test_distribution_contains_one_app_with_external_runtime_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "player/launch.py", "player/graph.vpy", "player/policy.py", "player/settings_api.py", "player/settings.json", "player/compat.lua", "player/application.py", "player/session.py", "player/dependencies.py", "player/install-homebrew.command", "player/install-dependencies.command", "docs/usage.md"):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            libraries = Path(directory) / "libraries"
            libraries.mkdir()
            for name in module.PLUGINS:
                (libraries / name).write_bytes(b"native-plugin")
            executable = Path(directory) / "OpenSVPflow"
            executable.write_bytes(b"native-launcher")
            executable.chmod(0o755)
            icons = Path(directory) / "icons"
            icons.mkdir()
            (icons / "OpenSVPflow.icns").write_bytes(b"app-icon")
            (icons / "StatusIconTemplate.png").write_bytes(b"status-icon")
            app = module.assemble_bundle(source, libraries, executable, icons, Path(directory) / "build", "0.1.3-mac.5")
            result = module.archive_bundle(app, Path(directory) / "dist", "0.1.3-mac.5")
            self.assertEqual(result.name, "open-svpflow-0.1.3-mac.5-arm64.zip")
            with zipfile.ZipFile(result) as archive:
                names = archive.namelist()
                self.assertEqual({name.split('/')[0] for name in names}, {"Open SVPflow.app"})
                prefix = "Open SVPflow.app/Contents/"
                self.assertEqual(archive.read(prefix + "Frameworks/libsvpflow1_vs.dylib"), b"native-plugin")
                self.assertEqual(archive.read(prefix + "Resources/player/settings_api.py"), b"player/settings_api.py")
                self.assertEqual(archive.read(prefix + "Resources/LICENSE"), b"LICENSE")
                self.assertFalse(any("docs/" in name or "__pycache__" in name for name in names))
                self.assertFalse(any(name.endswith("/mpv") or name.endswith("/python3") or "libvsscript" in name for name in names))
                self.assertTrue(archive.getinfo(prefix + "MacOS/OpenSVPflow").external_attr >> 16 & 0o111)
                metadata = plistlib.loads(archive.read(prefix + "Info.plist"))
                self.assertEqual(metadata["CFBundleExecutable"], "OpenSVPflow")
                self.assertEqual(metadata["LSMinimumSystemVersion"], "26.0")

    def test_invalid_version_fails_before_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with self.assertRaises(ValueError):
                module.archive_bundle(Path(directory), output, "../escape")
            self.assertFalse(output.exists())

    @unittest.skipUnless(Path("/usr/bin/ditto").is_file(), "macOS archive extraction")
    def test_native_archive_extraction_preserves_application_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / module.APP_NAME
            executable = app / "Contents/MacOS/OpenSVPflow"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"executable fixture")
            executable.chmod(0o755)
            archive = module.archive_bundle(app, root / "dist", "0.1.3-mac.5")
            subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(root / "extracted")], check=True, timeout=10)
            extracted = root / "extracted" / module.APP_NAME / "Contents/MacOS/OpenSVPflow"
            self.assertTrue(os.access(extracted, os.X_OK))
            self.assertEqual(extracted.read_bytes(), b"executable fixture")

    def test_archive_rejects_symlinks_instead_of_collecting_external_files(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Open SVPflow.app"
            app.mkdir()
            outside = Path(directory) / "private"
            outside.write_text("not part of the app")
            (app / "alias").symlink_to(outside)
            with self.assertRaises(ValueError):
                module.archive_bundle(app, Path(directory) / "dist", "0.1.3-mac.5")


if __name__ == "__main__":
    unittest.main()
