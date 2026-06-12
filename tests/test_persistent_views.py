"""Tests for persistent Discord views — Approve/Reject buttons must survive a bot restart.

The fix's load-bearing invariant: button custom_ids are DETERMINISTIC (keyed by rec_id),
so a view re-registered at startup matches the buttons baked into the message before the
restart. These tests pin the exact custom_id values — a revert to discord.py's default
random ids would otherwise leave the suite green while silently re-breaking restart
survival.
"""
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Config
from database.models import initialize_db
from database.queries import (
    create_recommendation,
    set_discord_message_id,
    create_position,
    get_recommendation,
    get_open_positions,
)
from discord_bot.bot import (
    ApproveRejectView,
    SellApproveRejectView,
    build_view_for_recommendation,
    TradingBot,
)


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    initialize_db(path)
    return path


@pytest.fixture
def config(db_path):
    c = Config()
    c.db_path = db_path
    c.dry_run = True
    # Pin sizing so reconstruction tests don't depend on the developer's local .env.
    c.max_position_size_usd = 500.0
    c.max_portfolio_usd = 20000.0
    return c


# --- deterministic custom_ids: the real regression guard ---

@pytest.mark.asyncio
async def test_buy_view_has_deterministic_custom_ids(config):
    view = ApproveRejectView(rec_id=42, ticker="AAPL", price=100.0, config=config)
    assert view.approve.custom_id == "approve:42"
    assert view.reject.custom_id == "reject:42"
    assert view.is_persistent()


@pytest.mark.asyncio
async def test_sell_view_has_deterministic_custom_ids(config):
    view = SellApproveRejectView(7, "AAPL", 10.0, 170.0, config)
    assert view.approve.custom_id == "sell_approve:7"
    assert view.reject.custom_id == "sell_reject:7"
    assert view.is_persistent()


@pytest.mark.asyncio
async def test_buy_and_sell_custom_ids_do_not_collide(config):
    buy = ApproveRejectView(rec_id=5, ticker="AAPL", price=100.0, config=config)
    sell = SellApproveRejectView(5, "AAPL", 10.0, 170.0, config)
    ids = {
        buy.approve.custom_id, buy.reject.custom_id,
        sell.approve.custom_id, sell.reject.custom_id,
    }
    assert len(ids) == 4  # all distinct even for the same rec_id


# --- build_view_for_recommendation reconstructs the right view from a stored row ---

@pytest.mark.asyncio
async def test_build_view_returns_buy_view_for_buy_row(config, db_path):
    rec_id = create_recommendation(db_path, "AAPL", "BUY", "Strong", 100.0, 0.02, 20.0)
    rec = get_recommendation(db_path, rec_id)
    view = build_view_for_recommendation(rec, config)
    assert isinstance(view, ApproveRejectView)
    assert view.approve.custom_id == f"approve:{rec_id}"
    assert view.ticker == "AAPL"
    assert view.price == 100.0


@pytest.mark.asyncio
async def test_build_view_returns_sell_view_with_shares_from_position(config, db_path):
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 13, 150.0)
    rec = get_recommendation(db_path, rec_id)
    view = build_view_for_recommendation(rec, config)
    assert isinstance(view, SellApproveRejectView)
    assert view.shares == 13
    assert view.approve.custom_id == f"sell_approve:{rec_id}"


# --- restart survival: a reconstructed view still drives the DB correctly ---

@pytest.mark.asyncio
async def test_reconstructed_buy_view_callback_updates_db(config, db_path):
    """Rebuild the buy view from the DB row and fire approve -> recommendation approved,
    position recorded (dry-run, no broker call)."""
    rec_id = create_recommendation(db_path, "AAPL", "BUY", "Strong", 100.0, 0.02, 20.0)
    set_discord_message_id(db_path, rec_id, "999")
    rec = get_recommendation(db_path, rec_id)

    view = build_view_for_recommendation(rec, config)
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    await view.approve.callback.callback(view, interaction, MagicMock())

    assert get_recommendation(db_path, rec_id)["status"] == "approved"
    assert any(p["ticker"] == "AAPL" for p in get_open_positions(db_path))


@pytest.mark.asyncio
async def test_reconstructed_sell_view_callback_updates_db(config, db_path):
    """Simulate restart: rebuild the sell view from the DB row and fire approve ->
    position closes + recommendation goes 'approved' (dry-run, no broker call)."""
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    rec = get_recommendation(db_path, rec_id)

    view = build_view_for_recommendation(rec, config)
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    await view.approve.callback.callback(view, interaction, MagicMock())

    assert get_recommendation(db_path, rec_id)["status"] == "approved"
    assert len(get_open_positions(db_path)) == 0


# --- startup re-registration binds a view per pending rec that has a message id ---

@pytest.mark.asyncio
async def test_register_persistent_views_adds_pending(config, db_path):
    buy_id = create_recommendation(db_path, "AAPL", "BUY", "Strong", 100.0, 0.02, 20.0)
    set_discord_message_id(db_path, buy_id, "111")
    sell_id = create_recommendation(db_path, "MSFT", "SELL", "Overbought", 200.0, None, None)
    create_position(db_path, "MSFT", 5, 180.0)
    set_discord_message_id(db_path, sell_id, "222")
    # A pending rec with no Discord message id can't be bound -> must be skipped.
    create_recommendation(db_path, "TSLA", "BUY", "n/a", 50.0, None, None)

    bot = TradingBot(config)
    with patch.object(bot, "add_view") as mock_add_view:
        bot._register_persistent_views()

    assert mock_add_view.call_count == 2
    message_ids = {kw["message_id"] for _args, kw in mock_add_view.call_args_list}
    assert message_ids == {111, 222}
