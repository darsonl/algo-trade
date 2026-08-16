"""`/resolve` candidate search and reporting (spec v4 step 12, §11, finding 3).

An ambiguous submission (`submit_unknown`) may or may not have reached the
broker. Matching fields establish an order's SHAPE, not its PROVENANCE, and
Schwab exposes no client-supplied correlation id -- two identical $500 buys may
both be ours. So this whole path **reads, ranks and reports**. It never writes
order status. Only `resolve_order_manually`, driven by a human, transitions a
row.

Two rules here invert `parse_working_orders`, deliberately:

1. **Terminal statuses are INCLUDED.** `parse_working_orders` skips them because
   filled shares are already position market value. Here a `FILLED` order in the
   window is the single most dangerous candidate -- it means a real position
   exists that our ledger never recorded. Filtering it out would hide the worst
   case.
2. **An unpriceable candidate does not raise.** `parse_working_orders` raises,
   because reserving zero for a live order opens the ceiling. A candidate with no
   limit price instead carries `limit_price=None`, and
   `record_candidate_observation` prices it at OUR order's `reference_price` -- a
   defined, conservative number. Raising would abort an entire report over one
   market order.
"""
import pytest

from schwab_client.order_payload import parse_candidate_orders

SINCE = "2026-08-16T13:30:00+0000"
UNTIL = "2026-08-16T21:00:00+0000"


def _order(order_id="B1", symbol="AAPL", status="WORKING", quantity=10.0,
           filled=0.0, price=100.0, instruction="BUY",
           entered="2026-08-16T14:00:00+0000"):
    payload = {
        "orderId": order_id,
        "status": status,
        "quantity": quantity,
        "filledQuantity": filled,
        "enteredTime": entered,
        "orderLegCollection": [
            {"instruction": instruction, "instrument": {"symbol": symbol}}
        ],
    }
    if price is not None:
        payload["price"] = price
    return payload


def _parse(payload, symbol="AAPL", side="buy", since=SINCE, until=UNTIL):
    return parse_candidate_orders(payload, symbol=symbol, side=side,
                                  since=since, until=until)


# --- shape ---

def test_a_matching_order_is_returned_with_everything_the_report_needs():
    got = _parse([_order()])
    assert got == [{
        "broker_order_id": "B1",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 10.0,
        "limit_price": 100.0,
        "status": "WORKING",
        "entered_at": "2026-08-16T14:00:00+0000",
    }]


def test_broker_order_id_is_a_string_so_it_matches_the_ledger():
    assert _parse([_order(order_id=12345)])[0]["broker_order_id"] == "12345"


def test_no_candidates_is_an_empty_list_not_a_failure():
    assert _parse([]) == []


# --- the inverted status rule: terminal candidates are the dangerous ones ---

@pytest.mark.parametrize("status", ["FILLED", "CANCELED", "EXPIRED",
                                    "REJECTED", "REPLACED"])
def test_terminal_candidates_are_included_unlike_working_orders(status):
    """A FILLED candidate means a real position our ledger never recorded.

    `parse_working_orders` skips these; reusing that filter here would hide
    exactly the case an operator most needs to see.
    """
    got = _parse([_order(status=status)])
    assert [c["status"] for c in got] == [status]


def test_an_unrecognised_status_is_included_too():
    assert len(_parse([_order(status="UNKNOWN")])) == 1


# --- filtering ---

def test_a_different_symbol_is_not_a_candidate():
    assert _parse([_order(symbol="MSFT")]) == []


def test_symbol_matching_ignores_case():
    assert len(_parse([_order(symbol="aapl")])) == 1


def test_the_opposite_side_is_not_a_candidate():
    assert _parse([_order(instruction="SELL")]) == []


def test_an_order_entered_before_the_window_is_not_a_candidate():
    assert _parse([_order(entered="2026-08-16T13:00:00+0000")]) == []


def test_an_order_entered_after_the_window_is_not_a_candidate():
    assert _parse([_order(entered="2026-08-16T22:00:00+0000")]) == []


def test_an_order_with_an_unreadable_time_is_kept_not_dropped():
    """Over-counting rejects a legitimate trade, which is recoverable.
    Under-counting opens the ceiling, which is not. So an unparseable
    enteredTime is included rather than silently filtered away.
    """
    assert len(_parse([_order(entered="not a timestamp")])) == 1
    assert len(_parse([_order(entered=None)])) == 1


# --- unpriceable candidates do not abort the report ---

def test_a_candidate_with_no_limit_price_carries_none_rather_than_raising():
    got = _parse([_order(price=None)])
    assert got[0]["limit_price"] is None


def test_a_zero_limit_price_is_also_none_not_zero():
    """Zero would price the candidate's notional at nothing, which is the one
    answer that reserves less than the truth."""
    assert _parse([_order(price=0)])[0]["limit_price"] is None


# --- failing closed on a malformed payload ---

def test_an_error_body_raises_rather_than_reading_as_no_candidates():
    """An HTTP error body is a structurally valid dict. Letting it fall through
    a .get() chain is how "no candidates exist" gets invented."""
    with pytest.raises(ValueError, match="not a list"):
        _parse({"error": "unauthorized"})


def test_a_candidate_without_an_order_id_raises():
    with pytest.raises(ValueError, match="orderId"):
        _parse([_order(order_id=None)])


def test_an_entry_that_names_no_symbol_raises_rather_than_being_skipped():
    """We cannot tell whether it is a candidate, and silently dropping it
    under-states the worst case the reservation is supposed to cover."""
    entry = _order()
    entry["orderLegCollection"] = []
    with pytest.raises(ValueError, match="symbol"):
        _parse([entry])


def test_an_entry_that_is_not_an_object_raises():
    with pytest.raises(ValueError, match="not an order"):
        _parse(["nonsense"])


# --- the broker read ---

class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    def __init__(self, resp):
        self._resp, self.calls = resp, []

    def get_orders_for_account(self, account_hash, **kwargs):
        self.calls.append((account_hash, kwargs))
        return self._resp


def _cfg():
    from types import SimpleNamespace
    return SimpleNamespace(schwab_account_hash="HASH")


def test_find_recent_orders_returns_parsed_candidates():
    from schwab_client.orders import find_recent_orders

    client = _Client(_Resp([_order()]))
    got = find_recent_orders(_cfg(), symbol="AAPL", side="buy",
                             since=SINCE, until=UNTIL, client=client)
    assert [c["broker_order_id"] for c in got] == ["B1"]


def test_find_recent_orders_passes_the_window_to_the_broker():
    """Narrowing server-side keeps the response small and the search honest."""
    from schwab_client.orders import find_recent_orders

    client = _Client(_Resp([]))
    find_recent_orders(_cfg(), symbol="AAPL", side="buy",
                       since=SINCE, until=UNTIL, client=client)
    account_hash, kwargs = client.calls[0]
    assert account_hash == "HASH"
    assert kwargs["from_entered_datetime"] is not None
    assert kwargs["to_entered_datetime"] is not None


def test_find_recent_orders_validates_transport_before_parsing():
    """A 401 body must raise, never parse to []. `[]` here would tell an
    operator no candidate exists, which is the one answer that makes a
    `confirmed_absent` resolution look justified when it is not."""
    from schwab_client.orders import find_recent_orders

    client = _Client(_Resp([], status=401))
    with pytest.raises(RuntimeError, match="401"):
        find_recent_orders(_cfg(), symbol="AAPL", side="buy",
                           since=SINCE, until=UNTIL, client=client)


# ---------------------------------------------------------------------------
# The report: reads, ranks, reports. NEVER writes order status.
# ---------------------------------------------------------------------------

import os  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from database.models import get_cursor, initialize_db  # noqa: E402
from database.queries import (  # noqa: E402
    create_order,
    get_candidate_observations,
    get_order,
    mark_order_submit_unknown,
)

DB_PATH = "test_resolve_reporting.db"


@pytest.fixture
def db():
    initialize_db(DB_PATH)
    yield DB_PATH
    os.remove(DB_PATH)


def _report_cfg():
    return SimpleNamespace(
        db_path=DB_PATH, schwab_account_hash="HASH", resolve_lookback_min=30,
    )


def _unresolved(shares=5.0, limit=100.0, ticker="AAPL"):
    """An ambiguous $500 buy, the round-5 #5 scenario."""
    with get_cursor(DB_PATH) as conn:
        oid = create_order(conn, None, ticker, "buy", "limit", shares, 100.0, limit)
        mark_order_submit_unknown(conn, oid, "read timeout after POST")
    return oid


def _cand(broker_id="BRK2", qty=5.0, price=100.0, status="WORKING"):
    return {
        "broker_order_id": broker_id, "symbol": "AAPL", "side": "buy",
        "quantity": qty, "limit_price": price, "status": status,
        "entered_at": "2026-08-16T14:00:00+0000",
    }


def _run(monkeypatch, candidates):
    """Run the report with the broker read replaced.

    Patches `risk.resolution.find_recent_orders`, NOT the source module: it
    binds the name at import, so patching `schwab_client.orders` does nothing.
    """
    from risk import resolution

    def fake(config, **kwargs):
        if isinstance(candidates, Exception):
            raise candidates
        return list(candidates)

    monkeypatch.setattr(resolution, "find_recent_orders", fake)
    return resolution.report_unknown_submissions(_report_cfg())


def _status(order_id):
    with get_cursor(DB_PATH) as conn:
        return get_order(conn, order_id)["status"]


def _reservation(order_id):
    with get_cursor(DB_PATH) as conn:
        return get_order(conn, order_id)["reserved_notional_override"]


# --- the invariant: report-only ---

@pytest.mark.parametrize("candidates,label", [
    ([], "no candidates"),
    ([_cand()], "one exact candidate"),
    ([_cand(qty=3.0)], "one partial candidate"),
    ([_cand("BRK2"), _cand("BRK3")], "two candidates"),
])
def test_the_report_never_changes_order_status(db, monkeypatch, candidates, label):
    """Matching fields establish an order's SHAPE, not its PROVENANCE.

    Even a single exact match must not be adopted automatically: two identical
    $500 buys may both be ours, and Schwab offers no correlation id to tell
    them apart. Only a human, through resolve_order_manually, transitions a row.
    """
    oid = _unresolved()
    _run(monkeypatch, candidates)
    assert _status(oid) == "submit_unknown", label


def test_a_failed_broker_read_leaves_the_row_untouched(db, monkeypatch):
    oid = _unresolved()
    _run(monkeypatch, RuntimeError("HTTP 401"))
    assert _status(oid) == "submit_unknown"
    assert _reservation(oid) is None


def test_a_failed_read_is_reported_rather_than_raised(db, monkeypatch):
    """The report runs from a Discord command and from a scan. Raising would
    turn one unreadable order into a dead command."""
    _unresolved()
    out = _run(monkeypatch, RuntimeError("HTTP 401"))
    assert "401" in out


def test_one_unreadable_order_does_not_hide_the_others(db, monkeypatch):
    from risk import resolution
    _unresolved(ticker="AAPL")
    _unresolved(ticker="MSFT")

    def fake(config, **kwargs):
        if kwargs["symbol"] == "AAPL":
            raise RuntimeError("HTTP 500")
        return [_cand("BRK9")]

    monkeypatch.setattr(resolution, "find_recent_orders", fake)
    out = resolution.report_unknown_submissions(_report_cfg())
    assert "AAPL" in out and "MSFT" in out and "BRK9" in out


# --- worst-case reservation ---

def test_two_plausible_candidates_reserve_both(db, monkeypatch):
    """$500 submitted, two $500 candidates -> $1,000 reserved, not $500."""
    oid = _unresolved()
    _run(monkeypatch, [_cand("BRK2"), _cand("BRK3")])
    assert _reservation(oid) == 1000.0


def test_the_reservation_is_floored_at_our_own_commitment(db, monkeypatch):
    """Our order may be precisely the one the endpoint did not return."""
    oid = _unresolved()
    _run(monkeypatch, [_cand("BRK2", qty=1.0, price=10.0)])
    assert _reservation(oid) == 500.0


def test_finding_no_candidates_does_not_release_capital(db, monkeypatch):
    oid = _unresolved()
    _run(monkeypatch, [_cand("BRK2"), _cand("BRK3")])
    _run(monkeypatch, [])  # they filled, cancelled, or aged out
    assert _reservation(oid) == 1000.0


def test_candidates_are_persisted_for_the_audit_trail(db, monkeypatch):
    oid = _unresolved()
    _run(monkeypatch, [_cand("BRK2"), _cand("BRK3")])
    with get_cursor(DB_PATH) as conn:
        seen = get_candidate_observations(conn, oid)
    assert {c["broker_order_id"] for c in seen} == {"BRK2", "BRK3"}


# --- what the operator actually reads ---

def test_the_report_names_each_candidate_and_its_status(db, monkeypatch):
    _unresolved()
    out = _run(monkeypatch, [_cand("BRK2", status="FILLED")])
    assert "BRK2" in out and "FILLED" in out


def test_the_report_tells_the_operator_how_to_resolve(db, monkeypatch):
    oid = _unresolved()
    out = _run(monkeypatch, [_cand("BRK2")])
    assert "/resolve" in out and str(oid) in out


def test_nothing_unresolved_says_so_plainly(db, monkeypatch):
    out = _run(monkeypatch, [])
    assert "no unresolved" in out.lower()


def test_a_resolved_order_is_not_examined(db, monkeypatch):
    """Only UNRESOLVED_ORDER_STATUSES are in scope. Re-examining a settled row
    would let a stale candidate re-reserve capital already released."""
    with get_cursor(DB_PATH) as conn:
        create_order(conn, None, "AAPL", "buy", "limit", 5.0, 100.0, 100.0)
    out = _run(monkeypatch, [_cand("BRK2")])
    assert "no unresolved" in out.lower()
