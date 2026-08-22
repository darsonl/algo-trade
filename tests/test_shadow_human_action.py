"""The human's click, and when it happened.

Approval latency is unrecoverable retrospectively -- historical data cannot
reveal whether or when someone would have clicked Approve. Recording it forward
is the only way this quantity ever exists.

Every test here drives the REAL `ApproveRejectView` handlers against a real
database. The plan's original tests called `queries.set_shadow_human_action`
directly; that function shipped in Task 4, so those tests passed against
untouched production code and would have survived deleting this task entirely.
The thing under test is the WIRING, so the wiring is what these call.
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
from research import shadow_log
from risk import kill_switch
from risk.preflight import BrokerSnapshot
from schwab_client.quotes import Quote

POSTED = datetime(2026, 8, 17, 14, 30, tzinfo=timezone.utc)   # 10:30 ET Mon
CLICKED = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)   # 30 minutes later
APPROVER = 1001


def _make_config(dry_run=True, trading_enabled=True):
    db_path = os.path.join(tempfile.mkdtemp(), "shadow_click.db")
    initialize_db(db_path)
    kill_switch.init(db_path, env_default=trading_enabled)
    c = Config()
    c.db_path = db_path
    c.dry_run = dry_run
    c.max_position_size_usd = 500.0
    c.max_portfolio_usd = 20000.0
    c.max_daily_notional_usd = 20000.0
    c.allowed_discord_user_ids = str(APPROVER)
    c.discord_guild_id = 0
    c.discord_channel_id = 0
    c.approval_price_tolerance_pct = 2.0
    c.approval_slippage_buffer_pct = 0.5
    return c


def _observed_recommendation(config, ticker="AAPL", price=100.0):
    """A recommendation with the shadow observation the scan would have left."""
    rec_id = create_recommendation(
        config.db_path, ticker=ticker, signal="BUY", reasoning="t",
        price=price, dividend_yield=None, pe_ratio=None,
    )
    expires = (CLICKED + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(config.db_path) as conn:
        conn.execute("UPDATE recommendations SET expires_at = ? WHERE id = ?",
                     (expires, rec_id))
    shadow_log.observe(config, ticker, "stock", "recommended", "recommended",
                       recommendation_id=rec_id, reference_price=price,
                       instant=POSTED)
    return rec_id


def _make_view(config, rec_id, ticker="AAPL", price=100.0):
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


async def _call_approve(view, interaction, now=CLICKED, place=None):
    place = place or (lambda client, config, spec: _Submitted())
    quote = Quote(symbol=view.ticker, bid=99.5, ask=100.0, last=100.0,
                  quote_time=now - timedelta(seconds=1))
    patches = (
        patch("discord_bot.bot.fetch_quote", return_value=quote),
        patch("discord_bot.bot.collect_broker_snapshot", return_value=BrokerSnapshot([], [])),
        patch("discord_bot.bot._utcnow", return_value=now),
        patch("discord_bot.bot._call_place_order", side_effect=place),
        patch("schwab_client.auth.get_client", return_value=MagicMock()),
    )
    for p in patches:
        p.start()
    try:
        await view.approve.callback.callback(view, interaction, MagicMock())
    finally:
        for p in patches:
            p.stop()


async def _call_reject(view, interaction, now=CLICKED):
    with patch("discord_bot.bot._utcnow", return_value=now):
        await view.reject.callback.callback(view, interaction, MagicMock())


def _click(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM shadow_observations").fetchone()


# --- the click is recorded ---

@pytest.mark.asyncio
async def test_approval_latency_is_derivable_from_the_stored_timestamps():
    config = _make_config(dry_run=True)
    rec_id = _observed_recommendation(config)
    await _call_approve(_make_view(config, rec_id), _make_interaction())

    row = _click(config.db_path)
    assert row["human_action"] == "approved"
    t0 = datetime.strptime(row["observed_at"], "%Y-%m-%dT%H:%M:%SZ")
    t1 = datetime.strptime(row["human_action_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert (t1 - t0).total_seconds() == 30 * 60


@pytest.mark.asyncio
async def test_a_live_approval_records_the_click_too():
    """The recording sits before the dry-run branch, so both modes reach it.

    A dry-run-only recording would silently stop measuring latency the day the
    bot is armed -- exactly when the number starts to matter.
    """
    config = _make_config(dry_run=False)
    rec_id = _observed_recommendation(config)
    await _call_approve(_make_view(config, rec_id), _make_interaction())

    assert _click(config.db_path)["human_action"] == "approved"


@pytest.mark.asyncio
async def test_rejection_is_recorded_as_rejected():
    config = _make_config()
    rec_id = _observed_recommendation(config, ticker="XOM")
    await _call_reject(_make_view(config, rec_id, ticker="XOM"), _make_interaction())

    row = _click(config.db_path)
    assert row["human_action"] == "rejected"
    assert row["human_action_at"] == "2026-08-17T15:00:00Z"


# --- the click is NOT recorded when there was no approval ---

@pytest.mark.asyncio
async def test_an_unauthorized_click_is_not_recorded_as_the_human_approving():
    """Guard 1 refuses before anything else. Recording it would attribute an
    approval to the operator that the operator never gave."""
    config = _make_config()
    rec_id = _observed_recommendation(config)
    await _call_approve(_make_view(config, rec_id), _make_interaction(user_id=4242))

    assert _click(config.db_path)["human_action"] is None


@pytest.mark.asyncio
async def test_a_guard_refusal_is_not_recorded_as_a_click():
    """A halted bot refuses at guard 2, after the click but before the claim.

    The click happened, but the recommendation was never approved -- and the
    funnel asks what the recommendation BECAME, not what was pressed.
    """
    config = _make_config(trading_enabled=False)
    rec_id = _observed_recommendation(config)
    await _call_approve(_make_view(config, rec_id), _make_interaction())

    assert _click(config.db_path)["human_action"] is None


@pytest.mark.asyncio
async def test_no_click_leaves_the_action_null():
    """A recommendation nobody touched must be distinguishable from a rejected
    one -- non-response is data, and lumping it in with rejection would
    overstate how often the human said no."""
    config = _make_config()
    _observed_recommendation(config, ticker="MSFT")

    row = _click(config.db_path)
    assert row["human_action"] is None
    assert row["human_action_at"] is None


@pytest.mark.asyncio
async def test_a_second_click_does_not_overwrite_the_first():
    """The claim is what is recorded, and only the first click claims."""
    config = _make_config(dry_run=False)
    rec_id = _observed_recommendation(config)
    view = _make_view(config, rec_id)

    await _call_approve(view, _make_interaction(), now=CLICKED)
    await _call_approve(view, _make_interaction(),
                        now=CLICKED + timedelta(minutes=45))

    assert _click(config.db_path)["human_action_at"] == "2026-08-17T15:00:00Z"


# --- instrumentation must never break the trade ---

@pytest.mark.asyncio
async def test_a_failing_shadow_write_does_not_abort_the_approval():
    """This is a research instrument bolted to the order path. A logging table
    that can refuse a trade is worse than no logging table."""
    config = _make_config(dry_run=False)
    rec_id = _observed_recommendation(config)
    interaction = _make_interaction()

    with patch("database.queries.set_shadow_human_action",
               side_effect=sqlite3.OperationalError("database is locked")):
        await _call_approve(_make_view(config, rec_id), interaction)

    sent = " ".join(str(c) for c in interaction.followup.send.call_args_list)
    assert "OID-1" in sent


@pytest.mark.asyncio
async def test_a_failing_shadow_write_does_not_abort_the_rejection():
    config = _make_config()
    rec_id = _observed_recommendation(config)
    view = _make_view(config, rec_id)

    with patch("database.queries.set_shadow_human_action",
               side_effect=sqlite3.OperationalError("database is locked")):
        await _call_reject(view, _make_interaction())

    with sqlite3.connect(config.db_path) as conn:
        status = conn.execute("SELECT status FROM recommendations WHERE id = ?",
                              (rec_id,)).fetchone()[0]
    assert status == "rejected"
