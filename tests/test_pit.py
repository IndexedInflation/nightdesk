"""Rebuilding who was in the index when: the fairer test depends on getting this right."""
import numpy as np
import pandas as pd

from conftest import make_panel
from nightdesk import features as F
from nightdesk import pit

CURRENT = [{"symbol": "AAA", "added": "2015-06-01"}, {"symbol": "BBB", "added": "1990-01-01"}, {"symbol": "ZZZ", "added": "unknown"}]
EVENTS = [
    {"date": "2008-01-01", "added": "AAA", "removed": None},       # AAA's first spell in the index ...
    {"date": "2010-01-01", "added": None, "removed": "AAA"},       # ... ended here; it came back in 2015
    {"date": "2012-03-01", "added": "CCC", "removed": "DDD"},
    {"date": "2018-05-01", "added": "EEE", "removed": "CCC"},
    {"date": "1999-01-01", "added": None, "removed": "OLD"},       # before our price history starts: ignored
]


def test_each_company_gets_the_spells_it_was_really_in_the_index():
    s = pit.build_stints(EVENTS, CURRENT)
    assert sorted(s["AAA"]) == [("2008-01-01", "2010-01-01"), ("2015-06-01", "9999-12-31")]
    assert s["BBB"] == [("1990-01-01", "9999-12-31")]
    assert s["CCC"] == [("2012-03-01", "2018-05-01")]              # joined and later left
    assert s["DDD"] == [("0000-00-00", "2012-03-01")]              # in since before records, until it was dropped
    assert s["ZZZ"] == [("0000-00-00", "9999-12-31")]              # no join date recorded: assume always
    assert "OLD" not in s


def test_membership_is_true_only_inside_a_spell(monkeypatch):
    monkeypatch.setattr(pit, "stints", lambda: pit.build_stints(EVENTS, CURRENT))
    days = pd.to_datetime(["2007-06-01", "2009-06-01", "2011-06-01", "2016-06-01", "2019-06-01"])
    m = pit.membership(days, ["AAA", "CCC", "DDD", "NOPE"])
    assert m["AAA"].tolist() == [False, True, False, True, True]
    assert m["CCC"].tolist() == [False, False, False, True, False]
    assert m["DDD"].tolist() == [True, True, True, False, False]
    assert not m["NOPE"].any()


def test_a_company_is_left_out_of_the_training_data_on_days_it_was_not_in_the_index():
    panel = make_panel(n_days=600, n_symbols=12, seed=3)
    cols = F.tradable_columns(panel)
    member = pd.DataFrame(True, index=panel["close"].index, columns=cols)
    member.loc[member.index[:450], "S05"] = False                  # S05 only joined late
    ds = F.build_dataset(panel, {c: "X" for c in cols}, horizons=(5,), min_dollar_volume=1e5, membership=member)
    s05 = ds.dates[ds.symbols == "S05"]
    assert len(s05) > 0 and s05.min() >= member.index[450].to_datetime64()
    everyone = F.build_dataset(panel, {c: "X" for c in cols}, horizons=(5,), min_dollar_volume=1e5)
    assert len(everyone.dates) > len(ds.dates)


# ---- the former members' price file must not keep a half-day bar either ----
def _frames(days, values):
    from nightdesk import data
    idx = pd.DatetimeIndex(days)
    return {f: pd.DataFrame(values, index=idx, dtype=float) for f in data.FIELDS}


def test_former_members_prices_saved_mid_session_are_refreshed_or_dropped(tmp_path, monkeypatch):
    from datetime import datetime
    from nightdesk import data, mktcal
    monkeypatch.setattr(pit, "CACHE", tmp_path)
    days = [str(d.date()) for d in pd.bdate_range(end="2026-10-13", periods=14)]
    nothing = [np.nan] * 10
    old = _frames(days, {"ALIVE": nothing + [10, 11, 12, 99], "GONE": [5, 5] + nothing + [np.nan, np.nan], "HALF": nothing + [np.nan, np.nan, np.nan, 77]})
    for f, df in old.items():                                       # saved at 1:37 pm New York on the 13th: its last row is half a day
        df.to_parquet(tmp_path / f"pit_{f}.parquet")
    (tmp_path / "pit_meta.json").write_text('{"saved_at": %r}' % datetime(2026, 10, 13, 13, 37, tzinfo=mktcal.NY).timestamp())
    asked = []

    def fake_download(symbols, start, **kw):
        asked.append(sorted(symbols))
        return _frames(days, {"ALIVE": nothing + [10, 11, 12, 13]})         # the real close on the 13th was 13, not 99

    monkeypatch.setattr(data, "_download", fake_download)
    now = datetime(2026, 10, 13, 19, 40, tzinfo=mktcal.NY)
    got = pit.former_prices(["ALIVE", "GONE", "HALF"], lambda m: None, now=now)
    assert asked == [["ALIVE"]]                                     # only companies with a recent trusted price can have newer ones
    assert got["close"]["ALIVE"].dropna().tolist() == [10, 11, 12, 13] and got["close"]["GONE"].dropna().tolist() == [5, 5]
    assert np.isnan(got["close"].loc["2026-10-13", "HALF"])         # a half-day print with nothing to replace it is dropped, not kept
    assert pit.former_prices(["ALIVE", "GONE", "HALF"], lambda m: None, now=now)["close"]["ALIVE"].iloc[-1] == 13 and len(asked) == 1   # now trusted: no second download


def test_former_members_prices_survive_a_failed_refresh(tmp_path, monkeypatch):
    from datetime import datetime
    from nightdesk import data, mktcal
    monkeypatch.setattr(pit, "CACHE", tmp_path)
    days = ["2026-10-09", "2026-10-12", "2026-10-13"]
    for f, df in _frames(days, {"ALIVE": [11, 12, 99]}).items():
        df.to_parquet(tmp_path / f"pit_{f}.parquet")
    (tmp_path / "pit_meta.json").write_text('{"saved_at": %r}' % datetime(2026, 10, 13, 13, 37, tzinfo=mktcal.NY).timestamp())

    def broken(symbols, start, **kw):
        raise OSError("offline")

    monkeypatch.setattr(data, "_download", broken)
    got = pit.former_prices(["ALIVE"], lambda m: None, now=datetime(2026, 10, 13, 19, 40, tzinfo=mktcal.NY))
    assert got["close"]["ALIVE"].dropna().tolist() == [11, 12]      # the half-day 99 is never used
