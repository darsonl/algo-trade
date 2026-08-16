"""US market session date helpers.

The machine runs in Asia/Taipei (UTC+8) while the bot trades US equities. A US
regular session (09:30-16:00 ET) is 21:30-04:00 Taipei and therefore crosses
local midnight, so machine-local calendar dates split one session in two.
"""
from datetime import date, datetime, timezone

import pytest

from market_time import (
    intended_session_date,
    market_session_bounds_utc,
    market_session_date,
)

# The two configured scan times as real instants, both inside ONE US session:
#   21:45 Taipei Aug 14 == 13:45 UTC == 09:45 ET Aug 14
#   03:30 Taipei Aug 15 == 19:30 UTC == 15:30 ET Aug 14
SCAN_A = datetime(2026, 8, 14, 13, 45, tzinfo=timezone.utc)
SCAN_B = datetime(2026, 8, 14, 19, 30, tzinfo=timezone.utc)


def test_session_date_is_the_eastern_calendar_date():
    assert market_session_date(SCAN_A) == date(2026, 8, 14)


def test_both_configured_scans_share_one_session_date():
    """The regression this whole fix exists for."""
    assert market_session_date(SCAN_A) == market_session_date(SCAN_B)


def test_late_evening_eastern_is_the_same_session_date():
    """22:00 ET is still the ET calendar day, even though it is next-day UTC."""
    instant = datetime(2026, 8, 15, 2, 0, tzinfo=timezone.utc)  # 22:00 ET Aug 14
    assert market_session_date(instant) == date(2026, 8, 14)


def test_after_eastern_midnight_is_the_next_session_date():
    instant = datetime(2026, 8, 15, 5, 0, tzinfo=timezone.utc)  # 01:00 ET Aug 15
    assert market_session_date(instant) == date(2026, 8, 15)


def test_naive_datetime_is_treated_as_utc():
    assert market_session_date(datetime(2026, 8, 14, 13, 45)) == date(2026, 8, 14)


def test_defaults_to_now_without_raising():
    assert isinstance(market_session_date(), date)


# --- bounds ---

def test_bounds_are_utc_strings_in_sqlite_format():
    start, end = market_session_bounds_utc(SCAN_A)
    for value in (start, end):
        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")  # raises if malformed
    assert start < end


def test_both_scans_fall_inside_the_same_bounds():
    start, end = market_session_bounds_utc(SCAN_A)
    for instant in (SCAN_A, SCAN_B):
        stamp = instant.strftime("%Y-%m-%d %H:%M:%S")
        assert start <= stamp < end


def test_bounds_computed_from_either_scan_are_identical():
    assert market_session_bounds_utc(SCAN_A) == market_session_bounds_utc(SCAN_B)


def test_previous_session_falls_outside_the_bounds():
    start, _end = market_session_bounds_utc(SCAN_A)
    previous = datetime(2026, 8, 13, 19, 30, tzinfo=timezone.utc)  # 15:30 ET Aug 13
    assert previous.strftime("%Y-%m-%d %H:%M:%S") < start


def test_bounds_span_24_hours_on_a_normal_day():
    start, end = market_session_bounds_utc(SCAN_A)
    delta = datetime.strptime(end, "%Y-%m-%d %H:%M:%S") - datetime.strptime(
        start, "%Y-%m-%d %H:%M:%S"
    )
    assert delta.total_seconds() == 24 * 3600


def test_spring_forward_day_is_23_hours():
    """DST correctness: 2026-03-08 is the US spring-forward date."""
    instant = datetime(2026, 3, 8, 17, 0, tzinfo=timezone.utc)  # 12:00 EST->EDT day
    start, end = market_session_bounds_utc(instant)
    delta = datetime.strptime(end, "%Y-%m-%d %H:%M:%S") - datetime.strptime(
        start, "%Y-%m-%d %H:%M:%S"
    )
    assert delta.total_seconds() == 23 * 3600


def test_fall_back_day_is_25_hours():
    """2026-11-01 is the US fall-back date."""
    instant = datetime(2026, 11, 1, 16, 0, tzinfo=timezone.utc)
    start, end = market_session_bounds_utc(instant)
    delta = datetime.strptime(end, "%Y-%m-%d %H:%M:%S") - datetime.strptime(
        start, "%Y-%m-%d %H:%M:%S"
    )
    assert delta.total_seconds() == 25 * 3600


# --- intended session attribution (round-5 #7) ---
#
# market_session_date answers "which ET calendar day is this instant in".
# intended_session_date answers a different question: "which trading session
# will an order submitted at this instant actually execute in". They agree
# during a session and diverge everywhere else — after the close, at weekends,
# on holidays, and after an early close.
#
# The rule: the first session whose CLOSE is strictly after the instant.

def test_intended_session_is_today_during_the_session():
    instant = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)  # 11:00 ET Mon
    assert intended_session_date(instant) == date(2026, 8, 17)


def test_intended_session_is_today_before_the_open():
    """Pre-market orders queue for this session's open, not the next one."""
    instant = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)  # 08:00 ET Mon
    assert intended_session_date(instant) == date(2026, 8, 17)


def test_intended_session_rolls_forward_after_the_close():
    instant = datetime(2026, 8, 17, 20, 30, tzinfo=timezone.utc)  # 16:30 ET Mon
    assert intended_session_date(instant) == date(2026, 8, 18)


def test_friday_night_is_attributed_to_monday():
    """The exact defect: submitted_at says Friday, Schwab queues it for Monday."""
    instant = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)  # 20:00 ET Fri
    assert intended_session_date(instant) == date(2026, 8, 17)


def test_saturday_is_attributed_to_monday():
    instant = datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc)  # 12:00 ET Sat
    assert intended_session_date(instant) == date(2026, 8, 17)


def test_night_before_a_holiday_skips_the_holiday():
    """Good Friday is a market holiday but not a federal one, so a weekend-only
    rule buckets this into a Friday that never trades."""
    instant = datetime(2026, 4, 3, 0, 0, tzinfo=timezone.utc)  # 20:00 ET Thu Apr 2
    assert intended_session_date(instant) == date(2026, 4, 6)


def test_after_an_early_close_rolls_forward():
    """Black Friday 2025 closed at 13:00 ET. A hardcoded 16:00 would call this
    still-open and bucket it into a session that had already ended."""
    instant = datetime(2025, 11, 28, 19, 0, tzinfo=timezone.utc)  # 14:00 ET
    assert intended_session_date(instant) == date(2025, 12, 1)


def test_before_an_early_close_is_still_that_session():
    instant = datetime(2025, 11, 28, 17, 0, tzinfo=timezone.utc)  # 12:00 ET
    assert intended_session_date(instant) == date(2025, 11, 28)


def test_intended_session_treats_naive_datetime_as_utc():
    assert intended_session_date(datetime(2026, 8, 15, 0, 0)) == date(2026, 8, 17)


def test_intended_session_defaults_to_now_without_raising():
    assert isinstance(intended_session_date(), date)


def test_calendar_is_rebuilt_when_an_instant_falls_beyond_it():
    """The XNYS calendar is built once and ends roughly a year out, so a process
    running longer than that would raise MinuteOutOfBounds inside the order
    path. Failing closed is the right direction but an opaque crash is not; the
    calendar must extend itself instead."""
    import exchange_calendars

    import market_time

    original = market_time._calendar
    try:
        market_time._calendar = exchange_calendars.get_calendar(
            "XNYS", start="2020-01-02", end="2020-12-31"
        )
        instant = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)  # 20:00 ET Fri
        assert intended_session_date(instant) == date(2026, 8, 17)
    finally:
        market_time._calendar = original
