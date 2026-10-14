"""Earnings and analyst events. The rule that matters: an event counts only from the first
closing bell after it became public. (These tests were written straight after the code, then
the code was broken on purpose to check they notice.)"""
import numpy as np
import pandas as pd

from nightdesk import events as E

DAYS = pd.bdate_range("2024-01-01", periods=300)          # day 0 is a Monday


def at(day: int, clock: str = "10:00") -> pd.Timestamp:
    return pd.Timestamp(f"{DAYS[day].date()} {clock}")


def earnings(rows):
    return pd.DataFrame([{"symbol": s, "ts": ts, "estimate": 1.0, "reported": rep, "surprise": sur} for s, ts, rep, sur in rows])


def analyst(rows):
    return pd.DataFrame([{"symbol": s, "ts": ts, "firm": firm, "action": act, "target": tgt, "prior": pri} for s, ts, firm, act, tgt, pri in rows])


NO_EARNINGS = earnings([("ZZZ", at(0), 1.0, 0.0)]).iloc[:0]
NO_ANALYST = analyst([("ZZZ", at(0), "x", "up", 1, 1)]).iloc[:0]


def test_an_event_counts_from_the_first_close_after_it_was_public():
    ts = pd.Series([at(1, "10:00"), at(1, "16:00"), at(1, "16:30"), at(1, "00:00"),
                    pd.Timestamp("2024-01-06 09:00"),                       # a Saturday
                    pd.Timestamp("2023-12-25 10:00"),                       # five weekdays before the table starts
                    pd.Timestamp("2030-01-01 10:00")])
    assert E.known_position(ts, DAYS).tolist() == [1, 2, 2, 2, 5, -5, 300]


def test_earnings_surprise_is_carried_forward_and_ages_a_day_at_a_time():
    e = earnings([("AAA", at(10, "07:00"), 1.00, 10.0), ("AAA", at(73, "16:30"), 0.80, -20.0)])
    f = E.event_features(DAYS, ["AAA", "BBB"], e, NO_ANALYST)
    age, sur, fresh = f["earn_age"]["AAA"], f["earn_surprise"]["AAA"], f["earn_fresh"]["AAA"]
    assert np.isnan(age.iloc[9]) and age.iloc[10] == 0 and age.iloc[20] == 10
    assert sur.iloc[20] == 10.0 and sur.iloc[73] == 10.0           # the after-hours report is not known at day 73's close
    assert sur.iloc[74] == -20.0 and age.iloc[74] == 0
    assert fresh.iloc[74] == -20.0 and fresh.iloc[84] == -20.0 and np.isnan(fresh.iloc[85])   # fresh for 10 trading days
    assert np.isnan(sur.iloc[74 + 81])                             # forgotten once it is more than a quarter old
    assert f["earn_surprise"]["BBB"].isna().all()                  # a company with no reports stays blank


def test_profit_growth_compares_with_the_same_quarter_a_year_earlier():
    e = earnings([("AAA", at(5 + 60 * k), eps, 0.0) for k, eps in enumerate([1.0, 1.1, 1.2, 1.3, 1.5])])
    g = E.event_features(DAYS, ["AAA"], e, NO_ANALYST)["eps_growth"]["AAA"]
    assert np.isnan(g.iloc[200])                                   # only four reports so far: nothing to compare with
    assert np.isclose(g.iloc[245], 0.5)                            # 1.5 against 1.0 a year earlier


def test_the_price_reaction_is_the_return_on_the_day_the_report_first_showed_in_the_close():
    r = pd.DataFrame({"AAA": np.zeros(300)}, index=DAYS)
    r.iloc[40, 0], r.iloc[41, 0] = 0.07, -0.03
    e = earnings([("AAA", at(40, "16:10"), 1.0, 5.0)])             # after the bell on day 40: day 41 carries the reaction
    react = E.event_features(DAYS, ["AAA"], e, NO_ANALYST, returns=r)["earn_react"]["AAA"]
    assert np.isnan(react.iloc[40]) and np.isclose(react.iloc[41], -0.03) and np.isclose(react.iloc[60], -0.03)


def test_analyst_upgrades_and_target_changes_are_counted_over_a_month_and_a_quarter():
    a = analyst([("AAA", at(5), "Alpha", "up", 110.0, 100.0), ("AAA", at(30), "Beta", "down", 0.0, 0.0),
                 ("AAA", at(31), "Alpha", "main", 99.0, 110.0)])
    f = E.event_features(DAYS, ["AAA", "BBB"], NO_EARNINGS, a)
    net_m, net_q, tgt, cover = f["an_net_month"]["AAA"], f["an_net_quarter"]["AAA"], f["an_target_month"]["AAA"], f["an_cover"]["AAA"]
    assert np.isnan(net_m.iloc[4])                                 # before any analyst record: blank, not zero
    assert net_m.iloc[5] == 1 and net_m.iloc[25] == 1 and net_m.iloc[26] == 0     # the upgrade drops out after 21 trading days
    assert net_q.iloc[30] == 0 and net_q.iloc[31] == 0             # one up, one down inside the quarter
    assert np.isclose(tgt.iloc[5], 0.10) and np.isclose(tgt.iloc[31], -0.10)      # +10% then, a month later, -10%
    assert cover.iloc[5] == 1 and cover.iloc[30] == 2 and cover.iloc[31] == 3     # analyst notes in the last year
    assert f["an_busy_month"]["AAA"].iloc[31] == 2
    assert f["an_net_month"]["BBB"].isna().all()


def test_events_that_have_not_happened_yet_change_nothing_in_the_past():
    rng = np.random.default_rng(0)
    e_rows = [("AAA", at(int(d), "16:30"), float(rng.normal(1, .2)), float(rng.normal(0, 10))) for d in range(8, 290, 62)]
    a_rows = [("AAA", at(int(d), "09:00"), f"F{d % 4}", ["up", "down", "main"][d % 3], 100.0 + d, 100.0) for d in range(3, 295, 9)]
    cut = 150
    full = E.event_features(DAYS, ["AAA"], earnings(e_rows), analyst(a_rows))
    past = E.event_features(DAYS, ["AAA"], earnings([r for r in e_rows if r[1] < DAYS[cut]]), analyst([r for r in a_rows if r[1] < DAYS[cut]]))
    for name in full:
        assert np.allclose(full[name].iloc[:cut].to_numpy(), past[name].iloc[:cut].to_numpy(), equal_nan=True), name


def test_a_short_recent_window_gives_the_same_answer_for_today_as_the_full_history():
    rng = np.random.default_rng(1)
    e_rows = [("AAA", at(int(d), "07:30"), float(rng.normal(1, .2)), float(rng.normal(0, 10))) for d in range(8, 290, 62)]
    a_rows = [("AAA", at(int(d), "09:00"), f"F{d % 4}", ["up", "down", "main"][d % 3], 100.0 + d, 100.0) for d in range(160, 295, 9)]
    full = E.event_features(DAYS, ["AAA"], earnings(e_rows), analyst(a_rows))
    tail = E.event_features(DAYS[-280:], ["AAA"], earnings(e_rows), analyst(a_rows))    # the live engine keeps 420 days; a year is the longest look-back
    for name in full:
        assert np.allclose(full[name].iloc[-1].to_numpy(), tail[name].iloc[-1].to_numpy(), equal_nan=True), name
