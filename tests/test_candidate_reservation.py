"""Reserving against orders that MIGHT be ours.

When a submission outcome is ambiguous, `/resolve` looks for broker orders that
could be it. The design promised those candidates would be reserved worst-case
against both ceilings — but `/resolve` is report-only and nothing persisted the
observation, so the number appeared in Discord and nowhere else (round-5 #5).

The failure that makes this Critical: an ambiguous $500 submission has two
plausible $500 candidates. The channel says $1,000; the next daily check still
reads $500. Then the candidates fill or cancel, leave the working-order
endpoint, and nothing compensates for the reservation that was never taken.

So the reservation is durable and MONOTONIC while unresolved. A later
observation that finds fewer candidates — or none, because they have aged out of
the endpoint — must never lower it. Disappearing evidence is not evidence of
absence; only a human resolution clears it.
"""
import os
import sqlite3

import pytest

from database.models import get_cursor, initialize_db
from database.queries import (
    create_order,
    get_candidate_observations,
    get_day_notional,
    get_open_buy_reservation,
    get_order,
    mark_order_submit_unknown,
    record_candidate_observation,
    resolve_order_manually,
)

DB_PATH = "test_candidate_reservation.db"


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def _unresolved(conn, shares=5.0, limit=100.0):
    oid = create_order(conn, None, "AAPL", "buy", "limit", shares, 100.0, limit)
    mark_order_submit_unknown(conn, oid, "read timeout after POST")
    return oid


def _candidate(broker_id="BRK2", qty=5.0, price=100.0):
    return {
        "broker_order_id": broker_id,
        "symbol": "AAPL",
        "side": "buy",
        "quantity": qty,
        "limit_price": price,
    }


def test_candidates_table_exists():
    conn = sqlite3.connect(DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "order_candidates" in tables


def test_an_observation_is_persisted_for_audit():
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        record_candidate_observation(conn, oid, [_candidate("BRK2"), _candidate("BRK3")])
        observed = get_candidate_observations(conn, oid)
    assert sorted(c["broker_order_id"] for c in observed) == ["BRK2", "BRK3"]


def test_two_plausible_candidates_reserve_both(_two=None):
    """The exact round-5 #5 scenario: $500 submitted, two $500 candidates."""
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        assert get_day_notional(conn) == pytest.approx(500.0)

        record_candidate_observation(conn, oid, [_candidate("BRK2"), _candidate("BRK3")])

        assert get_day_notional(conn) == pytest.approx(1000.0)


def test_the_portfolio_ceiling_consumes_the_same_reservation():
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        record_candidate_observation(conn, oid, [_candidate("BRK2"), _candidate("BRK3")])
        assert get_open_buy_reservation(conn) == pytest.approx(1000.0)


def test_a_single_smaller_candidate_never_lowers_the_reservation():
    """Floored at the order's own commitment: our order may be the one not seen."""
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        record_candidate_observation(conn, oid, [_candidate("BRK2", qty=1.0, price=10.0)])
        assert get_day_notional(conn) == pytest.approx(500.0)


def test_candidates_ageing_out_of_the_endpoint_do_not_release_capital():
    """The hazard that makes this durable rather than computed on the fly."""
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        record_candidate_observation(conn, oid, [_candidate("BRK2"), _candidate("BRK3")])
        assert get_day_notional(conn) == pytest.approx(1000.0)

        record_candidate_observation(conn, oid, [])  # they filled, cancelled, or aged out

        assert get_day_notional(conn) == pytest.approx(1000.0)


def test_a_later_larger_observation_raises_the_reservation():
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        record_candidate_observation(conn, oid, [_candidate("BRK2")])
        record_candidate_observation(conn, oid, [_candidate("BRK2"), _candidate("BRK3")])
        assert get_day_notional(conn) == pytest.approx(1000.0)


# --- resolution is the only thing that clears it ---

def test_adopting_clears_the_override():
    """Once we know WHICH order is ours, its own economics govern."""
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        record_candidate_observation(conn, oid, [_candidate("BRK2"), _candidate("BRK3")])
        resolve_order_manually(conn, oid, "adopt", actor="darson",
                               evidence="BRK2 is ours", broker_order_id="BRK2")
        assert get_order(conn, oid)["reserved_notional_override"] is None
        assert get_day_notional(conn) == pytest.approx(500.0)


def test_confirming_absence_releases_everything():
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        record_candidate_observation(conn, oid, [_candidate("BRK2"), _candidate("BRK3")])
        resolve_order_manually(conn, oid, "confirmed_absent", actor="darson",
                               evidence="neither order is mine")
        assert get_day_notional(conn) == 0.0


def test_keep_blocked_retains_the_reservation():
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        record_candidate_observation(conn, oid, [_candidate("BRK2"), _candidate("BRK3")])
        resolve_order_manually(conn, oid, "keep_blocked", actor="darson",
                               evidence="Schwab down, cannot confirm")
        assert get_day_notional(conn) == pytest.approx(1000.0)


def test_only_unresolved_orders_accept_candidate_observations():
    with get_cursor(DB_PATH) as conn:
        oid = create_order(conn, None, "AAPL", "buy", "limit", 5.0, 100.0, 100.0)
        with pytest.raises(ValueError):
            record_candidate_observation(conn, oid, [_candidate("BRK2")])


def test_recording_against_a_missing_order_is_refused():
    with get_cursor(DB_PATH) as conn:
        with pytest.raises(ValueError):
            record_candidate_observation(conn, 999, [_candidate("BRK2")])


# --- migration ---

def test_override_column_reaches_an_existing_orders_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("ALTER TABLE orders DROP COLUMN reserved_notional_override")
    conn.commit()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)")]
    conn.close()
    assert "reserved_notional_override" not in cols

    initialize_db(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)")]
    conn.close()
    assert "reserved_notional_override" in cols
