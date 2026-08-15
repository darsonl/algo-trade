"""Durable outbox for ops alerts (round-5 #11).

`send_ops_alert` used to catch every delivery error and only log it. Several
safety states now depend on an alert ARRIVING — stuck orders, unresolved
submissions, reconciliation failures — and a swallowed exception makes "Discord
was down" indistinguishable from "nothing was wrong". Absence of data is not
data.

The fix is a transactional outbox: the alert is persisted BEFORE delivery is
attempted, so a failed send leaves a durable row a later drain can retry.
"""
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import Config
from database import queries
from database.models import get_cursor, initialize_db
from discord_bot.bot import TradingBot
from tests.test_ops_hardening import etf_scan_patches, stock_scan_patches


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    initialize_db(path)
    return path


@pytest.fixture
def config_for_scan(db_path):
    c = Config()
    c.db_path = db_path
    c.dry_run = True  # never reach the live Schwab API from the suite
    c.analyst_call_delay_s = 0
    return c


# ─── Enqueue ─────────────────────────────────────────────────────────────────


def test_enqueue_persists_an_undelivered_alert(db_path):
    with get_cursor(db_path) as conn:
        alert_id = queries.enqueue_ops_alert(conn, "reconciliation failed")

    with get_cursor(db_path) as conn:
        row = queries.get_ops_alert(conn, alert_id)

    assert row["message"] == "reconciliation failed"
    assert row["delivered_at"] is None
    assert row["attempts"] == 0


def test_enqueued_alert_appears_in_undelivered(db_path):
    with get_cursor(db_path) as conn:
        alert_id = queries.enqueue_ops_alert(conn, "stuck order 7")
        undelivered = queries.get_undelivered_ops_alerts(conn)

    assert [r["id"] for r in undelivered] == [alert_id]


# ─── Delivery outcome ────────────────────────────────────────────────────────


def test_marking_delivered_removes_it_from_undelivered(db_path):
    with get_cursor(db_path) as conn:
        alert_id = queries.enqueue_ops_alert(conn, "delivered once")
        queries.mark_ops_alert_delivered(conn, alert_id)

        assert queries.get_undelivered_ops_alerts(conn) == []
        assert queries.get_ops_alert(conn, alert_id)["delivered_at"] is not None


def test_failure_records_the_error_and_keeps_it_undelivered(db_path):
    """A failed send must NOT consume the alert — that is the whole point."""
    with get_cursor(db_path) as conn:
        alert_id = queries.enqueue_ops_alert(conn, "keep me")
        queries.record_ops_alert_failure(conn, alert_id, "ConnectionError")

        row = queries.get_ops_alert(conn, alert_id)
        assert row["delivered_at"] is None
        assert row["attempts"] == 1
        assert row["last_error"] == "ConnectionError"
        assert [r["id"] for r in queries.get_undelivered_ops_alerts(conn)] == [alert_id]


def test_repeated_failures_accumulate_attempts(db_path):
    with get_cursor(db_path) as conn:
        alert_id = queries.enqueue_ops_alert(conn, "flaky")
        queries.record_ops_alert_failure(conn, alert_id, "first")
        queries.record_ops_alert_failure(conn, alert_id, "second")

        row = queries.get_ops_alert(conn, alert_id)

    assert row["attempts"] == 2
    assert row["last_error"] == "second"


# ─── Retrieval order and bounds ──────────────────────────────────────────────


def test_undelivered_are_returned_oldest_first(db_path):
    """Ops alerts are a narrative; replaying them out of order misleads the operator."""
    with get_cursor(db_path) as conn:
        first = queries.enqueue_ops_alert(conn, "first")
        second = queries.enqueue_ops_alert(conn, "second")
        third = queries.enqueue_ops_alert(conn, "third")

        ids = [r["id"] for r in queries.get_undelivered_ops_alerts(conn)]

    assert ids == [first, second, third]


def test_undelivered_respects_limit(db_path):
    """A long outage must not dump an unbounded backlog into one drain pass."""
    with get_cursor(db_path) as conn:
        for i in range(5):
            queries.enqueue_ops_alert(conn, f"alert {i}")

        limited = queries.get_undelivered_ops_alerts(conn, limit=2)

    assert [r["message"] for r in limited] == ["alert 0", "alert 1"]


def test_get_ops_alert_returns_none_for_unknown_id(db_path):
    with get_cursor(db_path) as conn:
        assert queries.get_ops_alert(conn, 999) is None


# ─── send_ops_alert: persist before delivery ─────────────────────────────────


@pytest.fixture
def bot(db_path):
    cfg = Config()
    cfg.db_path = db_path
    cfg.dry_run = True
    b = TradingBot.__new__(TradingBot)
    b.config = cfg
    b._cached_channel = None
    return b


def _channel(send=None):
    ch = MagicMock()
    ch.send = send or AsyncMock()
    return ch


def _undelivered(db_path):
    with get_cursor(db_path) as conn:
        return queries.get_undelivered_ops_alerts(conn)


@pytest.mark.asyncio
async def test_successful_send_marks_the_alert_delivered(bot, db_path):
    bot._cached_channel = _channel()

    await bot.send_ops_alert("all good")

    bot._cached_channel.send.assert_awaited_once_with("[OPS ALERT] all good")
    # Asserting the row EXISTS and is delivered, not merely that nothing is
    # pending — an empty outbox would satisfy that vacuously.
    with get_cursor(db_path) as conn:
        rows = conn.execute("SELECT * FROM ops_alerts").fetchall()
    assert len(rows) == 1
    assert rows[0]["message"] == "all good"
    assert rows[0]["delivered_at"] is not None


@pytest.mark.asyncio
async def test_failed_send_leaves_the_alert_undelivered(bot, db_path):
    """The defect this slice fixes: the alert used to vanish into a log line."""
    bot._cached_channel = _channel(AsyncMock(side_effect=ConnectionError("discord down")))

    await bot.send_ops_alert("reconciliation failed")

    pending = _undelivered(db_path)
    assert len(pending) == 1
    assert pending[0]["message"] == "reconciliation failed"
    assert pending[0]["attempts"] == 1
    assert "ConnectionError" in pending[0]["last_error"]


@pytest.mark.asyncio
async def test_failed_send_does_not_raise(bot, db_path):
    """run_scan posts alerts mid-loop; a raising alert would abort the scan."""
    bot._cached_channel = _channel(AsyncMock(side_effect=ConnectionError("boom")))

    await bot.send_ops_alert("still fine")  # must not raise


@pytest.mark.asyncio
async def test_alert_survives_when_channel_resolution_fails(bot, db_path):
    """Resolving the channel is itself an API call, and it can be what fails."""
    bot._resolve_channel = AsyncMock(side_effect=RuntimeError("no such channel"))

    await bot.send_ops_alert("unresolved submission 4")

    pending = _undelivered(db_path)
    assert len(pending) == 1
    assert pending[0]["message"] == "unresolved submission 4"


# ─── drain_ops_alerts: the retry ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drain_redelivers_a_backlogged_alert(bot, db_path):
    bot._cached_channel = _channel(AsyncMock(side_effect=ConnectionError("down")))
    await bot.send_ops_alert("stuck order 7")
    assert len(_undelivered(db_path)) == 1

    bot._cached_channel = _channel()  # Discord comes back
    delivered = await bot.drain_ops_alerts()

    assert delivered == 1
    bot._cached_channel.send.assert_awaited_once_with("[OPS ALERT] stuck order 7")
    assert _undelivered(db_path) == []


@pytest.mark.asyncio
async def test_drain_replays_backlog_in_order(bot, db_path):
    bot._cached_channel = _channel(AsyncMock(side_effect=ConnectionError("down")))
    await bot.send_ops_alert("first")
    await bot.send_ops_alert("second")

    bot._cached_channel = _channel()
    await bot.drain_ops_alerts()

    sent = [c.args[0] for c in bot._cached_channel.send.await_args_list]
    assert sent == ["[OPS ALERT] first", "[OPS ALERT] second"]


@pytest.mark.asyncio
async def test_drain_stops_at_the_first_still_failing_alert(bot, db_path):
    """Discord being down is not per-alert. Hammering it reorders the backlog."""
    bot._cached_channel = _channel(AsyncMock(side_effect=ConnectionError("down")))
    await bot.send_ops_alert("first")
    await bot.send_ops_alert("second")

    still_down = _channel(AsyncMock(side_effect=ConnectionError("down")))
    bot._cached_channel = still_down
    delivered = await bot.drain_ops_alerts()

    assert delivered == 0
    assert still_down.send.await_count == 1
    assert len(_undelivered(db_path)) == 2


@pytest.mark.asyncio
async def test_drain_with_empty_backlog_touches_no_channel(bot, db_path):
    """An empty outbox must not cost an API round-trip on every scan."""
    bot._resolve_channel = AsyncMock(side_effect=AssertionError("must not resolve"))

    assert await bot.drain_ops_alerts() == 0


@pytest.mark.asyncio
async def test_drain_does_not_redeliver_an_already_delivered_alert(bot, db_path):
    bot._cached_channel = _channel()
    await bot.send_ops_alert("delivered once")

    bot._cached_channel = _channel()
    assert await bot.drain_ops_alerts() == 0
    bot._cached_channel.send.assert_not_awaited()


# ─── Wiring: the outbox is only a fix if something drains it ─────────────────


@pytest.mark.asyncio
async def test_run_scan_drains_the_outbox(config_for_scan):
    """Storage alone does not fix the defect — a backlog needs a retry trigger."""
    from main import run_scan

    bot = AsyncMock()
    with stock_scan_patches([], ValueError("unused")):
        await run_scan(bot, config_for_scan)

    bot.drain_ops_alerts.assert_awaited()


@pytest.mark.asyncio
async def test_run_scan_etf_drains_the_outbox(config_for_scan):
    from main import run_scan_etf

    bot = AsyncMock()
    with etf_scan_patches([], None):
        await run_scan_etf(bot, config_for_scan)

    bot.drain_ops_alerts.assert_awaited()


@pytest.mark.asyncio
async def test_a_failing_drain_does_not_abort_the_scan(config_for_scan):
    """A broken outbox must not take the scan down with it."""
    from main import run_scan

    bot = AsyncMock()
    bot.drain_ops_alerts = AsyncMock(side_effect=RuntimeError("outbox broken"))
    with stock_scan_patches([], ValueError("unused")):
        await run_scan(bot, config_for_scan)  # must not raise
