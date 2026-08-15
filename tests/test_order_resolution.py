"""Operator resolution of orders the system cannot resolve itself.

`/resolve` is report-only by design (round-4 Q1): five matching fields establish
an order's SHAPE, not its PROVENANCE, and Schwab exposes no client-supplied
correlation id. So a human decides, and this is the audited API they decide
through.

Round-5 #8: the previous design named a manual override, gave it no broker-id
parameter (making `adopt` unimplementable), and said it "writes an audit row"
without defining a table. All three are fixed here.

The audit log is append-only. A repeated `keep_blocked` must not overwrite the
actor and evidence of the decision before it — the history of who decided what,
on what basis, is the whole point of auditing an override of a money guard.
"""
import os
import sqlite3

import pytest

from database.models import get_cursor, initialize_db
from database.order_accounting import order_commitment
from database.queries import (
    attach_broker_order_id,
    create_order,
    get_day_notional,
    get_order,
    get_resolution_events,
    mark_order_submit_unknown,
    resolve_order_manually,
    sweep_stale_pending_submits,
)

DB_PATH = "test_order_resolution.db"


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def _unresolved(conn, ticker="AAPL"):
    oid = create_order(
        conn, recommendation_id=None, ticker=ticker, side="buy", order_type="limit",
        requested_shares=10.0, reference_price=100.0, limit_price=100.5,
    )
    mark_order_submit_unknown(conn, oid, "read timeout after POST")
    return oid


def test_resolution_events_table_exists():
    conn = sqlite3.connect(DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "order_resolution_events" in tables


# --- adopt ---

def test_adopt_requires_a_broker_order_id():
    """Without one there is nothing to attach, so the order stays unpollable."""
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        with pytest.raises(ValueError):
            resolve_order_manually(conn, oid, "adopt", actor="darson",
                                   evidence="saw it in the Schwab app")


def test_adopt_attaches_the_id_and_makes_the_order_pollable():
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        resolve_order_manually(conn, oid, "adopt", actor="darson",
                               evidence="order BRK1 visible in Schwab",
                               broker_order_id="BRK1")
        row = get_order(conn, oid)
    assert row["status"] == "submitted"
    assert row["broker_order_id"] == "BRK1"


def test_adopt_does_not_release_any_capital():
    """Adopting says where the order is, not what it filled.

    fills_observed must stay 0 so the order keeps its full commitment until
    someone actually reads fill data.
    """
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        before = get_day_notional(conn)
        resolve_order_manually(conn, oid, "adopt", actor="darson",
                               evidence="BRK1 in Schwab", broker_order_id="BRK1")
        assert get_order(conn, oid)["fills_observed"] == 0
        assert get_day_notional(conn) == pytest.approx(before)


def test_adopt_cannot_steal_a_broker_id_already_attached_elsewhere():
    """One real broker order backs at most one ledger row."""
    with get_cursor(DB_PATH) as conn:
        taken = create_order(conn, None, "MSFT", "buy", "limit", 5.0, 200.0, 201.0)
        attach_broker_order_id(conn, taken, "BRK1")
        oid = _unresolved(conn)
        with pytest.raises(sqlite3.IntegrityError):
            resolve_order_manually(conn, oid, "adopt", actor="darson",
                                   evidence="mistake", broker_order_id="BRK1")


# --- confirmed_absent ---

def test_confirmed_absent_releases_the_capital():
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        assert get_day_notional(conn) == pytest.approx(1005.0)
        resolve_order_manually(conn, oid, "confirmed_absent", actor="darson",
                               evidence="no such order in Schwab order history")
        assert get_order(conn, oid)["status"] == "submit_failed"
        assert get_day_notional(conn) == 0.0


# --- keep_blocked ---

def test_keep_blocked_changes_nothing_about_the_order():
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        resolve_order_manually(conn, oid, "keep_blocked", actor="darson",
                               evidence="Schwab site down, checking later")
        row = get_order(conn, oid)
    assert row["status"] == "submit_unknown"
    assert order_commitment(row) == pytest.approx(1005.0)


# --- input validation ---

def test_unknown_resolution_is_refused():
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        with pytest.raises(ValueError):
            resolve_order_manually(conn, oid, "looks_fine", actor="darson", evidence="vibes")


@pytest.mark.parametrize("actor,evidence", [("", "reason"), ("  ", "reason"),
                                            ("darson", ""), ("darson", "   ")])
def test_actor_and_evidence_are_mandatory(actor, evidence):
    """An unattributed override of a money guard is not an audit trail."""
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        with pytest.raises(ValueError):
            resolve_order_manually(conn, oid, "keep_blocked", actor=actor, evidence=evidence)


@pytest.mark.parametrize("status", ["pending_submit", "submitted", "filled", "submit_failed"])
def test_only_unresolved_orders_can_be_manually_resolved(status):
    """Resolving a resolved order would let an operator overwrite real state."""
    with get_cursor(DB_PATH) as conn:
        oid = create_order(conn, None, "AAPL", "buy", "limit", 10.0, 100.0, 100.5)
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, oid))
        with pytest.raises(ValueError):
            resolve_order_manually(conn, oid, "keep_blocked", actor="darson", evidence="x")


def test_resolving_a_missing_order_is_refused():
    with get_cursor(DB_PATH) as conn:
        with pytest.raises(ValueError):
            resolve_order_manually(conn, 999, "keep_blocked", actor="darson", evidence="x")


# --- the audit log is append-only ---

def test_each_decision_appends_an_event():
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        resolve_order_manually(conn, oid, "keep_blocked", actor="darson",
                               evidence="Schwab down")
        resolve_order_manually(conn, oid, "keep_blocked", actor="ops",
                               evidence="still down")
        events = get_resolution_events(conn, oid)
    assert [e["actor"] for e in events] == ["darson", "ops"]
    assert events[0]["evidence"] == "Schwab down"


def test_an_event_records_the_transition_it_caused():
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        resolve_order_manually(conn, oid, "confirmed_absent", actor="darson",
                               evidence="absent from order history")
        event = get_resolution_events(conn, oid)[-1]
    assert event["previous_status"] == "submit_unknown"
    assert event["new_status"] == "submit_failed"


def test_a_rejected_resolution_writes_no_event():
    with get_cursor(DB_PATH) as conn:
        oid = _unresolved(conn)
        with pytest.raises(ValueError):
            resolve_order_manually(conn, oid, "adopt", actor="darson", evidence="no id given")
        assert get_resolution_events(conn, oid) == []


# --- ties back to the crash window ---

def test_an_order_stranded_by_a_crash_can_be_resolved():
    """sweep_stale_pending_submits is what makes this reachable at all.

    A pending_submit row with no broker id is refused by this API; the sweep
    moving it to submit_unknown is the bridge between round-5 #3 and #8.
    """
    with get_cursor(DB_PATH) as conn:
        oid = create_order(conn, None, "AAPL", "buy", "limit", 10.0, 100.0, 100.5)
        conn.execute(
            "UPDATE orders SET submitted_at = datetime('now','-1 hour') WHERE id = ?", (oid,)
        )
        sweep_stale_pending_submits(conn, older_than_seconds=300)
        resolve_order_manually(conn, oid, "adopt", actor="darson",
                               evidence="found BRK9 in Schwab", broker_order_id="BRK9")
        assert get_order(conn, oid)["broker_order_id"] == "BRK9"
