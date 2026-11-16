import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import tempfile


PLUGIN_NAMES = ("libsvpflow1_vs.dylib", "libsvpflow2_vs.dylib")
PROBE = """import json,pathlib,platform,sys
import vapoursynth as vs
directory=pathlib.Path(vs.__file__).resolve().parent
library=next((str(directory/name) for name in ('libvsscript.dylib','libvapoursynth-script.dylib') if (directory/name).is_file()),'')
if not library:
    raise RuntimeError('The matching VSScript library is missing')
for name in ('libsvpflow1_vs.dylib','libsvpflow2_vs.dylib'):
    vs.core.std.LoadPlugin(path=str(pathlib.Path(sys.argv[1])/name))
print(json.dumps({'vsscript':library,'api':vs.__api_version__.api_major,'plugins':True,'machine':platform.machine()}))
"""


def find_homebrew():
    standard = Path("/opt/homebrew/bin/brew")
    if standard.is_file() and os.access(standard, os.X_OK):
        return standard
    candidate = shutil.which("brew")
    return Path(candidate) if candidate else None


def plugin_directory(bundle_root):
    root = Path(bundle_root)
    packaged = root.parent / "Frameworks"
    if root.name == "Resources" and packaged.is_dir():
        return packaged
    for candidate in (root / "lib", root / "target/release"):
        if all((candidate / name).is_file() for name in PLUGIN_NAMES):
            return candidate
    return root / "lib"


def is_arm64_binary(path):
    with Path(path).open("rb") as stream:
        header = stream.read(65536)
    if len(header) < 8:
        return False
    magic = header[:4]
    if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return struct.unpack_from("<I", header, 4)[0] == 0x0100000C
    if magic in (b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"):
        return struct.unpack_from(">I", header, 4)[0] == 0x0100000C
    if magic not in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        return False
    count = struct.unpack_from(">I", header, 4)[0]
    stride = 32 if magic[-1] == 0xBF else 20
    if not 0 < count <= (len(header) - 8) // stride:
        return False
    return any(struct.unpack_from(">I", header, 8 + index * stride)[0] == 0x0100000C for index in range(count))


def _environment(prefix=None):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.update(HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_ANALYTICS="1", HOMEBREW_NO_INSTALL_CLEANUP="1", PYTHONDONTWRITEBYTECODE="1")
    if prefix is not None:
        env["PATH"] = str(prefix / "bin") + ":" + str(prefix / "sbin") + ":/usr/bin:/bin:/usr/sbin:/sbin"
    return env


def _run(arguments, environment, timeout=10):
    result = subprocess.run([str(value) for value in arguments], env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-4096:]
        raise RuntimeError(detail or "Dependency check failed")
    return result.stdout.strip()


def _python(prefix):
    pipe = prefix / "bin/vspipe"
    if pipe.is_file():
        with pipe.open("rb") as stream:
            first = stream.readline(4097).decode("utf-8").strip()
        if first.startswith("#!/"):
            candidate = Path(first[2:])
            if candidate.name.startswith("python") and candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    for candidate in sorted((prefix / "opt").glob("python*/bin/python3*"), reverse=True):
        if candidate.is_file() and os.access(candidate, os.X_OK) and "-" not in candidate.name:
            return candidate
    candidate = prefix / "bin/python3"
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None


def inspect_environment(bundle_root):
    result = {"ready": False, "needs_homebrew": False, "needs_dependencies": False, "message": "", "components": [], "runtime": {}}
    brew = find_homebrew()
    if brew is None:
        result.update(needs_homebrew=True, message="Install Homebrew to prepare the external playback components.")
        return result
    result["runtime"]["brew"] = str(brew)
    try:
        prefix = Path(_run([brew, "--prefix"], _environment(), 5))
        if not prefix.is_absolute() or not prefix.is_dir():
            raise RuntimeError("Homebrew returned an invalid installation location")
        mpv = prefix / "bin/mpv"
        python = _python(prefix)
        if not mpv.is_file() or python is None:
            result.update(needs_dependencies=True, message="Install mpv and VapourSynth with their matching Python runtime.")
            return result
        if platform.machine() != "arm64" or not is_arm64_binary(mpv):
            result.update(needs_dependencies=True, message="Native Apple Silicon playback components are required.")
            return result
        environment = _environment(prefix)
        result["runtime"].update(mpv=str(mpv), python=str(python))
        properties = _run([mpv, "--no-config", "--load-scripts=no", "--list-properties"], environment)
        filters = _run([mpv, "--no-config", "--load-scripts=no", "--vf=help"], environment)
        if "dropped-files" not in properties.split() or "vapoursynth" not in filters or "lavfi" not in filters:
            result.update(needs_dependencies=True, message="mpv 0.42 or newer with VapourSynth, Lua and libavfilter support is required.")
            return result
        try:
            with tempfile.TemporaryDirectory(prefix="osvp-check-") as directory:
                script = Path(directory) / "lua-check.lua"
                script.write_text('mp.msg.info("OSVP_LUA_READY")\nmp.commandv("quit")\n')
                lua = _run([mpv, "--no-config", "--load-scripts=no", "--vo=null", "--ao=null", "--force-window=no", "--idle=yes", "--script=" + str(script)], environment, 5)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            result.update(needs_dependencies=True, message="mpv could not verify Lua playback hooks: " + str(error)[-2048:])
            return result
        if "OSVP_LUA_READY" not in lua:
            result.update(needs_dependencies=True, message="mpv could not execute the required Lua playback hooks.")
            return result
        plugins = plugin_directory(bundle_root)
        if not all((plugins / name).is_file() for name in PLUGIN_NAMES):
            raise RuntimeError("The application is missing its interpolation plugins. Download a complete release again.")
        probe = json.loads(_run([python, "-I", "-B", "-c", PROBE, plugins], environment, 15))
        if probe.get("api") != 4 or probe.get("machine") != "arm64" or not probe.get("plugins"):
            raise RuntimeError("The matching native VapourSynth API 4 environment could not load both plugins.")
        vsscript = Path(probe.get("vsscript", ""))
        if not vsscript.is_absolute() or not vsscript.is_file():
            raise RuntimeError("The matching VSScript library is unavailable.")
        result["runtime"]["vsscript"] = str(vsscript)
        result["components"] = [{"name": name, "status": "ready", "message": "Available"} for name in ("Homebrew", "mpv", "Python", "VapourSynth", "Interpolation plugins")]
        result.update(ready=True, message="Playback components are ready.")
    except subprocess.TimeoutExpired:
        result["message"] = "A playback component check timed out. Close any installation in progress and choose Retry."
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        result["message"] = str(error)[-4096:] or "Unable to verify the external playback components."
        if "No module named" in result["message"]:
            result["needs_dependencies"] = True
    return result


def prepare_install_script(kind, state_dir):
    if kind not in ("homebrew", "dependencies"):
        raise ValueError("Unknown installation action")
    source = Path(__file__).resolve().parent / ("install-" + kind + ".command")
    data = source.read_bytes()
    setup = Path(state_dir) / "setup"
    if setup.is_symlink():
        raise ValueError("The setup directory must not be a symbolic link")
    setup.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = setup / source.name
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o700)
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), 0o700)
        stream.write(data)
    return destination
