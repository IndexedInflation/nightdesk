"""The forecaster must give the same answers after being saved and reloaded, and the
market clock must know when New York is open."""
from datetime import datetime
import json

import numpy as np

from nightdesk import mktcal
from nightdesk.model import Ensemble, ModelSpec, zscore_by_group


def _toy(n=4000, f=6, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.uniform(-0.5, 0.5, (n, f)).astype(np.float32)
    y = (0.6 * X[:, 0] - 0.4 * X[:, 1] + rng.normal(0, 0.05, n)).astype(np.float32)
    return X, y


SMALL = ModelSpec(horizons=(5,), seeds=(1, 2), n_trees=40, min_leaf=50, threads=2)


def test_zscore_by_group_centres_and_scales_each_day_separately():
    values = np.array([1.0, 2.0, 3.0, 10.0, 20.0, 30.0])
    days = np.array([0, 0, 0, 1, 1, 1])
    z = zscore_by_group(values, days)
    assert np.allclose(z[:3], [-1, 0, 1]) and np.allclose(z[3:], [-1, 0, 1])


def test_committee_learns_a_simple_pattern_and_ranks_new_cases_correctly():
    X, y = _toy()
    model = Ensemble(SMALL, [f"f{i}" for i in range(6)]).fit(X, {5: y})
    Xn, yn = _toy(seed=9)
    assert np.corrcoef(model.score(Xn), yn)[0, 1] > 0.9
    top = list(model.importance())[:2]
    assert set(top) == {"f0", "f1"}                       # it found the two signals that matter


def test_saved_model_gives_identical_scores_after_reload(tmp_path):
    X, y = _toy()
    model = Ensemble(SMALL, [f"f{i}" for i in range(6)]).fit(X, {5: y})
    model.save(tmp_path / "m.json")
    assert json.loads((tmp_path / "m.json").read_text())["format"] == "nightdesk-ensemble-v1"
    again = Ensemble.load(tmp_path / "m.json")
    assert np.allclose(model.score(X[:200]), again.score(X[:200]))
    assert again.spec.horizons == (5,) and again.feature_names == model.feature_names


def test_rows_marked_unusable_are_left_out_of_training():
    X, y = _toy()
    y_bad = y.copy()
    y_bad[2000:] = -y_bad[2000:]                          # second half teaches the opposite lesson
    ok = {5: np.arange(len(y)) < 2000}
    model = Ensemble(SMALL, [f"f{i}" for i in range(6)]).fit(X, {5: y_bad}, row_ok=ok)
    assert np.corrcoef(model.score(X), y)[0, 1] > 0.9


def test_clock_knows_open_closed_weekend_and_holiday():
    ny = mktcal.NY
    assert mktcal.status(datetime(2026, 10, 13, 11, 0, tzinfo=ny))["phase"] == "open"
    assert mktcal.status(datetime(2026, 10, 13, 8, 0, tzinfo=ny))["phase"] == "pre"
    assert mktcal.status(datetime(2026, 10, 13, 17, 0, tzinfo=ny))["phase"] == "after"
    assert mktcal.status(datetime(2026, 10, 17, 11, 0, tzinfo=ny))["phase"] == "closed"     # Saturday
    assert mktcal.status(datetime(2026, 11, 26, 11, 0, tzinfo=ny))["phase"] == "closed"     # Thanksgiving
    assert mktcal.status(datetime(2026, 11, 27, 14, 0, tzinfo=ny))["phase"] == "after"      # 1pm early close


def test_last_completed_session_skips_the_day_still_in_progress():
    ny = mktcal.NY
    assert mktcal.last_completed_session(datetime(2026, 10, 13, 11, 0, tzinfo=ny)).isoformat() == "2026-10-12"
    assert mktcal.last_completed_session(datetime(2026, 10, 13, 16, 30, tzinfo=ny)).isoformat() == "2026-10-13"
    assert mktcal.last_completed_session(datetime(2026, 10, 12, 9, 0, tzinfo=ny)).isoformat() == "2026-10-09"   # Monday morning


def test_us_session_falls_overnight_in_sydney():
    s = mktcal.status(datetime(2026, 10, 13, 11, 0, tzinfo=mktcal.NY))
    assert s["session_open_sydney"] == "Wed 12:30 am" and s["session_close_sydney"] == "Wed 7:00 am"


def test_a_committee_taught_from_two_answer_sheets_blends_them_evenly(tmp_path):
    rng = np.random.default_rng(3)
    X = rng.uniform(-0.5, 0.5, (4000, 6)).astype(np.float32)
    a = (X[:, 0] + rng.normal(0, 0.03, 4000)).astype(np.float32)          # sheet A only cares about signal 0
    b = (X[:, 3] + rng.normal(0, 0.03, 4000)).astype(np.float32)          # sheet B only cares about signal 3
    names = [f"f{i}" for i in range(6)]
    both = Ensemble(SMALL, names).fit(X, {"plain": {5: a}, "risk": {5: b}})
    only_a = Ensemble(SMALL, names).fit(X, {5: a})
    only_b = Ensemble(SMALL, names).fit(X, {5: b})
    assert np.allclose(both.score(X), (only_a.score(X) + only_b.score(X)) / 2, atol=1e-6)     # exactly the average of the two
    assert abs(np.corrcoef(both.score(X), X[:, 0])[0, 1] - np.corrcoef(both.score(X), X[:, 3])[0, 1]) < 0.05
    assert both.members == 6 and only_a.members == 3                      # 2 sheets x (2 tree models + 1 straight line)
    both.save(tmp_path / "m.json")
    assert np.allclose(Ensemble.load(tmp_path / "m.json").score(X[:300]), both.score(X[:300]))


def test_calendar_handles_holidays_and_early_closes_beyond_the_old_table():
    from datetime import date
    assert not mktcal.is_trading_day(date(2030, 1, 1))
    assert not mktcal.is_trading_day(date(2030, 11, 28))
    assert mktcal.session(date(2030, 11, 29))[1].hour == 13
    assert not mktcal.is_trading_day(date(2025, 1, 9))   # special presidential funeral closure
    assert not mktcal.is_trading_day(date(2027, 6, 18))  # observed Juneteenth


def test_calendar_preserves_new_york_bells_across_daylight_saving():
    from datetime import date
    winter = mktcal.session(date(2026, 1, 6))[0]
    summer = mktcal.session(date(2026, 7, 7))[0]
    assert winter.hour == summer.hour == 9 and winter.minute == summer.minute == 30
    assert winter.utcoffset().total_seconds() == -5 * 3600
    assert summer.utcoffset().total_seconds() == -4 * 3600
