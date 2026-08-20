"""The sell approval path, on the guards and the ledger (spec v4 §9, sell half).

Sells run a shorter guard list than buys — 1, 2, 3, 4, 5, 11 and 12. The
buy-only ceilings do not apply: selling reduces exposure, and refusing a sell
because the portfolio is large would hold a position through exactly the
decline the signal fired on.

Two things differ from the buy path and are the point of this file:

  * **Guard 12 revalidates the quantity against the broker.** The view captured
    `self.shares` when the embed was posted; the position can shrink before
    anyone clicks.
  * **One quote prices both the guard and the order.** The old path guarded on
    nothing and then let `place_marketable_sell_order` fetch its *own* quote,
    so the price that was checked and the price that was sent could differ.
"""
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Config
from database.models import initialize_db
from database import queries
from database.queries import create_position, create_recommendation
from discord_bot.bot import SellApproveRejectView
from risk import kill_switch
from risk.preflight import BrokerSnapshot
from schwab_client.quotes import Quote

NOW = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)  # 11:00 ET Mon
APPROVER = 1001


def _config(dry_run=False, **overrides):
    db_path = os.path.join(tempfile.mkdtemp(), "sell.db")
    initialize_db(db_path)
    kill_switch.init(db_path, env_default=True)
    c = Config()
    c.db_path = db_path
    c.dry_run = dry_run
    c.allowed_discord_user_ids = str(APPROVER)
    c.discord_guild_id = 0
    c.discord_channel_id = 0
    c.approval_slippage_buffer_pct = 0.5
    c.approval_price_tolerance_pct = 2.0
    c.max_daily_notional_usd = 20000.0
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _sell_recommendation(config, ticker="AAPL", price=170.0):
    rec_id = create_recommendation(
        config.db_path, ticker=ticker, signal="SELL", reasoning="overbought",
        price=price, dividend_yield=None, pe_ratio=None,
    )
    expires = (NOW + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(config.db_path) as conn:
        conn.execute("UPDATE recommendations SET expires_at = ? WHERE id = ?", (expires, rec_id))
    return rec_id


def _interaction(user_id=APPROVER):
    it = MagicMock()
    it.user.id = user_id
    it.guild_id = 0
    it.channel_id = 0
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup.send = AsyncMock()
    return it


def _quote(bid=170.0, ask=170.2):
    return Quote(symbol="AAPL", bid=bid, ask=ask, last=bid,
                 quote_time=NOW - timedelta(seconds=1))


class _Submitted:
    status_code = 201
    headers = {"Location": "https://api.schwab.com/orders/SELL-9"}


def _held(shares=10.0, symbol="AAPL"):
    return BrokerSnapshot(
        positions=[{"symbol": symbol, "quantity": shares,
                    "market_value": shares * 170.0, "avg_price": 150.0}],
        working_orders=[],
    )


def _orders(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY id")]


def _rec_status(db_path, rec_id):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT status FROM recommendations WHERE id = ?", (rec_id,)
        ).fetchone()[0]


def _positions(db_path):
    """OPEN positions only -- close_position sets status='closed', it does not
    delete, so selecting every row would never show a close."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in
                conn.execute("SELECT * FROM positions WHERE status = 'open'")]


_DEFAULT = object()


async def _approve(view, interaction, *, quote=_DEFAULT, snapshot=None,
                   submit_error=None, capture=None):
    # A sentinel, not None: quote=None is a MEANINGFUL argument here (guard 4),
    # and `quote or _quote()` would silently substitute the default for it.
    quote = _quote() if quote is _DEFAULT else quote
    snapshot = snapshot if snapshot is not None else _held()

    def _place(client, config, spec):
        if capture is not None:
            capture.append(spec)
        if submit_error:
            raise submit_error
        return _Submitted()

    with patch("discord_bot.bot.fetch_quote", return_value=quote), \
         patch("discord_bot.bot.collect_broker_snapshot", return_value=snapshot), \
         patch("discord_bot.bot._utcnow", return_value=NOW), \
         patch("discord_bot.bot._call_place_order", side_effect=_place), \
         patch("schwab_client.auth.get_client", return_value=MagicMock()):
        await view.approve.callback.callback(view, interaction, MagicMock())


def _view(config, rec_id, shares=10, price=170.0, ticker="AAPL"):
    create_position(config.db_path, ticker, shares, 150.0)
    return SellApproveRejectView(rec_id, ticker, shares, price, config)


# --- the ledger ---

@pytest.mark.asyncio
async def test_an_approved_sell_writes_an_order_row():
    config = _config()
    rec_id = _sell_recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    rows = _orders(config.db_path)
    assert len(rows) == 1
    assert rows[0]["side"] == "sell"
    assert rows[0]["status"] == "submitted"
    assert rows[0]["broker_order_id"] == "SELL-9"


@pytest.mark.asyncio
async def test_a_sell_reserves_no_buy_capital():
    """Selling reduces exposure. A sell row must not eat the daily buy ceiling."""
    from database.models import get_connection
    from database.queries import get_day_notional

    config = _config()
    rec_id = _sell_recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    conn = get_connection(config.db_path)
    try:
        assert get_day_notional(conn, instant=NOW) == 0.0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_an_ambiguous_sell_leaves_the_row_unknown_and_keeps_the_position():
    """We do not know it sold, so the position must not be closed."""
    config = _config()
    rec_id = _sell_recommendation(config)
    await _approve(_view(config, rec_id), _interaction(),
                   submit_error=TimeoutError("read timed out"))

    assert _orders(config.db_path)[0]["status"] == "submit_unknown"
    assert _rec_status(config.db_path, rec_id) == "approved"
    assert len(_positions(config.db_path)) == 1


@pytest.mark.asyncio
async def test_a_submitted_sell_closes_the_position_and_records_cost_basis():
    config = _config()
    rec_id = _sell_recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    with sqlite3.connect(config.db_path) as conn:
        conn.row_factory = sqlite3.Row
        trade = dict(conn.execute("SELECT * FROM trades WHERE side='sell'").fetchone())
    assert trade["cost_basis"] == 150.0
    assert _positions(config.db_path) == []


# --- guard 12: quantity revalidated against the broker ---

@pytest.mark.asyncio
async def test_selling_more_than_the_broker_holds_is_refused():
    """The embed was posted when 10 were held; only 3 are left now."""
    config = _config()
    rec_id = _sell_recommendation(config)
    await _approve(_view(config, rec_id, shares=10), _interaction(),
                   snapshot=_held(shares=3.0))

    assert _orders(config.db_path) == []
    assert _rec_status(config.db_path, rec_id) == "pending"
    assert len(_positions(config.db_path)) == 1


@pytest.mark.asyncio
async def test_selling_a_symbol_the_broker_does_not_hold_is_refused():
    config = _config()
    rec_id = _sell_recommendation(config)
    await _approve(_view(config, rec_id), _interaction(),
                   snapshot=BrokerSnapshot(positions=[], working_orders=[]))

    assert _orders(config.db_path) == []


@pytest.mark.asyncio
async def test_a_broker_outage_refuses_the_sell():
    """No market-order fallback and no trading blind: a sell we cannot size is
    a sell we do not send."""
    config = _config()
    rec_id = _sell_recommendation(config)
    await _approve(_view(config, rec_id), _interaction(),
                   snapshot=BrokerSnapshot(positions=None, working_orders=None))

    assert _orders(config.db_path) == []
    assert _rec_status(config.db_path, rec_id) == "pending"


@pytest.mark.asyncio
async def test_no_usable_quote_refuses_the_sell():
    """There is no market-order fallback on purpose: an unbounded market sell
    on a stock already flagged as falling is the fill this instrument exists
    to bound."""
    config = _config()
    rec_id = _sell_recommendation(config)
    await _approve(_view(config, rec_id), _interaction(), quote=None)

    assert _orders(config.db_path) == []
    assert _rec_status(config.db_path, rec_id) == "pending"


@pytest.mark.asyncio
async def test_an_unresolved_order_blocks_a_sell():
    """Guard 11. Selling into an unknown order state can oversell.

    The first recommendation is retired first so that the block demonstrated
    here comes from the unresolved ORDER, not from the active-recommendation
    unique index.
    """
    config = _config()
    first = _sell_recommendation(config)
    await _approve(_view(config, first), _interaction(),
                   submit_error=TimeoutError("boom"))

    queries.update_recommendation_status(config.db_path, first, "completed")
    second = _sell_recommendation(config)
    view = SellApproveRejectView(second, "AAPL", 10, 170.0, config)
    await _approve(view, _interaction())

    assert len(_orders(config.db_path)) == 1


@pytest.mark.asyncio
async def test_a_halted_switch_refuses_the_sell():
    config = _config()
    kill_switch.halt(config.db_path, actor="test", reason="incident")
    rec_id = _sell_recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    assert _orders(config.db_path) == []
    assert _rec_status(config.db_path, rec_id) == "pending"


@pytest.mark.asyncio
async def test_an_unauthorized_sell_click_is_refused_privately():
    config = _config()
    rec_id = _sell_recommendation(config)
    interaction = _interaction(user_id=999)
    await _approve(_view(config, rec_id), interaction)

    interaction.response.defer.assert_not_called()
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True


# --- one quote prices both the guard and the order ---

@pytest.mark.asyncio
async def test_the_order_is_priced_from_the_quote_the_guards_saw():
    """The old path guarded on nothing and let place_marketable_sell_order
    fetch its OWN quote, so the checked price and the sent price could differ.
    Bid 170.00 less the 0.5% buffer, rounded DOWN to the tick, is 169.15."""
    captured = []
    config = _config()
    rec_id = _sell_recommendation(config)
    await _approve(_view(config, rec_id), _interaction(),
                   quote=_quote(bid=170.0), capture=captured)

    assert len(captured) == 1
    assert str(captured[0]["price"]) == "169.15"
    assert captured[0]["duration"] == "DAY"


# --- dry run ---

@pytest.mark.asyncio
async def test_dry_run_sizes_the_sell_against_SIMULATED_positions():
    """In dry run the broker holds nothing, so a broker-sourced guard 12 would
    refuse every simulated sell. The guards run against the simulated book
    instead, which is what makes a dry run a rehearsal rather than a no-op."""
    config = _config(dry_run=True)
    rec_id = _sell_recommendation(config)
    await _approve(_view(config, rec_id), _interaction(),
                   snapshot=BrokerSnapshot(positions=[], working_orders=[]))

    assert _rec_status(config.db_path, rec_id) == "approved"
    assert _positions(config.db_path) == []          # closed, as before


@pytest.mark.asyncio
async def test_dry_run_writes_no_order_row():
    config = _config(dry_run=True)
    rec_id = _sell_recommendation(config)
    await _approve(_view(config, rec_id), _interaction(),
                   snapshot=BrokerSnapshot(positions=[], working_orders=[]))

    assert _orders(config.db_path) == []


@pytest.mark.asyncio
async def test_dry_run_still_refuses_an_oversized_sell():
    config = _config(dry_run=True)
    rec_id = _sell_recommendation(config)
    view = _view(config, rec_id, shares=3)
    view.shares = 99                                  # more than the simulated 3
    await _approve(view, _interaction(),
                   snapshot=BrokerSnapshot(positions=[], working_orders=[]))

    assert _rec_status(config.db_path, rec_id) == "pending"
