"""The kill switch: durable, cross-process, fail-closed.

Imports nothing from `schwab_client`, `discord_bot`, or `config`, so both the
preflight guard and the submission sink can depend on it without a cycle.

Three decisions carry the safety weight:

**State lives in the database, not in a module variable.** A halt typed into
the Discord process must stop a scan running in another one, and it must
survive a restart. A module-level cache would make `/halt` local to whichever
process happened to receive it — which is the defect this module exists to fix.

**Unknown means off.** No row, no table, an unreadable database, a value nobody
recognises — every one of those returns False. An unverified state is not
evidence of a safe one, and the seed default is deliberately UNINITIALIZED so
that a code path which forgets `init()` refuses to trade rather than trades.

**The gate is an asyncio.Lock.** The spec proposed a `threading.RLock`; an
RLock is reentrant per thread and every coroutine shares the loop thread, so
`/halt` would acquire the "same gate" as an in-flight submission and walk
straight into the critical section. `tests/test_kill_switch_gate.py` runs both
primitives and shows the difference.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import weakref

from database.models import get_cursor

logger = logging.getLogger(__name__)

UNINITIALIZED = "UNINITIALIZED"
ENABLED = "ENABLED"
HALTED = "HALTED"


class TradingHalted(RuntimeError):
    """Raised when a submission is refused because trading is not enabled.

    A distinct type because the caller must tell the operator something
    different from a broker failure: nothing was dispatched, so "verify in
    Schwab before retrying" would be actively misleading.
    """

# ENABLED is deliberately the only member. Anything not on this list — including
# a state added later and not thought through — reads as "not enabled".
_ENABLED_STATES = frozenset({ENABLED})

# One lock PER EVENT LOOP, not one per process. A module-level asyncio.Lock
# binds itself to the first loop that contends on it and raises
# RuntimeError('bound to a different event loop') for every loop after that —
# and only under contention, since acquire() has a fast path that never
# consults the loop when the lock is free. So the bug hides from every
# uncontended test and surfaces inside the submission path instead.
#
# Weak keys so a finished loop does not keep its gate alive. The threading lock
# guards the dict only: loops can live on different threads, and there is no
# await inside the critical section.
_gates: "weakref.WeakKeyDictionary[object, asyncio.Lock]" = weakref.WeakKeyDictionary()
_gates_lock = threading.Lock()


def submission_gate() -> asyncio.Lock:
    """The lock spanning the final enabled-check through broker dispatch.

    /halt acquires the same lock, so it returns only once no submission is
    mid-flight. Held across `await`, which is safe precisely because this is an
    asyncio primitive: waiting on it suspends the waiter instead of freezing
    the loop the holder needs in order to finish.

    Process-local by nature — a lock cannot span processes, and this one cannot
    even span loops. That is not a gap being papered over: cross-process safety
    comes from the durable state, which every submission re-reads inside this
    gate. The lock only closes the interleaving window between coroutines on
    one loop.

    Raises RuntimeError if called with no running loop, which is a bug at the
    call site rather than something to paper over with a dummy lock.
    """
    loop = asyncio.get_running_loop()
    with _gates_lock:
        gate = _gates.get(loop)
        if gate is None:
            gate = asyncio.Lock()
            _gates[loop] = gate
        return gate


def get_state(db_path: str) -> str:
    """The persisted state, or UNINITIALIZED if it cannot be established.

    Every failure mode collapses to UNINITIALIZED rather than raising: callers
    are safety checks, and a check that explodes is a check that gets wrapped
    in a try/except and swallowed somewhere upstream.
    """
    try:
        with get_cursor(db_path) as conn:
            row = conn.execute(
                "SELECT state FROM kill_switch WHERE id = 1"
            ).fetchone()
    except sqlite3.Error as exc:
        logger.error("Kill switch unreadable (%s) — treating as UNINITIALIZED", exc)
        return UNINITIALIZED

    if row is None:
        return UNINITIALIZED
    return row["state"]


def is_enabled(db_path: str) -> bool:
    """True only when durable state says ENABLED.

    Reads the database on every call, on purpose. The cost is a local SQLite
    read; the benefit is that a halt performed anywhere is honoured everywhere,
    which a value cached at init() could not do.
    """
    state = get_state(db_path)
    if state not in _ENABLED_STATES and state not in (UNINITIALIZED, HALTED):
        logger.error("Kill switch holds unrecognised state %r — refusing to trade", state)
    return state in _ENABLED_STATES


def _transition(db_path: str, new_state: str, actor: str, reason: str) -> None:
    """Persist a new state and append the audit row, in one transaction."""
    with get_cursor(db_path) as conn:
        row = conn.execute(
            "SELECT state FROM kill_switch WHERE id = 1"
        ).fetchone()
        previous = row["state"] if row else UNINITIALIZED

        conn.execute(
            """INSERT INTO kill_switch (id, state, actor, reason, updated_at)
                    VALUES (1, ?, ?, ?, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                    state = excluded.state, actor = excluded.actor,
                    reason = excluded.reason, updated_at = excluded.updated_at""",
            (new_state, actor, reason),
        )
        conn.execute(
            """INSERT INTO kill_switch_events
                    (previous_state, new_state, actor, reason)
               VALUES (?, ?, ?, ?)""",
            (previous, new_state, actor, reason),
        )
    logger.warning(
        "Kill switch %s -> %s by %s (%s)", previous, new_state, actor, reason
    )


def init(db_path: str, env_default: bool) -> str:
    """Seed the switch on first ever run; never override persisted state.

    The env value is the seed for a database that has never been written, not a
    setting applied at every startup. An operator halts during an incident, the
    process restarts, and `TRADING_ENABLED=true` must NOT quietly turn trading
    back on — a kill switch a restart can clear is not a kill switch.

    Returns the state in force after the call.
    """
    current = get_state(db_path)
    if current != UNINITIALIZED:
        return current

    seeded = ENABLED if env_default else HALTED
    _transition(db_path, seeded, actor="init", reason="seeded from environment")
    return seeded


def halt(db_path: str, actor: str, reason: str) -> None:
    """Stop new submissions, durably.

    Deliberately does not require init() to have run: halting must never depend
    on setup having happened correctly. It also cannot recall an order the
    broker has already accepted — the honest guarantee is "no new submissions
    after this returns", not "nothing is outstanding".
    """
    _transition(db_path, HALTED, actor, reason)


def resume(db_path: str, actor: str, reason: str) -> None:
    """Allow submissions again. Subject to the same authorization allowlist as
    /halt — a switch anyone can clear protects nothing."""
    _transition(db_path, ENABLED, actor, reason)


def require_enabled(config) -> None:
    """Raise TradingHalted unless durable state says trading is on.

    The sink's guard. Takes the config rather than a db_path so a call site
    cannot satisfy it by passing some other database, and refuses outright when
    the config carries no db_path: with no durable state there is nothing to
    verify, and an unverifiable switch is not an open one.
    """
    db_path = getattr(config, "db_path", None)
    if not db_path:
        raise TradingHalted(
            "order submission blocked: config has no db_path, so the kill "
            "switch cannot be verified"
        )
    if not is_enabled(db_path):
        raise TradingHalted(
            f"order submission blocked: trading is {get_state(db_path)} "
            "(/resume re-enables it)"
        )


def get_transitions(db_path: str) -> list[dict]:
    """Every state change ever recorded, oldest first."""
    with get_cursor(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM kill_switch_events ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]
