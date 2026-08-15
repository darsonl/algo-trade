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
