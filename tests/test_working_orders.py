"""Broker working orders (spec v4 step 8, round-4 finding 9).

A buy placed by hand in the Schwab app is *working and unfilled*: it appears in
no position and in no local ledger row. Sourcing working orders from our own
table alone therefore under-states exposure, and guards 9 and 10 both pass while
a live order for that symbol already exists.

The status rule is the one that matters here. **Terminal is an allowlist**, never
a denylist: Schwab's enum has a literal `UNKNOWN`, and any status this code does
not recognise -- including ones Schwab adds later -- must count as still live.
Over-counting exposure rejects a legitimate trade, which is recoverable.
Under-counting opens the ceiling, which is not.
"""
import pytest

from schwab_client.order_payload import (
    UnpriceableOrder,
    parse_working_orders,
)


def _order(order_id="B1", symbol="AAPL", status="WORKING", quantity=10.0,
           filled=0.0, price=100.0, instruction="BUY", order_type="LIMIT"):
    payload = {
        "orderId": order_id,
        "status": status,
        "quantity": quantity,
        "filledQuantity": filled,
        "orderType": order_type,
        "orderLegCollection": [
            {"instruction": instruction, "instrument": {"symbol": symbol}}
        ],
    }
    if price is not None:
        payload["price"] = price
    return payload


# --- shape ---

def test_a_working_buy_is_returned_in_the_shape_preflight_expects():
    got = parse_working_orders([_order()])
    assert got == [{
        "broker_order_id": "B1", "symbol": "AAPL", "side": "buy", "notional": 1000.0,
    }]


def test_broker_order_id_is_a_string_so_it_merges_with_ledger_rows():
    """Guard 9 de-duplicates by broker id. An int here and a str in the ledger
    would never match, and the same order would be counted twice."""
    got = parse_working_orders([_order(order_id=12345)])
    assert got[0]["broker_order_id"] == "12345"


def test_an_empty_list_is_an_empty_list_not_a_failure():
    assert parse_working_orders([]) == []


# --- the status allowlist ---

@pytest.mark.parametrize("status", ["FILLED", "CANCELED", "EXPIRED", "REJECTED", "REPLACED"])
def test_terminal_statuses_are_excluded(status):
    assert parse_working_orders([_order(status=status)]) == []


@pytest.mark.parametrize("status", [
    "WORKING", "ACCEPTED", "QUEUED", "NEW", "PENDING_ACTIVATION",
    "AWAITING_PARENT_ORDER", "AWAITING_MANUAL_REVIEW", "PENDING_CANCEL",
    "PENDING_REPLACE", "AWAITING_UR_OUT",
])
def test_live_statuses_are_included(status):
    assert len(parse_working_orders([_order(status=status)])) == 1


def test_the_literal_unknown_status_counts_as_live():
    """Schwab's enum really has UNKNOWN. Treating it as terminal would release
    capital the order may still be holding."""
    assert len(parse_working_orders([_order(status="UNKNOWN")])) == 1


def test_an_unrecognised_future_status_counts_as_live():
    """The allowlist must not be inverted into a denylist by the next API
    revision. A status this code has never seen is live until proven dead."""
    assert len(parse_working_orders([_order(status="SOME_NEW_SCHWAB_STATUS")])) == 1


def test_a_missing_status_counts_as_live():
    payload = _order()
    del payload["status"]
    assert len(parse_working_orders([payload])) == 1


# --- notional ---

def test_notional_reserves_only_the_unfilled_remainder():
    """The filled part is already reported as position market value. Counting it
    here as well would double-charge the portfolio ceiling."""
    got = parse_working_orders([_order(quantity=10.0, filled=4.0, price=100.0)])
    assert got[0]["notional"] == pytest.approx(600.0)


def test_a_sell_reserves_nothing_but_is_still_reported():
    """Sells do not add buy exposure, but guard 10 must still see that a live
    order exists for the symbol."""
    got = parse_working_orders([_order(instruction="SELL")])
    assert got[0]["side"] == "sell"
    assert got[0]["notional"] == 0.0


def test_a_fully_filled_remainder_of_a_live_order_reserves_nothing():
    got = parse_working_orders([_order(quantity=10.0, filled=10.0, status="PENDING_CANCEL")])
    assert got[0]["notional"] == 0.0


# --- refusing to guess ---

def test_an_unpriceable_buy_raises_rather_than_being_skipped():
    """A market buy at the broker has no limit to price against. Skipping it
    would silently under-state exposure -- the open direction. The caller is
    told, and fails closed through guard 5."""
    with pytest.raises(UnpriceableOrder, match="B1"):
        parse_working_orders([_order(price=None, order_type="MARKET")])


def test_a_zero_price_buy_raises():
    """`.get('price', 0)` on an odd payload is how a live order comes to reserve
    nothing at all."""
    with pytest.raises(UnpriceableOrder):
        parse_working_orders([_order(price=0.0)])


def test_an_unpriceable_SELL_does_not_raise():
    """Sells reserve nothing, so an unpriced one costs the ceiling nothing."""
    got = parse_working_orders([_order(instruction="SELL", price=None, order_type="MARKET")])
    assert got[0]["notional"] == 0.0


def test_an_order_without_a_symbol_raises():
    payload = _order()
    payload["orderLegCollection"] = [{"instruction": "BUY", "instrument": {}}]
    with pytest.raises(UnpriceableOrder):
        parse_working_orders([payload])


def test_an_order_without_an_id_raises():
    payload = _order()
    del payload["orderId"]
    with pytest.raises(UnpriceableOrder):
        parse_working_orders([payload])


def test_a_non_list_payload_raises():
    """An HTTP error body is a valid dict. It must not parse to []."""
    with pytest.raises(ValueError):
        parse_working_orders({"error": "unauthorized"})


def test_a_non_dict_entry_raises():
    with pytest.raises(ValueError):
        parse_working_orders(["not an order"])


# --- the fetch ---

class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def get_orders_for_account(self, account_hash, **kwargs):
        self.calls.append((account_hash, kwargs))
        return self._resp


def _cfg():
    from types import SimpleNamespace
    return SimpleNamespace(schwab_account_hash="HASH")


def test_fetch_returns_parsed_working_orders():
    from schwab_client.orders import get_working_orders

    client = _Client(_Resp([_order()]))
    got = get_working_orders(_cfg(), client=client)
    assert got[0]["symbol"] == "AAPL"
    assert client.calls[0][0] == "HASH"


def test_fetch_validates_transport_before_parsing():
    """A 401 body is structurally a valid list-of-nothing. It must raise, never
    parse to [] -- that is precisely how get_positions read an outage as 'the
    account holds nothing' and OPENED the size guards."""
    from schwab_client.orders import get_working_orders

    client = _Client(_Resp([], status=401))
    with pytest.raises(RuntimeError, match="401"):
        get_working_orders(_cfg(), client=client)


def test_fetch_does_not_swallow_an_unpriceable_order():
    from schwab_client.orders import get_working_orders

    client = _Client(_Resp([_order(price=None, order_type="MARKET")]))
    with pytest.raises(UnpriceableOrder):
        get_working_orders(_cfg(), client=client)


# --- assembling the guard inputs ---

class _TwoCallClient:
    """get_account and get_orders_for_account, each independently failable."""

    def __init__(self, account=None, orders=None, account_raises=None, orders_raises=None):
        self._account, self._orders = account, orders
        self._account_raises, self._orders_raises = account_raises, orders_raises

    def get_account(self, account_hash, **kwargs):
        if self._account_raises:
            raise self._account_raises
        return _Resp(self._account)

    def get_orders_for_account(self, account_hash, **kwargs):
        if self._orders_raises:
            raise self._orders_raises
        return _Resp(self._orders)


_ACCOUNT = {"securitiesAccount": {"positions": [{
    "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
    "longQuantity": 3.0, "averagePrice": 100.0, "marketValue": 310.0,
}]}}


def test_snapshot_carries_both_reads_when_both_succeed():
    from schwab_client.orders import collect_broker_snapshot

    snap = collect_broker_snapshot(_cfg(), client=_TwoCallClient(_ACCOUNT, [_order()]))
    assert snap.readable
    assert snap.positions[0]["symbol"] == "AAPL"
    assert snap.working_orders[0]["symbol"] == "AAPL"


def test_a_failed_position_read_becomes_None_not_empty():
    from schwab_client.orders import collect_broker_snapshot

    snap = collect_broker_snapshot(
        _cfg(), client=_TwoCallClient(orders=[], account_raises=RuntimeError("HTTP 401"))
    )
    assert snap.positions is None
    assert not snap.readable


def test_a_failed_order_read_becomes_None_not_empty():
    from schwab_client.orders import collect_broker_snapshot

    snap = collect_broker_snapshot(
        _cfg(), client=_TwoCallClient(_ACCOUNT, orders_raises=RuntimeError("HTTP 500"))
    )
    assert snap.working_orders is None
    assert not snap.readable


def test_an_unpriceable_order_makes_the_whole_read_unusable():
    """One order we cannot price means we cannot state exposure at all."""
    from schwab_client.orders import collect_broker_snapshot

    snap = collect_broker_snapshot(
        _cfg(), client=_TwoCallClient(_ACCOUNT, [_order(price=None, order_type="MARKET")])
    )
    assert snap.working_orders is None


def test_collecting_never_raises():
    """It runs on the approval path, where the guards -- not an exception --
    decide what happens next."""
    from schwab_client.orders import collect_broker_snapshot

    snap = collect_broker_snapshot(
        _cfg(),
        client=_TwoCallClient(account_raises=RuntimeError("x"), orders_raises=RuntimeError("y")),
    )
    assert snap.positions is None and snap.working_orders is None


def test_a_genuinely_empty_account_is_readable():
    from schwab_client.orders import collect_broker_snapshot

    snap = collect_broker_snapshot(
        _cfg(), client=_TwoCallClient({"securitiesAccount": {"positions": []}}, [])
    )
    assert snap.readable
    assert snap.positions == [] and snap.working_orders == []


# --- end to end into the guard table ---

def test_a_manual_broker_buy_blocks_a_bot_buy_of_the_same_symbol():
    """Round-4 finding 9, end to end. The order exists only at the broker: no
    position, no local row. Before this, guards 9 and 10 both passed."""
    from datetime import datetime, timedelta, timezone

    from risk.preflight import TradeRequest, evaluate_trade
    from schwab_client.orders import collect_broker_snapshot
    from schwab_client.quotes import Quote

    now = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)
    client = _TwoCallClient(
        {"securitiesAccount": {"positions": []}},          # broker holds nothing
        [_order(order_id="MANUAL-1", symbol="AAPL")],      # but has a live AAPL buy
    )
    snap = collect_broker_snapshot(_cfg(), client=client)

    decision = evaluate_trade(
        TradeRequest(side="buy", ticker="AAPL", scan_price=100.0, user_id=1001,
                     guild_id=0, channel_id=0, expires_at=now + timedelta(hours=1)),
        quote=Quote(symbol="AAPL", bid=99.0, ask=100.0, last=99.5,
                    quote_time=now - timedelta(seconds=1)),
        broker=snap,
        local_orders=[],                                   # our ledger knows nothing
        day_notional=0.0,
        trading_enabled=True,
        config=_guard_config(),
        now=now,
    )
    assert decision.reason_code == "duplicate_symbol"


def test_a_broker_outage_refuses_instead_of_reading_the_book_as_empty():
    """The composed failure path: read fails -> None -> guard 5 -> refusal."""
    from datetime import datetime, timedelta, timezone

    from risk.preflight import TradeRequest, evaluate_trade
    from schwab_client.orders import collect_broker_snapshot
    from schwab_client.quotes import Quote

    now = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)
    snap = collect_broker_snapshot(
        _cfg(), client=_TwoCallClient(orders=[], account_raises=RuntimeError("HTTP 401"))
    )
    decision = evaluate_trade(
        TradeRequest(side="buy", ticker="AAPL", scan_price=100.0, user_id=1001,
                     guild_id=0, channel_id=0, expires_at=now + timedelta(hours=1)),
        quote=Quote(symbol="AAPL", bid=99.0, ask=100.0, last=99.5,
                    quote_time=now - timedelta(seconds=1)),
        broker=snap, local_orders=[], day_notional=0.0, trading_enabled=True,
        config=_guard_config(), now=now,
    )
    assert decision.reason_code == "broker_unavailable"


def _guard_config():
    from types import SimpleNamespace
    return SimpleNamespace(
        allowed_discord_user_ids="1001", discord_guild_id=0, discord_channel_id=0,
        max_position_size_usd=500.0, max_portfolio_usd=20000.0,
        max_daily_notional_usd=2000.0, approval_price_tolerance_pct=2.0,
        approval_slippage_buffer_pct=0.5, quote_max_age_s=30,
    )
