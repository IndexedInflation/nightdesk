"""The engine's front door: a tiny local web server the app window talks to.

It only listens on 127.0.0.1 (this computer). Anything that changes state needs a header that
ordinary web pages cannot send, so a random website can't poke the engine.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import asdict

import pandas as pd
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import data, mktcal
from .config import DEFAULT_MODE, ASSET_ROOT, HOST, LOGS, PORT, PUBLIC_RESEARCH, RESEARCH, RISK_MODES, ROOT, UI_DIR, Settings
from .engine import Engine
from .news import Reader

ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
EDITABLE = {"auto_trade": bool, "max_positions": int, "max_weight": float, "max_sector_weight": float, "target_vol": float,
            "trend_filter": bool, "slippage_bps": float, "daily_loss_limit": float, "max_drawdown_limit": float,
            "position_stop": float, "news_veto": bool, "keep_awake": bool, "rebalance_minutes_before_close": int, "hold_buffer": float}
LIMITS = {"max_positions": (5, 50), "max_weight": (0.02, 0.25), "max_sector_weight": (0.1, 1.0), "target_vol": (0.05, 1.0),
          "slippage_bps": (0.0, 50.0), "daily_loss_limit": (0.005, 0.20), "max_drawdown_limit": (0.03, 0.60),
          "position_stop": (0.03, 0.50), "rebalance_minutes_before_close": (3, 120), "hold_buffer": (1.0, 5.0)}

OPENABLE = {"explainer": ASSET_ROOT / "How-Nightdesk-Works.html", "log": LOGS / "engine.log", "folder": ROOT}     # the only things the window may open

engine: Engine = None            # created when the server starts (tests plug in their own)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = engine or Engine()
    await engine.start()
    yield
    await engine.stop()


app = FastAPI(title="Nightdesk", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.headers.get("host") not in ALLOWED_HOSTS:
        return JSONResponse({"error": "Nightdesk only answers on this computer."}, status_code=403)
    if request.method not in ("GET", "HEAD") and request.headers.get("x-nightdesk") != "1":
        return JSONResponse({"error": "Missing app header."}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/api/") or request.url.path.endswith((".js", ".css", ".html", "/")):
        response.headers["Cache-Control"] = "no-store"
    return response


def _json_safe(obj):
    if isinstance(obj, float):
        return obj if obj == obj and abs(obj) != float("inf") else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


@app.get("/api/health")
async def health():
    return {"ok": True, "app": "nightdesk", "version": "0.1.0", "ready": engine.ready, "message": engine.message, "uptime": time.time() - engine.started_at, "pid": os.getpid()}


@app.get("/api/state")
async def state():
    return _json_safe(engine.snapshot())


@app.get("/api/activity")
async def activity(limit: int = 200, kind: str | None = None):
    return engine.journal.recent(min(limit, 1000), [kind] if kind else None)


@app.get("/api/board")
async def board(limit: int = 600):
    return _json_safe({"at": engine.board_at, "live": engine.board_live, "rows": engine.board_view(limit)})


@app.get("/api/curve")
async def curve(span: str = "session"):
    since = {"session": engine.session_open_ts or 0, "week": time.time() - 7 * 86400, "month": time.time() - 31 * 86400, "all": 0}.get(span, 0)
    return {"span": span, "starting_cash": engine.broker.starting_cash, "points": _json_safe(engine.journal.curve(since))}


_bar_cache: dict[tuple, tuple[float, list]] = {}


@app.get("/api/bars")
async def bars(symbol: str, span: str = "day"):
    """Candles for one stock: today's one-minute bars, or a year of daily bars from the history file."""
    symbol = symbol.upper()
    key = (symbol, span)
    hit = _bar_cache.get(key)
    if hit and time.time() - hit[0] < (30 if span == "day" else 600):
        return hit[1]
    if span == "day":
        if not engine.settings.network_enabled:
            raise HTTPException(409, "Allow public data downloads in Setup before requesting live prices.")
        got = await asyncio.to_thread(data.intraday_bars, [symbol], "1m", "1d", lambda *_: None)
        df = got.get(symbol)
        rows = [] if df is None else [{"time": int(ts.timestamp()), "open": float(r.open), "high": float(r.high), "low": float(r.low),
                                       "close": float(r.close), "volume": float(r.volume)} for ts, r in df.iterrows()]
    else:
        if engine.scorer is None or symbol not in engine.scorer.base["close"].columns:
            raise HTTPException(404, "No history for that symbol")
        base = engine.scorer.base
        n = 260 if span == "year" else 66
        frame = pd.DataFrame({f: base[f][symbol] for f in ("open", "high", "low", "close", "volume")}).dropna().iloc[-n:]
        rows = [{"time": ts.strftime("%Y-%m-%d"), "open": float(r.open), "high": float(r.high), "low": float(r.low),
                 "close": float(r.close), "volume": float(r.volume)} for ts, r in frame.iterrows()]
    _bar_cache[key] = (time.time(), rows)
    return rows


def _read(name: str):
    path = RESEARCH / name
    if not path.is_file():
        path = PUBLIC_RESEARCH / name
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


@app.get("/api/research")
async def research():
    return {"design": _read("grade_design.json"), "exam": _read("grade_exam.json"), "exam_members": _read("grade_exam_members.json"),
            "variants": _read("variants.json"), "luck_exam": _read("placebo_exam.json"), "luck_design": _read("placebo_design.json"),
            "experiments": _read("experiments.json"), "outsiders": _read("outsiders.json"), "flattery": _read("flattery.json"), "experiments2": _read("experiments2.json"), "pit": _read("pit.json"), "pit_menu": _read("pit_menu.json"),
            "pit_rehearsal": _read("grade_pit_rehearsal.json"), "pit_exam": _read("grade_pit_exam.json"),
            "model": _read("model_card.json"), "intraday": _read("intraday.json"),
            "lab": _read("lab.json"), "modes": _read("modes.json"), "calm": _read("lab/calm_check.json"),
            "candidate": _read("candidate_risk/modes.json"), "blend": _read("lab/blend_check.json"),
            "round1": {"rehearsal": _read("round1/grade_pit_rehearsal.json"), "exam": _read("round1/grade_pit_exam.json")},
            "reader_resources": _read("reader_resources.json"), "latency": _read("latency.json")}


@app.get("/api/news")
async def news():
    return _json_safe(engine.news_view())


@app.get("/api/setup")
async def get_setup():
    return engine.setup_status()


@app.post("/api/setup")
async def setup(request: Request):
    body = await request.json()
    if not isinstance(body, dict) or set(body) - {"network_enabled", "start"}:
        raise HTTPException(400, "Setup accepts only network_enabled and start.")
    if any(type(value) is not bool for value in body.values()):
        raise HTTPException(400, "Setup options must be true or false.")
    if body.get("network_enabled") is False:
        if body.get("start"):
            raise HTTPException(400, "Allow public data downloads before starting Setup.")
        await engine.disconnect()
        return engine.setup_status()
    if body.get("network_enabled") is True:
        engine.settings.network_enabled = True
        engine.settings.save()
    if body.get("start"):
        if not engine.settings.network_enabled:
            raise HTTPException(409, "Allow public data downloads before starting Setup.")
        return engine.start_setup()
    return engine.setup_status()


@app.post("/api/reader")
async def configure_reader(request: Request):
    body = await request.json()
    if not isinstance(body, dict) or set(body) != {"python", "model"} or any(not isinstance(v, str) for v in body.values()):
        raise HTTPException(400, "Reader setup needs your Python executable and model directory.")
    python, model = body["python"].strip(), body["model"].strip()
    reader = Reader(python, model)
    if python or model:
        if not reader.available:
            raise HTTPException(400, "Choose an existing Python executable and a model directory containing config.json and model.safetensors.")
        try:
            mapping = json.loads((reader.model_dir / "config.json").read_text())["id2label"]
            labels = [str(mapping[str(i)]).lower() for i in range(3)]
            if len(mapping) != 3 or labels != ["good", "bad", "nothing"]:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(400, "The reader must declare id2label 0=good, 1=bad, 2=nothing. Generic sentiment models have different semantics.")
    await engine.news.reader.close()
    engine.news.reader = reader
    engine.settings.reader_python, engine.settings.reader_model = python, model
    engine.settings.save()
    return {"ok": True, "available": reader.available}


@app.get("/api/settings")
async def get_settings():
    return {"settings": engine.settings.to_dict(), "limits": LIMITS, "modes": RISK_MODES, "default_mode": DEFAULT_MODE}


@app.post("/api/settings")
async def set_settings(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Settings must be an object.")
    changed = []
    validated = {}
    for key, value in body.items():
        if key not in EDITABLE:
            raise HTTPException(400, f"'{key}' can't be changed here")
        if EDITABLE[key] is bool and type(value) is not bool:
            raise HTTPException(400, f"'{key}' needs a boolean")
        if key == "auto_trade" and value and (not engine.ready or not engine.settings.network_enabled):
            raise HTTPException(409, "Finish Setup and enable public data before practice trading.")
        if EDITABLE[key] is not bool and (type(value) is bool or not isinstance(value, (int, float))):
            raise HTTPException(400, f"'{key}' needs a number")
        if EDITABLE[key] is not bool and not math.isfinite(value):
            raise HTTPException(400, f"'{key}' needs a finite number")
        if EDITABLE[key] is int and value != int(value):
            raise HTTPException(400, f"'{key}' needs a whole number")
        try:
            value = EDITABLE[key](value)
        except (TypeError, ValueError):
            raise HTTPException(400, f"'{key}' needs a {EDITABLE[key].__name__}")
        if key in LIMITS and not (LIMITS[key][0] <= value <= LIMITS[key][1]):
            raise HTTPException(400, f"'{key}' must be between {LIMITS[key][0]} and {LIMITS[key][1]}")
        validated[key] = value
    for key, value in validated.items():
        if getattr(engine.settings, key) != value:
            setattr(engine.settings, key, value)
            if key == "auto_trade" and not value:
                engine._trading_generation += 1
                engine.broker.cancel_all("practice trading paused")
            changed.append(key)
    if changed:
        engine.settings.save()
        engine.broker.slippage = engine.settings.slippage_bps / 1e4
        engine.say("user", "You changed a setting", ", ".join(c.replace("_", " ") for c in changed))
    return {"ok": True, "changed": changed, "settings": engine.settings.to_dict()}


@app.post("/api/settings/defaults")
async def default_settings():
    """Put every dial back to the tested value. Trading stays paused if it was paused: a reset never switches it back on."""
    fresh, changed = Settings(), []
    for key in EDITABLE:
        if key != "auto_trade" and getattr(engine.settings, key) != getattr(fresh, key):
            setattr(engine.settings, key, getattr(fresh, key))
            changed.append(key)
    if changed:
        engine.settings.save()
        engine.broker.slippage = engine.settings.slippage_bps / 1e4
        engine.say("user", "You put the settings back to the tested ones", ", ".join(c.replace("_", " ") for c in changed))
    return {"ok": True, "changed": changed, "settings": engine.settings.to_dict()}


def _open(path, reveal: bool) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open"] + (["-R"] if reveal else []) + [str(path)])


@app.post("/api/open")
async def open_thing(request: Request):
    """Open the explainer page in the browser, or show the log or the project folder in Finder. Nothing else."""
    what = str((await request.json()).get("what", ""))
    path = OPENABLE.get(what)
    if path is None or not path.exists():
        raise HTTPException(404, "Nothing to open")
    _open(path, reveal=what == "log")
    return {"ok": True}


@app.post("/api/control")
async def control(request: Request):
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("action"), str) or set(body) - {"action", "value"}:
        raise HTTPException(400, "Choose an app action.")
    if body["action"] == "reset" and "value" in body:
        amount = body["value"]
        if type(amount) is bool or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
            raise HTTPException(400, "Starting cash must be a positive finite number.")
    return engine.control(body["action"], body.get("value"))


@app.post("/api/export")
async def export(request: Request):
    body = await request.json()
    return engine.export_trades(reveal=bool(body.get("reveal", True)))


@app.websocket("/ws")
async def ws(socket: WebSocket):
    """Pushes a fresh snapshot whenever something changes, at most twice a second."""
    origin = socket.headers.get("origin")
    if socket.headers.get("host") not in ALLOWED_HOSTS or (origin and origin not in {f"http://{host}" for host in ALLOWED_HOSTS}):
        await socket.close()
        return
    await socket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    engine.listeners.add(queue)
    try:
        while True:
            await socket.send_text(json.dumps(_json_safe(engine.snapshot())))
            try:
                await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        engine.listeners.discard(queue)


@app.get("/")
async def index():
    return FileResponse(UI_DIR / "index.html")


if UI_DIR.exists():
    app.mount("/", StaticFiles(directory=UI_DIR), name="ui")
