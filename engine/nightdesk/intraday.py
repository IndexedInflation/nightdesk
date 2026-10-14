"""A side experiment: would a FAST version (trading every 30 minutes) make money?

Nightdesk holds stocks for weeks. The request was for "the smallest latency possible", so
this tests the obvious alternative honestly: same kind of model, but on 5-minute price bars,
predicting the next 30 minutes, graded on days it never saw and charged a realistic cost.

    .venv/bin/python -m nightdesk.intraday

Yahoo only keeps 60 days of 5-minute bars, so this is a small test and is labelled as one.
"""
from __future__ import annotations

from .providers import yahoo

import json
import time
import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd

from . import universe
from .config import CACHE, RESEARCH

BARS = 78                  # 5-minute bars in a normal session (9:30 to 16:00 New York)
HORIZON = 6                # predict 6 bars ahead = 30 minutes
FIELDS = ["open", "high", "low", "close", "volume"]


def download(symbols: list[str], log=print) -> dict[str, pd.DataFrame]:
    yf = yahoo(CACHE)
    parts: dict[str, list] = {f: [] for f in FIELDS}
    for i in range(0, len(symbols), 50):
        chunk = symbols[i:i + 50]
        for attempt in range(3):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    df = yf.download(chunk, period="60d", interval="5m", group_by="column", auto_adjust=True, progress=False, threads=True, prepost=False)
                if len(df):
                    break
            except Exception as exc:
                log(f"  batch {i // 50 + 1} retry ({type(exc).__name__})")
            time.sleep(4 * (attempt + 1))
        else:
            continue
        for f in FIELDS:
            parts[f].append(df[f.capitalize()])
        log(f"  batch {i // 50 + 1}/{(len(symbols) - 1) // 50 + 1}: {df.shape[0]} bars")
        time.sleep(1.0)
    out = {}
    for f in FIELDS:
        wide = pd.concat(parts[f], axis=1)
        wide.index = pd.DatetimeIndex(wide.index).tz_convert("America/New_York")
        out[f] = wide.sort_index()
        out[f].to_parquet(CACHE / f"intraday5m_{f}.parquet")
    return out


def load_or_download(symbols: list[str], log=print) -> dict[str, pd.DataFrame]:
    paths = [CACHE / f"intraday5m_{f}.parquet" for f in FIELDS]
    if all(p.exists() and time.time() - p.stat().st_mtime < 86400 for p in paths):
        return {f: pd.read_parquet(p) for f, p in zip(FIELDS, paths)}
    return download(symbols, log)


def to_cube(panel: dict[str, pd.DataFrame]) -> tuple[dict[str, np.ndarray], list, list[str]]:
    """Reshape to arrays of [day, bar, stock], keeping only full 78-bar days."""
    idx = panel["close"].index
    days = [d for d, n in pd.Series(1, index=idx).groupby(idx.date).sum().items() if n == BARS]
    keep = np.isin(idx.date, days)
    cols = list(panel["close"].columns[panel["close"].loc[keep].notna().mean() > 0.97])     # stocks that traded nearly every bar
    cube = {}
    for f in FIELDS:
        arr = panel[f].loc[keep, cols].to_numpy(dtype="float64").reshape(len(days), BARS, len(cols))
        cube[f] = arr
    for f in ("open", "high", "low", "close"):                                           # fill the rare missing bar from the previous one
        a = cube[f]
        for b in range(1, BARS):
            a[:, b] = np.where(np.isnan(a[:, b]), a[:, b - 1], a[:, b])
    cube["volume"] = np.nan_to_num(cube["volume"])
    return cube, days, cols


def _rank(x: np.ndarray) -> np.ndarray:
    """Rank across stocks at each moment, scaled to -0.5..0.5 (same idea as the daily model)."""
    order = np.argsort(np.argsort(np.where(np.isnan(x), np.inf, x), axis=-1), axis=-1).astype("float64")
    n = (~np.isnan(x)).sum(axis=-1, keepdims=True)
    out = (order + 0.5) / np.maximum(n, 1) - 0.5
    return np.where(np.isnan(x), 0.0, out)


def features(cube: dict[str, np.ndarray]) -> tuple[np.ndarray, list[str], np.ndarray]:
    c, o, h, l, v = cube["close"], cube["open"], cube["high"], cube["low"], cube["volume"]
    D, B, S = c.shape
    nan = np.full((D, B, S), np.nan)
    def lag_ret(k):
        out = nan.copy()
        out[:, k:] = c[:, k:] / c[:, :-k] - 1
        return out
    f = {"ret_5m": lag_ret(1), "ret_15m": lag_ret(3), "ret_30m": lag_ret(6), "ret_60m": lag_ret(12)}
    f["ret_5m"][:, 0] = c[:, 0] / o[:, 0] - 1
    f["since_open"] = c / o[:, :1] - 1
    gap = nan.copy(); gap[1:] = (o[1:, :1] / c[:-1, -1:] - 1) * np.ones((1, B, 1))
    f["overnight_gap"] = gap
    yday = nan.copy(); yday[2:] = (c[1:-1, -1:] / c[:-2, -1:] - 1) * np.ones((1, B, 1))
    f["yesterday"] = yday
    typical = (h + l + c) / 3
    vwap = np.cumsum(typical * v, axis=1) / np.maximum(np.cumsum(v, axis=1), 1)
    f["vs_vwap"] = c / vwap - 1
    hi, lo = np.maximum.accumulate(h, axis=1), np.minimum.accumulate(l, axis=1)
    f["place_in_range"] = (c - lo) / np.where(hi - lo > 0, hi - lo, np.nan)
    cumv = np.cumsum(v, axis=1)
    rel, crel = nan.copy(), nan.copy()
    for d in range(10, D):
        rel[d] = v[d] / np.maximum(v[d - 10:d].mean(axis=0), 1)
        crel[d] = cumv[d] / np.maximum(cumv[d - 10:d].mean(axis=0), 1)
    f["bar_volume_vs_normal"], f["day_volume_vs_normal"] = rel, crel
    r1 = f["ret_5m"]
    sw = np.lib.stride_tricks.sliding_window_view(r1, 6, axis=1)                          # last 30 minutes of 5-minute moves
    vol = nan.copy(); vol[:, 5:] = sw.std(axis=-1)
    f["jumpiness_30m"] = vol
    names = list(f)
    X = np.stack([_rank(f[n]) for n in names], axis=-1).astype(np.float32)
    tod = np.broadcast_to((np.arange(B) / (B - 1) - 0.5)[None, :, None, None], (D, B, S, 1)).astype(np.float32)
    fwd = nan.copy(); fwd[:, :-HORIZON] = c[:, HORIZON:] / c[:, :-HORIZON] - 1
    return np.concatenate([X, tod], axis=-1), names + ["time_of_day"], fwd


def run(log=print) -> dict:
    symbols = universe.symbols()
    cube, days, cols = to_cube(load_or_download(symbols, log))
    X, names, fwd = features(cube)
    D, B, S = fwd.shape
    log(f"{D} full days, {S} stocks, {len(names)} measurements")
    y = _rank(fwd).astype(np.float32)
    usable = np.zeros((D, B, S), dtype=bool)
    usable[10:, 12:B - HORIZON] = True                                                   # 10 warm-up days; skip the first hour and the last half-hour
    usable &= ~np.isnan(fwd)
    split = 10 + int((D - 10) * 0.6)
    train = usable.copy(); train[split:] = False; train[:, 1::2] = False                 # every second bar is plenty for training
    test = np.zeros_like(usable); test[split + 1:, 12:B - HORIZON:HORIZON] = True        # one decision every 30 minutes, one day gap after training
    test &= usable
    log(f"training on {int(train.sum()):,} examples ({split - 10} days), testing on {int(test.sum()):,} ({D - split - 1} days)")
    params = dict(objective="regression", learning_rate=0.05, num_leaves=31, min_data_in_leaf=2000, feature_fraction=0.7,
                  bagging_fraction=0.5, bagging_freq=1, lambda_l2=10, max_bin=63, num_threads=6, seed=7, verbose=-1)
    model = lgb.train(params, lgb.Dataset(X[train], label=y[train], feature_name=names), num_boost_round=300)
    pred = np.full((D, B, S), np.nan)
    pred[test] = model.predict(X[test], num_threads=6)
    ics, top, bottom, mean_all, daily = [], [], [], [], {}
    for d in range(split + 1, D):
        for b in range(12, B - HORIZON, HORIZON):
            m = test[d, b]
            if m.sum() < 100:
                continue
            p, r = pred[d, b][m], fwd[d, b][m]
            ics.append(np.corrcoef(np.argsort(np.argsort(p)), np.argsort(np.argsort(r)))[0, 1])
            k = max(5, int(0.05 * m.sum()))
            order = np.argsort(p)
            top.append(r[order[-k:]].mean() - r.mean()); bottom.append(r[order[:k]].mean() - r.mean()); mean_all.append(r.mean())
            daily.setdefault(d, []).append(top[-1])
    day_means = np.array([np.mean(vs) for vs in daily.values()])
    edge = float(np.mean(top) * 1e4)
    t_stat = float(day_means.mean() / (day_means.std(ddof=1) / np.sqrt(len(day_means)))) if len(day_means) > 2 else None
    gain = model.feature_importance(importance_type="gain"); gain = gain / gain.sum()
    out = {"days_total": D, "days_tested": int(D - split - 1), "stocks": S, "decisions": len(top), "train_rows": int(train.sum()),
           "ic_mean": float(np.mean(ics)), "ic_positive": float(np.mean(np.array(ics) > 0)),
           "top_edge_bps": edge, "bottom_edge_bps": float(np.mean(bottom) * 1e4), "edge_t_stat": t_stat,
           "trades_per_day": 11, "cost_cheap_bps": 3.0, "cost_realistic_bps": 6.0,
           "net_cheap_bps": edge - 3.0, "net_realistic_bps": edge - 6.0,
           "net_cheap_per_year": (edge - 3.0) / 1e4 * 11 * 252, "net_realistic_per_year": (edge - 6.0) / 1e4 * 11 * 252,
           "importance": sorted(zip(names, [float(g) for g in gain]), key=lambda kv: -kv[1])[:6],
           "first_day": str(days[0]), "last_day": str(days[-1])}
    (RESEARCH / "intraday.json").write_text(json.dumps(out))
    return out


if __name__ == "__main__":
    res = run(lambda m: print(m, flush=True))
    print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in res.items()}, indent=1))
