"""The engine's notebook: a plain-English activity log and the account-value curve.

Lives in the same small database file as the practice account.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, title TEXT, detail TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS curve (ts REAL PRIMARY KEY, equity REAL, spy REAL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


class Journal:
    def __init__(self, db_path: Path):
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.executescript("PRAGMA journal_mode=WAL;" + _SCHEMA)

    # ---- activity log -------------------------------------------------
    def add(self, kind: str, title: str, detail: str = "", data: dict | None = None, ts: float | None = None) -> dict:
        """kind: trade | decision | risk | news | user (something the person did) | system (housekeeping)"""
        ts = ts or time.time()
        cur = self.db.execute("INSERT INTO journal(ts, kind, title, detail, data) VALUES(?,?,?,?,?)",
                              (ts, kind, title, detail, json.dumps(data or {})))
        self.db.commit()
        return {"id": cur.lastrowid, "ts": ts, "kind": kind, "title": title, "detail": detail, "data": data or {}}

    def recent(self, limit: int = 200, kinds: list[str] | None = None, since: float = 0.0) -> list[dict]:
        sql, args = "SELECT id, ts, kind, title, detail, data FROM journal WHERE ts >= ?", [since]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        rows = self.db.execute(sql + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()
        return [{"id": r[0], "ts": r[1], "kind": r[2], "title": r[3], "detail": r[4], "data": json.loads(r[5] or "{}")} for r in rows]

    # ---- account-value curve ------------------------------------------
    def mark(self, ts: float, equity: float, spy: float | None) -> None:
        self.db.execute("INSERT OR REPLACE INTO curve(ts, equity, spy) VALUES(?,?,?)", (ts, equity, spy))
        self.db.commit()

    def curve(self, since: float = 0.0, max_points: int = 1500) -> list[list[float]]:
        rows = self.db.execute("SELECT ts, equity, spy FROM curve WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
        if len(rows) > max_points:
            step = len(rows) / max_points
            rows = [rows[int(i * step)] for i in range(max_points - 1)] + [rows[-1]]
        return [list(r) for r in rows]

    # ---- small remembered facts ---------------------------------------
    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        self.db.execute("INSERT INTO kv(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        self.db.commit()

    def wipe(self) -> None:
        for table in ("journal", "curve", "kv"):
            self.db.execute(f"DELETE FROM {table}")
        self.db.commit()
