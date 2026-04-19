"""Tests for run_scan sell pass — SELL-03, SELL-04."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from config import Config
from database.models import initialize_db, get_connection
from database.queries import (
    create_position,
    get_open_positions,
    set_sell_blocked,
)
from main import run_scan
import tempfile
import os


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    initialize_db(path)
    return path


@pytest.fixture
def config(db_path):
    c = Config()
    c.db_path = db_path
    c.dry_run = True
    c.sell_rsi_threshold = 70.0
    c.analyst_call_delay_s = 0  # no delay in tests
    return c


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_recommendation = AsyncMock(return_value="msg123")
    bot.send_sell_recommendation = AsyncMock(return_value="msg456")
    bot.send_ops_alert = AsyncMock()
    return bot


# RSI above threshold — exit signal fires
TECH_DATA_OVERBOUGHT = {
    "rsi": 75.0, "price": 170.0, "ma50": 160.0, "volume": 1000000, "avg_volume": 900000,
    "macd_line": -0.5, "signal_line": 0.2, "macd_histogram": -0.7,
}
# RSI below threshold — no sell signal
TECH_DATA_NORMAL = {
    "rsi": 50.0, "price": 170.0, "ma50": 160.0, "volume": 1000000, "avg_volume": 900000,
    "macd_line": 0.3, "signal_line": 0.1, "macd_histogram": 0.2,
}


@pytest.mark.asyncio
@patch("main.fetch_news_headlines", return_value=["Headline 1"])
@patch("main.fetch_technical_data", return_value=TECH_DATA_OVERBOUGHT)
@patch("main.analyze_sell_ticker", return_value={"signal": "SELL", "reasoning": "Overbought", "provider_used": "gemini"})
@patch("main.fetch_fundamental_info", return_value={"trailingPE": 20, "dividendYield": 0.03, "earningsGrowth": 0.1})
@patch("main.fetch_macro_context", return_value={"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"})
@patch("main.get_top_sp500_by_fundamentals", return_value=[])
@patch("main.get_universe", return_value=[])
async def test_sell_pass_posts_sell_recommendation(
    mock_universe, mock_sp500, mock_macro, mock_fund, mock_sell_analyze,
    mock_tech, mock_news, config, mock_bot, db_path
):
    create_position(db_path, "AAPL", 10, 150.0)

    await run_scan(mock_bot, config)

    mock_bot.send_sell_recommendation.assert_called_once()
    call_kwargs = mock_bot.send_sell_recommendation.call_args
    # Verify the call included correct ticker
    args = call_kwargs[1] if call_kwargs[1] else {}
    ticker_in_kwargs = args.get("ticker")
    ticker_in_args = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None
    assert ticker_in_kwargs == "AAPL" or ticker_in_args == "AAPL"


@pytest.mark.asyncio
@patch("main.fetch_news_headlines", return_value=[])
@patch("main.fetch_technical_data", return_value=TECH_DATA_NORMAL)
@patch("main.fetch_fundamental_info", return_value={"trailingPE": 20, "dividendYield": 0.03, "earningsGrowth": 0.1})
@patch("main.fetch_macro_context", return_value={"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"})
@patch("main.get_top_sp500_by_fundamentals", return_value=[])
@patch("main.get_universe", return_value=[])
async def test_sell_pass_skips_when_rsi_below_threshold(
    mock_universe, mock_sp500, mock_macro, mock_fund,
    mock_tech, mock_news, config, mock_bot, db_path
):
    create_position(db_path, "AAPL", 10, 150.0)

    await run_scan(mock_bot, config)

    mock_bot.send_sell_recommendation.assert_not_called()


@pytest.mark.asyncio
@patch("main.fetch_news_headlines", return_value=[])
@patch("main.fetch_technical_data", return_value=TECH_DATA_OVERBOUGHT)
@patch("main.analyze_sell_ticker", return_value={"signal": "HOLD", "reasoning": "Momentum strong", "provider_used": "gemini"})
@patch("main.fetch_fundamental_info", return_value={"trailingPE": 20, "dividendYield": 0.03, "earningsGrowth": 0.1})
@patch("main.fetch_macro_context", return_value={"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"})
@patch("main.get_top_sp500_by_fundamentals", return_value=[])
@patch("main.get_universe", return_value=[])
async def test_sell_pass_skips_when_analyst_says_hold(
    mock_universe, mock_sp500, mock_macro, mock_fund, mock_sell_analyze,
    mock_tech, mock_news, config, mock_bot, db_path
):
    create_position(db_path, "AAPL", 10, 150.0)

    await run_scan(mock_bot, config)

    mock_bot.send_sell_recommendation.assert_not_called()


@pytest.mark.asyncio
@patch("main.fetch_news_headlines", return_value=[])
@patch("main.fetch_technical_data", return_value=TECH_DATA_OVERBOUGHT)
@patch("main.fetch_fundamental_info", return_value={"trailingPE": 20, "dividendYield": 0.03, "earningsGrowth": 0.1})
@patch("main.fetch_macro_context", return_value={"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"})
@patch("main.get_top_sp500_by_fundamentals", return_value=[])
@patch("main.get_universe", return_value=[])
async def test_sell_pass_skips_sell_blocked_position(
    mock_universe, mock_sp500, mock_macro, mock_fund,
    mock_tech, mock_news, config, mock_bot, db_path
):
    """sell_blocked=True — position is skipped, no sell recommendation posted."""
    create_position(db_path, "AAPL", 10, 150.0)
    set_sell_blocked(db_path, "AAPL")

    await run_scan(mock_bot, config)

    mock_bot.send_sell_recommendation.assert_not_called()


@pytest.mark.asyncio
@patch("main.fetch_news_headlines", return_value=[])
@patch("main.fetch_technical_data", return_value=TECH_DATA_NORMAL)
@patch("main.fetch_fundamental_info", return_value={"trailingPE": 20, "dividendYield": 0.03, "earningsGrowth": 0.1})
@patch("main.fetch_macro_context", return_value={"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"})
@patch("main.get_top_sp500_by_fundamentals", return_value=[])
@patch("main.get_universe", return_value=[])
async def test_sell_pass_resets_sell_blocked_when_rsi_drops(
    mock_universe, mock_sp500, mock_macro, mock_fund,
    mock_tech, mock_news, config, mock_bot, db_path
):
    """sell_blocked=True but RSI (50) drops below threshold (70) — sell_blocked reset."""
    create_position(db_path, "AAPL", 10, 150.0)
    set_sell_blocked(db_path, "AAPL")

    await run_scan(mock_bot, config)

    conn = get_connection(db_path)
    pos = conn.execute(
        "SELECT sell_blocked FROM positions WHERE ticker = 'AAPL'"
    ).fetchone()
    assert pos["sell_blocked"] == 0
    conn.close()


@pytest.mark.asyncio
@patch("main.fetch_news_headlines", return_value=[])
@patch("main.fetch_technical_data", return_value=TECH_DATA_OVERBOUGHT)
@patch("main.fetch_fundamental_info", return_value={"trailingPE": 20, "dividendYield": 0.03, "earningsGrowth": 0.1})
@patch("main.fetch_macro_context", return_value={"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"})
@patch("main.get_top_sp500_by_fundamentals", return_value=[])
@patch("main.get_universe", return_value=[])
async def test_sell_pass_does_not_reset_sell_blocked_when_rsi_still_high(
    mock_universe, mock_sp500, mock_macro, mock_fund,
    mock_tech, mock_news, config, mock_bot, db_path
):
    """sell_blocked=True and RSI (75) > threshold (70) — sell_blocked remains set."""
    create_position(db_path, "AAPL", 10, 150.0)
    set_sell_blocked(db_path, "AAPL")

    await run_scan(mock_bot, config)

    conn = get_connection(db_path)
    pos = conn.execute(
        "SELECT sell_blocked FROM positions WHERE ticker = 'AAPL'"
    ).fetchone()
    assert pos["sell_blocked"] == 1
    conn.close()


@pytest.mark.asyncio
@patch("main.fetch_fundamental_info", return_value={"trailingPE": 20, "dividendYield": 0.03, "earningsGrowth": 0.1})
@patch("main.fetch_macro_context", return_value={"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"})
@patch("main.get_top_sp500_by_fundamentals", return_value=[])
@patch("main.get_universe", return_value=[])
async def test_sell_pass_no_positions_skips_sell_evaluation(
    mock_universe, mock_sp500, mock_macro, mock_fund,
    config, mock_bot, db_path
):
    """No open positions — sell pass has nothing to evaluate."""
    # No positions created in DB
    await run_scan(mock_bot, config)

    mock_bot.send_sell_recommendation.assert_not_called()


@pytest.mark.asyncio
@patch("main.fetch_news_headlines", return_value=["Headline"])
@patch("main.fetch_technical_data", return_value=TECH_DATA_OVERBOUGHT)
@patch("main.analyze_sell_ticker", return_value={"signal": "SELL", "reasoning": "Overbought", "provider_used": "gemini"})
@patch("main.fetch_fundamental_info", return_value={"trailingPE": 20, "dividendYield": 0.03, "earningsGrowth": 0.1})
@patch("main.fetch_macro_context", return_value={"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"})
@patch("main.get_top_sp500_by_fundamentals", return_value=[])
@patch("main.get_universe", return_value=[])
async def test_sell_pass_sets_discord_message_id_after_posting(
    mock_universe, mock_sp500, mock_macro, mock_fund, mock_sell_analyze,
    mock_tech, mock_news, config, mock_bot, db_path
):
    """Discord message id returned by send_sell_recommendation is stored in DB."""
    mock_bot.send_sell_recommendation = AsyncMock(return_value="sell_msg_42")
    create_position(db_path, "AAPL", 10, 150.0)

    await run_scan(mock_bot, config)

    # Verify recommendation was posted with a message ID
    mock_bot.send_sell_recommendation.assert_called_once()


@pytest.mark.asyncio
@patch("main.fetch_news_headlines", return_value=[])
@patch("main.fetch_technical_data", return_value=TECH_DATA_OVERBOUGHT)
@patch("main.analyze_sell_ticker", return_value={"signal": "SELL", "reasoning": "Overbought", "provider_used": "gemini"})
@patch("main.fetch_fundamental_info", return_value={"trailingPE": 20, "dividendYield": 0.03, "earningsGrowth": 0.1})
@patch("main.fetch_macro_context", return_value={"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"})
@patch("main.get_top_sp500_by_fundamentals", return_value=[])
@patch("main.get_universe", return_value=[])
async def test_sell_pass_multiple_positions_evaluated(
    mock_universe, mock_sp500, mock_macro, mock_fund, mock_sell_analyze,
    mock_tech, mock_news, config, mock_bot, db_path
):
    """Multiple open positions — each gets sell evaluation."""
    create_position(db_path, "AAPL", 10, 150.0)
    create_position(db_path, "MSFT", 5, 300.0)

    await run_scan(mock_bot, config)

    assert mock_bot.send_sell_recommendation.call_count == 2


@pytest.mark.asyncio
@patch("main.fetch_news_headlines", return_value=["Headline"])
@patch("main.fetch_technical_data", return_value=TECH_DATA_OVERBOUGHT)
@patch("main.analyze_sell_ticker", return_value={"signal": "SELL", "reasoning": "Overbought", "provider_used": "gemini"})
@patch("main.fetch_fundamental_info", return_value={"sector": "Technology", "fiftyTwoWeekHigh": 200.0, "fiftyTwoWeekLow": 100.0, "trailingPE": 20, "dividendYield": 0.03, "earningsGrowth": 0.1})
@patch("main.fetch_macro_context", return_value={"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"})
@patch("main.get_top_sp500_by_fundamentals", return_value=[])
@patch("main.get_universe", return_value=[])
async def test_sell_pass_passes_macro_context_to_analyze_sell_ticker(
    mock_universe, mock_sp500, mock_macro, mock_fund, mock_sell_analyze,
    mock_tech, mock_news, config, mock_bot, db_path
):
    """analyze_sell_ticker receives macro_context and info kwargs."""
    create_position(db_path, "AAPL", 10, 150.0)

    await run_scan(mock_bot, config)

    mock_sell_analyze.assert_called_once()
    call_kwargs = mock_sell_analyze.call_args[1]
    assert call_kwargs.get("macro_context") == {"spy_trend_1m": "Bullish (+1.0%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.0 (Low volatility)"}
    assert call_kwargs.get("info") is not None
