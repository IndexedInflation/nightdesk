"""The scorekeeper must count money correctly, charge costs, and never pay a signal for
a move that happened before the signal could have been acted on."""
import numpy as np
import pandas as pd
import pytest

from nightdesk import backtest as B
from nightdesk import portfolio as P


def _panel(returns: pd.DataFrame, overnight: pd.DataFrame | None = None) -> dict:
    """Build prices from daily returns. Optional overnight part (the rest happens intraday)."""
    adj = 100 * (1 + returns.fillna(0)).cumprod()
    prev = adj.shift(1).fillna(100.0)
    op = prev * (1 + (overnight if overnight is not None else 0 * returns))
    return {"adj": adj, "close": adj, "open": op, "high": np.maximum(adj, op), "low": np.minimum(adj, op),
            "volume": adj * 0 + 1e6}


def _days(n):
    return pd.bdate_range("2021-01-04", periods=n)


PLAIN = dict(max_positions=1, hold_buffer=1.0, max_weight=1.0, max_sector_weight=1.0,
             cost_bps=0.0, target_vol=None, trend_filter=False)


def test_holding_a_stock_that_rises_one_percent_a_day_compounds_exactly():
    days = _days(11)
    rets = pd.DataFrame({"A": 0.01, "B": 0.0}, index=days)
    scores = pd.DataFrame({"A": 1.0, "B": 0.0}, index=days)
    res = B.simulate(scores, _panel(rets), B.PortfolioParams(**PLAIN))
    assert res.equity.iloc[-1] == pytest.approx(1.01 ** 10)       # bought at day 0's close, 10 days of gains


def test_costs_are_charged_on_every_dollar_traded():
    days = _days(6)
    rets = pd.DataFrame({"A": 0.0, "B": 0.0}, index=days)
    scores = pd.DataFrame({"A": 1.0, "B": 0.0}, index=days)
    res = B.simulate(scores, _panel(rets), B.PortfolioParams(**{**PLAIN, "cost_bps": 10.0}))
    assert res.equity.iloc[-1] == pytest.approx(1 - 0.001)        # one full buy at 10 hundredths of a percent
    assert res.turnover.sum() == pytest.approx(1.0)


def test_a_signal_never_earns_the_same_days_move():
    days = _days(10)
    rets = pd.DataFrame({"A": 0.0, "B": 0.0}, index=days)
    rets.loc[days[5], "A"] = 0.10                                 # A jumps 10% on day 5
    late = pd.DataFrame({"A": 0.0, "B": 1.0}, index=days)
    late.loc[days[5], ["A", "B"]] = [1.0, 0.0]                    # signal appears on day 5: too late
    early = pd.DataFrame({"A": 0.0, "B": 1.0}, index=days)
    early.loc[days[4], ["A", "B"]] = [1.0, 0.0]                   # signal appears on day 4: in time
    assert B.simulate(late, _panel(rets), B.PortfolioParams(**PLAIN)).equity.iloc[-1] == pytest.approx(1.0)
    assert B.simulate(early, _panel(rets), B.PortfolioParams(**PLAIN)).equity.iloc[-1] == pytest.approx(1.10)


def test_trading_at_the_next_open_misses_the_overnight_gap():
    days = _days(4)
    rets = pd.DataFrame({"A": 0.0, "B": 0.0}, index=days)
    gap = pd.DataFrame({"A": 0.0, "B": 0.0}, index=days)
    rets.loc[days[1], "A"] = 0.02 * 1.0 + 0.0                     # day 1: +2% in total ...
    gap.loc[days[1], "A"] = 0.02                                  # ... all of it before the opening bell
    scores = pd.DataFrame({"A": 1.0, "B": 0.0}, index=days)
    at_close = B.simulate(scores, _panel(rets, gap), B.PortfolioParams(**PLAIN))
    at_open = B.simulate(scores, _panel(rets, gap), B.PortfolioParams(**{**PLAIN, "execution": "next_open"}))
    assert at_close.equity.iloc[-1] == pytest.approx(1.02)
    assert at_open.equity.iloc[-1] == pytest.approx(1.0)


def test_a_holding_is_kept_while_it_stays_inside_the_buffer():
    scores = pd.Series({"A": 5.0, "B": 4.0, "C": 3.0, "D": 2.0, "E": 1.0})
    assert P.choose_holdings(scores, held=["C"], max_positions=2, hold_buffer=2.0) == ["C", "A"]   # C is 3rd of 4 allowed
    assert P.choose_holdings(scores, held=["E"], max_positions=2, hold_buffer=2.0) == ["A", "B"]   # E fell out


def test_no_industry_takes_more_than_its_cap():
    scores = pd.Series({f"T{i}": 10 - i for i in range(6)} | {"X1": 1.0, "X2": 0.5})
    sectors = {f"T{i}": "Tech" for i in range(6)} | {"X1": "Banks", "X2": "Health"}
    picked = P.choose_holdings(scores, held=[], max_positions=4, hold_buffer=1.0, sectors=sectors, max_sector_weight=0.5)
    assert sum(sectors[s] == "Tech" for s in picked) == 2 and len(picked) == 4


def test_blocked_names_are_never_bought_but_existing_holdings_stay():
    scores = pd.Series({"A": 5.0, "B": 4.0, "C": 3.0})
    assert P.choose_holdings(scores, held=[], max_positions=2, hold_buffer=1.0, blocked=["A"]) == ["B", "C"]
    assert "A" in P.choose_holdings(scores, held=["A"], max_positions=2, hold_buffer=1.0, blocked=["A"])


def test_weights_are_equal_capped_and_scaled_by_exposure():
    w = P.target_weights(["A", "B", "C", "D"], exposure=0.5, max_weight=0.2)
    assert w == pytest.approx({"A": 0.125, "B": 0.125, "C": 0.125, "D": 0.125})
    capped = P.target_weights(["A", "B"], exposure=1.0, max_weight=0.3)
    assert capped == pytest.approx({"A": 0.3, "B": 0.3})          # the rest stays in cash


def test_exposure_is_cut_when_the_portfolio_is_wilder_than_the_target():
    calm = np.random.default_rng(0).normal(0, 0.005, 80)          # about 8% a year
    wild = np.random.default_rng(0).normal(0, 0.03, 80)           # about 48% a year
    assert P.exposure_scale(calm, target_vol=0.15, below_trend=False, trend_filter=True) == 1.0
    assert 0.2 < P.exposure_scale(wild, target_vol=0.15, below_trend=False, trend_filter=True) < 0.45
    half = P.exposure_scale(calm, target_vol=0.15, below_trend=True, trend_filter=True)
    assert half == pytest.approx(0.5)


def test_metrics_report_growth_drawdown_and_sharpe():
    r = pd.Series([0.10, -0.50, 0.20, 0.0], index=_days(4))
    m = B.metrics(r)
    assert m["total_return"] == pytest.approx(1.1 * 0.5 * 1.2 - 1)
    assert m["max_drawdown"] == pytest.approx(-0.5)
    steady = pd.Series(0.001, index=_days(252))
    assert B.metrics(steady)["cagr"] == pytest.approx(1.001 ** 252 - 1, rel=1e-6)
    assert B.metrics(pd.Series([0.01, -0.01] * 126, index=_days(252)))["sharpe"] == pytest.approx(0.0, abs=0.2)


def test_daily_rank_correlation_is_one_for_a_perfect_forecast_and_minus_one_for_a_backwards_one():
    days = _days(5)
    fwd = pd.DataFrame(np.random.default_rng(2).normal(size=(5, 8)), index=days, columns=list("ABCDEFGH"))
    assert B.information_coefficient(fwd, fwd)["mean"] == pytest.approx(1.0)
    assert B.information_coefficient(-fwd, fwd)["mean"] == pytest.approx(-1.0)


def test_the_luck_test_cannot_tell_random_scores_from_other_random_scores_but_spots_a_real_edge():
    from nightdesk import research
    rng = np.random.default_rng(4)
    days = _days(260)
    cols = [f"S{i:03d}" for i in range(200)]                     # big enough for "hold 20, keep while in the top 80" to mean something
    rets = pd.DataFrame(rng.normal(0.0003, 0.015, (260, 200)), index=days, columns=cols)
    panel = _panel(rets)
    panel["adj"]["SPY"] = 100 * (1 + rets.mean(axis=1)).cumprod()
    for f in ("close", "open", "high", "low", "volume"):
        panel[f]["SPY"] = panel["adj"]["SPY"]
    edge = rets.shift(-1).rolling(5).mean().shift(-4)            # a crystal ball: knows next week's returns
    noise = pd.DataFrame(rng.normal(size=(260, 200)), index=days, columns=cols)
    sectors = {c: f"group {i % 10}" for i, c in enumerate(cols)}
    real = research.placebo(edge.fillna(0) + 0.0 * noise, panel, sectors, runs=8, log=lambda m: None)
    assert real["beaten_cagr"] == 8                               # genuine foresight beats every shuffle
    fake = research.placebo(noise, panel, sectors, runs=8, log=lambda m: None)
    assert 0 < fake["beaten_cagr"] < 8                            # noise is lost in the pack


def test_a_stop_loss_sells_the_faller_at_that_days_close_and_stays_away_for_a_week():
    days = _days(12)
    rets = pd.DataFrame({"A": 0.0, "B": 0.0}, index=days)
    rets.loc[days[3], "A"] = -0.15                                 # A drops 15% on day 3 ...
    rets.loc[days[4], "A"] = -0.10                                 # ... and another 10% on day 4
    scores = pd.DataFrame({"A": 1.0, "B": 0.0}, index=days)        # the model still loves A throughout
    held = B.simulate(scores, _panel(rets), B.PortfolioParams(**PLAIN))
    assert held.equity.iloc[-1] == pytest.approx(0.85 * 0.90)      # no stop: rides it all the way down
    stopped = B.simulate(scores, _panel(rets), B.PortfolioParams(**{**PLAIN, "position_stop": 0.12, "stop_cooldown": 5}))
    assert stopped.equity.iloc[4] == pytest.approx(0.85)           # took the first fall, sold at that close, missed the second
    assert stopped.n_holdings.iloc[3] == 1 and stopped.last_holdings.keys() == {"A"}   # sat in B for 5 days, then back in A
    assert stopped.equity.iloc[-1] == pytest.approx(0.85)


def test_accuracy_counts_how_often_the_picks_beat_the_average_stock():
    days = _days(6)
    cols = list("ABCDEFGH")
    fwd = pd.DataFrame(np.tile(np.arange(8.0), (6, 1)), index=days, columns=cols) / 100      # H always does best, A worst
    good = pd.DataFrame(np.tile(np.arange(8.0), (6, 1)), index=days, columns=cols)           # scores agree with what happens
    bad = -good
    assert B.pick_accuracy(good, fwd, top=2) == {"picks_beat_average": 1.0, "group_beats_average": 1.0, "days": 6, "picks": 12}
    assert B.pick_accuracy(bad, fwd, top=2)["picks_beat_average"] == 0.0
    coin = pd.DataFrame(np.random.default_rng(0).normal(size=(6, 8)), index=days, columns=cols)
    assert 0.0 < B.pick_accuracy(coin, fwd, top=4)["picks_beat_average"] < 1.0
