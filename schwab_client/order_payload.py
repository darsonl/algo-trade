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
