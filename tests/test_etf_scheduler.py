"""Tests for ETF scheduler configuration — _parse_etf_scan_times helper and
configure_scheduler ETF job registration (Phase 12-01)."""
import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from config import _parse_etf_scan_times, Config
from main import configure_scheduler


# ---------------------------------------------------------------------------
# _parse_etf_scan_times helper tests
# ---------------------------------------------------------------------------

def test_parse_etf_scan_times_default_no_env_vars(monkeypatch):
    """With no ETF_SCAN_HOUR/ETF_SCAN_MINUTE set, returns ["09:30"]."""
    monkeypatch.delenv("ETF_SCAN_HOUR", raising=False)
    monkeypatch.delenv("ETF_SCAN_MINUTE", raising=False)
    result = _parse_etf_scan_times()
    assert result == ["09:30"]


def test_parse_etf_scan_times_respects_hour_and_minute(monkeypatch):
    """ETF_SCAN_HOUR=10 + ETF_SCAN_MINUTE=15 returns ["10:15"]."""
    monkeypatch.setenv("ETF_SCAN_HOUR", "10")
    monkeypatch.setenv("ETF_SCAN_MINUTE", "15")
    result = _parse_etf_scan_times()
    assert result == ["10:15"]


def test_parse_etf_scan_times_zero_pads_single_digit(monkeypatch):
    """Single-digit hour and minute are zero-padded: 7 → 07, 5 → 05."""
    monkeypatch.setenv("ETF_SCAN_HOUR", "7")
    monkeypatch.setenv("ETF_SCAN_MINUTE", "5")
    result = _parse_etf_scan_times()
    assert result == ["07:05"]


# ---------------------------------------------------------------------------
# Config.etf_scan_hour / etf_scan_minute / etf_scan_times field defaults
# ---------------------------------------------------------------------------

def test_config_etf_scan_hour_default():
    """Config().etf_scan_hour defaults to 9."""
    cfg = Config()
    assert cfg.etf_scan_hour == 9


def test_config_etf_scan_minute_default():
    """Config().etf_scan_minute defaults to 30."""
    cfg = Config()
    assert cfg.etf_scan_minute == 30


def test_config_etf_scan_times_default():
    """Config().etf_scan_times defaults to ["09:30"]."""
    cfg = Config()
    assert cfg.etf_scan_times == ["09:30"]


def test_config_etf_scan_times_respects_env_override(monkeypatch):
    """Config().etf_scan_times reflects ETF_SCAN_HOUR/ETF_SCAN_MINUTE overrides when
    _parse_etf_scan_times is called directly (env vars evaluated at call time)."""
    monkeypatch.setenv("ETF_SCAN_HOUR", "11")
    monkeypatch.setenv("ETF_SCAN_MINUTE", "45")
    result = _parse_etf_scan_times()
    assert result == ["11:45"]


# ---------------------------------------------------------------------------
# configure_scheduler — ETF job registration
# ---------------------------------------------------------------------------

def _dummy_job():
    pass


def test_configure_scheduler_default_args_uses_scan_prefix():
    """Default call registers jobs with IDs scan_0, scan_1, ... (backward compat)."""
    cfg = Config()
    cfg.scan_times = ["09:00", "13:00"]
    scheduler = BackgroundScheduler()
    configure_scheduler(scheduler, cfg, _dummy_job)
    job_ids = {j.id for j in scheduler.get_jobs()}
    assert "scan_0" in job_ids
    assert "scan_1" in job_ids


def test_configure_scheduler_etf_prefix_registers_etf_scan_0():
    """Passing times and job_id_prefix='etf_scan' registers job with ID etf_scan_0."""
    cfg = Config()
    scheduler = BackgroundScheduler()
    configure_scheduler(
        scheduler,
        cfg,
        _dummy_job,
        times=["09:30"],
        job_id_prefix="etf_scan",
    )
    job_ids = {j.id for j in scheduler.get_jobs()}
    assert "etf_scan_0" in job_ids


def test_configure_scheduler_two_calls_no_collision():
    """Calling configure_scheduler twice results in both scan_0 and etf_scan_0 with
    non-colliding IDs and different trigger times."""
    cfg = Config()
    cfg.scan_times = ["09:00"]
    cfg.etf_scan_times = ["09:30"]
    scheduler = BackgroundScheduler()

    # stock scan
    configure_scheduler(scheduler, cfg, _dummy_job)
    # ETF scan
    configure_scheduler(
        scheduler,
        cfg,
        _dummy_job,
        times=cfg.etf_scan_times,
        job_id_prefix="etf_scan",
    )

    job_ids = {j.id for j in scheduler.get_jobs()}
    assert "scan_0" in job_ids
    assert "etf_scan_0" in job_ids
    assert len(job_ids) == 2


def test_configure_scheduler_two_calls_different_trigger_times():
    """stock scan_0 fires at 09:00; etf_scan_0 fires at 09:30 — independently configured."""
    cfg = Config()
    cfg.scan_times = ["09:00"]
    cfg.etf_scan_times = ["09:30"]
    scheduler = BackgroundScheduler()

    configure_scheduler(scheduler, cfg, _dummy_job)
    configure_scheduler(
        scheduler,
        cfg,
        _dummy_job,
        times=cfg.etf_scan_times,
        job_id_prefix="etf_scan",
    )

    jobs_by_id = {j.id: j for j in scheduler.get_jobs()}
    stock_job = jobs_by_id["scan_0"]
    etf_job = jobs_by_id["etf_scan_0"]

    stock_hour = str(next(f for f in stock_job.trigger.fields if f.name == "hour"))
    stock_minute = str(next(f for f in stock_job.trigger.fields if f.name == "minute"))
    etf_hour = str(next(f for f in etf_job.trigger.fields if f.name == "hour"))
    etf_minute = str(next(f for f in etf_job.trigger.fields if f.name == "minute"))

    assert stock_hour == "9"
    assert stock_minute == "0"
    assert etf_hour == "9"
    assert etf_minute == "30"


def test_configure_scheduler_etf_times_do_not_affect_stock_job():
    """Changing etf_scan_times does not alter the stock scan_0 trigger time."""
    cfg = Config()
    cfg.scan_times = ["09:00"]
    cfg.etf_scan_times = ["10:15"]  # different ETF time
    scheduler = BackgroundScheduler()

    configure_scheduler(scheduler, cfg, _dummy_job)
    configure_scheduler(
        scheduler,
        cfg,
        _dummy_job,
        times=cfg.etf_scan_times,
        job_id_prefix="etf_scan",
    )

    stock_job = next(j for j in scheduler.get_jobs() if j.id == "scan_0")
    stock_hour = str(next(f for f in stock_job.trigger.fields if f.name == "hour"))
    stock_minute = str(next(f for f in stock_job.trigger.fields if f.name == "minute"))

    assert stock_hour == "9"
    assert stock_minute == "0"
