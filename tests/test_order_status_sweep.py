"""fetch_order + the terminal sweep (spec v4 step 11).

Covering `approved` with the partial unique index needs a RELEASE VALVE, and
v2 shipped the index without one: it named a `completed` transition owned by a
poller that did not exist, so the first buy of any ticker would have blocked
that ticker forever. This is that valve, and it ships BEFORE the index.
"""
from unittest.mock import MagicMock

import pytest

from schwab_client.orders import fetch_order


def _response(payload, status_code=200):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    return resp


def _config():
    from config import Config
    c = Config()
    c.schwab_account_hash = "hash"
    return c


def test_fetch_order_returns_the_full_payload_not_a_status_string():
    """v3 specified `-> str`. Round-4 finding 4 showed a bare string cannot
    carry `replacingOrderCollection` or `filledQuantity`, both of which the
    caller needs to follow a chain and to price a partial fill."""
    client = MagicMock()
    client.get_order.return_value = _response(
        {"orderId": 123, "status": "FILLED", "filledQuantity": 10,
         "replacingOrderCollection": []}
    )

    payload = fetch_order(_config(), "123", client=client)

    assert payload["status"] == "FILLED"
    assert payload["filledQuantity"] == 10
    assert "replacingOrderCollection" in payload


def test_fetch_order_asks_the_broker_for_that_id_on_our_account():
    client = MagicMock()
    client.get_order.return_value = _response({"orderId": 456, "status": "WORKING"})

    fetch_order(_config(), "456", client=client)

    client.get_order.assert_called_once_with("456", "hash")


def test_fetch_order_validates_the_transport_before_parsing():
    """An HTTP error body is a structurally valid dict. Parsing first and
    checking later lets a 401 masquerade as an order payload — the same shape
    that made `get_positions` read an auth failure as 'the account is empty'."""
    client = MagicMock()
    resp = _response({"errors": ["Invalid token"]}, status_code=401)
    resp.raise_for_status.side_effect = RuntimeError("401 Unauthorized")
    client.get_order.return_value = resp

    with pytest.raises(RuntimeError):
        fetch_order(_config(), "789", client=client)


def test_fetch_order_raises_rather_than_returning_an_empty_payload():
    """`{}` would map to 'not terminal' and look like a clean answer. A failed
    read must reach the caller so the sweep can leave the row alone."""
    client = MagicMock()
    client.get_order.return_value = _response([])

    with pytest.raises(ValueError):
        fetch_order(_config(), "111", client=client)


# ─── complete_recommendation: the release valve ──────────────────────────────

import os
import tempfile

from database.models import initialize_db
from database.queries import (
    complete_recommendation,
    create_order,
    create_recommendation,
    get_cursor,
    get_order,
    get_recommendation,
    update_recommendation_status,
)


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    initialize_db(path)
    return path


def _approved_rec(db_path, ticker="AAPL"):
    rec_id = create_recommendation(
        db_path, ticker=ticker, signal="BUY", reasoning="r", price=100.0,
        dividend_yield=0.0, pe_ratio=15.0
    )
    update_recommendation_status(db_path, rec_id, "approved")
    return rec_id


def test_an_approved_recommendation_completes(db_path):
    rec_id = _approved_rec(db_path)

    assert complete_recommendation(db_path, rec_id, "FILLED") is True
    assert get_recommendation(db_path, rec_id)["status"] == "completed"


def test_completing_twice_is_a_no_op(db_path):
    """Every scan sweeps. The second pass must not re-report or re-transition."""
    rec_id = _approved_rec(db_path)
    complete_recommendation(db_path, rec_id, "FILLED")

    assert complete_recommendation(db_path, rec_id, "FILLED") is False


def test_a_pending_recommendation_is_not_completed(db_path):
    """Pending rows belong to the buttons and to expiry, not to the sweep.
    Completing one would retire a recommendation nobody ever acted on."""
    rec_id = create_recommendation(
        db_path, ticker="AAPL", signal="BUY", reasoning="r", price=100.0,
        dividend_yield=0.0, pe_ratio=15.0
    )

    assert complete_recommendation(db_path, rec_id, "FILLED") is False
    assert get_recommendation(db_path, rec_id)["status"] == "pending"


def test_a_rejected_order_still_completes_its_recommendation(db_path):
    """Nothing was bought, but the recommendation's life is over. Leaving it
    `approved` is what blocks the ticker forever under the partial index."""
    rec_id = _approved_rec(db_path)

    assert complete_recommendation(db_path, rec_id, "REJECTED") is True


def test_a_non_terminal_broker_status_is_refused(db_path):
    """Guard against the caller that sweeps on the wrong predicate. WORKING
    means the order is live; completing it frees the ticker under a live order."""
    rec_id = _approved_rec(db_path)

    with pytest.raises(ValueError):
        complete_recommendation(db_path, rec_id, "WORKING")

    assert get_recommendation(db_path, rec_id)["status"] == "approved"


# ─── sweep_terminal_recommendations ──────────────────────────────────────────

from unittest.mock import patch

from database.queries import get_orders_by_status


def _config_db(db_path):
    from config import Config
    c = Config()
    c.db_path = db_path
    c.dry_run = False
    c.schwab_account_hash = "hash"
    return c


def _submitted_order(db_path, rec_id, broker_order_id="B1", ticker="AAPL", shares=10):
    with get_cursor(db_path) as conn:
        order_id = create_order(
            conn, recommendation_id=rec_id, ticker=ticker, side="buy",
            order_type="limit", requested_shares=shares, reference_price=100.0,
            limit_price=101.0,
        )
        if broker_order_id is not None:
            from database.queries import attach_broker_order_id
            attach_broker_order_id(conn, order_id, broker_order_id)
    return order_id


def _filled_payload(shares=10, price=100.0):
    return {
        "orderId": "B1", "status": "FILLED", "filledQuantity": shares,
        "orderLegCollection": [
            {"instruction": "BUY",
             "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
             "quantity": shares},
        ],
        "orderActivityCollection": [
            {"executionLegs": [{"quantity": shares, "price": price}]},
        ],
    }


@pytest.mark.asyncio
async def test_a_filled_order_completes_its_recommendation(db_path):
    import main
    rec_id = _approved_rec(db_path)
    _submitted_order(db_path, rec_id)

    with patch.object(main, "fetch_order", return_value=_filled_payload()):
        await main.sweep_terminal_recommendations(_config_db(db_path))

    assert get_recommendation(db_path, rec_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_a_filled_order_records_what_actually_filled(db_path):
    """`fills_observed` gates the release of capital. A terminal status with an
    unverified zero fill releases the whole budget — finding 6."""
    import main
    rec_id = _approved_rec(db_path)
    order_id = _submitted_order(db_path, rec_id)

    with patch.object(main, "fetch_order", return_value=_filled_payload(shares=10, price=100.0)):
        await main.sweep_terminal_recommendations(_config_db(db_path))

    with get_cursor(db_path) as conn:
        row = get_order(conn, order_id)
    assert row["status"] == "filled"
    assert row["filled_shares"] == 10
    assert row["filled_notional"] == 1000.0
    assert row["fills_observed"]


@pytest.mark.asyncio
async def test_a_working_order_leaves_the_recommendation_approved(db_path):
    import main
    rec_id = _approved_rec(db_path)
    _submitted_order(db_path, rec_id)

    with patch.object(main, "fetch_order", return_value={"orderId": "B1", "status": "WORKING"}):
        await main.sweep_terminal_recommendations(_config_db(db_path))

    assert get_recommendation(db_path, rec_id)["status"] == "approved"


@pytest.mark.asyncio
async def test_a_replaced_order_follows_the_chain_and_does_not_complete(db_path):
    """The documented fail-open. REPLACED means a NEW order is live under an id
    we do not hold; completing here frees the ticker under a live order."""
    import main
    rec_id = _approved_rec(db_path)
    payload = {
        "orderId": "B1", "status": "REPLACED",
        "orderLegCollection": [
            {"instruction": "BUY",
             "instrument": {"symbol": "AAPL", "assetType": "EQUITY"}, "quantity": 10},
        ],
        "replacingOrderCollection": [
            {"orderId": "B2", "quantity": 12, "price": 105.0,
             "orderLegCollection": [
                 {"instruction": "BUY",
                  "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
                  "quantity": 12}]},
        ],
    }
    _submitted_order(db_path, rec_id)

    with patch.object(main, "fetch_order", return_value=payload):
        await main.sweep_terminal_recommendations(_config_db(db_path))

    assert get_recommendation(db_path, rec_id)["status"] == "approved"
    with get_cursor(db_path) as conn:
        live = get_orders_by_status(conn, ("submitted",))
    assert [o["broker_order_id"] for o in live] == ["B2"]


@pytest.mark.asyncio
async def test_a_replaced_order_with_no_successor_becomes_unresolved(db_path):
    """Never terminal. We do not know the order is dead, only that we cannot
    see where it went — which is a human's problem, via /resolve."""
    import main
    rec_id = _approved_rec(db_path)
    order_id = _submitted_order(db_path, rec_id)
    payload = {"orderId": "B1", "status": "REPLACED", "replacingOrderCollection": []}

    with patch.object(main, "fetch_order", return_value=payload):
        await main.sweep_terminal_recommendations(_config_db(db_path))

    with get_cursor(db_path) as conn:
        row = get_order(conn, order_id)
    assert row["status"] == "submit_unknown"
    assert get_recommendation(db_path, rec_id)["status"] == "approved"


@pytest.mark.asyncio
async def test_a_broker_read_failure_completes_nothing(db_path):
    """`[]` or an exception is not information. Completing on a failed read is
    how a broker outage frees a ticker that has a live order against it."""
    import main
    rec_id = _approved_rec(db_path)

    _submitted_order(db_path, rec_id)

    with patch.object(main, "fetch_order", side_effect=RuntimeError("503")):
        await main.sweep_terminal_recommendations(_config_db(db_path))

    assert get_recommendation(db_path, rec_id)["status"] == "approved"


@pytest.mark.asyncio
async def test_one_failed_read_does_not_abort_the_whole_sweep(db_path):
    """A sweep that stops at the first outage leaves every later ticker blocked
    for reasons that have nothing to do with those tickers."""
    import main
    bad_rec = _approved_rec(db_path, ticker="BAD")
    good_rec = _approved_rec(db_path, ticker="AAPL")
    _submitted_order(db_path, bad_rec, broker_order_id="B_BAD", ticker="BAD")
    _submitted_order(db_path, good_rec, broker_order_id="B_GOOD", ticker="AAPL")

    def _fetch(config, broker_order_id, **kwargs):
        if broker_order_id == "B_BAD":
            raise RuntimeError("503")
        return _filled_payload()

    with patch.object(main, "fetch_order", side_effect=_fetch):
        await main.sweep_terminal_recommendations(_config_db(db_path))

    assert get_recommendation(db_path, bad_rec)["status"] == "approved"
    assert get_recommendation(db_path, good_rec)["status"] == "completed"


@pytest.mark.asyncio
async def test_an_order_with_no_broker_id_is_never_asked_about(db_path):
    """A pending_submit row has no id to ask about. Calling the broker with a
    NULL id is a request for someone else's order."""
    import main
    rec_id = _approved_rec(db_path)
    _submitted_order(db_path, rec_id, broker_order_id=None)

    with patch.object(main, "fetch_order") as mock_fetch:
        await main.sweep_terminal_recommendations(_config_db(db_path))

    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_the_sweep_is_skipped_in_dry_run(db_path):
    """Simulated orders have no broker counterpart to ask about."""
    import main
    rec_id = _approved_rec(db_path)
    _submitted_order(db_path, rec_id)
    config = _config_db(db_path)
    config.dry_run = True

    with patch.object(main, "fetch_order") as mock_fetch:
        await main.sweep_terminal_recommendations(config)

    mock_fetch.assert_not_called()


# ─── Terminal, but the fills are not trustworthy ─────────────────────────────
#
# `fills_observed` is what releases capital: order_commitment() charges a
# terminal order its FULL requested amount until someone has actually looked,
# because a default zero and a verified zero are indistinguishable. Flipping the
# flag on a payload that does not really carry fills is therefore the fail-open
# direction — it books a zero fill for an order that filled.


@pytest.mark.asyncio
async def test_a_filled_payload_with_no_quantity_does_not_book_a_zero_fill(db_path):
    """FILLED but silent about how much. The status is honoured — the
    recommendation is done — but the capital stays committed at the worst case."""
    import main
    rec_id = _approved_rec(db_path)
    order_id = _submitted_order(db_path, rec_id)
    payload = _filled_payload()
    del payload["filledQuantity"]

    with patch.object(main, "fetch_order", return_value=payload):
        await main.sweep_terminal_recommendations(_config_db(db_path))

    with get_cursor(db_path) as conn:
        row = get_order(conn, order_id)
    assert row["status"] == "filled"
    assert not row["fills_observed"]


@pytest.mark.asyncio
async def test_a_fill_that_cannot_be_priced_does_not_book_a_zero_notional(db_path):
    """extract_fills reports quantity with zero notional when no execution
    prices are available, and says the caller must decide. Booking $0 for ten
    real shares releases the whole reservation."""
    import main
    rec_id = _approved_rec(db_path)
    order_id = _submitted_order(db_path, rec_id)
    payload = _filled_payload()
    del payload["orderActivityCollection"]

    with patch.object(main, "fetch_order", return_value=payload):
        await main.sweep_terminal_recommendations(_config_db(db_path))

    with get_cursor(db_path) as conn:
        row = get_order(conn, order_id)
    assert not row["fills_observed"], "a fill with no price must not be booked"


@pytest.mark.asyncio
async def test_an_unpriced_terminal_order_keeps_its_full_commitment(db_path):
    """The consequence that matters: the ceiling still sees the money."""
    import main
    from database.order_accounting import order_commitment
    rec_id = _approved_rec(db_path)
    order_id = _submitted_order(db_path, rec_id, ticker="AAPL", shares=10)
    payload = _filled_payload()
    del payload["filledQuantity"]

    with patch.object(main, "fetch_order", return_value=payload):
        await main.sweep_terminal_recommendations(_config_db(db_path))

    with get_cursor(db_path) as conn:
        row = get_order(conn, order_id)
    assert order_commitment(row) == 10 * 101.0  # requested shares at the LIMIT


@pytest.mark.asyncio
async def test_a_rejected_order_may_book_its_zero_fill(db_path):
    """The one zero that can be trusted without anyone having looked: the
    broker refused outright, so no capital moved."""
    import main
    rec_id = _approved_rec(db_path)
    order_id = _submitted_order(db_path, rec_id)
    payload = {"orderId": "B1", "status": "REJECTED"}

    with patch.object(main, "fetch_order", return_value=payload):
        await main.sweep_terminal_recommendations(_config_db(db_path))

    with get_cursor(db_path) as conn:
        row = get_order(conn, order_id)
    assert row["status"] == "rejected"
    assert row["fills_observed"]


# ─── The sweep is actually wired into the scan ───────────────────────────────


@pytest.mark.asyncio
async def test_run_scan_sweeps_before_it_screens(db_path):
    """A release valve nothing calls is not a release valve. It must also run
    BEFORE the scan builds its universe, so a ticker freed by this sweep is
    eligible in the very same scan rather than the next one."""
    import main
    from unittest.mock import AsyncMock, MagicMock

    order = []
    config = _config_db(db_path)
    config.dry_run = True

    async def _sweep(cfg):
        order.append("sweep")
        return 0

    def _universe(*args, **kwargs):
        order.append("universe")
        return []

    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()

    with patch.object(main, "sweep_terminal_recommendations", side_effect=_sweep), \
         patch.object(main, "get_universe", side_effect=_universe), \
         patch.object(main, "get_top_sp500_by_fundamentals", return_value=[]), \
         patch.object(main, "alert_stuck_orders", new=AsyncMock()), \
         patch.object(main, "_drain_ops_outbox", new=AsyncMock()):
        await main.run_scan(bot, config)

    assert order[:2] == ["sweep", "universe"]
