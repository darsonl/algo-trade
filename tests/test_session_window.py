"""The bot runs for one session and then goes home.

Three states, decided once at startup and again by a scheduled shutdown:

* `not_a_session` -- a weekend or a market holiday. Exit before touching
  Discord, so nothing idles and nothing can be orphaned.
* `already_closed` -- a session, but the bell has gone. This is what makes the
  task's repeating trigger safe: a repetition that fires after the close starts
  a process that immediately exits instead of camping until tomorrow.
* `run` -- inside the window, including pre-open, which is when this system is
  designed to be approved from.
"""
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

import main


# 2026-09-14 is a Monday, EDT, closing 16:00 ET == 20:00 UTC.
PRE_OPEN = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)   # 08:00 ET
MIDDAY = datetime(2026, 9, 14, 17, 0, tzinfo=timezone.utc)     # 13:00 ET
CLOSE = datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)      # 16:00 ET
AFTER = datetime(2026, 9, 14, 22, 0, tzinfo=timezone.utc)      # 18:00 ET
SUNDAY = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)


def test_pre_open_is_inside_the_window():
    """The whole point of starting at 08:00 ET. Guard 4 relaxes quote staleness
    outside regular hours precisely because pre-open is when approvals happen."""
    assert main.session_window_status(PRE_OPEN) == "run"


def test_midday_is_inside_the_window():
    assert main.session_window_status(MIDDAY) == "run"


def test_a_weekend_is_not_a_session():
    assert main.session_window_status(SUNDAY) == "not_a_session"


def test_after_the_bell_is_already_closed():
    assert main.session_window_status(AFTER) == "already_closed"


def test_the_close_itself_counts_as_closed():
    """Boundary. At exactly 16:00 the session is over, so a process starting on
    that instant must not decide it has a full day ahead of it."""
    assert main.session_window_status(CLOSE) == "already_closed"


def test_a_half_day_closes_early():
    """13:00 ET on 2026-11-27. At 14:00 ET a hardcoded 16:00 would still say
    'run' and leave the bot up three hours past the market."""
    after_half_day_close = datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc)  # 14:00 ET
    assert main.session_window_status(after_half_day_close) == "already_closed"
    before = datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)  # 12:00 ET
    assert main.session_window_status(before) == "run"


def test_shutdown_is_scheduled_at_the_session_close():
    """Registered on a REAL scheduler, so the assertion is the fire time the
    scheduler actually computed, not an argument we handed a mock."""
    scheduler = BackgroundScheduler()
    marker = object()

    def _shutdown():
        return marker

    main.schedule_session_shutdown(scheduler, _shutdown, CLOSE)
    scheduler.start(paused=True)
    try:
        job = scheduler.get_job("session_shutdown")
        assert job is not None, "no shutdown job was registered"
        assert job.next_run_time == CLOSE
        assert job.func is _shutdown
    finally:
        scheduler.shutdown(wait=False)
