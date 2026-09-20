"""The deadman that catches a bot which should have exited and did not.

On 2026-09-17 the host slept through both scheduled exits. APScheduler's
one-second default misfire grace discarded them *without calling them*, and the
process stayed up for 25 hours holding the PC awake.

#62 raised the grace on both exits, which fixes that particular sleep. This
module guards the case that fix cannot reach: the scheduler itself being the
thing that failed. A watchdog scheduled ON APScheduler inherits the defect it
exists to catch, so this one is a plain daemon thread and consults nothing but
a clock.

WHY A STARTUP CHECK WOULD NOT WORK, which the 2026-09-19 handoff proposed:
`scripts/algo_trade_task.xml` sets `MultipleInstancesPolicy=IgnoreNew`, so while
a task instance runs, every later trigger is DISCARDED -- including the next
day's, not merely the 30-minute repeats. A stuck process suppresses its own
replacement, so `main.py` never starts and a check at its head is unreachable
in exactly the scenario it was written for. Verified in the 2026-09-18 log: the
20:00 task fire produced no process at all while the stuck one was alive.
"""

import ast
import concurrent.futures
import inspect
import threading
from datetime import datetime, timedelta, timezone

import pytest

import main
from config import Config
from database import queries
from database.models import get_cursor, initialize_db
from process_watchdog import handle_overdue, start_watchdog, watchdog_deadline


UTC = timezone.utc


def test_the_deadline_is_the_close_plus_the_approval_window_plus_grace():
    close = datetime(2026, 9, 21, 20, 0, tzinfo=UTC)  # 16:00 ET

    deadline = watchdog_deadline(close, window_min=30, grace_min=15)

    assert deadline == datetime(2026, 9, 21, 20, 45, tzinfo=UTC)


def test_the_deadline_clears_an_exit_armed_by_a_restart_inside_the_window():
    """A restart inside the approval window arms its exit AFTER the close.

    `main.py`'s `on_ready` re-arms at `last_scan + post_scan_window_min` when it
    starts inside the window. With a late enough last scan that instant is past
    the bell, so a deadline of the close alone would kill a bot doing exactly
    what it was told to. The window term is what makes this a fact rather than
    a judgement call.
    """
    close = datetime(2026, 9, 21, 20, 0, tzinfo=UTC)
    last_scan = close - timedelta(minutes=5)
    legitimate_exit = last_scan + timedelta(minutes=30)  # 20:25, past the close

    deadline = watchdog_deadline(close, window_min=30, grace_min=15)

    assert deadline > legitimate_exit


def test_a_naive_close_is_refused_rather_than_assumed_to_be_utc():
    """Every instant in this codebase is tz-aware; a naive one is a bug upstream.

    Guessing UTC here would put the deadline hours off on this Asia/Taipei host
    and the error would surface as a bot killed mid-session, far from its cause.
    """
    with pytest.raises(ValueError):
        watchdog_deadline(datetime(2026, 9, 21, 20, 0), window_min=30, grace_min=15)


# ---------------------------------------------------------------------------
# The thread. It consults a clock and nothing else -- deliberately NOT an
# APScheduler job, because APScheduler discarding its jobs is the failure this
# catches, and a watchdog scheduled on it would inherit that defect.
# ---------------------------------------------------------------------------


def test_it_fires_once_the_clock_is_past_the_deadline():
    deadline = datetime(2026, 9, 21, 20, 45, tzinfo=UTC)
    fired = threading.Event()

    handle = start_watchdog(
        deadline,
        on_overdue=lambda overdue: fired.set(),
        clock=lambda: deadline + timedelta(minutes=1),
        interval_s=0.01,
    )
    try:
        assert fired.wait(timeout=5), "the watchdog never fired past its deadline"
    finally:
        handle.stop()


def test_it_stays_quiet_while_the_deadline_is_still_ahead():
    deadline = datetime(2026, 9, 21, 20, 45, tzinfo=UTC)
    fired = threading.Event()

    handle = start_watchdog(
        deadline,
        on_overdue=lambda overdue: fired.set(),
        clock=lambda: deadline - timedelta(minutes=1),
        interval_s=0.01,
    )
    try:
        assert not fired.wait(timeout=0.3), "the watchdog fired before its deadline"
    finally:
        handle.stop()


def test_it_reports_how_far_past_the_deadline_it_is():
    """The operator's first question is 'how long was it stuck?', and only the
    watchdog can answer it -- nothing else recorded the deadline."""
    deadline = datetime(2026, 9, 21, 20, 45, tzinfo=UTC)
    seen = []
    done = threading.Event()

    def _record(overdue):
        seen.append(overdue)
        done.set()

    handle = start_watchdog(
        deadline,
        on_overdue=_record,
        clock=lambda: deadline + timedelta(hours=8, minutes=27),
        interval_s=0.01,
    )
    try:
        assert done.wait(timeout=5)
    finally:
        handle.stop()

    assert seen == [timedelta(hours=8, minutes=27)]


def test_the_thread_ends_after_firing_so_it_cannot_fire_twice():
    deadline = datetime(2026, 9, 21, 20, 45, tzinfo=UTC)
    calls = []
    done = threading.Event()

    def _count(overdue):
        calls.append(overdue)
        done.set()

    handle = start_watchdog(
        deadline,
        on_overdue=_count,
        clock=lambda: deadline + timedelta(minutes=1),
        interval_s=0.01,
    )
    assert done.wait(timeout=5)
    handle.thread.join(timeout=5)

    assert not handle.thread.is_alive(), "the watchdog thread outlived its one job"
    assert len(calls) == 1


def test_the_thread_is_a_daemon_so_it_cannot_itself_hold_the_process_open():
    """A non-daemon watchdog would keep the interpreter alive at shutdown --
    the fix becoming the bug it was written to catch."""
    deadline = datetime(2026, 9, 21, 20, 45, tzinfo=UTC)

    handle = start_watchdog(
        deadline,
        on_overdue=lambda overdue: None,
        clock=lambda: deadline - timedelta(hours=1),
        interval_s=0.01,
    )
    try:
        assert handle.thread.daemon is True
    finally:
        handle.stop()


def test_stopping_it_ends_the_thread_without_firing():
    deadline = datetime(2026, 9, 21, 20, 45, tzinfo=UTC)
    fired = threading.Event()

    handle = start_watchdog(
        deadline,
        on_overdue=lambda overdue: fired.set(),
        clock=lambda: deadline - timedelta(hours=1),
        interval_s=30.0,
    )
    handle.stop()
    handle.thread.join(timeout=5)

    assert not handle.thread.is_alive(), "stop() did not end the watchdog thread"
    assert not fired.is_set()


# ---------------------------------------------------------------------------
# What firing actually does. The exit is the guarantee; everything before it is
# reporting, and reporting must never be able to prevent the exit.
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path):
    db = str(tmp_path / "wd.db")
    initialize_db(db)
    return db


def test_it_leaves_a_durable_ops_alert_naming_how_long_it_was_stuck(tmp_path):
    """Written to the outbox, NOT sent via Discord.

    `bot.send_ops_alert` is a coroutine on the event loop, and this process is
    leaving precisely because we cannot assume that loop is healthy. The outbox
    is durable and drains at the next scan start, so the operator sees it after
    the process is gone -- which is the whole reason the outbox exists.
    """
    db = _fresh_db(tmp_path)

    handle_overdue(
        timedelta(hours=8, minutes=27),
        deadline=datetime(2026, 9, 21, 20, 45, tzinfo=UTC),
        db_path=db,
        request_close=lambda: None,
        force_exit=lambda: None,
    )

    with get_cursor(db) as conn:
        alerts = queries.get_undelivered_ops_alerts(conn)
    assert len(alerts) == 1
    assert "8:27" in alerts[0]["message"]


def test_it_requests_a_graceful_close_before_forcing_the_exit(tmp_path):
    order = []

    handle_overdue(
        timedelta(minutes=1),
        deadline=datetime(2026, 9, 21, 20, 45, tzinfo=UTC),
        db_path=_fresh_db(tmp_path),
        request_close=lambda: order.append("close"),
        force_exit=lambda: order.append("exit"),
    )

    assert order == ["close", "exit"]


def test_it_forces_the_exit_even_when_the_graceful_close_never_returns(tmp_path):
    """The loop being wedged is a live possibility -- a console QuickEdit
    selection blocks a log write while holding the logging lock, which has
    already cost this project a scan. Waiting on it forever would reproduce the
    stuck process the watchdog was added to end."""
    exited = []

    def _never_completes():
        return concurrent.futures.Future()  # never resolved

    handle_overdue(
        timedelta(minutes=1),
        deadline=datetime(2026, 9, 21, 20, 45, tzinfo=UTC),
        db_path=_fresh_db(tmp_path),
        request_close=_never_completes,
        force_exit=lambda: exited.append(True),
        graceful_wait_s=0.05,
    )

    assert exited == [True]


def test_it_forces_the_exit_even_when_the_graceful_close_raises(tmp_path):
    exited = []

    def _raises():
        raise RuntimeError("event loop is closed")

    handle_overdue(
        timedelta(minutes=1),
        deadline=datetime(2026, 9, 21, 20, 45, tzinfo=UTC),
        db_path=_fresh_db(tmp_path),
        request_close=_raises,
        force_exit=lambda: exited.append(True),
    )

    assert exited == [True]


def test_it_forces_the_exit_even_when_the_alert_cannot_be_written(tmp_path):
    """An unwritable database is a reason to exit, not a reason to stay up."""
    exited = []

    handle_overdue(
        timedelta(minutes=1),
        deadline=datetime(2026, 9, 21, 20, 45, tzinfo=UTC),
        db_path=str(tmp_path / "no_such_dir" / "wd.db"),
        request_close=lambda: None,
        force_exit=lambda: exited.append(True),
    )

    assert exited == [True]


# ---------------------------------------------------------------------------
# Wiring. Structural, for the same reason test_post_scan_exit.py's checks are:
# the watchdog is invisible when it works, so a revert that silently unwires it
# would show up in no other test.
# ---------------------------------------------------------------------------


def _main_calls():
    """Every call inside main() by callee name, with its keyword names.

    Parsed rather than grepped, so a docstring mentioning the watchdog does not
    count as wiring it.
    """
    tree = ast.parse(inspect.getsource(main.main))
    calls = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            calls.setdefault(name, []).append({k.arg for k in node.keywords})
    return calls, tree


def test_main_arms_the_watchdog():
    calls, _ = _main_calls()
    assert "start_watchdog" in calls, "main() never arms the stuck-process watchdog"


def test_main_derives_the_deadline_rather_than_hardcoding_one():
    calls, _ = _main_calls()
    assert "watchdog_deadline" in calls, \
        "main() does not compute the deadline from the session close"


def test_the_watchdog_is_armed_before_the_bot_blocks_on_run():
    """`on_ready` is the wrong home for it.

    A process that never reaches `on_ready` -- the gateway never connects, or
    the channel check raises -- is exactly as stuck as one that does, and
    `on_ready` has already demonstrated it can abandon its own tail partway
    through (2026-09-18: `scheduler.start()` raised and skipped everything
    below it, including the repair for the previous defect).
    """
    tree = ast.parse(inspect.getsource(main.main))
    arm_line = exit_line = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "start_watchdog":
            arm_line = node.lineno
        elif name == "run" and getattr(node.func, "value", None) is not None:
            if getattr(node.func.value, "id", None) == "bot":
                exit_line = node.lineno

    assert arm_line is not None, "main() never arms the watchdog"
    assert exit_line is not None, "could not find bot.run() in main()"
    assert arm_line < exit_line, "the watchdog is armed after bot.run() blocks"


def test_the_grace_is_configurable_and_defaults_to_fifteen_minutes():
    assert Config().watchdog_grace_min == 15


def test_the_alert_reports_whole_seconds_not_raw_clock_microseconds(tmp_path):
    """A real overdue interval is the difference of two clock readings, so it
    carries microseconds: `8:15:00.001405`. The unit tests above pass synthetic
    timedeltas that stringify cleanly and cannot see this; driving the real
    watchdog in a real process is what surfaced it. The alert is the operator's
    only artifact here, and spurious precision reads as a malfunction.
    """
    db = _fresh_db(tmp_path)

    handle_overdue(
        timedelta(hours=8, minutes=15, microseconds=1405),
        deadline=datetime(2026, 9, 21, 20, 45, tzinfo=UTC),
        db_path=db,
        request_close=lambda: None,
        force_exit=lambda: None,
    )

    with get_cursor(db) as conn:
        message = queries.get_undelivered_ops_alerts(conn)[0]["message"]

    assert "8:15:00" in message
    assert ".001405" not in message
