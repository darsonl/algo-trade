"""Durable, cross-process kill switch (round-5 #4).

Two properties matter more than the API:

1. **It fails closed.** Before init, on an unreadable state, on a value nobody
   recognises — trading is off. An unverified state is not evidence of a safe
   one (the recurring defect shape: absence of data is not data).
2. **It is durable and shared.** A halt must survive a restart, and must be
   seen by a process that did not perform it. A kill switch a crash can clear
   is not a kill switch, and one that only halts the process you typed into is
   not one either.
"""
import asyncio
import os
import sqlite3
import tempfile

import pytest

from database.models import get_cursor, initialize_db
from risk import kill_switch


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    initialize_db(path)
    return path


# ─── Fails closed ────────────────────────────────────────────────────────────


def test_uninitialized_database_is_not_enabled(db_path):
    """A code path that forgets init() must refuse to trade, not trade."""
    assert kill_switch.get_state(db_path) == "UNINITIALIZED"
    assert kill_switch.is_enabled(db_path) is False


def test_unrecognised_persisted_state_is_not_enabled(db_path):
    """Defensive parsing must not turn a corrupt row into a confident 'go'."""
    kill_switch.init(db_path, env_default=True)
    with get_cursor(db_path) as conn:
        conn.execute("UPDATE kill_switch SET state = 'BANANA' WHERE id = 1")

    assert kill_switch.is_enabled(db_path) is False


def test_missing_table_is_not_enabled(db_path):
    """A database that predates the switch must not read as enabled."""
    with get_cursor(db_path) as conn:
        conn.execute("DROP TABLE kill_switch")

    assert kill_switch.is_enabled(db_path) is False


# ─── init seeds once, then never overrides ───────────────────────────────────


def test_init_seeds_enabled_from_env_on_a_fresh_database(db_path):
    kill_switch.init(db_path, env_default=True)

    assert kill_switch.get_state(db_path) == "ENABLED"
    assert kill_switch.is_enabled(db_path) is True


def test_init_seeds_halted_when_env_default_is_false(db_path):
    kill_switch.init(db_path, env_default=False)

    assert kill_switch.get_state(db_path) == "HALTED"
    assert kill_switch.is_enabled(db_path) is False


def test_a_halt_survives_restart_even_when_env_says_enabled(db_path):
    """The defect this fixes: a restart silently re-enabled trading.

    The env value seeds a database that has never been written. It must never
    override a halt an operator actually performed.
    """
    kill_switch.init(db_path, env_default=True)
    kill_switch.halt(db_path, actor="operator", reason="incident")

    kill_switch.init(db_path, env_default=True)  # process restarts

    assert kill_switch.get_state(db_path) == "HALTED"
    assert kill_switch.is_enabled(db_path) is False


# ─── Cross-process: the actual point of #4 ───────────────────────────────────


def test_a_halt_written_by_another_process_is_honoured(db_path):
    """Simulates /halt run in a second process: a bare connection, no module state.

    is_enabled must consult durable state rather than a value cached at init,
    or the halt stops only the process it was typed into.
    """
    kill_switch.init(db_path, env_default=True)
    assert kill_switch.is_enabled(db_path) is True

    other = sqlite3.connect(db_path)
    try:
        other.execute("UPDATE kill_switch SET state = 'HALTED' WHERE id = 1")
        other.commit()
    finally:
        other.close()

    assert kill_switch.is_enabled(db_path) is False


def test_a_resume_by_another_process_is_honoured(db_path):
    kill_switch.init(db_path, env_default=False)
    assert kill_switch.is_enabled(db_path) is False

    other = sqlite3.connect(db_path)
    try:
        other.execute("UPDATE kill_switch SET state = 'ENABLED' WHERE id = 1")
        other.commit()
    finally:
        other.close()

    assert kill_switch.is_enabled(db_path) is True


# ─── halt / resume ───────────────────────────────────────────────────────────


def test_halt_then_resume_round_trips(db_path):
    kill_switch.init(db_path, env_default=True)

    kill_switch.halt(db_path, actor="alice", reason="bad fills")
    assert kill_switch.is_enabled(db_path) is False

    kill_switch.resume(db_path, actor="alice", reason="all clear")
    assert kill_switch.is_enabled(db_path) is True


def test_halt_works_even_if_init_was_never_called(db_path):
    """Halting must never depend on setup having happened correctly."""
    kill_switch.halt(db_path, actor="operator", reason="panic")

    assert kill_switch.get_state(db_path) == "HALTED"
    assert kill_switch.is_enabled(db_path) is False


# ─── Audit trail ─────────────────────────────────────────────────────────────


def test_transitions_are_recorded_with_actor_and_reason(db_path):
    kill_switch.init(db_path, env_default=True)
    kill_switch.halt(db_path, actor="alice", reason="bad fills")

    events = kill_switch.get_transitions(db_path)

    assert events[-1]["previous_state"] == "ENABLED"
    assert events[-1]["new_state"] == "HALTED"
    assert events[-1]["actor"] == "alice"
    assert events[-1]["reason"] == "bad fills"


def test_the_audit_log_is_append_only_across_transitions(db_path):
    kill_switch.init(db_path, env_default=True)
    kill_switch.halt(db_path, actor="alice", reason="one")
    kill_switch.resume(db_path, actor="bob", reason="two")
    kill_switch.halt(db_path, actor="carol", reason="three")

    events = kill_switch.get_transitions(db_path)

    assert [e["actor"] for e in events][-3:] == ["alice", "bob", "carol"]
    assert [e["new_state"] for e in events][-3:] == ["HALTED", "ENABLED", "HALTED"]


def test_seeding_is_itself_audited(db_path):
    """How trading came to be on at all is part of the record."""
    kill_switch.init(db_path, env_default=True)

    events = kill_switch.get_transitions(db_path)
    assert events[0]["previous_state"] == "UNINITIALIZED"
    assert events[0]["new_state"] == "ENABLED"


def test_repeated_init_does_not_append_further_events(db_path):
    kill_switch.init(db_path, env_default=True)
    kill_switch.init(db_path, env_default=True)

    assert len(kill_switch.get_transitions(db_path)) == 1


# ─── The gate ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submission_gate_is_an_asyncio_lock():
    """Not a threading.RLock — see test_kill_switch_gate.py for why."""
    assert isinstance(kill_switch.submission_gate(), asyncio.Lock)


@pytest.mark.asyncio
async def test_submission_gate_is_the_same_object_within_one_loop():
    """A fresh lock per call would exclude nothing."""
    assert kill_switch.submission_gate() is kill_switch.submission_gate()


def test_submission_gate_requires_a_running_loop():
    """Calling it outside async context is a call-site bug, not a silent no-op.

    Returning a dummy lock instead would hand back something that excludes
    nothing while looking like a gate.
    """
    with pytest.raises(RuntimeError):
        kill_switch.submission_gate()


async def _contended_use_of_the_gate():
    """Acquire the gate with a second task queued behind it.

    Contention is the point. asyncio.Lock.acquire() returns on a fast path when
    the lock is free and never consults the running loop, so an uncontended
    acquire proves nothing about loop binding — only a waiter forces the lock
    to create a future and bind itself.
    """
    gate = kill_switch.submission_gate()

    async def waiter():
        async with gate:
            return True

    async with gate:
        queued = asyncio.create_task(waiter())
        await asyncio.sleep(0)  # let it register as a waiter
    return await queued


def test_gate_survives_contention_in_two_different_event_loops():
    """A module-level asyncio.Lock binds to the first loop that contends on it.

    Every later loop then raises RuntimeError('... is bound to a different
    event loop') — and only under contention, which is exactly when the gate
    matters. That would surface as a crash in the submission path in
    production while every uncontended test stayed green.
    """
    assert asyncio.run(_contended_use_of_the_gate()) is True
    assert asyncio.run(_contended_use_of_the_gate()) is True
