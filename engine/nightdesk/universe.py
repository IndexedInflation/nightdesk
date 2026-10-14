"""Which stocks Nightdesk looks at: the companies in the S&P 500 (America's 500 biggest).

The list comes from Wikipedia's public table and is cached, so the app still starts
when Wikipedia is unreachable.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .config import CACHE

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
UNIVERSE_FILE = CACHE / "universe.json"
MAX_AGE_DAYS = 7


def _yahoo_symbol(symbol: str) -> str:
    """Wikipedia writes BRK.B, Yahoo wants BRK-B."""
    return symbol.strip().upper().replace(".", "-")


def fetch_sp500() -> list[dict]:
    """Download the current S&P 500 table. Returns one dict per company."""
    headers = {"User-Agent": "Nightdesk/1.0 (personal research app; contact: local user)"}
    html = httpx.get(WIKI_URL, headers=headers, timeout=30, follow_redirects=True).text
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "constituents"})
    if table is None:
        raise RuntimeError("Wikipedia page layout changed: no 'constituents' table")
    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) < 6:
            continue
        rows.append({
            "symbol": _yahoo_symbol(cells[0]),
            "name": cells[1],
            "sector": cells[2],
            "industry": cells[3],
            "added": cells[5][:10],
        })
    if len(rows) < 450:
        raise RuntimeError(f"Only {len(rows)} companies parsed; refusing to use a broken list")
    return rows


def load_universe(refresh: bool = False) -> list[dict]:
    """The cached company list; re-downloaded once a week."""
    fresh = UNIVERSE_FILE.exists() and (time.time() - UNIVERSE_FILE.stat().st_mtime) < MAX_AGE_DAYS * 86400
    if UNIVERSE_FILE.exists() and fresh and not refresh:
        return json.loads(UNIVERSE_FILE.read_text())
    try:
        rows = fetch_sp500()
        tmp = Path(str(UNIVERSE_FILE) + ".tmp")
        tmp.write_text(json.dumps(rows))
        tmp.replace(UNIVERSE_FILE)
        return rows
    except Exception:
        if UNIVERSE_FILE.exists():            # stale list beats no list
            return json.loads(UNIVERSE_FILE.read_text())
        raise


def symbols(refresh: bool = False) -> list[str]:
    return sorted({r["symbol"] for r in load_universe(refresh)})


def sector_map() -> dict[str, str]:
    return {r["symbol"]: r["sector"] for r in load_universe()}


def name_map() -> dict[str, str]:
    return {r["symbol"]: r["name"] for r in load_universe()}
