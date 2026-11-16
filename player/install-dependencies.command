#!/bin/bash
set -euo pipefail
export HOMEBREW_NO_AUTO_UPDATE=1
export HOMEBREW_NO_ANALYTICS=1
export HOMEBREW_NO_INSTALL_CLEANUP=1
brew_bin=/opt/homebrew/bin/brew
if [[ ! -x "$brew_bin" ]]; then
    brew_bin="$(command -v brew)"
fi
"$brew_bin" install mpv vapoursynth
brew_prefix="$("$brew_bin" --prefix)"
if ! "$brew_prefix/bin/mpv" --no-config --load-scripts=no --list-properties | /usr/bin/awk '$1 == "dropped-files" { found=1 } END { exit !found }'; then
    "$brew_bin" upgrade mpv
fi
printf '\nPlayback component preparation finished. Return to Open SVPflow and choose Retry.\n'
