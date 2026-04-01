import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from config import Config
from main import should_recommend, configure_scheduler


# --- should_recommend ---

def make_cfg(max_rsi=70.0):
    c = Config()
    c.max_rsi = max_rsi
    return c


def make_tech(rsi=55.0, price=110.0, ma50=100.0, volume=1_200_000, avg_volume=1_000_000):
    return {"rsi": rsi, "price": price, "ma50": ma50, "volume": volume, "avg_volume": avg_volume}


def test_buy_signal_with_passing_technicals_recommends():
    assert should_recommend("BUY", make_tech(), make_cfg()) is True


def test_hold_signal_does_not_recommend():
    assert should_recommend("HOLD", make_tech(), make_cfg()) is False


def test_skip_signal_does_not_recommend():
    assert should_recommend("SKIP", make_tech(), make_cfg()) is False


def test_buy_signal_with_overbought_rsi_does_not_recommend():
    assert should_recommend("BUY", make_tech(rsi=75.0), make_cfg()) is False


def test_buy_signal_with_price_below_ma50_does_not_recommend():
    assert should_recommend("BUY", make_tech(price=90.0, ma50=100.0), make_cfg()) is False


def test_buy_signal_with_low_volume_does_not_recommend():
    assert should_recommend("BUY", make_tech(volume=300_000, avg_volume=1_000_000), make_cfg()) is False


# --- configure_scheduler ---

def _dummy_job():
    pass


def test_scheduler_has_one_job_after_configure():
    cfg = Config()
    cfg.scan_times = ["09:30"]
    scheduler = BackgroundScheduler()
    configure_scheduler(scheduler, cfg, _dummy_job)
    assert len(scheduler.get_jobs()) == 1


def test_scheduler_registers_multiple_jobs():
    cfg = Config()
    cfg.scan_times = ["09:00", "13:00", "16:00"]
    scheduler = BackgroundScheduler()
    configure_scheduler(scheduler, cfg, _dummy_job)
    assert len(scheduler.get_jobs()) == 3


def test_scheduler_job_fires_at_configured_hour():
    cfg = Config()
    cfg.scan_times = ["14:00"]
    scheduler = BackgroundScheduler()
    configure_scheduler(scheduler, cfg, _dummy_job)
    job = scheduler.get_jobs()[0]
    hour_field = next(f for f in job.trigger.fields if f.name == "hour")
    assert str(hour_field) == "14"


def test_scheduler_job_fires_at_configured_minute():
    cfg = Config()
    cfg.scan_times = ["09:45"]
    scheduler = BackgroundScheduler()
    configure_scheduler(scheduler, cfg, _dummy_job)
    job = scheduler.get_jobs()[0]
    minute_field = next(f for f in job.trigger.fields if f.name == "minute")
    assert str(minute_field) == "45"
