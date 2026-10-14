"""Measure the price feed's delay, and whether fewer symbols per line makes it faster.

Uses crypto pairs because they trade around the clock (the stock market is shut during
Sydney daytime). Same Yahoo stream, same machinery as the stock prices.

    .venv/bin/python tools/latency_probe.py
"""
import asyncio, base64, json, statistics, sys, time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from yfinance.pricing_pb2 import PricingData

URL = "wss://streamer.finance.yahoo.com/?version=2"
COINS = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "BNB-USD", "ADA-USD", "AVAX-USD", "LINK-USD", "LTC-USD", "DOT-USD", "TRX-USD",
         "BCH-USD", "XLM-USD", "ATOM-USD", "ETC-USD", "UNI7083-USD", "FIL-USD", "NEAR-USD", "APT21794-USD", "HBAR-USD", "ICP-USD", "ALGO-USD", "AAVE-USD",
         "SHIB-USD", "XMR-USD", "TON11419-USD", "SUI20947-USD", "ARB11841-USD", "OP-USD", "INJ-USD", "MKR-USD", "GRT6719-USD", "SAND-USD", "MANA-USD",
         "EOS-USD", "XTZ-USD", "THETA-USD", "FTM-USD", "RUNE-USD", "EGLD-USD", "FLOW-USD", "CHZ-USD", "AXS-USD", "KAVA-USD", "ZEC-USD", "DASH-USD", "NEO-USD"]


async def line(symbols, seconds, sink):
    async with websockets.connect(URL, ping_interval=None, max_size=None) as ws:
        t = time.perf_counter(); pong = await ws.ping(); await pong; sink["rtt"].append((time.perf_counter() - t) * 1000)
        await ws.send(json.dumps({"subscribe": symbols}))
        end, last = time.time() + seconds, {}
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), 1.0)
            except asyncio.TimeoutError:
                continue
            recv = time.time()
            m = json.loads(raw).get("message")
            if not m:
                continue
            p = PricingData(); p.ParseFromString(base64.b64decode(m))
            key = (p.time, p.price)
            if p.id in last and last[p.id][0] != key:
                sink["lag"].append(recv - p.time / 1000)
                sink["gap"].append(recv - last[p.id][1])
                sink["syms"].add(p.id)
            last[p.id] = (key, recv)
        t = time.perf_counter(); pong = await ws.ping(); await pong; sink["rtt"].append((time.perf_counter() - t) * 1000)


async def trial(name, groups, seconds=25):
    sink = {"lag": [], "gap": [], "rtt": [], "syms": set()}
    await asyncio.gather(*[line(g, seconds, sink) for g in groups])
    lag = sorted(sink["lag"])
    q = lambda a, f: round(a[int(len(a) * f)] * 1000) if a else None
    row = {"setup": name, "lines": len(groups), "symbols": sum(len(g) for g in groups), "updates": len(lag), "symbols_updated": len(sink["syms"]),
           "delay_median_ms": q(lag, 0.5), "delay_fastest_tenth_ms": q(lag, 0.1), "delay_slowest_tenth_ms": q(lag, 0.9),
           "seconds_between_updates_per_symbol": round(statistics.median(sink["gap"]), 2) if sink["gap"] else None,
           "round_trip_ms": round(statistics.median(sink["rtt"]), 1)}
    print(json.dumps(row), flush=True)
    return row


async def main():
    rows = [await trial("2 symbols on 1 line", [COINS[:2]]),
            await trial("12 symbols on 1 line", [COINS[:12]]),
            await trial("48 symbols on 1 line", [COINS]),
            await trial("48 symbols on 4 lines", [COINS[i::4] for i in range(4)]),
            await trial("48 symbols on 12 lines", [COINS[i::12] for i in range(12)])]
    out = Path(__file__).resolve().parents[1] / "research" / "latency.json"
    out.write_text(json.dumps({"measured_at": time.time(), "note": "crypto pairs, measured while the stock market was shut", "rows": rows}))

asyncio.run(main())
