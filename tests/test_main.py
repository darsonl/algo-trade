import pytest
import pytest_asyncio
from unittest.mock import patch
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
@patch("main.fetch_eps_data")
async def test_run_scan_cache_hit_skips_analyze_ticker(mock_eps):
    """When analyst cache has a hit, analyze_ticker is not called — and the slow
    fetch_eps_data enrichment is skipped (it lives in the cache-miss branch)."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from main import run_scan

    bot = MagicMock()
    bot.send_recommendation = AsyncMock(return_value="msg_1")
    bot.send_ops_alert = AsyncMock()

    config = Config()
    config.db_path = ":memory:"
    config.dry_run = True  # keep reconciliation skipped — live mode would call Schwab

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
                                                                    mock_eps.assert_not_called()  # EPS enrichment skipped on cache hit


@pytest.mark.asyncio
@patch("main.fetch_eps_data")
async def test_run_scan_cache_miss_calls_analyze_ticker_and_caches(mock_eps):
    """On cache miss, analyze_ticker is called, the EPS enrichment runs, and the
    result is written to cache."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from main import run_scan

    bot = MagicMock()
    bot.send_recommendation = AsyncMock(return_value="msg_1")
    bot.send_ops_alert = AsyncMock()

    config = Config()
    config.db_path = ":memory:"
    config.dry_run = True  # keep reconciliation skipped — live mode would call Schwab

    analysis_result = {"signal": "BUY", "reasoning": "Fresh analysis.", "provider_used": "gemini"}

    with patch("main.get_top_sp500_by_fundamentals", return_value=[]):
        with patch("main.get_universe", return_value=["AAPL"]):
            with patch("main.partition_watchlist", return_value=(["AAPL"], [])):
                with patch("main.queries.ticker_recommended_today", return_value=False):
                    with patch("main.queries.has_open_position", return_value=False):
                        with patch("main.queries.expire_stale_recommendations"):
                            with patch("main.queries.get_open_positions", return_value=[]):
                                with patch("main.yf.Ticker"):
                                    with patch("main.fetch_fundamental_info", return_value={"trailingPE": 20.0, "dividendYield": 0.03, "earningsGrowth": 0.1}):
                                        with patch("main.passes_fundamental_filter", return_value=True):
                                            with patch("main.fetch_news_headlines", return_value=["headline B"]):
                                                with patch("main.queries.get_cached_analysis", return_value=None):
                                                    with patch("main.queries.get_analyst_call_count_today", return_value=0):
                                                        with patch("main.queries.increment_analyst_call_count"):
                                                            with patch("main.analyze_ticker", return_value=analysis_result) as mock_analyze:
                                                                with patch("main.queries.set_cached_analysis") as mock_set_cache:
                                                                    with patch("main.fetch_technical_data", return_value={"price": 150.0, "rsi": 60.0, "ma50": 140.0, "volume_ratio": 1.2}):
                                                                        with patch("main.passes_technical_filter", return_value=True):
                                                                            with patch("main.queries.create_recommendation", return_value=1):
                                                                                with patch("main.queries.set_discord_message_id"):
                                                                                    await run_scan(bot, config)
                                                                                    mock_analyze.assert_called_once()
                                                                                    mock_eps.assert_called_once()  # EPS enrichment runs on cache miss
                                                                                    assert mock_set_cache.call_count == 1
                                                                                    args = mock_set_cache.call_args[0]
                                                                                    assert "BUY" in args
                                                                                    assert "Fresh analysis." in args


# ---------------------------------------------------------------------------
# Quota exhaustion tests (TEST-11 / D-03)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_scan_skips_analyze_ticker_when_all_providers_exhausted():
    """When all three providers are at/over the daily limit, analyze_ticker is NOT called.

    Guards: all three provider slots configured non-empty (no unset-provider shortcut),
    and get_cached_analysis returns None (cache miss) so the quota guard is actually reached.
    This mirrors test_run_scan_cache_miss_calls_analyze_ticker_and_caches — the positive
    harness that proves analyze_ticker IS reached when not exhausted — making the
    assert_not_called() assertion here meaningful (non-vacuous).
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from main import run_scan

    bot = MagicMock()
    bot.send_recommendation = AsyncMock(return_value="msg_1")
    bot.send_ops_alert = AsyncMock()

    config = Config()
    config.db_path = ":memory:"
    config.dry_run = True  # keep reconciliation skipped — live mode would call Schwab
    # All three provider slots are non-empty — no unset-provider shortcut that would
    # trip the guard "for free" without actually exercising the three-way AND.
    config.analyst_provider = "gemini"
    config.analyst_fallback_provider = "deepseek"
    config.analyst_fallback2_provider = "openai"
    config.analyst_daily_limit = 18

    # Prove all three slots are set before run_scan (documents the false-green guard).
    assert config.analyst_provider == "gemini"
    assert config.analyst_fallback_provider == "deepseek"
    assert config.analyst_fallback2_provider == "openai"

    with patch("main.get_top_sp500_by_fundamentals", return_value=[]):
        with patch("main.get_universe", return_value=["AAPL"]):
            with patch("main.partition_watchlist", return_value=(["AAPL"], [])):
                with patch("main.queries.ticker_recommended_today", return_value=False):
                    with patch("main.queries.has_open_position", return_value=False):
                        with patch("main.queries.expire_stale_recommendations"):
                            with patch("main.queries.get_open_positions", return_value=[]):
                                with patch("main.yf.Ticker"):
                                    with patch("main.fetch_fundamental_info", return_value={"trailingPE": 20.0, "dividendYield": 0.03, "earningsGrowth": 0.1}):
                                        with patch("main.passes_fundamental_filter", return_value=True):
                                            with patch("main.fetch_news_headlines", return_value=["headline B"]):
                                                with patch("main.queries.get_cached_analysis", return_value=None):
                                                    # All three providers return the daily limit — guard fires.
                                                    with patch("main.queries.get_analyst_call_count_today", return_value=18):
                                                        with patch("main.queries.increment_analyst_call_count") as mock_increment:
                                                            with patch("main.analyze_ticker") as mock_analyze:
                                                                await run_scan(bot, config)
                                                                # The quota guard must have fired — analyze_ticker skipped.
                                                                mock_analyze.assert_not_called()
                                                                mock_increment.assert_not_called()


# ---------------------------------------------------------------------------
# run_scan_etf tests
# ---------------------------------------------------------------------------

def _make_etf_bot():
    """Build a minimal mock TradingBot for ETF scan tests."""
    from unittest.mock import AsyncMock, MagicMock
    bot = MagicMock()
    bot.send_etf_recommendation = AsyncMock(return_value="12345")
    bot.send_ops_alert = AsyncMock()
    return bot


def _make_etf_config():
    """Config with in-memory DB and default quota settings."""
    config = Config()
    config.db_path = ":memory:"
    config.dry_run = True  # keep reconciliation skipped — live mode would call Schwab
    config.analyst_provider = "gemini"
    config.analyst_fallback_provider = None
    config.analyst_daily_limit = 18
    return config


_ETF_TECH_DATA = {"price": 480.0, "rsi": 55.0, "ma50": 460.0, "volume": 1_200_000, "avg_volume": 1_000_000}
_ETF_ANALYSIS_BUY = {"signal": "BUY", "reasoning": "Strong trend", "provider_used": "gemini"}
_ETF_ANALYSIS_HOLD = {"signal": "HOLD", "reasoning": "Sideways", "provider_used": "gemini"}


@pytest.mark.asyncio
async def test_run_scan_etf_posts_buy_recommendation():
    """run_scan_etf posts a BUY embed and creates rec with asset_type='etf'."""
    from unittest.mock import patch, MagicMock
    from main import run_scan_etf

    bot = _make_etf_bot()
    config = _make_etf_config()

    with patch("main.get_watchlist", return_value=["SPY", "QQQ"]):
        with patch("main.partition_watchlist", return_value=([], ["SPY", "QQQ"])):
            with patch("main.queries.expire_stale_recommendations"):
                with patch("main.queries.ticker_recommended_today", return_value=False):
                    with patch("main.queries.has_open_position", return_value=False):
                        with patch("main.yf.Ticker", return_value=MagicMock()):
                            with patch("main.fetch_technical_data", return_value=_ETF_TECH_DATA):
                                with patch("main.fetch_fundamental_info", return_value={"annualReportExpenseRatio": 0.0009}):
                                    with patch("main.fetch_news_headlines", return_value=["headline1"]):
                                        with patch("main.queries.get_cached_analysis", return_value=None):
                                            with patch("main.queries.get_analyst_call_count_today", return_value=0):
                                                with patch("main.queries.increment_analyst_call_count"):
                                                    with patch("main.analyze_etf_ticker", return_value=_ETF_ANALYSIS_BUY):
                                                        with patch("main.queries.set_cached_analysis"):
                                                            with patch("main.queries.create_recommendation", return_value=1) as mock_create_rec:
                                                                with patch("main.queries.set_discord_message_id"):
                                                                    await run_scan_etf(bot, config)
                                                                    assert bot.send_etf_recommendation.call_count >= 1
                                                                    first_call = bot.send_etf_recommendation.call_args_list[0]
                                                                    assert first_call.kwargs.get("ticker") == "SPY" or first_call.kwargs.get("signal") == "BUY"
                                                                    # asset_type="etf" must be in create_recommendation call
                                                                    calls = mock_create_rec.call_args_list
                                                                    assert any(c.kwargs.get("asset_type") == "etf" for c in calls)


@pytest.mark.asyncio
async def test_run_scan_etf_skips_non_buy():
    """run_scan_etf does not post when analysis signal is HOLD."""
    from unittest.mock import patch, MagicMock
    from main import run_scan_etf

    bot = _make_etf_bot()
    config = _make_etf_config()

    with patch("main.get_watchlist", return_value=["SPY"]):
        with patch("main.partition_watchlist", return_value=([], ["SPY"])):
            with patch("main.queries.expire_stale_recommendations"):
                with patch("main.queries.ticker_recommended_today", return_value=False):
                    with patch("main.queries.has_open_position", return_value=False):
                        with patch("main.yf.Ticker", return_value=MagicMock()):
                            with patch("main.fetch_technical_data", return_value=_ETF_TECH_DATA):
                                with patch("main.fetch_fundamental_info", return_value={"annualReportExpenseRatio": 0.0009}):
                                    with patch("main.fetch_news_headlines", return_value=["headline1"]):
                                        with patch("main.queries.get_cached_analysis", return_value=None):
                                            with patch("main.queries.get_analyst_call_count_today", return_value=0):
                                                with patch("main.queries.increment_analyst_call_count"):
                                                    with patch("main.analyze_etf_ticker", return_value=_ETF_ANALYSIS_HOLD):
                                                        with patch("main.queries.set_cached_analysis"):
                                                            await run_scan_etf(bot, config)
                                                            bot.send_etf_recommendation.assert_not_called()


@pytest.mark.asyncio
async def test_run_scan_etf_skips_already_recommended():
    """run_scan_etf skips tickers already recommended today without calling analyze_etf_ticker."""
    from unittest.mock import patch, MagicMock
    from main import run_scan_etf

    bot = _make_etf_bot()
    config = _make_etf_config()

    with patch("main.get_watchlist", return_value=["SPY"]):
        with patch("main.partition_watchlist", return_value=([], ["SPY"])):
            with patch("main.queries.expire_stale_recommendations"):
                with patch("main.queries.ticker_recommended_today", return_value=True):
                    with patch("main.queries.has_open_position", return_value=False):
                        with patch("main.analyze_etf_ticker") as mock_analyze:
                            await run_scan_etf(bot, config)
                            mock_analyze.assert_not_called()
                            bot.send_etf_recommendation.assert_not_called()


@pytest.mark.asyncio
async def test_run_scan_etf_zero_recs_sends_ops_alert():
    """run_scan_etf sends an ops alert when 0 recommendations are posted."""
    from unittest.mock import patch, MagicMock
    from main import run_scan_etf

    bot = _make_etf_bot()
    config = _make_etf_config()

    with patch("main.get_watchlist", return_value=["SPY"]):
        with patch("main.partition_watchlist", return_value=([], ["SPY"])):
            with patch("main.queries.expire_stale_recommendations"):
                with patch("main.queries.ticker_recommended_today", return_value=False):
                    with patch("main.queries.has_open_position", return_value=False):
                        with patch("main.yf.Ticker", return_value=MagicMock()):
                            with patch("main.fetch_technical_data", return_value=_ETF_TECH_DATA):
                                with patch("main.fetch_fundamental_info", return_value={"annualReportExpenseRatio": 0.0009}):
                                    with patch("main.fetch_news_headlines", return_value=["headline1"]):
                                        with patch("main.queries.get_cached_analysis", return_value=None):
                                            with patch("main.queries.get_analyst_call_count_today", return_value=0):
                                                with patch("main.queries.increment_analyst_call_count"):
                                                    with patch("main.analyze_etf_ticker", return_value=_ETF_ANALYSIS_HOLD):
                                                        with patch("main.queries.set_cached_analysis"):
                                                            await run_scan_etf(bot, config)
                                                            bot.send_ops_alert.assert_called_once()
                                                            alert_msg = bot.send_ops_alert.call_args[0][0]
                                                            assert "ETF scan complete: 0" in alert_msg


# ---------------------------------------------------------------------------
# Confidence wiring tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_scan_passes_confidence_to_recommendation():
    """run_scan passes confidence from analyze_ticker result to create_recommendation."""
    from unittest.mock import AsyncMock, MagicMock, patch, call
    from main import run_scan

    bot = MagicMock()
    bot.send_recommendation = AsyncMock(return_value="msg_1")
    bot.send_ops_alert = AsyncMock()

    config = Config()
    config.db_path = ":memory:"
    config.dry_run = True  # keep reconciliation skipped — live mode would call Schwab

    analysis_result = {
        "signal": "BUY",
        "reasoning": "Strong fundamentals.",
        "provider_used": "gemini",
        "confidence": "high",
    }

    with patch("main.get_top_sp500_by_fundamentals", return_value=[]):
        with patch("main.get_universe", return_value=["AAPL"]):
            with patch("main.partition_watchlist", return_value=(["AAPL"], [])):
                with patch("main.queries.ticker_recommended_today", return_value=False):
                    with patch("main.queries.has_open_position", return_value=False):
                        with patch("main.queries.expire_stale_recommendations"):
                            with patch("main.queries.get_open_positions", return_value=[]):
                                with patch("main.yf.Ticker"):
                                    with patch("main.fetch_fundamental_info", return_value={"trailingPE": 20.0, "dividendYield": 0.03, "earningsGrowth": 0.1}):
                                        with patch("main.passes_fundamental_filter", return_value=True):
                                            with patch("main.fetch_news_headlines", return_value=["headline A"]):
                                                with patch("main.queries.get_cached_analysis", return_value=None):
                                                    with patch("main.queries.get_analyst_call_count_today", return_value=0):
                                                        with patch("main.queries.increment_analyst_call_count"):
                                                            with patch("main.analyze_ticker", return_value=analysis_result):
                                                                with patch("main.queries.set_cached_analysis") as mock_set_cache:
                                                                    with patch("main.fetch_technical_data", return_value={"price": 150.0, "rsi": 60.0, "ma50": 140.0, "volume_ratio": 1.2}):
                                                                        with patch("main.passes_technical_filter", return_value=True):
                                                                            with patch("main.queries.create_recommendation", return_value=1) as mock_create_rec:
                                                                                with patch("main.queries.set_discord_message_id"):
                                                                                    await run_scan(bot, config)
                                                                                    # create_recommendation called with confidence="high"
                                                                                    assert mock_create_rec.called
                                                                                    kwargs = mock_create_rec.call_args.kwargs
                                                                                    assert kwargs.get("confidence") == "high"
                                                                                    # send_recommendation called with confidence="high"
                                                                                    assert bot.send_recommendation.called
                                                                                    send_kwargs = bot.send_recommendation.call_args.kwargs
                                                                                    assert send_kwargs.get("confidence") == "high"
                                                                                    # set_cached_analysis called with confidence="high"
                                                                                    assert mock_set_cache.called
                                                                                    cache_kwargs = mock_set_cache.call_args.kwargs
                                                                                    assert cache_kwargs.get("confidence") == "high"


@pytest.mark.asyncio
async def test_run_scan_passes_none_confidence_when_missing():
    """run_scan passes confidence=None when analyze_ticker result has no confidence key."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from main import run_scan

    bot = MagicMock()
    bot.send_recommendation = AsyncMock(return_value="msg_1")
    bot.send_ops_alert = AsyncMock()

    config = Config()
    config.db_path = ":memory:"
    config.dry_run = True  # keep reconciliation skipped — live mode would call Schwab

    # analysis_result without confidence key (older path)
    analysis_result = {
        "signal": "BUY",
        "reasoning": "Strong.",
        "provider_used": "gemini",
    }

    with patch("main.get_top_sp500_by_fundamentals", return_value=[]):
        with patch("main.get_universe", return_value=["MSFT"]):
            with patch("main.partition_watchlist", return_value=(["MSFT"], [])):
                with patch("main.queries.ticker_recommended_today", return_value=False):
                    with patch("main.queries.has_open_position", return_value=False):
                        with patch("main.queries.expire_stale_recommendations"):
                            with patch("main.queries.get_open_positions", return_value=[]):
                                with patch("main.yf.Ticker"):
                                    with patch("main.fetch_fundamental_info", return_value={"trailingPE": 25.0, "dividendYield": 0.01, "earningsGrowth": 0.05}):
                                        with patch("main.passes_fundamental_filter", return_value=True):
                                            with patch("main.fetch_news_headlines", return_value=["headline X"]):
                                                with patch("main.queries.get_cached_analysis", return_value=None):
                                                    with patch("main.queries.get_analyst_call_count_today", return_value=0):
                                                        with patch("main.queries.increment_analyst_call_count"):
                                                            with patch("main.analyze_ticker", return_value=analysis_result):
                                                                with patch("main.queries.set_cached_analysis"):
                                                                    with patch("main.fetch_technical_data", return_value={"price": 300.0, "rsi": 58.0, "ma50": 290.0, "volume_ratio": 1.1}):
                                                                        with patch("main.passes_technical_filter", return_value=True):
                                                                            with patch("main.queries.create_recommendation", return_value=2) as mock_create_rec:
                                                                                with patch("main.queries.set_discord_message_id"):
                                                                                    await run_scan(bot, config)
                                                                                    kwargs = mock_create_rec.call_args.kwargs
                                                                                    assert kwargs.get("confidence") is None


@pytest.mark.asyncio
async def test_run_scan_etf_passes_confidence_to_recommendation():
    """run_scan_etf passes confidence from analyze_etf_ticker result to create_recommendation."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from main import run_scan_etf

    bot = _make_etf_bot()
    config = _make_etf_config()

    analysis_with_confidence = {
        "signal": "BUY",
        "reasoning": "Strong ETF trend.",
        "provider_used": "gemini",
        "confidence": "medium",
    }

    with patch("main.get_watchlist", return_value=["SPY"]):
        with patch("main.partition_watchlist", return_value=([], ["SPY"])):
            with patch("main.queries.expire_stale_recommendations"):
                with patch("main.queries.ticker_recommended_today", return_value=False):
                    with patch("main.queries.has_open_position", return_value=False):
                        with patch("main.yf.Ticker", return_value=MagicMock()):
                            with patch("main.fetch_technical_data", return_value=_ETF_TECH_DATA):
                                with patch("main.fetch_fundamental_info", return_value={"netExpenseRatio": 0.0003}):
                                    with patch("main.fetch_news_headlines", return_value=["headline1"]):
                                        with patch("main.queries.get_cached_analysis", return_value=None):
                                            with patch("main.queries.get_analyst_call_count_today", return_value=0):
                                                with patch("main.queries.increment_analyst_call_count"):
                                                    with patch("main.analyze_etf_ticker", return_value=analysis_with_confidence):
                                                        with patch("main.queries.set_cached_analysis") as mock_set_cache:
                                                            with patch("main.queries.create_recommendation", return_value=1) as mock_create_rec:
                                                                with patch("main.queries.set_discord_message_id"):
                                                                    await run_scan_etf(bot, config)
                                                                    kwargs = mock_create_rec.call_args.kwargs
                                                                    assert kwargs.get("confidence") == "medium"
                                                                    send_kwargs = bot.send_etf_recommendation.call_args.kwargs
                                                                    assert send_kwargs.get("confidence") == "medium"
                                                                    cache_kwargs = mock_set_cache.call_args.kwargs
                                                                    assert cache_kwargs.get("confidence") == "medium"


# --- compute_headline_hash (cache-key salting for empty news feeds) ---

def test_headline_hash_stable_for_same_headlines():
    from main import compute_headline_hash
    h1 = compute_headline_hash(["Fed cuts rates", "Apple beats estimates"])
    h2 = compute_headline_hash(["Apple beats estimates", "Fed cuts rates"])
    assert h1 == h2  # order-insensitive (sorted before hashing)


def test_headline_hash_differs_for_different_headlines():
    from main import compute_headline_hash
    assert compute_headline_hash(["A"]) != compute_headline_hash(["B"])


def test_headline_hash_empty_list_salted_with_date():
    """An empty feed must not produce the same key as any real headline set,
    and must roll daily so a broken news pipeline can't pin a cache entry forever."""
    from unittest.mock import patch
    from datetime import date
    from main import compute_headline_hash

    with patch("main.date") as mock_date:
        mock_date.today.return_value = date(2026, 6, 11)
        h_day1 = compute_headline_hash([])
    with patch("main.date") as mock_date:
        mock_date.today.return_value = date(2026, 6, 12)
        h_day2 = compute_headline_hash([])
    assert h_day1 != h_day2


# --- configure_scheduler: SCAN_TIMEZONE ---

def test_scheduler_uses_configured_timezone():
    cfg = Config()
    cfg.scan_times = ["09:30"]
    cfg.scan_timezone = "America/New_York"
    scheduler = BackgroundScheduler()
    configure_scheduler(scheduler, cfg, _dummy_job)
    job = scheduler.get_jobs()[0]
    assert str(job.trigger.timezone) == "America/New_York"


def test_scheduler_falls_back_to_local_timezone_when_unset():
    cfg = Config()
    cfg.scan_times = ["09:30"]
    cfg.scan_timezone = ""
    scheduler = BackgroundScheduler()
    configure_scheduler(scheduler, cfg, _dummy_job)
    job = scheduler.get_jobs()[0]
    # Empty setting must not crash and must resolve to a concrete (local) timezone
    assert job.trigger.timezone is not None
