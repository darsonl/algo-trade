"""One scan at a time, across BOTH scan paths (spec v4 step 13's remainder).

Not one lock each. A symbol can appear in the stock universe and in the ETF
universe, so two concurrent scans can reach the same ticker, and the dupe guard
(`ticker_recommended_today`) is a read followed by a later write with awaits in
between — the classic check-then-act race. `idx_active_rec_per_ticker` is the
durable backstop, but it turns the race into an IntegrityError rather than
preventing it, and an aborted scan is not a good outcome either.

A scheduled scan that arrives while one is running is SKIPPED, not queued:
running it afterwards would screen a market that has already moved on, against
an analyst quota it has already spent.
"""
import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from risk.scan_lock import scan_in_progress, scan_lock


# ─── The lock primitive ──────────────────────────────────────────────────────


def test_the_scan_lock_survives_a_second_event_loop():
    """A module-level asyncio.Lock binds to the first loop that CONTENDS on it
    and raises for every loop after — and acquire()'s uncontended fast path
    hides that from any test which never actually blocks. So this CONTENDS, on
    two separate loops. Same trap as the submission and approval gates."""
    assert asyncio.run(_contended_use()) is True
    assert asyncio.run(_contended_use()) is True


async def _contended_use() -> bool:
    lock = scan_lock()

    async def hold():
        async with lock:
            await asyncio.sleep(0)

    await asyncio.gather(hold(), hold())   # real contention, not a fast path
    assert scan_lock() is lock             # same loop -> same lock
    return True


def test_each_loop_gets_its_own_lock():
    locks = []
    asyncio.run(_record(locks))
    asyncio.run(_record(locks))
    assert locks[0] is not locks[1]


async def _record(sink):
    sink.append(scan_lock())


@pytest.mark.asyncio
async def test_scan_in_progress_is_false_when_idle():
    assert scan_in_progress() is False


@pytest.mark.asyncio
async def test_scan_in_progress_is_true_while_held():
    async with scan_lock():
        assert scan_in_progress() is True


@pytest.mark.asyncio
async def test_the_lock_is_released_after_the_holder_raises():
    """A scan that dies must not wedge every later scan."""
    with pytest.raises(RuntimeError):
        async with scan_lock():
            raise RuntimeError("scan blew up")

    assert scan_in_progress() is False


# ─── Both scans share it ─────────────────────────────────────────────────────


def _config(tmp_path):
    from config import Config
    from database.models import initialize_db
    c = Config()
    c.db_path = str(tmp_path / "t.db")
    initialize_db(c.db_path)
    c.dry_run = True
    c.execution_mode = "dry_run"
    c.discord_channel_id = 1
    return c


def _bot():
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    bot.send_recommendation = AsyncMock()
    return bot


@pytest.mark.asyncio
async def test_a_second_concurrent_scan_is_skipped_not_queued(tmp_path):
    import main
    config = _config(tmp_path)
    starts = []

    async def _slow_sweep(cfg):
        starts.append("body")
        await asyncio.sleep(0.02)   # hold the lock long enough to contend
        return 0

    patches = [
        patch.object(main, "get_top_sp500_by_fundamentals", return_value=[]),
        patch.object(main, "get_universe", side_effect=lambda *a, **k: []),
        patch.object(main, "sweep_terminal_recommendations", side_effect=_slow_sweep),
        patch.object(main, "alert_stuck_orders", new=AsyncMock()),
        patch.object(main, "_drain_ops_outbox", new=AsyncMock()),
    ]
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        await asyncio.gather(
            main.run_scan(_bot(), config), main.run_scan(_bot(), config)
        )

    assert starts == ["body"], "the second scan must not run the body"


@pytest.mark.asyncio
async def test_the_etf_scan_shares_the_stock_scan_s_lock(tmp_path):
    """Not one lock each: a symbol can appear in both universes, so two
    concurrent scans can reach the same ticker.

    Instrumented on `_drain_ops_outbox`, the FIRST statement inside the
    locked body of both scans. An earlier version of this test watched
    `get_universe`, which the ETF path never calls -- so it passed
    vacuously, and a mutation giving each scan its own lock survived it.
    """
    import main
    config = _config(tmp_path)
    ran = []

    async def _record(bot):
        ran.append("etf-body")

    patches = [
        patch.object(main, "_drain_ops_outbox", side_effect=_record),
        patch.object(main, "get_watchlist", return_value=[]),
        patch.object(main, "partition_watchlist", side_effect=lambda *a, **k: ([], [])),
        patch.object(main, "alert_stuck_orders", new=AsyncMock()),
    ]
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        async with scan_lock():
            await main.run_scan_etf(_bot(), config)

    assert ran == [], "the ETF scan must be skipped while a stock scan holds the lock"


# ─── The slash commands say so ───────────────────────────────────────────────


def _interaction():
    i = MagicMock()
    i.response.send_message = AsyncMock()
    return i


def _bot_instance():
    from config import Config
    from discord_bot.bot import TradingBot
    with patch("discord.Client.__init__", return_value=None), \
         patch("discord.app_commands.CommandTree", return_value=MagicMock()):
        bot = TradingBot.__new__(TradingBot)
        bot.config = Config()
        bot._scan_callback = AsyncMock()
        bot._scan_etf_callback = AsyncMock()
        bot.tree = MagicMock()
    return bot


@pytest.mark.asyncio
async def test_scan_command_refuses_while_a_scan_runs():
    from discord_bot.bot import TradingBot
    bot = _bot_instance()
    interaction = _interaction()

    async with scan_lock():
        with patch("discord_bot.bot.asyncio.create_task") as create_task:
            await TradingBot._scan_command(bot, interaction)

    create_task.assert_not_called()
    assert "already running" in interaction.response.send_message.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_scan_etf_command_refuses_while_a_scan_runs():
    from discord_bot.bot import TradingBot
    bot = _bot_instance()
    interaction = _interaction()

    async with scan_lock():
        with patch("discord_bot.bot.asyncio.create_task") as create_task:
            await TradingBot._scan_etf_command(bot, interaction)

    create_task.assert_not_called()
    assert "already running" in interaction.response.send_message.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_scan_command_still_runs_when_idle():
    from discord_bot.bot import TradingBot
    bot = _bot_instance()

    with patch("discord_bot.bot.asyncio.create_task") as create_task:
        await TradingBot._scan_command(bot, _interaction())

    create_task.assert_called_once()
    # The patched create_task never consumed the coroutine it was handed;
    # closing it keeps the run free of "coroutine was never awaited" noise.
    create_task.call_args[0][0].close()
