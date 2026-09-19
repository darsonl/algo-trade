"""Two ways the daily exit failed on 2026-09-17, leaving the bot up for 25 hours.

`test_post_scan_exit.py` already tells the first half of this story: 2026-09-14's
host slept 08:33->20:06 and froze the scheduler, so the bot learned to exit as
soon as the session's scans were done rather than idle until the bell.

That mitigation has a residual hole, and 2026-09-17 fell in it. The exit is a
one-shot `DateTrigger`, and the machine slept *before* it fired:

    22:01:40  Last scheduled scan is done -- shutting down at 22:31
    06:58:53  (8h57m gap -- the host slept)
    06:59:07  post_scan_shutdown ... was missed by 8:27:27
    06:59:07  _shutdown_at_the_bell ... was missed by 2:59:07

Both were discarded rather than run. `BackgroundScheduler()` is constructed with
no `misfire_grace_time`, so APScheduler's default of ONE SECOND applies, and a
job that comes due while the host is asleep is always later than that. The
process never called `bot.close()` and was still alive the next evening.

A shutdown is the one job that is always worth running late: arriving at "exit"
eight hours after the bell is not a stale scan, it is simply the exit. A scan
keeps the 1-second default on purpose -- nothing re-runs a missed scan, and the
post-scan listener depends on that.

The second failure compounded the first. `on_ready` is not a startup hook:
discord.py fires it again on every gateway RESUME, 46 times that session. Each
fire re-ran the scheduler setup, which appended ANOTHER post-scan listener and
then raised `SchedulerAlreadyRunningError` from `start()` -- abandoning the rest
of the handler, including the approval-window re-arm that sits below it. The
recovery path for the sleep was unreachable from the reconnect that needed it.
"""
import threading
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

import main

UTC = timezone.utc


def _slept_through(hours: int) -> datetime:
    """A fire time that came due while the host was asleep."""
    return datetime.now(UTC) - timedelta(hours=hours)


# --- the exit survives a sleep ---

def test_a_post_scan_exit_missed_during_sleep_still_fires_on_wake():
    """The 2026-09-17 failure itself: due 8h27m ago, and it must still run.

    Fails with the 1-second default -- APScheduler logs "was missed by" and
    removes the job without calling it, which is how the process stayed up.
    """
    scheduler = BackgroundScheduler()
    fired = threading.Event()

    main.schedule_post_scan_shutdown(scheduler, fired.set, _slept_through(8))
    scheduler.start()
    try:
        assert fired.wait(timeout=5), \
            "the missed post-scan exit was discarded as a misfire, not run"
    finally:
        scheduler.shutdown(wait=False)


def test_a_bell_shutdown_missed_during_sleep_still_fires_on_wake():
    """The same sleep swallowed the backstop: missed by 2:59:07, also discarded.

    The bell exists to catch a post-scan exit that never armed, so the two
    failing together is the case that leaves nothing at all to stop the process.
    """
    scheduler = BackgroundScheduler()
    fired = threading.Event()

    main.schedule_session_shutdown(scheduler, fired.set, _slept_through(3))
    scheduler.start()
    try:
        assert fired.wait(timeout=5), \
            "the missed bell shutdown was discarded as a misfire, not run"
    finally:
        scheduler.shutdown(wait=False)


def test_a_missed_scan_is_still_discarded():
    """The grace change is scoped to the exits. A scan slept through is NOT re-run.

    `post_scan_shutdown_at` treats a run time in the past as "being processed,
    not waiting", and the listener arms the exit on MISSED exactly like EXECUTED.
    A scan that fired hours late would place observations under the wrong
    session and re-open an approval window nobody is watching.
    """
    from config import Config

    cfg = Config()
    cfg.scan_times = ["09:35"]
    scheduler = BackgroundScheduler()
    main.configure_scheduler(scheduler, cfg, lambda: None)

    # Paused, not stopped: a job stays "pending" until the scheduler starts, and
    # the scheduler's defaults -- misfire_grace_time among them -- are only
    # applied as it is realised. Pending jobs carry no such attribute at all.
    scheduler.start(paused=True)
    try:
        job = scheduler.get_job("scan_0")
        assert job.misfire_grace_time is not None, \
            "a missed scan must not be re-run hours late; only the exits may be"
    finally:
        scheduler.shutdown(wait=False)


# --- on_ready is fired again on every reconnect ---

def test_the_scheduler_setup_is_skipped_once_it_is_running():
    """The guard that makes a reconnect harmless.

    46 fires stacked 46 post-scan listeners and raised 16 times from start().
    """
    scheduler = BackgroundScheduler()
    assert main.scheduler_needs_setup(scheduler), \
        "the first on_ready must set the scheduler up"

    scheduler.start()
    try:
        assert not main.scheduler_needs_setup(scheduler), \
            "a reconnect must not set the scheduler up a second time"
    finally:
        scheduler.shutdown(wait=False)


def test_on_ready_consults_the_guard_before_starting_the_scheduler():
    """Parsed, not grepped: the guard must be wired in, not merely defined.

    Mirrors the wiring tests in `test_post_scan_exit.py` -- `on_ready` closes
    over main()'s locals and cannot be called without a live gateway.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(main.main))
    names = [
        getattr(n.func, "id", None) or getattr(n.func, "attr", None)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    ]
    assert "scheduler_needs_setup" in names, \
        "on_ready never consults scheduler_needs_setup, so a reconnect re-runs the setup"
