"""US market session dates.

Every "today" in this project means a US *trading session*, not a calendar day
on whatever machine happens to be running the bot.

This matters because the machine is not in the US. On a UTC+8 host a US regular
session (09:30-16:00 ET) runs 21:30-04:00 local and therefore crosses local
midnight, so `date(created_at, 'localtime')` splits one trading day into two.
With SCAN_TIMES=21:45,03:30 both scheduled scans land in the same US session but
on different local dates, which silently disabled the duplicate-recommendation
guard and let the analyst daily quota reset mid-session.

Bare `date('now')` (UTC) is wrong for the mirror-image reason: UTC midnight
falls in the middle of the US afternoon.

Use these helpers for any per-day logic touching market activity.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")

# NYSE. Schwab routes US equities here and the bot trades nothing else.
_CALENDAR = "XNYS"

# SQLite stores timestamps as 'YYYY-MM-DD HH:MM:SS' in UTC.
_SQLITE_FORMAT = "%Y-%m-%d %H:%M:%S"


def as_utc(instant: datetime | None) -> datetime:
    """Normalise an optional instant to an aware UTC datetime.

    A naive datetime is assumed to be UTC, matching how SQLite stores
    created_at via datetime('now').
    """
    if instant is None:
        return datetime.now(timezone.utc)
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def market_session_date(instant: datetime | None = None) -> date:
    """Return the US market session date (an Eastern calendar date) for an instant.

    Defaults to now. DST is handled by zoneinfo, so this stays correct across
    the March and November transitions without a hardcoded offset.
    """
    return as_utc(instant).astimezone(MARKET_TZ).date()


def market_session_bounds_utc(instant: datetime | None = None) -> tuple[str, str]:
    """Return [start, end) UTC bounds of the session date containing `instant`.

    Both values are SQLite-comparable 'YYYY-MM-DD HH:MM:SS' UTC strings, so a
    query can use a plain range predicate:

        WHERE created_at >= ? AND created_at < ?

    A range comparison is preferable to `date(created_at, ...) = ...` because it
    leaves created_at unwrapped and therefore usable by an index.

    The span is 24 hours on a normal day, 23 on spring-forward and 25 on
    fall-back — which is exactly what a "day" means in that timezone.
    """
    session = market_session_date(instant)
    start_et = datetime.combine(session, time.min, tzinfo=MARKET_TZ)
    end_et = datetime.combine(session + timedelta(days=1), time.min, tzinfo=MARKET_TZ)
    return (
        start_et.astimezone(timezone.utc).strftime(_SQLITE_FORMAT),
        end_et.astimezone(timezone.utc).strftime(_SQLITE_FORMAT),
    )


_calendar = None


def _nyse_calendar(minute=None):
    """The XNYS calendar, built once and rebuilt only when it runs out.

    Construction walks a multi-decade date range and costs seconds, so it is
    memoised. Imports are deferred to keep module import cheap for the callers
    that only want a session date.

    A built calendar ends about a year out and never grows, so a process alive
    longer than that would raise MinuteOutOfBounds from inside the order path.
    That fails closed, which is the right direction, but an unexplained crash
    where orders are submitted is not an acceptable way to get there — so an
    instant past the end triggers a rebuild, which re-derives the horizon from
    the current date.

    Only the upper bound rebuilds. An instant BEFORE the calendar starts is a
    twenty-year-old timestamp in a ledger created this month; that is bad data,
    not a stale horizon, and it should raise rather than be quietly absorbed.
    """
    global _calendar
    if _calendar is not None and minute is not None and minute > _calendar.last_minute:
        _calendar = None
    if _calendar is None:
        import exchange_calendars

        _calendar = exchange_calendars.get_calendar(_CALENDAR)
    return _calendar


def intended_session_date(instant: datetime | None = None) -> date:
    """Return the trading session an order submitted at `instant` executes in.

    This is NOT market_session_date. That one answers "which Eastern calendar
    day is this instant in"; this one answers "which session will the broker
    actually run this order in", and the two diverge outside market hours.

    An order entered 20:00 ET Friday has a Friday session date but Schwab
    queues it for **Monday's** regular session. Bucketing the daily notional
    ceiling by the former lets Friday night and Monday each claim a full
    allowance while both fill against Monday's — the ceiling is doubled by
    nothing more than the clock.

    The rule is one sentence: the first session whose *close* is strictly after
    `instant`. That covers pre-market (still today), after-hours (next
    session), weekends, holidays, and early closes uniformly. It needs a real
    exchange calendar: Good Friday is a market holiday but not a federal one,
    and the half-day after Thanksgiving closes at 13:00 ET, so neither a
    weekday rule nor a hardcoded 16:00 gets those right.
    """
    import pandas as pd

    minute = pd.Timestamp(as_utc(instant))
    return _nyse_calendar(minute).minute_to_session(minute, direction="next").date()
