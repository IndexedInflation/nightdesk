"""Check the installed software without connecting to a data provider."""
from __future__ import annotations

import importlib
import platform
import sys


def main() -> int:
    if not (3, 11) <= sys.version_info[:2] < (3, 14):
        print("Nightdesk needs Python 3.11, 3.12, or 3.13.", file=sys.stderr)
        return 1
    failed = []
    for module in ("numpy", "pandas", "pandas_market_calendars", "pyarrow", "sklearn", "lightgbm", "fastapi", "uvicorn", "httpx", "websockets", "yfinance"):
        try:
            importlib.import_module(module)
        except (ImportError, OSError) as exc:
            failed.append((module, exc))
    if failed:
        for module, exc in failed:
            print(f"Cannot load {module}: {exc}", file=sys.stderr)
        if any(module == "lightgbm" for module, _ in failed):
            command = "brew install libomp" if platform.system() == "Darwin" else "sudo apt-get install libgomp1"
            print(f"LightGBM needs the OpenMP system library. Install it with {command}, then retry.", file=sys.stderr)
        print("Run Install.command again after fixing the reported problem.", file=sys.stderr)
        return 1
    print("Nightdesk's Python dependencies are ready. No market data or model was downloaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
