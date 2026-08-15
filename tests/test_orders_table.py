"""The orders table: durable state for anything that may have reached the broker.

The row is created BEFORE submission, so a broker-accepted order can never exist
outside the ledger. That ordering is why the crash window in
`sweep_stale_pending_submits` exists and has to be recoverable.

Round-5 #2 drove the interface: these functions take a CONNECTION, not a
db_path. The daily-notional check and the insert that reserves against it must
happen in one transaction, and a db_path-taking function opens its own second
connection — which then blocks on the caller's write lock and eventually raises
"database is locked". Taking a conn is what makes the cap check atomic.
"""
import os
import sqlite3

import pytest

from database.models import get_connection, get_cursor, immediate_transaction, initialize_db
from database.queries import (
    attach_broker_order_id,
    create_order,
    get_day_notional,
    get_open_buy_reservation,
    get_order,
    get_orders_by_status,
    mark_order_submit_failed,
    mark_order_submit_unknown,
    observe_fills,
    sweep_stale_pending_submits,
)

DB_PATH = "test_orders_table.db"


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def _new_order(conn, ticker="AAPL", side="buy", shares=10.0, ref=100.0, limit=100.5):
    return create_order(
        conn,
        recommendation_id=None,
        ticker=ticker,
        side=side,
        order_type="limit",
        requested_shares=shares,
        reference_price=ref,
        limit_price=limit,
    )


# --- table and lifecycle ---

def test_orders_table_exists():
    conn = sqlite3.connect(DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "orders" in tables


def test_new_order_starts_pending_submit_with_nothing_assumed():
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        row = get_order(conn, oid)
    assert row["status"] == "pending_submit"
    assert row["broker_order_id"] is None
    assert row["filled_shares"] == 0
    assert row["fills_observed"] == 0


def test_attaching_a_broker_id_moves_the_order_to_submitted():
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        attach_broker_order_id(conn, oid, "BRK1")
        row = get_order(conn, oid)
    assert row["status"] == "submitted"
    assert row["broker_order_id"] == "BRK1"


def test_a_broker_order_id_cannot_be_claimed_by_two_orders():
    """Guards manual `adopt`: the same real order must not back two ledger rows."""
    with get_cursor(DB_PATH) as conn:
        first, second = _new_order(conn), _new_order(conn, ticker="MSFT")
        attach_broker_order_id(conn, first, "BRK1")
        with pytest.raises(sqlite3.IntegrityError):
            attach_broker_order_id(conn, second, "BRK1")


def test_submit_unknown_records_the_reason_and_is_not_terminal():
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        mark_order_submit_unknown(conn, oid, "read timeout after POST")
        row = get_order(conn, oid)
    assert row["status"] == "submit_unknown"
    assert "timeout" in row["failure_reason"]


def test_submit_failed_records_the_reason():
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        mark_order_submit_failed(conn, oid, "400 invalid symbol")
        row = get_order(conn, oid)
    assert row["status"] == "submit_failed"


def test_get_orders_by_status_filters():
    with get_cursor(DB_PATH) as conn:
        kept = _new_order(conn)
        gone = _new_order(conn, ticker="MSFT")
        mark_order_submit_failed(conn, gone, "nope")
        rows = get_orders_by_status(conn, ("pending_submit",))
    assert [r["id"] for r in rows] == [kept]


# --- daily notional, via order_commitment ---

def test_day_notional_reserves_the_limit_price_for_an_open_order():
    with get_cursor(DB_PATH) as conn:
        _new_order(conn)
        assert get_day_notional(conn) == pytest.approx(1005.0)


def test_day_notional_excludes_sells():
    with get_cursor(DB_PATH) as conn:
        _new_order(conn, side="sell")
        assert get_day_notional(conn) == 0.0


def test_day_notional_releases_a_definitively_refused_order():
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        mark_order_submit_failed(conn, oid, "400")
        assert get_day_notional(conn) == 0.0


def test_day_notional_retains_an_ambiguous_submission():
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        mark_order_submit_unknown(conn, oid, "timeout")
        assert get_day_notional(conn) == pytest.approx(1005.0)


def test_day_notional_keeps_a_partial_fill_after_cancellation():
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        attach_broker_order_id(conn, oid, "BRK1")
        observe_fills(conn, oid, filled_shares=4.0, filled_notional=398.0, status="cancelled")
        assert get_day_notional(conn) == pytest.approx(398.0)


def test_day_notional_fails_closed_on_an_unobserved_cancellation():
    """Terminal status with no fill observation must not release the budget."""
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (oid,))
        assert get_day_notional(conn) == pytest.approx(1005.0)


def test_day_notional_buckets_both_daily_scans_into_one_session():
    """21:45 and 03:30 Taipei are 09:45 and 15:30 ET of the SAME US session.

    Bucketing on the host calendar splits them across two dates and resets the
    ceiling mid-session — the defect commit 36761da removed elsewhere.
    """
    from datetime import datetime, timezone
    evening_scan = datetime(2026, 8, 17, 13, 45, tzinfo=timezone.utc)  # 09:45 ET Mon
    overnight_scan = datetime(2026, 8, 17, 19, 30, tzinfo=timezone.utc)  # 15:30 ET Mon

    with get_cursor(DB_PATH) as conn:
        for stamp in (evening_scan, overnight_scan):
            oid = _new_order(conn)
            conn.execute(
                "UPDATE orders SET submitted_at = ? WHERE id = ?",
                (stamp.strftime("%Y-%m-%d %H:%M:%S"), oid),
            )
        total = get_day_notional(conn, instant=overnight_scan)
    assert total == pytest.approx(2010.0)


def test_day_notional_ignores_a_previous_session():
    from datetime import datetime, timezone
    last_week = datetime(2026, 8, 10, 13, 45, tzinfo=timezone.utc)
    now = datetime(2026, 8, 17, 19, 30, tzinfo=timezone.utc)
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        conn.execute(
            "UPDATE orders SET submitted_at = ? WHERE id = ?",
            (last_week.strftime("%Y-%m-%d %H:%M:%S"), oid),
        )
        assert get_day_notional(conn, instant=now) == 0.0


# --- portfolio reservation is a different number ---

def test_open_buy_reservation_excludes_filled_shares():
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        attach_broker_order_id(conn, oid, "BRK1")
        observe_fills(conn, oid, filled_shares=4.0, filled_notional=398.0,
                      status="partially_filled")
        # 6 shares still working at 100.5; the filled 4 are already broker position value
        assert get_open_buy_reservation(conn) == pytest.approx(603.0)


# --- the transaction property round-5 #2 demanded ---

def test_cap_check_and_reservation_commit_together():
    with immediate_transaction(DB_PATH) as conn:
        before = get_day_notional(conn)
        _new_order(conn)
    with get_cursor(DB_PATH) as conn:
        assert before == 0.0
        assert get_day_notional(conn) == pytest.approx(1005.0)


def test_a_rejected_guard_leaves_no_reservation_behind():
    """If the ceiling check fails after the insert, the row must not persist."""
    with pytest.raises(RuntimeError):
        with immediate_transaction(DB_PATH) as conn:
            _new_order(conn)
            raise RuntimeError("daily_notional exceeded")
    with get_cursor(DB_PATH) as conn:
        assert get_orders_by_status(conn, ("pending_submit",)) == []


def test_a_second_writer_cannot_interleave_with_the_cap_check():
    """Two processes must not both read the same total and both reserve against it."""
    with immediate_transaction(DB_PATH) as conn:
        get_day_notional(conn)
        _new_order(conn)

        other = get_connection(DB_PATH)
        other.execute("PRAGMA busy_timeout=50")
        try:
            with pytest.raises(sqlite3.OperationalError):
                other.execute("BEGIN IMMEDIATE")
        finally:
            other.close()


# --- the crash window (round-5 #3) ---

def test_a_stale_order_with_no_broker_id_becomes_resolvable():
    """Committed before submission, then the process died.

    The order may exist at Schwab. Left as pending_submit it is unreachable: the
    status sweep needs a broker id and resolution only accepts unresolved rows.
    """
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        conn.execute(
            "UPDATE orders SET submitted_at = datetime('now','-1 hour') WHERE id = ?", (oid,)
        )
        swept = sweep_stale_pending_submits(conn, older_than_seconds=300)
        assert swept == [oid]
        assert get_order(conn, oid)["status"] == "submit_unknown"


def test_a_recent_pending_submit_is_left_alone():
    """It is probably just mid-flight; sweeping it would invent an unknown."""
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        assert sweep_stale_pending_submits(conn, older_than_seconds=300) == []
        assert get_order(conn, oid)["status"] == "pending_submit"


def test_sweeping_never_touches_an_order_that_reached_the_broker():
    with get_cursor(DB_PATH) as conn:
        oid = _new_order(conn)
        attach_broker_order_id(conn, oid, "BRK1")
        conn.execute(
            "UPDATE orders SET submitted_at = datetime('now','-1 hour') WHERE id = ?", (oid,)
        )
        assert sweep_stale_pending_submits(conn, older_than_seconds=300) == []
