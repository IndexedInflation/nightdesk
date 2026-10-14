"""Price history: download it once from Yahoo Finance, keep it on disk, top it up daily.

Layout on disk: one table per field (open, high, low, close, adj, volume), rows are
trading days and columns are stocks. "adj" is the close corrected for splits and
dividends, so a return computed from it is what an investor really earned.
"""
from __future__ import annotations

from .providers import yahoo

import json
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import mktcal
from .config import CACHE, EXTRA_SYMBOLS, HISTORY_START

FIELDS = ["open", "high", "low", "close", "adj", "volume"]
_YF_NAMES = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "adj": "Adj Close", "volume": "Volume"}


def _path(field: str) -> Path:
    return CACHE / f"daily_{field}.parquet"


def _download(symbols: list[str], start: str, batch: int = 60, pause: float = 1.0, log=print) -> dict[str, pd.DataFrame]:
    """Fetch daily bars in batches. Returns {field: wide DataFrame}."""
    yf = yahoo(CACHE)

    parts: dict[str, list[pd.DataFrame]] = {f: [] for f in FIELDS}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        df = None
        for attempt in range(4):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    df = yf.download(chunk, start=start, interval="1d", group_by="column",
                                     auto_adjust=False, progress=False, threads=True)
                if df is not None and len(df):
                    break
            except Exception as exc:  # network hiccup or rate limit: wait and retry
                log(f"  batch {i // batch + 1}: {type(exc).__name__}, retry {attempt + 1}")
            time.sleep(3 * (attempt + 1))
        if df is None or not len(df):
            log(f"  batch {i // batch + 1}: no data for {chunk[:3]}...")
            continue
        if not isinstance(df.columns, pd.MultiIndex):            # single symbol comes back flat
            df.columns = pd.MultiIndex.from_product([df.columns, chunk])
        for f in FIELDS:
            if _YF_NAMES[f] in df.columns.get_level_values(0):
                parts[f].append(df[_YF_NAMES[f]])
        log(f"  batch {i // batch + 1}/{(len(symbols) - 1) // batch + 1}: {len(chunk)} symbols, {len(df)} days")
        time.sleep(pause)
    out = {}
    for f in FIELDS:
        if parts[f]:
            wide = pd.concat(parts[f], axis=1)
            wide = wide.loc[:, ~wide.columns.duplicated()]
            wide.index = pd.DatetimeIndex(wide.index).tz_localize(None).normalize()
            out[f] = wide.sort_index().astype("float64")
    return out


def _clean(panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Drop empty columns and obviously broken prints (zero or negative prices)."""
    close = panel["close"]
    keep = close.columns[close.notna().sum() > 0]
    out = {}
    for f, df in panel.items():
        df = df.reindex(columns=keep)
        if f != "volume":
            df = df.where(df > 0)
        out[f] = df
    return out


def save_panel(panel: dict[str, pd.DataFrame], now: float | None = None) -> None:
    for f, df in panel.items():
        tmp = Path(str(_path(f)) + ".tmp")
        df.to_parquet(tmp)
        tmp.replace(_path(f))
    (CACHE / "daily_meta.json").write_text(json.dumps({"saved_at": now or time.time()}))


def cache_saved_at() -> float | None:
    """When the price tables were last written. A day's prices are only trusted if they were saved
    after that day's closing bell: a download made mid-session holds half a day, not a close."""
    try:
        return float(json.loads((CACHE / "daily_meta.json").read_text())["saved_at"])
    except (OSError, ValueError, KeyError):
        pass
    try:
        return _path("close").stat().st_mtime           # files from before the stamp existed
    except OSError:
        return None


def load_panel() -> dict[str, pd.DataFrame]:
    """The cached price tables. Raises FileNotFoundError before the first download."""
    return {f: pd.read_parquet(_path(f)) for f in FIELDS}


def have_cache() -> bool:
    return all(_path(f).exists() for f in FIELDS)


def build_history(symbols: list[str], start: str = HISTORY_START, log=print) -> dict[str, pd.DataFrame]:
    """First-time full download (about 500 stocks x 20 years)."""
    wanted = sorted(set(symbols) | set(EXTRA_SYMBOLS))
    log(f"Downloading daily history for {len(wanted)} symbols since {start}")
    panel = _clean(_download(wanted, start, log=log))
    save_panel(panel)
    log(f"Saved {panel['close'].shape[0]} days x {panel['close'].shape[1]} symbols")
    return panel


class IncompleteHistory(RuntimeError):
    """An update came back with too many stocks missing to be trusted (usually Yahoo rate-limiting)."""


def update_history(symbols: list[str], log=print, now: datetime | None = None, retries: int = 2, pause: float = 20.0) -> dict[str, pd.DataFrame]:
    """Top up the cache with the latest days.

    - Days saved before their closing bell (half days) are thrown out first, never kept.
    - Re-fetches the last 10 days so late corrections (splits, dividends) are picked up; if the adjusted
      prices of the overlap changed, the whole history of that stock is downloaded again.
    - Stocks that fail to come back get more tries. If the newest day still has too many holes, nothing
      is saved and IncompleteHistory is raised, so the caller can try again later."""
    if not have_cache():
        return build_history(symbols, log=log)
    panel = load_panel()
    saved = cache_saved_at()
    if saved is not None:
        trusted = pd.Timestamp(mktcal.last_completed_session(datetime.fromtimestamp(saved, mktcal.NY)))
        panel = {f: df.loc[:trusted] for f, df in panel.items()}
    wanted = sorted(set(symbols) | set(EXTRA_SYMBOLS))
    missing = [s for s in wanted if s not in panel["close"].columns]
    last = panel["close"].index[-1]
    start = (last - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    have = [s for s in wanted if s not in missing]
    fresh = _clean(_download(have, start, log=log)) if have else {}
    alive = [s for s in have if panel["close"][s].iloc[-5:].notna().any()]       # traded in the last week, so should have new prices

    def lacking() -> list[str]:
        if not fresh:
            return alive
        newest = fresh["close"].iloc[-1].reindex(alive)
        return sorted(newest.index[newest.isna()])

    for attempt in range(retries):
        todo = lacking()
        if not todo:
            break
        log(f"  {len(todo)} stocks did not come back; trying them again")
        time.sleep(pause * (attempt + 1))
        again = _clean(_download(todo, start, batch=20, pause=2.0, log=log))
        for f in FIELDS:
            if f in again:
                fresh[f] = again[f] if f not in fresh else fresh[f].reindex(index=fresh[f].index.union(again[f].index)).combine_first(again[f])
    if fresh and fresh["close"].index[-1] > last:
        got = 1 - len(lacking()) / max(len(alive), 1)
        if got < 0.97 or ("SPY" in alive and "SPY" in lacking()):
            raise IncompleteHistory(f"only {got:.0%} of stocks came back for {fresh['close'].index[-1].date()}")
    restate: list[str] = []
    if fresh:
        overlap = fresh["adj"].index.intersection(panel["adj"].index)[:-1]   # skip the newest shared day
        if len(overlap):
            old = panel["adj"].reindex(index=overlap, columns=fresh["adj"].columns)
            new = fresh["adj"].reindex(index=overlap)
            drift = ((new / old) - 1).abs().max()
            restate = sorted(drift[drift > 0.002].index)
        for f in FIELDS:
            add = fresh[f]
            combined = panel[f].reindex(index=panel[f].index.union(add.index))
            combined.loc[add.index, add.columns] = add
            panel[f] = combined
    redo = sorted(set(missing) | set(restate))
    if redo:
        log(f"Re-downloading full history for {len(redo)} symbols (new or restated)")
        full = _clean(_download(redo, HISTORY_START, log=log))
        for f in FIELDS:
            if f not in full:
                continue
            idx = panel[f].index.union(full[f].index)
            panel[f] = panel[f].reindex(index=idx)
            for s in full[f].columns:
                panel[f][s] = full[f][s].reindex(idx)
    panel = {f: df.sort_index() for f, df in panel.items()}
    save_panel(panel, now=now.timestamp() if now else None)
    return panel


def intraday_bars(symbols: list[str], interval: str = "1m", period: str = "1d", log=print) -> dict[str, pd.DataFrame]:
    """Recent intraday bars (used at start-up to learn today's open/high/low so far)."""
    yf = yahoo(CACHE)

    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = yf.download(chunk, period=period, interval=interval, group_by="ticker",
                                 auto_adjust=False, progress=False, threads=True, prepost=False)
        except Exception as exc:
            log(f"intraday batch failed: {type(exc).__name__}")
            continue
        if df is None or not len(df):
            continue
        for s in chunk:
            try:
                sub = df[s].dropna(subset=["Close"]) if isinstance(df.columns, pd.MultiIndex) else df.dropna(subset=["Close"])
            except KeyError:
                continue
            if len(sub):
                out[s] = sub.rename(columns=str.lower)
        time.sleep(0.5)
    return out


def trading_days(panel: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """Days the US market was open, taken from the benchmark's own history."""
    return panel["close"]["SPY"].dropna().index


def returns(adj: pd.DataFrame) -> pd.DataFrame:
    return adj.pct_change(fill_method=None)


def log_dollar_volume(panel: dict[str, pd.DataFrame], window: int = 63) -> pd.DataFrame:
    dv = panel["close"] * panel["volume"]
    return np.log1p(dv.rolling(window, min_periods=window // 2).median())
