"""A pretend-money stockbroker ("paper trading"). No real order ever leaves this computer.

It behaves like a real broker where it matters:
- an order fills at the NEXT price that arrives, never at a price already seen;
- every trade pays a small cost (slippage), so results are not flattered;
- you cannot spend cash you do not have, and you cannot sell shares you do not own.

The whole account is saved in a small database file, so it survives restarts.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

MIN_NOTIONAL = 1.0      # ignore orders worth less than a dollar


@dataclass
class Position:
    symbol: str
    qty: float
    avg_cost: float
    opened_at: float

    def unrealized(self, price: float) -> float:
        return (price - self.avg_cost) * self.qty


@dataclass
class Order:
    id: int
    symbol: str
    side: str                      # "buy" or "sell"
    qty: float
    reason: str                    # plain-English why
    tag: str
    created: float
    status: str = "pending"        # pending -> filled | rejected | cancelled
    ref_price: float | None = None # last price seen when the decision was made
    filled_qty: float = 0.0
    fill_price: float | None = None
    filled_at: float | None = None
    latency_ms: float | None = None
    note: str = ""


@dataclass
class Fill:
    order_id: int
    symbol: str
    side: str
    qty: float
    price: float
    ts: float
    cost: float                    # dollars lost to slippage on this fill
    realized_pnl: float            # profit booked (sells only)
    reason: str
    tag: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (key TEXT PRIMARY KEY, value REAL);
CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, qty REAL, avg_cost REAL, opened_at REAL);
CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, created REAL, body TEXT);
CREATE TABLE IF NOT EXISTS fills (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, body TEXT);
CREATE TABLE IF NOT EXISTS equity (ts REAL PRIMARY KEY, equity REAL, cash REAL, invested REAL);
"""


class PaperBroker:
    def __init__(self, cash: float, slippage_bps: float = 3.0, db_path: Path | None = None,
                 order_timeout: float = 120.0):
        self.slippage = slippage_bps / 1e4
        self.order_timeout = order_timeout
        self.cash = float(cash)
        self.starting_cash = float(cash)
        self.realized_pnl = 0.0
        self.costs_paid = 0.0
        self.positions: dict[str, Position] = {}
        self.last: dict[str, float] = {}
        self.pending: dict[int, Order] = {}
        self._next_id = 1
        self._db = None
        if db_path is not None:
            self._db = sqlite3.connect(str(db_path), check_same_thread=False)
            self._db.executescript("PRAGMA journal_mode=WAL;" + _SCHEMA)
            self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        acct = dict(self._db.execute("SELECT key, value FROM account").fetchall())
        if "cash" not in acct:                       # brand-new account
            self._save_account()
            return
        self.cash, self.starting_cash = acct["cash"], acct.get("starting_cash", self.starting_cash)
        self.realized_pnl, self.costs_paid = acct.get("realized_pnl", 0.0), acct.get("costs_paid", 0.0)
        self._next_id = int(acct.get("next_id", 1))
        for sym, qty, avg, opened in self._db.execute("SELECT symbol, qty, avg_cost, opened_at FROM positions"):
            self.positions[sym] = Position(sym, qty, avg, opened)
        for oid, body in self._db.execute("SELECT id, body FROM orders").fetchall():      # orders orphaned by a shutdown
            order = json.loads(body)
            if order.get("status") == "pending":
                order.update(status="cancelled", note="engine restarted before a price arrived")
                self._db.execute("UPDATE orders SET body=? WHERE id=?", (json.dumps(order), oid))
        self._db.commit()

    def _save_account(self) -> None:
        if self._db is None:
            return
        rows = [("cash", self.cash), ("starting_cash", self.starting_cash), ("realized_pnl", self.realized_pnl),
                ("costs_paid", self.costs_paid), ("next_id", float(self._next_id))]
        self._db.executemany("INSERT INTO account(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", rows)
        self._db.commit()

    def _save_position(self, symbol: str) -> None:
        if self._db is None:
            return
        p = self.positions.get(symbol)
        if p is None:
            self._db.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
        else:
            self._db.execute("INSERT INTO positions(symbol, qty, avg_cost, opened_at) VALUES(?,?,?,?) "
                             "ON CONFLICT(symbol) DO UPDATE SET qty=excluded.qty, avg_cost=excluded.avg_cost", (p.symbol, p.qty, p.avg_cost, p.opened_at))

    def _save_order(self, o: Order) -> None:
        if self._db is None:
            return
        self._db.execute("INSERT INTO orders(id, created, body) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                         (o.id, o.created, json.dumps(asdict(o))))
        self._db.commit()

    # ------------------------------------------------------------------ trading
    def submit(self, symbol: str, side: str, qty: float, reason: str, now: float, tag: str = "") -> Order:
        """Queue an order. It fills on the next price for that stock."""
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        order = Order(id=self._next_id, symbol=symbol, side=side, qty=float(qty), reason=reason, tag=tag,
                      created=now, ref_price=self.last.get(symbol))
        self._next_id += 1
        if qty <= 0:
            order.status, order.note = "rejected", "quantity must be positive"
        elif side == "sell" and symbol not in self.positions:
            order.status, order.note = "rejected", "no shares held to sell"
        else:
            self.pending[order.id] = order
        self._save_order(order)
        self._save_account()
        return order

    def on_tick(self, symbol: str, price: float, ts: float) -> list[Fill]:
        """A new price arrived. Remember it and fill any waiting orders for that stock."""
        if not price or price <= 0:
            return []
        self.last[symbol] = price
        fills = []
        for order in [o for o in self.pending.values() if o.symbol == symbol and o.created < ts]:
            fill = self._fill(order, price, ts)
            del self.pending[order.id]
            self._save_order(order)
            if fill:
                fills.append(fill)
        if fills:
            self._save_account()
        return fills

    def _fill(self, order: Order, price: float, ts: float) -> Fill | None:
        if order.side == "buy":
            px = price * (1 + self.slippage)
            qty = min(order.qty, self.cash / px)
            if qty * px < MIN_NOTIONAL:
                order.status, order.note = "rejected", "not enough cash"
                return None
            pos = self.positions.get(order.symbol)
            if pos:
                pos.avg_cost = (pos.avg_cost * pos.qty + px * qty) / (pos.qty + qty)
                pos.qty += qty
            else:
                self.positions[order.symbol] = Position(order.symbol, qty, px, ts)
            self.cash -= qty * px
            realized = 0.0
        else:
            pos = self.positions.get(order.symbol)
            if pos is None:
                order.status, order.note = "rejected", "no shares held to sell"
                return None
            px = price * (1 - self.slippage)
            qty = min(order.qty, pos.qty)
            realized = (px - pos.avg_cost) * qty
            self.cash += qty * px
            self.realized_pnl += realized
            pos.qty -= qty
            if pos.qty <= 1e-9:
                del self.positions[order.symbol]
        cost = abs(px - price) * qty
        self.costs_paid += cost
        order.status, order.filled_qty, order.fill_price, order.filled_at = "filled", qty, px, ts
        order.latency_ms = (ts - order.created) * 1000
        if qty < order.qty - 1e-9:
            order.note = "trimmed to what was available"
        fill = Fill(order.id, order.symbol, order.side, qty, px, ts, cost, realized, order.reason, order.tag)
        self._save_position(order.symbol)
        if self._db is not None:
            self._db.execute("INSERT INTO fills(ts, body) VALUES(?, ?)", (ts, json.dumps(asdict(fill))))
        return fill

    def expire(self, now: float) -> list[Order]:
        """Cancel orders that waited too long for a price (a halted or very quiet stock)."""
        dead = [o for o in self.pending.values() if now - o.created > self.order_timeout]
        for o in dead:
            o.status, o.note = "cancelled", "no price arrived in time"
            del self.pending[o.id]
            self._save_order(o)
        return dead

    def cancel_all(self, note: str = "cancelled") -> int:
        n = len(self.pending)
        for o in list(self.pending.values()):
            o.status, o.note = "cancelled", note
            self._save_order(o)
        self.pending.clear()
        return n

    # ------------------------------------------------------------------ reading the account
    def market_value(self) -> float:
        return sum(p.qty * self.last.get(s, p.avg_cost) for s, p in self.positions.items())

    def equity(self) -> float:
        return self.cash + self.market_value()

    def weights(self) -> dict[str, float]:
        eq = self.equity()
        return {s: p.qty * self.last.get(s, p.avg_cost) / eq for s, p in self.positions.items()} if eq > 0 else {}

    def record_equity(self, ts: float) -> None:
        if self._db is None:
            return
        mv = self.market_value()
        self._db.execute("INSERT OR REPLACE INTO equity(ts, equity, cash, invested) VALUES(?,?,?,?)", (ts, self.cash + mv, self.cash, mv))
        self._db.commit()

    def equity_history(self, since: float = 0.0, max_points: int = 2000) -> list[tuple[float, float]]:
        if self._db is None:
            return []
        rows = self._db.execute("SELECT ts, equity FROM equity WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
        step = max(1, len(rows) // max_points)
        return rows[::step] + (rows[-1:] if rows and (len(rows) - 1) % step else [])

    def recent_fills(self, n: int = 100) -> list[Fill]:
        if self._db is None:
            return []
        rows = self._db.execute("SELECT body FROM fills ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [Fill(**json.loads(r[0])) for r in rows]

    def recent_orders(self, n: int = 100) -> list[Order]:
        if self._db is None:
            return []
        rows = self._db.execute("SELECT body FROM orders ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [Order(**json.loads(r[0])) for r in rows]

    def trade_stats(self) -> dict:
        """Win rate over completed sells."""
        sells = [f for f in self.recent_fills(100_000) if f.side == "sell"]
        wins = [f for f in sells if f.realized_pnl > 0]
        return {"closed": len(sells), "wins": len(wins), "win_rate": len(wins) / len(sells) if sells else None,
                "avg_win": sum(f.realized_pnl for f in wins) / len(wins) if wins else None,
                "avg_loss": (sum(f.realized_pnl for f in sells if f.realized_pnl <= 0) / (len(sells) - len(wins))) if len(sells) > len(wins) else None}

    def reset(self, cash: float) -> None:
        """Wipe everything and start again with fresh pretend cash."""
        self.cash = self.starting_cash = float(cash)
        self.realized_pnl = self.costs_paid = 0.0
        self.positions.clear(); self.pending.clear()
        if self._db is not None:
            for table in ("positions", "orders", "fills", "equity", "account"):
                self._db.execute(f"DELETE FROM {table}")
            self._db.commit()
        self._save_account()
