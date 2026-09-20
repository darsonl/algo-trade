"""A deadman that ends a bot process which outlived its own deadline.

See `tests/test_stuck_process_watchdog.py` for the incident this exists for and
for why a startup check cannot serve the same purpose on this host.

The pure half (`watchdog_deadline`, `is_overdue`) has no clock, no thread and no
I/O, so it is tested without mocks -- the same split as `risk/preflight.py`.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

logger = logging.getLogger(__name__)


def watchdog_deadline(close_utc: datetime, window_min: int, grace_min: int) -> datetime:
    """The last instant at which this process may legitimately still be alive.

    NOT the close alone. `main.py`'s `on_ready` re-arms the post-scan exit at
    `last_scan + post_scan_window_min` when it starts inside the approval
    window, and with a late enough last scan that lands after the bell. Adding
    the window makes this deadline dominate every legitimate exit, which is what
    lets the watchdog fire without ever weighing a judgement call: past this
    instant, staying up is not a choice the bot was given.

    `grace_min` absorbs a slow `bot.close()` and the tick interval, so a healthy
    shutdown in progress is never mistaken for a stuck one.
    """
    if close_utc.tzinfo is None:
        raise ValueError(
            f"close_utc must be timezone-aware, got naive {close_utc!r} -- "
            "assuming UTC on this Asia/Taipei host would misplace the deadline "
            "by hours and surface as a bot killed mid-session."
        )
    return close_utc + timedelta(minutes=window_min + grace_min)


@dataclass(frozen=True)
class WatchdogHandle:
    """The running watchdog: its thread, and the event that ends it."""

    thread: threading.Thread
    _stop: threading.Event

    def stop(self) -> None:
        """Ask the watchdog to end. Returns immediately; join the thread to wait."""
        self._stop.set()


def is_overdue(now: datetime, deadline: datetime) -> timedelta | None:
    """How far past `deadline` we are, or None while it is still ahead."""
    return now - deadline if now >= deadline else None


def start_watchdog(
    deadline: datetime,
    on_overdue: Callable[[timedelta], None],
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    interval_s: float = 60.0,
) -> WatchdogHandle:
    """Run a daemon thread that calls `on_overdue` once the deadline has passed.

    A DAEMON thread on purpose: a non-daemon one would itself keep the
    interpreter alive at shutdown, which is the exact failure this guards.

    The wait is `Event.wait`, not `time.sleep`, so `stop()` takes effect at once
    instead of after a full interval. A sleeping host freezes this thread too --
    it therefore fires within one interval of WAKING, which is the 2026-09-17
    case: both scheduled exits had already been discarded by then.

    It returns after firing, so `on_overdue` runs at most once. That matters
    because `on_overdue` ends the process, and a second call racing the first
    would report a second, spurious incident.
    """
    stop = threading.Event()

    def _run() -> None:
        while not stop.is_set():
            overdue = is_overdue(clock(), deadline)
            if overdue is not None:
                on_overdue(overdue)
                return
            stop.wait(interval_s)

    thread = threading.Thread(target=_run, name="process-watchdog", daemon=True)
    thread.start()
    return WatchdogHandle(thread=thread, _stop=stop)


def handle_overdue(
    overdue: timedelta,
    *,
    deadline: datetime,
    db_path: str,
    request_close: Callable[[], object],
    force_exit: Callable[[], None],
    graceful_wait_s: float = 30.0,
) -> None:
    """Report the stuck process, ask it to leave politely, then make it leave.

    The ordering is the safety property, and it is the same one
    `enqueue_ops_alert` was written for: persist FIRST, because everything after
    this point may fail and the alert is the only record that this happened.

    Every step before `force_exit` is wrapped: an unwritable database or a dead
    event loop is a reason to exit, not a reason to stay up. Letting reporting
    abort the exit would leave exactly the process this function exists to end
    -- the same shape as a sweep that aborts on its first outage.
    """
    # A real overdue interval is the difference of two clock readings and so
    # carries microseconds (`8:15:00.001405`). Precision nobody can act on reads
    # as a malfunction in the one artifact the operator actually sees.
    elapsed = timedelta(seconds=int(overdue.total_seconds()))
    when = deadline.astimezone().strftime("%Y-%m-%d %H:%M %Z")

    logger.critical(
        "WATCHDOG: this process should have exited at %s and is still running "
        "%s later. Reporting and shutting down.",
        when,
        elapsed,
    )

    try:
        from database.models import get_cursor
        from database.queries import enqueue_ops_alert

        with get_cursor(db_path) as conn:
            enqueue_ops_alert(
                conn,
                f"Watchdog: the bot outlived its deadline of {when} by {elapsed} "
                f"and was shut down. Both scheduled exits failed to end it -- "
                f"check the log around that deadline before the next session.",
            )
    except Exception as exc:  # noqa: BLE001 - reporting must not block the exit
        logger.error("WATCHDOG: could not persist the ops alert: %s", exc)

    try:
        pending = request_close()
        if pending is not None and hasattr(pending, "result"):
            pending.result(timeout=graceful_wait_s)
    except Exception as exc:  # noqa: BLE001 - politeness is optional, leaving is not
        logger.warning("WATCHDOG: the graceful close did not complete: %s", exc)

    logger.critical("WATCHDOG: forcing exit now.")
    force_exit()
