"""The New York Stock Exchange clock. App displays use the viewer's local timezone.
Sydney fields remain for compatibility with historical research.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache

import pandas_market_calendars as calendars
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SYD = ZoneInfo("Australia/Sydney")
OPEN, CLOSE, EARLY_CLOSE = time(9, 30), time(16, 0), time(13, 0)

@lru_cache(maxsize=32)
def _year_sessions(year: int) -> dict[date, tuple[datetime, datetime]]:
    """Maintained NYSE holidays, special closures and early bells, computed offline.

    Cache a year at a time so live snapshots never repeatedly build a calendar.
    Updating the dependency supplies newly announced exceptional closures.
    """
    schedule = calendars.get_calendar("NYSE").schedule(
        start_date=f"{year}-01-01", end_date=f"{year}-12-31", tz="America/New_York")
    return {stamp.date(): (row.market_open.to_pydatetime(), row.market_close.to_pydatetime())
            for stamp, row in schedule.iterrows()}


def now_ny() -> datetime:
    return datetime.now(tz=NY)


def is_trading_day(d: date) -> bool:
    return d in _year_sessions(d.year)


def session(d: date) -> tuple[datetime, datetime]:
    """Opening and closing bell for a trading day, in New York time."""
    try:
        return _year_sessions(d.year)[d]
    except KeyError:
        raise ValueError(f"The NYSE has no regular session on {d.isoformat()}") from None


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def status(now: datetime | None = None) -> dict:
    """Where we are in the trading day: phase, the bells, and seconds until the next change."""
    now = (now or now_ny()).astimezone(NY)
    today = now.date()
    if is_trading_day(today):
        o, c = session(today)
        if now < o:
            phase, nxt = "pre", o
        elif now < c:
            phase, nxt = "open", c
        else:
            phase, nxt = "after", session(next_trading_day(today))[0]
    else:
        phase, nxt = "closed", session(next_trading_day(today))[0]
    sess_day = today if is_trading_day(today) and phase != "after" else next_trading_day(today)
    o, c = session(sess_day if phase != "open" else today)
    return {
        "phase": phase, "now_ny": now.isoformat(), "next_change": nxt.isoformat(),
        "seconds_to_change": max(0, int((nxt - now).total_seconds())),
        "session_open": o.isoformat(), "session_close": c.isoformat(),
        "session_open_local": o.astimezone().strftime("%a %I:%M %p %Z"),
        "session_close_local": c.astimezone().strftime("%a %I:%M %p %Z"),
        "session_open_sydney": o.astimezone(SYD).strftime("%a %-I:%M %p").replace("AM", "am").replace("PM", "pm"),
        "session_close_sydney": c.astimezone(SYD).strftime("%a %-I:%M %p").replace("AM", "am").replace("PM", "pm"),
    }


def last_completed_session(now: datetime | None = None) -> date:
    """The most recent trading day whose closing bell has already rung."""
    now = (now or now_ny()).astimezone(NY)
    d = now.date()
    if is_trading_day(d) and now >= session(d)[1] + timedelta(minutes=10):
        return d
    return previous_trading_day(d)


def minutes_to_close(now: datetime | None = None) -> float | None:
    now = (now or now_ny()).astimezone(NY)
    if not is_trading_day(now.date()):
        return None
    o, c = session(now.date())
    return (c - now).total_seconds() / 60 if o <= now < c else None


def fraction_of_session_elapsed(now: datetime | None = None) -> float:
    now = (now or now_ny()).astimezone(NY)
    if not is_trading_day(now.date()):
        return 1.0
    o, c = session(now.date())
    return min(1.0, max(0.0, (now - o) / (c - o)))
