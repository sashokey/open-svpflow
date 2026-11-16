import argparse
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile


def write_video(path, chroma="420jpeg", aspect="1:1", width=320, height=192, full_range=False):
    with path.open("wb") as stream:
        stream.write(("YUV4MPEG2 W" + str(width) + " H" + str(height) + " F24:1 Ip A" + aspect + " C" + chroma + (" XCOLORRANGE=FULL" if full_range else "") + "\n").encode("ascii"))
        for index in range(16):
            plane = bytearray([100 if full_range else 32]) * (width * height)
            for y in range(0 if full_range else height):
                start = y * width + 64 + index * 4
                plane[start:start + 48] = bytes([180]) * 48
            stream.write(b"FRAME\n")
            stream.write(plane)
            stream.write(bytes([128]) * (((width + 1) // 2) * height * 2 if chroma == "422" else ((width + 1) // 2) * ((height + 1) // 2) * 2))


def write_tagged_rgb(path):
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=red:s=320x192:r=24:d=1,format=rgb24,setparams=range=full:color_primaries=bt2020:color_trc=smpte2084:colorspace=gbr",
        "-c:v", "libx264rgb", "-crf", "0", "-preset", "ultrafast",
        "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "rgb", "-n", str(path),
    ], capture_output=True, text=True, timeout=20)
    if result.returncode != 0:
        raise SystemExit("Unable to create RGB integration fixture: " + result.stderr)


def build_command(package, movie, refresh=None):
    command = [
        sys.executable, str(package / "player" / "launch.py"),
    ]
    command.extend(["--fps", "48"] if refresh is None else ["--mpv-option=--vo-null-fps=" + str(refresh)])
    return command + [
        "--mpv-option=--no-config", "--mpv-option=--load-scripts=no",
        "--mpv-option=--vo=null", "--mpv-option=--audio=no",
        "--mpv-option=--frames=12", "--mpv-option=--untimed",
        "--mpv-option=--terminal=yes",
        "--mpv-option=--term-playing-msg=OSVP_SMOKE=${video-out-params/w}x${video-out-params/h};MATRIX=${video-out-params/colormatrix};RANGE=${video-out-params/colorlevels};PRIMARIES=${video-out-params/primaries};TRANSFER=${video-out-params/gamma};PAR=${video-out-params/par};FORMAT=${video-out-params/pixelformat};AUTOFIT=${options/autofit-larger}",
        "--", str(movie),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="open-svpflow-smoke-") as directory:
        movie = Path(directory) / "synthetic motion.y4m"
        remove_filter = Path(directory) / "remove-filter.lua"
        remove_filter.write_text('local mp = require "mp"\nmp.register_event("playback-restart", function()\n    mp.add_timeout(0.05, function() mp.commandv("vf", "remove", "@svp-interpolate") end)\nend)\n')
        for refresh, chroma, aspect, width, height in ((None, "420jpeg", "1:1", 320, 192), (240, "420jpeg", "1:1", 320, 192), (240, "422", "1:1", 320, 192), (240, "420jpeg", "16:15", 320, 192), (240, "rgb", "1:1", 320, 192), (48, "420jpeg", "1:1", 319, 191), (48, "config", "1:1", 319, 191), (48, "full", "1:1", 320, 192), (48, "window", "1:1", 320, 192), (0, "420jpeg", "1:1", 320, 192), (24, "420jpeg", "1:1", 320, 192), (48, "filter-loss", "1:1", 320, 192)):
            if chroma == "rgb":
                movie = Path(directory) / "tagged RGB.mkv"
                write_tagged_rgb(movie)
            else:
                write_video(movie, "420jpeg" if chroma in ("config", "full", "window", "filter-loss") else chroma, aspect, width + (chroma == "config"), height + (chroma == "config"), full_range=chroma == "full")
            target = 48 if refresh is None else refresh
            command = build_command(args.package, movie, refresh)
            if chroma == "window":
                command[-2:-2] = ["--mpv-option=--autofit-larger=75%x65%"]
            if chroma == "filter-loss":
                command[-2:-2] = ["--mpv-option=--untimed=no", f"--mpv-option=--script={remove_filter}"]
            if chroma in ("rgb", "full"):
                command[-2:-2] = ["--mpv-option=--frames=2", "--mpv-option=--vf-add=lavfi=[signalstats,metadata=mode=print]", "--mpv-option=--msg-level=ffmpeg=v"]
            if chroma == "full":
                command[-2:-2] = ["--bits", "16"]
            environment = dict(os.environ)
            if chroma == "config":
                xdg = Path(directory) / "user configuration"
                scripts = xdg / "mpv" / "scripts"
                scripts.mkdir(parents=True)
                (xdg / "mpv" / "mpv.conf").write_text("volume=37\nautofit-larger=80%x70%\nvf=lavfi=[crop=319:191:0:0:exact=1]\n")
                (scripts / "user.lua").write_text('mp.msg.info("OSVP_USER_SCRIPT=" .. mp.get_property("volume"))\n')
                environment["XDG_CONFIG_HOME"] = str(xdg)
                command.remove("--mpv-option=--no-config")
                command.remove("--mpv-option=--load-scripts=no")
            process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
            try:
                output, _ = process.communicate(timeout=10 if chroma == "filter-loss" else 90)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                output, _ = process.communicate(timeout=5)
                print(output)
                raise SystemExit("External mpv interpolation smoke timed out")
            print(output, flush=True)
            if chroma == "filter-loss":
                if process.returncode != 2 or "Open SVPflow: The interpolation filter failed or was disabled" not in output:
                    raise SystemExit("External mpv did not stop after the interpolation filter was removed")
                continue
            if refresh in (0, 24):
                reason = ("Display refresh rate is unavailable." if refresh == 0 else
                          "The target frame rate must be higher than the video's frame rate.")
                if process.returncode != 2 or "Open SVPflow: " + reason not in output or "interpolation target" in output:
                    raise SystemExit("External mpv did not stop with the expected interpolation error")
                continue
            if chroma == "config" and "OSVP_USER_SCRIPT=37" not in output:
                raise SystemExit("External mpv did not preserve user configuration and script loading")
            if process.returncode != 0 or "OSVP_SMOKE=" + str(width) + "x" + str(height) not in output or "interpolation target " + str(target) + " FPS" not in output:
                raise SystemExit("External mpv interpolation smoke failed at " + str(target) + " FPS")
            expected_format = "yuv420p16" if chroma == "full" else "yuv420p" if chroma in ("420jpeg", "window") and width % 2 == height % 2 == 0 else "yuv444p"
            if "FORMAT=" + expected_format + ";" not in output:
                raise SystemExit("External mpv unexpectedly changed chroma format: expected " + expected_format)
            expected_autofit = "80%x70%" if chroma == "config" else "75%x65%" if chroma == "window" else "100%x100%"
            if "AUTOFIT=" + expected_autofit not in output:
                raise SystemExit("External mpv window limit did not preserve the requested default or user override")
            if aspect == "16:15":
                par = re.search(r"PAR=([0-9.]+)", output)
                if par is None or abs(float(par.group(1)) - 16 / 15) > 0.0001:
                    raise SystemExit("External mpv changed anamorphic pixel aspect")
            if chroma == "full":
                value = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", output)
                if "RANGE=full" not in output or value is None or abs(float(value.group(1)) - 25700) > 1:
                    raise SystemExit("External mpv changed full-range samples during 8-to-16-bit conversion")
            if chroma == "rgb":
                if not all(marker in output for marker in ("MATRIX=bt.709", "RANGE=limited", "PRIMARIES=bt.2020", "TRANSFER=pq")):
                    raise SystemExit("External mpv lost converted RGB color metadata")
                for plane, expected in (("Y", 63), ("U", 102), ("V", 240)):
                    found = re.search(r"lavfi\.signalstats\." + plane + r"AVG=([0-9.]+)", output)
                    if found is None or abs(float(found.group(1)) - expected) > 2:
                        raise SystemExit("External mpv RGB pixels disagree with BT.709 limited metadata")
    print("PASS: external mpv preserved source geometry, odd input/user-filter geometry, user config/scripts, anamorphic aspect, RGB/PQ metadata and full-range depth conversion at explicit 48 FPS and automatic 240 FPS; this is not a presentation or throughput benchmark")


if __name__ == "__main__":
    main()
