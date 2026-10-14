"""News desk: fetch fresh headlines and use an optional user-configured local
three-class headline reader to label each one good, bad or nothing.

What the labels are used for, and no more than this:
  - A confident, fresh, company-specific BAD headline blocks a NEW purchase of that stock
    for 24 hours. It never sells anything and never causes a purchase.
Whether that helps is unproven, so every block is recorded and scored later.
"""
from __future__ import annotations

from .providers import yahoo

import asyncio
import json
import re
import sqlite3
import time
import warnings
from pathlib import Path

from .sysinfo import footprint_mb
from .config import CACHE, READER_MODEL, READER_PYTHON

LABELS = ["good", "bad", "nothing"]          # the order the model was trained with
MIN_CONF = 0.80
WINDOW = 24 * 3600.0
_SUFFIX = {"inc", "corporation", "corp", "company", "co", "holdings", "holding", "group", "plc", "ltd", "limited", "nv", "sa", "ag", "lp", "llc", "trust", "&"}


_EVERYDAY = {"dow", "target", "gap", "block", "match", "ball", "progressive", "coach", "keys", "visa", "public storage", "best buy"}


_GENERIC = {"american", "western", "eastern", "southern", "northern", "united", "general", "international", "national", "first", "global", "digital",
            "applied", "advanced", "consolidated", "pacific", "atlantic", "public", "universal", "royal", "marathon", "citizens", "principal",
            "discover", "prudential", "capital", "federal", "regions", "energy", "electric", "republic", "people", "mutual", "motors"}


def name_key(name: str) -> str:
    """'Franklin Resources, Inc.' -> 'franklin resources': the part a headline would use."""
    words = re.sub(r"\(.*?\)", " ", name.lower()).replace(",", " ").split()
    if words and words[0] == "the":
        words = words[1:]
    while len(words) > 1 and words[-1].strip(".") in _SUFFIX:
        words.pop()
    return " ".join(w.strip(".") for w in words)


def is_specific(title: str, name: str, symbol: str, related: list[str] | None) -> bool:
    """True when the headline is about this one company, not a market wrap or a list of ten stocks."""
    tickers = [t for t in (related or []) if not t.startswith("^")]
    if len(tickers) > 3:
        return False
    key = name_key(name)
    everyday = key in _EVERYDAY
    if everyday:                                        # "Dow rises 400 points" is the index, not Dow Inc.
        key = " ".join(w.strip(".,") for w in re.sub(r"\(.*?\)", " ", name.lower()).split())
    if key and re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", title.lower().replace(".", "").replace(",", "")):
        return True
    sym = re.escape(symbol)
    if re.search(rf"\((?:NYSE|NASDAQ)\s*:\s*{sym}\)|\({sym}\)", title, flags=re.I):
        return True
    first = key.split(" ")[0] if " " in key and not everyday else ""   # "Micron" for Micron Technology, when it can't be mistaken
    return (len(first) >= 6 and first not in _GENERIC and len(tickers) <= 2
            and bool(re.search(r"(?<![a-z0-9])" + re.escape(first) + r"(?![a-z0-9])", title.lower())))


_MOVE = re.compile(r"\b(shares?|stock)\b.*\b(falls?|fell|drops?|dropped|sinks?|sank|slides?|slid|slips?|dips?|tumbles?|plunges?|declines?|down|lower|higher|up|rises?|rose|jumps?|gains?|surges?|soars?|rall(?:y|ies))\b"
                   r"|\b(dips?|falls?|sinks?|gains?|rises?|surges?) more than\b|\bunderperforms?\b|\boutperforms?\b|\btrending stock\b|\bstraight session\b", re.I)
_CAUSE = re.compile(r"\b(as|after|amid|following|because|due to|over)\b"
                    r"|\bon\b(?!\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|the day)\b)", re.I)


def is_price_recap(title: str) -> bool:
    """'Shares sink for a fourth day' only restates what the price already did. Not counted as news."""
    return bool(_MOVE.search(title)) and not _CAUSE.search(title)


def veto_decision(items: list[dict], now: float, min_conf: float = MIN_CONF, window: float = WINDOW) -> dict | None:
    """The headline that blocks a buy, or None. `items` are one stock's labelled, specific headlines."""
    recent = [i for i in items if 0 <= now - i["ts"] <= window]
    bad = [i for i in recent if i["label"] == "bad" and i["conf"] >= min_conf]
    if not bad:
        return None
    latest = max(bad, key=lambda i: i["ts"])
    if any(i["label"] == "good" and i["conf"] >= min_conf and i["ts"] > latest["ts"] for i in recent):
        return None                                   # newer good news lifts the block
    return latest


def parse_search_items(raw: list[dict], symbol: str, name: str) -> list[dict]:
    out = []
    for it in raw or []:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        out.append({"symbol": symbol, "title": title, "publisher": it.get("publisher") or "", "ts": float(it.get("providerPublishTime") or time.time()),
                    "url": it.get("link") or "", "specific": is_specific(title, name, symbol, it.get("relatedTickers")) and not is_price_recap(title)})
    return out


def label_all(items: list[dict], answers: list[dict]) -> list[dict]:
    out = []
    for item, ans in zip(items, answers):
        p = ans["p"]
        k = max(range(3), key=lambda j: p[j])
        out.append({**item, "label": LABELS[k], "conf": p[k], "p": p})
    return out


def fetch_headlines(symbol: str, name: str, count: int = 8) -> list[dict]:
    """Recent headlines for one stock from Yahoo Finance's search (no account needed)."""
    yf = yahoo(CACHE)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return parse_search_items(yf.Search(symbol, news_count=count, max_results=1).news, symbol, name)


class Reader:
    """Talks to the reader model, which runs as a separate small program (see reader_sidecar.py)."""

    def __init__(self, python: str | Path = READER_PYTHON, model_dir: str | Path = READER_MODEL,
                 label_order: tuple[str, ...] = ("good", "bad", "nothing")):
        self.python = Path(python).expanduser() if str(python) else None
        self.model_dir = Path(model_dir).expanduser() if str(model_dir) else None
        self.label_order = tuple(label_order)
        self.proc: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()
        self.status = "idle" if self.available else "missing"
        self.error = ""
        self.scored = 0
        self.stats: dict = {}            # memory and speed, as reported by the reader itself
        self._id = 0

    @property
    def available(self) -> bool:
        if not self.python or not self.model_dir or not self.python.is_file():
            return False
        if self.label_order != tuple(LABELS):
            return False
        return (self.model_dir / "model.safetensors").is_file() and (self.model_dir / "config.json").is_file()

    async def _line(self, timeout: float) -> dict:
        raw = await asyncio.wait_for(self.proc.stdout.readline(), timeout)
        if not raw:
            raise RuntimeError("reader stopped")
        return json.loads(raw)

    async def score(self, headlines: list[str]) -> list[dict] | None:
        """[{'p': [good, bad, nothing]}, ...] or None when the reader can't be used."""
        if not headlines:
            return []
        if not self.available:
            self.status = "missing"
            return None
        async with self.lock:
            try:
                if self.proc is None or self.proc.returncode is not None:
                    sidecar = Path(__file__).with_name("reader_sidecar.py")
                    self.proc = await asyncio.create_subprocess_exec(str(self.python), str(sidecar), str(self.model_dir),
                                                                     stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                                     stderr=asyncio.subprocess.DEVNULL, cwd=str(self.model_dir.parent))
                    await self._line(60)                       # {"ready": true}
                self._id += 1
                self.proc.stdin.write((json.dumps({"id": self._id, "headlines": headlines}) + "\n").encode())
                await self.proc.stdin.drain()
                answer = await self._line(180)                 # the first call also loads the model
                if answer.get("error") or len(answer.get("results", [])) != len(headlines):
                    raise RuntimeError(answer.get("error") or "wrong number of answers")
                self.status, self.error = "ready", ""
                self.stats = {**self.stats, **answer.get("stats", {}), "at": time.time()}
                mb = await asyncio.to_thread(footprint_mb, self.proc.pid)          # what the model really holds while awake
                if mb:
                    self.stats["footprint_mb"] = mb
                self.scored += len(headlines)
                return answer["results"]
            except Exception as exc:
                self.status, self.error = "error", f"{type(exc).__name__}: {exc}"
                await self.close()
                return None

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
                await self.proc.wait()
            except ProcessLookupError:
                pass
        self.proc = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS news (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, seen REAL, symbol TEXT, title TEXT, publisher TEXT, url TEXT,
                                 label TEXT, conf REAL, specific INTEGER, UNIQUE(symbol, title));
CREATE TABLE IF NOT EXISTS vetoes (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, symbol TEXT, title TEXT, price REAL, spy REAL);
"""


class NewsDesk:
    def __init__(self, db: sqlite3.Connection, reader: Reader | None = None):
        self.db = db
        self.db.executescript(_SCHEMA)
        self.reader = reader or Reader()
        self.last_refresh = 0.0
        self.last_count = 0

    def _known(self, symbol: str) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT title FROM news WHERE symbol=?", (symbol,))}

    async def refresh(self, stocks: list[tuple[str, str]], pause: float = 0.4) -> list[dict]:
        """Fetch and label new headlines for (symbol, company name) pairs. Returns the new ones."""
        fresh: list[dict] = []
        for symbol, name in stocks:
            try:
                items = await asyncio.to_thread(fetch_headlines, symbol, name)
            except Exception:
                continue
            known = self._known(symbol)
            fresh += [i for i in items if i["title"] not in known and time.time() - i["ts"] < 3 * 86400]
            await asyncio.sleep(pause)
        self.last_refresh = time.time()
        if not fresh:
            self.last_count = 0
            return []
        answers = await self.reader.score([i["title"] for i in fresh])
        if answers is None:
            return []
        labelled = label_all(fresh, answers)
        now = time.time()
        for i in labelled:
            self.db.execute("INSERT OR IGNORE INTO news(ts, seen, symbol, title, publisher, url, label, conf, specific) VALUES(?,?,?,?,?,?,?,?,?)",
                            (i["ts"], now, i["symbol"], i["title"], i["publisher"], i["url"], i["label"], i["conf"], int(i["specific"])))
        self.db.commit()
        self.last_count = len(labelled)
        return labelled

    def blocks(self, now: float | None = None) -> dict[str, dict]:
        """symbol -> the headline currently blocking a purchase of it."""
        now = now or time.time()
        rows = self.db.execute("SELECT symbol, ts, title, label, conf FROM news WHERE specific=1 AND ts >= ?", (now - WINDOW,)).fetchall()
        by: dict[str, list[dict]] = {}
        for sym, ts, title, label, conf in rows:
            by.setdefault(sym, []).append({"ts": ts, "title": title, "label": label, "conf": conf})
        out = {}
        for sym, items in by.items():
            hit = veto_decision(items, now)
            if hit:
                out[sym] = {"title": hit["title"], "conf": hit["conf"], "ts": hit["ts"], "until": hit["ts"] + WINDOW}
        return out

    def recent(self, limit: int = 150) -> list[dict]:
        rows = self.db.execute("SELECT ts, symbol, title, publisher, url, label, conf, specific FROM news ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "symbol": r[1], "title": r[2], "publisher": r[3], "url": r[4], "label": r[5], "conf": r[6], "specific": bool(r[7])} for r in rows]

    def record_veto(self, symbol: str, title: str, price: float | None, spy: float | None) -> None:
        recent = self.db.execute("SELECT 1 FROM vetoes WHERE symbol=? AND ts >= ?", (symbol, time.time() - WINDOW)).fetchone()
        if not recent:                                # count each block once a day
            self.db.execute("INSERT INTO vetoes(ts, symbol, title, price, spy) VALUES(?,?,?,?,?)", (time.time(), symbol, title, price, spy))
            self.db.commit()

    def scoreboard(self, prices: dict[str, float], spy: float | None) -> dict:
        """Did blocking help? Compares each blocked stock's move since the block with the market's."""
        rows = self.db.execute("SELECT ts, symbol, title, price, spy FROM vetoes ORDER BY ts DESC").fetchall()
        items, helped = [], 0
        for ts, sym, title, price, spy0 in rows:
            now_px = prices.get(sym)
            move = (now_px / price - 1) if now_px and price else None
            market = (spy / spy0 - 1) if spy and spy0 else None
            edge = (market - move) if move is not None and market is not None else None   # positive = the block avoided a laggard
            helped += bool(edge is not None and edge > 0)
            items.append({"ts": ts, "symbol": sym, "title": title, "move": move, "market": market, "edge": edge})
        judged = [i for i in items if i["edge"] is not None]
        return {"blocks": len(items), "judged": len(judged), "helped": helped,
                "avg_edge": sum(i["edge"] for i in judged) / len(judged) if judged else None, "items": items[:40]}
