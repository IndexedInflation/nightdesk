"""Lazy public-data clients with caches confined to the current user's app data.

Importing this helper does not import a provider or contact the internet. Calling yahoo()
only configures local cache paths; the caller must explicitly request data afterwards.
"""
from __future__ import annotations

import importlib
import threading
from pathlib import Path

_lock = threading.Lock()
_configured_module = None
_configured_location: str | None = None


def yahoo(cache: Path | None = None):
    """Return the installed yfinance module using this user's isolated provider cache.

    yfinance's public setter redirects timezone, cookie and ISIN databases together.
    Configure once per module/location: changing an already-open cache repeatedly would
    close databases while another download thread is using them.
    """
    global _configured_module, _configured_location
    if cache is None:
        from .config import CACHE
        cache = CACHE
    location = str(Path(cache).expanduser().resolve() / "yahoo")
    module = importlib.import_module("yfinance")
    with _lock:
        if module is not _configured_module or location != _configured_location:
            Path(location).mkdir(parents=True, exist_ok=True)
            module.set_tz_cache_location(location)
            _configured_module, _configured_location = module, location
    return module
