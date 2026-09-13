"""An approval outside regular hours prices off the last close, end to end.

The design (spec v4 §8, guard 4) says staleness is enforced during the regular
session only: before the open the last close is the only quote there is, and
pre-open is when this system is designed to be used. Guard 4 implemented that.
But `fetch_quote` ran first with its own 30-second rule at ANY hour, raised
StaleQuote, and handed guard 4 None -- so every pre-open Approve was refused
"No usable quote". Each piece was tested and correct alone; nothing drove the
real `fetch_quote` into the real guard table.

Only the Schwab client is faked here. The quote parser, the guard table and
the calendar are real.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Config
from database.models import initialize_db
from database.queries import create_recommendation
from discord_bot.bot import ApproveRejectView
from risk import kill_switch
from risk.preflight import BrokerSnapshot

PRE_OPEN = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)     # 08:00 ET Mon
MID_SESSION = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)  # 11:00 ET Mon


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "t.db")
    initialize_db(path)
    return path


def _config(db_path):
    c = Config()
    c.db_path = db_path
    c.execution_mode = "dry_run"
    c.dry_run = True
    c.allowed_discord_user_ids = "1001"
    c.discord_guild_id = 0
    c.discord_channel_id = 0
    c.max_daily_notional_usd = 20000.0
    c.approval_price_tolerance_pct = 2.0
    c.approval_slippage_buffer_pct = 0.5
    c.max_position_size_usd = 500.0
    c.max_portfolio_usd = 20000.0
    c.quote_max_age_s = 30
    return c


def _schwab_quote(stamped_at):
    """A Schwab get_quote client whose quote is stamped at `stamped_at`."""
    resp = MagicMock()
    resp.json.return_value = {"AAPL": {"symbol": "AAPL", "quote": {
        "bidPrice": 99.9, "askPrice": 100.1, "lastPrice": 100.0,
        "quoteTime": int(stamped_at.timestamp() * 1000)}}}
    client = MagicMock()
    client.get_quote.return_value = resp
    return client


async def _click_approve(db_path, now, quote_stamped_at):
    kill_switch.init(db_path, env_default=True)
    rec_id = create_recommendation(db_path, "AAPL", "BUY", "t", 100.0, None, None)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE recommendations SET expires_at = ? WHERE id = ?",
                     ((now + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S"), rec_id))
    view = ApproveRejectView(rec_id, "AAPL", 100.0, _config(db_path))
    interaction = MagicMock()
    interaction.user.id = 1001
    interaction.guild_id = 0
    interaction.channel_id = 0
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()

    with patch("schwab_client.auth.get_client",
               return_value=_schwab_quote(quote_stamped_at)), \
         patch("discord_bot.bot.collect_broker_snapshot",
               return_value=BrokerSnapshot([], [])), \
         patch("discord_bot.bot._utcnow", return_value=now):
        await view.approve.callback.callback(view, interaction, MagicMock())
    return " ".join(str(c.args[0]) for c in interaction.followup.send.call_args_list if c.args)


@pytest.mark.asyncio
async def test_a_pre_open_approval_prices_off_the_last_close(db_path):
    """Friday's close, stamped 20:00 UTC, approved Monday 08:00 ET: ~64 hours
    old, and the only quote that exists. Before the fix: 'No usable quote'."""
    friday_close = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)
    sent = await _click_approve(db_path, PRE_OPEN, friday_close)
    assert "[DRY RUN] Approved" in sent, sent


@pytest.mark.asyncio
async def test_a_stale_quote_during_the_session_is_still_refused(db_path):
    """The rule moved to guard 4; it did not go away."""
    sent = await _click_approve(db_path, MID_SESSION, MID_SESSION - timedelta(minutes=5))
    assert "Approved" not in sent
    assert "300s old" in sent, sent
