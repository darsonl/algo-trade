from __future__ import annotations
import logging
from dataclasses import dataclass

from schwab.client import Client as SchwabClient
from schwab.orders.equities import equity_buy_limit, equity_sell_limit
from schwab.orders.common import Duration

from risk import kill_switch
from schwab_client.order_payload import (
    _as_datetime,
    parse_candidate_orders,
    parse_working_orders,
)

logger = logging.getLogger(__name__)

def _checked(resp):
    """Validate the transport before anything parses the payload.

    A JSON error body is structurally a valid dict, so parsing first and
    inspecting later lets a 401/429/500 masquerade as data. Every broker READ
    goes through here.
    """
    resp.raise_for_status()
    return resp.json()


def build_limit_buy(ticker: str, shares: int, limit_price_str: str) -> dict:
    """Return the JSON spec for a GTC limit buy order (no network call).

    CRITICAL: .set_duration(Duration.GOOD_TILL_CANCEL) is required.
    Without it, equity_buy_limit defaults to DAY — late approvals silently expire.
    """
    spec = equity_buy_limit(ticker, shares, limit_price_str)
    spec.set_duration(Duration.GOOD_TILL_CANCEL)
    return spec.build()


def build_marketable_sell(ticker: str, shares: int, limit_price_str: str) -> dict:
    """Return the JSON spec for a DAY marketable-limit sell (no network call).

    DAY, deliberately, where buys use GTC. A marketable limit that has not
    filled by the close has already missed the move it was reacting to; leaving
    it resting for weeks would sell into an unrelated future market. A late buy
    is still a buy at a price you set, which is why the buy side differs.
    """
    spec = equity_sell_limit(ticker, shares, limit_price_str)
    spec.set_duration(Duration.DAY)
    return spec.build()


def parse_positions(account_response: dict) -> list[dict]:
    """
    Extract a clean list of positions from a Schwab get_account response dict.

    Returns dicts with keys: symbol, quantity, avg_price, market_value, asset_type.
    asset_type is the Schwab instrument assetType (e.g. 'EQUITY', 'CASH_EQUIVALENT'),
    or '' when absent — reconciliation uses it to ignore cash/sweep instruments.
    """
    account = account_response.get("securitiesAccount")
    if not isinstance(account, dict):
        # An HTTP error body is a valid dict too. Without this check its missing
        # keys flow through the .get() chain below and come out as [] — "the
        # account holds nothing" — which makes a broker outage look like a clean
        # empty account and OPENS the exposure and holdings guards.
        raise ValueError(
            "Schwab account response has no 'securitiesAccount' object; "
            f"got keys {sorted(account_response)!r}"
        )

    raw_positions = account.get("positions", [])
    result = []
    for pos in raw_positions:
        instrument = pos.get("instrument", {})
        symbol = instrument.get("symbol")
        if not symbol:
            continue  # Skip non-equity / cash positions
        result.append({
            "symbol": symbol,
            "quantity": pos.get("longQuantity", 0.0),
            "avg_price": pos.get("averagePrice", 0.0),
            "market_value": pos.get("marketValue", 0.0),
            "asset_type": instrument.get("assetType", ""),
        })
    return result


def _call_place_order(client, config, spec) -> object:
    """The single choke point every order dispatch passes through.

    The kill switch is checked HERE rather than only in the approval path, so a
    call site that forgets the gate still fails closed. Round-4 C1 was this
    defect in an earlier design: the sink was documented as re-reading the
    switch while no term for it existed in the predicate, and /halt during a
    pending approval did nothing.

    There is no retry here to sit outside of any more: `_dispatch` submits
    exactly once (§3). A halt was never a transient fault worth re-attempting,
    and neither is a submission whose outcome we cannot see.
    """
    kill_switch.require_enabled(config)
    return _dispatch(client, config.schwab_account_hash, spec)


def _dispatch(client, account_hash: str, spec) -> object:
    """Submit once. NEVER retried, and never decorated with `@_retry`.

    A timeout after Schwab accepts an order is an UNKNOWN outcome, not a
    failure. The Schwab order API has no idempotency key, so a retry is a
    second chance to buy the same stock — and the duplicate is a real position
    nobody approved. Ambiguity is resolved by `classify_submission` and, when
    it stays ambiguous, by a human through /resolve.

    Reads may be retried; submission may not. That asymmetry is the whole point.
    """
    return client.place_order(account_hash, spec)


@dataclass(frozen=True)
class SubmissionOutcome:
    status: str
    broker_order_id: str | None
    message: str

    @property
    def reserves_capital(self) -> bool:
        """Only a definitive refusal releases the ceiling it claimed.

        An order we cannot account for may exist and may fill. Releasing its
        capital lets the next approval spend the same dollars twice.
        """
        return self.status != "submit_failed"


# 4xx statuses that say nothing about whether the order landed. 408 is a
# timeout and 429 is a rate limit; both can arrive after Schwab has already
# accepted the order, so neither is a definitive refusal.
_AMBIGUOUS_4XX = frozenset({408, 429})


def _status_code(error) -> int | None:
    response = getattr(error, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def classify_submission(response=None, error=None) -> SubmissionOutcome:
    """Decide what a submission attempt actually did (spec v4 §3).

    v2 mapped every submission exception to `submit_unknown`. That is
    over-broad: a definitive HTTP rejection is not ambiguous, and treating it
    as unknown reserves capital that was never committed. The split is:

      2xx + Location      accepted and identifiable   -> submitted
      2xx, no Location    accepted, unidentifiable    -> submit_unknown
      4xx not 408/429     definitively refused        -> submit_failed
      408, 429, any 5xx   may or may not have landed  -> submit_unknown
      timeout / transport may or may not have landed  -> submit_unknown
      anything unrecognised                           -> submit_unknown

    The default is `submit_unknown` on purpose. An outcome this function cannot
    classify might still have placed an order.
    """
    if response is None and error is None:
        raise ValueError("classify_submission needs either a response or an error")

    if response is not None:
        location = (getattr(response, "headers", {}) or {}).get("Location", "")
        broker_order_id = location.rstrip("/").split("/")[-1] if location else ""
        if broker_order_id:
            return SubmissionOutcome(
                status="submitted",
                broker_order_id=broker_order_id,
                message=f"Order {broker_order_id} accepted.",
            )
        return SubmissionOutcome(
            status="submit_unknown",
            broker_order_id=None,
            message=_UNKNOWN_MESSAGE.format(
                detail="the broker accepted the order but did not return an id"
            ),
        )

    code = _status_code(error)
    if code is not None and 400 <= code < 500 and code not in _AMBIGUOUS_4XX:
        return SubmissionOutcome(
            status="submit_failed",
            broker_order_id=None,
            message=f"The broker refused the order (HTTP {code}). Nothing was placed.",
        )

    detail = f"HTTP {code}" if code is not None else f"{type(error).__name__}: {error}"
    return SubmissionOutcome(
        status="submit_unknown",
        broker_order_id=None,
        message=_UNKNOWN_MESSAGE.format(detail=detail),
    )


_UNKNOWN_MESSAGE = (
    "The broker call failed after submission ({detail}). The order may or may "
    "not exist at Schwab. Its capital stays reserved against your ceilings and "
    "the symbol is blocked for new buys until this is settled — run /resolve, "
    "or check Schwab directly."
)


def get_positions(config, client=None) -> list[dict]:
    """Return current account positions as a list of dicts."""
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)

    resp = client.get_account(
        config.schwab_account_hash,
        fields=[SchwabClient.Account.Fields.POSITIONS],
    )
    return parse_positions(_checked(resp))


def get_working_orders(config, client=None) -> list[dict]:
    """Return the broker's LIVE orders, in the shape `risk.preflight` consumes.

    Preflight needs this because our own ledger is not the whole truth: a buy
    placed by hand in the Schwab app is working and unfilled, so it appears in
    no position and in no local row. Guards 9 and 10 would both pass and the bot
    would add a second order for a symbol that already has one live
    (round-4 finding 9).

    Raises rather than returning a partial list. Every failure here -- transport,
    shape, or an order that cannot be priced -- must reach the caller so it can
    pass `working_orders=None` and let guard 5 refuse. Returning `[]` on failure
    would tell the guards the book is empty, which is the one answer that opens
    every ceiling at once.
    """
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)

    resp = client.get_orders_for_account(config.schwab_account_hash)
    return parse_working_orders(_checked(resp))


def find_recent_orders(config, *, symbol: str, side: str, since, until,
                       client=None) -> list[dict]:
    """Broker orders that might be an ambiguous submission of ours.

    Feeds `/resolve`'s report. Unlike `get_working_orders` this deliberately
    includes TERMINAL orders: a candidate that already FILLED is the most
    important one to show, because it means a real position exists that our
    ledger never recorded.

    Raises rather than returning a partial or empty list. `[]` from a failed
    read would tell an operator that no candidate exists -- the one answer that
    makes a `confirmed_absent` resolution look justified when it is not, and
    that resolution releases capital.

    Not retried and not wrapped: this is a READ, so the no-retry rule that
    governs submission does not apply, but it matches `get_working_orders` in
    letting every failure reach the caller.
    """
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)

    resp = client.get_orders_for_account(
        config.schwab_account_hash,
        from_entered_datetime=_as_datetime(since),
        to_entered_datetime=_as_datetime(until),
    )
    return parse_candidate_orders(
        _checked(resp), symbol=symbol, side=side, since=since, until=until
    )


def collect_broker_snapshot(config, client=None):
    """Gather everything the guard table needs from the broker, failing CLOSED.

    Each read is captured independently and a failure becomes `None`, never
    `[]`. That distinction is the whole point: `[]` tells guard 9 the account is
    empty and every ceiling is wide open, while `None` tells guard 5 the read
    failed and the approval is refused. A 401 parsed as "the account holds
    nothing" is the defect this shape exists to prevent.

    Never raises. It runs on the approval path, where the guards decide what
    happens next -- an exception here would bypass the very table that is
    supposed to adjudicate a broker outage.
    """
    from risk.preflight import BrokerSnapshot

    if client is None:
        from schwab_client.auth import get_client
        try:
            client = get_client(config)
        except Exception:
            logger.exception("broker snapshot: could not build a Schwab client")
            return BrokerSnapshot(positions=None, working_orders=None)

    try:
        positions = get_positions(config, client=client)
    except Exception as exc:
        logger.warning("broker snapshot: position read failed (%s)", exc)
        positions = None

    try:
        working_orders = get_working_orders(config, client=client)
    except Exception as exc:
        # Includes UnpriceableOrder: one order we cannot price means we cannot
        # state exposure at all, so the whole read is unusable rather than
        # partially trusted.
        logger.warning("broker snapshot: working-order read failed (%s)", exc)
        working_orders = None

    return BrokerSnapshot(positions=positions, working_orders=working_orders)
