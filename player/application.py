import argparse
import json
from pathlib import Path
import sys

import session


def request_input():
    text = sys.stdin.read(session.MAX_MESSAGE + 1)
    if len(text.encode("utf-8")) > session.MAX_MESSAGE:
        raise ValueError("The application request exceeds its size limit")
    value = json.loads(text) if text.strip() else {}
    if type(value) is not dict:
        raise ValueError("The application request must be an object")
    return value


def fields(value, allowed):
    if not set(value) <= set(allowed):
        raise ValueError("Unknown application request fields")


def dispatch(command, value, state_dir, bundle_root):
    if command == "open":
        fields(value, ("files", "action"))
        return session.open_files(state_dir, bundle_root, value.get("files", []), value.get("action", "auto"))
    if command == "status":
        fields(value, ())
        return session.status(state_dir)
    if command == "check":
        from dependencies import inspect_environment
        fields(value, ())
        return dict(inspect_environment(bundle_root), ok=True)
    if command == "install-script":
        from dependencies import prepare_install_script
        fields(value, ("kind",))
        if value.get("kind") not in ("homebrew", "dependencies"):
            raise ValueError("Choose the Homebrew or dependency installation")
        return {"ok": True, "path": str(prepare_install_script(value["kind"], state_dir))}
    from settings_api import describe_settings, load_document, save_document
    if command == "describe":
        fields(value, ())
        return {"ok": True, "description": describe_settings()}
    if command == "settings-get":
        fields(value, ())
        return {"ok": True, "document": load_document(state_dir / "settings.json")}
    if command == "settings-save":
        fields(value, ("document",))
        if "document" not in value:
            raise ValueError("A settings document is required")
        return {"ok": True, "document": save_document(state_dir / "settings.json", value["document"])}
    raise ValueError("Unsupported application request")


def main(arguments=None):
    parser = argparse.ArgumentParser(description="Open SVPflow application backend")
    parser.add_argument("command", choices=("describe", "settings-get", "settings-save", "check", "install-script", "open", "status", "serve"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    options = parser.parse_args(arguments)
    if options.command == "serve":
        session.serve(options.state_dir, options.bundle_root)
        return 0
    try:
        state_dir = session.private_directory(options.state_dir)
        result = dispatch(options.command, request_input(), state_dir, options.bundle_root.resolve())
    except (OSError, ValueError, RuntimeError, TimeoutError, ImportError) as error:
        result = {"ok": False, "status": "error", "message": str(error)[:4096]}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
