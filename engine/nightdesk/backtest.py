"""The scorekeeper: replay history day by day and count what the strategy would have made.

Timing rule (the one that matters): a score made at the close of day t can first earn
the return of day t+1. With execution="next_open" it is stricter still: the trade waits
for the next morning's opening price and misses the overnight move.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .portfolio import TRADING_DAYS, choose_holdings, exposure_scale, target_weights


@dataclass
class PortfolioParams:
    max_positions: int = 20
    hold_buffer: float = 2.5
    max_weight: float = 0.08
    max_sector_weight: float = 0.30
    cost_bps: float = 5.0                 # charged on every dollar bought or sold
    target_vol: float | None = 0.15
    trend_filter: bool = True
    execution: str = "close"              # "close" or "next_open"
    rebalance_band: float = 0.25          # leave a position alone unless it is 25% off its target size
    position_stop: float | None = None    # sell a holding that closes this far below its purchase price
    stop_cooldown: int = 5                # ...and leave it alone for this many trading days


@dataclass
class Result:
    equity: pd.Series
    returns: pd.Series
    turnover: pd.Series
    exposure: pd.Series
    n_holdings: pd.Series
    last_holdings: dict = field(default_factory=dict)


def simulate(scores: pd.DataFrame, panel: dict[str, pd.DataFrame], params: PortfolioParams,
             sectors: dict[str, str] | None = None) -> Result:
    """Run the strategy over `scores` (days x stocks; blank = not allowed that day)."""
    days, cols = scores.index, list(scores.columns)
    n, T = len(cols), len(days)
    adj_full = panel["adj"]
    adj = adj_full.reindex(index=days, columns=cols)
    R = np.nan_to_num(adj.pct_change(fill_method=None).to_numpy(), nan=0.0)
    if params.execution == "next_open":
        factor = adj_full / panel["close"]
        op = (panel["open"] * factor).reindex(index=days, columns=cols)
        R_on = np.nan_to_num((op / adj.shift(1) - 1).to_numpy(), nan=0.0)
        R_id = np.nan_to_num((adj / op - 1).to_numpy(), nan=0.0)
    below = np.zeros(T, dtype=bool)
    if params.trend_filter and "SPY" in adj_full.columns:
        spy = adj_full["SPY"].dropna()
        below = (spy < spy.rolling(200).mean()).reindex(days).fillna(False).to_numpy()
    S = scores.to_numpy(dtype="float64")
    col_pos = {c: j for j, c in enumerate(cols)}
    cost_rate = params.cost_bps / 1e4

    w = np.zeros(n)
    entry = np.full(n, np.nan)             # purchase price of each holding (for the stop-loss)
    cool = np.zeros(n, dtype=int)          # day index until which a stopped-out stock is off limits
    PX = adj.to_numpy()
    equity = 1.0
    eq = np.ones(T)
    turnover, expo, count = np.zeros(T), np.zeros(T), np.zeros(T)

    def drift(weights: np.ndarray, r: np.ndarray) -> tuple[np.ndarray, float]:
        pr = float(np.dot(weights, r))
        return (weights * (1 + r) / (1 + pr) if 1 + pr > 1e-12 else weights * 0), pr

    def decide(t: int) -> np.ndarray | None:
        s = S[t]
        valid = ~np.isnan(s)
        if not valid.any():
            return None
        today = pd.Series(s[valid], index=[cols[j] for j in np.nonzero(valid)[0]])
        if params.position_stop:
            hit = (w > 1e-9) & (PX[t] <= entry * (1 - params.position_stop))
            cool[hit] = t + params.stop_cooldown
        blocked = [cols[j] for j in np.nonzero(cool > t)[0]]
        held = [cols[j] for j in np.nonzero((w > 1e-9) & (cool <= t))[0]]
        picks = choose_holdings(today, held, params.max_positions, params.hold_buffer, sectors, params.max_sector_weight, blocked)
        if not picks:
            return np.zeros(n)
        idx = [col_pos[p] for p in picks]
        unit = np.array(list(target_weights(picks, 1.0, params.max_weight).values()))
        recent = np.dot(R[max(0, t - 62): t + 1][:, idx], unit) if t >= 10 else np.array([])   # np.dot: Apple's "@" raises false alarms
        scale = exposure_scale(recent, params.target_vol, bool(below[t]), params.trend_filter)
        target = np.zeros(n)
        target[idx] = np.array(list(target_weights(picks, scale, params.max_weight).values()))
        stay = (w > 1e-9) & (target > 0) & (np.abs(w - target) <= params.rebalance_band * target)
        target[stay] = w[stay]                          # close enough: do not pay to nudge it
        return target

    def trade_to(target: np.ndarray, t: int) -> None:
        nonlocal w, equity
        opened = (target > 1e-9) & (w <= 1e-9)
        entry[opened] = PX[t][opened]
        entry[target <= 1e-9] = np.nan
        traded = float(np.abs(target - w).sum())
        equity *= 1 - traded * cost_rate
        turnover[t] += traded
        w = target

    pending = None
    for t in range(T):
        if t > 0:
            if params.execution == "next_open":
                w, pr = drift(w, R_on[t]); equity *= 1 + pr        # old book rides the overnight move
                if pending is not None:
                    trade_to(pending, t)                           # new book goes on at the open
                w, pr = drift(w, R_id[t]); equity *= 1 + pr
            else:
                w, pr = drift(w, R[t]); equity *= 1 + pr
        target = decide(t)
        if params.execution == "next_open":
            pending = target
        elif target is not None:
            trade_to(target, t)
        eq[t] = equity
        expo[t], count[t] = w.sum(), (w > 1e-9).sum()

    equity_s = pd.Series(eq, index=days)
    returns = equity_s.pct_change().fillna(equity_s.iloc[0] - 1)
    last = {cols[j]: float(w[j]) for j in np.nonzero(w > 1e-9)[0]}
    return Result(equity=equity_s, returns=returns, turnover=pd.Series(turnover, index=days),
                  exposure=pd.Series(expo, index=days), n_holdings=pd.Series(count, index=days), last_holdings=last)


def metrics(returns: pd.Series, benchmark: pd.Series | None = None) -> dict:
    """Plain performance numbers from a series of daily returns."""
    r = returns.dropna()
    if len(r) < 2:
        return {}
    years = len(r) / TRADING_DAYS
    equity = (1 + r).cumprod()
    total = float(equity.iloc[-1] - 1)
    sd = float(r.std(ddof=1))
    down = float(np.sqrt((r.clip(upper=0) ** 2).mean()))
    peak = np.maximum(1.0, equity.cummax())
    max_dd = float((equity / peak - 1).min())
    cagr = float((1 + total) ** (1 / years) - 1) if total > -1 else -1.0
    out = {
        "total_return": total, "cagr": cagr, "vol": sd * np.sqrt(TRADING_DAYS),
        "sharpe": float(r.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 1e-12 else float("nan"),
        "sortino": float(r.mean() / down * np.sqrt(TRADING_DAYS)) if down > 1e-12 else float("nan"),
        "max_drawdown": max_dd, "calmar": float(cagr / abs(max_dd)) if max_dd < -1e-12 else float("nan"),
        "win_days": float((r > 0).mean()), "best_day": float(r.max()), "worst_day": float(r.min()),
        "years": float(years), "days": int(len(r)),
    }
    if benchmark is not None:
        b = benchmark.reindex(r.index).fillna(0.0)
        var = float(b.var(ddof=1))
        beta = float(r.cov(b) / var) if var > 1e-12 else float("nan")
        active = r - b
        asd = float(active.std(ddof=1))
        out.update({
            "beta": beta, "alpha": float((r.mean() - beta * b.mean()) * TRADING_DAYS),
            "corr": float(r.corr(b)), "excess_cagr": cagr - metrics(b)["cagr"],
            "info_ratio": float(active.mean() / asd * np.sqrt(TRADING_DAYS)) if asd > 1e-12 else float("nan"),
        })
    return out


def information_coefficient(scores: pd.DataFrame, fwd: pd.DataFrame) -> dict:
    """Each day: do higher scores line up with higher future returns? (rank correlation, -1 to +1)."""
    fwd = fwd.reindex(index=scores.index, columns=scores.columns)
    both = scores.notna() & fwd.notna()
    a = scores.where(both).rank(axis=1)
    b = fwd.where(both).rank(axis=1)
    ic = a.corrwith(b, axis=1).dropna()
    if not len(ic):
        return {"mean": float("nan"), "n_days": 0, "series": ic}
    sd = float(ic.std(ddof=1)) if len(ic) > 1 else float("nan")
    return {"mean": float(ic.mean()), "std": sd, "icir": float(ic.mean() / sd) if sd and sd > 1e-12 else float("nan"),
            "t_stat": float(ic.mean() / sd * np.sqrt(len(ic))) if sd and sd > 1e-12 else float("nan"),
            "positive_days": float((ic > 0).mean()), "n_days": int(len(ic)), "series": ic}


def bucket_returns(scores: pd.DataFrame, fwd: pd.DataFrame, buckets: int = 10) -> list[float]:
    """Average future return of each score bucket (bucket 1 = worst scores, last = best)."""
    fwd = fwd.reindex(index=scores.index, columns=scores.columns)
    both = scores.notna() & fwd.notna()
    pct = scores.where(both).rank(axis=1, pct=True)
    label = np.ceil(pct * buckets).clip(1, buckets)
    out = []
    for k in range(1, buckets + 1):
        out.append(float(fwd.where(label == k).mean(axis=1).mean()))
    return out


def pick_accuracy(scores: pd.DataFrame, fwd: pd.DataFrame, top: int = 20) -> dict:
    """"Accuracy" in plain terms: of the `top` best-scored stocks each day, what share went on to
    beat the middle stock, and on what share of days did the group as a whole beat the average?"""
    fwd = fwd.reindex(index=scores.index, columns=scores.columns)
    both = scores.notna() & fwd.notna()
    s, f = scores.where(both), fwd.where(both)
    picked = s.rank(axis=1, ascending=False) <= top
    median, mean = f.median(axis=1), f.mean(axis=1)
    wins = (f.gt(median, axis=0) & picked).sum().sum()
    n = int(picked.sum().sum())
    group = f.where(picked).mean(axis=1)
    valid = group.notna() & mean.notna()
    return {"picks_beat_average": float(wins / n) if n else float("nan"), "group_beats_average": float((group[valid] > mean[valid]).mean()),
            "days": int(valid.sum()), "picks": n}
