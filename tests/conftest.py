"""Shared test helpers: a small made-up market so tests never touch the internet."""
import sys
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Configure isolation before importing engine modules; never read a real user's account.
_test_home = tempfile.TemporaryDirectory(prefix="nightdesk-tests-")
os.environ["NIGHTDESK_HOME"] = _test_home.name

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))


def make_panel(n_days: int = 700, n_symbols: int = 30, seed: int = 7, late_starters: int = 4) -> dict:
    """Random-walk prices shaped like the real cache: {field: days x symbols}."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2019-01-01", periods=n_days)
    names = [f"S{i:02d}" for i in range(n_symbols)] + ["SPY"]
    n = len(names)
    market = rng.normal(0.0004, 0.009, n_days)
    rets = market[:, None] * rng.uniform(0.6, 1.4, n) + rng.normal(0, 0.014, (n_days, n))
    rets[:, -1] = market                                         # SPY is the market itself
    close = 50 * np.exp(np.cumsum(rets, axis=0)) * rng.uniform(0.5, 4, n)
    overnight = rng.normal(0, 0.004, (n_days, n))
    prev = np.vstack([close[:1], close[:-1]])
    open_ = prev * (1 + overnight)
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, (n_days, n))))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, (n_days, n))))
    volume = rng.lognormal(14, 0.5, (n_days, n)).round()
    adj = close * np.linspace(0.9, 1.0, n_days)[:, None]          # pretend dividends were paid along the way
    panel = {}
    for f, arr in dict(open=open_, high=high, low=low, close=close, adj=adj, volume=volume).items():
        df = pd.DataFrame(arr, index=days, columns=names)
        for j in range(late_starters):                            # some companies listed later
            df.iloc[: 150 + 40 * j, j] = np.nan
        panel[f] = df
    return panel


@pytest.fixture(scope="session")
def panel():
    return make_panel()


@pytest.fixture(scope="session")
def sectors(panel):
    names = [c for c in panel["close"].columns if c != "SPY"]
    return {s: ["Tech", "Banks", "Health"][i % 3] for i, s in enumerate(names)}
