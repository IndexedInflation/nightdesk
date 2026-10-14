"""Signals ("features"): ~65 measurements of each stock, each day, built from price and volume.

Every number for day t uses only information up to the close of day t. Nothing here
looks forward, except `forward_returns`, which is the answer sheet the model is graded
against (and is blank for the most recent days, where the future has not happened yet).

Each signal is then ranked across all stocks on the same day (best = +0.5, worst = -0.5),
so the model learns "which stocks look better than the others today", not price levels.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import EXTRA_SYMBOLS

LIVE_WINDOW = 420            # trading days the live engine needs to rebuild today's signals
NON_TRADABLE = set(EXTRA_SYMBOLS)


def tradable_columns(panel: dict[str, pd.DataFrame]) -> list[str]:
    return [c for c in panel["close"].columns if c not in NON_TRADABLE]


def _market_return(panel: dict[str, pd.DataFrame], stock_returns: pd.DataFrame) -> pd.Series:
    if "SPY" in panel["adj"].columns:
        return panel["adj"]["SPY"].pct_change(fill_method=None)
    return stock_returns.mean(axis=1)


def _broadcast(series: pd.Series, like: pd.DataFrame) -> pd.DataFrame:
    arr = np.repeat(series.to_numpy()[:, None], like.shape[1], axis=1)
    return pd.DataFrame(arr, index=like.index, columns=like.columns).where(like.notna())


def _rsi(r: pd.DataFrame, n: int) -> pd.DataFrame:
    up = r.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    down = (-r.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / down.replace(0, np.nan))


def _group_mean(wide: pd.DataFrame, groups: dict[str, list[int]]) -> pd.DataFrame:
    """Average across the stocks in each group, repeated for every member of the group."""
    arr = wide.to_numpy()
    mean = np.full_like(arr, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for members in groups.values():
            mean[:, members] = np.nanmean(arr[:, members], axis=1)[:, None]
    return pd.DataFrame(mean, index=wide.index, columns=wide.columns)


def compute_features(panel: dict[str, pd.DataFrame], sectors: dict[str, str] | None = None,
                     industries: dict[str, str] | None = None, events: tuple | None = None,
                     event_families: tuple | None = None) -> dict[str, pd.DataFrame]:
    """Raw (un-ranked) signals. Returns {name: days x stocks}.
    Passing `industries` (a finer grouping than sectors) adds an experimental set of extra price signals.
    Passing `events` = (earnings table, analyst table) adds the earnings and analyst signals."""
    cols = tradable_columns(panel)
    adj = panel["adj"][cols]
    close = panel["close"][cols]
    volume = panel["volume"][cols]
    factor = adj / close                                   # converts raw prices to dividend-adjusted ones
    op, hi, lo = panel["open"][cols] * factor, panel["high"][cols] * factor, panel["low"][cols] * factor
    r = adj.pct_change(fill_method=None)
    mkt = _broadcast(_market_return(panel, r), r)
    f: dict[str, pd.DataFrame] = {}

    # --- momentum and reversal: how far the price moved over different look-backs
    for n in (1, 2, 3, 5, 10, 21, 63, 126, 252):
        f[f"r{n}"] = adj / adj.shift(n) - 1
    f["mom_12_1"] = adj.shift(21) / adj.shift(252) - 1     # the classic "12 months, skipping the last one"
    f["mom_6_1"] = adj.shift(21) / adj.shift(126) - 1
    f["mom_3m_1w"] = adj.shift(5) / adj.shift(63) - 1
    f["seas_12m"] = adj.shift(231) / adj.shift(252) - 1    # what it did this same month a year ago
    overnight = op / adj.shift(1) - 1
    intraday = adj / op - 1
    f["on_1"], f["id_1"] = overnight, intraday
    for n in (5, 21):
        f[f"on_{n}"] = overnight.rolling(n).sum()
        f[f"id_{n}"] = intraday.rolling(n).sum()

    # --- risk: how violently the price moves
    v5, v21, v63 = r.rolling(5).std(), r.rolling(21).std(), r.rolling(63).std()
    f["vol_5"], f["vol_21"], f["vol_63"] = v5, v21, v63
    f["vol_ratio"] = v5 / v63
    f["downvol_21"] = np.sqrt((r.clip(upper=0) ** 2).rolling(21).mean())
    day_range = (hi - lo) / adj
    f["range_5"], f["range_21"] = day_range.rolling(5).mean(), day_range.rolling(21).mean()
    prev = adj.shift(1)
    true_range = np.maximum(np.maximum(hi - lo, (hi - prev).abs()), (lo - prev).abs())
    f["atr_14"] = true_range.rolling(14).mean() / adj
    f["max_21"], f["min_21"] = r.rolling(21).max(), r.rolling(21).min()
    f["skew_63"] = r.rolling(63).skew()

    # --- link to the overall market
    def _cov(a, b, w):
        return (a * b).rolling(w).mean() - a.rolling(w).mean() * b.rolling(w).mean()

    beta = _cov(r, mkt, 126) / _cov(mkt, mkt, 126)
    f["beta_126"] = beta
    resid = r - beta * mkt                                  # the part of the move the market does not explain
    f["ivol_63"] = resid.rolling(63).std()
    f["resid_5"], f["resid_21"] = resid.rolling(5).sum(), resid.rolling(21).sum()
    f["corr_63"] = _cov(r, mkt, 63) / np.sqrt(_cov(r, r, 63) * _cov(mkt, mkt, 63))

    # --- where the price sits relative to its own recent past
    hi252, lo252 = adj.rolling(252).max(), adj.rolling(252).min()
    hi21, lo21 = adj.rolling(21).max(), adj.rolling(21).min()
    f["hi_252"], f["lo_252"] = adj / hi252 - 1, adj / lo252 - 1
    f["hi_21"] = adj / hi21 - 1
    f["pos_21"] = (adj - lo21) / (hi21 - lo21).replace(0, np.nan)
    clv = ((adj - lo) / (hi - lo).replace(0, np.nan)).clip(0, 1)
    f["clv_1"], f["clv_5"] = clv, clv.rolling(5).mean()
    for n in (5, 10, 21, 50, 200):
        f[f"ma_{n}"] = adj / adj.rolling(n).mean() - 1
    f["ma_50_200"] = adj.rolling(50).mean() / adj.rolling(200).mean() - 1
    f["rsi_2"], f["rsi_14"] = _rsi(r, 2), _rsi(r, 14)
    f["bb_21"] = (adj - adj.rolling(21).mean()) / adj.rolling(21).std().replace(0, np.nan)

    # --- trading activity
    dollars = close * volume
    vol21, vol63 = volume.rolling(21).mean(), volume.rolling(63).mean()
    relvol = volume / vol21.shift(1).replace(0, np.nan)
    f["relvol_1"] = relvol
    f["relvol_5"] = volume.rolling(5).mean() / vol63.replace(0, np.nan)
    f["vol_trend"] = vol21 / volume.rolling(126).mean().replace(0, np.nan)
    f["dvol_63"] = np.log1p(dollars.rolling(63).median())
    f["amihud_21"] = (r.abs() / dollars.replace(0, np.nan)).rolling(21).mean() * 1e9
    up_volume = volume.where(r > 0, 0.0).where(r.notna())
    f["upvol_21"] = up_volume.rolling(21).sum() / volume.rolling(21).sum().replace(0, np.nan)
    f["pv_corr_21"] = r.rolling(21).corr(relvol)
    f["shock"] = r * relvol                                  # a big move on big volume

    # --- versus companies in the same industry group
    if sectors:
        groups: dict[str, list[int]] = {}
        for j, c in enumerate(cols):
            groups.setdefault(sectors.get(c, "Other"), []).append(j)
        for n in (5, 21, 63, 126):
            sec = _group_mean(f[f"r{n}"], groups).where(adj.notna())
            if n in (5, 21, 63):
                f[f"sec_rel_{n}"] = f[f"r{n}"] - sec
            if n in (21, 126):
                f[f"sec_mom_{n}"] = sec

    # --- experimental extras (only when asked for; not part of the live model unless they prove themselves)
    if industries:
        f["resid_126"] = resid.rolling(126).sum()                                     # six months of market-independent moves
        past = resid.shift(21)
        f["resid_mom"] = past.rolling(231).sum() / (past.rolling(231).std() * np.sqrt(231)).replace(0, np.nan)   # steady-ness of its own 12-month run
        f["r504"], f["r756"] = adj / adj.shift(504) - 1, adj / adj.shift(756) - 1     # two- and three-year moves (long-term reversal)
        fine: dict[str, list[int]] = {}
        for j, c in enumerate(cols):
            fine.setdefault(industries.get(c, "Other"), []).append(j)
        coarse: dict[str, list[int]] = {}
        for j, c in enumerate(cols):
            coarse.setdefault((sectors or {}).get(c, "Other"), []).append(j)
        small = [k for k, members in fine.items() if len(members) < 4]                # tiny industries fall back to their sector
        for k in small:
            for j in fine.pop(k):
                fine.setdefault("sector:" + (sectors or {}).get(cols[j], "Other"), []).append(j)
        for n in (21, 126):
            ind = _group_mean(f[f"r{n}"], fine).where(adj.notna())
            f[f"ind_mom_{n}"] = ind
            f[f"ind_rel_{n}"] = f[f"r{n}"] - ind
        f["ind_vol_trend"] = _group_mean(f["vol_trend"], fine).where(adj.notna())     # is the whole industry getting busier?

    # --- "something happened" days: a move three times bigger than normal on heavy volume
    z = r / v63.shift(1).replace(0, np.nan)
    event = ((z.abs() > 3) & (relvol > 1.5)).to_numpy()
    rows = np.arange(len(adj), dtype="float64")[:, None]
    last = pd.DataFrame(np.where(event, rows, np.nan), index=adj.index, columns=cols).ffill()
    f["jump_age"] = (rows - last).clip(upper=63).fillna(63).where(adj.notna())
    f["jump_ret"] = r.where(event).ffill(limit=63).fillna(0).where(adj.notna())

    if events is not None:
        from .events import event_features
        ev = event_features(adj.index, cols, events[0], events[1], returns=r)
        if event_families is not None:                      # keep only the families asked for: "earn" and/or "analyst"
            ev = {k: v for k, v in ev.items() if ("earn" in event_families and k.startswith(("earn_", "eps_")))
                  or ("analyst" in event_families and k.startswith("an_"))}
        f.update(ev)

    return {k: v.replace([np.inf, -np.inf], np.nan).astype("float32") for k, v in f.items()}


def universe_mask(panel: dict[str, pd.DataFrame], min_history: int = 252, min_price: float = 5.0,
                  min_dollar_volume: float = 5e6) -> pd.DataFrame:
    """True where a stock is allowed that day: a year of history, not a penny stock, liquid."""
    cols = tradable_columns(panel)
    close, volume = panel["close"][cols], panel["volume"][cols]
    seasoned = close.notna().cumsum() >= min_history
    liquid = (close * volume).rolling(63, min_periods=21).median() >= min_dollar_volume
    return seasoned & (close >= min_price) & liquid & close.notna()


def rank_normalize(wide: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """Rank across stocks on each day: lowest about -0.5, highest about +0.5, centred on 0."""
    x = wide.where(mask)
    rank = x.rank(axis=1, method="average")
    count = x.notna().sum(axis=1).replace(0, np.nan)
    return (rank.sub(0.5)).div(count, axis=0) - 0.5


def forward_returns(panel: dict[str, pd.DataFrame], horizon: int) -> pd.DataFrame:
    """THE ANSWER SHEET: return over the next `horizon` trading days. Blank for the latest days."""
    adj = panel["adj"][tradable_columns(panel)]
    return adj.shift(-horizon) / adj - 1


@dataclass
class Dataset:
    X: np.ndarray                    # rows x signals, ranked, float32
    y: dict[int, np.ndarray]         # horizon -> ranked future return (the training target)
    fwd: dict[int, np.ndarray]       # horizon -> raw future return (for grading)
    dates: np.ndarray                # day of each row
    symbols: np.ndarray              # stock of each row
    feature_names: list[str]

    def frame(self, values: np.ndarray) -> pd.DataFrame:
        """Turn one value per row back into a days x stocks table."""
        return pd.Series(values, index=pd.MultiIndex.from_arrays([self.dates, self.symbols])).unstack()


def build_dataset(panel: dict[str, pd.DataFrame], sectors: dict[str, str] | None,
                  horizons: tuple[int, ...] = (5,), min_dollar_volume: float = 5e6,
                  start: str | None = None, risk_adjusted: bool = False, industries: dict[str, str] | None = None,
                  membership: pd.DataFrame | None = None, events: tuple | None = None,
                  event_families: tuple | None = None) -> Dataset:
    """Everything the model trains on, as flat arrays (one row per stock per day).
    risk_adjusted=True also builds `y_risk`: the same targets after dividing each stock's
    future return by its own recent jumpiness (so a calm stock's 2% counts for more)."""
    mask = universe_mask(panel, min_dollar_volume=min_dollar_volume)
    if membership is not None:                             # only days the company was really in the index
        mask = mask & membership.reindex(index=mask.index, columns=mask.columns, fill_value=False)
    if start:
        mask.loc[mask.index < pd.Timestamp(start)] = False
    raw = compute_features(panel, sectors, industries, events, event_families)
    vol63 = raw["vol_63"].astype("float64").replace(0, np.nan) if risk_adjusted else None
    names = list(raw)
    m = mask.to_numpy()
    day_idx, sym_idx = np.nonzero(m)
    X = np.zeros((len(day_idx), len(names)), dtype=np.float32)
    for j, name in enumerate(names):
        ranked = rank_normalize(raw.pop(name), mask).to_numpy(dtype=np.float32)
        col = ranked[day_idx, sym_idx]
        X[:, j] = np.where(np.isnan(col), 0.0, col)          # unknown -> "average"
    y, fwd, y_risk, y_sector, y_both = {}, {}, {}, {}, {}
    groups: dict[str, list[int]] = {}
    for j, c in enumerate(mask.columns):
        groups.setdefault((sectors or {}).get(c, "Other"), []).append(j)
    for h in horizons:
        fr = forward_returns(panel, h)
        fwd[h] = fr.to_numpy(dtype=np.float32)[day_idx, sym_idx]
        y[h] = rank_normalize(fr, mask).to_numpy(dtype=np.float32)[day_idx, sym_idx]
        if risk_adjusted:                                 # two alternative answer sheets, for experiments
            y_risk[h] = rank_normalize(fr / vol63, mask).to_numpy(dtype=np.float32)[day_idx, sym_idx]
            against = fr - _group_mean(fr.where(mask), groups)       # how it did against its own industry
            y_sector[h] = rank_normalize(against, mask).to_numpy(dtype=np.float32)[day_idx, sym_idx]
            y_both[h] = rank_normalize(against / vol63, mask).to_numpy(dtype=np.float32)[day_idx, sym_idx]   # both ideas at once
    ds = Dataset(X=X, y=y, fwd=fwd, dates=mask.index.to_numpy()[day_idx],
                 symbols=mask.columns.to_numpy()[sym_idx].astype(object), feature_names=names)
    ds.y_risk, ds.y_sector, ds.y_both = y_risk, y_sector, y_both
    return ds


def recent_matrix(panel: dict[str, pd.DataFrame], sectors: dict[str, str] | None,
                  min_dollar_volume: float = 5e6, n_days: int = 1, events: tuple | None = None,
                  event_families: tuple | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Ranked signals for the most recent `n_days` (rows = eligible stock-days).

    Works from the last LIVE_WINDOW days only, which gives the same answer as the full
    history because no signal looks back further than a year.
    Returns (day of each row, stock of each row, signal table, signal names).
    """
    tail = {f: df.iloc[-LIVE_WINDOW:] for f, df in panel.items()}
    mask = universe_mask(tail, min_dollar_volume=min_dollar_volume).iloc[-n_days:]
    raw = compute_features(tail, sectors, None, events, event_families)
    day_idx, sym_idx = np.nonzero(mask.to_numpy())
    X = np.zeros((len(day_idx), len(raw)), dtype=np.float32)
    for j, wide in enumerate(raw.values()):
        col = rank_normalize(wide.iloc[-n_days:], mask).to_numpy(dtype=np.float32)[day_idx, sym_idx]
        X[:, j] = np.where(np.isnan(col), 0.0, col)
    return mask.index.to_numpy()[day_idx], mask.columns.to_numpy()[sym_idx].astype(object), X, list(raw)


def latest_matrix(panel: dict[str, pd.DataFrame], sectors: dict[str, str] | None,
                  min_dollar_volume: float = 5e6, events: tuple | None = None,
                  event_families: tuple | None = None) -> tuple[list[str], np.ndarray, list[str]]:
    """Ranked signals for the most recent day only (what the live engine feeds the model)."""
    _, syms, X, names = recent_matrix(panel, sectors, min_dollar_volume, n_days=1, events=events, event_families=event_families)
    return list(syms), X, names
