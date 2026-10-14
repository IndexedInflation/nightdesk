"""Company events: earnings announcements and analyst actions, turned into signals.

Price history alone has been picked over by every fund on earth. These add information
that isn't in the price chart:
  - earnings: when a company reported, and how far it beat or missed what analysts expected
  - analysts: upgrades, downgrades and price-target changes

Both come free from Yahoo Finance with the date AND time of each event, which matters:
an event only counts from the first closing bell after it became public.

    .venv/bin/python -m nightdesk.events          (download or refresh the event files)
"""
from __future__ import annotations

from .providers import yahoo

import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .config import CACHE

EARNINGS_FILE = CACHE / "events_earnings.parquet"
ANALYST_FILE = CACHE / "events_analyst.parquet"
CARRY = 80               # trading days an earnings fact stays "current" (a little over a quarter)
FRESH = 10               # trading days an earnings surprise counts as fresh news
MONTH, QUARTER, YEAR = 21, 63, 252


# ----------------------------------------------------------------------------- downloading
def _fetch(symbol: str) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    yf = yahoo(CACHE)
    earn = act = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ticker = yf.Ticker(symbol)
        for attempt in range(2):
            try:
                e = ticker.get_earnings_dates(limit=100)
                if e is not None and len(e):
                    e = e.rename(columns={"EPS Estimate": "estimate", "Reported EPS": "reported", "Surprise(%)": "surprise"})
                    e = e.reset_index().rename(columns={e.index.name or "Earnings Date": "ts"})
                    e["ts"] = pd.to_datetime(e["ts"], utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
                    earn = e[["ts", "estimate", "reported", "surprise"]].assign(symbol=symbol)
                break
            except Exception:
                time.sleep(2.0)
        for attempt in range(2):
            try:
                a = ticker.upgrades_downgrades
                if a is not None and len(a):
                    a = a.reset_index().rename(columns={"GradeDate": "ts", "Firm": "firm", "Action": "action",
                                                        "currentPriceTarget": "target", "priorPriceTarget": "prior"})
                    a["ts"] = pd.to_datetime(a["ts"]).dt.tz_localize(None)
                    for col in ("target", "prior"):
                        if col not in a:
                            a[col] = np.nan
                    act = a[["ts", "firm", "action", "target", "prior"]].assign(symbol=symbol)
                break
            except Exception:
                time.sleep(2.0)
    return earn, act


def download(symbols: list[str], workers: int = 4, log=print) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch both event histories for every symbol and save them (replaces the old files)."""
    earns, acts, done = [], [], 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for earn, act in pool.map(_fetch, symbols):
            done += 1
            if earn is not None:
                earns.append(earn)
            if act is not None:
                acts.append(act)
            if done % 50 == 0:
                log(f"  {done}/{len(symbols)} companies ({time.time() - t0:.0f}s): {len(earns)} with earnings, {len(acts)} with analyst history")
    earnings = pd.concat(earns, ignore_index=True) if earns else pd.DataFrame(columns=["ts", "estimate", "reported", "surprise", "symbol"])
    analyst = pd.concat(acts, ignore_index=True) if acts else pd.DataFrame(columns=["ts", "firm", "action", "target", "prior", "symbol"])
    for df, path in ((earnings, EARNINGS_FILE), (analyst, ANALYST_FILE)):
        tmp = Path(str(path) + ".tmp")
        df.to_parquet(tmp)
        tmp.replace(path)
    log(f"saved {len(earnings):,} earnings reports for {earnings['symbol'].nunique()} companies and {len(analyst):,} analyst actions for {analyst['symbol'].nunique()}")
    return earnings, analyst


def load() -> tuple[pd.DataFrame, pd.DataFrame] | None:
    if not (EARNINGS_FILE.exists() and ANALYST_FILE.exists()):
        return None
    return pd.read_parquet(EARNINGS_FILE), pd.read_parquet(ANALYST_FILE)


# ----------------------------------------------------------------------------- timing
def known_position(ts: pd.Series, days: pd.DatetimeIndex) -> np.ndarray:
    """For each event time (New York clock), the row of `days` at whose CLOSE the event is first known.

    Before 4 pm on a trading day -> that day. At or after 4 pm, on a weekend/holiday, or with no time
    of day recorded (midnight) -> the next trading day. Events from before the table starts get a
    negative position (counted in weekdays) so "how long ago" stays right; events after it ends get len(days).
    """
    ts = pd.to_datetime(ts)
    date = ts.dt.normalize()
    late = (ts.dt.hour >= 16) | ((ts.dt.hour == 0) & (ts.dt.minute == 0))
    need = np.where(late, date + pd.Timedelta(days=1), date)                  # first calendar day whose close comes after the event
    need = pd.DatetimeIndex(need)
    pos = np.searchsorted(days.values, need.values, side="left").astype("int64")
    early = need < days[0]
    if early.any():
        pos[early] = -np.busday_count(need[early].values.astype("datetime64[D]"), days[0].to_datetime64().astype("datetime64[D]"))
    return pos


def _carry(values: np.ndarray, pos: np.ndarray, n_days: int, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """Spread point events along the day axis: the latest event's value, and how many days ago it was.
    `values`/`pos` must be sorted by pos. Returns (value on each day, age on each day); NaN outside `limit`."""
    out = np.full(n_days, np.nan)
    age = np.full(n_days, np.nan)
    if not len(pos):
        return out, age
    day = np.arange(n_days)
    last = np.searchsorted(pos, day, side="right") - 1                         # index of the most recent event known by each day
    ok = last >= 0
    gap = np.where(ok, day - pos[np.maximum(last, 0)], np.nan)
    ok &= gap <= limit
    out[ok] = values[last[ok]]
    age[ok] = gap[ok]
    return out, age


def _window_sum(amount: np.ndarray, pos: np.ndarray, n_days: int, window: int) -> np.ndarray:
    """Total of `amount` over events known in the last `window` trading days (including today)."""
    lo, hi = -window - 5, n_days
    keep = (pos > lo) & (pos < hi)
    bucket = np.zeros(n_days + window + 6)
    np.add.at(bucket, pos[keep] - lo, amount[keep])
    csum = np.cumsum(bucket)
    idx = np.arange(n_days) - lo
    return csum[idx] - csum[idx - window]


# ----------------------------------------------------------------------------- signals
def event_features(days: pd.DatetimeIndex, columns: list[str], earnings: pd.DataFrame, analyst: pd.DataFrame,
                   returns: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    """Event signals on the same days x stocks grid as the price signals.

    Every value on day t uses only events public before day t's closing bell.
    `returns` (daily, days x stocks) adds the price reaction to the latest earnings report.
    """
    n, col_pos = len(days), {c: j for j, c in enumerate(columns)}
    names = ["earn_age", "earn_surprise", "earn_fresh", "earn_beat_run", "eps_growth", "earn_react",
             "an_net_month", "an_net_quarter", "an_target_month", "an_target_quarter", "an_busy_month", "an_cover"]
    out = {k: np.full((n, len(columns)), np.nan) for k in names}

    e = earnings.dropna(subset=["reported"]).copy()
    if len(e):
        e["pos"] = known_position(e["ts"], days)
        e = e[e["pos"] < n].sort_values(["symbol", "pos"])
        for sym, g in e.groupby("symbol", sort=False):
            j = col_pos.get(sym)
            if j is None:
                continue
            pos = g["pos"].to_numpy()
            surprise = np.clip(g["surprise"].to_numpy(dtype="float64"), -200, 200)
            reported = g["reported"].to_numpy(dtype="float64")
            value, age = _carry(surprise, pos, n, CARRY)
            out["earn_age"][:, j] = age
            out["earn_surprise"][:, j] = value
            out["earn_fresh"][:, j] = np.where(age <= FRESH, value, np.nan)
            beats = pd.Series(np.sign(np.nan_to_num(surprise))).rolling(4, min_periods=2).mean().to_numpy()   # share of recent beats minus misses
            out["earn_beat_run"][:, j] = _carry(beats, pos, n, CARRY)[0]
            growth = np.full(len(g), np.nan)                                    # this quarter against the same quarter a year earlier
            if len(g) > 4:
                base = reported[:-4]
                growth[4:] = np.clip((reported[4:] - base) / np.maximum(np.abs(base), 0.10), -5, 5)
            out["eps_growth"][:, j] = _carry(growth, pos, n, CARRY)[0]
            if returns is not None and sym in returns.columns:
                r = returns[sym].to_numpy()
                inside = (pos >= 0) & (pos < n)
                react = np.full(len(g), np.nan)
                react[inside] = r[pos[inside]]                                  # the day the report first showed up in the closing price
                out["earn_react"][:, j] = _carry(react, pos, n, CARRY)[0]

    a = analyst.copy()
    if len(a):
        a["pos"] = known_position(a["ts"], days)
        a = a[a["pos"] < n].sort_values(["symbol", "pos"])
        a["step"] = (a["action"] == "up").astype("float64") - (a["action"] == "down").astype("float64")
        both = (a["target"] > 0) & (a["prior"] > 0)
        a["revision"] = np.where(both, np.clip(a["target"] / a["prior"].where(both, 1.0) - 1, -0.6, 0.6), 0.0)
        a["has_revision"] = both.astype("float64")
        for sym, g in a.groupby("symbol", sort=False):
            j = col_pos.get(sym)
            if j is None:
                continue
            pos = g["pos"].to_numpy()
            start = max(int(pos.min()), 0)                                      # no analyst record before this: leave blank, not zero
            live = np.arange(n) >= start
            for label, window in (("month", MONTH), ("quarter", QUARTER)):
                net = _window_sum(g["step"].to_numpy(), pos, n, window)
                rev = _window_sum(g["revision"].to_numpy(), pos, n, window)
                cnt = _window_sum(g["has_revision"].to_numpy(), pos, n, window)
                out[f"an_net_{label}"][live, j] = net[live]
                out[f"an_target_{label}"][live, j] = np.where(cnt > 0, rev / np.maximum(cnt, 1), 0.0)[live]
            out["an_busy_month"][live, j] = _window_sum(np.ones(len(g)), pos, n, MONTH)[live]
            out["an_cover"][live, j] = _window_sum(np.ones(len(g)), pos, n, YEAR)[live]      # how closely analysts follow it: notes in the last year
    return {k: pd.DataFrame(v, index=days, columns=columns).astype("float32") for k, v in out.items()}


if __name__ == "__main__":
    from . import features as F
    from . import pit
    panel, _, _ = pit.pit_panel(lambda m: None)
    download(F.tradable_columns(panel), log=lambda m: print(m, flush=True))
