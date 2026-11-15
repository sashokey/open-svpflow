import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


def write_video(path):
    width, height = 320, 192
    with path.open("wb") as stream:
        stream.write(b"YUV4MPEG2 W320 H192 F24:1 Ip A1:1 C420jpeg\n")
        for index in range(16):
            plane = bytearray([32]) * (width * height)
            for y in range(height):
                start = y * width + 64 + index * 4
                plane[start:start + 48] = bytes([180]) * 48
            stream.write(b"FRAME\n")
            stream.write(plane)
            stream.write(bytes([128]) * (width * height // 2))


def build_command(package, movie):
    return [
        sys.executable, str(package / "player" / "launch.py"),
        "--profile", "smooth", "--fps", "48",
        "--mpv-option=--vo=null", "--mpv-option=--audio=no",
        "--mpv-option=--frames=12", "--mpv-option=--untimed",
        "--mpv-option=--terminal=yes",
        "--mpv-option=--term-playing-msg=OSVP_SMOKE=${video-out-params/w}x${video-out-params/h}",
        "--", str(movie),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="open-svpflow-smoke-") as directory:
        movie = Path(directory) / "synthetic motion.y4m"
        write_video(movie)
        process = subprocess.Popen(build_command(args.package, movie), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            output, _ = process.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            output, _ = process.communicate(timeout=5)
            print(output)
            raise SystemExit("External mpv interpolation smoke timed out")
        print(output)
        if process.returncode != 0 or "OSVP_SMOKE=2560x1440" not in output or "interpolation target 48 FPS" not in output:
            raise SystemExit("External mpv interpolation smoke failed")
    print("PASS: external mpv ran the packaged graph and plugins with QHD filtered output; this is not a presentation or throughput benchmark")


if __name__ == "__main__":
    main()
