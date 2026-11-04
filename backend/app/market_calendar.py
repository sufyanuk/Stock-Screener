"""US equity market calendar (NYSE sessions, holidays, early closes, DST) via exchange_calendars."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

NY = ZoneInfo("America/New_York")


@lru_cache
def _cal():
    return xcals.get_calendar("XNYS")


def is_session(d: date) -> bool:
    return bool(_cal().is_session(pd.Timestamp(d)))


def previous_sessions(d: date, n: int) -> list[date]:
    """The ``n`` sessions ending at ``d`` (inclusive if ``d`` is a session), ascending."""
    cal = _cal()
    ts = pd.Timestamp(d)
    if not cal.is_session(ts):
        ts = cal.date_to_session(ts, direction="previous")
    idx = cal.sessions.get_loc(ts)
    return [s.date() for s in cal.sessions[max(0, idx - n + 1): idx + 1]]


def last_completed_session(now: datetime) -> date:
    cal = _cal()
    local = now.astimezone(NY)
    d = local.date()
    if is_session(d):
        close = cal.session_close(pd.Timestamp(d)).tz_convert(NY)
        if local >= close.to_pydatetime():
            return d
    return previous_sessions(d - timedelta(days=1), 1)[-1]


def session_times(d: date) -> tuple[datetime, datetime] | None:
    if not is_session(d):
        return None
    cal = _cal()
    o = cal.session_open(pd.Timestamp(d)).tz_convert(NY).to_pydatetime()
    c = cal.session_close(pd.Timestamp(d)).tz_convert(NY).to_pydatetime()
    return o, c


def market_phase(now: datetime) -> str:
    local = now.astimezone(NY)
    t = session_times(local.date())
    if not t:
        return "closed (holiday/weekend)"
    o, c = t
    if local < datetime.combine(local.date(), time(4, 0), NY):
        return "closed"
    if local < o:
        return "pre-market"
    if local < c:
        return "open"
    if local < datetime.combine(local.date(), time(20, 0), NY):
        return "after-hours"
    return "closed"
