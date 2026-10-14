"""The night-shift worker. Watches live prices, re-scores every stock twice a minute,
and trades the practice account by the tested rules.

One process, one event loop. Nothing here blocks: slow jobs (downloads, scoring) run on
helper threads so a price never waits behind them.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import subprocess
import sys
import time
from collections import deque
from datetime import datetime

import pandas as pd

from . import data, explain, features as F, mktcal, research, sysinfo, universe
from .broker import Fill, PaperBroker
from .config import MODELS, ROOT, STATE, Settings
from .feed import YahooStream
from .model import Ensemble
from .news import NewsDesk, Reader
from .portfolio import choose_holdings
from .store import Journal
from .strategy import LiveScorer, plan_orders, risk_check

SCORE_EVERY = 10.0          # seconds between full re-scores while the market is open (one takes about half a second)
MEMORY_EVERY = 120.0        # seconds between readings of the engine's own memory use
MARK_EVERY = 15.0           # seconds between points on the account-value curve
COOLDOWN_DAYS = 5           # after a stop-loss, leave that stock alone for a week
NEWS_EVERY = 900.0          # seconds between headline checks while the market is open
RETRAIN_AFTER_DAYS = 30     # re-teach the forecaster when it is a month behind
WILD_MOVE = 0.20            # a single print this far from the last one must be confirmed by a second


class Quote:
    __slots__ = ("last", "exch", "recv", "open", "high", "low", "volume", "day", "suspect")

    def __init__(self):
        self.last = self.exch = self.recv = self.open = self.high = self.low = self.suspect = None
        self.volume = 0.0
        self.day = None


def _median(values) -> float | None:
    v = sorted(values)
    return v[len(v) // 2] if v else None


class Engine:
    def __init__(self):
        self.settings = Settings.load()
        db = STATE / "nightdesk.db"
        self.broker = PaperBroker(self.settings.starting_cash, self.settings.slippage_bps, db)
        self.journal = Journal(db)
        self.sectors: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.model: Ensemble | None = None
        self.scorer: LiveScorer | None = None
        self.stream: YahooStream | None = None
        self.quotes: dict[str, Quote] = {}
        self.board: pd.DataFrame | None = None
        self.board_at = 0.0
        self.board_live = False
        self.reasons: dict[str, dict] = {}
        self.ready = False
        self.message = "Starting up"
        self.phase = "starting"
        self.day_key: str | None = None
        self.session_open_ts = self.session_close_ts = 0.0
        self.day_start_equity = self.journal.get("day_start", {}).get("equity", self.broker.equity())
        self.peak_equity = self.journal.get("peak_equity", self.broker.equity())
        self.halt_today = False
        self.killed = bool(self.journal.get("killed", False))
        self.cooldown: dict[str, float] = self.journal.get("cooldown", {})
        self.news = NewsDesk(self.journal.db, Reader(self.settings.reader_python, self.settings.reader_model, self.settings.reader_label_order))
        self.news_blocked: dict[str, dict] = {}
        self.rebalanced_days: set[str] = set(self.journal.get("rebalanced_days", []))
        self.closed_days: set[str] = set(self.journal.get("closed_days", []))
        self._stopping: set[str] = set()
        self._rebalancing = False
        self._history_checked = 0.0
        self.decision_ms: deque = deque(maxlen=200)
        self.fill_ms: deque = deque(maxlen=500)
        self.stop_us: deque = deque(maxlen=200)
        self.listeners: set[asyncio.Queue] = set()
        self.started_at = time.time()
        self.tasks: list[asyncio.Task] = []
        self._tick_count_at = (time.time(), 0)
        self.tick_rate = 0.0
        self.expected = 0               # how many stocks should be on a complete board
        self._last_rebalance = 0.0
        self._refills = 0
        self._checking = False
        self._retry_after = 0.0
        self._fills_seen = 0
        self._bell_board: tuple | None = None
        self._stats_cache: tuple = (-1, {})
        self._retraining = False
        self._history_warned: str | None = None
        self._refreshing = False
        self._scoring = False
        self.memory_mb: float | None = None
        self._memory_at = 0.0
        self._sessions: tuple = (None, 1)
        self.setup_phase = "waiting"
        self.setup_error = ""
        self.setup_progress = ""
        self.setup_task: asyncio.Task | None = None
        self.setup_process: asyncio.subprocess.Process | None = None
        self.retrain_process: asyncio.subprocess.Process | None = None
        self.runtime_tasks: list[asyncio.Task] = []
        self._trading_generation = 0

    # ------------------------------------------------------------------ helpers
    def log(self, msg: str) -> None:
        print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)

    def say(self, kind: str, title: str, detail: str = "", data: dict | None = None) -> None:
        """Write to the activity log (what the person reads) and the console."""
        self.journal.add(kind, title, detail, data)
        self.log(f"[{kind}] {title} {detail}")
        self._notify()

    def _notify(self) -> None:
        for q in list(self.listeners):
            if q.empty():
                q.put_nowait(1)

    def label(self, sym: str) -> str:
        name = self.names.get(sym)
        return f"{name} ({sym})" if name else sym

    # ------------------------------------------------------------------ start-up
    async def start(self) -> None:
        if not self.settings.network_enabled:
            self.phase = "setup"
            self.message = "Setup needed: allow public data downloads to train your own forecaster."
            return
        if not (MODELS / "production.json").exists() or not data.have_cache():
            self.phase = "setup"
            self.message = "Setup needed: download missing data and train the forecaster."
            return
        self.setup_task = asyncio.create_task(self._boot())
        self.tasks.append(self.setup_task)

    def _spawn_runtime(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        self.runtime_tasks.append(task)
        def finished(done):
            if done in self.tasks:
                self.tasks.remove(done)
            if done in self.runtime_tasks:
                self.runtime_tasks.remove(done)
        task.add_done_callback(finished)
        return task

    @staticmethod
    async def _terminate_process(process) -> None:
        if process and process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass

    async def stop(self) -> None:
        if self.stream:
            self.stream.stop()
        await self._terminate_process(self.setup_process)
        await self._terminate_process(self.retrain_process)
        await self.news.reader.close()
        tasks = list(self.tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.write_report()

    def setup_status(self) -> dict:
        busy = self.setup_task is not None and not self.setup_task.done()
        return {"phase": self.setup_phase, "busy": busy, "ready": self.ready,
                "network_enabled": self.settings.network_enabled,
                "model_available": (MODELS / "production.json").exists(),
                "history_available": data.have_cache(), "message": self.message,
                "progress": self.setup_progress, "error": self.setup_error,
                "reader_available": self.news.reader.available}

    def start_setup(self) -> dict:
        if not self.settings.network_enabled:
            raise ValueError("Allow public data downloads before starting setup.")
        if self.ready:
            return self.setup_status()
        if self.setup_task and not self.setup_task.done():
            return self.setup_status()
        self.setup_error = ""
        self.setup_phase = "preparing"
        self.message = "Preparing your local data and forecaster. This can take a long time."
        self.setup_task = asyncio.create_task(self._prepare())
        self.tasks.append(self.setup_task)
        self._notify()
        return self.setup_status()

    async def _prepare(self) -> None:
        try:
            self.setup_phase = "downloading_and_training"
            # Run downloads and training in a separate process so the UI stays responsive.
            env = {**os.environ, "NIGHTDESK_HOME": str(ROOT)}
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "nightdesk.setup_worker", env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            self.setup_process = proc
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                # Worker output contains only status text and provider symbols, never local paths.
                self.setup_progress = line.decode(errors="replace").strip()[:300]
                self._notify()
            if await proc.wait() != 0:
                raise RuntimeError("Download or training failed. Check your internet connection, available memory and installed dependencies, then retry.")
            await self._boot()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.setup_phase = "error"
            self.setup_error = "Setup could not finish. Check the local engine log for details, then retry."
            self.message = self.setup_error
            self.ready = False
            self._notify()
        finally:
            self.setup_process = None

    async def disconnect(self) -> None:
        self._trading_generation += 1
        self.broker.cancel_all("public data disabled")
        self.settings.network_enabled = False
        self.settings.auto_trade = False
        self.settings.save()
        await self.stop()
        self.tasks.clear()
        self.runtime_tasks.clear()
        self.setup_task = None
        self.stream = None
        self.ready = False
        self.setup_phase = "waiting"
        self.phase = "setup"
        self.message = "Offline. Public data downloads and practice trading are paused."
        self._notify()

    def _keep_awake(self) -> None:
        """Ask macOS not to sleep while this engine is running (the screen may still turn off)."""
        if self.settings.keep_awake and sys.platform == "darwin":
            try:
                subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass

    async def _boot(self) -> None:
        try:
            if not self.settings.network_enabled:
                self.phase = "setup"
                return
            self.setup_phase = "starting"
            self._keep_awake()
            self.say("system", "Engine started", "Loading the forecaster and price history.")
            self.sectors, self.names = await asyncio.to_thread(universe.sector_map), await asyncio.to_thread(universe.name_map)
            path = MODELS / "production.json"
            if not path.exists():
                self.setup_phase = "waiting"
                self.message = "No trained forecaster yet. Start Setup to download data and train locally."
                self.say("system", "No forecaster found", self.message)
                return
            self.model = await asyncio.to_thread(Ensemble.load, path)
            await self._refresh_history(force=True)
            symbols = sorted(set(F.tradable_columns(self.scorer.base)) | {"SPY", "QQQ"})
            self.stream = YahooStream(symbols, self._on_tick, self.log)
            self._sync_clock()
            self._spawn_runtime(self.stream.run())
            if self.phase == "open":
                await self._seed_today(symbols)                    # finish catching up BEFORE any scoring or trading starts
            for coroutine in (self._clock_loop(), self._score_loop(), self._mark_loop(), self._news_loop()):
                self._spawn_runtime(coroutine)
            self.ready = True
            self.setup_phase = "ready"
            self.message = "Running"
            through = self.model.meta.get("trained_through")
            self.say("system", "Ready", f"Watching {len(symbols)} symbols. Forecaster trained on prices up to {pd.Timestamp(through):%-d %B %Y}." if through else f"Watching {len(symbols)} symbols.")
        except Exception as exc:
            self.log(f"Start-up failed: {type(exc).__name__}: {exc}")
            self.setup_phase = "error"
            self.setup_error = "Could not start the forecaster or price feed. Check the local engine log, then retry Setup."
            self.message = self.setup_error
            self.ready = False
            if self.stream:
                self.stream.stop()
            for task in self.runtime_tasks:
                task.cancel()
            self._notify()

    async def _refresh_history(self, force: bool = False) -> None:
        """Make sure the daily price history includes the latest finished trading day."""
        if not self.settings.network_enabled:
            return
        want = pd.Timestamp(mktcal.last_completed_session())
        have = self.scorer.as_of if self.scorer else None
        if not force and have is not None and have >= want:
            return
        if not data.have_cache():
            self.message = "Downloading 20 years of price history (first run only, about 2 minutes)"
            await asyncio.to_thread(data.build_history, universe.symbols(), log=self.log)
        panel = await asyncio.to_thread(research.load_research_panel)
        if panel["close"].index[-1] < want:
            self.log(f"history ends {panel['close'].index[-1].date()}, need {want.date()}: updating")
            try:
                await asyncio.to_thread(data.update_history, universe.symbols(), self.log)
                panel = await asyncio.to_thread(research.load_research_panel)
            except Exception as exc:
                if self._history_warned != str(want.date()):           # say it once a day, then keep trying quietly
                    self._history_warned = str(want.date())
                    self.say("risk", "Could not get yesterday's closing prices", f"{exc or type(exc).__name__}. Trading waits until they arrive; trying again every two minutes.")
                else:
                    self.log(f"history update failed again: {type(exc).__name__}: {exc}")
        self.scorer = await asyncio.to_thread(LiveScorer, panel, self.sectors, self.model, 5e6, self.settings.score_smoothing)
        self.expected = int(F.universe_mask({f: df.iloc[-F.LIVE_WINDOW:] for f, df in panel.items()}).iloc[-1].sum())
        self.log(f"history ready through {self.scorer.as_of.date()}")
        self._adopt_closing_prices()
        if self.phase != "open":
            await self._score_closed(panel)
            if self._bell_board and self._bell_board[0] == str(self.scorer.as_of.date()):
                self._self_check(self.board)

    async def _refresh_in_background(self) -> None:
        """Fetch missing history without holding up the clock. Used while the market is open."""
        self._refreshing = True
        try:
            await self._refresh_history()
        except Exception as exc:
            self.log(f"history refresh failed: {type(exc).__name__}: {exc}")
        finally:
            self._refreshing = False

    def history_ok(self) -> bool:
        """Does the price history include the latest finished trading day? Without it every measurement is a day stale."""
        if self.scorer is None:
            return False
        if self.phase in ("open", "pre") and self.day_key:
            want = mktcal.previous_trading_day(datetime.fromisoformat(self.day_key).date())
        else:
            want = mktcal.last_completed_session()
        return self.scorer.as_of >= pd.Timestamp(want)

    def _adopt_closing_prices(self) -> None:
        """Value holdings at the last official close: always when the market is shut, and for any
        stock with no live price yet when it is open (so a restart never shows holdings at cost)."""
        for sym, px in self.scorer.prev_close.items():
            if px == px and (self.phase != "open" or sym not in self.broker.last):
                self.broker.last[sym] = float(px)

    def _reference_close(self):
        """The closing prices "today's change" is measured from."""
        if self.scorer is None:
            return {}
        includes_today = str(self.scorer.as_of.date()) == self.day_key      # after the bell, history already holds today
        return self.scorer.prev2_close if includes_today else self.scorer.prev_close

    async def _score_closed(self, panel=None) -> None:
        """Outside market hours: the board as of the last closing bell."""
        self.board = self.scorer.closed_board()
        self.board_at, self.board_live = time.time(), False
        await asyncio.to_thread(self._explain)

    async def _seed_today(self, symbols: list[str]) -> None:
        """Started mid-session: fetch today's one-minute bars so open/high/low/volume are right."""
        self.message = "Catching up on today's prices"
        bars = await asyncio.to_thread(data.intraday_bars, symbols, "1m", "1d", self.log)
        now = time.time()
        for sym, df in bars.items():
            day = df[df.index.tz_convert(mktcal.NY).strftime("%Y-%m-%d") == self.day_key]
            if not len(day):
                continue
            q = self.quotes.setdefault(sym, Quote())
            if q.day != self.day_key:
                q.day, q.open, q.high, q.low = self.day_key, float(day["open"].iloc[0]), float(day["high"].max()), float(day["low"].min())
                q.last = q.last or float(day["close"].iloc[-1])
                q.volume = max(q.volume or 0.0, float(day["volume"].sum()))
                q.exch = q.exch or day.index[-1].timestamp()
                q.recv = q.recv or now
            else:
                q.open = float(day["open"].iloc[0])
                q.high, q.low = max(q.high, float(day["high"].max())), min(q.low, float(day["low"].min()))
            self.broker.last[sym] = q.last                    # today's price beats yesterday's close
        self.log(f"seeded {len(bars)} symbols from one-minute bars")

    # ------------------------------------------------------------------ the fast path: one price arrives
    def _on_tick(self, sym: str, price: float, exch: float, recv: float, day_volume: float, regular: bool, fresh: bool = True) -> None:
        if not self.settings.network_enabled:
            return
        if not (regular and self.session_open_ts <= exch <= self.session_close_ts + 90):
            return                          # pre-market and after-hours prints never touch the account
        q = self.quotes.get(sym)
        if q is None:
            q = self.quotes[sym] = Quote()
        if fresh and q.last and q.day == self.day_key and abs(price / q.last - 1) > WILD_MOVE:
            if q.suspect is None or abs(price / q.suspect - 1) > 0.05:
                q.suspect = price           # one print far from the last: hold it back until a second agrees
                return
        q.suspect = None
        q.last, q.exch, q.recv = price, exch, recv
        if q.day != self.day_key:
            q.day, q.open, q.high, q.low, q.volume = self.day_key, price, price, price, day_volume
        else:
            if q.open is None:
                q.open = price
            q.high = price if q.high is None or price > q.high else q.high
            q.low = price if q.low is None or price < q.low else q.low
            if day_volume > q.volume:
                q.volume = day_volume
        if not fresh:                       # a re-sent old print: fine for display, but never fill or stop on it
            self.broker.last[sym] = price
            return
        for fill in self.broker.on_tick(sym, price, recv):
            self._on_fill(fill)
        pos = self.broker.positions.get(sym)
        if pos and sym not in self._stopping and self.settings.auto_trade and price <= pos.avg_cost * (1 - self.settings.position_stop):
            self._stopping.add(sym)
            self.broker.submit(sym, "sell", pos.qty, f"Sold: it fell {price / pos.avg_cost - 1:.1%} below what was paid, past the {self.settings.position_stop:.0%} stop.", now=recv, tag="stop")
            self.stop_us.append((time.time() - recv) * 1e6)
            self.cooldown[sym] = recv + COOLDOWN_DAYS * 86400
            self.journal.set("cooldown", self.cooldown)
            self.say("risk", f"Stop-loss on {self.label(sym)}", f"Price ${price:,.2f} is {price / pos.avg_cost - 1:.1%} below the ${pos.avg_cost:,.2f} paid. Selling, and leaving it alone for {COOLDOWN_DAYS} days.")

    def _on_fill(self, fill: Fill) -> None:
        self.fill_ms.append((fill.ts - next((o.created for o in self.broker.recent_orders(50) if o.id == fill.order_id), fill.ts)) * 1000)
        self._stopping.discard(fill.symbol)
        self._fills_seen += 1
        verb = "Bought" if fill.side == "buy" else "Sold"
        why = fill.reason.split(": ", 1)[-1]
        why = why[:1].upper() + why[1:]
        extra = f" Trading cost ${fill.cost:,.2f}."
        if fill.side == "sell":
            extra += f" {'Profit' if fill.realized_pnl >= 0 else 'Loss'} booked: ${abs(fill.realized_pnl):,.2f}."
        self.say("trade", f"{verb} {fill.qty:,.2f} shares of {self.label(fill.symbol)} at ${fill.price:,.2f}", why + extra,
                 {"symbol": fill.symbol, "side": fill.side, "qty": fill.qty, "price": fill.price, "pnl": fill.realized_pnl, "tag": fill.tag})

    # ------------------------------------------------------------------ the clock
    def _sync_clock(self) -> dict:
        st = mktcal.status()
        self.phase = st["phase"]
        if st["phase"] in ("pre", "open"):
            day_key = st["session_open"][:10]
        else:
            day_key = mktcal.last_completed_session().isoformat() if st["phase"] == "closed" else st["now_ny"][:10]
        if st["phase"] in ("pre", "open") and day_key != self.day_key:
            o, c = mktcal.session(datetime.fromisoformat(st["session_open"]).date())
            self.session_open_ts, self.session_close_ts = o.timestamp(), c.timestamp()
            saved = self.journal.get("day_start", {})
            if saved.get("day") == day_key:
                self.day_start_equity = saved["equity"]
            else:
                self.day_start_equity = self.broker.equity()
                self.journal.set("day_start", {"day": day_key, "equity": self.day_start_equity})
            self.halt_today = False
            self._refills = 0
            self._stopping.clear()
        elif st["phase"] in ("after", "closed") and self.session_close_ts == 0:
            o, c = mktcal.session(datetime.fromisoformat(day_key).date()) if st["phase"] == "after" else (None, None)
            if o:
                self.session_open_ts, self.session_close_ts = o.timestamp(), c.timestamp()
        self.day_key = day_key
        if self.stream is not None:
            self.stream.measure = st["phase"] == "open"       # late re-prints after the bell would make the feed look slow
        return st

    async def _clock_loop(self) -> None:
        last_phase = self.phase
        while True:
            try:
                st = self._sync_clock()
                now = time.time()
                if st["phase"] != last_phase:
                    names = {"open": "New York opened", "after": "New York closed for the day", "pre": "New York opens later today", "closed": "Market closed"}
                    self.say("system", names[st["phase"]], f"Local time {datetime.now().astimezone():%I:%M %p %Z}.".replace("AM", "am").replace("PM", "pm"))
                    last_phase = st["phase"]
                if now - self._memory_at > MEMORY_EVERY:
                    self._memory_at = now
                    self.memory_mb = await asyncio.to_thread(sysinfo.footprint_mb, os.getpid())
                count_t, count_n = self._tick_count_at
                if self.stream and now - count_t >= 5:
                    self.tick_rate = (self.stream.messages - count_n) / (now - count_t)
                    self._tick_count_at = (now, self.stream.messages)
                if st["phase"] == "open" and self.ready:
                    for o in self.broker.expire(now):
                        self._stopping.discard(o.symbol)
                        self.say("system", f"Order for {self.label(o.symbol)} cancelled", "No price arrived for two minutes.")
                    self._risk()
                    fresh = self.board is not None and self.board_live and now - self.board_at < 3 * SCORE_EVERY
                    mins = mktcal.minutes_to_close()
                    if fresh and self.settings.auto_trade and not self.killed and not self._rebalancing and not self._checking and now >= self._retry_after:
                        if not self.broker.positions and not self.broker.pending and self.day_key not in self.rebalanced_days:
                            self._spawn_runtime(self._daily_check(self.day_key, "First purchase"))       # once a day at most, then the close check
                        elif mins is not None and 1.5 <= mins <= self.settings.rebalance_minutes_before_close and self.day_key + ":close" not in self.rebalanced_days:
                            self._spawn_runtime(self._daily_check(self.day_key + ":close", "Daily check before the close"))
                        elif (len(self.broker.positions) < 0.75 * self.settings.max_positions and not self.broker.pending and not self.halt_today
                              and now - self._last_rebalance > 300 and self._refills < 3):
                            self._refills += 1                              # orders went unfilled earlier: try again, a few times at most
                            self._spawn_runtime(self._check("Filling empty slots", final=False))
                if st["phase"] == "after" and self.day_key not in self.closed_days and self.ready and now >= self.session_close_ts + 120:
                    self.closed_days.add(self.day_key)
                    self.journal.set("closed_days", sorted(self.closed_days)[-30:])
                    self.broker.cancel_all("market closed")
                    self._end_of_day()
                stale = self.ready and not self.history_ok()            # yesterday's prices missing: keep trying, market open or not
                if st["phase"] == "open" and stale and not self._refreshing and now - self._history_checked > 120:
                    self._history_checked = now
                    self._spawn_runtime(self._refresh_in_background())   # never hold up the clock (and its safety checks) while trading hours run
                if st["phase"] != "open" and self.ready and not self._refreshing and now - self._history_checked > (120 if stale else 600):
                    self._history_checked = now
                    await self._refresh_history()
                    far_from_open = st["phase"] != "pre" or st["seconds_to_change"] > 2 * 3600
                    if far_from_open and self.retrain_due() and not self._retraining:
                        self._spawn_runtime(self._retrain())
                self._notify()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log(f"clock loop error: {type(exc).__name__}: {exc}")
            await asyncio.sleep(1.0)

    def _risk(self) -> None:
        eq = self.broker.equity()
        if eq > self.peak_equity:
            self.peak_equity = eq
            self.journal.set("peak_equity", eq)
        risk = risk_check({}, eq, self.day_start_equity, self.peak_equity, self.settings)
        if risk.halt_today and not self.halt_today:
            self.halt_today = True
            self.say("risk", "Buying paused for today", risk.notes[0])
        if risk.kill and not self.killed and self.settings.auto_trade:
            self.killed = True
            self.journal.set("killed", True)
            self.say("risk", "Safety switch tripped", risk.notes[-1] + " Auto-trading stays off until you turn it back on in Settings.")
            self._sell_everything("Sold: the account's safety switch tripped.")

    def _sell_everything(self, reason: str) -> int:
        self.broker.cancel_all("replaced by sell-everything")
        now = time.time()
        for sym, pos in list(self.broker.positions.items()):
            self.broker.submit(sym, "sell", pos.qty, reason, now=now, tag="close")
        return len(self.broker.positions)

    def _remember_live_board(self) -> None:
        """Keep the ranking as it stood at the closing bell, to audit against official data later."""
        if self.board is not None:
            self._bell_board = (self.day_key, self.board[["score", "raw"]].copy())

    def _self_check(self, official: pd.DataFrame) -> dict | None:
        """Did the live feed tell the truth? Compare the ranking made from live prices at the bell
        with the ranking made later from the exchange's official closing prices."""
        if self._bell_board is None:
            return None
        day, live = self._bell_board
        self._bell_board = None
        both = live.index.intersection(official.index)
        if len(both) < 20:
            return None
        agreement = float(live.loc[both, "raw"].rank().corr(official.loc[both, "raw"].rank()))
        same = len(set(live.sort_values("score", ascending=False).index[:20]) & set(official.sort_values("score", ascending=False).index[:20]))
        result = {"day": day, "agreement": agreement, "top20_same": same, "stocks": len(both)}
        self.journal.set("self_check", result)
        if agreement < 0.9 or same < 14:
            self.say("risk", "Self-check failed: live prices disagreed with official ones",
                     f"Only {same} of the top 20 matched once official closing prices arrived (agreement {agreement:.2f}, 1.00 is perfect). The price feed may have been wrong today. Worth a look before trusting tomorrow's trades.")
        else:
            self.say("system", "Self-check passed", f"The ranking from live prices at the bell matched the one from official closing prices: {same} of the top 20 the same, agreement {agreement:.2f} (1.00 is perfect).")
        return result

    def _end_of_day(self) -> None:
        self._remember_live_board()
        eq = self.broker.equity()
        change = eq - self.day_start_equity
        pct = change / self.day_start_equity if self.day_start_equity else 0.0
        self.say("decision", f"Day finished {'up' if change >= 0 else 'down'} ${abs(change):,.2f} ({pct:+.2%})",
                 f"Account value ${eq:,.2f}. Holding {len(self.broker.positions)} stocks overnight.")
        lag = self.stream.lag_stats() if self.stream else {}
        if lag.get("samples") or self.decision_ms:                      # keep this session's measured speed for when the market is shut
            self.journal.set("last_speed", {"day": self.day_key, **lag, "decision_ms": _median(self.decision_ms),
                                            "fill_ms": _median(self.fill_ms), "stop_us": _median(self.stop_us)})
        self.write_report()

    # ------------------------------------------------------------------ keeping the forecaster fresh
    def retrain_due(self) -> bool:
        """True when the forecaster has not seen the last month of prices."""
        if self.model is None or self.scorer is None:
            return False
        through = self.model.meta.get("trained_through")
        return bool(through) and (self.scorer.as_of - pd.Timestamp(through)).days > RETRAIN_AFTER_DAYS

    async def _retrain(self) -> None:
        """Re-teach the forecaster on all prices to date (time depends on the local computer), in a separate program so prices keep flowing."""
        if not self.settings.network_enabled:
            return
        self._retraining = True
        proc = None
        try:
            self.say("system", "Retraining the forecaster", "It hasn't seen the last month of prices. The app keeps running; training time depends on your computer and data.")
            env = {**os.environ, "NIGHTDESK_HOME": str(ROOT)}
            proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "nightdesk.research", "fit", "--name", "auto", env=env,
                                                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            self.retrain_process = proc
            if await asyncio.wait_for(proc.wait(), 3600) != 0:
                raise RuntimeError("training program failed")
            self.model = await asyncio.to_thread(Ensemble.load, MODELS / "production.json")
            await self._refresh_history(force=True)
            self.say("system", "Forecaster retrained", f"Now trained on prices up to {pd.Timestamp(self.model.meta.get('trained_through')):%-d %B %Y}.")
        except Exception as exc:
            self.say("system", "Retraining failed", f"{type(exc).__name__}: {exc}. Carrying on with the current forecaster.")
            self.model.meta["trained_through"] = str(self.scorer.as_of.date())     # don't retry every ten minutes; try again next month
        finally:
            await self._terminate_process(proc)
            self.retrain_process = None
            self._retraining = False

    # ------------------------------------------------------------------ scoring
    def _today_frame(self) -> pd.DataFrame:
        rows = {s: (q.open, q.high, q.low, q.last, q.volume) for s, q in self.quotes.items() if q.day == self.day_key and q.last}
        return pd.DataFrame.from_dict(rows, orient="index", columns=["open", "high", "low", "last", "volume"])

    async def _score_now(self) -> bool:
        """Re-rank every stock on the latest prices. False when too few stocks have a price yet."""
        if self._scoring:                                             # a re-rank is already under way: wait for it, never run two at once
            for _ in range(200):
                await asyncio.sleep(0.05)
                if not self._scoring:
                    break
            return self.board is not None and self.board_live
        self._scoring = True
        try:
            today = self._today_frame()
            if len(today) < 0.9 * self.expected:                      # never rank or trade on a partial list
                self.message = f"Waiting for prices: {len(today)} of about {self.expected} stocks so far"
                return False
            self.message = "Running"
            t0 = time.perf_counter()
            frac = mktcal.fraction_of_session_elapsed()
            board = await asyncio.to_thread(self.scorer.score, today, frac, self.day_key)
            self.decision_ms.append((time.perf_counter() - t0) * 1000)
            self.board, self.board_at, self.board_live = board, time.time(), True
            await asyncio.to_thread(self._explain)
            self._notify()
            return True
        finally:
            self._scoring = False

    async def _score_loop(self) -> None:
        while True:
            try:
                if self.phase == "open" and self.scorer is not None:
                    await self._score_now()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log(f"scoring error: {type(exc).__name__}: {exc}")
            await asyncio.sleep(SCORE_EVERY)

    def _explain(self) -> None:
        """Plain-English reasons for the top of the board and for everything held."""
        if self.board is None or self.scorer is None or self.scorer.last_X is None:
            return
        want = list(dict.fromkeys(list(self.board.index[:60]) + [s for s in self.broker.positions if s in self.board.index]))
        X = self.scorer.last_X.loc[want].to_numpy()
        self.reasons = dict(zip(want, explain.reasons(self.model, X)))

    # ------------------------------------------------------------------ headlines (optional reader, on trial)
    async def _news_loop(self) -> None:
        await asyncio.sleep(25)
        while True:
            try:
                if self.ready and self.settings.network_enabled and self.phase == "open" and self.board is not None and self.news.reader.available:
                    held = list(self.broker.positions)
                    stocks = [(s, self.names.get(s, s)) for s in held + [s for s in self.board.index[:25] if s not in held]]
                    fresh = await self.news.refresh(stocks)
                    self.news_blocked = self.news.blocks()
                    for item in fresh:
                        sure = item["specific"] and item["conf"] >= 0.8 and item["label"] in ("bad", "good") and time.time() - item["ts"] < 86400
                        if sure and item["symbol"] in self.broker.positions:
                            self.say("news", f"{item['label'].capitalize()} news on a holding: {self.label(item['symbol'])}",
                                     f"\u201c{item['title']}\u201d ({item['publisher']}). Your reader is {item['conf']:.0%} sure. Nothing was traded on it: the reader is on trial.")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log(f"news loop error: {type(exc).__name__}: {exc}")
            await asyncio.sleep(NEWS_EVERY)

    def news_view(self) -> dict:
        spy = self.quotes.get("SPY")
        r = self.news.reader
        awake = bool(r.proc and r.proc.returncode is None)
        return {"reader": {"available": r.available, "status": r.status if awake or r.status != "ready" else "asleep", "error": r.error,
                           "scored": r.scored, "awake": awake, "stats": r.stats},
                "veto_on": self.settings.news_veto, "last_refresh": self.news.last_refresh, "items": self.news.recent(150),
                "blocked": self.news_blocked, "scoreboard": self.news.scoreboard({s: q.last for s, q in self.quotes.items() if q.last}, spy.last if spy else None)}

    # ------------------------------------------------------------------ trading
    def blocked(self) -> set[str]:
        now = time.time()
        self.cooldown = {s: t for s, t in self.cooldown.items() if t > now}
        out = set(self.cooldown)
        if self.settings.news_veto:
            out |= {s for s, v in self.news_blocked.items() if v.get("until", 0) > now}
        return out

    def _prices(self) -> dict[str, float]:
        return {s: q.last for s, q in self.quotes.items() if q.day == self.day_key and q.last}

    async def _daily_check(self, key: str, why: str) -> None:
        """Run a scheduled check and tick it off, or leave it un-ticked so the clock tries again."""
        self._checking = True
        try:
            if await self._check(why):
                self.rebalanced_days.add(key)
                self.journal.set("rebalanced_days", sorted(self.rebalanced_days)[-30:])
            else:
                self._retry_after = time.time() + 120
        finally:
            self._checking = False

    async def _check(self, why: str, final: bool = True) -> bool:
        """A portfolio check on a ranking made from the very latest prices, not one up to ten seconds old."""
        try:
            if self.scorer is not None and hasattr(self.scorer, "score") and time.time() - self.board_at > 1.0:
                await self._score_now()
        except Exception as exc:                                      # a failed refresh must never cancel the check itself
            self.log(f"could not refresh the ranking before the check: {type(exc).__name__}: {exc}")
        return await self.rebalance(why, final)

    async def rebalance(self, why: str, final: bool = True) -> bool:
        """Compare what we hold with what the board says we should hold, and trade the difference.
        Returns False when it had to skip (partial list, dead feed) so the caller can retry."""
        generation = self._trading_generation
        if not self.settings.network_enabled or not self.ready:
            return False
        if self._rebalancing or self.board is None:
            return False
        if len(self.board) < 0.9 * self.expected:
            self.say("system", f"{why}: skipped", f"Only {len(self.board)} of about {self.expected} stocks have prices. Not trading on a partial list.")
            return False
        if not self.history_ok():
            self.say("system", f"{why}: skipped", "Yesterday's closing prices are missing from the price history, so today's measurements would be wrong. Not trading until they arrive.")
            return False
        if self.stream is not None:
            quiet = self.stream.seconds_since_message()
            if not self.stream.lines_up or quiet is None or quiet > 45:
                self.say("system", f"{why}: skipped", "The price feed is down or silent. Not trading on old prices; it will try again when prices return.")
                return False
        self._rebalancing = True
        self._last_rebalance = time.time()
        try:
            scores, prices = self.board["score"], self._prices()
            positions = {s: p.qty for s, p in self.broker.positions.items()}
            blocked = self.blocked()
            tradable = scores[[s for s in scores.index if prices.get(s)]]
            picks = choose_holdings(tradable, list(positions), self.settings.max_positions, self.settings.hold_buffer,
                                    self.sectors, self.settings.max_sector_weight, blocked)
            spy = self.quotes.get("SPY")
            exposure = self.scorer.exposure(picks, self.settings, spy.last if spy else None)
            plan = plan_orders(scores, positions, prices, self.broker.equity(), exposure, self.settings, self.sectors, blocked)
            if self.halt_today:
                plan.buys = []
            now = time.time()
            flagged_now = {s for s, v in self.news_blocked.items() if v.get("until", 0) > now}
            vetoed = []
            if self.settings.news_veto and flagged_now:                # which stocks did the reader actually keep out?
                without = choose_holdings(tradable, list(positions), self.settings.max_positions, self.settings.hold_buffer,
                                          self.sectors, self.settings.max_sector_weight, blocked - flagged_now)
                vetoed = [s for s in without if s not in picks and s in flagged_now]
            on_trial = [] if self.settings.news_veto else [o.symbol for o in plan.buys if o.tag == "open" and o.symbol in flagged_now]
            for s in vetoed + on_trial:                                 # keep score either way
                self.news.record_veto(s, self.news_blocked[s]["title"], prices.get(s), spy.last if spy else None)
            for o in plan.sells:
                self.broker.submit(o.symbol, "sell", o.qty, o.reason, now=now, tag=o.tag)
            if plan.sells:
                for _ in range(40):                                  # let the sells fill so the cash is there for the buys
                    if not any(o.side == "sell" for o in self.broker.pending.values()):
                        break
                    await asyncio.sleep(0.5)
            if generation != self._trading_generation or not self.settings.network_enabled:
                self.broker.cancel_all("portfolio check paused")
                return False
            now = time.time()
            for o in plan.buys:
                r = self.reasons.get(o.symbol, {})
                reason = o.reason + (" In its favour: " + "; ".join(r["for"]) + "." if r.get("for") else "")
                self.broker.submit(o.symbol, "buy", o.qty, reason, now=now, tag=o.tag)
            n = len(plan.sells) + len(plan.buys)
            detail = (f"{len(plan.buys)} to buy, {len(plan.sells)} to sell. Aiming to be {exposure:.0%} invested across {len(picks)} stocks."
                      if n else f"Nothing to change. Staying {sum(self.broker.weights().values()):.0%} invested across {len(positions)} stocks.")
            if exposure < 0.97 and n:
                detail += " Holding some cash because recent swings are above the comfort level." if exposure > 0.5 or not self.settings.trend_filter else " Holding extra cash: the market is below its 200-day average or swinging hard."
            if vetoed:
                detail += f" Skipped after bad news: {', '.join(vetoed)}."
            if on_trial:
                detail += f" Your headline reader would have blocked {', '.join(on_trial)}. Bought anyway: the reader is on trial and the News page is keeping score."
            if self.halt_today:
                detail += " Buying is paused for today after the daily loss limit."
            self.say("decision", why, detail, {"picks": picks, "exposure": exposure, "ranking_age_ms": round((time.time() - self.board_at) * 1000)})
            return True
        except Exception as exc:
            self.say("system", "Rebalance failed", f"{type(exc).__name__}: {exc}")
            return False
        finally:
            self._rebalancing = False

    def control(self, action: str, value=None) -> dict:
        if action == "pause":
            self._trading_generation += 1
            self.broker.cancel_all("practice trading paused")
            self.settings.auto_trade = False
            self.settings.save()
            self.say("user", "Auto-trading paused", "Holdings stay as they are. No new orders until you resume.")
        elif action == "resume":
            if not self.ready or not self.settings.network_enabled:
                return {"ok": False, "message": "Finish Setup and enable public data before resuming practice trading."}
            self.settings.auto_trade, self.killed, self.halt_today = True, False, False
            self.settings.save()
            self.journal.set("killed", False)
            self.peak_equity = self.broker.equity()
            self.journal.set("peak_equity", self.peak_equity)
            self.say("user", "Auto-trading resumed", "The engine will trade by its rules again.")
        elif action == "sell_all":
            if self.phase != "open":
                return {"ok": False, "message": "New York is closed. Nothing was sold. Try again when the market is open."}
            n = self._sell_everything("Sold: you asked to sell everything.")
            self.settings.auto_trade = False
            self.settings.save()
            self.say("user", "Selling everything", f"{n} sell orders placed. Auto-trading paused so it doesn't buy straight back.")
        elif action == "rebalance":
            if self.phase != "open" or self.board is None or not self.board_live:
                return {"ok": False, "message": "This needs the market to be open and live prices flowing."}
            self._spawn_runtime(self._check("Check requested by you"))
        elif action == "reset":
            if value is not None and (type(value) is bool or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
                return {"ok": False, "message": "Starting cash must be a positive finite number."}
            cash = float(self.settings.starting_cash if value is None else value)
            self._trading_generation += 1
            self.broker.reset(cash)
            self.journal.wipe()
            self.settings.starting_cash, self.settings.auto_trade = cash, False
            self.settings.save()
            self.day_start_equity = self.peak_equity = cash
            self.killed = self.halt_today = False
            self.cooldown, self.rebalanced_days, self.closed_days = {}, set(), set()
            self.journal.set("day_start", {"day": self.day_key, "equity": cash})
            self.say("user", "Practice account reset", f"Fresh start with ${cash:,.0f} of pretend money.")
        elif action == "shutdown":
            self.say("system", "Engine stopping", "Nightdesk was quit. The practice account is saved.")
            asyncio.get_running_loop().call_later(0.3, os.kill, os.getpid(), signal.SIGTERM)
        else:
            return {"ok": False, "message": f"Unknown action: {action}"}
        return {"ok": True}

    def export_trades(self, reveal: bool = True) -> dict:
        """Write every trade to a spreadsheet file (CSV) in the project's exports folder."""
        import csv
        folder = ROOT / "exports"
        folder.mkdir(exist_ok=True)
        path = folder / f"nightdesk-trades-{datetime.now().astimezone():%Y-%m-%d-%H%M}.csv"
        fills = list(reversed(self.broker.recent_fills(1_000_000)))
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Local time (with offset)", "Stock", "Company", "Side", "Shares", "Price", "Value", "Trading cost", "Profit booked", "Why"])
            for f in fills:
                when = datetime.fromtimestamp(f.ts).astimezone().isoformat(timespec="seconds")
                w.writerow([when, f.symbol, self.names.get(f.symbol, ""), f.side, round(f.qty, 4), round(f.price, 4), round(f.qty * f.price, 2),
                            round(f.cost, 2), round(f.realized_pnl, 2) if f.side == "sell" else "", f.reason])
        if reveal and sys.platform == "darwin":
            subprocess.Popen(["/usr/bin/open", "-R", str(path)])          # show the file in Finder
        return {"ok": True, "path": str(path), "rows": len(fills)}

    # ------------------------------------------------------------------ record keeping
    async def _mark_loop(self) -> None:
        last_report = 0.0
        while True:
            try:
                if self.ready and self.phase == "open":
                    spy = self.quotes.get("SPY")
                    self.journal.mark(time.time(), self.broker.equity(), spy.last if spy and spy.day == self.day_key else None)
                    if time.time() - last_report > 60:
                        last_report = time.time()
                        await asyncio.to_thread(self.write_report)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log(f"mark loop error: {type(exc).__name__}: {exc}")
            await asyncio.sleep(MARK_EVERY)

    def write_report(self) -> None:
        """A small summary file of the current session, used by the explainer page."""
        try:
            snap = self.snapshot()
            curve = self.journal.curve(since=self.session_open_ts or 0, max_points=400)
            fills = [f for f in self.broker.recent_fills(500) if f.ts >= (self.session_open_ts or 0)]
            spy_first = next((c[2] for c in curve if c[2]), None)
            spy_last = next((c[2] for c in reversed(curve) if c[2]), None)
            report = {
                "day": self.day_key, "updated_at": time.time(), "phase": self.phase,
                "first_trade_at": min((f.ts for f in fills), default=None),
                "account": snap["account"], "feed": snap["feed"], "latency": snap["latency"],
                "buys": sum(f.side == "buy" for f in fills), "sells": sum(f.side == "sell" for f in fills),
                "spy_change_since_first_mark": (spy_last / spy_first - 1) if spy_first and spy_last else None,
                "equity_change_since_first_mark": (curve[-1][1] / curve[0][1] - 1) if len(curve) > 1 else None,
                "curve": curve, "positions": snap["positions"],
                "activity": self.journal.recent(60, since=self.session_open_ts or 0),
            }
            tmp = STATE / "session_report.json.tmp"
            tmp.write_text(json.dumps(report))
            tmp.replace(STATE / "session_report.json")
        except Exception as exc:
            self.log(f"report error: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ what the app shows
    def snapshot(self) -> dict:
        b = self.broker
        eq, mv = b.equity(), b.market_value()
        prev = self._reference_close()
        positions = []
        for sym, p in b.positions.items():
            last = b.last.get(sym, p.avg_cost)
            pc = float(prev.get(sym, float("nan"))) if len(prev) else float("nan")
            row = self.board.loc[sym] if self.board is not None and sym in self.board.index else None
            positions.append({
                "symbol": sym, "name": self.names.get(sym, sym), "sector": self.sectors.get(sym, ""),
                "qty": p.qty, "avg_cost": p.avg_cost, "last": last, "value": p.qty * last, "weight": p.qty * last / eq if eq else 0,
                "pnl": p.unrealized(last), "pnl_pct": last / p.avg_cost - 1 if p.avg_cost else 0,
                "day_pct": (last / pc - 1) if pc == pc and pc > 0 else None,
                "rank": int(row["rank"]) if row is not None else None, "since": p.opened_at,
                "why": self.reasons.get(sym, {}),
            })
        positions.sort(key=lambda r: -r["value"])
        spy = self.quotes.get("SPY")
        spy_prev = float(prev.get("SPY", float("nan"))) if len(prev) else float("nan")
        st = mktcal.status()
        lag = self.stream.lag_stats() if self.stream else {"median_ms": None, "p90_ms": None, "samples": 0}
        kept = self.journal.get("last_speed") or {}                    # the last session's measurements, for when the market is shut
        if not lag.get("samples") and kept.get("median_ms") is not None:
            lag = {"median_ms": kept["median_ms"], "p90_ms": kept.get("p90_ms"), "samples": kept.get("samples", 0)}
        lag["measured_on"] = self.day_key if self.phase == "open" else kept.get("day")
        speed = {"decision_ms": _median(self.decision_ms), "fill_ms": _median(self.fill_ms), "stop_us": _median(self.stop_us)}
        speed = {k: (v if v is not None else kept.get(k)) for k, v in speed.items()}
        age = self.stream.seconds_since_message() if self.stream else None
        model = None
        if self.model is not None:
            spec = getattr(self.model, "spec", None)
            model = {**self.model.meta, "signals": len(getattr(self.model, "feature_names", []) or []),
                     "members": getattr(self.model, "members", None) or (len(spec.horizons) * (len(spec.seeds) + 1) if spec is not None else None)}
        return {
            "t": time.time(), "clock": st, "setup": self.setup_status(),
            "engine": {"ready": self.ready, "message": self.message, "auto_trade": self.settings.auto_trade, "halt_today": self.halt_today,
                       "killed": self.killed, "uptime": time.time() - self.started_at, "rebalancing": self._rebalancing,
                       "model": model, "board_age": time.time() - self.board_at if self.board_at else None,
                       "memory_mb": self.memory_mb, "score_every": SCORE_EVERY,
                       "board_live": self.board_live, "board_size": len(self.board) if self.board is not None else 0,
                       "self_check": self.journal.get("self_check")},
            "feed": {"connected": bool(self.stream and self.stream.connected), "messages": self.stream.messages if self.stream else 0,
                     "rate": round(self.tick_rate, 1), "fresh": self.stream.fresh if self.stream else 0, "age": age, "symbols": len(self.stream.symbols) if self.stream else 0,
                     "reconnects": self.stream.reconnects if self.stream else 0,
                     "lines": len(self.stream.lines) if self.stream else 0, "lines_up": len(self.stream.lines_up) if self.stream else 0, **lag},
            "latency": {**speed, "decisions": len(self.decision_ms), "fills": len(self.fill_ms)},
            "account": {"equity": eq, "cash": b.cash, "invested": mv, "exposure": mv / eq if eq else 0, "starting_cash": b.starting_cash,
                        "day_pnl": eq - self.day_start_equity, "day_pnl_pct": eq / self.day_start_equity - 1 if self.day_start_equity else 0,
                        "total_pnl": eq - b.starting_cash, "total_pnl_pct": eq / b.starting_cash - 1 if b.starting_cash else 0,
                        "realized": b.realized_pnl, "unrealized": sum(r["pnl"] for r in positions), "costs": b.costs_paid,
                        "positions": len(positions), "peak": self.peak_equity, "trades": self._trade_stats(), "sessions": self._session_count()},
            "positions": positions,
            "pending": [{"id": o.id, "symbol": o.symbol, "side": o.side, "qty": o.qty, "reason": o.reason} for o in b.pending.values()],
            "spy": {"last": spy.last if spy else None, "day_pct": (spy.last / spy_prev - 1) if spy and spy.last and spy_prev == spy_prev else None},
        }

    def _session_count(self) -> int:
        """How many trading sessions the practice account has been through (counted once per day, then remembered)."""
        if self._sessions[0] != (self.day_key, self.phase):
            n = self.journal.db.execute("SELECT COUNT(DISTINCT CAST(ts / 86400 AS INTEGER)) FROM curve").fetchone()[0]
            self._sessions = ((self.day_key, self.phase), max(1, int(n or 0)))
        return self._sessions[1]

    def _trade_stats(self) -> dict:
        """Win/loss counts, recomputed only when a new trade has happened."""
        if self._stats_cache[0] != self._fills_seen:
            self._stats_cache = (self._fills_seen, self.broker.trade_stats())
        return self._stats_cache[1]

    def board_view(self, limit: int = 600) -> list[dict]:
        if self.board is None:
            return []
        prev = self._reference_close()
        held, blocked = set(self.broker.positions), self.blocked()
        rows = []
        for sym, r in self.board.head(limit).iterrows():
            last = self.broker.last.get(sym)
            pc = float(prev.get(sym, float("nan"))) if len(prev) else float("nan")
            rows.append({"symbol": sym, "name": self.names.get(sym, sym), "sector": self.sectors.get(sym, ""), "rank": int(r["rank"]),
                         "score": round(float(r["score"]), 3), "last": last,
                         "day_pct": (last / pc - 1) if last and pc == pc and pc > 0 else None,
                         "held": sym in held, "blocked": sym in blocked, "why": self.reasons.get(sym, {})})
        return rows
