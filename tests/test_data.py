"""The price history file: a day only counts once it was saved after that day's closing bell."""
from datetime import datetime

import numpy as np
import pandas as pd

from nightdesk import data, mktcal, research


def make(days):
    idx = pd.DatetimeIndex(days)
    return {f: pd.DataFrame(np.arange(len(idx) * 2, dtype=float).reshape(len(idx), 2) + 10, index=idx, columns=["AAA", "SPY"]) for f in data.FIELDS}


def ny(day, hour, minute=0):
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hour, minute, tzinfo=mktcal.NY)


def test_a_bar_saved_in_the_middle_of_a_session_is_not_trusted_as_that_days_close(monkeypatch):
    panel = make(["2026-10-09", "2026-10-12", "2026-10-13"])                    # the 13th was saved at 11:56 am New York: half a day
    monkeypatch.setattr(data, "load_panel", lambda: panel)
    monkeypatch.setattr(data, "cache_saved_at", lambda: ny("2026-10-13", 11, 56).timestamp())
    evening = ny("2026-10-13", 19, 40)                                          # the bell rang hours ago
    assert research.load_research_panel(evening)["close"].index[-1] == pd.Timestamp("2026-10-12")


def test_a_bar_saved_after_the_bell_counts(monkeypatch):
    panel = make(["2026-10-09", "2026-10-12", "2026-10-13"])
    monkeypatch.setattr(data, "load_panel", lambda: panel)
    monkeypatch.setattr(data, "cache_saved_at", lambda: ny("2026-10-13", 16, 20).timestamp())
    assert research.load_research_panel(ny("2026-10-13", 19, 40))["close"].index[-1] == pd.Timestamp("2026-10-13")


def test_todays_unfinished_session_is_still_left_out(monkeypatch):
    panel = make(["2026-10-09", "2026-10-12", "2026-10-13"])
    monkeypatch.setattr(data, "load_panel", lambda: panel)
    monkeypatch.setattr(data, "cache_saved_at", lambda: ny("2026-10-13", 11, 56).timestamp())
    assert research.load_research_panel(ny("2026-10-13", 12, 0))["close"].index[-1] == pd.Timestamp("2026-10-12")


def test_saving_the_history_stamps_the_time_and_an_old_file_falls_back_to_its_own_date(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "CACHE", tmp_path)
    assert data.cache_saved_at() is None                                        # nothing saved yet
    data.save_panel(make(["2026-10-12", "2026-10-13"]), now=1_791_900_000.0)
    assert data.cache_saved_at() == 1_791_900_000.0
    (tmp_path / "daily_meta.json").unlink()                                     # a file from before stamps existed
    assert abs(data.cache_saved_at() - (tmp_path / "daily_close.parquet").stat().st_mtime) < 1e-6


# ---- topping up the history: holes and half days must never be saved as if they were closes ----
NAMES = [f"S{i:02d}" for i in range(40)] + ["SPY"]


def wide(days, value, names=NAMES):
    idx = pd.DatetimeIndex(days)
    return {f: pd.DataFrame(float(value), index=idx, columns=list(names)) for f in data.FIELDS}


def seed_cache(tmp_path, monkeypatch, saved):
    """A cache whose last row (the 13th) was saved mid-session, so every stock shows a half-day 99."""
    monkeypatch.setattr(data, "CACHE", tmp_path)
    monkeypatch.setattr(data, "EXTRA_SYMBOLS", ["SPY"])
    old = wide(["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"], 50)
    for df in old.values():
        df.loc["2026-10-13"] = 99.0
    data.save_panel(old, now=saved.timestamp())


def test_an_update_replaces_the_half_day_with_the_real_close(tmp_path, monkeypatch):
    seed_cache(tmp_path, monkeypatch, ny("2026-10-13", 11, 56))
    monkeypatch.setattr(data, "_download", lambda symbols, start, **kw: wide(["2026-10-09", "2026-10-12", "2026-10-13"], 50, symbols))
    after = ny("2026-10-13", 19, 40)
    panel = data.update_history(NAMES[:-1], log=lambda m: None, now=after, pause=0)
    assert (panel["close"].loc["2026-10-13"] == 50.0).all()
    assert data.cache_saved_at() == after.timestamp()


def test_a_stock_that_does_not_come_back_gets_another_try(tmp_path, monkeypatch):
    seed_cache(tmp_path, monkeypatch, ny("2026-10-13", 11, 56))
    calls = []

    def flaky(symbols, start, **kw):
        calls.append(list(symbols))
        keep = [s for s in symbols if len(calls) > 1 or s not in ("S03", "S07")]      # two stocks fail the first time (rate limit)
        return wide(["2026-10-09", "2026-10-12", "2026-10-13"], 50, keep)

    monkeypatch.setattr(data, "_download", flaky)
    panel = data.update_history(NAMES[:-1], log=lambda m: None, now=ny("2026-10-13", 19, 40), pause=0)
    assert calls[1] == ["S03", "S07"] and len(calls) == 2
    assert panel["close"].loc["2026-10-13", "S03"] == 50.0


def test_a_stock_that_never_comes_back_is_left_blank_not_left_with_its_half_day_price(tmp_path, monkeypatch):
    seed_cache(tmp_path, monkeypatch, ny("2026-10-13", 11, 56))
    monkeypatch.setattr(data, "_download", lambda symbols, start, **kw: wide(["2026-10-09", "2026-10-12", "2026-10-13"], 50, [s for s in symbols if s != "S03"]))
    panel = data.update_history(NAMES[:-1], log=lambda m: None, now=ny("2026-10-13", 19, 40), pause=0)
    assert np.isnan(panel["close"].loc["2026-10-13", "S03"]) and panel["close"].loc["2026-10-12", "S03"] == 50.0
    assert panel["close"].loc["2026-10-13", "S04"] == 50.0


def test_an_update_full_of_holes_is_thrown_away(tmp_path, monkeypatch):
    saved = ny("2026-10-13", 11, 56)
    seed_cache(tmp_path, monkeypatch, saved)
    monkeypatch.setattr(data, "_download", lambda symbols, start, **kw: wide(["2026-10-09", "2026-10-12", "2026-10-13"], 50, [s for s in symbols if s >= "S10"]))
    import pytest
    with pytest.raises(data.IncompleteHistory):
        data.update_history(NAMES[:-1], log=lambda m: None, now=ny("2026-10-13", 19, 40), pause=0)
    assert data.cache_saved_at() == saved.timestamp()                           # nothing was saved: the next try starts clean
    monkeypatch.setattr(data, "cache_saved_at", lambda: saved.timestamp())
    assert research.load_research_panel(ny("2026-10-13", 19, 40))["close"].index[-1] == pd.Timestamp("2026-10-12")
