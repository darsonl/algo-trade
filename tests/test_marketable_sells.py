"""Sells become marketable limits priced through the bid (round-5 #9, slice 2).

Sells were market orders. Spec §6 replaces them with a limit priced THROUGH the
bid, so the order still behaves like a market order for fill purposes while
carrying a worst-case price the guards validated.

The instrument is matched to the trigger that actually exists (RSI + MACD, a
momentum exit): a missed sell holds the position through the decline that fired
the signal. Buys keep the passive `quote * (1 + buffer)` limit — that asymmetry
is deliberate and documented so nobody "fixes" it into symmetry.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Config
from database.models import initialize_db
from risk import kill_switch
from schwab_client.orders import build_marketable_sell, place_marketable_sell_order
from schwab_client.quotes import QuoteUnavailable, StaleQuote

NOW = datetime(2026, 8, 16, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    initialize_db(path)
    kill_switch.init(path, env_default=True)
    return path


def _config(db_path, buffer=0.5):
    c = Config()
    c.db_path = db_path
    c.dry_run = False
    c.schwab_account_hash = "hash"
    c.approval_slippage_buffer_pct = buffer
    c.quote_max_age_s = 30
    return c


def _quote_response(bid=100.0, ask=100.10, age_s=0, symbol="AAPL"):
    ts = int((NOW - timedelta(seconds=age_s)).timestamp() * 1000)
    resp = MagicMock()
    resp.json.return_value = {
        symbol: {"symbol": symbol,
                 "quote": {"bidPrice": bid, "askPrice": ask,
                           "lastPrice": bid, "quoteTime": ts}}
    }
    return resp


def _client(quote_resp=None):
    c = MagicMock()
    c.get_quote.return_value = quote_resp or _quote_response()
    c.place_order.return_value = MagicMock(
        headers={"Location": "https://api/orders/55555"}
    )
    return c


# ─── Order construction ──────────────────────────────────────────────────────


def test_marketable_sell_is_a_limit_order():
    spec = build_marketable_sell("AAPL", 10, "99.50")

    leg = spec["orderLegCollection"][0]
    assert spec["orderType"] == "LIMIT"
    assert leg["instruction"] == "SELL"
    assert leg["quantity"] == 10


def test_marketable_sell_carries_the_limit_price():
    spec = build_marketable_sell("AAPL", 10, "99.50")
    assert spec["price"] == "99.50"


def test_marketable_sell_is_a_day_order():
    """DAY, not GTC: a marketable limit that fails to fill today has missed the
    move it was reacting to. Leaving it resting for weeks would sell into an
    unrelated future market."""
    spec = build_marketable_sell("AAPL", 10, "99.50")
    assert spec["duration"] == "DAY"


# ─── Pricing through the bid ─────────────────────────────────────────────────


def test_sell_is_priced_through_the_bid(db_path):
    client = _client(_quote_response(bid=100.0))

    place_marketable_sell_order("AAPL", 10, _config(db_path, buffer=0.5),
                                client=client, now=NOW)

    spec = client.place_order.call_args.args[1]
    assert spec["price"] == "99.50"


def test_price_uses_the_bid_not_the_ask(db_path):
    """A wide spread makes this visible: pricing off the ask would be wrong."""
    client = _client(_quote_response(bid=100.0, ask=110.0))

    place_marketable_sell_order("AAPL", 10, _config(db_path, buffer=0.0),
                                client=client, now=NOW)

    assert client.place_order.call_args.args[1]["price"] == "100.00"


def test_returns_the_broker_order_id(db_path):
    client = _client()

    order_id = place_marketable_sell_order(
        "AAPL", 10, _config(db_path), client=client, now=NOW
    )

    assert order_id == "55555"


# ─── No quote means no sell ──────────────────────────────────────────────────


def test_unusable_quote_blocks_the_sell(db_path):
    """Refuse rather than fall back to a market order.

    Without a quote there is no validated worst case, and an unbounded market
    sell on a stock already flagged as falling is exactly the fill this
    instrument exists to bound.
    """
    client = _client()
    client.get_quote.return_value.json.return_value = {"errors": ["bad token"]}

    with pytest.raises(QuoteUnavailable):
        place_marketable_sell_order("AAPL", 10, _config(db_path), client=client, now=NOW)

    client.place_order.assert_not_called()


def test_stale_quote_blocks_the_sell(db_path):
    client = _client(_quote_response(age_s=300))

    with pytest.raises(StaleQuote):
        place_marketable_sell_order("AAPL", 10, _config(db_path), client=client, now=NOW)

    client.place_order.assert_not_called()


def test_zero_bid_blocks_the_sell(db_path):
    """The dangerous one: a zero bid would price the sell at give-it-away."""
    client = _client(_quote_response(bid=0))

    with pytest.raises(QuoteUnavailable):
        place_marketable_sell_order("AAPL", 10, _config(db_path), client=client, now=NOW)

    client.place_order.assert_not_called()


def test_a_halted_kill_switch_still_blocks_the_sell(db_path):
    """The new path must go through the same sink guard as the old one."""
    from risk.kill_switch import TradingHalted
    kill_switch.halt(db_path, actor="operator", reason="incident")
    client = _client()

    with pytest.raises(TradingHalted):
        place_marketable_sell_order("AAPL", 10, _config(db_path), client=client, now=NOW)

    client.place_order.assert_not_called()


def test_quote_is_not_fetched_when_trading_is_halted(db_path):
    """Cheapest correct order: refuse before spending a broker round-trip."""
    kill_switch.halt(db_path, actor="operator", reason="incident")
    client = _client()

    with pytest.raises(Exception):
        place_marketable_sell_order("AAPL", 10, _config(db_path), client=client, now=NOW)

    client.get_quote.assert_not_called()


# ─── The sell approval path uses it ──────────────────────────────────────────


def _interaction():
    i = MagicMock()
    i.response.send_message = AsyncMock()
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    return i


# The two approval-path tests that lived here moved to
# tests/test_sell_approval_ledger.py when the sell path was rewired onto the
# guard table. They patched `place_marketable_sell_order` and mocked `queries`
# wholesale; the path no longer calls that function, and the behaviours they
# described are now checked against a real database:
#
#   places a marketable limit  -> test_the_order_is_priced_from_the_quote_the_guards_saw
#                                 (which also pins DAY duration and the exact
#                                  through-the-bid price, neither of which the
#                                  old assert_called_once could see)
#   reopens with no quote      -> test_no_usable_quote_refuses_the_sell
#
# What stays here is the pure pricing maths, which is what this file is for.
