"""Open a local Nightdesk window, or start the engine and use the browser."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


def health(origin: str) -> bool:
    try:
        with urlopen(origin + "/api/health", timeout=1) as response:
            payload = json.load(response)
        return payload.get("app") == "nightdesk"
    except (OSError, URLError, ValueError, AttributeError):
        return False


def occupied(host: str, port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.5)
        return connection.connect_ex((host, port)) == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true", help="use your browser even if the Mac window is built")
    parser.add_argument("--no-open", action="store_true", help="start or check the engine without opening a window")
    parser.add_argument("--foreground", action="store_true", help="run the engine here; Ctrl+C stops it")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    # A source checkout may move after installation. Resolve its engine here,
    # rather than relying on an editable install's old absolute .pth location.
    source = repo / "engine"
    sys.path.insert(0, str(source))
    env = dict(os.environ, PYTHONPATH=str(source), NIGHTDESK_PROJECT_DIR=str(repo))
    try:
        from nightdesk.config import HOST, LOGS, PORT
    except (ImportError, OSError, ValueError) as exc:
        print(f"Nightdesk is not installed correctly: {exc}\nRun Install.command first.", file=sys.stderr)
        return 1
    if not 1 <= PORT <= 65535:
        print("NIGHTDESK_PORT must be between 1 and 65535.", file=sys.stderr)
        return 1
    origin = f"http://{HOST}:{PORT}"
    running = health(origin)
    if not running and occupied(HOST, PORT):
        print(f"Another application is using port {PORT}. Close it or set NIGHTDESK_PORT to a free port and retry.", file=sys.stderr)
        return 1
    if args.foreground:
        if running:
            print(f"Nightdesk is already running: {origin}")
            return 0
        return subprocess.call([sys.executable, "-u", "-m", "nightdesk"], cwd=repo, env=env)

    native = repo / "Nightdesk.app" / "Contents" / "MacOS" / "Nightdesk"
    if platform.system() == "Darwin" and native.is_file() and not (repo / ".native-disabled").exists() and not args.browser and not args.no_open:
        return subprocess.call([str(native)], cwd=repo, env=env)

    process = None
    if not running:
        LOGS.mkdir(parents=True, exist_ok=True)
        log = LOGS / "engine.log"
        with log.open("ab") as output:
            process = subprocess.Popen([sys.executable, "-u", "-m", "nightdesk"], cwd=repo, env=env,
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if health(origin):
                break
            if process.poll() is not None:
                print(f"Nightdesk did not start. Read {log}, then run Install.command again.", file=sys.stderr)
                return 1
            time.sleep(0.2)
        else:
            process.terminate()
            print(f"Nightdesk did not answer within 30 seconds. Read {log} for the reported error.", file=sys.stderr)
            return 1
    print(f"Nightdesk is ready at {origin}. Use Settings to set up your own data and model.")
    if not args.no_open and not webbrowser.open(origin):
        print("Open the address above in your browser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
