Native macOS ARM64 interpolation plugins with a launcher for separately installed mpv. Based on [upstream open-svpflow 0.1.3](https://github.com/Z1xus/open-svpflow/tree/badc0de2282d2300303f5b9461919a2bc4dbcf65). The engine patch fixes signed motion-vector decoding; the other additions provide playback configuration, tests and packaging. Upstream already supports ARM64.

Playback requires an Apple Silicon Mac, Python 3.9+, VapourSynth API 4 and mpv with the VapourSynth filter. Local checks cover macOS 27.

## Installation

Follow the official instructions for [mpv](https://mpv.io/installation/), [Python](https://www.python.org/downloads/macos/) and [VapourSynth](https://www.vapoursynth.com/doc/installation.html). With an existing Homebrew installation:

```sh
brew install mpv vapoursynth
mpv --vf=help
```

The filter list must include `vapoursynth`. Homebrew supplies a matching Python environment for VapourSynth; the launcher discovers that environment.

Extract the ARM64 ZIP from [Releases](https://github.com/sashokey/open-svpflow/releases) and keep its directories together. The plugins are ad-hoc signed, not notarized. mpv, Python and VapourSynth are not bundled.

From the extracted directory, replace the movie path and run:

```sh
python3 player/launch.py --profile smooth --fps 120000/1001 --settings player/settings.json -- "/path/to/movie.mkv"
```

## Usage notes

The default `auto` profile uses mpv's reported display refresh; no compatible rate selects original-rate playback. `smooth` requires interpolation; `original` bypasses it. Automatic targets reach at most 120 FPS. `--fps` accepts an integer or fraction up to 240 FPS and at least twice the source rate, such as `60`, `120000/1001` or `240`.

Pass `--settings player/settings.json` to use edits to that file. Without it, built-in defaults apply. All JSON fields are required. `--mpv`, `--plugin-dir` and `--vsscript` override dependency locations; `--start` sets the start time and `--duration` limits a run to at most 300 seconds.

| Control / JSON field | Default | Values and meaning |
|---|---|---|
| Frames interpolation mode / `interpolation_mode` | `3` | `0–3`; engine scene/interpolation mode |
| SVP shader / `shader` | `23` | `21/22/23`; synthesis algorithm |
| Artifacts masking / `artifacts_masking` | `cover:80, area:0` | Cover `1–100`, area `0–100`; engine mask controls |
| Motion vectors precision / `motion_precision` | `2` | `1`: whole pixel; `2`: half pixel |
| Motion vectors grid / `motion_grid` | `block:16, overlap:2` | Block `8/16/32`; overlap `0/1/2/3`: none, 1/8, 1/4, 1/2 block |
| Decrease grid step / `decrease_grid_step` | `0` | `0/1`; enable one vector refinement stage |
| Search radius / `search_radius` | `fine:0, coarse:-8` | Each `−10…10`; engine distances, not literal pixel radii |
| Wide search / `wide_search` | `true` | `true/false`; wider search for bad vectors |
| Width of top coarse level / `coarse_width` | `1050` | `128–2560` pixels |
| Processing of scene changes / `scene_changes` | `nearest` | `nearest/blend`; scene-boundary fallback |
| Duplicate frames removal / `duplicate_frames_removal` | `false` | `true/false`; engine duplicate-frame handling |

Half-pixel precision requires `fine:0`. Refinement requires block16, overlap2 and `fine:0`. This adapter rejects duplicate removal with 10-bit synthesis. These are adapter constraints, not limits of every upstream API.

The graph accepts constant-format YUV420P8/P10 at source rates up to 60 FPS. It produces fixed 2560×1440 output with 8-bit analysis and preserved 8/10-bit synthesis. Non-16:9 sources are resized to 16:9; use `original` to bypass the graph. OpenCL rendering is requested. A target FPS does not guarantee sustainable speed. Normal mpv handles display conversion; headless checks do not validate HDR-to-SDR presentation or audio synchronization.

The wire format stores `(X << 16) + Y`. Negative Y borrows from the high word: the former decoder read `(-8,-4)` as `(-9,-4)`. The patch restores X in both metadata and motion-field readers, preserves the encoder and tests real roundtrips including signed boundaries. Scene protections, shaders and GPU kernels remain upstream. This correction does not establish universal perceptual improvement.

The launcher adds no network requests, downloads or update checks and does not change permanent player settings. External dependencies retain their existing capabilities. For algorithms and direct APIs, see [upstream](https://github.com/Z1xus/open-svpflow).
