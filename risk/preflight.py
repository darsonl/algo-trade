"""The approval preflight guard table (spec v4 §8).

Twelve guards evaluated in a fixed order, as one pure function. Nothing here
touches the network, the database, Discord, or the clock: every input is passed
in, so the whole table is testable without a single mock.

**`None` and `[]` are different inputs throughout.** `None` means "the read
failed"; `[]` means "the read succeeded and there is nothing there". Collapsing
the two is the defect that has recurred most often in this project -- a 401 body
flowed through `.get()` chains, became `[]` = "the account holds nothing", and a
broker outage OPENED the size guards. Guard 5 exists to make that impossible,
which is why it must precede every guard that consumes broker data.

Two deliberate departures from the spec text, both to keep this function pure:

  * The spec has `evaluate_trade` call `kill_switch.is_enabled()` itself. It
    takes `trading_enabled` instead. A pure function that reads global mutable
    state is not pure, and the AUTHORITATIVE read already happens where it has
    to -- inside `submission_gate()`, spanning the final read through broker
    dispatch. Guard 2 is the early, friendly rejection, not the safety control.
  * Broker reads arrive as one `BrokerSnapshot` rather than two parameters, so
    "the read failed" has exactly one representation to check.

Import direction: this module imports nothing from `schwab_client` or
`discord`. `schwab_client.quotes` imports `Quote` from here, never the reverse.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from database.order_accounting import (
    BLOCKING_ORDER_STATUSES,
    UNRESOLVED_ORDER_STATUSES,
    remaining_buy_reservation,
)

MARKET_TZ = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)


@dataclass(frozen=True)
class Quote:
    """A validated two-sided quote. Built by `schwab_client.quotes.parse_quote`."""

    symbol: str
    bid: float
    ask: float
    last: float | None
    quote_time: datetime

    def age_seconds(self, now: datetime | None = None) -> float:
        """Seconds since the quote was stamped, never negative.

        A broker clock running ahead of ours would otherwise produce a negative
        age, and any `age < max_age` check would pass forever -- clock skew must
        not be able to make a stale quote look fresh.
        """
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.quote_time).total_seconds())


@dataclass(frozen=True)
class TradeRequest:
    side: str
    ticker: str
    scan_price: float
    rec_id: int | None = None
    shares: float | None = None
    expires_at: datetime | None = None
    user_id: int | None = None
    guild_id: int | None = None
    channel_id: int | None = None


@dataclass(frozen=True)
class BrokerSnapshot:
    """What the broker said. `None` on either field means the read FAILED."""

    positions: list[dict] | None = None
    working_orders: list[dict] | None = None

    @property
    def readable(self) -> bool:
        return self.positions is not None and self.working_orders is not None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason_code: str | None = None
    message: str = ""
    limit_price: float | None = None
    shares: float | None = None


def _block(reason_code: str, message: str) -> Decision:
    return Decision(allowed=False, reason_code=reason_code, message=message)


def _parse_allowlist(raw) -> set[int]:
    """Parse a comma-separated id list. Malformed entries are DROPPED, not
    guessed at -- an unparseable id must never widen the allowlist."""
    ids = set()
    for chunk in str(raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError:
            continue
    return ids


def check_authorization(request: TradeRequest, config) -> Decision | None:
    """Guard 1. Returns None when authorized, a Decision when not.

    Called by `evaluate_trade` first, and by the button directly before it
    defers, so an unauthorized click is answered without any broker work.

    An empty allowlist denies everyone. It never means "allow all": the failure
    mode of the opposite convention is a bot that anyone in the channel can
    spend money with, reached by deleting one line of config.
    """
    allowed = _parse_allowlist(getattr(config, "allowed_discord_user_ids", ""))
    denial = _block(
        "unauthorized", "You are not authorized to approve trades."
    )

    if not allowed or request.user_id not in allowed:
        return denial

    expected_guild = getattr(config, "discord_guild_id", 0) or 0
    if expected_guild and request.guild_id != expected_guild:
        return denial

    expected_channel = getattr(config, "discord_channel_id", 0) or 0
    if expected_channel and request.channel_id != expected_channel:
        return denial

    return None


def _in_regular_hours(now: datetime) -> bool:
    et = now.astimezone(MARKET_TZ)
    return REGULAR_OPEN <= et.time() < REGULAR_CLOSE


def buy_limit_price(ask: float, buffer_pct: float) -> float:
    """A passive buy limit: through the ask by buffer_pct, rounded UP to the cent.

    Rounds up because for a buy a higher limit is more marketable, mirroring
    `marketable_sell_limit`'s rounding down. Buys stay passive on purpose -- a
    missed buy costs an opportunity, a missed sell costs the move.
    """
    return round(math.ceil(round(ask * (1 + buffer_pct / 100), 6) * 100) / 100, 2)


def _exposure(broker: BrokerSnapshot, local_orders) -> float:
    """Broker market value + open reservations, merged by broker order id.

    A buy placed by hand in the Schwab app is working and unfilled: it appears
    in no position and in no local row, so counting only our own ledger
    under-states exposure -- the open direction. A local row that HAS been
    attached to a broker id is the same order the broker is reporting, so it is
    counted once; double-counting would reject legitimate trades, which is the
    recoverable direction but still wrong.
    """
    total = sum(float(p.get("market_value") or 0.0) for p in broker.positions)

    seen_broker_ids = set()
    for order in broker.working_orders:
        broker_id = order.get("broker_order_id")
        if broker_id is not None:
            seen_broker_ids.add(str(broker_id))
        total += float(order.get("notional") or 0.0)

    for row in local_orders:
        broker_id = row.get("broker_order_id")
        if broker_id is not None and str(broker_id) in seen_broker_ids:
            continue  # Already counted from the broker's own view of it.
        total += remaining_buy_reservation(row)

    return total


def evaluate_trade(
    request: TradeRequest,
    *,
    quote: Quote | None,
    broker: BrokerSnapshot,
    local_orders,
    day_notional: float,
    trading_enabled: bool,
    config,
    now: datetime | None = None,
) -> Decision:
    """Run the twelve guards in order and return the first rejection.

    Order is load-bearing, not cosmetic. Guard 1 first so a rejection message
    cannot be used as a side channel into the book; guard 5 before 8-11 so a
    failed broker read can never be evaluated as an empty one.
    """
    now = now or datetime.now(timezone.utc)
    is_buy = request.side == "buy"
    local_orders = list(local_orders or [])
    ticker = request.ticker

    # 1 -- unauthorized
    denial = check_authorization(request, config)
    if denial is not None:
        return denial

    # 2 -- trading_disabled
    if not trading_enabled:
        return _block("trading_disabled", "Trading is halted. Use /resume to re-enable.")

    # 3 -- expired
    if request.expires_at is not None and now >= request.expires_at:
        return _block("expired", f"This {ticker} recommendation has expired.")

    # 4 -- quote_unavailable
    if quote is None:
        return _block("quote_unavailable", f"No usable quote for {ticker}.")
    max_age = getattr(config, "quote_max_age_s", 30)
    if _in_regular_hours(now) and quote.age_seconds(now) > max_age:
        # Outside regular hours the last close is the only quote there is, and
        # rejecting it would block every pre-open approval -- which is when this
        # system is designed to be used. The limit price is the binding control
        # after hours, not freshness.
        return _block(
            "quote_unavailable",
            f"{ticker} quote is {quote.age_seconds(now):.0f}s old (limit {max_age}s).",
        )

    # 5 -- broker_unavailable. BEFORE anything that reads broker data.
    if not broker.readable:
        return _block(
            "broker_unavailable",
            "Could not read the account from Schwab; refusing to trade blind.",
        )

    # 11 -- unresolved_order. Applies to buys AND sells: selling into an
    # unknown order state can oversell. Checked before the buy-only guards so a
    # sell reaches it too.
    for row in local_orders:
        if row.get("ticker") == ticker and row.get("status") in UNRESOLVED_ORDER_STATUSES:
            return _block(
                "unresolved_order",
                f"A {ticker} order is in an unknown state. Verify it in Schwab, "
                "then use /resolve before trading this symbol again.",
            )

    if not is_buy:
        # 12 -- sell_quantity, revalidated against the broker. The view captured
        # its share count at post time and the position can shrink before the
        # click.
        held = sum(
            float(p.get("quantity") or 0.0)
            for p in broker.positions
            if p.get("symbol") == ticker
        )
        requested = float(request.shares or 0.0)
        if requested <= 0 or requested > held:
            return _block(
                "sell_quantity",
                f"Cannot sell {requested:g} {ticker}: the account holds {held:g}.",
            )
        return Decision(allowed=True, shares=requested)

    # --- buy-only guards ---

    # 6 -- price_drift
    tolerance = getattr(config, "approval_price_tolerance_pct", 2.0)
    if request.scan_price > 0:
        drift = abs(quote.ask - request.scan_price) / request.scan_price * 100
        if drift > tolerance:
            return _block(
                "price_drift",
                f"{ticker} moved {drift:.1f}% since the scan "
                f"(${request.scan_price:.2f} -> ${quote.ask:.2f}, limit {tolerance:.1f}%).",
            )

    # Everything below prices at the LIMIT, never the scan price and never the
    # raw quote, so each ceiling is computed against the most the order can cost.
    limit_price = buy_limit_price(quote.ask, getattr(config, "approval_slippage_buffer_pct", 0.5))

    # 7 -- size_zero
    shares = int(getattr(config, "max_position_size_usd", 0.0) // limit_price)
    if shares < 1:
        return _block(
            "size_zero",
            f"${getattr(config, 'max_position_size_usd', 0.0):.0f} buys no whole "
            f"shares of {ticker} at ${limit_price:.2f}.",
        )
    order_notional = shares * limit_price

    # 8 -- daily_notional
    daily_ceiling = getattr(config, "max_daily_notional_usd", 0.0)
    if day_notional + order_notional > daily_ceiling:
        return _block(
            "daily_notional",
            f"${order_notional:.0f} for {ticker} would take today's committed buys to "
            f"${day_notional + order_notional:.0f}, over the ${daily_ceiling:.0f} ceiling.",
        )

    # 9 -- portfolio_exposure
    exposure = _exposure(broker, local_orders)
    portfolio_ceiling = getattr(config, "max_portfolio_usd", 0.0)
    if exposure + order_notional > portfolio_ceiling:
        return _block(
            "portfolio_exposure",
            f"${order_notional:.0f} for {ticker} would take exposure to "
            f"${exposure + order_notional:.0f}, over the ${portfolio_ceiling:.0f} ceiling.",
        )

    # 10 -- duplicate_symbol
    holds = any(
        p.get("symbol") == ticker and float(p.get("quantity") or 0.0) > 0
        for p in broker.positions
    )
    working = any(o.get("symbol") == ticker for o in broker.working_orders)
    local_working = any(
        row.get("ticker") == ticker and row.get("status") in BLOCKING_ORDER_STATUSES
        for row in local_orders
    )
    if holds or working or local_working:
        return _block(
            "duplicate_symbol",
            f"You already hold {ticker} or have a working order for it.",
        )

    return Decision(allowed=True, limit_price=limit_price, shares=shares)
