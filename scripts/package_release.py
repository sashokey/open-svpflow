import argparse
from pathlib import Path
import re
import zipfile

PAYLOAD = (
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "README.md",
    "player/launch.py",
    "player/graph.vpy",
    "player/settings.json",
    "player/policy.py",
)
PLUGINS = ("libsvpflow1_vs.dylib", "libsvpflow2_vs.dylib")


def package(source, library, output, version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("Invalid release version")
    files = [(Path(source) / name, name) for name in PAYLOAD]
    files += [(Path(library) / name, "lib/" + name) for name in PLUGINS]
    for path, name in files:
        if path.is_symlink():
            raise ValueError("Symlinked package input: " + name)
        if not path.is_file():
            raise FileNotFoundError("Missing package input: " + name)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / ("open-svpflow-" + version + "-arm64.zip")
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in files:
            member = zipfile.ZipInfo("open-svpflow/" + name)
            member.create_system = 3
            member.external_attr = 0o100644 << 16
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, path.read_bytes())
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--library", type=Path, default=Path("target/release"))
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(package(args.source, args.library, args.output, args.version))


if __name__ == "__main__":
    main()

