"""Tests for SellApproveRejectView button handlers — SELL-06, SELL-08, SELL-09."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from config import Config
from database.models import initialize_db, get_connection
from database.queries import (
    create_recommendation,
    get_recommendation,
    create_position,
    get_open_positions,
)
from discord_bot.bot import SellApproveRejectView
import tempfile
import os


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
    return c


@pytest.fixture
def mock_interaction():
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    return interaction


def _get_approve_callback(view):
    """Extract the approve button callback from a SellApproveRejectView."""
    return view.approve.callback.callback


def _get_reject_callback(view):
    """Extract the reject button callback from a SellApproveRejectView."""
    return view.reject.callback.callback


@pytest.mark.asyncio
async def test_sell_approve_dry_run_creates_trade_with_side_sell(config, mock_interaction, db_path):
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    await _get_approve_callback(view)(view, mock_interaction, MagicMock())

    conn = get_connection(db_path)
    trade = conn.execute(
        "SELECT * FROM trades WHERE recommendation_id = ?", (rec_id,)
    ).fetchone()
    assert trade is not None
    assert trade["side"] == "sell"
    assert trade["shares"] == 10
    conn.close()


@pytest.mark.asyncio
async def test_sell_approve_closes_position(config, mock_interaction, db_path):
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    await _get_approve_callback(view)(view, mock_interaction, MagicMock())

    positions = get_open_positions(db_path)
    assert len(positions) == 0  # position should be closed


@pytest.mark.asyncio
async def test_sell_approve_updates_recommendation_status(config, mock_interaction, db_path):
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    await _get_approve_callback(view)(view, mock_interaction, MagicMock())

    rec = get_recommendation(db_path, rec_id)
    assert rec["status"] == "approved"


@pytest.mark.asyncio
async def test_sell_approve_sends_confirmation_message(config, mock_interaction, db_path):
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    await _get_approve_callback(view)(view, mock_interaction, MagicMock())

    mock_interaction.response.send_message.assert_called_once()
    msg = mock_interaction.response.send_message.call_args[0][0]
    assert "DRY RUN" in msg
    assert "selling" in msg
    assert "AAPL" in msg


@pytest.mark.asyncio
@patch("discord_bot.bot.place_sell_order", return_value="ORDER123")
async def test_sell_approve_live_calls_place_sell_order(mock_place, db_path, mock_interaction):
    c = Config()
    c.db_path = db_path
    c.dry_run = False
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, c)

    await _get_approve_callback(view)(view, mock_interaction, MagicMock())

    mock_place.assert_called_once_with("AAPL", 10, c)


@pytest.mark.asyncio
async def test_sell_reject_sets_sell_blocked(config, mock_interaction, db_path):
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    await _get_reject_callback(view)(view, mock_interaction, MagicMock())

    conn = get_connection(db_path)
    pos = conn.execute(
        "SELECT sell_blocked FROM positions WHERE ticker = 'AAPL'"
    ).fetchone()
    assert pos["sell_blocked"] == 1
    conn.close()


@pytest.mark.asyncio
async def test_sell_reject_marks_recommendation_rejected(config, mock_interaction, db_path):
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    await _get_reject_callback(view)(view, mock_interaction, MagicMock())

    rec = get_recommendation(db_path, rec_id)
    assert rec["status"] == "rejected"


@pytest.mark.asyncio
async def test_sell_reject_position_stays_open(config, mock_interaction, db_path):
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    await _get_reject_callback(view)(view, mock_interaction, MagicMock())

    positions = get_open_positions(db_path)
    assert len(positions) == 1
    assert positions[0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_sell_approve_dry_run_does_not_call_place_sell_order(
    config, mock_interaction, db_path
):
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    with patch("discord_bot.bot.place_sell_order") as mock_place:
        await _get_approve_callback(view)(view, mock_interaction, MagicMock())
        mock_place.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 13 Task 3: cost_basis population test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sell_approve_populates_cost_basis(config, mock_interaction, db_path):
    """SellApproveRejectView.approve fetches avg_cost_usd from open position and passes it as cost_basis."""
    from database.models import get_connection
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 120.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    await _get_approve_callback(view)(view, mock_interaction, MagicMock())

    conn = get_connection(db_path)
    trade = conn.execute(
        "SELECT cost_basis FROM trades WHERE recommendation_id = ? AND side = 'sell'",
        (rec_id,),
    ).fetchone()
    conn.close()
    assert trade is not None
    assert trade["cost_basis"] == pytest.approx(120.0)
