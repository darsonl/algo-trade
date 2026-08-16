"""The kill switch reaches the submission path (round-5 #4, slice 2).

Slice 1 built durable state and the gate primitive; on its own that is a switch
wired to nothing. Round-4 C1 was exactly this defect in an earlier design — the
spec claimed "the sink re-reads it" while no term for it existed in the
predicate, so /halt during a pending approval was cosmetic.

Two layers, deliberately redundant:

* the **sink** in schwab_client.orders — the single choke point every order
  dispatch passes through, so a caller that forgets the gate still fails closed;
* the **gate** in the approval path — held from the final check through the
  broker call, so /halt cannot land in an await boundary between them.
"""
import asyncio
import os
import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Config
from database.models import initialize_db
from database.queries import create_position, create_recommendation
from discord_bot.bot import ApproveRejectView, SellApproveRejectView
from risk import kill_switch
from risk.kill_switch import TradingHalted
from risk.preflight import BrokerSnapshot
from schwab_client import orders
from schwab_client.quotes import Quote


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    initialize_db(path)
    return path


def _config(db_path, dry_run=False):
    c = Config()
    c.db_path = db_path
    c.dry_run = dry_run
    c.allowed_discord_user_ids = "1001"
    c.discord_guild_id = 0
    c.discord_channel_id = 0
    c.max_daily_notional_usd = 20000.0
    c.approval_price_tolerance_pct = 2.0
    c.approval_slippage_buffer_pct = 0.5
    c.max_position_size_usd = 500.0
    c.max_portfolio_usd = 20000.0
    c.schwab_account_hash = "hash"
    return c


# ─── The sink ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("place", ["place_order", "place_limit_order", "place_sell_order"])
def test_sink_refuses_every_order_type_when_halted(db_path, place):
    kill_switch.init(db_path, env_default=True)
    kill_switch.halt(db_path, actor="operator", reason="incident")
    cfg = _config(db_path)
    args = (("AAPL", 1, 100.0, cfg) if place == "place_limit_order"
            else ("AAPL", 1, cfg))

    with pytest.raises(TradingHalted):
        getattr(orders, place)(*args, client=MagicMock())


def test_sink_refuses_when_the_switch_was_never_initialised(db_path):
    """Fail closed: a forgotten init() must not read as permission to trade."""
    cfg = _config(db_path)

    with pytest.raises(TradingHalted):
        orders.place_order("AAPL", 1, cfg, client=MagicMock())


def test_sink_refuses_when_config_carries_no_db_path(db_path):
    """Without durable state there is nothing to verify, so refuse."""
    cfg = _config(db_path)
    cfg.db_path = None

    with pytest.raises(TradingHalted):
        orders.place_order("AAPL", 1, cfg, client=MagicMock())


def test_sink_dispatches_when_enabled(db_path):
    kill_switch.init(db_path, env_default=True)
    client = MagicMock()
    client.place_order.return_value = MagicMock(
        headers={"Location": "https://api/orders/12345"}
    )

    order_id = orders.place_order("AAPL", 1, _config(db_path), client=client)

    assert order_id == "12345"
    client.place_order.assert_called_once()


def test_sink_never_reaches_the_broker_when_halted(db_path):
    """The point is that no HTTP happens, not merely that we raise afterwards."""
    kill_switch.init(db_path, env_default=False)
    client = MagicMock()

    with pytest.raises(TradingHalted):
        orders.place_order("AAPL", 1, _config(db_path), client=client)

    client.place_order.assert_not_called()


# ─── The approval path ───────────────────────────────────────────────────────


def _interaction():
    i = MagicMock()
    i.user.id = 1001
    i.guild_id = 0
    i.channel_id = 0
    i.response.send_message = AsyncMock()
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    return i


async def _approve(view, interaction):
    await view.approve.callback.callback(view, interaction, MagicMock())


_NOW = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)   # 11:00 ET Mon


def _buy_env():
    """Patches the rewired buy path needs: a quote, a readable book, a clock.

    The recommendation row is created for real by each test, so the claim and
    the reservation exercise the actual transaction rather than a mock.
    """
    quote = Quote(symbol="AAPL", bid=99.5, ask=100.0, last=100.0,
                  quote_time=_NOW - timedelta(seconds=1))
    return (
        patch("discord_bot.bot.fetch_quote", return_value=quote),
        patch("discord_bot.bot.collect_broker_snapshot",
              return_value=BrokerSnapshot([], [])),
        patch("discord_bot.bot._utcnow", return_value=_NOW),
        patch("schwab_client.auth.get_client", return_value=MagicMock()),
    )


def _live_recommendation(db_path, ticker="AAPL", price=100.0) -> int:
    rec_id = create_recommendation(db_path, ticker, "BUY", "t", price, None, None)
    expires = (_NOW + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE recommendations SET expires_at = ? WHERE id = ?",
                     (expires, rec_id))
    return rec_id


def _rec_status(db_path, rec_id):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT status FROM recommendations WHERE id = ?",
                            (rec_id,)).fetchone()[0]


@pytest.mark.asyncio
async def test_halted_buy_approval_does_not_place_an_order(db_path):
    kill_switch.init(db_path, env_default=True)
    kill_switch.halt(db_path, actor="operator", reason="incident")
    config = _config(db_path)
    rec_id = _live_recommendation(db_path)
    view = ApproveRejectView(rec_id, "AAPL", 100.0, config)

    patches = _buy_env() + (patch("discord_bot.bot._call_place_order"),)
    for pp in patches:
        pp.start()
    try:
        await _approve(view, _interaction())
    finally:
        for pp in patches:
            pp.stop()
    # Guard 2 refuses before the reservation, so no order row exists at all.
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_halted_buy_approval_reopens_the_recommendation(db_path):
    """Nothing was submitted, so the click must be retryable after /resume."""
    kill_switch.init(db_path, env_default=False)
    config = _config(db_path)
    rec_id = _live_recommendation(db_path)
    view = ApproveRejectView(rec_id, "AAPL", 100.0, config)

    patches = _buy_env()
    for pp in patches:
        pp.start()
    try:
        await _approve(view, _interaction())
    finally:
        for pp in patches:
            pp.stop()

    assert _rec_status(db_path, rec_id) == "pending"


@pytest.mark.asyncio
async def test_halted_buy_approval_says_halted_not_check_schwab(db_path):
    """The generic failure text tells the operator to verify at the broker.

    That is actively misleading here: no request was ever dispatched.
    """
    kill_switch.init(db_path, env_default=False)
    config = _config(db_path)
    rec_id = _live_recommendation(db_path)
    view = ApproveRejectView(rec_id, "AAPL", 100.0, config)
    interaction = _interaction()

    patches = _buy_env()
    for pp in patches:
        pp.start()
    try:
        await _approve(view, interaction)
    finally:
        for pp in patches:
            pp.stop()

    sent = " ".join(
        str(c.args[0]) for c in interaction.followup.send.call_args_list if c.args
    )
    assert "halt" in sent.lower()
    assert "verify in schwab" not in sent.lower()


@pytest.mark.asyncio
async def test_enabled_buy_approval_places_the_order(db_path):
    """The guard must not block the ordinary case."""
    kill_switch.init(db_path, env_default=True)
    config = _config(db_path)
    rec_id = _live_recommendation(db_path)
    view = ApproveRejectView(rec_id, "AAPL", 100.0, config)

    submitted = []

    def _place(client, cfg, spec):
        submitted.append(spec)
        return MagicMock(status_code=201,
                         headers={"Location": "https://x/orders/oid-1"})

    patches = _buy_env() + (
        patch("discord_bot.bot._call_place_order", side_effect=_place),
    )
    for pp in patches:
        pp.start()
    try:
        await _approve(view, _interaction())
    finally:
        for pp in patches:
            pp.stop()

    assert len(submitted) == 1


@pytest.mark.asyncio
async def test_halted_sell_approval_does_not_place_an_order(db_path):
    kill_switch.init(db_path, env_default=False)
    config = _config(db_path)
    rec_id = _live_recommendation(db_path)
    create_position(db_path, "AAPL", 3, 90.0)
    view = SellApproveRejectView(rec_id, "AAPL", 3, 100.0, config)

    patches = _buy_env() + (patch("discord_bot.bot._call_place_order"),)
    started = [pp.start() for pp in patches]
    try:
        await view.approve.callback.callback(view, _interaction(), MagicMock())
    finally:
        for pp in patches:
            pp.stop()

    started[-1].assert_not_called()


# ─── The gate actually spans the dispatch ────────────────────────────────────


@pytest.mark.asyncio
async def test_the_gate_is_held_across_the_broker_call(db_path):
    """/halt must not be able to land between the check and the dispatch.

    Asserts the lock is genuinely held while the broker call is in flight —
    v3 had the re-read but not the gate, so /halt could slip into one of the
    await boundaries, reply "halted", and let the worker submit anyway.
    """
    kill_switch.init(db_path, env_default=True)
    config = _config(db_path)
    rec_id = _live_recommendation(db_path)
    view = ApproveRejectView(rec_id, "AAPL", 100.0, config)
    in_broker = threading.Event()
    may_return = threading.Event()

    def slow_place(*_args, **_kwargs):
        in_broker.set()
        may_return.wait(5)
        return MagicMock(status_code=201, headers={"Location": "https://x/orders/oid-1"})

    patches = _buy_env() + (
        patch("discord_bot.bot._call_place_order", side_effect=slow_place),
    )
    for pp in patches:
        pp.start()
    try:
        task = asyncio.create_task(_approve(view, _interaction()))

        await asyncio.to_thread(in_broker.wait, 5)
        gate = kill_switch.submission_gate()
        held_during_dispatch = gate.locked()

        may_return.set()
        await task
    finally:
        for pp in patches:
            pp.stop()

    assert held_during_dispatch is True
    assert gate.locked() is False
