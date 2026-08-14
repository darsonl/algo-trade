"""US market session date helpers.

The machine runs in Asia/Taipei (UTC+8) while the bot trades US equities. A US
regular session (09:30-16:00 ET) is 21:30-04:00 Taipei and therefore crosses
local midnight, so machine-local calendar dates split one session in two.
"""
from datetime import date, datetime, timezone

import pytest

from market_time import market_session_date, market_session_bounds_utc

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
