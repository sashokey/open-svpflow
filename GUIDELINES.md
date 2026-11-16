# Guidelines

## Purpose

Open SVPflow brings motion-compensated frame interpolation to everyday movie playback on Apple Silicon. This fork builds on [upstream open-svpflow 0.1.3](https://github.com/Z1xus/open-svpflow/tree/badc0de2282d2300303f5b9461919a2bc4dbcf65) and adds a macOS launcher for the user's installed mpv. The goal is to make interpolation easy to configure while preserving normal mpv playback, scaling, subtitles, scripts and user settings.

## What the fork adds

- A fix for decoding negative vertical motion vectors in both readers of the shared vector format. The engine still uses the upstream vector representation, shaders and GPU kernels.
- A Python launcher and VapourSynth graph that validate settings, prepare input frames, preserve source dimensions and aspect ratio, and apply interpolation at the requested rate.
- A SwiftUI launcher with file drop, playlist management, localized settings and a compact playback-quality view. An independent mpv session keeps playing when the launcher quits.
- A GitHub Actions build that tests the engine and launcher, exercises the extracted application with external mpv, and checks the published ARM64 ZIP. The ZIP contains one ad-hoc signed app bundle with the launcher and plugins.

## Decisions and ideas explored

- **Reuse the engine.** A separate interpolation engine was considered early. The upstream engine was already viable on Apple Silicon, so the fork concentrated on the verified vector fix and the playback integration.
- **Keep playback dependencies external.** Bundling the entire player stack was considered for easier installation. The app bundles the launcher and plugins and checks for Homebrew mpv, Python and VapourSynth. This lets users keep their installed player and its configuration.
- **Build the GUI on the existing launcher.** The CLI established playback and settings behavior. SwiftUI made those controls accessible without hand-editing JSON and uses the same settings contract as the CLI.
- **Use the playback status to inform decisions.** The interface shows recent playback quality and suggests a lower target rate or lighter settings when suitable. Users choose the settings, and interpolation changes apply starting with the next file.

## Guidelines for future changes

- **Keep the fork focused.** Establish a reproducible issue before changing the engine. Put playback policy and compatibility handling in the launcher or graph when the issue originates there.
- **Keep settings consistent.** Derive interface controls and defaults from the Python settings contract. Preserve existing settings-file semantics and apply interpolation changes at the next file boundary.
- **Explain compatibility precisely.** Use the SVPflow parameter documentation as a reference. Describe the preset and supported settings in terms of what this implementation does, and identify options it cannot reproduce.
- **Preserve the playback boundary.** Use local Unix-domain IPC for session control. Keep the launcher free of internet requests, telemetry and automatic updates. Dependency installation starts at the user's request, and external mpv retains its own capabilities.
- **Verify the behavior users see.** Measure interpolation throughput separately from decoding and display. For window changes, check the native app on an isolated runner, including restored window sizes and scrolling in long settings panes. Publish packages after the automated build and extracted-app checks pass.

See the [README](README.md) for installation, usage and compatibility details.
