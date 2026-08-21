"""Day-bucketing must follow the US market session, not the machine calendar.

Regression tests for the Taipei/US-session split: the machine is UTC+8, so a US
session spans two local dates and every `date(..., 'localtime')` comparison
silently splits one trading day in half.
"""
import os
from datetime import datetime, timezone

import pytest

from database.models import initialize_db, get_cursor
from database.queries import (
    create_recommendation,
    ticker_recommended_today,
    increment_analyst_call_count,
    get_analyst_call_count_today,
    create_position,
    get_open_positions,
)

DB_PATH = "test_session_day_bucketing.db"

# One US session, two Taipei calendar dates.
SCAN_A = datetime(2026, 8, 14, 13, 45, tzinfo=timezone.utc)  # 09:45 ET / 21:45 Taipei Aug 14
SCAN_B = datetime(2026, 8, 14, 19, 30, tzinfo=timezone.utc)  # 15:30 ET / 03:30 Taipei Aug 15
PREV_SESSION = datetime(2026, 8, 13, 19, 30, tzinfo=timezone.utc)  # 15:30 ET Aug 13


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def _rec_at(instant, ticker="AAPL"):
    """Create a recommendation and backdate created_at to a specific UTC instant."""
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker=ticker, signal="BUY", reasoning="t", price=100.0,
        dividend_yield=None, pe_ratio=None,
    )
    with get_cursor(DB_PATH) as conn:
        conn.execute(
            "UPDATE recommendations SET created_at = ?, expires_at = ? WHERE id = ?",
            (
                instant.strftime("%Y-%m-%d %H:%M:%S"),
                (instant.replace(hour=23)).strftime("%Y-%m-%d %H:%M:%S"),
                rec_id,
            ),
        )
    return rec_id


# --- ticker_recommended_today ---

def test_second_scan_of_the_same_session_sees_the_first():
    """THE BUG: 09:45 ET and 15:30 ET are one session but two Taipei dates."""
    _rec_at(SCAN_A)
    assert ticker_recommended_today(DB_PATH, "AAPL", instant=SCAN_B) is True


def test_same_scan_instant_sees_itself():
    _rec_at(SCAN_A)
    assert ticker_recommended_today(DB_PATH, "AAPL", instant=SCAN_A) is True


def test_previous_session_is_not_today():
    _rec_at(PREV_SESSION)
    assert ticker_recommended_today(DB_PATH, "AAPL", instant=SCAN_B) is False


def test_untouched_ticker_is_not_recommended():
    _rec_at(SCAN_A, ticker="AAPL")
    assert ticker_recommended_today(DB_PATH, "MSFT", instant=SCAN_B) is False


def test_rejected_recommendation_does_not_block():
    rec_id = _rec_at(SCAN_A)
    with get_cursor(DB_PATH) as conn:
        conn.execute("UPDATE recommendations SET status='rejected' WHERE id=?", (rec_id,))
    assert ticker_recommended_today(DB_PATH, "AAPL", instant=SCAN_B) is False


# --- analyst quota ---

def test_quota_does_not_reset_mid_session():
    """ANALYST_DAILY_LIMIT must not silently double across Taipei midnight."""
    increment_analyst_call_count(DB_PATH, "claude", "m", instant=SCAN_A)
    increment_analyst_call_count(DB_PATH, "claude", "m", instant=SCAN_B)
    assert get_analyst_call_count_today(DB_PATH, "claude", "m", instant=SCAN_B) == 2


def test_quota_does_reset_on_a_new_session():
    increment_analyst_call_count(DB_PATH, "claude", "m", instant=PREV_SESSION)
    assert get_analyst_call_count_today(DB_PATH, "claude", "m", instant=SCAN_B) == 0


def test_quota_is_tracked_per_provider():
    """Still true, and per MODEL within a provider too — see
    tests/test_per_model_quota.py, which is where that half is pinned."""
    increment_analyst_call_count(DB_PATH, "claude", "m", instant=SCAN_A)
    increment_analyst_call_count(DB_PATH, "gemini", "m", instant=SCAN_B)
    assert get_analyst_call_count_today(DB_PATH, "claude", "m", instant=SCAN_B) == 1
    assert get_analyst_call_count_today(DB_PATH, "gemini", "m", instant=SCAN_B) == 1


# --- positions.entry_date ---

def test_entry_date_uses_the_session_date():
    create_position(DB_PATH, "AAPL", shares=10, avg_cost_usd=100.0, instant=SCAN_B)
    assert get_open_positions(DB_PATH)[0]["entry_date"] == "2026-08-14"


def test_entry_date_default_is_not_left_to_sqlite():
    """models.py's DEFAULT date('now') is UTC and disagreed with the localtime
    writes in queries.py. Inserts must always supply the session date."""
    create_position(DB_PATH, "AAPL", shares=10, avg_cost_usd=100.0)
    entry = get_open_positions(DB_PATH)[0]["entry_date"]
    from market_time import market_session_date
    assert entry == market_session_date().isoformat()
