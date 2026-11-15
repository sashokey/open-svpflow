import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent


def smoke_module(name):
    path = ROOT / (name + '.py')
    if not path.is_file():
        raise AssertionError('Native smoke test has not been implemented')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IntermediateTests(unittest.TestCase):
    def setUp(self):
        self.previous = bytes([32] * 4 + [180] * 4 + [32] * 8)
        self.following = bytes([32] * 8 + [180] * 4 + [32] * 4)
        self.intermediate = bytes([32] * 6 + [180] * 4 + [32] * 6)

    def test_translated_intermediate_is_accepted_without_frame_properties(self):
        metrics = smoke_module('smoke_plugins').check_intermediate(self.previous, self.following, self.intermediate)
        self.assertGreater(metrics['source_delta'], 0.25)
        self.assertGreater(metrics['blend_delta'], 0.25)

    def test_repeated_source_frame_is_rejected(self):
        module = smoke_module('smoke_plugins')
        for repeated in (self.previous, self.following):
            with self.subTest(repeated=repeated), self.assertRaisesRegex(AssertionError, 'source'):
                module.check_intermediate(self.previous, self.following, repeated)

    def test_simple_frame_blend_is_rejected(self):
        blend = bytes((a + b + 1) // 2 for a, b in zip(self.previous, self.following))
        with self.assertRaisesRegex(AssertionError, 'blend'):
            smoke_module('smoke_plugins').check_intermediate(self.previous, self.following, blend)


class MpvSmokeTests(unittest.TestCase):
    def test_package_smoke_disables_display_and_audio_and_checks_qhd(self):
        module = smoke_module('smoke_mpv')
        package = Path('/extracted package')
        movie = Path('/temporary motion.y4m')
        command = module.build_command(package, movie)
        self.assertEqual(command[:2], [sys.executable, str(package / 'player' / 'launch.py')])
        self.assertIn('--mpv-option=--vo=null', command)
        self.assertIn('--mpv-option=--audio=no', command)
        self.assertIn('--mpv-option=--untimed', command)
        self.assertIn('--mpv-option=--term-playing-msg=OSVP_SMOKE=${video-out-params/w}x${video-out-params/h}', command)
        self.assertEqual(command[-2:], ['--', str(movie)])


if __name__ == '__main__':
    unittest.main()
