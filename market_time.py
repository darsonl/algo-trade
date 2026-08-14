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

# SQLite stores timestamps as 'YYYY-MM-DD HH:MM:SS' in UTC.
_SQLITE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _as_utc(instant: datetime | None) -> datetime:
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
    return _as_utc(instant).astimezone(MARKET_TZ).date()


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
