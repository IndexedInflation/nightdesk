"""The signals must only use information that existed at the time. These tests prove it."""
import numpy as np
import pandas as pd

from conftest import make_panel
from nightdesk import features as F


def test_features_never_use_prices_from_the_future(panel, sectors):
    cut = 520
    before = F.compute_features(panel, sectors)
    scrambled = {f: df.copy() for f, df in panel.items()}
    rng = np.random.default_rng(1)
    for f, df in scrambled.items():                               # rewrite history AFTER the cut-off day
        df.iloc[cut + 1:] = df.iloc[cut + 1:].to_numpy() * rng.uniform(0.5, 1.5, df.iloc[cut + 1:].shape)
    after = F.compute_features(scrambled, sectors)
    assert before.keys() == after.keys() and len(before) >= 50
    for name in before:
        a = before[name].iloc[: cut + 1].to_numpy()
        b = after[name].iloc[: cut + 1].to_numpy()
        assert np.allclose(a, b, equal_nan=True), f"{name} changed when only the future changed"


def test_five_day_return_matches_hand_calculation(panel, sectors):
    feats = F.compute_features(panel, sectors)
    adj = panel["adj"]
    expected = adj["S10"].iloc[300] / adj["S10"].iloc[295] - 1
    assert np.isclose(feats["r5"]["S10"].iloc[300], expected)


def test_benchmark_is_not_a_tradable_feature_column(panel, sectors):
    feats = F.compute_features(panel, sectors)
    assert "SPY" not in feats["r5"].columns


def test_rank_normalize_spreads_each_day_between_minus_half_and_half():
    wide = pd.DataFrame([[1.0, 5.0, 3.0, np.nan], [2.0, 2.0, 9.0, 4.0]], columns=list("ABCD"))
    mask = pd.DataFrame([[True, True, True, True], [True, True, True, False]], columns=list("ABCD"))
    out = F.rank_normalize(wide, mask)
    assert out.loc[0, "A"] < out.loc[0, "C"] < out.loc[0, "B"]
    assert np.isnan(out.loc[0, "D"]) and np.isnan(out.loc[1, "D"])   # missing or outside the universe
    assert np.nanmin(out.to_numpy()) >= -0.5 and np.nanmax(out.to_numpy()) <= 0.5
    assert np.isclose(np.nansum(out.loc[0].to_numpy()), 0.0)         # centred on zero


def test_forward_return_is_the_next_h_days_and_blank_at_the_end(panel):
    fwd = F.forward_returns(panel, 5)
    adj = panel["adj"]
    assert np.isclose(fwd["S12"].iloc[100], adj["S12"].iloc[105] / adj["S12"].iloc[100] - 1)
    assert fwd.iloc[-5:].isna().all().all()


def test_universe_needs_a_year_of_history_and_real_liquidity(panel):
    mask = F.universe_mask(panel, min_history=252, min_price=5.0, min_dollar_volume=1e5)
    assert not mask["S00"].iloc[:150 + 252 - 1].any()               # S00 lists on day 150: not eligible for a year
    assert mask["S00"].iloc[-1]
    strict = F.universe_mask(panel, min_history=252, min_price=5.0, min_dollar_volume=1e15)
    assert not strict.any().any()                                   # nothing trades a quadrillion dollars a day


def test_last_day_from_a_short_recent_window_matches_full_history(panel, sectors):
    """The live engine recomputes signals from the last ~400 days only. Same answer required."""
    full = F.compute_features(panel, sectors)
    tail = F.compute_features({f: df.iloc[-F.LIVE_WINDOW:] for f, df in panel.items()}, sectors)
    for name in full:
        a = full[name].iloc[-1].to_numpy()
        b = tail[name].iloc[-1].to_numpy()
        assert np.allclose(a, b, rtol=1e-3, atol=1e-4, equal_nan=True), f"{name} differs on the short window"


def test_dataset_rows_only_cover_universe_members_with_known_outcomes():
    panel = make_panel(n_days=600, n_symbols=12, seed=3)
    sectors = {f"S{i:02d}": "X" for i in range(12)}
    ds = F.build_dataset(panel, sectors, horizons=(1, 5), min_dollar_volume=1e5)
    assert ds.X.dtype == np.float32 and ds.X.shape[0] == len(ds.dates) == len(ds.symbols)
    assert ds.X.shape[1] == len(ds.feature_names)
    assert set(ds.y) == {1, 5}
    last_day = panel["adj"].index[-1]
    assert (ds.dates < last_day).all() or np.isnan(ds.y[1][ds.dates == last_day]).all()
    assert np.nanmax(np.abs(ds.X)) <= 0.5 + 1e-6
