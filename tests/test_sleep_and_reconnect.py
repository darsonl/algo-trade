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
keeps a FINITE grace on purpose -- nothing re-runs a missed scan, and the
post-scan listener depends on that. Finite, not one second: on 2026-10-02 a
+1.363 s NTP clock step made the stock scan 1.4 s late and the 1-second default
discarded it (`main.SCAN_MISFIRE_GRACE_S`, tests at the foot of this file).

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


# --- a scan seconds late is not a missed scan ---

def _scan_due(seconds_ago: float):
    """A real scheduler holding one scan job, made due `seconds_ago`.

    Returns (scheduler, fired, missed). The job's next run time is moved into
    the past while the scheduler is paused, so resuming it is exactly a wake-up
    that arrives late -- the comparison APScheduler makes against the grace.
    """
    from apscheduler.events import EVENT_JOB_MISSED
    from config import Config

    cfg = Config()
    cfg.scan_times = ["09:35"]
    fired, missed = threading.Event(), threading.Event()
    scheduler = BackgroundScheduler()
    main.configure_scheduler(scheduler, cfg, fired.set)
    scheduler.add_listener(lambda e: missed.set(), EVENT_JOB_MISSED)
    scheduler.start(paused=True)
    scheduler.get_job("scan_0").modify(
        next_run_time=datetime.now(UTC) - timedelta(seconds=seconds_ago))
    scheduler.resume()
    return scheduler, fired, missed


def test_a_scan_a_second_and_a_half_late_still_runs():
    """2026-10-02: Windows' time service stepped the clock +1.363 s at 20:00:19,
    three seconds after the scheduler had computed its wait for 21:35. The
    wait is monotonic, so the job woke at a wall-clock 21:35:01.397 -- "missed
    by 0:00:01.397457" -- and the 1-second default discarded the stock scan.
    Over the 33 scans logged before it, the worst start latency was 44 ms."""
    scheduler, fired, missed = _scan_due(1.4)
    try:
        assert fired.wait(timeout=5), \
            "a scan 1.4 s late was discarded as a misfire, not run"
        assert not missed.is_set()
    finally:
        scheduler.shutdown(wait=False)


def test_a_scan_slept_through_for_hours_is_still_discarded():
    """The grace widens jitter tolerance only: a scan due 3 h ago is MISSED,
    which is what the post-scan listener arms the exit on."""
    scheduler, fired, missed = _scan_due(3 * 3600)
    try:
        assert missed.wait(timeout=5), "the scan was neither run nor missed"
        assert not fired.is_set(), "a scan was re-run hours late"
    finally:
        scheduler.shutdown(wait=False)


def test_the_scan_grace_ends_before_the_etf_scan_could_collide():
    """A late stock scan (09:35 ET) must start well before the 10:00 ET ETF
    scan; a colliding scan is SKIPPED by the shared scan lock."""
    assert 60 <= main.SCAN_MISFIRE_GRACE_S <= 15 * 60


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
