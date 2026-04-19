import pytest
from contextlib import contextmanager, ExitStack
from unittest.mock import AsyncMock, MagicMock, patch
from config import Config
from main import run_scan, run_scan_etf


def _make_bot():
    bot = MagicMock()
    bot.send_recommendation = AsyncMock(return_value="msg_999")
    bot.send_ops_alert = AsyncMock()
    return bot


def _make_config():
    c = Config()
    c.db_path = ":memory:"
    return c


@contextmanager
def _full_patch(
    *,
    universe=("AAPL",),
    recommended_today=False,
    fundamental_pass=True,
    analysis=None,
    technical_pass=True,
    rec_id=1,
):
    analysis = analysis or {"signal": "BUY", "reasoning": "Strong.", "provider_used": "gemini"}
    fund_info = {"trailingPE": 20.0, "dividendYield": 0.03, "earningsGrowth": 0.10}
    tech_data = {
        "price": 150.0, "rsi": 55.0, "ma50": 140.0,
        "volume": 1_200_000, "avg_volume": 1_000_000,
    }

    patches = [
        patch("main.get_top_sp500_by_fundamentals", return_value=[]),
        patch("main.get_universe", return_value=list(universe)),
        patch("main.queries.expire_stale_recommendations"),
        patch("main.queries.ticker_recommended_today", return_value=recommended_today),
        patch("main.queries.has_open_position", return_value=False),
        patch("main.queries.get_open_positions", return_value=[]),
        patch("main.yf.Ticker"),
        patch("main.create_analyst_client", return_value=MagicMock()),
        patch("main.fetch_macro_context", return_value={"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"}),
        patch("main.fetch_fundamental_info", return_value=fund_info),
        patch("main.passes_fundamental_filter", return_value=fundamental_pass),
        patch("main.fetch_news_headlines", return_value=["headline A"]),
        patch("main.queries.get_cached_analysis", return_value=None),
        patch("main.queries.get_analyst_call_count_today", return_value=0),
        patch("main.queries.increment_analyst_call_count"),
        patch("main.analyze_ticker", return_value=analysis),
        patch("main.queries.set_cached_analysis"),
        patch("main.fetch_technical_data", return_value=tech_data),
        patch("main.passes_technical_filter", return_value=technical_pass),
        patch("main.queries.create_recommendation", return_value=rec_id),
        patch("main.queries.set_discord_message_id"),
    ]

    with ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        yield {
            "fetch_fundamental_info": mocks[9],
            "analyze_ticker": mocks[15],
            "create_recommendation": mocks[19],
            "set_discord_message_id": mocks[20],
            "ticker_recommended_today": mocks[3],
            "has_open_position": mocks[4],
        }


@pytest.mark.asyncio
async def test_run_scan_happy_path_posts_recommendation():
    """Full happy path: one ticker passes all filters -> recommendation posted, no ops alert."""
    bot = _make_bot()
    config = _make_config()
    with _full_patch() as mocks:
        await run_scan(bot, config)
    bot.send_recommendation.assert_awaited_once()
    bot.send_ops_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_scan_skips_already_recommended_ticker():
    """ticker_recommended_today=True -> fetch_fundamental_info never called."""
    bot = _make_bot()
    config = _make_config()
    with _full_patch(recommended_today=True) as mocks:
        await run_scan(bot, config)
        mocks["fetch_fundamental_info"].assert_not_called()
    bot.send_recommendation.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_scan_skips_on_fundamental_filter_failure():
    """passes_fundamental_filter=False -> analyze_ticker never called."""
    bot = _make_bot()
    config = _make_config()
    with _full_patch(fundamental_pass=False) as mocks:
        await run_scan(bot, config)
        mocks["analyze_ticker"].assert_not_called()
    bot.send_recommendation.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_scan_skips_on_technical_filter_failure():
    """BUY signal but passes_technical_filter=False -> no recommendation posted."""
    bot = _make_bot()
    config = _make_config()
    with _full_patch(technical_pass=False):
        await run_scan(bot, config)
    bot.send_recommendation.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_scan_continues_after_ticker_error():
    """Exception processing first ticker -> second ticker still processed."""
    bot = _make_bot()
    config = _make_config()
    fund_info = {"trailingPE": 20.0, "dividendYield": 0.03, "earningsGrowth": 0.10}
    call_count = {"n": 0}

    def fund_side_effect(yf_ticker):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("yfinance timeout")
        return fund_info

    with _full_patch(universe=("FAIL", "AAPL")) as mocks:
        mocks["fetch_fundamental_info"].side_effect = fund_side_effect
        await run_scan(bot, config)

    bot.send_recommendation.assert_awaited_once()  # AAPL went through


@pytest.mark.asyncio
async def test_run_scan_zero_recommendations_sends_ops_alert():
    """All tickers fail fundamental filter -> recommendations_posted=0 -> ops alert."""
    bot = _make_bot()
    config = _make_config()
    with _full_patch(fundamental_pass=False):
        await run_scan(bot, config)
    bot.send_ops_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_scan_sets_discord_message_id_after_posting():
    """Discord message id returned by send_recommendation is stored in DB."""
    bot = _make_bot()
    bot.send_recommendation = AsyncMock(return_value="discord_msg_42")
    config = _make_config()
    with _full_patch() as mocks:
        await run_scan(bot, config)
    call_args = mocks["set_discord_message_id"].call_args[0]
    assert "discord_msg_42" in call_args


# --- open-position skip guard (POS-06) ---

@pytest.mark.asyncio
async def test_run_scan_skips_open_position():
    """has_open_position=True for first ticker -> fetch_fundamental_info not called for it."""
    bot = _make_bot()
    config = _make_config()

    def has_open_pos_side_effect(db_path, ticker):
        return ticker == "TSLA"

    with _full_patch(universe=("TSLA", "AAPL")) as mocks:
        mocks["has_open_position"].side_effect = has_open_pos_side_effect
        await run_scan(bot, config)

    # fetch_fundamental_info should only be called for AAPL (once), not TSLA
    assert mocks["fetch_fundamental_info"].call_count == 1


@pytest.mark.asyncio
async def test_run_scan_allows_ticker_without_position():
    """has_open_position=False -> ticker proceeds to fetch_fundamental_info."""
    bot = _make_bot()
    config = _make_config()

    with _full_patch(universe=("AAPL",)) as mocks:
        mocks["has_open_position"].return_value = False
        await run_scan(bot, config)

    mocks["fetch_fundamental_info"].assert_called_once()


# --- G-3: run_scan excludes ETFs from stock universe ---

@pytest.mark.asyncio
async def test_run_scan_excludes_etfs_from_stock_universe():
    """partition_watchlist returns stocks=["AAPL"], etfs=["SPY"] — analyze_ticker must not
    be called for SPY (only AAPL enters the stock scan loop)."""
    from contextlib import ExitStack

    bot = _make_bot()
    config = _make_config()

    analysis = {"signal": "BUY", "reasoning": "Strong.", "provider_used": "gemini"}
    fund_info = {"trailingPE": 20.0, "dividendYield": 0.03, "earningsGrowth": 0.10}
    tech_data = {
        "price": 150.0, "rsi": 55.0, "ma50": 140.0,
        "volume": 1_200_000, "avg_volume": 1_000_000,
    }

    patches = [
        patch("main.get_top_sp500_by_fundamentals", return_value=[]),
        patch("main.get_universe", return_value=["AAPL", "SPY"]),
        patch("main.partition_watchlist", return_value=(["AAPL"], ["SPY"])),
        patch("main.queries.expire_stale_recommendations"),
        patch("main.queries.ticker_recommended_today", return_value=False),
        patch("main.queries.has_open_position", return_value=False),
        patch("main.queries.get_open_positions", return_value=[]),
        patch("main.yf.Ticker"),
        patch("main.create_analyst_client", return_value=MagicMock()),
        patch("main.create_fallback_client", return_value=None),
        patch("main.fetch_macro_context", return_value={"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"}),
        patch("main.fetch_fundamental_info", return_value=fund_info),
        patch("main.passes_fundamental_filter", return_value=True),
        patch("main.fetch_news_headlines", return_value=["headline A"]),
        patch("main.queries.get_cached_analysis", return_value=None),
        patch("main.queries.get_analyst_call_count_today", return_value=0),
        patch("main.queries.increment_analyst_call_count"),
        patch("main.analyze_ticker", return_value=analysis),
        patch("main.queries.set_cached_analysis"),
        patch("main.fetch_technical_data", return_value=tech_data),
        patch("main.passes_technical_filter", return_value=True),
        patch("main.queries.create_recommendation", return_value=1),
        patch("main.queries.set_discord_message_id"),
    ]

    with ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        m_partition = mocks[2]
        m_analyze = mocks[17]
        await run_scan(bot, config)

    # partition_watchlist was called (the ETF filtering step ran)
    m_partition.assert_called_once()

    # analyze_ticker was called exactly once — only for AAPL, not SPY
    assert m_analyze.call_count == 1
    called_ticker = m_analyze.call_args[0][0]
    assert called_ticker == "AAPL", f"Expected analyze_ticker called for AAPL, got {called_ticker!r}"


# --- Macro context wiring tests ---

@pytest.mark.asyncio
async def test_run_scan_macro_fetch_failure_scan_continues():
    """fetch_macro_context raises Exception — scan completes without macro (D-06)."""
    bot = _make_bot()
    config = _make_config()
    with _full_patch() as mocks:
        with patch("main.fetch_macro_context", side_effect=Exception("network error")):
            await run_scan(bot, config)
    # Scan still posts recommendation despite macro failure
    bot.send_recommendation.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_scan_passes_macro_to_analyze_ticker():
    """analyze_ticker is called with macro_context kwarg containing the fetched macro data."""
    bot = _make_bot()
    config = _make_config()
    expected_macro = {"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"}
    with _full_patch() as mocks:
        await run_scan(bot, config)
    call_kwargs = mocks["analyze_ticker"].call_args[1]
    assert call_kwargs.get("macro_context") == expected_macro


@pytest.mark.asyncio
async def test_run_scan_passes_none_macro_to_analyze_ticker_on_fetch_failure():
    """When macro fetch fails, analyze_ticker receives macro_context with None values."""
    bot = _make_bot()
    config = _make_config()
    with _full_patch() as mocks:
        with patch("main.fetch_macro_context", side_effect=Exception("timeout")):
            await run_scan(bot, config)
    call_kwargs = mocks["analyze_ticker"].call_args[1]
    macro = call_kwargs.get("macro_context")
    assert macro == {"spy_trend_1m": None, "spy_trend_1y": None, "vix_level": None}
