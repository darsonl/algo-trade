"""The startup banner must report the times that were actually scheduled.

It printed `config.scan_hour:scan_minute` while `configure_scheduler` schedules
from `config.scan_times`. Those are different fields: SCAN_HOUR/SCAN_MINUTE are
only a FALLBACK, used when SCAN_TIMES is empty. With SCAN_TIMES=21:45,3:30 set —
as it is in this repo's .env — the bot logged "daily scan at 22:00", a time at
which nothing happens, and never mentioned the two times at which scans do run.

A message that reads like fact and is not one is the defect shape this project
keeps finding. Here it is in the operator's first line of feedback at startup.
"""
from main import scheduler_summary


def test_it_reports_every_scheduled_time():
    summary = scheduler_summary("Stock scan", ["21:45", "3:30"], None)

    assert "21:45" in summary
    assert "3:30" in summary


def test_it_does_not_report_a_time_that_was_not_scheduled():
    """The actual bug: 22:00 came from the fallback fields, not the schedule."""
    summary = scheduler_summary("Stock scan", ["21:45", "3:30"], None)

    assert "22:00" not in summary


def test_a_single_time_reads_naturally():
    assert "09:30" in scheduler_summary("ETF scan", ["09:30"], None)


def test_it_names_the_configured_timezone():
    summary = scheduler_summary("Stock scan", ["09:45"], "America/New_York")

    assert "America/New_York" in summary


def test_an_unset_timezone_says_machine_local_and_warns():
    """The host is Asia/Taipei and the markets are in New York. 'Machine-local'
    is the one fact that makes 21:45 legible as an ET market time, and its
    absence is what made the timezone bug in market_time.py hard to see."""
    summary = scheduler_summary("Stock scan", ["21:45"], None)

    assert "machine-local" in summary.lower()


def test_an_empty_timezone_string_is_treated_as_unset():
    """config.scan_timezone defaults to '' rather than None."""
    summary = scheduler_summary("Stock scan", ["21:45"], "")

    assert "machine-local" in summary.lower()


def test_no_scheduled_times_is_stated_plainly():
    """Silence here would read as 'scheduled fine'."""
    summary = scheduler_summary("Stock scan", [], None)

    assert "no" in summary.lower()
