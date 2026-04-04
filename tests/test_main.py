import pytest
import pytest_asyncio
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


# --- run_scan cache integration ---

@pytest.mark.asyncio
async def test_run_scan_cache_hit_skips_analyze_ticker():
    """When analyst cache has a hit, analyze_ticker is not called."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from main import run_scan

    bot = MagicMock()
    bot.send_recommendation = AsyncMock(return_value="msg_1")
    bot.send_ops_alert = AsyncMock()

    config = Config()
    config.db_path = ":memory:"

    cached = {"signal": "BUY", "reasoning": "Cached reasoning."}

    with patch("main.get_top_sp500_by_fundamentals", return_value=[]):
        with patch("main.get_universe", return_value=["AAPL"]):
            with patch("main.queries.ticker_recommended_today", return_value=False):
                with patch("main.queries.has_open_position", return_value=False):
                    with patch("main.queries.expire_stale_recommendations"):
                        with patch("main.queries.get_open_positions", return_value=[]):
                            with patch("main.yf.Ticker"):
                                with patch("main.fetch_fundamental_info", return_value={"trailingPE": 20.0, "dividendYield": 0.03, "earningsGrowth": 0.1}):
                                    with patch("main.passes_fundamental_filter", return_value=True):
                                        with patch("main.fetch_news_headlines", return_value=["headline A"]):
                                            with patch("main.queries.get_cached_analysis", return_value=cached):
                                                with patch("main.analyze_ticker") as mock_analyze:
                                                    with patch("main.fetch_technical_data", return_value={"price": 150.0, "rsi": 60.0, "ma50": 140.0, "volume_ratio": 1.2}):
                                                        with patch("main.passes_technical_filter", return_value=True):
                                                            with patch("main.queries.create_recommendation", return_value=1):
                                                                with patch("main.queries.set_discord_message_id"):
                                                                    await run_scan(bot, config)
                                                                    mock_analyze.assert_not_called()


@pytest.mark.asyncio
async def test_run_scan_cache_miss_calls_analyze_ticker_and_caches():
    """On cache miss, analyze_ticker is called and result is written to cache."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from main import run_scan

    bot = MagicMock()
    bot.send_recommendation = AsyncMock(return_value="msg_1")
    bot.send_ops_alert = AsyncMock()

    config = Config()
    config.db_path = ":memory:"

    analysis_result = {"signal": "BUY", "reasoning": "Fresh analysis."}

    with patch("main.get_top_sp500_by_fundamentals", return_value=[]):
        with patch("main.get_universe", return_value=["AAPL"]):
            with patch("main.queries.ticker_recommended_today", return_value=False):
                with patch("main.queries.has_open_position", return_value=False):
                    with patch("main.queries.expire_stale_recommendations"):
                        with patch("main.queries.get_open_positions", return_value=[]):
                            with patch("main.yf.Ticker"):
                                with patch("main.fetch_fundamental_info", return_value={"trailingPE": 20.0, "dividendYield": 0.03, "earningsGrowth": 0.1}):
                                    with patch("main.passes_fundamental_filter", return_value=True):
                                        with patch("main.fetch_news_headlines", return_value=["headline B"]):
                                            with patch("main.queries.get_cached_analysis", return_value=None):
                                                with patch("main.analyze_ticker", return_value=analysis_result) as mock_analyze:
                                                    with patch("main.queries.set_cached_analysis") as mock_set_cache:
                                                        with patch("main.fetch_technical_data", return_value={"price": 150.0, "rsi": 60.0, "ma50": 140.0, "volume_ratio": 1.2}):
                                                            with patch("main.passes_technical_filter", return_value=True):
                                                                with patch("main.queries.create_recommendation", return_value=1):
                                                                    with patch("main.queries.set_discord_message_id"):
                                                                        await run_scan(bot, config)
                                                                        mock_analyze.assert_called_once()
                                                                        assert mock_set_cache.call_count == 1
                                                                        args = mock_set_cache.call_args[0]
                                                                        assert "BUY" in args
                                                                        assert "Fresh analysis." in args
