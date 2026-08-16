"""ApproveRejectView handlers.

The BUY approval path moved onto the guard table and the order ledger, and its
behaviour is covered against a real database in `test_approval_ledger.py`. What
remains here is the reject path and the handful of approve behaviours that are
about the VIEW rather than about trading: idempotency under a double click, and
that a dry run never reaches the broker.

The old tests in this file mocked `queries` wholesale and asserted call shapes
(`place_order(...)` called with these args). They were testing the previous
implementation rather than any behaviour, and the branch-per-`USE_LIMIT_BUY`
cases described a toggle that no longer exists -- every buy is a limit order.
"""
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Config
from database.models import initialize_db
from database.queries import create_recommendation
from discord_bot.bot import ApproveRejectView
from risk import kill_switch
from risk.preflight import BrokerSnapshot
from schwab_client.quotes import Quote

NOW = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)  # 11:00 ET Mon
APPROVER = 1001


def _make_config(dry_run=True, max_usd=500.0, max_portfolio_usd=20000.0):
    """A Config backed by a REAL database file.

    ":memory:" used to be the default here, which quietly meant "a brand new
    empty database per connection" — fine while nothing read durable state, but
    the kill switch does, and an empty database reads UNINITIALIZED, which is
    not-enabled. Live-mode approvals would be blocked for the wrong reason.
    """
    db_path = os.path.join(tempfile.mkdtemp(), "buttons.db")
    initialize_db(db_path)
    kill_switch.init(db_path, env_default=True)
    c = Config()
    c.db_path = db_path
    c.dry_run = dry_run
    c.max_position_size_usd = max_usd
    c.max_portfolio_usd = max_portfolio_usd
    c.max_daily_notional_usd = 20000.0
    c.allowed_discord_user_ids = str(APPROVER)
    c.discord_guild_id = 0
    c.discord_channel_id = 0
    c.approval_price_tolerance_pct = 2.0
    c.approval_slippage_buffer_pct = 0.5
    return c


def _recommendation(config, ticker="AAPL", price=100.0):
    rec_id = create_recommendation(
        config.db_path, ticker=ticker, signal="BUY", reasoning="t",
        price=price, dividend_yield=None, pe_ratio=None,
    )
    expires = (NOW + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(config.db_path) as conn:
        conn.execute("UPDATE recommendations SET expires_at = ? WHERE id = ?", (expires, rec_id))
    return rec_id


def _make_view(config=None, ticker="AAPL", price=100.0, rec_id=None, **cfg):
    config = config or _make_config(**cfg)
    if rec_id is None:
        rec_id = _recommendation(config, ticker=ticker, price=price)
    return ApproveRejectView(rec_id=rec_id, ticker=ticker, price=price, config=config)


def _make_interaction(user_id=APPROVER):
    it = MagicMock()
    it.user.id = user_id
    it.guild_id = 0
    it.channel_id = 0
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup.send = AsyncMock()
    return it


class _Submitted:
    status_code = 201
    headers = {"Location": "https://api.schwab.com/orders/OID-1"}


def _broker_patches(place):
    quote = Quote(symbol="AAPL", bid=99.5, ask=100.0, last=100.0,
                  quote_time=NOW - timedelta(seconds=1))
    return (
        patch("discord_bot.bot.fetch_quote", return_value=quote),
        patch("discord_bot.bot.collect_broker_snapshot", return_value=BrokerSnapshot([], [])),
        patch("discord_bot.bot._utcnow", return_value=NOW),
        patch("discord_bot.bot._call_place_order", side_effect=place),
        patch("schwab_client.auth.get_client", return_value=MagicMock()),
    )


async def _call_approve(view, interaction, place=None):
    place = place or (lambda client, config, spec: _Submitted())
    patches = _broker_patches(place)
    for p in patches:
        p.start()
    try:
        await view.approve.callback.callback(view, interaction, MagicMock())
    finally:
        for p in patches:
            p.stop()


async def _call_reject(view, interaction):
    await view.reject.callback.callback(view, interaction, MagicMock())


def _rec_status(db_path, rec_id):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT status FROM recommendations WHERE id = ?", (rec_id,)
        ).fetchone()[0]


# --- approve: the view's own responsibilities ---

@pytest.mark.asyncio
async def test_approve_dry_run_does_not_reach_the_broker():
    calls = []
    view = _make_view(dry_run=True)
    await _call_approve(view, _make_interaction(),
                        place=lambda c, cfg, s: calls.append(s) or _Submitted())
    assert calls == []


@pytest.mark.asyncio
async def test_approve_defers_before_any_network_work():
    """Discord closes the interaction after 3s; everything after this is HTTP."""
    view = _make_view(dry_run=True)
    interaction = _make_interaction()
    await _call_approve(view, interaction)
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited()


@pytest.mark.asyncio
async def test_approve_double_click_submits_only_one_order():
    """Idempotency. The claim is inside the reservation transaction, so the
    second click loses the race and never reaches the broker."""
    submissions = []
    config = _make_config(dry_run=False)
    rec_id = _recommendation(config)
    view = _make_view(config=config, rec_id=rec_id)

    def _place(client, cfg, spec):
        submissions.append(spec)
        return _Submitted()

    await _call_approve(view, _make_interaction(), place=_place)
    await _call_approve(view, _make_interaction(), place=_place)

    assert len(submissions) == 1
    assert _rec_status(config.db_path, rec_id) == "approved"


@pytest.mark.asyncio
async def test_approve_by_an_unlisted_user_is_refused_privately():
    config = _make_config(dry_run=False)
    view = _make_view(config=config)
    interaction = _make_interaction(user_id=4242)
    await _call_approve(view, interaction)

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True
    interaction.response.defer.assert_not_called()


# --- reject ---

@pytest.mark.asyncio
async def test_reject_sets_status_to_rejected():
    config = _make_config()
    rec_id = _recommendation(config)
    view = _make_view(config=config, rec_id=rec_id)
    await _call_reject(view, _make_interaction())
    assert _rec_status(config.db_path, rec_id) == "rejected"


@pytest.mark.asyncio
async def test_reject_sends_confirmation_message():
    view = _make_view(ticker="JNJ")
    interaction = _make_interaction()
    await _call_reject(view, interaction)
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_never_reaches_the_broker():
    view = _make_view()
    with patch("discord_bot.bot._call_place_order") as place:
        await _call_reject(view, _make_interaction())
        place.assert_not_called()


@pytest.mark.asyncio
async def test_reject_already_handled_sends_ephemeral():
    config = _make_config()
    rec_id = _recommendation(config)
    view = _make_view(config=config, rec_id=rec_id)
    await _call_reject(view, _make_interaction())

    interaction = _make_interaction()
    await _call_reject(view, interaction)
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True
