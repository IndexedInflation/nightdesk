"""Provider setup never touches a shared cache or issues a network request."""
import importlib
import sys
import types
from concurrent.futures import ThreadPoolExecutor

from nightdesk import providers


def test_importing_provider_helper_does_not_import_yahoo_or_request_network(monkeypatch):
    calls = []
    monkeypatch.setattr(importlib, "import_module", lambda name: calls.append(name))
    importlib.reload(providers)
    assert calls == []


def test_yahoo_cache_is_local_and_configured_once_without_fetching(monkeypatch, tmp_path):
    locations = []
    def forbidden(*args, **kwargs):
        raise AssertionError("Cache configuration made a network request")
    fake = types.SimpleNamespace(set_tz_cache_location=locations.append, download=forbidden,
                                 Ticker=forbidden, Search=forbidden)
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    with ThreadPoolExecutor(max_workers=4) as pool:
        modules = list(pool.map(lambda _: providers.yahoo(tmp_path), range(8)))
    assert all(module is fake for module in modules)
    assert locations == [str(tmp_path / "yahoo")]
    assert (tmp_path / "yahoo").is_dir()
    assert providers.yahoo(tmp_path).download is forbidden


def test_yahoo_uses_current_user_cache_by_default(monkeypatch, tmp_path):
    from nightdesk import config
    locations = []
    fake = types.SimpleNamespace(set_tz_cache_location=locations.append)
    monkeypatch.setattr(config, "CACHE", tmp_path)
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    assert providers.yahoo() is fake
    assert locations == [str(tmp_path / "yahoo")]
