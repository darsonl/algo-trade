"""Capital accounting for orders: what an order costs against the ceilings, now.

Pure functions over an order row dict. No SQLite, no broker, no config — so the
rules that bound real money are testable in isolation.

Two numbers, deliberately different:

    order_commitment(row)          -> against the DAILY NOTIONAL ceiling
    remaining_buy_reservation(row) -> against the PORTFOLIO EXPOSURE ceiling

They differ because the portfolio guard also reads broker positions, whose
market value already includes filled shares. Adding total commitment on top
would charge filled shares twice and could wedge trading at the ceiling.

Commitment is computed PER ROW, never by status-set membership. A 10-share
order that fills 4 and then cancels is 'terminal' yet still holds capital, so
no set of status names can answer the question on its own.
"""
from __future__ import annotations

OPEN_ORDER_STATUSES = ("pending_submit", "submitted", "working", "partially_filled")

# The order may exist at the broker; the submission outcome was ambiguous.
# Never terminal — nothing may sweep it away automatically, only an operator.
UNRESOLVED_ORDER_STATUSES = ("submit_unknown",)

# Broker EXPIRED maps onto 'cancelled': for accounting they behave identically
# (unfilled remainder released, any fill retained). One canonical name, so the
# status contract cannot disagree with itself across documents.
TERMINAL_ORDER_STATUSES = ("filled", "cancelled", "rejected", "submit_failed")

# The broker refused outright, so no capital moved — the only case where a zero
# fill can be trusted without anyone having looked.
DEFINITIVELY_UNFILLED_STATUSES = ("rejected", "submit_failed")

ALL_ORDER_STATUSES = tuple(
    dict.fromkeys(OPEN_ORDER_STATUSES + UNRESOLVED_ORDER_STATUSES + TERMINAL_ORDER_STATUSES)
)

# Anything that can still be holding real capital.
COMMITTING_ORDER_STATUSES = tuple(
    s for s in ALL_ORDER_STATUSES if s not in DEFINITIVELY_UNFILLED_STATUSES
)

# Anything that blocks a second buy of the same symbol.
BLOCKING_ORDER_STATUSES = OPEN_ORDER_STATUSES + UNRESOLVED_ORDER_STATUSES


def _num(value) -> float:
    """NULL columns are zero, but must never crash a ceiling computation."""
    return float(value or 0.0)


def _unit_price(row: dict) -> float:
    """What one unfilled share may still cost.

    The LIMIT price, not the reference quote: the order can execute anywhere up
    to the limit, so the quote understates the maximum it can consume.
    """
    limit = row.get("limit_price")
    return float(limit) if limit is not None else _num(row.get("reference_price"))


def _with_override(row: dict, computed: float) -> float:
    """Take the worst case between what we can compute and what we observed.

    `reserved_notional_override` is the summed commitment of every broker order
    that might be this one, recorded while its submission outcome is ambiguous.
    It is a floor, never a replacement: if the order's own numbers imply more,
    those win.
    """
    override = row.get("reserved_notional_override")
    if override is None:
        return computed
    return max(computed, float(override))


def _remaining_shares(row: dict) -> float:
    remaining = _num(row.get("requested_shares")) - _num(row.get("filled_shares"))
    return max(remaining, 0.0)


def order_commitment(row: dict) -> float:
    """Dollars this order counts against the daily notional ceiling.

    Terminal and observed -> only what actually filled.
    Terminal and UNOBSERVED -> the full requested amount. filled_shares defaults
      to 0, and an unverified 0 is indistinguishable from "filled nothing";
      releasing on it is the fail-open direction, so `fills_observed` gates the
      release rather than the value itself.
    Open or unresolved -> what filled, plus the remainder at the limit price.
    """
    status = row.get("status")
    if status in DEFINITIVELY_UNFILLED_STATUSES:
        return 0.0

    if status in TERMINAL_ORDER_STATUSES:
        if not row.get("fills_observed"):
            return _with_override(row, _num(row.get("requested_shares")) * _unit_price(row))
        return _with_override(row, _num(row.get("filled_notional")))

    return _with_override(
        row, _num(row.get("filled_notional")) + _remaining_shares(row) * _unit_price(row)
    )


def remaining_buy_reservation(row: dict) -> float:
    """Dollars this order adds to portfolio exposure beyond broker positions.

    Filled shares are excluded: the broker already reports them as position
    market value, and counting them here as well double-charges the ceiling.
    Only the part that could still execute is reserved.
    """
    if row.get("side") != "buy":
        return 0.0

    status = row.get("status")
    if status in DEFINITIVELY_UNFILLED_STATUSES:
        return 0.0

    if status in TERMINAL_ORDER_STATUSES and row.get("fills_observed"):
        return 0.0

    return _with_override(row, _remaining_shares(row) * _unit_price(row))
