"""Live decisions. Pure logic only (no internet, no clock), so every rule here is unit-tested.

- LiveScorer   : today's prices + history -> a score for every stock (same maths as research)
- plan_orders  : scores + current holdings -> the list of orders to place
- risk_check   : should anything be sold or stopped right now?
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import features as F
from .config import Settings
from .portfolio import choose_holdings, exposure_scale, smooth_scores, target_weights


def cum_volume_fraction(session_fraction: float) -> float:
    """Roughly how much of a day's trading is done by this point (busy open, quiet lunch, busy close)."""
    x = min(1.0, max(0.03, session_fraction))
    return 0.7 * x + 1.2 * ((x - 0.5) ** 3 + 0.125)


class LiveScorer:
    """Scores every stock using history up to yesterday plus today's prices so far."""

    def __init__(self, history: dict[str, pd.DataFrame], sectors: dict[str, str] | None, model, min_dollar_volume: float = 5e6,
                 smooth_span: int = 1, warmup: int = 40):
        self._prepare(history, sectors, min_dollar_volume)
        self.model = model
        self.smooth_span = smooth_span
        # un-blended daily scores for the last few finished days: needed to blend, and for the after-hours board
        days, syms, X, names = F.recent_matrix(history, sectors, min_dollar_volume, n_days=max(1, warmup))
        if names != model.feature_names:
            raise ValueError("signals do not match the signals the model was trained on")
        self.raw_history = pd.Series(model.score(X, days), index=pd.MultiIndex.from_arrays([days, syms])).unstack()
        last = days == days.max()
        self.closed_X = pd.DataFrame(X[last], index=syms[last], columns=names)

    def _board(self, raw: pd.Series, blended: pd.Series) -> pd.DataFrame:
        board = pd.DataFrame({"score": blended.reindex(raw.index), "raw": raw})
        board = board.sort_values("score", ascending=False, kind="mergesort")
        board["rank"] = np.arange(1, len(board) + 1)
        return board

    def closed_board(self) -> pd.DataFrame:
        """The board as of the last closing bell (used when the market is shut)."""
        raw = self.raw_history.iloc[-1].dropna()
        self.last_X = self.closed_X
        return self._board(raw, smooth_scores(self.raw_history, self.smooth_span).iloc[-1])

    def _prepare(self, history, sectors, min_dollar_volume: float = 5e6) -> None:
        self.base = {f: df.iloc[-(F.LIVE_WINDOW - 1):] for f, df in history.items()}
        self.sectors = sectors
        self.min_dollar_volume = min_dollar_volume
        self.factor = (history["adj"].iloc[-1] / history["close"].iloc[-1])     # dividend adjustment carried into today
        self.prev_close = history["close"].iloc[-1]             # closing prices of the latest finished day
        self.prev2_close = history["close"].iloc[-2]            # and of the day before
        self.as_of = history["close"].index[-1]
        self.returns = history["adj"].iloc[-64:].pct_change(fill_method=None).iloc[1:]
        spy = history["adj"]["SPY"].dropna() if "SPY" in history["adj"].columns else None
        self.spy_tail = spy.iloc[-199:] if spy is not None else None
        self.last_X: pd.DataFrame | None = None

    def panel_with_today(self, today: pd.DataFrame, session_fraction: float, day) -> dict[str, pd.DataFrame]:
        """History plus one extra row for today. Part-day volume is scaled to a full-day estimate."""
        cols = self.base["close"].columns
        t = today.reindex(cols)
        last = t["last"].astype("float64")
        op = t["open"].astype("float64").fillna(last)
        row = {
            "open": op, "close": last, "adj": last * self.factor,
            "high": np.fmax(np.fmax(t["high"].astype("float64"), last), op),
            "low": np.fmin(np.fmin(t["low"].astype("float64"), last), op),
            "volume": t["volume"].astype("float64") / cum_volume_fraction(session_fraction),
        }
        out = {}
        for f, df in self.base.items():
            new = pd.DataFrame([row[f].to_numpy(dtype="float64")], index=pd.DatetimeIndex([day]), columns=cols)
            out[f] = pd.concat([df, new])
        return out

    def score(self, today: pd.DataFrame, session_fraction: float, day) -> pd.DataFrame:
        """The board: every eligible stock, best score first."""
        panel = self.panel_with_today(today, session_fraction, pd.Timestamp(day))
        syms, X, names = F.latest_matrix(panel, self.sectors, self.min_dollar_volume)
        if names != self.model.feature_names:
            raise ValueError("live signals do not match the signals the model was trained on")
        raw = pd.Series(self.model.score(X), index=syms)
        self.last_X = pd.DataFrame(X, index=syms, columns=names)
        if self.smooth_span <= 1:
            return self._board(raw, raw)
        table = pd.concat([self.raw_history, raw.rename(pd.Timestamp(day)).to_frame().T])
        return self._board(raw, smooth_scores(table, self.smooth_span).iloc[-1])

    def exposure(self, picks: list[str], settings: Settings, spy_last: float | None) -> float:
        """How much of the account to invest right now (see portfolio.exposure_scale)."""
        if not picks:
            return 0.0
        unit = np.array(list(target_weights(picks, 1.0, settings.max_weight).values()))
        recent = np.dot(np.nan_to_num(self.returns.reindex(columns=picks).to_numpy(), nan=0.0), unit)
        below = False
        if spy_last and self.spy_tail is not None and len(self.spy_tail) >= 150:
            below = spy_last * float(self.factor.get("SPY", 1.0)) < (self.spy_tail.sum() + spy_last) / (len(self.spy_tail) + 1)
        return exposure_scale(recent, settings.target_vol, below, settings.trend_filter)


@dataclass
class PlannedOrder:
    symbol: str
    side: str
    qty: float
    reason: str
    tag: str                      # open | close | trim | top-up
    rank: int | None = None


@dataclass
class Plan:
    sells: list[PlannedOrder] = field(default_factory=list)
    buys: list[PlannedOrder] = field(default_factory=list)
    picks: list[str] = field(default_factory=list)
    targets: dict[str, float] = field(default_factory=dict)


def plan_orders(scores: pd.Series, positions: dict[str, float], prices: dict[str, float], equity: float,
                exposure: float, settings: Settings, sectors: dict[str, str] | None = None,
                blocked: set[str] | frozenset = frozenset()) -> Plan:
    """Work out the trades that move the account from what it holds to what it should hold."""
    scores = scores.dropna()
    scores = scores[[s for s in scores.index if prices.get(s)]]              # cannot trade what has no price
    order = list(scores.sort_values(ascending=False, kind="mergesort").index)
    rank = {s: i + 1 for i, s in enumerate(order)}
    n = len(order)
    keep_cut = max(settings.max_positions, int(round(settings.max_positions * settings.hold_buffer)))
    picks = choose_holdings(scores, list(positions), settings.max_positions, settings.hold_buffer, sectors,
                            settings.max_sector_weight, blocked)
    targets = target_weights(picks, exposure, settings.max_weight)
    plan = Plan(picks=picks, targets=targets)
    if equity <= 0:
        return plan
    for sym, qty in positions.items():
        if sym in targets or qty <= 0:
            continue
        why = (f"Sold: slipped to rank {rank[sym]} of {n}, outside the top {keep_cut} it needs to stay in."
               if sym in rank else "Sold: no longer on today's list of eligible stocks.")
        plan.sells.append(PlannedOrder(sym, "sell", qty, why, "close", rank.get(sym)))
    for sym in picks:
        price = prices[sym]
        held = positions.get(sym, 0.0)
        current, target = held * price / equity, targets[sym]
        dollars = (target - current) * equity
        if held > 0 and abs(target - current) <= settings.rebalance_band * target:
            continue                                                         # close enough: don't pay to nudge it
        if abs(dollars) < settings.min_trade_dollars:
            continue
        qty = round(abs(dollars) / price, 4)
        if held <= 0:
            plan.buys.append(PlannedOrder(sym, "buy", qty, f"Bought: ranked {rank[sym]} of {n} today.", "open", rank[sym]))
        elif dollars > 0:
            plan.buys.append(PlannedOrder(sym, "buy", qty, f"Topped up: it was {current:.1%} of the account against a {target:.1%} target.", "top-up", rank[sym]))
        else:
            plan.sells.append(PlannedOrder(sym, "sell", min(qty, held), f"Trimmed: it had grown to {current:.1%} of the account against a {target:.1%} target.", "trim", rank[sym]))
    return plan


@dataclass
class Risk:
    stops: list[str] = field(default_factory=list)   # stocks to sell now
    halt_today: bool = False                         # no more buying today
    kill: bool = False                               # sell everything and switch auto-trading off
    notes: list[str] = field(default_factory=list)


def risk_check(positions: dict[str, tuple[float, float, float]], equity: float, day_start_equity: float,
               peak_equity: float, settings: Settings) -> Risk:
    """positions: symbol -> (quantity, average cost, latest price)."""
    risk = Risk()
    for sym, (qty, cost, last) in positions.items():
        if qty > 0 and cost > 0 and last > 0 and last <= cost * (1 - settings.position_stop):
            risk.stops.append(sym)
            risk.notes.append(f"{sym} is {last / cost - 1:.1%} below what was paid (limit {-settings.position_stop:.0%}).")
    if day_start_equity > 0 and equity <= day_start_equity * (1 - settings.daily_loss_limit):
        risk.halt_today = True
        risk.notes.append(f"Down {equity / day_start_equity - 1:.1%} today (limit {-settings.daily_loss_limit:.0%}): no more buying today.")
    if peak_equity > 0 and equity <= peak_equity * (1 - settings.max_drawdown_limit):
        risk.kill = True
        risk.notes.append(f"Down {equity / peak_equity - 1:.1%} from the account's high (limit {-settings.max_drawdown_limit:.0%}): selling everything and stopping.")
    return risk
