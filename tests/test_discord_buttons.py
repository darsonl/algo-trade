import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from discord_bot.bot import ApproveRejectView
from config import Config


# --- helpers ---

def _make_config(dry_run=True, max_usd=500.0, db_path=":memory:"):
    c = Config()
    c.dry_run = dry_run
    c.max_position_size_usd = max_usd
    c.db_path = db_path
    return c


def _make_view(ticker="AAPL", price=100.0, rec_id=1, dry_run=True, max_usd=500.0):
    config = _make_config(dry_run=dry_run, max_usd=max_usd)
    return ApproveRejectView(rec_id=rec_id, ticker=ticker, price=price, config=config)


def _make_interaction():
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


async def _call_approve(view, interaction, button=None):
    """Invoke the approve handler, bypassing discord.py's Button wrapper."""
    if button is None:
        button = MagicMock()
    await view.approve.callback.callback(view, interaction, button)


async def _call_reject(view, interaction, button=None):
    """Invoke the reject handler, bypassing discord.py's Button wrapper."""
    if button is None:
        button = MagicMock()
    await view.reject.callback.callback(view, interaction, button)


# --- approve: dry-run ---

@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_dry_run_does_not_call_place_order(mock_queries, mock_place_order):
    mock_queries.create_trade.return_value = 1
    view = _make_view(dry_run=True)
    await _call_approve(view, _make_interaction())
    mock_place_order.assert_not_called()


@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_dry_run_creates_trade_with_none_order_id(mock_queries, mock_place_order):
    mock_queries.create_trade.return_value = 1
    view = _make_view(ticker="AAPL", price=100.0, rec_id=7, dry_run=True)
    await _call_approve(view, _make_interaction())
    call_kwargs = mock_queries.create_trade.call_args[1]
    assert call_kwargs["order_id"] is None
    assert call_kwargs["ticker"] == "AAPL"
    assert call_kwargs["recommendation_id"] == 7


# --- approve: live ---

@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_live_calls_place_order(mock_queries, mock_place_order):
    mock_place_order.return_value = "schwab-order-xyz"
    mock_queries.create_trade.return_value = 1
    view = _make_view(price=100.0, dry_run=False)
    await _call_approve(view, _make_interaction())
    mock_place_order.assert_called_once_with("AAPL", 5, view.config)


@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_live_stores_order_id_in_trade(mock_queries, mock_place_order):
    mock_place_order.return_value = "schwab-order-abc"
    mock_queries.create_trade.return_value = 1
    view = _make_view(price=100.0, dry_run=False)
    await _call_approve(view, _make_interaction())
    call_kwargs = mock_queries.create_trade.call_args[1]
    assert call_kwargs["order_id"] == "schwab-order-abc"


# --- approve: status update ---

@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_sets_status_to_approved(mock_queries, mock_place_order):
    mock_queries.create_trade.return_value = 1
    view = _make_view(rec_id=3, dry_run=True)
    await _call_approve(view, _make_interaction())
    mock_queries.update_recommendation_status.assert_called_once_with(
        view.config.db_path, 3, "approved"
    )


@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_sends_confirmation_message(mock_queries, mock_place_order):
    mock_queries.create_trade.return_value = 1
    view = _make_view(dry_run=True)
    interaction = _make_interaction()
    await _call_approve(view, interaction)
    interaction.response.send_message.assert_awaited_once()


# --- approve: zero shares guard ---

@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_zero_shares_sends_ephemeral_and_does_not_trade(mock_queries, mock_place_order):
    """Price exceeds max_position_size_usd -> 0 shares -> early return, no trade record."""
    view = _make_view(price=600.0, max_usd=500.0, dry_run=True)
    interaction = _make_interaction()
    await _call_approve(view, interaction)
    interaction.response.send_message.assert_awaited_once()
    mock_queries.create_trade.assert_not_called()
    mock_place_order.assert_not_called()


# --- reject (TEST-03) ---

@pytest.mark.asyncio
@patch("discord_bot.bot.queries")
async def test_reject_sets_status_to_rejected(mock_queries):
    view = _make_view(rec_id=9)
    await _call_reject(view, _make_interaction())
    mock_queries.update_recommendation_status.assert_called_once_with(
        view.config.db_path, 9, "rejected"
    )


@pytest.mark.asyncio
@patch("discord_bot.bot.queries")
async def test_reject_sends_confirmation_message(mock_queries):
    view = _make_view(ticker="JNJ")
    interaction = _make_interaction()
    await _call_reject(view, interaction)
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
@patch("discord_bot.bot.queries")
async def test_reject_does_not_call_place_order(mock_queries):
    view = _make_view()
    with patch("discord_bot.bot.place_order") as mock_place:
        await _call_reject(view, _make_interaction())
        mock_place.assert_not_called()
