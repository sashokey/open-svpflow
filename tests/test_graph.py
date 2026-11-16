from fractions import Fraction
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

try:
    import vapoursynth as vs
except ImportError:
    vs = None


ROOT = Path(__file__).resolve().parents[1]
RANGE_KEY = "_Range" if vs is not None and hasattr(vs, "RANGE_LIMITED") else "_ColorRange"
RANGE_LIMITED = getattr(vs, "RANGE_LIMITED", 1)
RANGE_FULL = getattr(vs, "RANGE_FULL", 0)


@unittest.skipIf(vs is None, "Requires the external VapourSynth Python environment")
class GraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = runpy.run_path(str(ROOT / "player" / "graph.vpy"))
        vs.core.max_cache_size = 128

    def source(self, format, width=320, height=192, color=None, **props):
        args = dict(width=width, height=height, format=format, length=4, fpsnum=24)
        if color is not None:
            args["color"] = color
        if "_ColorRange" in props:
            props[RANGE_KEY] = RANGE_LIMITED if props.pop("_ColorRange") == 1 else RANGE_FULL
        props.setdefault("_SARNum", 1)
        props.setdefault("_SARDen", 1)
        clip = vs.core.std.BlankClip(**args)
        return vs.core.std.SetFrameProps(clip, **props) if props else clip

    def prepare(self, clip, **kwargs):
        return self.graph["prepare_clip"](clip, **kwargs)

    def test_graph_confirms_target_only_after_producing_a_frame(self):
        policy = self.graph["policy"]
        plugins = next((folder for folder in (ROOT / "lib", ROOT / "target/release")
                        if all((folder / name).is_file() for name in policy.PLUGIN_NAMES)), None)
        if plugins is None:
            self.skipTest("Requires built interpolation plugins")
        payload = policy.encode_payload({"schema": 1, "plugins": str(plugins),
                                         "settings": {"backend": "cpu"}, "fps": "48", "attempt": 3})
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPEN_SVPFLOW_GRAPH_DIR": directory}):
            report = Path(directory) / "graph-3.json"
            output = self.graph["bootstrap"](self.source(vs.YUV420P8), 24, 60, payload)
            self.assertFalse(report.exists())
            with output.get_frame(0):
                self.assertEqual(json.loads(report.read_text()), {"target_fps": 48.0})

    def test_source_size_is_preserved_without_a_fixed_processing_canvas(self):
        for width, height in ((320, 192), (1920, 1080), (3840, 2160)):
            with self.subTest(size=(width, height)):
                synthesis, analysis = self.prepare(self.source(vs.YUV420P8, width, height))
                self.assertEqual((synthesis.width, synthesis.height), (width, height))
                self.assertEqual((analysis.width, analysis.height), (width, height))

    def test_svp_compatibility_prepares_420_with_source_geometry_and_eight_bit_analysis(self):
        for format in (vs.YUV420P10, vs.YUV444P10, vs.RGB24):
            with self.subTest(format=format):
                clip = self.source(format, _Matrix=1, _Primaries=1, _Transfer=1)
                synthesis, analysis = self.prepare(clip, output_bits=10, dither="random", compatibility="svp")
                self.assertEqual((synthesis.width, synthesis.height, synthesis.format.id), (320, 192, vs.YUV420P10))
                self.assertEqual(analysis.format.id, vs.YUV420P8)
                with synthesis.get_frame(0) as frame:
                    self.assertEqual(frame.props["_Transfer"], 1)

    def test_svp_compatibility_pads_odd_input_without_resizing_content(self):
        synthesis, analysis = self.prepare(self.source(vs.RGB24, width=321, height=193), output_bits=8, compatibility="svp")
        self.assertEqual((synthesis.width, synthesis.height, synthesis.format.id), (322, 194, vs.YUV420P8))
        self.assertEqual((analysis.width, analysis.height), (322, 194))

    def test_widescreen_and_portrait_geometry_are_preserved(self):
        for size in ((640, 268), (192, 320)):
            with self.subTest(size=size):
                synthesis, _ = self.prepare(self.source(vs.YUV420P8, *size))
                self.assertEqual((synthesis.width, synthesis.height), size)
                with synthesis.get_frame(0) as frame:
                    sar = Fraction(frame.props["_SARNum"], frame.props["_SARDen"])
                    self.assertEqual(Fraction(frame.width, frame.height) * sar, Fraction(*size))

    def test_coprime_geometry_stays_original(self):
        synthesis, _ = self.prepare(self.source(vs.RGB24, width=321, height=193))
        self.assertEqual((synthesis.width, synthesis.height), (321, 193))

    def test_anamorphic_source_size_and_sample_aspect_are_preserved(self):
        clip = self.source(vs.YUV420P8, width=720, height=576, _SARNum=16, _SARDen=15)
        synthesis, _ = self.prepare(clip)
        self.assertEqual((synthesis.width, synthesis.height), (720, 576))
        with synthesis.get_frame(0) as frame:
            self.assertEqual(Fraction(frame.width, frame.height) * Fraction(frame.props["_SARNum"], frame.props["_SARDen"]), Fraction(4, 3))

    def test_existing_10_bit_hdr_synthesis_and_metadata_are_preserved(self):
        clip = self.source(vs.YUV420P10, color=[400, 512, 512], _Matrix=9, _Transfer=16, _Primaries=9, _ColorRange=1)
        synthesis, analysis = self.prepare(clip, output_bits="source")
        self.assertEqual(synthesis.format.id, vs.YUV420P10)
        self.assertEqual(analysis.format.id, vs.YUV420P8)
        with synthesis.get_frame(0) as frame:
            self.assertEqual(frame[0][0, 0], 400)
            self.assertEqual(tuple(frame.props[key] for key in ("_Matrix", "_Transfer", "_Primaries", RANGE_KEY)), (9, 16, 9, RANGE_LIMITED))

    def test_default_8_bit_and_explicit_synthesis_depths(self):
        clip = self.source(vs.YUV420P10, color=[400, 512, 512])
        for bits in (8, 10, 16):
            with self.subTest(bits=bits):
                synthesis, analysis = self.prepare(clip, output_bits=bits)
                self.assertEqual(synthesis.format.bits_per_sample, bits)
                self.assertEqual(analysis.format.bits_per_sample, 8)
                with synthesis.get_frame(0) as frame:
                    self.assertLessEqual(abs(frame[0][0, 0] - 400 * 2 ** (bits - 10)), 1)
        synthesis, _ = self.prepare(clip)
        self.assertEqual(synthesis.format.bits_per_sample, 8)

    def test_all_dither_modes_keep_quantization_within_one_8_bit_step(self):
        clip = self.source(vs.YUV420P10, color=[257, 512, 512], _Matrix=9, _Transfer=16, _Primaries=9, _ColorRange=1)
        for name in ("none", "ordered", "random", "error_diffusion"):
            with self.subTest(dither=name):
                synthesis, analysis = self.prepare(clip, output_bits=8, dither=name)
                self.assertEqual((synthesis.width, synthesis.height, synthesis.format.id), (320, 192, vs.YUV420P8))
                with synthesis.get_frame(0) as frame:
                    values = set(bytes(frame[0]))
                    self.assertEqual(values, {64} if name == "none" else {64, 65})
                    self.assertEqual(tuple(frame.props[key] for key in ("_Matrix", "_Transfer", "_Primaries", RANGE_KEY)), (9, 16, 9, RANGE_LIMITED))
        with self.assertRaises(ValueError):
            self.prepare(clip, dither="invalid")

    def test_legacy_mpv_bridge_full_range_is_scaled_correctly(self):
        clip = self.source(vs.YUV420P8, color=[100, 128, 128])
        clip = vs.core.std.SetFrameProps(clip, _ColorRange=0, _ColorSpace=1)
        synthesis, _ = self.prepare(clip, output_bits=16)
        with synthesis.get_frame(0) as frame:
            self.assertEqual(frame[0][0, 0], 25700)
            self.assertEqual(frame.props[RANGE_KEY], RANGE_FULL)
            self.assertEqual(frame.props["_Matrix"], 1)

    def test_yuv_422_and_444_inputs_are_converted_without_8_bit_chroma_downsampling(self):
        for format in (vs.YUV422P8, vs.YUV444P8):
            with self.subTest(format=format):
                synthesis, analysis = self.prepare(self.source(format, color=[100, 128, 128]))
                self.assertEqual(synthesis.format.id, vs.YUV444P8)
                self.assertEqual(analysis.format.id, vs.YUV444P8)
                with synthesis.get_frame(0) as frame:
                    self.assertEqual(tuple(frame[p][0, 0] for p in range(3)), (100, 128, 128))

    def test_high_depth_inputs_keep_precision_in_native_16_bit_synthesis(self):
        for format, color, expected in ((vs.YUV420P12, [1600, 2048, 2048], 25600), (vs.YUV444P16, [25600, 32768, 32768], 25600)):
            with self.subTest(format=format):
                synthesis, analysis = self.prepare(self.source(format, color=color), output_bits="source")
                self.assertEqual(synthesis.format.id, vs.YUV420P16)
                self.assertEqual(analysis.format.id, vs.YUV420P8)
                with synthesis.get_frame(0) as frame:
                    self.assertEqual(frame[0][0, 0], expected)

    def test_rgb_red_converts_with_bt709_coefficients_and_limited_range(self):
        clip = self.source(vs.RGB24, color=[255, 0, 0], _Matrix=0, _Transfer=13, _Primaries=1, _ColorRange=0)
        synthesis, analysis = self.prepare(clip)
        self.assertEqual(synthesis.format.id, vs.YUV444P8)
        with synthesis.get_frame(0) as frame:
            for actual, expected in zip((frame[p][0, 0] for p in range(3)), (63, 102, 240)):
                self.assertLessEqual(abs(actual - expected), 1)
            self.assertEqual(tuple(frame.props[key] for key in ("_Matrix", "_Transfer", "_Primaries", RANGE_KEY)), (1, 13, 1, RANGE_LIMITED))

    def test_rgb_hdr_uses_bt2020_matrix_and_keeps_pq_transfer(self):
        clip = self.source(vs.RGB48, color=[65535, 0, 0], _Matrix=0, _Transfer=16, _Primaries=9, _ColorRange=0)
        synthesis, _ = self.prepare(clip, output_bits="source")
        self.assertEqual(synthesis.format.id, vs.YUV420P16)
        with synthesis.get_frame(0) as frame:
            self.assertEqual(tuple(frame.props[key] for key in ("_Matrix", "_Transfer", "_Primaries", RANGE_KEY)), (9, 16, 9, RANGE_LIMITED))

    def test_float_rgb_and_grayscale_are_supported(self):
        for format, color in ((vs.RGBS, [0.5, 0.5, 0.5]), (vs.GRAY8, [100]), (vs.GRAY16, [25600])):
            with self.subTest(format=format):
                synthesis, _ = self.prepare(self.source(format, color=color))
                with synthesis.get_frame(0) as frame:
                    self.assertEqual(frame.format.color_family, vs.YUV)
                    midpoint = 128 if frame.format.bits_per_sample == 8 else 32768
                    self.assertLessEqual(abs(frame[1][0, 0] - midpoint), 1)

    def test_odd_rgb_geometry_is_not_stretched_or_forced_to_even_dimensions(self):
        synthesis, _ = self.prepare(self.source(vs.RGB24, width=321, height=193, color=[0, 0, 0]))
        self.assertEqual((synthesis.width, synthesis.height, synthesis.format.id), (321, 193, vs.YUV444P8))
        with synthesis.get_frame(0) as frame:
            self.assertEqual((frame.props["_SARNum"], frame.props["_SARDen"]), (1, 1))


if __name__ == "__main__":
    if vs is None:
        raise SystemExit("Run this test with the installed VapourSynth Python environment")
    unittest.main()
