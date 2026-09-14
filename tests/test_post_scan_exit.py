"""The bot exits once today's scheduled scans are done, not at the closing bell.

Everything the bot does on a schedule happens in two scans (stock 09:35 ET, ETF
10:00 ET). Staying up to 16:00 kept a gateway open for six idle hours and, since
nothing held the machine awake, invited the PC to sleep mid-session and freeze
the scheduler -- which is how 2026-09-14's host slept 08:33->20:06.

So: when the LAST scan job of the session has finished (or been missed), the bot
stays up for a short approval window -- Approve/Reject buttons only answer while
the process is alive -- and then exits. The bell shutdown remains as a backstop.
While it runs it holds the system awake; Windows drops that hold when the
process ends, so normal sleep resumes with nothing to restore.

A startup guard mirrors `already_closed`: the task repeats every 30 minutes, and
a repeat that fires after the window must not bring the bot back up.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

import main
from config import Config

UTC = timezone.utc
# 2026-09-15 is a Tuesday, EDT: 10:00 ET == 14:00 UTC, close 16:00 ET == 20:00 UTC.
ETF_RUN = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
CLOSE = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)


def _config(stock=("09:35",), etf=("10:00",), window=30, tz="America/New_York"):
    c = Config()
    c.scan_times = list(stock)
    c.etf_scan_times = list(etf)
    c.scan_timezone = tz
    c.post_scan_window_min = window
    return c


# --- when is the last scheduled scan? ---

def test_the_last_scan_is_the_latest_across_both_schedules():
    """Fails if only SCAN_TIMES is read: the ETF scan runs after the stock scan."""
    assert main.last_scheduled_scan_utc(_config(), ETF_RUN) == ETF_RUN


def test_the_latest_wins_whichever_list_it_is_in():
    c = _config(stock=("11:15",), etf=("10:00",))
    assert main.last_scheduled_scan_utc(c, ETF_RUN) == datetime(2026, 9, 15, 15, 15, tzinfo=UTC)


def test_scan_times_are_market_times_across_dst():
    """10:00 ET is 14:00 UTC in September and 15:00 UTC after DST ends. Fails if
    the times are pinned to a fixed UTC offset."""
    winter = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    assert main.last_scheduled_scan_utc(_config(), winter) == winter


def test_no_scheduled_scans_means_no_last_scan():
    assert main.last_scheduled_scan_utc(_config(stock=(), etf=()), ETF_RUN) is None


# --- the startup guard ---

def test_inside_the_approval_window_the_bot_may_start():
    """A crash at 10:10 ET is restarted by the task; the window is not over."""
    at = ETF_RUN + timedelta(minutes=29, seconds=59)
    assert main.session_window_status(at, config=_config()) == "run"


def test_after_the_window_the_bot_must_not_start():
    """Fails without the new state: a 10:30 ET task repeat would start a bot
    that idles until the bell, holding the PC awake for nothing."""
    at = ETF_RUN + timedelta(minutes=30)
    assert main.session_window_status(at, config=_config()) == "scans_done"


def test_the_window_length_comes_from_config():
    at = ETF_RUN + timedelta(minutes=45)
    assert main.session_window_status(at, config=_config(window=60)) == "run"


def test_a_non_session_still_reports_as_such():
    sunday = datetime(2026, 9, 13, 15, 0, tzinfo=UTC)
    assert main.session_window_status(sunday, config=_config()) == "not_a_session"


def test_after_the_bell_is_still_already_closed():
    assert main.session_window_status(CLOSE, config=_config()) == "already_closed"


def test_without_config_the_old_behaviour_is_unchanged():
    assert main.session_window_status(ETF_RUN + timedelta(hours=1)) == "run"


# --- when to shut down, decided from the scheduler's own next run times ---

NOW = datetime(2026, 9, 15, 14, 2, tzinfo=UTC)
TOMORROW_STOCK = datetime(2026, 9, 16, 13, 35, tzinfo=UTC)
TOMORROW_ETF = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)


def test_a_scan_still_due_today_defers_shutdown():
    """After the stock scan, the ETF scan is still ahead. Fails if the first
    finished scan is taken to be the last."""
    later_today = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    stock_done = datetime(2026, 9, 15, 13, 45, tzinfo=UTC)
    assert main.post_scan_shutdown_at([TOMORROW_STOCK, later_today], CLOSE, stock_done, 30) is None


def test_when_every_scan_next_runs_tomorrow_shut_down_after_the_window():
    assert main.post_scan_shutdown_at([TOMORROW_STOCK, TOMORROW_ETF], CLOSE, NOW, 30) == NOW + timedelta(minutes=30)


def test_a_run_time_already_in_the_past_is_not_pending():
    """APScheduler emits a MISSED event before it advances that job's next run
    time, so the job can still show today's (past) time. It is being processed,
    not waiting -- counting it as pending would mean the bot never exits."""
    assert main.post_scan_shutdown_at([TOMORROW_STOCK, ETF_RUN], CLOSE, NOW, 30) == NOW + timedelta(minutes=30)


def test_a_paused_job_is_not_pending():
    assert main.post_scan_shutdown_at([None, TOMORROW_ETF], CLOSE, NOW, 30) == NOW + timedelta(minutes=30)


# --- the listener the scheduler calls after every job ---

def _listener(jobs, clock=lambda: NOW):
    registered = []
    listener = main.make_post_scan_listener(
        jobs_fn=lambda: jobs,
        schedule_fn=lambda when: registered.append(when),
        close_utc=CLOSE,
        window_min=30,
        clock=clock,
    )
    return listener, registered


def _job(job_id, next_run_time):
    return SimpleNamespace(id=job_id, next_run_time=next_run_time)


def test_the_last_scan_finishing_schedules_the_shutdown():
    listener, registered = _listener([_job("scan_0", TOMORROW_STOCK), _job("etf_scan_0", TOMORROW_ETF)])
    listener(SimpleNamespace(job_id="etf_scan_0"))
    assert registered == [NOW + timedelta(minutes=30)]


def test_a_non_scan_job_is_ignored():
    """The bell shutdown and the post-scan shutdown are jobs too. Fails if their
    own events re-arm a shutdown."""
    listener, registered = _listener([_job("scan_0", TOMORROW_STOCK), _job("etf_scan_0", TOMORROW_ETF),
                                      _job("session_shutdown", CLOSE)])
    listener(SimpleNamespace(job_id="session_shutdown"))
    assert registered == []


def test_other_jobs_do_not_count_as_pending_scans():
    """The bell shutdown sits before the close forever; if it counted as a
    pending scan the bot would never exit early."""
    listener, registered = _listener([_job("scan_0", TOMORROW_STOCK), _job("etf_scan_0", TOMORROW_ETF),
                                      _job("session_shutdown", CLOSE - timedelta(seconds=1))])
    listener(SimpleNamespace(job_id="scan_0"))
    assert registered == [NOW + timedelta(minutes=30)]


def test_the_first_of_two_scans_finishing_schedules_nothing():
    stock_done = datetime(2026, 9, 15, 13, 45, tzinfo=UTC)
    listener, registered = _listener([_job("scan_0", TOMORROW_STOCK), _job("etf_scan_0", ETF_RUN)],
                                     clock=lambda: stock_done)
    listener(SimpleNamespace(job_id="scan_0"))
    assert registered == []


def test_the_post_scan_shutdown_is_registered_on_a_real_scheduler():
    """The fire time is what the scheduler computed, and a second registration
    moves it rather than adding another shutdown."""
    scheduler = BackgroundScheduler()
    far = datetime(2099, 1, 5, 15, 0, tzinfo=UTC)

    def _shutdown():
        pass

    main.schedule_post_scan_shutdown(scheduler, _shutdown, far)
    main.schedule_post_scan_shutdown(scheduler, _shutdown, far + timedelta(minutes=2))
    scheduler.start(paused=True)
    try:
        jobs = [j for j in scheduler.get_jobs() if j.id == "post_scan_shutdown"]
        assert len(jobs) == 1
        assert jobs[0].next_run_time == far + timedelta(minutes=2)
        assert jobs[0].func is _shutdown
    finally:
        scheduler.shutdown(wait=False)


# --- wiring: main() must actually use all of the above ---

def _main_calls():
    """Every call inside main() (including nested on_ready), by callee name, with
    its keyword names. Parsed, not grepped, so docstrings do not count."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(main.main))
    calls = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            calls.setdefault(name, []).append({k.arg for k in node.keywords})
    return calls, tree


def test_main_consults_the_scans_done_guard():
    """Fails if startup calls session_window_status() without the config, which
    silently disables `scans_done` and lets every task repeat restart the bot."""
    calls, _ = _main_calls()
    assert any("config" in kws for kws in calls.get("session_window_status", [])), \
        "main() never passes config to session_window_status"


def test_main_refuses_to_start_when_scans_are_done():
    import ast
    _, tree = _main_calls()
    handled = any(isinstance(n, ast.Constant) and n.value == "scans_done" for n in ast.walk(tree))
    assert handled, "main() does not handle the 'scans_done' startup state"


def test_main_holds_the_pc_awake():
    calls, _ = _main_calls()
    assert "hold_system_awake" in calls


def test_main_registers_the_post_scan_listener():
    calls, _ = _main_calls()
    assert "make_post_scan_listener" in calls and "add_listener" in calls


# --- config ---

def test_the_window_defaults_to_thirty_minutes(monkeypatch):
    monkeypatch.delenv("POST_SCAN_WINDOW_MIN", raising=False)
    assert Config().post_scan_window_min == 30


def test_the_window_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("POST_SCAN_WINDOW_MIN", "45")
    assert Config().post_scan_window_min == 45


# --- holding the machine awake ---

class _Kernel32:
    def __init__(self, returns=1, raises=None):
        self.calls = []
        self._returns, self._raises = returns, raises

    def SetThreadExecutionState(self, flags):
        self.calls.append(flags)
        if self._raises:
            raise self._raises
        return self._returns


def test_the_hold_asks_for_the_system_not_the_display():
    """ES_CONTINUOUS | ES_SYSTEM_REQUIRED keeps the PC from sleeping while the
    process lives. ES_DISPLAY_REQUIRED (0x2) would also keep the monitor on all
    evening. Fails if the flags change."""
    from keep_awake import hold_system_awake
    k = _Kernel32()
    assert hold_system_awake(kernel32=k) is True
    assert k.calls == [0x80000001]
    assert not k.calls[0] & 0x2


def test_a_refused_hold_is_reported_not_raised():
    from keep_awake import hold_system_awake
    assert hold_system_awake(kernel32=_Kernel32(returns=0)) is False


def test_no_windows_api_means_no_hold():
    """CI runs on Linux. The bot must start there, just without the hold."""
    from keep_awake import hold_system_awake
    assert hold_system_awake(kernel32=None) is False


def test_an_api_error_does_not_stop_the_bot():
    from keep_awake import hold_system_awake
    assert hold_system_awake(kernel32=_Kernel32(raises=OSError("denied"))) is False
