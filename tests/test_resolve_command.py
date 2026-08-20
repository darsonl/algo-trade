"""`/resolve` as an operator command, and the alert that makes it get used.

Before this existed, a `submit_unknown` row had no operator path out at all.
Guard 11 blocks every new order for that ticker while such a row is open, so a
single ambiguous submission silently blocked a symbol FOREVER — and nothing
ever said so. The command is the exit; the stuck alert is what stops the block
from being silent.

The command is gated on OPS_USER_IDS, the same allowlist as /halt and /resume:
resolution is an incident-recovery action, and the operator holding the halt
lever is the one who will be dealing with a stuck order.
"""
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Config
from database.models import get_cursor, initialize_db
from database.queries import (
    create_order,
    get_order,
    get_resolution_events,
    mark_order_submit_unknown,
)
from discord_bot.bot import TradingBot


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    initialize_db(path)
    return path


def _config(db_path, allowlist="42"):
    c = Config()
    c.db_path = db_path
    c.dry_run = False
    c.ops_user_ids = allowlist
    return c


def _bot(config):
    b = TradingBot.__new__(TradingBot)
    b.config = config
    b._cached_channel = None
    return b


def _interaction(user_id=42):
    i = MagicMock()
    i.user.id = user_id
    i.response.send_message = AsyncMock()
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    return i


def _unresolved(db_path, ticker="AAPL", shares=5.0):
    with get_cursor(db_path) as conn:
        oid = create_order(conn, None, ticker, "buy", "limit", shares, 100.0, 100.0)
        mark_order_submit_unknown(conn, oid, "read timeout after POST")
    return oid


def _sent(interaction):
    """Everything the command replied, however it replied."""
    parts = []
    for mock in (interaction.response.send_message, interaction.followup.send):
        for call in mock.call_args_list:
            parts.extend(str(a) for a in call.args)
            parts.extend(str(v) for v in call.kwargs.values())
    return "\n".join(parts)


# ─── the allowlist ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unlisted_user_cannot_run_the_report(db_path):
    bot = _bot(_config(db_path, allowlist="42"))
    interaction = _interaction(user_id=99)
    await bot._resolve_command(interaction)
    assert "not authorized" in _sent(interaction).lower()


@pytest.mark.asyncio
async def test_an_unlisted_user_cannot_write_a_resolution(db_path):
    """confirmed_absent RELEASES reserved capital. This is the direction that
    must not be reachable by an arbitrary channel member."""
    oid = _unresolved(db_path)
    bot = _bot(_config(db_path, allowlist="42"))
    interaction = _interaction(user_id=99)

    await bot._resolve_command(interaction, order_id=oid,
                               resolution="confirmed_absent", evidence="nope")

    assert "not authorized" in _sent(interaction).lower()
    with get_cursor(db_path) as conn:
        assert get_order(conn, oid)["status"] == "submit_unknown"
        assert get_resolution_events(conn, oid) == []


@pytest.mark.asyncio
async def test_an_empty_allowlist_authorizes_nobody(db_path):
    bot = _bot(_config(db_path, allowlist=""))
    interaction = _interaction(user_id=42)
    await bot._resolve_command(interaction)
    assert "not authorized" in _sent(interaction).lower()


# ─── report mode ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_arguments_runs_the_report(db_path):
    bot = _bot(_config(db_path))
    interaction = _interaction()

    with patch("discord_bot.bot.report_unknown_submissions",
               return_value="THE REPORT") as report:
        await bot._resolve_command(interaction)

    assert report.called
    assert "THE REPORT" in _sent(interaction)


@pytest.mark.asyncio
async def test_a_failing_report_is_reported_not_raised(db_path):
    """The command must survive a broker outage: an operator hitting a dead
    command during an incident learns nothing about their stuck order."""
    bot = _bot(_config(db_path))
    interaction = _interaction()

    with patch("discord_bot.bot.report_unknown_submissions",
               side_effect=RuntimeError("boom")):
        await bot._resolve_command(interaction)

    assert "boom" in _sent(interaction)


# ─── write mode ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adopt_attaches_the_broker_id_and_marks_it_submitted(db_path):
    oid = _unresolved(db_path)
    bot = _bot(_config(db_path))
    interaction = _interaction()

    await bot._resolve_command(interaction, order_id=oid, resolution="adopt",
                               evidence="saw it in the Schwab app",
                               broker_order_id="BRK7")

    with get_cursor(db_path) as conn:
        row = get_order(conn, oid)
        assert row["status"] == "submitted"
        assert row["broker_order_id"] == "BRK7"


@pytest.mark.asyncio
async def test_confirmed_absent_releases_the_order(db_path):
    oid = _unresolved(db_path)
    bot = _bot(_config(db_path))

    await bot._resolve_command(_interaction(), order_id=oid,
                               resolution="confirmed_absent",
                               evidence="no such order in the app")

    with get_cursor(db_path) as conn:
        assert get_order(conn, oid)["status"] == "submit_failed"


@pytest.mark.asyncio
async def test_keep_blocked_changes_nothing_but_is_still_audited(db_path):
    oid = _unresolved(db_path)
    bot = _bot(_config(db_path))

    await bot._resolve_command(_interaction(), order_id=oid,
                               resolution="keep_blocked",
                               evidence="Schwab support ticket open")

    with get_cursor(db_path) as conn:
        assert get_order(conn, oid)["status"] == "submit_unknown"
        assert len(get_resolution_events(conn, oid)) == 1


@pytest.mark.asyncio
async def test_the_operator_is_recorded_as_the_actor(db_path):
    """An unattributed override is not an audit trail."""
    oid = _unresolved(db_path)
    bot = _bot(_config(db_path))

    await bot._resolve_command(_interaction(user_id=42), order_id=oid,
                               resolution="keep_blocked", evidence="checked")

    with get_cursor(db_path) as conn:
        assert "42" in get_resolution_events(conn, oid)[0]["actor"]


@pytest.mark.asyncio
async def test_adopt_without_a_broker_id_is_refused_not_crashed(db_path):
    oid = _unresolved(db_path)
    bot = _bot(_config(db_path))
    interaction = _interaction()

    await bot._resolve_command(interaction, order_id=oid, resolution="adopt",
                               evidence="I am sure")

    assert "broker order id" in _sent(interaction).lower()
    with get_cursor(db_path) as conn:
        assert get_order(conn, oid)["status"] == "submit_unknown"


@pytest.mark.asyncio
async def test_resolving_an_unknown_order_is_refused_not_crashed(db_path):
    bot = _bot(_config(db_path))
    interaction = _interaction()

    await bot._resolve_command(interaction, order_id=999,
                               resolution="keep_blocked", evidence="checked")

    assert "999" in _sent(interaction)


@pytest.mark.asyncio
async def test_a_resolution_without_evidence_is_refused(db_path):
    oid = _unresolved(db_path)
    bot = _bot(_config(db_path))
    interaction = _interaction()

    await bot._resolve_command(interaction, order_id=oid,
                               resolution="keep_blocked", evidence="   ")

    assert "evidence" in _sent(interaction).lower()
    with get_cursor(db_path) as conn:
        assert get_resolution_events(conn, oid) == []


@pytest.mark.asyncio
async def test_an_order_id_without_a_resolution_reports_rather_than_guessing(db_path):
    """Half a write is not a write. It must never fall through to a default."""
    oid = _unresolved(db_path)
    bot = _bot(_config(db_path))
    interaction = _interaction()

    await bot._resolve_command(interaction, order_id=oid)

    with get_cursor(db_path) as conn:
        assert get_resolution_events(conn, oid) == []


# ─── the stuck-order alert ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_order_past_the_threshold_alerts(db_path):
    from risk import resolution

    oid = _unresolved(db_path)
    with get_cursor(db_path) as conn:
        conn.execute("UPDATE orders SET submitted_at = datetime('now', '-30 hours') "
                     "WHERE id = ?", (oid,))

    config = _config(db_path)
    config.stuck_approval_alert_h = 24
    bot = _bot(config)
    bot.send_ops_alert = AsyncMock()

    await resolution.alert_stuck_orders(bot, config)

    assert bot.send_ops_alert.await_count == 1
    assert "AAPL" in str(bot.send_ops_alert.await_args)


@pytest.mark.asyncio
async def test_a_fresh_order_does_not_alert(db_path):
    from risk import resolution

    _unresolved(db_path)
    config = _config(db_path)
    config.stuck_approval_alert_h = 24
    bot = _bot(config)
    bot.send_ops_alert = AsyncMock()

    await resolution.alert_stuck_orders(bot, config)

    assert bot.send_ops_alert.await_count == 0


@pytest.mark.asyncio
async def test_the_alert_repeats_because_the_block_persists(db_path):
    """Alert-once would let the operator miss it and the symbol stay blocked
    with nothing ever mentioning it again."""
    from risk import resolution

    oid = _unresolved(db_path)
    with get_cursor(db_path) as conn:
        conn.execute("UPDATE orders SET submitted_at = datetime('now', '-30 hours') "
                     "WHERE id = ?", (oid,))

    config = _config(db_path)
    config.stuck_approval_alert_h = 24
    bot = _bot(config)
    bot.send_ops_alert = AsyncMock()

    await resolution.alert_stuck_orders(bot, config)
    await resolution.alert_stuck_orders(bot, config)

    assert bot.send_ops_alert.await_count == 2


@pytest.mark.asyncio
async def test_the_alert_names_the_command_that_clears_it(db_path):
    from risk import resolution

    oid = _unresolved(db_path)
    with get_cursor(db_path) as conn:
        conn.execute("UPDATE orders SET submitted_at = datetime('now', '-30 hours') "
                     "WHERE id = ?", (oid,))

    config = _config(db_path)
    config.stuck_approval_alert_h = 24
    bot = _bot(config)
    bot.send_ops_alert = AsyncMock()

    await resolution.alert_stuck_orders(bot, config)

    assert "/resolve" in str(bot.send_ops_alert.await_args)


@pytest.mark.asyncio
async def test_a_broken_alert_never_aborts_the_scan(db_path):
    """It runs at scan start. A failing nag must not stop the scan it precedes."""
    from risk import resolution

    oid = _unresolved(db_path)
    with get_cursor(db_path) as conn:
        conn.execute("UPDATE orders SET submitted_at = datetime('now', '-30 hours') "
                     "WHERE id = ?", (oid,))

    config = _config(db_path)
    config.stuck_approval_alert_h = 24
    bot = _bot(config)
    bot.send_ops_alert = AsyncMock(side_effect=RuntimeError("discord down"))

    await resolution.alert_stuck_orders(bot, config)  # must not raise


# ─── registration ────────────────────────────────────────────────────────────

def test_the_command_registers_with_the_three_resolutions_as_choices():
    """Discord itself then refuses anything else, so a typo cannot reach the
    database layer. A signature discord.py cannot map registers no command at
    all, and the operator's only exit from a stuck order disappears silently.
    """
    from discord import app_commands
    from discord_bot.bot import TradingBot as Bot

    command = app_commands.Command(
        name="resolve", description="x", callback=Bot._resolve_command,
    )
    params = {p.name: p for p in command.parameters}

    assert [c.value for c in params["resolution"].choices] == [
        "adopt", "confirmed_absent", "keep_blocked"
    ]
    # Report mode must be reachable with no arguments at all.
    assert not any(p.required for p in command.parameters)


# ─── config ──────────────────────────────────────────────────────────────────

def test_stuck_alert_threshold_has_a_default():
    assert Config().stuck_approval_alert_h == 24


def test_resolve_lookback_has_a_default():
    assert Config().resolve_lookback_min == 30
