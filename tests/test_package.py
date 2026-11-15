import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_release", root / "scripts/package_release.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PackageTests(unittest.TestCase):
    def test_distribution_contains_only_plugins_adapter_readme_and_notices(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "player/launch.py", "player/graph.vpy", "player/policy.py", "player/settings.json", "player/compat.lua", "docs/usage.md"):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            libraries = Path(directory) / "libraries"
            libraries.mkdir()
            for name in module.PLUGINS:
                (libraries / name).write_bytes(b"native-plugin")
            result = module.package(source, libraries, Path(directory) / "dist", "0.1.3-mac.1")
            self.assertEqual(result.name, "open-svpflow-0.1.3-mac.1-arm64.zip")
            with zipfile.ZipFile(result) as archive:
                self.assertEqual(set(archive.namelist()), {"open-svpflow/" + name for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "player/launch.py", "player/graph.vpy", "player/policy.py", "player/settings.json", "player/compat.lua", "lib/libsvpflow1_vs.dylib", "lib/libsvpflow2_vs.dylib")})
                self.assertEqual(archive.read("open-svpflow/lib/libsvpflow1_vs.dylib"), b"native-plugin")

    def test_invalid_version_fails_before_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with self.assertRaises(ValueError):
                module.package(Path(directory), Path(directory), output, "../escape")
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
