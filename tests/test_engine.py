"""The whole engine, driven by a made-up market: no internet, no real clock, a throwaway account."""
import asyncio
import json
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from nightdesk import api, config
from nightdesk import engine as E
from nightdesk.config import Settings

SYMS = [f"S{i:02d}" for i in range(30)]


class FakeScorer:
    """Stands in for the real scorer so the test needs no price history."""
    def __init__(self, close=100.0, before=100.0):
        self.prev_close = pd.Series(close, index=SYMS + ["SPY"])       # the latest finished day's closing prices
        self.prev2_close = pd.Series(before, index=SYMS + ["SPY"])     # and the day before that
        self.last_X = None
        self.as_of = pd.Timestamp("2026-10-12")

    def exposure(self, picks, settings, spy_last):
        return 1.0


def board(order=SYMS):
    return pd.DataFrame({"score": np.linspace(3, -3, len(order)), "raw": np.linspace(3, -3, len(order)), "rank": range(1, len(order) + 1)}, index=list(order))


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE", tmp_path)
    monkeypatch.setattr(E, "STATE", tmp_path)
    e = E.Engine()
    e.log = lambda msg: None
    e.settings = Settings(auto_trade=True, network_enabled=True, max_positions=5, hold_buffer=2.0, max_weight=0.25, max_sector_weight=1.0, min_trade_dollars=10, slippage_bps=0.0)
    e.broker.slippage = 0.0
    e.names, e.sectors = {s: f"Company {s}" for s in SYMS}, {s: "X" for s in SYMS}
    e.scorer, e.board, e.board_at, e.board_live, e.expected = FakeScorer(), board(), time.time(), True, 30
    e.ready, e.phase, e.day_key = True, "open", "2026-10-13"
    e.session_open_ts, e.session_close_ts = time.time() - 3600, time.time() + 3600
    return e


def tick(e, sym, price, fresh=True):
    tick.n += 1
    e._on_tick(sym, price, time.time(), time.time() + tick.n * 1e-4, 1e6 + tick.n, True, fresh)


tick.n = 0


def all_ticks(e, price=100.0, fresh=True):
    for s in SYMS + ["SPY"]:
        tick(e, s, price, fresh)


def invest(e):
    all_ticks(e)
    asyncio.run(e.rebalance("First purchase"))
    all_ticks(e)


def titles(e, kind):
    return [a["title"] for a in e.journal.recent(100, [kind])]


def test_from_cash_it_buys_the_five_best_and_explains_itself(eng):
    invest(eng)
    assert sorted(eng.broker.positions) == SYMS[:5] and not eng.broker.pending
    assert all(abs(w - 0.2) < 0.001 for w in eng.broker.weights().values())
    assert len(titles(eng, "trade")) == 5 and titles(eng, "decision") == ["First purchase"]
    assert "Bought 200.00 shares of Company S00 (S00) at $100.00" in titles(eng, "trade")
    json.dumps(api._json_safe(eng.snapshot()))                     # everything the window needs can be sent as JSON


def test_re_sent_old_prices_never_fill_an_order(eng):
    all_ticks(eng)
    asyncio.run(eng.rebalance("First purchase"))
    all_ticks(eng, fresh=False)
    assert eng.broker.positions == {} and len(eng.broker.pending) == 5
    all_ticks(eng)
    assert len(eng.broker.positions) == 5


def test_it_refuses_to_trade_on_a_partial_list(eng):
    all_ticks(eng)
    eng.board = board(SYMS[:10])                                   # only a third of the stocks have scores
    asyncio.run(eng.rebalance("First purchase"))
    assert eng.broker.pending == {} and eng.broker.positions == {}
    assert titles(eng, "system")[0] == "First purchase: skipped"


def test_a_holding_that_drops_down_the_rankings_is_swapped_for_the_next_best(eng):
    invest(eng)
    eng.board = board(SYMS[1:] + SYMS[:1])                         # S00 falls from first to last

    async def swap():
        job = asyncio.create_task(eng.rebalance("Daily check"))
        await asyncio.sleep(0.05)
        all_ticks(eng)                                             # the sale fills...
        await job
        all_ticks(eng)                                             # ...then the replacement is bought
    asyncio.run(swap())
    assert sorted(eng.broker.positions) == SYMS[1:6]
    assert any(t.startswith("Sold 200.00 shares of Company S00") for t in titles(eng, "trade"))


def test_a_stock_that_falls_past_its_stop_is_sold_at_once_and_left_alone(eng):
    invest(eng)
    tick(eng, "S00", 85.0)                                         # 15% under the $100 paid; the stop is 12%
    assert [o.symbol for o in eng.broker.pending.values()] == ["S00"]
    tick(eng, "S00", 84.5)
    assert "S00" not in eng.broker.positions and "S00" in eng.blocked()
    assert titles(eng, "risk") == ["Stop-loss on Company S00 (S00)"]
    assert eng.stop_us and eng.stop_us[0] < 50_000                 # reacted within a twentieth of a second


def test_the_safety_switch_sells_everything_and_turns_trading_off(eng):
    invest(eng)
    eng.settings.max_drawdown_limit = 0.25
    eng.peak_equity = eng.broker.equity() / 0.70                   # pretend the account is 30% off its high (the switch is at 25%)
    eng._risk()
    assert eng.killed and len(eng.broker.pending) == 5
    all_ticks(eng)
    assert eng.broker.positions == {} and titles(eng, "risk") == ["Safety switch tripped"]


def test_a_three_percent_loss_in_a_day_stops_new_buying_but_keeps_holdings(eng):
    invest(eng)
    all_ticks(eng, 96.5)                                           # holdings down 3.5%
    eng._risk()
    assert eng.halt_today and not eng.killed and len(eng.broker.positions) == 5
    eng.board = board(SYMS[1:] + SYMS[:1])

    async def check():
        job = asyncio.create_task(eng.rebalance("Daily check"))
        await asyncio.sleep(0.05); all_ticks(eng, 96.5); await job
    asyncio.run(check())
    assert len(eng.broker.pending) == 0 and len(eng.broker.positions) == 4       # sold the laggard, bought nothing


def test_pause_resume_and_reset_do_what_they_say(eng):
    invest(eng)
    assert eng.control("pause")["ok"] and not eng.settings.auto_trade
    assert not Settings.load().auto_trade                          # remembered across restarts
    assert eng.control("resume")["ok"] and eng.settings.auto_trade
    assert eng.control("reset", 50_000)["ok"]
    assert eng.broker.cash == 50_000 and eng.broker.positions == {} and titles(eng, "trade") == []
    assert titles(eng, "user") == ["Practice account reset"]              # the person's own actions are logged as theirs
    assert not eng.control("fly to the moon")["ok"]


def test_the_headline_reader_on_trial_is_recorded_but_not_obeyed(eng):
    eng.news_blocked = {"S00": {"title": "Company S00 cuts its outlook", "conf": 0.95, "ts": time.time(), "until": time.time() + 3600}}
    invest(eng)
    assert "S00" in eng.broker.positions                           # bought anyway
    assert eng.news.scoreboard({"S00": 100.0}, 100.0)["blocks"] == 1
    assert "would have blocked S00" in eng.journal.recent(5, ["decision"])[0]["detail"]


def test_the_headline_reader_switched_on_keeps_the_stock_out(eng):
    eng.settings.news_veto = True
    eng.news_blocked = {"S00": {"title": "Company S00 cuts its outlook", "conf": 0.95, "ts": time.time(), "until": time.time() + 3600}}
    invest(eng)
    assert sorted(eng.broker.positions) == SYMS[1:6]
    assert eng.news.scoreboard({"S00": 100.0}, 100.0)["blocks"] == 1
    assert "Skipped after bad news: S00" in eng.journal.recent(5, ["decision"])[0]["detail"]


# ---- the local web server the window talks to ----
@pytest.fixture
def web(eng, monkeypatch):
    monkeypatch.setattr(api, "engine", eng)
    return TestClient(api.app, base_url=f"http://127.0.0.1:{config.PORT}")


def test_the_server_only_answers_requests_addressed_to_this_mac(web):
    assert web.get("/api/health").status_code == 200
    assert web.get("/api/health", headers={"host": "evil.example.com"}).status_code == 403


def test_changes_need_the_app_header_so_a_stray_web_page_cannot_make_them(web, eng):
    assert web.post("/api/control", json={"action": "pause"}).status_code == 403
    assert eng.settings.auto_trade
    assert web.post("/api/control", json={"action": "pause"}, headers={"x-nightdesk": "1"}).json() == {"ok": True}
    assert not eng.settings.auto_trade


def test_settings_are_checked_before_they_are_saved(web, eng):
    h = {"x-nightdesk": "1"}
    assert web.post("/api/settings", json={"max_positions": 5000}, headers=h).status_code == 400
    assert web.post("/api/settings", json={"starting_cash": 1}, headers=h).status_code == 400          # not editable here
    assert web.post("/api/settings", json={"position_stop": "abc"}, headers=h).status_code == 400
    ok = web.post("/api/settings", json={"position_stop": 0.2, "max_positions": 12}, headers=h).json()
    assert ok["changed"] == ["position_stop", "max_positions"] and eng.settings.position_stop == 0.2


def test_state_board_activity_and_news_all_answer(web, eng):
    invest(eng)
    state = web.get("/api/state").json()
    assert state["account"]["positions"] == 5 and state["positions"][0]["name"].startswith("Company")
    rows = web.get("/api/board").json()["rows"]
    assert [r["rank"] for r in rows[:3]] == [1, 2, 3] and rows[0]["held"] is True
    assert len(web.get("/api/activity?kind=trade").json()) == 5
    assert web.get("/api/news").json()["veto_on"] is False
    assert web.get("/api/curve").json()["points"] == []


# ---- the clock: one trading day ending and the next beginning ----
from datetime import datetime

from nightdesk import mktcal

REAL_STATUS, REAL_LAST = mktcal.status, mktcal.last_completed_session      # kept before any test swaps them


def at(monkeypatch, year, month, day, hour, minute=0):
    """Pretend it is this moment in New York."""
    moment = datetime(year, month, day, hour, minute, tzinfo=mktcal.NY)
    monkeypatch.setattr(E.mktcal, "status", lambda now=None: REAL_STATUS(moment))
    monkeypatch.setattr(E.mktcal, "last_completed_session", lambda now=None: REAL_LAST(moment))
    return moment.timestamp()


def test_a_new_trading_day_gets_a_fresh_start_line_and_fresh_limits(eng, monkeypatch):
    eng.day_key = None
    at(monkeypatch, 2026, 10, 13, 15); eng._sync_clock()
    assert (eng.phase, eng.day_key) == ("open", "2026-10-13")
    eng.session_close_ts = time.time() + 3600; eng.session_open_ts = time.time() - 3600      # let the made-up ticks count as in-session
    invest(eng)
    all_ticks(eng, 97.0); eng._risk()
    assert eng.halt_today
    at(monkeypatch, 2026, 10, 13, 17); eng._sync_clock()
    assert (eng.phase, eng.day_key) == ("after", "2026-10-13") and eng.halt_today             # same day: still halted
    opened = at(monkeypatch, 2026, 10, 14, 8); eng._sync_clock()
    assert (eng.phase, eng.day_key) == ("pre", "2026-10-14")
    assert not eng.halt_today and eng.day_start_equity == pytest.approx(eng.broker.equity())  # today's profit starts from zero
    assert eng.session_open_ts == pytest.approx(opened + 1.5 * 3600) and eng.session_close_ts == pytest.approx(opened + 8 * 3600)
    assert eng.journal.get("day_start")["day"] == "2026-10-14"


def test_the_first_price_of_a_new_day_resets_that_days_open_high_and_low(eng, monkeypatch):
    all_ticks(eng, 100.0); tick(eng, "S00", 110.0)
    assert eng.quotes["S00"].high == 110.0
    eng.day_key = "2026-10-14"                                                             # the calendar moved on
    tick(eng, "S00", 105.0)
    q = eng.quotes["S00"]
    assert (q.open, q.high, q.low, q.day) == (105.0, 105.0, 105.0, "2026-10-14")
    assert "S01" not in eng._today_frame().index                                           # yesterday's prices are not today's


def test_weekends_show_the_last_real_trading_day(eng, monkeypatch):
    at(monkeypatch, 2026, 10, 17, 11); eng._sync_clock()                                   # a Saturday
    assert (eng.phase, eng.day_key) == ("closed", "2026-10-16")


class DeadFeed:
    lines_up: set = set()
    def seconds_since_message(self):
        return 300.0


def test_no_trading_while_the_price_feed_is_down(eng):
    all_ticks(eng)
    eng.stream = DeadFeed()
    asyncio.run(eng.rebalance("Daily check"))
    assert eng.broker.pending == {} and titles(eng, "system")[0] == "Daily check: skipped"
    assert "price feed" in eng.journal.recent(1, ["system"])[0]["detail"].lower()


def test_a_skipped_daily_check_is_not_marked_done_so_it_can_be_retried(eng):
    all_ticks(eng)
    eng.stream = DeadFeed()
    asyncio.run(eng._daily_check("2026-10-13:close", "Daily check before the close"))
    assert "2026-10-13:close" not in eng.rebalanced_days and eng._retry_after > time.time()
    eng.stream = None                                                                      # prices are back
    asyncio.run(eng._daily_check("2026-10-13:close", "Daily check before the close"))
    assert "2026-10-13:close" in eng.rebalanced_days and len(eng.broker.pending) == 5


def test_the_forecaster_is_due_for_retraining_after_a_month(eng):
    eng.scorer.as_of = pd.Timestamp("2026-11-20")
    eng.model = type("M", (), {"meta": {"trained_through": "2026-10-12"}})()
    assert eng.retrain_due()
    eng.model.meta["trained_through"] = "2026-11-10"
    assert not eng.retrain_due()


def test_after_hours_prices_do_not_move_the_account(eng):
    invest(eng)
    before = eng.broker.equity()
    eng._on_tick("S00", 140.0, time.time(), time.time(), 2e6, False, True)                  # an after-hours print, 40% higher
    assert eng.broker.equity() == before and eng.quotes["S00"].last == 100.0
    assert eng.snapshot()["positions"][0]["last"] == 100.0


def test_restarting_while_the_market_is_shut_values_holdings_at_the_last_close(eng, tmp_path):
    invest(eng)
    again = E.Engine()                                                                      # same account file, fresh memory
    again.log = lambda msg: None
    assert again.broker.equity() == pytest.approx(100_000)                                  # knows only what it paid
    again.scorer, again.phase, again.day_key = FakeScorer(close=110.0), "closed", "2026-10-13"
    again._adopt_closing_prices()
    assert again.broker.equity() == pytest.approx(110_000)                                  # 5 holdings, each up 10%


def test_live_prices_are_not_overwritten_by_yesterdays_close_while_the_market_is_open(eng):
    invest(eng)
    all_ticks(eng, 103.0)
    eng.scorer = FakeScorer(close=90.0)
    eng._adopt_closing_prices()
    assert eng.broker.equity() == pytest.approx(103_000)


def test_todays_change_is_measured_from_the_previous_close_even_after_the_bell(eng):
    invest(eng)
    all_ticks(eng, 105.0)
    assert eng.snapshot()["positions"][0]["day_pct"] == pytest.approx(0.05)                 # during the session: against yesterday's 100
    eng.phase = "after"
    eng.scorer = FakeScorer(close=105.0, before=100.0)                                      # history now includes today
    eng.scorer.as_of = pd.Timestamp(eng.day_key)
    assert eng.snapshot()["positions"][0]["day_pct"] == pytest.approx(0.05)                 # still +5%, not 0%


def test_trades_can_be_saved_as_a_spreadsheet(web, eng, tmp_path, monkeypatch):
    import csv
    monkeypatch.setattr(E, "ROOT", tmp_path)
    invest(eng)
    out = web.post("/api/export", json={"reveal": False}, headers={"x-nightdesk": "1"}).json()
    assert out["ok"] and out["rows"] == 5
    rows = list(csv.DictReader(open(out["path"])))
    assert {r["Stock"] for r in rows} == set(SYMS[:5]) and rows[0]["Side"] == "buy"
    assert float(rows[0]["Shares"]) == 200.0 and rows[0]["Company"].startswith("Company")


def test_daily_self_check_compares_the_live_ranking_with_the_official_one(eng):
    eng.board = board()                                            # what the engine believed at the closing bell
    eng._remember_live_board()
    official = board(SYMS[1:2] + SYMS[0:1] + SYMS[2:])             # official closing data swaps the top two
    result = eng._self_check(official)
    assert result["top20_same"] == 20 and 0.99 < result["agreement"] < 1.0
    assert eng.journal.get("self_check")["day"] == eng.day_key
    assert "20 of the top 20" in eng.journal.recent(1, ["system"])[0]["detail"]
    scrambled = board(list(reversed(SYMS)))
    eng._remember_live_board()
    assert eng._self_check(scrambled)["agreement"] < -0.9          # a badly wrong feed would show up here
    assert eng.journal.recent(1, ["risk"])[0]["title"] == "Self-check failed: live prices disagreed with official ones"


def test_a_fall_just_short_of_the_limit_does_not_trip_the_safety_switch(eng):
    invest(eng)
    eng.peak_equity = eng.broker.equity() / (1 - (eng.settings.max_drawdown_limit - 0.03))     # 3 points short of the switch
    eng._risk()
    assert not eng.killed and eng.broker.pending == {}


def test_each_risk_settings_safety_switch_sits_well_past_its_worst_fall_in_testing():
    """The switch sells everything, so it must never fire in a fall the setting has already been through in the fair test."""
    path = config.RESEARCH / "modes.json"
    if not path.exists():
        pytest.skip("no fair-test results on disk yet")
    tested = json.loads(path.read_text())
    for name, dial in config.RISK_MODES.items():
        worst = max(abs(tested[name]["max_drawdown"]), abs(tested[name].get("exam", {}).get("max_drawdown", 0.0)))
        assert dial["max_drawdown_limit"] >= 1.25 * worst, f"{name}: switch at {dial['max_drawdown_limit']:.0%} but it fell {worst:.0%} in testing"


def test_the_default_settings_are_one_of_the_three_risk_cards():
    s, dial = Settings(), config.RISK_MODES[config.DEFAULT_MODE]
    assert (s.target_vol, s.trend_filter, s.max_drawdown_limit) == (dial["target_vol"], dial["trend_filter"], dial["max_drawdown_limit"])


def test_one_wild_price_is_ignored_until_a_second_price_confirms_it(eng):
    invest(eng)
    tick(eng, "S00", 60.0)                                         # a 40% "fall" in one print: almost certainly a bad print
    assert eng.quotes["S00"].last == 100.0 and eng.broker.pending == {}       # not believed, so no stop-loss sale
    tick(eng, "S00", 100.2)                                        # the next print is normal: the wild one is forgotten
    assert eng.quotes["S00"].last == 100.2 and eng.broker.pending == {}
    tick(eng, "S01", 60.0); tick(eng, "S01", 60.5)                 # two in a row at the new level: it is real
    assert eng.quotes["S01"].last == 60.5
    assert [o.symbol for o in eng.broker.pending.values()] == ["S01"]         # and now the stop-loss acts


# ---- round two: clearer screens, fresher decisions, memory and speed on show ----
def test_the_daily_check_re_ranks_on_the_latest_prices_before_it_decides(eng):
    calls = []

    def score(today, frac, day):
        calls.append(len(today))
        return board(list(reversed(SYMS)))                         # the newest prices turn the ranking upside down

    eng.scorer.score = score
    all_ticks(eng)
    eng.board_at = time.time() - 8                                 # the ranking on screen is 8 seconds old
    asyncio.run(eng._daily_check("2026-10-13:close", "Daily check before the close"))
    assert calls == [31]                                           # ranked once, on all 30 stocks and the market
    assert sorted(o.symbol for o in eng.broker.pending.values()) == sorted(SYMS[-5:])
    assert eng.decision_ms and eng.board_live


def test_a_check_still_runs_if_the_fresh_ranking_fails(eng):
    def score(today, frac, day):
        raise RuntimeError("no prices")

    eng.scorer.score = score
    all_ticks(eng)
    eng.board_at = time.time() - 8
    asyncio.run(eng._daily_check("2026-10-13:close", "Daily check before the close"))
    assert len(eng.broker.pending) == 5 and "2026-10-13:close" in eng.rebalanced_days


def test_feed_delay_is_measured_only_while_new_york_is_open(eng, monkeypatch):
    eng.stream = type("Stream", (), {"measure": True, "lines_up": {0}})()
    at(monkeypatch, 2026, 10, 13, 17)                              # an hour after the closing bell
    eng._sync_clock()
    assert eng.stream.measure is False
    at(monkeypatch, 2026, 10, 14, 11)                              # mid-session next day
    eng._sync_clock()
    assert eng.stream.measure is True


def test_the_window_is_told_how_many_sessions_it_has_traded_and_how_big_the_model_is(eng):
    eng.model = type("M", (), {"meta": {"trained_through": "2026-10-12"}, "feature_names": ["a"] * 71,
                               "spec": type("S", (), {"horizons": (5, 21), "seeds": (11, 22, 33)})()})()
    eng.journal.mark(1_791_000_000.0, 100_000.0, 500.0)            # one session ...
    eng.journal.mark(1_791_000_900.0, 100_100.0, 501.0)
    eng.journal.mark(1_791_090_000.0, 100_200.0, 502.0)            # ... and the next day
    eng.memory_mb = 512.0
    snap = eng.snapshot()
    assert snap["account"]["sessions"] == 2
    assert snap["engine"]["model"]["signals"] == 71 and snap["engine"]["model"]["members"] == 8
    assert snap["engine"]["memory_mb"] == 512.0 and snap["engine"]["score_every"] == E.SCORE_EVERY


def test_last_sessions_speed_is_kept_for_when_the_market_is_shut(eng):
    eng.stream = type("Stream", (), {"measure": True, "connected": True, "messages": 10, "fresh": 9, "symbols": SYMS, "reconnects": 0,
                                     "lines": [1], "lines_up": {0}, "lag_stats": lambda self: {"median_ms": 1800, "p90_ms": 3100, "samples": 500},
                                     "seconds_since_message": lambda self: 1.0})()
    eng.decision_ms.append(400.0)
    eng._end_of_day()
    eng.stream.lag_stats = lambda: {"median_ms": None, "p90_ms": None, "samples": 0}       # after a restart nothing has been measured yet
    eng.decision_ms.clear()
    snap = eng.snapshot()
    assert snap["feed"]["median_ms"] == 1800 and snap["latency"]["decision_ms"] == 400.0
    assert snap["feed"]["measured_on"] == "2026-10-13"


def test_a_nearly_full_portfolio_is_not_described_as_holding_cash(eng):
    eng.scorer.exposure = lambda picks, settings, spy_last: 0.99
    invest(eng)
    assert "cash" not in eng.journal.recent(1, ["decision"])[0]["detail"].lower()


def test_fully_invested_mode_can_be_chosen(web, eng):
    h = {"x-nightdesk": "1"}
    ok = web.post("/api/settings", json={"target_vol": 1.0, "trend_filter": False}, headers=h)
    assert ok.status_code == 200 and eng.settings.target_vol == 1.0 and eng.settings.trend_filter is False


def test_reset_to_tested_settings_puts_dials_back_but_leaves_trading_paused(web, eng):
    h = {"x-nightdesk": "1"}
    web.post("/api/control", json={"action": "pause"}, headers=h)
    web.post("/api/settings", json={"position_stop": 0.3, "target_vol": 1.0}, headers=h)
    assert web.post("/api/settings/defaults", json={}).status_code == 403                  # needs the app header like any change
    out = web.post("/api/settings/defaults", json={}, headers=h).json()
    fresh = Settings()
    assert eng.settings.position_stop == fresh.position_stop and eng.settings.target_vol == fresh.target_vol
    assert eng.settings.max_positions == fresh.max_positions and "position_stop" in out["changed"]
    assert eng.settings.auto_trade is False                                                # a reset never switches trading back on


def test_the_window_can_only_open_three_known_things(web, eng, monkeypatch, tmp_path):
    opened = []
    page = tmp_path / "How-Nightdesk-Works.html"
    page.write_text("<html></html>")
    monkeypatch.setattr(api, "OPENABLE", {"explainer": page, "log": tmp_path / "missing.log", "folder": tmp_path})
    monkeypatch.setattr(api, "_open", lambda path, reveal: opened.append((path, reveal)))
    h = {"x-nightdesk": "1"}
    assert web.post("/api/open", json={"what": "explainer"}, headers=h).json() == {"ok": True}
    assert web.post("/api/open", json={"what": "/etc/passwd"}, headers=h).status_code == 404
    assert web.post("/api/open", json={"what": "log"}, headers=h).status_code == 404         # nothing there yet
    assert web.post("/api/open", json={"what": "explainer"}).status_code == 403
    assert opened == [(page, False)]


def test_research_answers_include_round_two_files(web, eng, monkeypatch, tmp_path):
    (tmp_path / "lab.json").write_text('{"rows": [{"name": "base"}]}')
    (tmp_path / "modes.json").write_text('{"steady": {"cagr": 0.12}}')
    monkeypatch.setattr(api, "RESEARCH", tmp_path)
    out = web.get("/api/research").json()
    assert out["lab"]["rows"][0]["name"] == "base" and out["modes"]["steady"]["cagr"] == 0.12
    assert out["reader_resources"] is None and out["latency"] is None                      # missing files are simply empty
    got = web.get("/api/settings").json()
    assert set(got["modes"]) == {"cautious", "steady", "full"} and got["default_mode"] in got["modes"]
    assert all(api.LIMITS[k][0] <= v <= api.LIMITS[k][1] for dial in got["modes"].values() for k, v in dial.items() if k in api.LIMITS)   # every card can be chosen


def test_memory_reading_is_parsed_from_the_mac_tool():
    from nightdesk import sysinfo
    text = "======\npython [22856]: 64-bit    Footprint: 518 MB (16384 bytes per page)\n======\n"
    assert sysinfo.parse_footprint(text) == 518.0
    assert sysinfo.parse_footprint("python [1]: 64-bit    Footprint: 2.4 GB (16384 bytes per page)") == 2400.0
    assert sysinfo.parse_footprint("python [1]: 64-bit    Footprint: 900 KB") == 0.9
    assert sysinfo.parse_footprint("no such process") is None


def test_it_will_not_trade_on_measurements_that_are_missing_yesterday(eng):
    all_ticks(eng)
    eng.scorer.as_of = pd.Timestamp("2026-10-09")                  # the history stops two sessions back: Monday's close never arrived
    asyncio.run(eng.rebalance("Daily check"))
    assert eng.broker.pending == {} and titles(eng, "system")[0] == "Daily check: skipped"
    assert "closing prices" in eng.journal.recent(1, ["system"])[0]["detail"]
    eng.scorer.as_of = pd.Timestamp("2026-10-12")                  # it arrived
    asyncio.run(eng.rebalance("Daily check"))
    assert len(eng.broker.pending) == 5


def test_fetching_missing_history_in_the_background_never_leaves_the_engine_stuck(eng):
    calls = []

    async def broken():
        calls.append(1)
        raise OSError("offline")

    eng._refresh_history = broken
    asyncio.run(eng._refresh_in_background())                      # a failure is logged, not raised ...
    assert calls == [1] and eng._refreshing is False               # ... and the next attempt is not blocked


def test_pause_during_a_portfolio_check_prevents_later_replacement_orders(eng):
    invest(eng)
    eng.board = board(SYMS[1:] + SYMS[:1])
    async def paused_check():
        task = asyncio.create_task(eng.rebalance("Daily check"))
        await asyncio.sleep(0.05)
        eng.control("pause")
        await task
    asyncio.run(paused_check())
    assert eng.broker.pending == {} and not eng.settings.auto_trade
