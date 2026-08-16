"""Round-5 #7: the daily ceiling must bucket by the session an order EXECUTES
in, not the session it was submitted in.

The defect: an order entered 20:00 ET Friday is stamped Friday by submitted_at,
but Schwab queues it for Monday's regular session. Bucketing on submitted_at
puts it in Friday's bucket, so Monday opens with a fresh full allowance and both
orders fill Monday against one real ceiling. The cap is doubled by nothing more
than the clock, and it fails in the OPEN direction.

These tests pin instants rather than mocking a clock, following the project's
`instant=` convention.
"""
import os
from datetime import datetime, timezone

import pytest

from database.models import get_cursor, initialize_db
from database.queries import create_order, get_day_notional

DB_PATH = "test_intended_session_attribution.db"

# 20:00 ET Friday 2026-08-14. Executes Monday 2026-08-17.
FRIDAY_NIGHT = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)
# 11:00 ET Monday 2026-08-17. Executes the same day.
MONDAY_MIDDAY = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)
# 12:00 ET Saturday 2026-08-15. Also executes Monday.
SATURDAY = datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def _buy(conn, notional, instant):
    return create_order(
        conn,
        recommendation_id=None,
        ticker="AAPL",
        side="buy",
        order_type="limit",
        requested_shares=notional / 100.0,
        reference_price=100.0,
        limit_price=100.0,
        instant=instant,
    )


def test_friday_night_order_is_stamped_with_mondays_session():
    with get_cursor(DB_PATH) as conn:
        oid = _buy(conn, 500.0, FRIDAY_NIGHT)
        row = conn.execute(
            "SELECT submitted_at, intended_session_date FROM orders WHERE id = ?", (oid,)
        ).fetchone()
    assert row["submitted_at"].startswith("2026-08-15")   # Friday 20:00 ET in UTC
    assert row["intended_session_date"] == "2026-08-17"   # but it trades Monday


def test_friday_night_and_monday_orders_share_one_ceiling():
    """The headline regression. Both fill Monday, so both must count Monday."""
    with get_cursor(DB_PATH) as conn:
        _buy(conn, 500.0, FRIDAY_NIGHT)
        _buy(conn, 500.0, MONDAY_MIDDAY)
        total = get_day_notional(conn, instant=MONDAY_MIDDAY)
    assert total == pytest.approx(1000.0)


def test_weekend_order_also_counts_against_monday():
    with get_cursor(DB_PATH) as conn:
        _buy(conn, 500.0, SATURDAY)
        total = get_day_notional(conn, instant=MONDAY_MIDDAY)
    assert total == pytest.approx(500.0)


def test_friday_night_order_does_not_count_against_friday():
    """The other half: it must LEAVE Friday's bucket, not merely join Monday's."""
    friday_midday = datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc)  # 11:00 ET Fri
    with get_cursor(DB_PATH) as conn:
        _buy(conn, 500.0, FRIDAY_NIGHT)
        total = get_day_notional(conn, instant=friday_midday)
    assert total == 0.0


def test_orders_in_different_sessions_stay_separate():
    with get_cursor(DB_PATH) as conn:
        _buy(conn, 500.0, MONDAY_MIDDAY)
        tuesday = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)
        _buy(conn, 700.0, tuesday)
        assert get_day_notional(conn, instant=MONDAY_MIDDAY) == pytest.approx(500.0)
        assert get_day_notional(conn, instant=tuesday) == pytest.approx(700.0)


def test_ceiling_query_is_asked_about_the_intended_session_not_the_calendar_day():
    """Asked at 22:00 ET Monday, the ceiling is Tuesday's -- Monday is closed and
    anything entered now executes Tuesday, so Monday's fills must not count."""
    monday_night = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)  # 22:00 ET Mon
    with get_cursor(DB_PATH) as conn:
        _buy(conn, 500.0, MONDAY_MIDDAY)
        total = get_day_notional(conn, instant=monday_night)
    assert total == 0.0
