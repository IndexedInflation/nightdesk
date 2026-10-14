"""Explicit first-run downloads and local training, executed by the Setup screen.

No weights or account files are fetched. The forecaster is learned locally from public
price data using the existing fixed recipe; this does not tune or grade held-out years.
"""
from __future__ import annotations

from . import data, pit, universe
from .config import MODELS
from .model import Ensemble


def progress(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    try:
        progress("Downloading the public stock list from Wikipedia.")
        symbols = universe.symbols()
        if data.have_cache():
            progress("Updating your locally cached Yahoo Finance price history.")
            data.update_history(symbols, log=progress)
        else:
            progress("Downloading public Yahoo Finance price history. Provider limits may make this take a while.")
            data.build_history(symbols, log=progress)
        valid_model = False
        if (MODELS / "production.json").exists():
            try:
                Ensemble.load(MODELS / "production.json")
                valid_model = True
            except (ValueError, KeyError, TypeError):
                progress("The local model is incomplete or incompatible. Training a replacement.")
        if not valid_model:
            progress("Preparing historical index membership and training your forecaster. This may take an hour or more.")
            pit.fit_production(log=progress)
        progress("Local forecaster and price history are ready.")
        return 0
    except Exception as exc:
        # Provider errors and missing local dependencies do not dump paths into API status.
        progress(f"Setup stopped ({type(exc).__name__}). Check connectivity, dependencies and available memory; retry Setup.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
