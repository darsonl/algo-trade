"""One scan at a time, across every scan path.

Lives outside `main.py` for the same reason `risk/resolution.py` does:
`discord_bot.bot` needs to ask whether a scan is running, and `main` already
imports `TradingBot`. Importing the other way would close the cycle.

**One lock for BOTH scans, not one each.** A symbol can appear in the stock
universe and in the ETF universe, so two concurrent scans can reach the same
ticker. `ticker_recommended_today` is a read followed by a much later write with
network awaits in between -- the classic check-then-act race -- and
`idx_active_rec_per_ticker` only turns that race into an IntegrityError rather
than preventing it.

**A scan that arrives while one is running is SKIPPED, never queued.** Running
it afterwards would screen a market that has already moved on, using analyst
quota it has already spent, and would post recommendations timestamped to a
moment that has passed.
"""
from __future__ import annotations

import asyncio
import threading
import weakref

# One lock PER RUNNING LOOP. A module-level `asyncio.Lock` binds to the first
# loop that CONTENDS on it and raises for every loop after -- and acquire()'s
# uncontended fast path hides that from any test which never actually blocks.
# Same trap, same fix, as `kill_switch.submission_gate()` and `approval_gate()`.
_locks: "weakref.WeakKeyDictionary[object, asyncio.Lock]" = weakref.WeakKeyDictionary()
_locks_lock = threading.Lock()


def scan_lock() -> asyncio.Lock:
    """The lock a scan holds for its whole run.

    Raises RuntimeError if called with no running loop, which is a bug at the
    call site rather than something to paper over with a dummy lock.
    """
    loop = asyncio.get_running_loop()
    with _locks_lock:
        lock = _locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            _locks[loop] = lock
        return lock


def scan_in_progress() -> bool:
    """Is a scan running on this loop right now?

    Used to answer a slash command immediately rather than making the operator
    wait for a scan they did not know was running. Checking this and then
    acquiring is NOT a race on one loop: `Lock.acquire()` returns without
    suspending when the lock is free, so nothing can interleave between the two.
    """
    return scan_lock().locked()
