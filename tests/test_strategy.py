"""The live decision logic: what to buy and sell, when to stop, and that the live path
scores stocks exactly the way the tested research path does."""
import numpy as np
import pandas as pd
import pytest

from conftest import make_panel
from nightdesk import features as F
from nightdesk import strategy as S
from nightdesk.config import Settings
from nightdesk.model import Ensemble, ModelSpec


def _settings(**kw):
    base = dict(max_positions=2, hold_buffer=1.5, max_weight=0.6, max_sector_weight=1.0, min_trade_dollars=50.0)
    return Settings(**{**base, **kw})


SCORES = pd.Series({"A": 5.0, "B": 4.0, "C": 3.0, "D": 2.0, "E": 1.0})
PRICES = {"A": 100.0, "B": 50.0, "C": 20.0, "D": 10.0, "E": 5.0}


def test_from_cash_it_buys_the_top_names_in_equal_dollar_amounts():
    plan = S.plan_orders(SCORES, positions={}, prices=PRICES, equity=10_000, exposure=1.0, settings=_settings())
    assert plan.sells == []
    assert [(o.symbol, o.side) for o in plan.buys] == [("A", "buy"), ("B", "buy")]
    assert plan.buys[0].qty == pytest.approx(50.0) and plan.buys[1].qty == pytest.approx(100.0)   # $5,000 each


def test_a_holding_that_fell_out_of_the_buffer_is_sold_in_full_and_replaced():
    plan = S.plan_orders(SCORES, positions={"A": 50.0, "E": 1000.0}, prices=PRICES, equity=10_000, exposure=1.0, settings=_settings())
    assert [(o.symbol, o.qty) for o in plan.sells] == [("E", 1000.0)]
    assert [o.symbol for o in plan.buys] == ["B"]
    assert "5 of 5" in plan.sells[0].reason                     # says where it ranked


def test_small_drifts_are_left_alone_but_big_ones_are_trimmed():
    near = S.plan_orders(SCORES, positions={"A": 55.0, "B": 100.0}, prices=PRICES, equity=10_500, exposure=1.0, settings=_settings())
    assert near.sells == [] and near.buys == []                 # A is 52% vs a 50% target: close enough
    far = S.plan_orders(SCORES, positions={"A": 80.0, "B": 40.0}, prices=PRICES, equity=10_000, exposure=1.0, settings=_settings())
    assert [(o.symbol, o.side) for o in far.sells] == [("A", "sell")] and far.sells[0].qty == pytest.approx(30.0)
    assert [(o.symbol, o.side) for o in far.buys] == [("B", "buy")] and far.buys[0].qty == pytest.approx(60.0)


def test_lower_exposure_means_smaller_positions_and_more_cash():
    plan = S.plan_orders(SCORES, positions={}, prices=PRICES, equity=10_000, exposure=0.5, settings=_settings())
    assert sum(o.qty * PRICES[o.symbol] for o in plan.buys) == pytest.approx(5_000)


def test_blocked_or_unpriced_names_are_skipped_for_the_next_best():
    plan = S.plan_orders(SCORES, positions={}, prices={k: v for k, v in PRICES.items() if k != "B"}, equity=10_000,
                         exposure=1.0, settings=_settings(), blocked={"A"})
    assert [o.symbol for o in plan.buys] == ["C", "D"]


def test_trades_smaller_than_the_minimum_are_not_worth_placing():
    plan = S.plan_orders(SCORES, positions={}, prices=PRICES, equity=80, exposure=1.0, settings=_settings(min_trade_dollars=50.0))
    assert plan.buys == []                                      # $40 each is under the $50 minimum


def test_a_stock_down_past_its_stop_is_flagged():
    risk = S.risk_check(positions={"A": (10, 100.0, 87.0), "B": (10, 100.0, 95.0)}, equity=9_000, day_start_equity=9_100,
                        peak_equity=9_500, settings=Settings(position_stop=0.12))
    assert risk.stops == ["A"] and not risk.halt_today and not risk.kill


def test_a_bad_day_halts_buying_and_a_deep_drawdown_trips_the_kill_switch():
    s = Settings(daily_loss_limit=0.03, max_drawdown_limit=0.15)
    bad_day = S.risk_check({}, equity=96_000, day_start_equity=100_000, peak_equity=100_000, settings=s)
    assert bad_day.halt_today and not bad_day.kill
    disaster = S.risk_check({}, equity=84_000, day_start_equity=85_000, peak_equity=100_000, settings=s)
    assert disaster.kill


def test_volume_curve_starts_at_zero_ends_at_one_and_is_half_at_midday():
    assert S.cum_volume_fraction(1.0) == pytest.approx(1.0)
    assert S.cum_volume_fraction(0.5) == pytest.approx(0.5)
    assert 0.10 < S.cum_volume_fraction(0.1) < 0.20             # the first 39 minutes are busy
    assert S.cum_volume_fraction(0.0) > 0                       # never divide by zero


def _flat_last_factor(panel):
    """Real data: the dividend adjustment is the same yesterday and today (unless a dividend lands today)."""
    out = {f: df.copy() for f, df in panel.items()}
    factor = out["adj"].iloc[-2] / out["close"].iloc[-2]
    out["adj"].iloc[-1] = (out["close"].iloc[-1] * factor).to_numpy()
    return out


def test_live_scoring_matches_the_research_path_for_the_same_day():
    panel = _flat_last_factor(make_panel(n_days=700, n_symbols=30, seed=11))
    sectors = {c: ["Tech", "Banks"][i % 2] for i, c in enumerate(panel["close"].columns)}
    syms, X, names = F.latest_matrix(panel, sectors, min_dollar_volume=1e5)
    rng = np.random.default_rng(0)
    model = Ensemble(ModelSpec(horizons=(5,), seeds=(1,), n_trees=20, min_leaf=20, threads=2), names)
    model.fit(rng.uniform(-0.5, 0.5, (3000, len(names))).astype(np.float32), {5: rng.normal(size=3000).astype(np.float32)})
    expected = pd.Series(model.score(X), index=syms)

    history = {f: df.iloc[:-1] for f, df in panel.items()}       # what the engine knows before today
    today = pd.DataFrame({"open": panel["open"].iloc[-1], "high": panel["high"].iloc[-1], "low": panel["low"].iloc[-1],
                          "last": panel["close"].iloc[-1], "volume": panel["volume"].iloc[-1]})
    scorer = S.LiveScorer(history, sectors, model, min_dollar_volume=1e5)
    board = scorer.score(today, session_fraction=1.0, day=panel["close"].index[-1])
    assert list(board.index[:5]) == list(expected.sort_values(ascending=False).index[:5])
    assert np.allclose(board["score"].reindex(expected.index).to_numpy(), expected.to_numpy(), atol=1e-4)


def test_a_stock_with_no_price_today_is_left_off_the_board():
    panel = _flat_last_factor(make_panel(n_days=700, n_symbols=30, seed=11))
    names = F.latest_matrix(panel, None, min_dollar_volume=1e5)[2]
    rng = np.random.default_rng(0)
    model = Ensemble(ModelSpec(horizons=(5,), seeds=(1,), n_trees=5, min_leaf=20, threads=2), names)
    model.fit(rng.uniform(-0.5, 0.5, (500, len(names))).astype(np.float32), {5: rng.normal(size=500).astype(np.float32)})
    history = {f: df.iloc[:-1] for f, df in panel.items()}
    today = pd.DataFrame({"open": panel["open"].iloc[-1], "high": panel["high"].iloc[-1], "low": panel["low"].iloc[-1],
                          "last": panel["close"].iloc[-1], "volume": panel["volume"].iloc[-1]}).drop(index="S20")
    board = S.LiveScorer(history, None, model, min_dollar_volume=1e5).score(today, 1.0, panel["close"].index[-1])
    assert "S20" not in board.index and len(board) >= 25


def test_partial_day_volume_is_scaled_up_to_a_full_day_estimate():
    panel = _flat_last_factor(make_panel(n_days=700, n_symbols=30, seed=11))
    history = {f: df.iloc[:-1] for f, df in panel.items()}
    today = pd.DataFrame({"open": panel["open"].iloc[-1], "high": panel["high"].iloc[-1], "low": panel["low"].iloc[-1],
                          "last": panel["close"].iloc[-1], "volume": panel["volume"].iloc[-1] * 0.5})
    scorer = S.LiveScorer.__new__(S.LiveScorer)
    scorer._prepare(history, None, min_dollar_volume=1e5)
    merged = scorer.panel_with_today(today, session_fraction=0.5, day=panel["close"].index[-1])
    assert np.allclose(merged["volume"].iloc[-1].dropna().to_numpy(), panel["volume"].iloc[-1].reindex(merged["volume"].columns).dropna().to_numpy())


def test_blending_scores_over_days_weights_recent_days_more_and_ignores_days_off_the_list():
    from nightdesk.portfolio import smooth_scores
    days = pd.bdate_range("2024-01-01", periods=3)
    raw = pd.DataFrame({"A": [0.0, 0.0, 3.0], "B": [1.0, np.nan, np.nan]}, index=days)
    out = smooth_scores(raw, span=3)
    assert 1.0 < out["A"].iloc[-1] < 3.0                        # pulled toward the earlier, lower days
    assert np.isnan(out["B"].iloc[-1])                          # not eligible today -> no score today
    assert smooth_scores(raw, span=1).equals(raw)


def test_live_blended_score_matches_the_research_blend_of_daily_scores():
    """The engine blends today's live score with previous days'. It must equal the backtest's recipe."""
    from nightdesk.portfolio import smooth_scores
    panel = _flat_last_factor(make_panel(n_days=700, n_symbols=30, seed=5))
    sectors = {c: ["Tech", "Banks"][i % 2] for i, c in enumerate(panel["close"].columns)}
    names = F.latest_matrix(panel, sectors, min_dollar_volume=1e5)[2]
    rng = np.random.default_rng(1)
    model = Ensemble(ModelSpec(horizons=(5,), seeds=(1,), n_trees=20, min_leaf=20, threads=2), names)
    model.fit(rng.uniform(-0.5, 0.5, (3000, len(names))).astype(np.float32), {5: rng.normal(size=3000).astype(np.float32)})
    daily = {}
    for back in range(12, -1, -1):                              # research path: score each of the last 13 days separately
        cut = {f: df.iloc[: len(df) - back] for f, df in panel.items()}
        syms, X, _ = F.latest_matrix(cut, sectors, min_dollar_volume=1e5)
        daily[cut["close"].index[-1]] = pd.Series(model.score(X), index=syms)
    expected = smooth_scores(pd.DataFrame(daily).T, span=4).iloc[-1].dropna()

    history = {f: df.iloc[:-1] for f, df in panel.items()}
    today = pd.DataFrame({"open": panel["open"].iloc[-1], "high": panel["high"].iloc[-1], "low": panel["low"].iloc[-1],
                          "last": panel["close"].iloc[-1], "volume": panel["volume"].iloc[-1]})
    scorer = S.LiveScorer(history, sectors, model, min_dollar_volume=1e5, smooth_span=4, warmup=12)
    board = scorer.score(today, session_fraction=1.0, day=panel["close"].index[-1])
    assert np.allclose(board["score"].reindex(expected.index).to_numpy(), expected.to_numpy(), atol=1e-4)
    assert "raw" in board.columns                               # today's un-blended score is kept for display
