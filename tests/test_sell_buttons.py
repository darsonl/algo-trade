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
from risk import kill_switch
import tempfile
import os
from datetime import datetime, timezone

from schwab_client.quotes import Quote


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    initialize_db(path)
    # Seeded here rather than in the config fixture because several live-mode
    # tests build their own Config() from this path. An unseeded switch reads
    # UNINITIALIZED, the sink refuses, and those tests would fail for a reason
    # unrelated to what they assert. Fail-closed behaviour has its own tests in
    # test_kill_switch.py and test_kill_switch_wiring.py.
    kill_switch.init(path, env_default=True)
    return path


@pytest.fixture
def config(db_path):
    c = Config()
    c.db_path = db_path
    c.dry_run = True
    # The sell path runs the guard table now, so it needs an approver allowlist
    # and the thresholds the guards read. In dry run the book the guards see is
    # the SIMULATED one (guard_snapshot), so these tests still size against the
    # positions they create rather than against an empty broker account.
    c.allowed_discord_user_ids = "1001"
    c.discord_guild_id = 0
    c.discord_channel_id = 0
    c.approval_slippage_buffer_pct = 0.5
    c.approval_price_tolerance_pct = 2.0
    c.max_daily_notional_usd = 20000.0
    return c


@pytest.fixture(autouse=True)
def _usable_quote():
    """Guard 4 needs one. Without it every sell here refuses for a reason none
    of these tests are about."""
    quote = Quote(symbol="AAPL", bid=170.0, ask=170.2, last=170.0,
                  quote_time=datetime.now(timezone.utc))
    with patch("discord_bot.bot.fetch_quote", return_value=quote):
        yield


@pytest.fixture
def mock_interaction():
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.user.id = 1001
    interaction.guild_id = 0
    interaction.channel_id = 0
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

    mock_interaction.response.defer.assert_called_once()
    mock_interaction.followup.send.assert_called_once()
    msg = mock_interaction.followup.send.call_args[0][0]
    assert "DRY RUN" in msg
    assert "selling" in msg
    assert "AAPL" in msg


@pytest.mark.asyncio
async def test_sell_approve_live_submits_a_marketable_limit(db_path, mock_interaction, config):
    """Was: patched `place_marketable_sell_order` and asserted its arguments.

    The path no longer calls it. It prices from the quote the GUARDS already
    saw and submits the built spec through `_call_place_order`, so the checked
    price and the sent price cannot diverge — which they could when the
    submission fetched a second quote of its own.
    """
    config.dry_run = False
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    submitted = []

    def _place(client, cfg, spec):
        submitted.append(spec)
        return MagicMock(status_code=201, headers={"Location": "https://x/orders/S1"})

    with patch("discord_bot.bot.collect_broker_snapshot", return_value=_broker_holding(10)), \
         patch("discord_bot.bot._call_place_order", side_effect=_place), \
         patch("schwab_client.auth.get_client", return_value=MagicMock()):
        await _get_approve_callback(view)(view, mock_interaction, MagicMock())

    assert len(submitted) == 1
    assert submitted[0]["orderType"] == "LIMIT"
    assert submitted[0]["duration"] == "DAY"


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
async def test_sell_approve_dry_run_does_not_reach_the_broker(
    config, mock_interaction, db_path
):
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    with patch("discord_bot.bot._call_place_order") as mock_place:
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


# ---------------------------------------------------------------------------
# Idempotency gate + order-failure recovery (review items 2/3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sell_approve_double_click_records_one_trade(config, mock_interaction, db_path):
    """Second approve loses the claim race (position closed + status approved): one trade only."""
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    second_interaction = AsyncMock()
    await _get_approve_callback(view)(view, mock_interaction, MagicMock())
    await _get_approve_callback(view)(view, second_interaction, MagicMock())

    conn = get_connection(db_path)
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM trades WHERE recommendation_id = ?", (rec_id,)
    ).fetchone()["n"]
    conn.close()
    assert count == 1
    # Second click was answered ephemerally, not with a sale confirmation
    second_interaction.followup.send.assert_not_called()
    assert second_interaction.response.send_message.call_args[1].get("ephemeral") is True


@pytest.mark.asyncio
async def test_sell_approve_definitive_refusal_reopens_recommendation(db_path, mock_interaction, config):
    """A 400 is the broker saying no. Nothing exists, so the position stays
    open, no trade is recorded, and a human may retry.

    Note this is now specifically a DEFINITIVE refusal. A timeout no longer
    reopens: it is `submit_unknown`, and reopening would invite a second sell
    of a position that may already be gone.
    """
    config.dry_run = False
    rec_id = create_recommendation(db_path, "AAPL", "SELL", "Overbought", 170.0, None, None)
    create_position(db_path, "AAPL", 10, 150.0)
    view = SellApproveRejectView(rec_id, "AAPL", 10.0, 170.0, config)

    refusal = RuntimeError("HTTP 400")
    refusal.response = MagicMock(status_code=400)

    with patch("discord_bot.bot.collect_broker_snapshot", return_value=_broker_holding(10)), \
         patch("discord_bot.bot._call_place_order", side_effect=refusal), \
         patch("schwab_client.auth.get_client", return_value=MagicMock()):
        await _get_approve_callback(view)(view, mock_interaction, MagicMock())

    rec = get_recommendation(db_path, rec_id)
    assert rec["status"] == "pending"
    assert len(get_open_positions(db_path)) == 1
    conn = get_connection(db_path)
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM trades WHERE recommendation_id = ?", (rec_id,)
    ).fetchone()["n"]
    conn.close()
    assert count == 0


def _broker_holding(shares, symbol="AAPL"):
    from risk.preflight import BrokerSnapshot
    return BrokerSnapshot(
        positions=[{"symbol": symbol, "quantity": float(shares),
                    "market_value": shares * 170.0, "avg_price": 150.0}],
        working_orders=[],
    )
