"""The buy approval path, wired onto the guards and the ledger (spec v4 §7/§9).

Before this, approving a buy ran two ad-hoc checks and then recorded a
*position* directly. The order ledger existed and was tested but nothing wrote
to it, so no ceiling was actually enforced.

The sequence now is:

    check_authorization        pure, BEFORE defer -> ephemeral reject
    defer
    ── approval gate ──        one lock, whole read->evaluate->claim->submit
      quote, broker snapshot   network, off the event loop
      BEGIN IMMEDIATE
        read day_notional + blocking orders
        evaluate_trade
        claim the recommendation (expiry in the SQL predicate)
        INSERT the order row   <- the reservation IS the row
      COMMIT
      submit once, classify the outcome
    ── release ──

The reservation being the row is what makes the ceiling global: `BEGIN
IMMEDIATE` takes the write lock before the reads, so two processes serialise
instead of both reading the same stale total (round-4 finding 8).
"""
import asyncio
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Config
from database.models import initialize_db
from database import queries
from database.queries import create_recommendation
from discord_bot.bot import ApproveRejectView, approval_gate
from risk import kill_switch
from risk.preflight import BrokerSnapshot
from schwab_client.quotes import Quote

NOW = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)  # 11:00 ET Mon
APPROVER = 1001


def _config(dry_run=False, **overrides):
    db_path = os.path.join(tempfile.mkdtemp(), "approval.db")
    initialize_db(db_path)
    kill_switch.init(db_path, env_default=True)
    c = Config()
    c.db_path = db_path
    c.dry_run = dry_run
    c.allowed_discord_user_ids = str(APPROVER)
    c.discord_guild_id = 0
    c.discord_channel_id = 0
    c.max_position_size_usd = 500.0
    c.max_portfolio_usd = 20000.0
    c.max_daily_notional_usd = 2000.0
    c.approval_price_tolerance_pct = 2.0
    c.approval_slippage_buffer_pct = 0.5
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _recommendation(config, ticker="AAPL", price=100.0, hours=6):
    expires = (NOW + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    rec_id = create_recommendation(
        config.db_path, ticker=ticker, signal="BUY", reasoning="test",
        price=price, dividend_yield=None, pe_ratio=None,
    )
    with sqlite3.connect(config.db_path) as conn:
        conn.execute("UPDATE recommendations SET expires_at = ? WHERE id = ?", (expires, rec_id))
    return rec_id


def _interaction(user_id=APPROVER):
    it = MagicMock()
    it.user.id = user_id
    it.guild_id = 0
    it.channel_id = 0
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.followup.send = AsyncMock()
    return it


def _quote(ask=100.0, bid=99.5):
    return Quote(symbol="AAPL", bid=bid, ask=ask, last=ask,
                 quote_time=NOW - timedelta(seconds=1))


class _Submitted:
    """A 201 carrying a Location header."""
    status_code = 201
    headers = {"Location": "https://api.schwab.com/orders/BRK-77"}


def _orders(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY id")]


def _rec_status(db_path, rec_id):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT status FROM recommendations WHERE id = ?", (rec_id,)
        ).fetchone()[0]


async def _approve(view, interaction, *, quote=_quote(), snapshot=None,
                   submit=None, submit_error=None, now=NOW):
    snapshot = snapshot if snapshot is not None else BrokerSnapshot([], [])

    def _place(client, config, spec):
        if submit_error:
            raise submit_error
        return submit if submit is not None else _Submitted()

    with patch("discord_bot.bot.fetch_quote", return_value=quote), \
         patch("discord_bot.bot.collect_broker_snapshot", return_value=snapshot), \
         patch("discord_bot.bot._utcnow", return_value=now), \
         patch("discord_bot.bot._call_place_order", side_effect=_place), \
         patch("schwab_client.auth.get_client", return_value=MagicMock()):
        await view.approve.callback.callback(view, interaction, MagicMock())


def _view(config, rec_id, ticker="AAPL", price=100.0):
    return ApproveRejectView(rec_id=rec_id, ticker=ticker, price=price, config=config)


# --- the order row is the reservation ---

@pytest.mark.asyncio
async def test_an_approved_buy_writes_an_order_row():
    config = _config()
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    rows = _orders(config.db_path)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["side"] == "buy"


@pytest.mark.asyncio
async def test_the_order_row_records_the_broker_id_on_acceptance():
    config = _config()
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    row = _orders(config.db_path)[0]
    assert row["status"] == "submitted"
    assert row["broker_order_id"] == "BRK-77"


@pytest.mark.asyncio
async def test_a_blocked_approval_writes_no_order_row():
    """The transaction rolls back, so a rejection leaves no reservation."""
    config = _config(max_daily_notional_usd=1.0)
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    assert _orders(config.db_path) == []


@pytest.mark.asyncio
async def test_a_blocked_approval_leaves_the_recommendation_open():
    config = _config(max_daily_notional_usd=1.0)
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    assert _rec_status(config.db_path, rec_id) == "pending"


@pytest.mark.asyncio
async def test_the_ceiling_is_enforced_across_two_approvals():
    """The headline: the second buy is refused because the first one's ROW is
    still holding the capital. Nothing enforced this before."""
    config = _config(max_daily_notional_usd=600.0)   # one 4x$100.50 order fits
    first, second = _recommendation(config), _recommendation(config, ticker="MSFT")

    await _approve(_view(config, first), _interaction())
    await _approve(_view(config, second, ticker="MSFT"), _interaction())

    rows = _orders(config.db_path)
    assert len(rows) == 1, "the second order must not have been reserved"
    assert _rec_status(config.db_path, second) == "pending"


# --- outcome classification reaches the ledger ---

@pytest.mark.asyncio
async def test_an_ambiguous_submission_leaves_the_row_unknown():
    config = _config()
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction(),
                   submit_error=TimeoutError("read timed out"))

    assert _orders(config.db_path)[0]["status"] == "submit_unknown"


@pytest.mark.asyncio
async def test_an_ambiguous_submission_does_NOT_reopen_the_recommendation():
    """v1 reopened it, inviting a second human approval and a second real
    order for something that may already exist."""
    config = _config()
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction(),
                   submit_error=TimeoutError("read timed out"))

    assert _rec_status(config.db_path, rec_id) == "approved"


@pytest.mark.asyncio
async def test_a_definitive_refusal_marks_the_row_failed_and_reopens():
    """Nothing was placed, so the capital is released and a human may retry."""
    config = _config()
    rec_id = _recommendation(config)
    error = RuntimeError("HTTP 400")
    error.response = MagicMock(status_code=400)
    await _approve(_view(config, rec_id), _interaction(), submit_error=error)

    assert _orders(config.db_path)[0]["status"] == "submit_failed"
    assert _rec_status(config.db_path, rec_id) == "pending"


@pytest.mark.asyncio
async def test_an_unknown_order_blocks_the_next_buy_of_that_ticker():
    """Guard 11, end to end: the reservation and the block both survive.

    The first recommendation is retired before the second is written, so the
    `idx_active_rec_per_ticker` partial unique index is NOT what does the
    blocking here. Guard 11 must refuse on the strength of the unresolved ORDER
    alone -- which is the case that matters, because an order can outlive its
    recommendation (an operator retires one, or the order was never tied to a
    recommendation at all).
    """
    config = _config()
    first = _recommendation(config)
    await _approve(_view(config, first), _interaction(),
                   submit_error=TimeoutError("boom"))

    queries.update_recommendation_status(config.db_path, first, "completed")
    second = _recommendation(config)
    await _approve(_view(config, second), _interaction())

    assert len(_orders(config.db_path)) == 1
    assert _rec_status(config.db_path, second) == "pending"


# --- guards actually run ---

@pytest.mark.asyncio
async def test_an_unauthorized_click_is_refused_before_defer():
    """Pure and instant, so it gets a private reply and never defers."""
    config = _config()
    rec_id = _recommendation(config)
    interaction = _interaction(user_id=999)
    await _approve(_view(config, rec_id), interaction)

    interaction.response.defer.assert_not_called()
    interaction.response.send_message.assert_awaited()
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True
    assert _orders(config.db_path) == []


@pytest.mark.asyncio
async def test_a_broker_outage_refuses_rather_than_trading_blind():
    config = _config()
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction(),
                   snapshot=BrokerSnapshot(positions=None, working_orders=None))

    assert _orders(config.db_path) == []
    assert _rec_status(config.db_path, rec_id) == "pending"


@pytest.mark.asyncio
async def test_an_unavailable_quote_refuses():
    config = _config()
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction(), quote=None)

    assert _orders(config.db_path) == []


@pytest.mark.asyncio
async def test_an_expired_recommendation_is_refused():
    config = _config()
    rec_id = _recommendation(config, hours=-1)
    await _approve(_view(config, rec_id), _interaction())

    assert _orders(config.db_path) == []


@pytest.mark.asyncio
async def test_a_halted_switch_refuses_and_writes_no_row():
    config = _config()
    kill_switch.halt(config.db_path, actor="test", reason="test")
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    assert _orders(config.db_path) == []
    assert _rec_status(config.db_path, rec_id) == "pending"


# --- dry run ---

@pytest.mark.asyncio
async def test_dry_run_still_runs_the_guards():
    config = _config(dry_run=True, max_daily_notional_usd=1.0)
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    assert _rec_status(config.db_path, rec_id) == "pending"


@pytest.mark.asyncio
async def test_dry_run_writes_no_order_row():
    """A simulated order must not reserve real capital against the ceiling."""
    config = _config(dry_run=True)
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    assert _orders(config.db_path) == []
    assert _rec_status(config.db_path, rec_id) == "approved"


# --- the approval gate ---

def test_the_approval_gate_survives_a_second_event_loop():
    """A module-level asyncio.Lock binds to the first loop that CONTENDS on it
    and raises for every loop after -- and acquire()'s uncontended fast path
    hides that from any test which never actually blocks. So this CONTENDS, on
    two separate loops. Synchronous on purpose: asyncio.run needs no loop
    already running. Same trap as the submission gate (test_kill_switch.py)."""
    assert asyncio.run(_contended_use_of_the_gate()) is True
    assert asyncio.run(_contended_use_of_the_gate()) is True


async def _contended_use_of_the_gate() -> bool:
    gate = approval_gate()

    async def hold():
        async with gate:
            await asyncio.sleep(0)

    await asyncio.gather(hold(), hold())     # real contention, not a fast path
    assert approval_gate() is gate           # same loop -> same lock
    return True


def test_each_loop_gets_its_own_gate():
    gates = []
    asyncio.run(_record_gate(gates))
    asyncio.run(_record_gate(gates))
    assert gates[0] is not gates[1]


async def _record_gate(sink):
    sink.append(approval_gate())


# --- the ledger owns ceilings; positions are still recorded ---

def _positions(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM positions")]


def _trades(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM trades")]


@pytest.mark.asyncio
async def test_a_submitted_buy_still_records_the_position():
    """The sell pass, /positions, /stats and the duplicate check all read the
    positions table. The ledger owns the ceilings, not the position book."""
    config = _config()
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    assert [p["ticker"] for p in _positions(config.db_path)] == ["AAPL"]
    assert _trades(config.db_path)[0]["order_id"] == "BRK-77"


@pytest.mark.asyncio
async def test_an_unknown_outcome_records_no_position():
    """We do not know that it filled, or even that it exists."""
    config = _config()
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction(),
                   submit_error=TimeoutError("boom"))

    assert _positions(config.db_path) == []


@pytest.mark.asyncio
async def test_a_refused_buy_records_no_position():
    config = _config(max_daily_notional_usd=1.0)
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    assert _positions(config.db_path) == []
    assert _trades(config.db_path) == []


@pytest.mark.asyncio
async def test_a_halt_stops_dry_run_approvals_too():
    """Deliberate change. The old path wrapped the kill-switch check in
    `if not dry_run`, so a halted bot still 'approved' simulated buys. A dry
    run that skips the guards rehearses nothing — and the whole point of dry
    run in a system like this is to be a faithful rehearsal of the live path."""
    config = _config(dry_run=True)
    kill_switch.halt(config.db_path, actor="test", reason="incident")
    rec_id = _recommendation(config)
    await _approve(_view(config, rec_id), _interaction())

    assert _rec_status(config.db_path, rec_id) == "pending"
    assert _positions(config.db_path) == []
