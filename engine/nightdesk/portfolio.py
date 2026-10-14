"""Turning model scores into a portfolio. The same three functions are used by the
backtest and by the live engine, so what was tested is exactly what trades.

1. choose_holdings  - which stocks to own
2. target_weights   - how much of each
3. exposure_scale   - how much of the account to have invested at all
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def choose_holdings(scores: pd.Series, held: Iterable[str], max_positions: int, hold_buffer: float,
                    sectors: Mapping[str, str] | None = None, max_sector_weight: float = 1.0,
                    blocked: Iterable[str] = ()) -> list[str]:
    """Pick the stocks to own today.

    Stocks already owned are kept while they rank inside the top (max_positions x hold_buffer):
    that buffer stops the portfolio flip-flopping between near-identical names, which only
    costs money. Empty slots go to the best-ranked stocks not yet owned. `blocked` names are
    never newly bought (used by the news check), and no industry group may take more than
    `max_sector_weight` of the slots.
    """
    order = list(scores.dropna().sort_values(ascending=False, kind="mergesort").index)
    rank_of = {s: i for i, s in enumerate(order)}
    keep_cut = max(max_positions, int(round(max_positions * hold_buffer)))
    picks = sorted((s for s in held if s in rank_of and rank_of[s] < keep_cut), key=rank_of.get)[:max_positions]
    cap = max(1, int(max_sector_weight * max_positions + 1e-9)) if sectors else None
    counts = Counter(sectors.get(s, "Other") for s in picks) if sectors else Counter()
    owned, blocked = set(picks), set(blocked)
    for s in order:
        if len(picks) >= max_positions:
            break
        if s in owned or s in blocked:
            continue
        if cap is not None:
            group = sectors.get(s, "Other")
            if counts[group] >= cap:
                continue
            counts[group] += 1
        picks.append(s)
        owned.add(s)
    return picks


def target_weights(holdings: list[str], exposure: float, max_weight: float) -> dict[str, float]:
    """Equal slices of the invested part of the account, never above `max_weight` each."""
    if not holdings:
        return {}
    each = min(max_weight, exposure / len(holdings))
    return {s: each for s in holdings}


def exposure_scale(recent_portfolio_returns: np.ndarray, target_vol: float | None,
                   below_trend: bool, trend_filter: bool) -> float:
    """Fraction of the account to invest (0 to 1, never borrowed money).

    If the candidate portfolio has recently swung more than the yearly target, hold some cash.
    If the overall market is below its 200-day average, halve the bet.
    """
    scale = 1.0
    r = np.asarray(recent_portfolio_returns, dtype="float64")
    r = r[~np.isnan(r)]
    if target_vol and len(r) >= 10:
        vol = max(r[-21:].std(ddof=1), r.std(ddof=1)) * np.sqrt(TRADING_DAYS)
        if vol > 1e-9:
            scale = min(1.0, target_vol / vol)
    if trend_filter and below_trend:
        scale *= 0.5
    return float(scale)


def smooth_scores(scores: pd.DataFrame, span: int) -> pd.DataFrame:
    """Blend each day's score with the previous days' (recent days count more).

    Steadier rankings mean fewer trades. A stock with no score today gets none after blending.
    `scores` is days x stocks.
    """
    if span <= 1:
        return scores
    return scores.ewm(span=span, min_periods=1).mean().where(scores.notna())
