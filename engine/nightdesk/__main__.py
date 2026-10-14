"""Start the Nightdesk engine:  .venv/bin/python -m nightdesk

Only one engine may run at a time (the second one sees the port is taken and exits).
"""
from __future__ import annotations

import socket
import sys
import json
import urllib.request

import uvicorn

from .config import HOST, PORT


def already_running() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, PORT)) == 0


def main() -> None:
    if already_running():
        try:
            with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/health", timeout=2) as response:
                health = json.load(response)
            if health.get("app") == "nightdesk":
                print(f"Nightdesk engine is already running on {HOST}:{PORT}.")
                return
        except (OSError, ValueError):
            pass
        print(f"Port {PORT} is occupied by another service. Set NIGHTDESK_PORT to an unused port.", file=sys.stderr)
        sys.exit(1)
    uvicorn.run("nightdesk.api:app", host=HOST, port=PORT, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
