"""Validated bid/ask quotes and marketable sell pricing (round-5 #9).

Spec §6 says sells should be priced *through the bid*, but nothing in the
codebase had a bid: there was no quote fetch at all. This adds one, and the
parsing is deliberately total — every field is mandatory and every failure
raises.

That severity is not paranoia. `get_positions` once parsed an unvalidated
response and turned a 401 body into `[]` = "the account holds nothing", which
OPENED the size guards. A quote parser has the same shape and a worse
consequence: `.get("bidPrice", 0)` on an error body yields a bid of zero, and
a sell priced through a zero bid is an instruction to give the shares away.
There is no safe default, so there are no defaults.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from schwab_client.quotes import (
    Quote,
    QuoteUnavailable,
    StaleQuote,
    fetch_quote,
    marketable_sell_limit,
    parse_quote,
)

NOW = datetime(2026, 8, 16, 15, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)


def _payload(bid=170.10, ask=170.20, last=170.15, quote_time=NOW_MS, symbol="AAPL"):
    quote = {}
    if bid is not None:
        quote["bidPrice"] = bid
    if ask is not None:
        quote["askPrice"] = ask
    if last is not None:
        quote["lastPrice"] = last
    if quote_time is not None:
        quote["quoteTime"] = quote_time
    return {symbol: {"symbol": symbol, "quote": quote}}


# ─── Parsing the good case ───────────────────────────────────────────────────


def test_parses_bid_ask_and_time():
    q = parse_quote("AAPL", _payload())

    assert q.symbol == "AAPL"
    assert q.bid == 170.10
    assert q.ask == 170.20
    assert q.quote_time == NOW


def test_parse_returns_a_quote_instance():
    assert isinstance(parse_quote("AAPL", _payload()), Quote)


# ─── Parsing refuses everything ambiguous ────────────────────────────────────


def test_missing_symbol_key_raises():
    """An error body is a valid dict. It must not read as a quote."""
    with pytest.raises(QuoteUnavailable):
        parse_quote("AAPL", {"errors": [{"message": "invalid token"}]})


def test_empty_payload_raises():
    with pytest.raises(QuoteUnavailable):
        parse_quote("AAPL", {})


def test_missing_quote_block_raises():
    with pytest.raises(QuoteUnavailable):
        parse_quote("AAPL", {"AAPL": {"symbol": "AAPL"}})


@pytest.mark.parametrize("field", ["bid", "ask", "quote_time"])
def test_missing_required_field_raises(field):
    with pytest.raises(QuoteUnavailable):
        parse_quote("AAPL", _payload(**{field: None}))


@pytest.mark.parametrize("bad", [0, -1.0, "170.10", None])
def test_non_positive_or_non_numeric_bid_raises(bad):
    """A zero bid is the dangerous one: it prices a sell at give-it-away."""
    with pytest.raises(QuoteUnavailable):
        parse_quote("AAPL", _payload(bid=bad))


@pytest.mark.parametrize("bad", [0, -5.0, "abc"])
def test_non_positive_or_non_numeric_ask_raises(bad):
    with pytest.raises(QuoteUnavailable):
        parse_quote("AAPL", _payload(ask=bad))


def test_crossed_market_raises():
    """ask < bid is not a tradable market; refuse rather than price off it."""
    with pytest.raises(QuoteUnavailable):
        parse_quote("AAPL", _payload(bid=170.20, ask=170.10))


def test_a_wide_but_valid_spread_is_accepted():
    q = parse_quote("AAPL", _payload(bid=100.0, ask=105.0))
    assert q.bid == 100.0


def test_last_price_is_optional():
    """Only bid/ask/time drive the decision, so last must not be mandatory."""
    q = parse_quote("AAPL", _payload(last=None))
    assert q.last is None


# ─── Freshness ───────────────────────────────────────────────────────────────


def test_fresh_quote_is_accepted():
    q = parse_quote("AAPL", _payload(quote_time=NOW_MS))
    assert q.age_seconds(now=NOW) == pytest.approx(0, abs=1)


def test_age_is_measured_from_quote_time():
    old = int((NOW - timedelta(seconds=30)).timestamp() * 1000)
    q = parse_quote("AAPL", _payload(quote_time=old))
    assert q.age_seconds(now=NOW) == pytest.approx(30, abs=1)


def test_a_future_quote_time_is_not_negative_age():
    """Clock skew must not make a stale quote look arbitrarily fresh."""
    future = int((NOW + timedelta(seconds=120)).timestamp() * 1000)
    q = parse_quote("AAPL", _payload(quote_time=future))
    assert q.age_seconds(now=NOW) >= 0


# ─── Marketable sell pricing ─────────────────────────────────────────────────


def test_sell_limit_is_priced_below_the_bid():
    """THROUGH the bid, not at bid - buffer of the quote.

    The bound is on how bad the fill may be, not on whether it happens: a
    missed sell holds the position through the decline that triggered the exit.
    """
    assert marketable_sell_limit(100.0, buffer_pct=0.5) == 99.50


def test_sell_limit_rounds_down_to_the_tick():
    """Down, never up: a lower limit is strictly more marketable for a sell."""
    # 99.999 * 0.995 = 99.499005 -> 99.49, not 99.50
    assert marketable_sell_limit(99.999, buffer_pct=0.5) == 99.49


def test_sell_limit_with_zero_buffer_is_the_bid_itself():
    assert marketable_sell_limit(100.0, buffer_pct=0.0) == 100.00


def test_sell_limit_is_rounded_to_cents():
    price = marketable_sell_limit(37.77, buffer_pct=0.37)
    assert price == round(price, 2)


@pytest.mark.parametrize("bad_bid", [0, -1.0])
def test_sell_limit_refuses_a_non_positive_bid(bad_bid):
    with pytest.raises(ValueError):
        marketable_sell_limit(bad_bid, buffer_pct=0.5)


def test_sell_limit_refuses_a_buffer_that_would_zero_the_price():
    """A 100% buffer prices the sell at zero. Refuse rather than emit it."""
    with pytest.raises(ValueError):
        marketable_sell_limit(100.0, buffer_pct=100.0)


def test_sell_limit_never_returns_zero_or_negative():
    assert marketable_sell_limit(0.02, buffer_pct=99.0) > 0


# ─── fetch_quote ─────────────────────────────────────────────────────────────


def _config(max_age=30):
    c = MagicMock()
    c.quote_max_age_s = max_age
    return c


def _response(payload, status_ok=True):
    resp = MagicMock()
    resp.json.return_value = payload
    if not status_ok:
        resp.raise_for_status.side_effect = RuntimeError("401 Unauthorized")
    return resp


def test_fetch_validates_transport_before_parsing():
    """raise_for_status first — a JSON error body must never reach the parser.

    The payload here is deliberately VALID, so parsing alone cannot fail. If
    raise_for_status were dropped, fetch_quote would return a Quote and this
    test would fail — which is the production change it is meant to catch. A
    malformed payload would have made the test pass for either reason.
    """
    client = MagicMock()
    client.get_quote.return_value = _response(_payload(), status_ok=False)

    with pytest.raises(RuntimeError, match="401"):
        fetch_quote("AAPL", _config(), client=client, now=NOW)


def test_fetch_returns_a_parsed_quote():
    client = MagicMock()
    client.get_quote.return_value = _response(_payload())

    q = fetch_quote("AAPL", _config(), client=client, now=NOW)

    assert q.bid == 170.10


def test_fetch_rejects_a_stale_quote():
    """A quote old enough to be wrong is worse than no quote: it looks usable."""
    old = int((NOW - timedelta(seconds=120)).timestamp() * 1000)
    client = MagicMock()
    client.get_quote.return_value = _response(_payload(quote_time=old))

    with pytest.raises(StaleQuote):
        fetch_quote("AAPL", _config(max_age=30), client=client, now=NOW)


def test_stale_quote_is_a_quote_unavailable():
    """Callers should be able to catch one type for 'no usable quote'."""
    assert issubclass(StaleQuote, QuoteUnavailable)
