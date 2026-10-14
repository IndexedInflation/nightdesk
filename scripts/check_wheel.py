"""Check public wheel contents and launch its offline first-run workspace."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
from zipfile import ZipFile


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_wheel.py dist/nightdesk-*.whl", file=sys.stderr)
        return 2
    wheel = Path(sys.argv[1]).resolve()
    with ZipFile(wheel) as archive:
        files = set(archive.namelist())
    required = {"nightdesk/assets/ui/index.html", "nightdesk/assets/ui/app.js",
                "nightdesk/assets/research/model_card.json", "nightdesk/assets/How-Nightdesk-Works.html"}
    if not required <= files:
        raise RuntimeError(f"Wheel is missing public assets: {sorted(required - files)}")
    forbidden = ("/data/", "/models/", "/state/", "/logs/", ".venv/", "__pycache__/")
    if any(any(part in name for part in forbidden) for name in files):
        raise RuntimeError("Wheel contains a local runtime artifact")
    with tempfile.TemporaryDirectory(prefix="nightdesk-wheel-test-") as temporary:
        root = Path(temporary)
        installed = root / "installed package"
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(installed), str(wheel)], check=True)
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        env = dict(os.environ, PYTHONPATH=str(installed), NIGHTDESK_HOME=str(root / "new account"), NIGHTDESK_PORT=str(port))
        env.pop("NIGHTDESK_READER_HOME", None)
        env.pop("NIGHTDESK_READER_MODEL", None)
        env.pop("NIGHTDESK_READER_PYTHON", None)
        origin = f"http://127.0.0.1:{port}"
        with (root / "engine.log").open("wb") as output:
            process = subprocess.Popen([sys.executable, "-u", "-m", "nightdesk"], cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        with urlopen(origin + "/api/health", timeout=1) as response:
                            health = json.load(response)
                        break
                    except (OSError, URLError):
                        if process.poll() is not None:
                            raise RuntimeError((root / "engine.log").read_text())
                        time.sleep(0.2)
                else:
                    raise RuntimeError("Packaged engine did not start within 30 seconds")
                assert health["app"] == "nightdesk"
                with urlopen(origin + "/api/setup", timeout=2) as response:
                    setup = json.load(response)
                assert setup["phase"] == "waiting" and not setup["ready"]
                assert not setup["network_enabled"]
                with urlopen(origin + "/", timeout=2) as response:
                    assert b"Nightdesk" in response.read()
                with urlopen(origin + "/api/research", timeout=2) as response:
                    assert json.load(response)
                print("Wheel assets and offline first launch passed in a fresh user account.")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
