"""/halt and /resume, and who is allowed to use them (round-5 #4, slice 3).

An operator needs a way to reach the switch, and not everyone in the channel
should have it. v1 of the design allowlisted only /halt — which protects the
wrong direction: anyone could then CLEAR a halt during an incident. Both
commands are guarded here.

/halt also has to acquire the submission gate, so that it returns only once no
submission is mid-flight. It persists BEFORE waiting on the gate, so a /halt
that never gets the lock still stops the next submission.
"""
import asyncio
import os
import tempfile
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Config
from database.models import initialize_db
from discord_bot.bot import TradingBot, is_authorized
from risk import kill_switch


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


# ─── The allowlist ───────────────────────────────────────────────────────────


def test_listed_user_is_authorized():
    assert is_authorized(_config("x", allowlist="42,77"), user_id=77) is True


def test_unlisted_user_is_not_authorized():
    assert is_authorized(_config("x", allowlist="42,77"), user_id=99) is False


def test_empty_allowlist_authorizes_nobody():
    """Fail closed: an unset allowlist must not mean "everyone".

    The opposite default would hand /halt and /resume to any channel member the
    moment someone forgot to configure it.
    """
    assert is_authorized(_config("x", allowlist=""), user_id=42) is False


def test_allowlist_tolerates_whitespace_and_blanks():
    assert is_authorized(_config("x", allowlist=" 42 , ,77 "), user_id=42) is True


def test_malformed_allowlist_entry_does_not_authorize_everyone():
    """A typo must fail closed rather than crash open."""
    assert is_authorized(_config("x", allowlist="oops,42"), user_id=42) is True
    assert is_authorized(_config("x", allowlist="oops"), user_id=42) is False


# ─── /halt ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_halt_persists_the_halt(db_path):
    kill_switch.init(db_path, env_default=True)
    bot = _bot(_config(db_path))

    await bot._halt_command(_interaction(), reason="incident")

    assert kill_switch.get_state(db_path) == "HALTED"


@pytest.mark.asyncio
async def test_halt_records_the_discord_user_as_actor(db_path):
    kill_switch.init(db_path, env_default=True)
    bot = _bot(_config(db_path))

    await bot._halt_command(_interaction(user_id=42), reason="bad fills")

    event = kill_switch.get_transitions(db_path)[-1]
    assert "42" in event["actor"]
    assert event["reason"] == "bad fills"


@pytest.mark.asyncio
async def test_unauthorized_user_cannot_halt(db_path):
    kill_switch.init(db_path, env_default=True)
    bot = _bot(_config(db_path, allowlist="42"))

    await bot._halt_command(_interaction(user_id=99), reason="mischief")

    assert kill_switch.get_state(db_path) == "ENABLED"


@pytest.mark.asyncio
async def test_unauthorized_user_cannot_resume(db_path):
    """The direction that actually matters: clearing a halt mid-incident."""
    kill_switch.init(db_path, env_default=False)
    bot = _bot(_config(db_path, allowlist="42"))

    await bot._resume_command(_interaction(user_id=99), reason="whatever")

    assert kill_switch.get_state(db_path) == "HALTED"


@pytest.mark.asyncio
async def test_resume_re_enables_for_an_authorized_user(db_path):
    kill_switch.init(db_path, env_default=False)
    bot = _bot(_config(db_path))

    await bot._resume_command(_interaction(user_id=42), reason="all clear")

    assert kill_switch.get_state(db_path) == "ENABLED"


@pytest.mark.asyncio
async def test_halt_works_when_the_switch_was_never_initialised(db_path):
    """Halting must never depend on startup having gone correctly."""
    bot = _bot(_config(db_path))

    await bot._halt_command(_interaction(), reason="panic")

    assert kill_switch.get_state(db_path) == "HALTED"


@pytest.mark.asyncio
async def test_halt_reports_that_it_cannot_recall_sent_orders(db_path):
    """The honest guarantee is 'no new submissions', not 'nothing outstanding'."""
    kill_switch.init(db_path, env_default=True)
    bot = _bot(_config(db_path))
    interaction = _interaction()

    await bot._halt_command(interaction, reason="incident")

    said = " ".join(
        str(c.args[0]) for c in
        interaction.response.send_message.call_args_list + interaction.followup.send.call_args_list
        if c.args
    )
    assert "already" in said.lower() or "outstanding" in said.lower()


# ─── /halt waits for in-flight submissions ───────────────────────────────────


@pytest.mark.asyncio
async def test_halt_persists_before_waiting_on_the_gate(db_path):
    """A /halt stuck behind a slow broker call must still stop the NEXT one.

    Persisting only after acquiring the gate would leave the switch enabled for
    as long as the in-flight submission takes.
    """
    kill_switch.init(db_path, env_default=True)
    bot = _bot(_config(db_path))
    gate = kill_switch.submission_gate()
    observed: dict = {}

    async def holder():
        async with gate:
            await asyncio.sleep(0.05)
            # While the gate is still held, the halt must already be durable.
            observed["state_during"] = kill_switch.get_state(db_path)

    held = asyncio.create_task(holder())
    await asyncio.sleep(0)  # let the holder take the gate first
    await bot._halt_command(_interaction(), reason="incident")
    await held

    assert observed["state_during"] == "HALTED"


@pytest.mark.asyncio
async def test_halt_returns_only_after_an_in_flight_submission_finishes(db_path):
    kill_switch.init(db_path, env_default=True)
    bot = _bot(_config(db_path))
    gate = kill_switch.submission_gate()
    finished = threading.Event()
    order: list[str] = []

    async def holder():
        async with gate:
            await asyncio.sleep(0.05)
            order.append("submission-done")
            finished.set()

    held = asyncio.create_task(holder())
    await asyncio.sleep(0)
    await bot._halt_command(_interaction(), reason="incident")
    order.append("halt-returned")
    await held

    assert order == ["submission-done", "halt-returned"]
