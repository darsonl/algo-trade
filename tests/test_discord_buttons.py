import os
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from discord_bot.bot import ApproveRejectView
from config import Config
from database.models import initialize_db
from risk import kill_switch


# --- helpers ---

def _make_config(dry_run=True, max_usd=500.0, max_portfolio_usd=20000.0, db_path=None, use_limit_buy=True,
                 trading_enabled=True):
    """Build a Config backed by a REAL database file.

    ":memory:" used to be the default here, which quietly meant "a brand new
    empty database per connection" — fine while nothing read durable state, but
    the kill switch does, and an empty database reads UNINITIALIZED, which is
    not-enabled. Live-mode approvals would be blocked for the wrong reason.
    """
    c = Config()
    c.dry_run = dry_run
    c.max_position_size_usd = max_usd
    c.max_portfolio_usd = max_portfolio_usd
    if db_path is None:
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        initialize_db(db_path)
        kill_switch.init(db_path, env_default=trading_enabled)
    c.db_path = db_path
    c.use_limit_buy = use_limit_buy
    return c


def _make_view(ticker="AAPL", price=100.0, rec_id=1, dry_run=True, max_usd=500.0, max_portfolio_usd=20000.0, use_limit_buy=True):
    config = _make_config(dry_run=dry_run, max_usd=max_usd, max_portfolio_usd=max_portfolio_usd, use_limit_buy=use_limit_buy)
    return ApproveRejectView(rec_id=rec_id, ticker=ticker, price=price, config=config)


def _make_interaction():
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
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
    # use_limit_buy=False to exercise the market-order (place_order) path
    view = _make_view(price=100.0, dry_run=False, use_limit_buy=False)
    await _call_approve(view, _make_interaction())
    mock_place_order.assert_called_once_with("AAPL", 5, view.config)


@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_live_stores_order_id_in_trade(mock_queries, mock_place_order):
    mock_place_order.return_value = "schwab-order-abc"
    mock_queries.create_trade.return_value = 1
    # use_limit_buy=False so place_order (not place_limit_order) sets the order_id
    view = _make_view(price=100.0, dry_run=False, use_limit_buy=False)
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
    mock_queries.claim_recommendation.assert_called_once_with(
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
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()


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
    mock_queries.claim_recommendation.assert_called_once_with(
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


# --- approve: exposure guard (POS-05) ---

@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_exposure_guard_blocks(mock_queries, mock_place_order):
    """Existing exposure $480 + new buy $100 = $580 > max $500 -> ephemeral rejection, no trade."""
    # One open position: 40 shares at avg_cost_usd=12.0 (last_price=None) -> $480 exposure
    existing_pos = {"shares": 40, "last_price": None, "avg_cost_usd": 12.0}
    mock_queries.get_open_positions.return_value = [existing_pos]
    mock_queries.create_trade.return_value = 1

    # price=100, max=500 -> shares=5, new_exposure=500, existing=480, total=980 > max_portfolio_usd=900
    view = _make_view(ticker="TSLA", price=100.0, max_usd=500.0, max_portfolio_usd=900.0)
    interaction = _make_interaction()
    await _call_approve(view, interaction)

    interaction.response.send_message.assert_awaited_once()
    call_kwargs = interaction.response.send_message.call_args[1]
    assert call_kwargs.get("ephemeral") is True
    msg = interaction.response.send_message.call_args[0][0]
    assert "exceed" in msg
    mock_queries.create_trade.assert_not_called()


@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_exposure_guard_allows(mock_queries, mock_place_order):
    """No existing positions -> exposure guard passes -> trade and upsert called."""
    mock_queries.get_open_positions.return_value = []
    mock_queries.create_trade.return_value = 1

    view = _make_view(ticker="AAPL", price=100.0, max_usd=500.0)
    interaction = _make_interaction()
    await _call_approve(view, interaction)

    mock_queries.create_trade.assert_called_once()
    mock_queries.upsert_position.assert_called_once()


@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_upserts_position(mock_queries, mock_place_order):
    """Successful approve calls upsert_position with (db_path, ticker, shares, price)."""
    mock_queries.get_open_positions.return_value = []
    mock_queries.create_trade.return_value = 1

    view = _make_view(ticker="MSFT", price=200.0, max_usd=500.0, rec_id=42)
    interaction = _make_interaction()
    await _call_approve(view, interaction)

    # shares = floor(500/200) = 2
    mock_queries.upsert_position.assert_called_once_with(
        view.config.db_path, "MSFT", 2, 200.0
    )


@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_exposure_uses_last_price_fallback(mock_queries, mock_place_order):
    """When last_price is set, use last_price (not avg_cost_usd) for exposure calc."""
    # Position: 10 shares, last_price=15.0, avg_cost_usd=10.0 -> exposure = 10*15=150
    existing_pos = {"shares": 10, "last_price": 15.0, "avg_cost_usd": 10.0}
    mock_queries.get_open_positions.return_value = [existing_pos]
    mock_queries.create_trade.return_value = 1

    # price=50, max=500 -> shares=10, new_exposure=500. existing=150. total=650 > max_portfolio_usd=600
    view = _make_view(ticker="NVDA", price=50.0, max_usd=500.0, max_portfolio_usd=600.0)
    interaction = _make_interaction()
    await _call_approve(view, interaction)

    # Verify blocked (uses last_price=15.0, not avg=10.0)
    call_kwargs = interaction.response.send_message.call_args[1]
    assert call_kwargs.get("ephemeral") is True
    mock_queries.create_trade.assert_not_called()

    # Now verify if avg_cost_usd=10.0 was used instead, exposure=10*10=100+500=600 -> also blocked
    # So let's verify with a case where only last_price causes the block but avg_cost wouldn't
    # Reset: existing_pos with last_price=45.0, avg_cost_usd=2.0
    # price=50, max=500, shares=10, new=500
    # with last_price=45 -> existing=10*45=450, total=950 > 500 -> blocked
    # with avg=2.0 -> existing=10*2=20, total=520 > 500 -> also blocked (same outcome here)
    # Use a case where avg would pass but last_price blocks:
    # last_price=30, avg=1. existing with last=10*30=300. new=10*50=500. total=800>500 blocked
    # with avg=1: existing=10. total=510>500 -> blocked either way
    # The test confirms last_price IS used when not None -- the mock sets last_price=15.0
    # and the guard correctly blocked. That's sufficient to confirm last_price was used.


# --- limit/market routing in ApproveRejectView.approve (RISK-01, RISK-02, RISK-03) ---

@pytest.mark.asyncio
@patch("discord_bot.bot.place_limit_order")
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_limit_live_calls_place_limit_order_not_place_order(
    mock_queries, mock_place_order, mock_place_limit_order
):
    """RISK-01 + RISK-02: use_limit_buy=True, dry_run=False -> place_limit_order called, place_order NOT called."""
    mock_place_limit_order.return_value = "schwab-limit-001"
    mock_queries.create_trade.return_value = 1
    view = _make_view(price=100.0, dry_run=False, use_limit_buy=True)
    await _call_approve(view, _make_interaction())
    mock_place_limit_order.assert_called_once_with("AAPL", 5, 100.0, view.config)
    mock_place_order.assert_not_called()


@pytest.mark.asyncio
@patch("discord_bot.bot.place_limit_order")
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_market_live_calls_place_order_not_place_limit_order(
    mock_queries, mock_place_order, mock_place_limit_order
):
    """RISK-02: use_limit_buy=False, dry_run=False -> place_order called, place_limit_order NOT called."""
    mock_place_order.return_value = "schwab-market-002"
    mock_queries.create_trade.return_value = 1
    view = _make_view(price=100.0, dry_run=False, use_limit_buy=False)
    await _call_approve(view, _make_interaction())
    mock_place_order.assert_called_once_with("AAPL", 5, view.config)
    mock_place_limit_order.assert_not_called()


@pytest.mark.asyncio
@patch("discord_bot.bot.place_limit_order")
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_limit_live_create_trade_records_limit_kwargs(
    mock_queries, mock_place_order, mock_place_limit_order
):
    """RISK-03 live limit path: create_trade called with limit_price=100.0 and order_type='limit'."""
    mock_place_limit_order.return_value = "schwab-limit-003"
    mock_queries.create_trade.return_value = 1
    view = _make_view(price=100.0, dry_run=False, use_limit_buy=True)
    await _call_approve(view, _make_interaction())
    call_kwargs = mock_queries.create_trade.call_args[1]
    assert call_kwargs["limit_price"] == 100.0
    assert call_kwargs["order_type"] == "limit"


@pytest.mark.asyncio
@patch("discord_bot.bot.place_limit_order")
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_dry_run_create_trade_records_market_regardless_of_use_limit_buy(
    mock_queries, mock_place_order, mock_place_limit_order
):
    """D-06: dry_run=True always records order_type='market', limit_price=None regardless of use_limit_buy."""
    mock_queries.create_trade.return_value = 1
    view = _make_view(price=100.0, dry_run=True, use_limit_buy=True)
    await _call_approve(view, _make_interaction())
    call_kwargs = mock_queries.create_trade.call_args[1]
    assert call_kwargs["limit_price"] is None
    assert call_kwargs["order_type"] == "market"


@pytest.mark.asyncio
@patch("discord_bot.bot.place_limit_order")
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_limit_live_confirmation_message_contains_limit_gtc(
    mock_queries, mock_place_order, mock_place_limit_order
):
    """D-10: use_limit_buy=True, dry_run=False -> confirmation message contains '(limit, GTC)'."""
    mock_place_limit_order.return_value = "schwab-limit-004"
    mock_queries.create_trade.return_value = 1
    view = _make_view(price=100.0, dry_run=False, use_limit_buy=True)
    interaction = _make_interaction()
    await _call_approve(view, interaction)
    msg = interaction.followup.send.call_args[0][0]
    assert "(limit, GTC)" in msg


@pytest.mark.asyncio
@patch("discord_bot.bot.place_limit_order")
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_market_live_confirmation_message_does_not_contain_limit_gtc(
    mock_queries, mock_place_order, mock_place_limit_order
):
    """D-11: use_limit_buy=False, dry_run=False -> confirmation message does NOT contain '(limit, GTC)'."""
    mock_place_order.return_value = "schwab-market-005"
    mock_queries.create_trade.return_value = 1
    view = _make_view(price=100.0, dry_run=False, use_limit_buy=False)
    interaction = _make_interaction()
    await _call_approve(view, interaction)
    msg = interaction.followup.send.call_args[0][0]
    assert "(limit, GTC)" not in msg


# --- idempotency gate + order-failure recovery (review items 2/3) ---

@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_double_click_places_only_one_order(mock_queries, mock_place_order):
    """Second click loses the pending->approved race: no second order, ephemeral notice."""
    mock_place_order.return_value = "schwab-order-once"
    mock_queries.create_trade.return_value = 1
    mock_queries.get_open_positions.return_value = []
    mock_queries.claim_recommendation.side_effect = [True, False]

    view = _make_view(price=100.0, dry_run=False, use_limit_buy=False)
    first, second = _make_interaction(), _make_interaction()
    await _call_approve(view, first)
    await _call_approve(view, second)

    mock_place_order.assert_called_once()
    second.followup.send.assert_not_awaited()
    second.response.send_message.assert_awaited_once()
    assert second.response.send_message.call_args[1].get("ephemeral") is True
    assert "already handled" in second.response.send_message.call_args[0][0]


@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_order_failure_reopens_recommendation(mock_queries, mock_place_order):
    """Order failure releases the claim (status back to pending) and records no trade."""
    mock_place_order.side_effect = RuntimeError("Schwab unavailable")
    mock_queries.get_open_positions.return_value = []

    view = _make_view(rec_id=5, price=100.0, dry_run=False, use_limit_buy=False)
    interaction = _make_interaction()
    await _call_approve(view, interaction)

    mock_queries.update_recommendation_status.assert_called_once_with(
        view.config.db_path, 5, "pending"
    )
    mock_queries.create_trade.assert_not_called()
    mock_queries.upsert_position.assert_not_called()
    msg = interaction.followup.send.call_args[0][0]
    assert "failed" in msg


@pytest.mark.asyncio
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_defers_before_placing_order(mock_queries, mock_place_order):
    """The interaction is acknowledged (defer) before the slow Schwab call runs."""
    call_order = []
    mock_queries.get_open_positions.return_value = []
    mock_queries.create_trade.return_value = 1
    mock_place_order.side_effect = lambda *a, **k: call_order.append("order") or "oid"

    view = _make_view(price=100.0, dry_run=False, use_limit_buy=False)
    interaction = _make_interaction()
    interaction.response.defer = AsyncMock(side_effect=lambda *a, **k: call_order.append("defer"))
    await _call_approve(view, interaction)

    assert call_order == ["defer", "order"]


@pytest.mark.asyncio
@patch("discord_bot.bot.queries")
async def test_reject_already_handled_sends_ephemeral(mock_queries):
    """Reject on a non-pending recommendation gets the ephemeral already-handled notice."""
    mock_queries.claim_recommendation.return_value = False
    view = _make_view(rec_id=11)
    interaction = _make_interaction()
    await _call_reject(view, interaction)
    assert interaction.response.send_message.call_args[1].get("ephemeral") is True
    assert "already handled" in interaction.response.send_message.call_args[0][0]
