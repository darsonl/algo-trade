"""Validated bid/ask quotes, and the marketable sell price derived from them.

Nothing here has a default. Every field is mandatory and every failure raises,
because there is no value a missing quote could take that is safe to trade on.
`get_positions` demonstrated the alternative: it parsed an unvalidated response,
so a 401 body became `[]` = "the account holds nothing", and a broker outage
OPENED the size guards. The same shape here is worse — `.get("bidPrice", 0)`
turns an error body into a zero bid, and a sell priced through a zero bid is an
instruction to give the shares away.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TICK = 0.01


class QuoteUnavailable(RuntimeError):
    """No usable quote could be established for the symbol."""


class StaleQuote(QuoteUnavailable):
    """A quote arrived but is too old to price against.

    Subclasses QuoteUnavailable so a caller can catch one type and mean "I have
    no price I trust". A stale quote is more dangerous than a missing one: it
    looks usable.
    """


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    last: float | None
    quote_time: datetime

    def age_seconds(self, now: datetime | None = None) -> float:
        """Seconds since the quote was stamped, never negative.

        A broker clock running ahead of ours would otherwise produce a negative
        age, and any `age < max_age` check would pass forever — clock skew must
        not be able to make a stale quote look fresh.
        """
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.quote_time).total_seconds())


def _require_positive_number(value, field: str, symbol: str) -> float:
    # bool is an int subclass; True would otherwise sail through as 1.0.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QuoteUnavailable(
            f"{symbol}: {field} is {value!r}, not a number — refusing to price off it"
        )
    if value <= 0 or math.isnan(value) or math.isinf(value):
        raise QuoteUnavailable(f"{symbol}: {field} is {value!r}, which is not a tradable price")
    return float(value)


def parse_quote(symbol: str, payload: dict) -> Quote:
    """Build a Quote from a Schwab get_quote body, or raise.

    Rejects: a payload without the symbol (which is what an error body looks
    like), a missing quote block, a missing/zero/negative/non-numeric bid or
    ask, a crossed market, and a missing timestamp.
    """
    if not isinstance(payload, dict):
        raise QuoteUnavailable(f"{symbol}: quote payload is {type(payload).__name__}, not a dict")

    entry = payload.get(symbol)
    if not isinstance(entry, dict):
        raise QuoteUnavailable(
            f"{symbol}: no entry in quote response; got keys {sorted(payload)!r}"
        )

    quote = entry.get("quote")
    if not isinstance(quote, dict):
        raise QuoteUnavailable(f"{symbol}: response has no 'quote' block")

    if "bidPrice" not in quote:
        raise QuoteUnavailable(f"{symbol}: quote has no bidPrice")
    if "askPrice" not in quote:
        raise QuoteUnavailable(f"{symbol}: quote has no askPrice")

    bid = _require_positive_number(quote["bidPrice"], "bidPrice", symbol)
    ask = _require_positive_number(quote["askPrice"], "askPrice", symbol)

    if ask < bid:
        # A crossed book is not a market to price against; it usually means
        # stale or partial data rather than a real arbitrage.
        raise QuoteUnavailable(f"{symbol}: crossed market, bid {bid} > ask {ask}")

    raw_time = quote.get("quoteTime")
    if not isinstance(raw_time, (int, float)) or isinstance(raw_time, bool):
        raise QuoteUnavailable(f"{symbol}: quoteTime is {raw_time!r}, not an epoch timestamp")

    last = quote.get("lastPrice")
    last = float(last) if isinstance(last, (int, float)) and not isinstance(last, bool) else None

    return Quote(
        symbol=symbol,
        bid=bid,
        ask=ask,
        last=last,
        quote_time=datetime.fromtimestamp(raw_time / 1000, tz=timezone.utc),
    )


def marketable_sell_limit(bid: float, buffer_pct: float, tick: float = TICK) -> float:
    """Price a sell THROUGH the bid by buffer_pct, rounded DOWN to the tick.

    Through the bid, not at `quote - buffer`: this is meant to behave like a
    market order for fill purposes while still carrying a worst-case price the
    guards validated. The bound is on how bad the fill may be, not on whether
    it happens — a missed sell holds the position through the decline that
    triggered the exit, which is the failure this instrument exists to avoid.

    Rounding is DOWN because for a sell a lower limit is strictly more
    marketable; rounding up could turn a marketable order into a resting one.

    Buys keep the passive `quote * (1 + buffer)` limit. The asymmetry is
    deliberate: a missed buy costs an opportunity, a missed sell costs the move.
    """
    if not isinstance(bid, (int, float)) or isinstance(bid, bool):
        raise ValueError(f"bid must be a number, got {bid!r}")
    if bid <= 0:
        raise ValueError(f"bid must be positive, got {bid!r}")
    if not 0 <= buffer_pct < 100:
        raise ValueError(f"buffer_pct must be in [0, 100), got {buffer_pct!r}")

    raw = bid * (1 - buffer_pct / 100)
    # Round through cents rather than on the float directly: 99.499005 must
    # floor to 99.49, and floating point makes /tick*tick unreliable at scale.
    price = math.floor(round(raw / tick, 6)) * tick
    price = round(price, 2)

    if price <= 0:
        # A tiny bid with a large buffer can floor to zero. One tick is the
        # smallest thing that is still an order rather than a giveaway.
        price = round(tick, 2)
    return price


def fetch_quote(symbol: str, config, client=None, now: datetime | None = None) -> Quote:
    """Fetch, validate, and freshness-check a quote. Raises QuoteUnavailable.

    Transport is validated BEFORE the body is parsed, so an HTTP error body
    cannot reach the parser and masquerade as data.
    """
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)

    from schwab.client import Client as SchwabClient

    resp = client.get_quote(symbol, fields=SchwabClient.Quote.Fields.QUOTE)
    resp.raise_for_status()
    quote = parse_quote(symbol, resp.json())

    max_age = getattr(config, "quote_max_age_s", 30)
    age = quote.age_seconds(now)
    if age > max_age:
        raise StaleQuote(
            f"{symbol}: quote is {age:.0f}s old (limit {max_age}s) — refusing to price off it"
        )
    return quote
