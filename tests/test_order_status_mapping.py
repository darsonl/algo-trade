"""map_broker_status: what a broker order payload says our order became (§11).

The sweep asks a different question from the working-order parser, so it needs a
different terminal set. `parse_working_orders` asks "is this order still
consuming exposure?" — REPLACED is not, because its successor is reported
separately. The sweep asks "may I free the recommendation?" — and there REPLACED
is the opposite of terminal: the order is alive under an id we do not hold.
"""
from schwab_client.order_payload import (
    SWEEP_TERMINAL_BROKER_STATUSES,
    map_broker_status,
)


def _payload(status, **extra):
    payload = {
        "status": status,
        "orderLegCollection": [
            {"instruction": "BUY",
             "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
             "quantity": 10},
        ],
    }
    payload.update(extra)
    return payload


def test_a_filled_order_is_terminal_and_maps_to_filled():
    update = map_broker_status(_payload("FILLED"))

    assert update.terminal is True
    assert update.status == "filled"


def _replaced(successor_id="999", **successor_extra):
    successor = {
        "orderId": successor_id,
        "quantity": 10,
        "price": 101.0,
        "orderLegCollection": [
            {"instruction": "BUY",
             "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
             "quantity": 10},
        ],
    }
    successor.update(successor_extra)
    return _payload("REPLACED", replacingOrderCollection=[successor])


def test_a_replaced_order_is_not_terminal():
    """The fail-open the spec warns about twice. REPLACED means the order is
    dead AND a new one took its place — still working, under an id we do not
    hold. Freeing the ticker here lets a second buy stack on a live order."""
    update = map_broker_status(_replaced())

    assert update.terminal is False


def test_a_replaced_order_carries_the_successor_id_to_follow():
    """Follow the pointer the broker gave us; never re-derive it by searching.
    A search cannot find the successor — it has a different price and falls
    outside any window anchored on the original submission."""
    update = map_broker_status(_replaced(successor_id="777"))

    assert update.successor_id == "777"


def test_a_replaced_order_with_no_successor_is_unresolvable_not_terminal():
    """Only a human may settle this. It is NOT terminal: we do not know the
    order is dead, only that we cannot see where it went."""
    payload = _payload("REPLACED", replacingOrderCollection=[])

    update = map_broker_status(payload)

    assert update.terminal is False
    assert update.status == "submit_unknown"
    assert update.successor_id is None


# ─── The allowlist defaults to "still open" ──────────────────────────────────


def test_a_working_order_is_not_terminal():
    assert map_broker_status(_payload("WORKING")).terminal is False


def test_the_literal_UNKNOWN_status_is_not_terminal():
    """Schwab's enum really does contain `UNKNOWN`. A denylist would call it
    terminal and free the ticker on the strength of no information."""
    assert map_broker_status(_payload("UNKNOWN")).terminal is False


def test_a_status_schwab_has_not_invented_yet_is_not_terminal():
    """The allowlist's whole purpose: a member added after this was written
    must read as open, not as permission to release capital."""
    assert map_broker_status(_payload("SOME_FUTURE_STATUS")).terminal is False


def test_a_missing_status_is_not_terminal():
    assert map_broker_status({}).terminal is False


# ─── The mapping table ───────────────────────────────────────────────────────


def test_canceled_maps_to_cancelled():
    """Schwab spells it with one L; our canonical name has two."""
    update = map_broker_status(_payload("CANCELED"))
    assert (update.terminal, update.status) == (True, "cancelled")


def test_expired_collapses_onto_cancelled():
    """For accounting they are identical — unfilled remainder released, any
    fill retained — so they share one canonical name and cannot disagree."""
    update = map_broker_status(_payload("EXPIRED"))
    assert (update.terminal, update.status) == (True, "cancelled")


def test_rejected_maps_to_rejected():
    update = map_broker_status(_payload("REJECTED"))
    assert (update.terminal, update.status) == (True, "rejected")


def test_every_mapped_status_produces_a_status_we_actually_store():
    """A mapping that emits a name the ledger does not know would write a row
    no ceiling query can see."""
    from database.order_accounting import ALL_ORDER_STATUSES

    for broker_status in SWEEP_TERMINAL_BROKER_STATUSES:
        update = map_broker_status(_payload(broker_status))
        assert update.status in ALL_ORDER_STATUSES, broker_status
