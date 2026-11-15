Native macOS ARM64 interpolation plugins with a launcher for separately installed mpv. Based on [upstream open-svpflow 0.1.3](https://github.com/Z1xus/open-svpflow/tree/badc0de2282d2300303f5b9461919a2bc4dbcf65). Upstream already supports ARM64; the only engine change fixes signed motion-vector decoding. The wrapper supplies configurable playback without an application bundle.

Requires an Apple Silicon Mac, Python 3.9+, VapourSynth API 4 and mpv with Lua, libavfilter and the VapourSynth filter. Local checks cover macOS 27.

## Installation

Follow the official instructions for [mpv](https://mpv.io/installation/), [Python](https://www.python.org/downloads/macos/) and [VapourSynth](https://www.vapoursynth.com/doc/installation.html). With an existing Homebrew installation:

```sh
brew install mpv vapoursynth
mpv --vf=help
```

The filter list must include `vapoursynth`. Homebrew supplies a matching Python environment, which the launcher discovers.

Extract the ARM64 ZIP from [Releases](https://github.com/sashokey/open-svpflow/releases) and keep its directories together. Plugins are ad-hoc signed, not notarized. External dependencies are not bundled.

From the extracted directory, replace the movie path and run:

```sh
python3 player/launch.py --profile smooth --settings player/settings.json -- "/path/to/movie.mkv"
```

## Usage notes

Without `--fps`, the target follows mpv's reported display refresh, including fractional rates. Pass `--fps 120000/1001` for an exact target. Rates must exceed the source and fit native timing fields; there is no fixed source/target FPS ceiling. The default `auto` profile keeps the original rate when refresh is unavailable or insufficient; `smooth` requires interpolation; `original` bypasses the wrapper filters. Source dimensions and sample aspect ratio are retained. Window/fullscreen scaling remains under mpv control, independently of processing resolution. When `autofit-larger` is unset, the launcher defaults it to `100%x100%`: small videos keep their natural window size and large videos fit within the screen, preserving aspect. Existing configuration and `--mpv-option=--autofit-larger=...` overrides are respected.

Settings use JSON, not a graphical menu. Pass `--settings player/settings.json` to use edits; omitted fields inherit defaults, including nested fields. `--bits 8|10|16|source` and `--backend gpu|cpu` override JSON. `--mpv`, `--plugin-dir` and `--vsscript` override dependency locations. `--start` and `--duration` accept start/length in seconds. Forward an mpv option with `--mpv-option=--option=value`.

| Control / JSON field | Default | Values and meaning |
|---|---|---|
| Frames interpolation mode / `interpolation_mode` | `3` | `0–3`; native scene/interpolation mode |
| SVP shader / `shader` | `23` | `1/2/11/13/21/22/23/90–100`; native algorithm IDs |
| Artifacts masking / `artifacts_masking` | `cover:80, area:0` | Nonnegative native integer cover/area values |
| Motion vectors precision / `motion_precision` | `2` | `1/2/4`: whole/half/quarter pixel |
| Motion vectors grid / `motion_grid` | `block:16, overlap:2` | Square block `4/8/16/32`; overlap `0/1/2/3`: none, 1/8, 1/4, 1/2 block; 4-pixel blocks have no overlap |
| Decrease grid step / `decrease_grid_step` | `0` | `0–3` refinement stages, subject to remaining block/overlap size |
| Search radius / `search_radius` | `fine:0, coarse:-8` | Native signed distances; negative values select adaptive search |
| Wide search / `wide_search` | `true` | Wider search for bad vectors |
| Width of top coarse level / `coarse_width` | `1050` | `0–2147483647` pixels; `0` disables width-based coarse selection |
| Processing of scene changes / `scene_changes` | `nearest` | `nearest/blend`; scene-boundary fallback |
| Duplicate frames removal / `duplicate_frames_removal` | `false` | Native duplicate handling; requires 8-bit synthesis |
| Synthesis depth / `output_bits` | `8` | `8/10/16/source`; `source` preserves integer depth as 8/10/16, float becomes 16 |
| Conversion dithering / `dither` | `ordered` | `none/ordered/random/error_diffusion`; ordered is fast, error diffusion preserves the previous conversion method but costs more CPU |
| Synthesis backend / `backend` | `gpu` | `gpu/cpu`; OpenCL is requested by default, with native CPU fallback |
| GPU device / `gpu_device` | `0` | Native GPU index, starting at zero; ignored for CPU |
| mpv buffering / `buffered_frames` | `auto` | Omit the override, using mpv's default of 4 per concurrent request; or `1–9999` |
| mpv concurrency / `concurrent_frames` | `auto` | Omit the override, using mpv's automatic logical-core count; or `1–99` |
| Advanced native options / `native` | Empty objects | `super`, `analyse`, `smooth`; recursively override generated algorithm options |

For example, a partial settings file can tune search, scene thresholds, masks and rectangular blocks directly:

```json
{
  "output_bits": 10,
  "native": {
    "super": {"scale": {"up": 1}},
    "analyse": {"block": {"w": 16, "h": 8}, "main": {"search": {"type": 3}}},
    "smooth": {"scene": {"limits": {"scene": 3500}}, "mask": {"still": true}}
  }
}
```

The trees follow the [SVPflow API](https://www.svp-team.com/wiki/Manual:SVPflow) as implemented by [open-svpflow](https://github.com/Z1xus/open-svpflow). This exposes engine options rather than promising that every feature of official SVP is implemented identically. Native overrides apply after the simple controls. Timing (`smooth.rate`) and backend selection (`super.gpu`, `analyse.gpu`, `smooth.api/gpuid/render`) are managed by the wrapper; use the dedicated controls instead. Unknown engine keys follow upstream parsing behavior.

Real engine constraints remain: scene mode `2` requires at least a 2× rate; refinement cannot halve a one-pixel overlap or a block below the native minimum; CPU synthesis requires even final overlap. High-depth CPU synthesis requires precision 1/2 and a reference shader without incompatible debug flags. Block mode is CPU-only and cubic mode GPU-only. Invalid combinations report an error rather than changing the engine.

Decoded YUV, RGB, grayscale and float input is normalized by mpv/libavfilter before the VapourSynth bridge, preserving tagged primaries and transfer. Analysis always uses an 8-bit copy. When reducing depth, the selected dithering method changes the fine quantization/noise pattern; switching methods does not change interpolation algorithms or guarantee visually identical pixels. Synthesis supports 8-bit 4:2:0/4:4:4 and 10/16-bit 4:2:0, so higher-depth RGB/4:2:2/4:4:4 loses chroma resolution. Even 4:2:0 input retains its chroma format before the bridge, including before mpv selects the video track. Odd coded dimensions or preceding filters use full-chroma bridge input; any internal chroma padding is cropped after interpolation. Input dimensions must stay fixed within a graph; mpv must reinitialize it after a size change. No HDR-to-SDR tone mapping is added. Avoid a single input-buffer slot when using interpolation: a crash has been observed in the mpv 0.41.0 VapourSynth input bridge. Leave buffering on `auto` unless tuning against the installed mpv version. A target FPS does not guarantee sustainable speed; headless checks do not validate display/HDR presentation or audio sync.

Ordinary mpv user configuration, scripts and filters remain enabled. The launcher creates temporary VapourSynth loader configuration without modifying permanent player settings. URLs, mpv protocols and `-` for stdin are passed to external mpv unchanged. The wrapper adds no network implementation, downloads or updates; mpv and user scripts retain their existing capabilities.

The vector wire format stores `(X << 16) + Y`. Negative Y borrows from the high word, so the old decoder read `(-8,-4)` as `(-9,-4)`. The patch restores X in both readers and tests real signed-boundary roundtrips without changing the encoder, shaders, scene protections or GPU kernels.
