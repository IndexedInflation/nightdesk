"""The pretend-money broker must do its sums exactly like a real one."""
import pytest

from nightdesk.broker import PaperBroker


def _broker(cash=10_000.0, slip=10.0, **kw):
    return PaperBroker(cash=cash, slippage_bps=slip, **kw)


def test_an_order_waits_for_the_next_price_before_it_fills():
    b = _broker()
    b.on_tick("AAA", 100.0, ts=1.0)
    order = b.submit("AAA", "buy", qty=10, reason="test", now=2.0)
    assert order.status == "pending" and b.positions == {}
    fills = b.on_tick("AAA", 101.0, ts=3.0)
    assert len(fills) == 1 and order.status == "filled"


def test_buy_pays_the_price_plus_slippage_and_cash_drops_by_exactly_that():
    b = _broker(slip=10.0)                                  # 10 hundredths of a percent = 0.1%
    b.submit("AAA", "buy", qty=10, reason="test", now=0.0)
    b.on_tick("AAA", 100.0, ts=1.0)
    assert b.positions["AAA"].qty == 10
    assert b.positions["AAA"].avg_cost == pytest.approx(100.10)
    assert b.cash == pytest.approx(10_000 - 1001.0)


def test_sell_receives_slightly_less_and_books_the_profit():
    b = _broker(slip=10.0)
    b.submit("AAA", "buy", qty=10, reason="in", now=0.0); b.on_tick("AAA", 100.0, ts=1.0)
    b.submit("AAA", "sell", qty=10, reason="out", now=2.0); fills = b.on_tick("AAA", 110.0, ts=3.0)
    assert "AAA" not in b.positions
    assert fills[0].price == pytest.approx(109.89)
    assert fills[0].realized_pnl == pytest.approx((109.89 - 100.10) * 10)
    assert b.cash == pytest.approx(10_000 - 1001.0 + 1098.9)
    assert b.realized_pnl == pytest.approx(97.9)


def test_adding_to_a_position_averages_the_cost():
    b = _broker(slip=0.0)
    b.submit("AAA", "buy", qty=10, reason="a", now=0.0); b.on_tick("AAA", 100.0, ts=1.0)
    b.submit("AAA", "buy", qty=30, reason="b", now=2.0); b.on_tick("AAA", 120.0, ts=3.0)
    assert b.positions["AAA"].qty == 40 and b.positions["AAA"].avg_cost == pytest.approx(115.0)


def test_a_buy_bigger_than_the_cash_is_trimmed_and_an_unaffordable_one_is_rejected():
    b = _broker(cash=1_000.0, slip=0.0)
    big = b.submit("AAA", "buy", qty=50, reason="too big", now=0.0); b.on_tick("AAA", 100.0, ts=1.0)
    assert big.status == "filled" and big.filled_qty == pytest.approx(10.0) and b.cash == pytest.approx(0.0)
    broke = b.submit("BBB", "buy", qty=1, reason="no cash", now=2.0); b.on_tick("BBB", 50.0, ts=3.0)
    assert broke.status == "rejected" and "cash" in broke.note


def test_selling_more_than_held_sells_only_what_is_held_and_never_goes_short():
    b = _broker(slip=0.0)
    b.submit("AAA", "buy", qty=5, reason="in", now=0.0); b.on_tick("AAA", 100.0, ts=1.0)
    order = b.submit("AAA", "sell", qty=9, reason="out", now=2.0); b.on_tick("AAA", 100.0, ts=3.0)
    assert order.filled_qty == 5 and "AAA" not in b.positions
    nothing = b.submit("ZZZ", "sell", qty=1, reason="none held", now=4.0); b.on_tick("ZZZ", 10.0, ts=5.0)
    assert nothing.status == "rejected"


def test_equity_is_cash_plus_holdings_at_the_latest_price():
    b = _broker(slip=0.0)
    b.submit("AAA", "buy", qty=10, reason="in", now=0.0); b.on_tick("AAA", 100.0, ts=1.0)
    b.on_tick("AAA", 150.0, ts=2.0)
    assert b.equity() == pytest.approx(9_000 + 1_500)
    assert b.positions["AAA"].unrealized(150.0) == pytest.approx(500.0)


def test_orders_record_how_long_they_waited_and_the_price_seen_when_decided():
    b = _broker()
    b.on_tick("AAA", 100.0, ts=10.0)
    order = b.submit("AAA", "buy", qty=1, reason="timing", now=10.5)
    b.on_tick("AAA", 100.5, ts=10.75)
    assert order.ref_price == 100.0 and order.latency_ms == pytest.approx(250.0)


def test_an_order_with_no_price_for_too_long_is_cancelled():
    b = _broker(order_timeout=60.0)
    order = b.submit("AAA", "buy", qty=1, reason="quiet stock", now=0.0)
    b.expire(now=30.0); assert order.status == "pending"
    b.expire(now=61.0); assert order.status == "cancelled"
    assert b.on_tick("AAA", 100.0, ts=62.0) == []


def test_account_survives_a_restart(tmp_path):
    db = tmp_path / "acct.db"
    b = _broker(slip=0.0, db_path=db)
    b.submit("AAA", "buy", qty=10, reason="in", now=0.0); b.on_tick("AAA", 100.0, ts=1.0)
    b.submit("AAA", "sell", qty=4, reason="trim", now=2.0); b.on_tick("AAA", 105.0, ts=3.0)
    again = PaperBroker(cash=999.0, slippage_bps=0.0, db_path=db)       # starting cash is ignored once an account exists
    assert again.cash == pytest.approx(b.cash)
    assert again.positions["AAA"].qty == 6 and again.positions["AAA"].avg_cost == pytest.approx(100.0)
    assert again.realized_pnl == pytest.approx(20.0)
    assert len(again.recent_fills(10)) == 2


def test_reset_wipes_the_account_back_to_starting_cash(tmp_path):
    b = _broker(slip=0.0, db_path=tmp_path / "acct.db")
    b.submit("AAA", "buy", qty=10, reason="in", now=0.0); b.on_tick("AAA", 100.0, ts=1.0)
    b.reset(cash=50_000.0)
    assert b.cash == 50_000.0 and b.positions == {} and b.recent_fills(10) == [] and b.realized_pnl == 0.0


def test_orders_left_waiting_when_the_engine_stopped_are_cancelled_on_restart(tmp_path):
    db = tmp_path / "acct.db"
    b = _broker(slip=0.0, db_path=db)
    b.submit("AAA", "buy", qty=10, reason="never filled", now=0.0)
    again = PaperBroker(cash=10_000.0, slippage_bps=0.0, db_path=db)
    assert again.pending == {}
    assert again.recent_orders(1)[0].status == "cancelled"
    assert again.on_tick("AAA", 100.0, ts=5.0) == [] and again.positions == {}
