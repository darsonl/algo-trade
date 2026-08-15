"""Replacement chains: when the broker moves an order to a new id.

Editing an order in the Schwab UI does not mutate it. Schwab kills the original
and creates a NEW order under a NEW id, at possibly different quantity and
price. Two ways to get this wrong, and earlier drafts managed both:

  - Treat REPLACED as terminal: frees the symbol while a live replacement sits
    at the broker (round-4 #4).
  - Follow the id but not the economics: the ledger keeps reserving the OLD
    amount. 5 shares at $100 replaced by 10 at $150 leaves $500 reserved while
    $1,500 is actionable, so a further $1,500 approval passes a $2,000 ceiling
    against $3,000 of real exposure (round-5 #6).

The rule this settles: when the broker tells you where the order went, follow
the pointer — never re-derive it by searching, and never carry the predecessor's
numbers onto the successor.

Anything ambiguous — several successors, a missing id, a changed symbol or side,
a successor whose economics are absent, a loop — leaves the predecessor
UNRESOLVED and fully reserved. Under-reserving places trades that breach the
ceiling; over-reserving only blocks trades. Only one of those is recoverable.
"""
import os

import pytest

from database.models import get_cursor, initialize_db
from database.queries import (
    adopt_replacement,
    create_order,
    get_day_notional,
    get_order,
    get_orders_by_status,
)
from schwab_client.order_payload import extract_replacement

DB_PATH = "test_order_replacement.db"


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def _payload(successors=(), filled_qty=0.0, filled_price=0.0, status="REPLACED"):
    payload = {
        "orderId": "BRK1",
        "status": status,
        "quantity": 5.0,
        "filledQuantity": filled_qty,
        "orderLegCollection": [
            {"instruction": "BUY", "instrument": {"symbol": "AAPL"}, "quantity": 5.0}
        ],
        "price": 100.0,
    }
    if filled_qty:
        payload["orderActivityCollection"] = [
            {"executionLegs": [{"quantity": filled_qty, "price": filled_price}]}
        ]
    if successors:
        payload["replacingOrderCollection"] = list(successors)
    return payload


def _successor(order_id="BRK2", symbol="AAPL", instruction="BUY", qty=10.0, price=150.0):
    return {
        "orderId": order_id,
        "quantity": qty,
        "price": price,
        "orderLegCollection": [
            {"instruction": instruction, "instrument": {"symbol": symbol}, "quantity": qty}
        ],
    }


def _live_order(conn, shares=5.0, ref=100.0, limit=100.0, broker_id="BRK1"):
    oid = create_order(conn, None, "AAPL", "buy", "limit", shares, ref, limit)
    conn.execute(
        "UPDATE orders SET status='submitted', broker_order_id=? WHERE id=?", (broker_id, oid)
    )
    return oid


# --- the pure extractor ---

def test_no_replacement_collection_means_no_successor():
    replacement, reason = extract_replacement(_payload())
    assert replacement is None and reason is None


def test_a_single_successor_is_extracted_with_its_own_economics():
    replacement, reason = extract_replacement(_payload(successors=[_successor()]))
    assert reason is None
    assert replacement.successor_id == "BRK2"
    assert replacement.quantity == 10.0
    assert replacement.limit_price == 150.0
    assert replacement.symbol == "AAPL"
    assert replacement.side == "buy"


def test_several_successors_are_ambiguous():
    payload = _payload(successors=[_successor("BRK2"), _successor("BRK3")])
    replacement, reason = extract_replacement(payload)
    assert replacement is None and "ambiguous" in reason.lower()


def test_a_successor_without_an_id_is_unresolvable():
    bad = _successor()
    del bad["orderId"]
    replacement, reason = extract_replacement(_payload(successors=[bad]))
    assert replacement is None and reason


def test_a_successor_without_economics_is_unresolvable():
    """A bare id gives no quantity or price, so its commitment cannot be computed."""
    replacement, reason = extract_replacement(_payload(successors=[{"orderId": "BRK2"}]))
    assert replacement is None and reason


def test_a_malformed_payload_is_unresolvable_not_a_crash():
    replacement, reason = extract_replacement({"replacingOrderCollection": "nonsense"})
    assert replacement is None and reason


# --- adopting the replacement ---

def test_the_successor_is_reserved_at_its_own_price_not_the_predecessors():
    """The round-5 #6 scenario: 5 at $100 becomes 10 at $150."""
    with get_cursor(DB_PATH) as conn:
        oid = _live_order(conn)
        assert get_day_notional(conn) == pytest.approx(500.0)

        adopt_replacement(conn, oid, _payload(successors=[_successor()]))

        assert get_day_notional(conn) == pytest.approx(1500.0)


def test_the_predecessor_stops_reserving_once_its_fills_are_known():
    with get_cursor(DB_PATH) as conn:
        oid = _live_order(conn)
        adopt_replacement(conn, oid, _payload(successors=[_successor()]))
        predecessor = get_order(conn, oid)
    assert predecessor["status"] == "cancelled"
    assert predecessor["fills_observed"] == 1


def test_a_partial_fill_before_replacement_is_retained():
    """2 of 5 filled at $99 before the edit; that capital really moved."""
    with get_cursor(DB_PATH) as conn:
        oid = _live_order(conn)
        payload = _payload(successors=[_successor()], filled_qty=2.0, filled_price=99.0)
        adopt_replacement(conn, oid, payload)
        predecessor = get_order(conn, oid)
        # 198 spent on the predecessor + 10 x 150 live on the successor
        assert predecessor["filled_notional"] == pytest.approx(198.0)
        assert get_day_notional(conn) == pytest.approx(198.0 + 1500.0)


def test_the_successor_row_records_the_chain_edge():
    with get_cursor(DB_PATH) as conn:
        oid = _live_order(conn)
        adopt_replacement(conn, oid, _payload(successors=[_successor()]))
        successor = get_orders_by_status(conn, ("submitted",))[-1]
    assert successor["predecessor_order_id"] == oid
    assert successor["broker_order_id"] == "BRK2"
    assert successor["fills_observed"] == 0


def test_adopting_returns_none_when_there_is_no_replacement():
    with get_cursor(DB_PATH) as conn:
        oid = _live_order(conn)
        assert adopt_replacement(conn, oid, _payload()) is None


# --- everything ambiguous stays reserved ---

@pytest.mark.parametrize("successors,label", [
    ([_successor("BRK2"), _successor("BRK3")], "two successors"),
    ([{"orderId": "BRK2"}], "no economics"),
    ([_successor(symbol="MSFT")], "symbol changed"),
    ([_successor(instruction="SELL")], "side changed"),
])
def test_an_ambiguous_replacement_leaves_the_order_unresolved(successors, label):
    with get_cursor(DB_PATH) as conn:
        oid = _live_order(conn)
        adopt_replacement(conn, oid, _payload(successors=successors))
        row = get_order(conn, oid)
    assert row["status"] == "submit_unknown", label


def test_an_ambiguous_replacement_keeps_the_full_commitment():
    """Fail closed: we do not know what is live, so we assume the worst."""
    with get_cursor(DB_PATH) as conn:
        oid = _live_order(conn)
        adopt_replacement(conn, oid, _payload(successors=[{"orderId": "BRK2"}]))
        assert get_day_notional(conn) == pytest.approx(500.0)


def test_a_changed_symbol_never_creates_a_successor_row():
    with get_cursor(DB_PATH) as conn:
        oid = _live_order(conn)
        adopt_replacement(conn, oid, _payload(successors=[_successor(symbol="MSFT")]))
        assert len(get_orders_by_status(conn, ("submitted", "submit_unknown"))) == 1


# --- chain safety ---

def test_a_chain_can_advance_more_than_once():
    with get_cursor(DB_PATH) as conn:
        first = _live_order(conn)
        adopt_replacement(conn, first, _payload(successors=[_successor("BRK2")]))
        second = get_orders_by_status(conn, ("submitted",))[-1]

        second_payload = _payload(successors=[_successor("BRK3", qty=12.0, price=160.0)])
        adopt_replacement(conn, second["id"], second_payload)

        assert get_day_notional(conn) == pytest.approx(12.0 * 160.0)


def test_a_loop_back_to_an_earlier_broker_id_is_refused():
    """BRK2 replaced 'by' BRK1 would walk the chain forever."""
    with get_cursor(DB_PATH) as conn:
        first = _live_order(conn)
        adopt_replacement(conn, first, _payload(successors=[_successor("BRK2")]))
        second = get_orders_by_status(conn, ("submitted",))[-1]

        adopt_replacement(conn, second["id"], _payload(successors=[_successor("BRK1")]))

        assert get_order(conn, second["id"])["status"] == "submit_unknown"


def test_an_over_long_chain_is_refused():
    """A bounded depth stops a pathological chain from consuming the ledger."""
    with get_cursor(DB_PATH) as conn:
        current = _live_order(conn)
        for n in range(2, 8):
            adopt_replacement(
                conn, current, _payload(successors=[_successor(f"BRK{n}")]), max_depth=4
            )
            rows = get_orders_by_status(conn, ("submitted",))
            if not rows:
                break
            current = rows[-1]["id"]
        assert any(r["status"] == "submit_unknown" for r in
                   [get_order(conn, i) for i in range(1, 9) if get_order(conn, i)])
