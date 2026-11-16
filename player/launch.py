import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from policy import build_command, read_settings, validate_settings
from dependencies import plugin_directory


def runtime_environment(vsscript=None, environment=None, config_root=None):
    result = dict(os.environ if environment is None else environment)
    if config_root is not None:
        Path(config_root).mkdir(parents=True, exist_ok=True)
        original = Path(result.get("XDG_CONFIG_HOME", ""))
        if not original.is_absolute():
            original = Path(result.get("HOME", str(Path.home()))) / ".config"
        mpv_config = original / "mpv"
        if mpv_config.is_dir():
            (Path(config_root) / "mpv").symlink_to(mpv_config, target_is_directory=True)
        result["XDG_CONFIG_HOME"] = str(config_root)
    if vsscript is not None:
        path = Path(vsscript).expanduser().resolve()
        if not path.is_file():
            raise ValueError("The external VSScript library is unavailable")
        result["VSSCRIPT_PATH"] = str(path)
    if result.get("VSSCRIPT_PATH") and config_root is None:
        return result
    requested = result.get("VSSCRIPT_PATH")
    interpreters = [sys.executable]
    pipe = shutil.which("vspipe", path=result.get("PATH"))
    if pipe:
        try:
            with Path(pipe).open("rb") as stream:
                header = stream.readline(4097).decode("utf-8")
            parts = shlex.split(header[2:]) if header.startswith("#!") else []
            if len(parts) == 2 and parts[0] == "/usr/bin/env":
                parts = [shutil.which(parts[1], path=result.get("PATH"))]
            if len(parts) == 1 and parts[0] and Path(parts[0]).is_absolute() and Path(parts[0]).name.lower().startswith("python"):
                interpreters.append(parts[0])
        except (OSError, UnicodeError, ValueError):
            pass
    query = "import pathlib,vapoursynth; p=pathlib.Path(vapoursynth.__file__).resolve().parent; print(next((str(p/n) for n in ('libvsscript.dylib','libvapoursynth-script.dylib') if (p/n).is_file()),''))"
    for executable in dict.fromkeys(interpreters):
        try:
            found = subprocess.run([executable, "-I", "-B", "-c", query], env=result, capture_output=True, text=True, timeout=5)
            candidate = found.stdout.strip()
            if found.returncode == 0 and candidate and len(candidate) <= 4096 and Path(candidate).is_file():
                if requested and Path(requested).resolve() != Path(candidate).resolve():
                    continue
                result["VSSCRIPT_PATH"] = str(Path(candidate).resolve())
                if config_root is not None and (Path(candidate).parent / "__main__.py").is_file():
                    configured = subprocess.run([executable, "-I", "-B", "-m", "vapoursynth", "config"], env=result, capture_output=True, text=True, timeout=5)
                    config = Path(config_root) / "vapoursynth" / "vapoursynth.toml"
                    if configured.returncode != 0 or not config.is_file():
                        raise ValueError("Unable to configure installed VapourSynth in temporary storage")
                break
        except (OSError, subprocess.TimeoutExpired):
            continue
    return result


def main(arguments=None):
    parser = argparse.ArgumentParser(description="Interpolate media using installed mpv and VapourSynth.")
    parser.add_argument("--mpv", default="mpv")
    parser.add_argument("--plugin-dir", type=Path)
    parser.add_argument("--vsscript", type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--profile", choices=("auto", "smooth", "original"))
    parser.add_argument("--fps")
    parser.add_argument("--bits", choices=("8", "10", "16", "source"))
    parser.add_argument("--backend", choices=("gpu", "cpu"))
    parser.add_argument("--start", type=float)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--mpv-option", action="append", default=[])
    parser.add_argument("movie", nargs="+")
    options = parser.parse_args(arguments)
    try:
        root = Path(__file__).resolve().parents[1]
        plugins = options.plugin_dir
        if plugins is None:
            plugins = plugin_directory(root)
        profile, fps = options.profile or "auto", options.fps or "auto"
        if options.settings is None:
            settings = validate_settings({})
        else:
            try:
                settings = read_settings(options.settings)
            except ValueError:
                from settings_api import load_document, playback_settings, rate_argument
                document = load_document(options.settings)
                settings = playback_settings(document)
                profile = options.profile or document["playback"]["profile"]
                fps = options.fps or rate_argument(document)
        if options.bits is not None:
            settings["output_bits"] = "source" if options.bits == "source" else int(options.bits)
        if options.backend is not None:
            settings["backend"] = options.backend
        commands = [build_command(options.mpv, movie, root / "player" / "graph.vpy", plugins, settings, profile, fps, options.mpv_option, options.start, options.duration) for movie in options.movie]
        command = commands[0] + [value[-1] for value in commands[1:]]
        executable = shutil.which(command[0])
        if executable is None:
            raise ValueError("External mpv was not found; install it or pass --mpv")
        command[0] = executable
        compatibility = root / "player" / "compat.lua"
        if not compatibility.is_file():
            raise ValueError("The player compatibility script is unavailable")
        command.insert(1, "--script=" + str(compatibility))
        if profile == "original":
            return subprocess.run(command, env=dict(os.environ)).returncode
        with tempfile.TemporaryDirectory(prefix="open-svpflow-vs-") as config_root:
            environment = runtime_environment(options.vsscript, config_root=config_root)
            return subprocess.run(command, env=environment).returncode
    except (OSError, ValueError) as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
