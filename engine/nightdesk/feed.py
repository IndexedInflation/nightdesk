"""Live prices: direct lines to Yahoo Finance's public price stream.

Each message is one trade print: symbol, price, the exchange's own timestamp and the
running day volume. We stamp the moment it lands on this computer, so the app can show
exactly how old every price is (the "feed delay").

The feed is split into lines because one line only delivers about 100 symbols no matter how many you
ask for. So the 500 stocks are split over several lines of 90 symbols each, which gives
roughly one fresh price per stock every 2 to 3 seconds.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import deque
from typing import Callable

import websockets
from yfinance.pricing_pb2 import PricingData

URL = "wss://streamer.finance.yahoo.com/?version=2"
REGULAR_HOURS = 1
PER_LINE = 90


class YahooStream:
    def __init__(self, symbols: list[str], on_tick: Callable, log: Callable[[str], None] = print):
        self.symbols = list(symbols)
        self.on_tick = on_tick              # (symbol, price, exchange_ts, received_ts, day_volume, regular_hours, is_new_print)
        self.log = log
        self.lines = [self.symbols[i:i + PER_LINE] for i in range(0, len(self.symbols), PER_LINE)]
        self.lines_up: set[int] = set()
        self.messages = 0
        self.fresh = 0                      # messages that carried a genuinely new trade
        self.last_message = 0.0
        self.reconnects = 0
        self.lags = deque(maxlen=3000)      # seconds between the exchange stamp and arrival here
        self.measure = True                 # the engine switches this off outside trading hours, when late re-prints would skew it
        self._last: dict[str, tuple] = {}
        self._stop = False

    @property
    def connected(self) -> bool:
        return bool(self.lines) and len(self.lines_up) == len(self.lines)

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        await asyncio.gather(*[self._run_line(i, syms) for i, syms in enumerate(self.lines)])

    async def _heartbeat(self, ws, symbols: list[str]) -> None:
        while True:                          # the server forgets quiet clients; re-announce every 15 s
            await asyncio.sleep(15)
            await ws.send(json.dumps({"subscribe": symbols}))

    async def _run_line(self, idx: int, symbols: list[str]) -> None:
        backoff = 1.0
        await asyncio.sleep(idx * 0.3)       # don't dial every line in the same instant
        while not self._stop:
            beat = None
            try:
                async with websockets.connect(URL, ping_interval=20, ping_timeout=20, max_size=None) as ws:
                    await ws.send(json.dumps({"subscribe": symbols}))
                    self.lines_up.add(idx)
                    backoff = 1.0
                    beat = asyncio.create_task(self._heartbeat(ws, symbols))
                    async for raw in ws:
                        self._handle(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:         # dropped line: wait a little longer each time, then redial
                self.log(f"price line {idx + 1} dropped ({type(exc).__name__}); redialling in {backoff:.0f}s")
            finally:
                self.lines_up.discard(idx)
                if beat:
                    beat.cancel()
            if not self._stop:
                self.reconnects += 1
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _handle(self, raw) -> None:
        recv = time.time()
        payload = json.loads(raw).get("message")
        if not payload:
            return
        p = PricingData()
        p.ParseFromString(base64.b64decode(payload))
        if not p.id or p.price <= 0:
            return
        exch = p.time / 1000.0 if p.time else recv
        self.messages += 1
        self.last_message = recv
        key = (p.time, p.price, p.day_volume)
        prev = self._last.get(p.id)
        fresh = prev is not None and key != prev     # the server also re-sends old prints; those are not news
        self._last[p.id] = key
        if fresh:
            self.fresh += 1
            if self.measure and p.market_hours == REGULAR_HOURS and p.time > prev[0] and 0 <= recv - exch < 120:
                self.lags.append(recv - exch)
        self.on_tick(p.id, float(p.price), exch, recv, float(p.day_volume or 0), p.market_hours == REGULAR_HOURS, fresh)

    def lag_stats(self) -> dict:
        if not self.lags:
            return {"median_ms": None, "p90_ms": None, "samples": 0}
        s = sorted(self.lags)
        return {"median_ms": round(s[len(s) // 2] * 1000), "p90_ms": round(s[int(len(s) * 0.9)] * 1000), "samples": len(s)}

    def seconds_since_message(self) -> float | None:
        return time.time() - self.last_message if self.last_message else None
