import pytest
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from config import Config
from main import run_scan


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
    analysis = analysis or {"signal": "BUY", "reasoning": "Strong."}
    fund_info = {"trailingPE": 20.0, "dividendYield": 0.03, "earningsGrowth": 0.10}
    tech_data = {
        "price": 150.0, "rsi": 55.0, "ma50": 140.0,
        "volume": 1_200_000, "avg_volume": 1_000_000,
    }

    with patch("main.get_top_sp500_by_fundamentals", return_value=[]), \
         patch("main.get_universe", return_value=list(universe)), \
         patch("main.queries.expire_stale_recommendations"), \
         patch("main.queries.ticker_recommended_today", return_value=recommended_today) as m_today, \
         patch("main.queries.has_open_position", return_value=False) as m_open_pos, \
         patch("main.yf.Ticker"), \
         patch("main.create_analyst_client", return_value=MagicMock()), \
         patch("main.fetch_fundamental_info", return_value=fund_info) as m_fund, \
         patch("main.passes_fundamental_filter", return_value=fundamental_pass), \
         patch("main.fetch_news_headlines", return_value=["headline A"]), \
         patch("main.queries.get_cached_analysis", return_value=None), \
         patch("main.analyze_ticker", return_value=analysis) as m_analyze, \
         patch("main.queries.set_cached_analysis"), \
         patch("main.fetch_technical_data", return_value=tech_data), \
         patch("main.passes_technical_filter", return_value=technical_pass), \
         patch("main.queries.create_recommendation", return_value=rec_id) as m_create_rec, \
         patch("main.queries.set_discord_message_id") as m_set_msg:
        yield {
            "fetch_fundamental_info": m_fund,
            "analyze_ticker": m_analyze,
            "create_recommendation": m_create_rec,
            "set_discord_message_id": m_set_msg,
            "ticker_recommended_today": m_today,
            "has_open_position": m_open_pos,
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
