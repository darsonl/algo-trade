"""Pure parsing of Schwab order payloads. No network, no config, no SQLite.

Kept separate from `orders.py` so the rules that interpret broker responses are
testable against fixtures, and so a malformed payload produces a *reason* rather
than an exception halfway through a database transaction.

Everything here fails toward "unresolvable". A caller that cannot determine what
an order became must keep reserving its capital; guessing is the direction that
places real trades against a ceiling that has already been consumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


# Statuses that mean an order is DONE and no longer holds capital. This is an
# ALLOWLIST and must stay one. Schwab's enum contains a literal `UNKNOWN`, and
# the API can gain members without asking us: anything not named here counts as
# still live. Over-counting exposure rejects a legitimate trade, which is
# recoverable; under-counting opens the ceiling, which is not.
#
# FILLED is terminal here because those shares are now reported as position
# market value -- counting them again would double-charge. REPLACED is terminal
# because its successor is reported separately, under a new id.
TERMINAL_BROKER_STATUSES = frozenset({
    "FILLED", "CANCELED", "EXPIRED", "REJECTED", "REPLACED",
})


# The sweep's terminal set, which is NOT `TERMINAL_BROKER_STATUSES` above.
#
# The two answer different questions and REPLACED separates them. The working-
# order parser asks "is this order still consuming exposure?" -- a REPLACED order
# is not, because the successor Schwab created is reported separately under its
# own id, so counting both would double-charge the ceiling. The sweep asks "may
# I free the recommendation?" -- and there REPLACED is the opposite of terminal:
# the order is alive under an id we do not hold, and freeing the ticker would
# let a second buy stack on top of a live one.
#
# Also an ALLOWLIST, for the same reason: `UNKNOWN` is a real member of Schwab's
# enum, and anything unrecognised must read as still open.
SWEEP_TERMINAL_BROKER_STATUSES = frozenset({
    "FILLED", "CANCELED", "EXPIRED", "REJECTED",
})

# Broker vocabulary -> ours. EXPIRED collapses onto 'cancelled' because for
# accounting they are identical (unfilled remainder released, any fill kept),
# and one canonical name means the status contract cannot disagree with itself.
_BROKER_STATUS_TO_ORDER_STATUS = {
    "FILLED": "filled",
    "CANCELED": "cancelled",
    "EXPIRED": "cancelled",
    "REJECTED": "rejected",
}

# Statuses where the order we asked about is gone and a DIFFERENT order carries
# on in its place. Neither is terminal; both mean "look somewhere else".
_REPLACEMENT_STATUSES = frozenset({"REPLACED", "PENDING_REPLACE"})


@dataclass(frozen=True)
class OrderStatusUpdate:
    """What a broker payload says one of our orders became."""

    status: str | None
    terminal: bool
    successor_id: str | None = None
    reason: str | None = None


class UnpriceableOrder(ValueError):
    """A live broker order whose exposure cannot be determined.

    Raised rather than skipped. A working order we cannot price is exactly the
    case where guessing zero is worst: it is live, it will cost money, and
    omitting it tells the guards the ceiling is emptier than it is.
    """


@dataclass(frozen=True)
class Replacement:
    """The order Schwab created to take a replaced order's place."""

    successor_id: str
    symbol: str
    side: str
    quantity: float
    limit_price: float | None


def _first_leg(payload: dict) -> dict:
    legs = payload.get("orderLegCollection")
    if isinstance(legs, list) and legs and isinstance(legs[0], dict):
        return legs[0]
    return {}


def _side(payload: dict) -> str | None:
    instruction = str(_first_leg(payload).get("instruction", "")).upper()
    if instruction.startswith("BUY"):
        return "buy"
    if instruction.startswith("SELL"):
        return "sell"
    return None


def _symbol(payload: dict) -> str | None:
    instrument = _first_leg(payload).get("instrument")
    if isinstance(instrument, dict):
        return instrument.get("symbol")
    return None


def extract_fills(payload: dict) -> tuple[float, float]:
    """Return (filled_shares, filled_notional) from an order payload.

    Notional comes from the execution legs, which carry the prices actually
    paid. When quantity is reported but no execution prices are available, the
    caller is told zero notional for a non-zero quantity — deliberately, so it
    can decide to keep the order fully committed rather than book a fill it
    cannot price.
    """
    filled_shares = float(payload.get("filledQuantity") or 0.0)

    notional = 0.0
    activities = payload.get("orderActivityCollection")
    if isinstance(activities, list):
        for activity in activities:
            if not isinstance(activity, dict):
                continue
            legs = activity.get("executionLegs")
            if not isinstance(legs, list):
                continue
            for leg in legs:
                if isinstance(leg, dict):
                    notional += float(leg.get("quantity") or 0.0) * float(leg.get("price") or 0.0)

    return filled_shares, notional


def extract_replacement(payload: dict) -> tuple[Replacement | None, str | None]:
    """Find the order that replaced this one.

    Returns (replacement, reason):
      (None, None)      no replacement in the payload — nothing to follow
      (None, "reason")  a replacement exists but cannot be trusted
      (obj,  None)      a single successor with complete, consistent economics

    Editing an order at Schwab kills it and creates a new one under a new id, at
    possibly different quantity and price. Carrying the predecessor's numbers
    forward would under-reserve by exactly the difference, so a successor whose
    own economics are missing is treated as unresolvable rather than assumed.
    """
    collection = payload.get("replacingOrderCollection")
    if collection is None:
        return None, None
    if not isinstance(collection, list):
        return None, "replacingOrderCollection is not a list"
    if not collection:
        return None, None
    if len(collection) > 1:
        return None, f"ambiguous: {len(collection)} replacing orders reported"

    successor = collection[0]
    if not isinstance(successor, dict):
        return None, "replacing order entry is not an object"

    successor_id = successor.get("orderId")
    if successor_id in (None, ""):
        return None, "replacing order has no orderId"

    quantity = successor.get("quantity")
    if quantity in (None, "") or float(quantity) <= 0:
        return None, "replacing order reports no quantity"

    symbol = _symbol(successor)
    side = _side(successor)
    if not symbol or not side:
        return None, "replacing order does not identify a symbol and side"

    price = successor.get("price")
    return (
        Replacement(
            successor_id=str(successor_id),
            symbol=symbol,
            side=side,
            quantity=float(quantity),
            limit_price=float(price) if price not in (None, "") else None,
        ),
        None,
    )


def map_broker_status(payload: dict) -> OrderStatusUpdate:
    """Map a broker order payload onto our canonical order status.

    Terminal only for the four statuses in `SWEEP_TERMINAL_BROKER_STATUSES`.
    Everything else -- WORKING, QUEUED, a literal `UNKNOWN`, and any member
    Schwab adds after this was written -- reads as still open, because the
    allowlist defaults the unrecognised case to the direction that keeps
    reserving capital.

    REPLACED is handled by following the pointer the broker gave us. Searching
    for the successor cannot work: it carries a different price and falls
    outside any window anchored on the original submission, so a matcher finds
    nothing, and "nothing found" would read as "the order failed" while the
    replacement is live. When the payload names no successor the order becomes
    `submit_unknown` for a human -- never terminal.
    """
    broker_status = payload.get("status")

    if broker_status in SWEEP_TERMINAL_BROKER_STATUSES:
        return OrderStatusUpdate(
            status=_BROKER_STATUS_TO_ORDER_STATUS[broker_status], terminal=True
        )

    if broker_status in _REPLACEMENT_STATUSES:
        replacement, reason = extract_replacement(payload)
        if replacement is not None:
            return OrderStatusUpdate(
                status=None, terminal=False, successor_id=replacement.successor_id
            )
        return OrderStatusUpdate(
            status="submit_unknown",
            terminal=False,
            reason=reason or f"{broker_status} with no successor order to follow",
        )

    return OrderStatusUpdate(status=None, terminal=False)


def parse_working_orders(payload) -> list[dict]:
    """Extract live broker orders in the shape `risk.preflight` consumes.

    Returns dicts of {broker_order_id, symbol, side, notional}. `notional` is
    the UNFILLED remainder priced at the order's limit: the filled part is
    already reported as position market value, and reserving it twice would
    double-charge the portfolio ceiling.

    `broker_order_id` is stringified because guard 9 de-duplicates local rows
    against this list by that id. An int here against a str in the ledger would
    never match, and the same order would be counted twice.

    Sells reserve nothing -- they do not add buy exposure -- but are still
    returned, because guard 10 must see that a live order exists for the symbol.
    """
    if not isinstance(payload, list):
        # An HTTP error body is a valid dict, and letting it fall through a
        # .get() chain is how "the account holds nothing" gets invented.
        raise ValueError(
            f"Schwab orders response is {type(payload).__name__}, not a list"
        )

    working = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError(f"orders response contains a {type(entry).__name__}, not an order")

        status = str(entry.get("status", "")).upper()
        if status in TERMINAL_BROKER_STATUSES:
            continue

        order_id = entry.get("orderId")
        if order_id in (None, ""):
            raise UnpriceableOrder(
                f"a live order ({status or 'no status'}) has no orderId; "
                "cannot tell it apart from our own"
            )
        order_id = str(order_id)

        symbol = _symbol(entry)
        if not symbol:
            raise UnpriceableOrder(f"live order {order_id} does not name a symbol")

        side = _side(entry)
        remaining = float(entry.get("quantity") or 0.0) - float(entry.get("filledQuantity") or 0.0)
        remaining = max(0.0, remaining)

        if side == "buy" and remaining > 0:
            price = entry.get("price")
            if price in (None, "") or float(price) <= 0:
                raise UnpriceableOrder(
                    f"live buy {order_id} ({symbol}) has no usable limit price "
                    f"({price!r}); refusing to reserve zero for an order that will cost money"
                )
            notional = remaining * float(price)
        else:
            notional = 0.0

        working.append({
            "broker_order_id": order_id,
            "symbol": symbol,
            "side": side,
            "notional": notional,
        })

    return working


def _as_datetime(value) -> datetime | None:
    """Best-effort ISO8601 -> datetime. None when it cannot be read."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_candidate_orders(payload, *, symbol: str, side: str,
                           since, until) -> list[dict]:
    """Broker orders that MIGHT be an ambiguous submission of ours.

    Two rules here are the deliberate inverse of `parse_working_orders`, and
    neither is an oversight:

    **Terminal statuses are INCLUDED.** `parse_working_orders` skips them
    because filled shares already count as position market value. Here a
    `FILLED` order in the window is the single most dangerous candidate: it
    means a real position exists that our ledger never recorded. Filtering it
    out would hide precisely the case an operator needs to see.

    **An unpriceable candidate does not raise.** `parse_working_orders` raises,
    because reserving zero for a live order opens the ceiling. A candidate
    instead carries `limit_price=None`, and `record_candidate_observation`
    prices it at OUR order's `reference_price` -- a defined, conservative
    number. Raising would abort a whole report over one market order.

    Reports only. Nothing here decides that a candidate IS ours: matching
    fields establish shape, not provenance, and Schwab offers no
    client-supplied correlation id. A human owns that judgement.
    """
    if not isinstance(payload, list):
        # An HTTP error body is a structurally valid dict, and letting it fall
        # through a .get() chain is how "no candidates exist" gets invented.
        raise ValueError(
            f"Schwab orders response is {type(payload).__name__}, not a list"
        )

    want_symbol = str(symbol).upper()
    want_side = str(side).lower()
    since_dt, until_dt = _as_datetime(since), _as_datetime(until)

    candidates = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError(f"orders response contains a {type(entry).__name__}, not an order")

        entry_symbol = _symbol(entry)
        if not entry_symbol:
            # Cannot tell whether this is a candidate. Dropping it silently
            # would under-state the worst case the reservation must cover.
            raise ValueError(
                f"order {entry.get('orderId')!r} does not name a symbol; "
                "cannot tell whether it is a candidate"
            )
        if entry_symbol.upper() != want_symbol or _side(entry) != want_side:
            continue

        # An unreadable timestamp is KEPT. Over-counting rejects a legitimate
        # trade, which is recoverable; under-counting opens the ceiling.
        entered = entry.get("enteredTime")
        entered_dt = _as_datetime(entered)
        if entered_dt is not None:
            if since_dt is not None and entered_dt < since_dt:
                continue
            if until_dt is not None and entered_dt > until_dt:
                continue

        order_id = entry.get("orderId")
        if order_id in (None, ""):
            raise ValueError(
                f"a candidate for {entry_symbol} has no orderId; "
                "it could not be adopted even if it is ours"
            )

        price = entry.get("price")
        limit_price = float(price) if price not in (None, "") and float(price) > 0 else None

        candidates.append({
            "broker_order_id": str(order_id),
            "symbol": entry_symbol,
            "side": want_side,
            "quantity": float(entry.get("quantity") or 0.0),
            "limit_price": limit_price,
            "status": str(entry.get("status", "")).upper() or None,
            "entered_at": entered,
        })

    return candidates
