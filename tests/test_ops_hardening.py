"""Tests for ops hardening — error counter, cap, overflow, ETF prefix (OPS-01, ETF-08)."""
import contextlib
import pytest
from unittest.mock import AsyncMock, patch
from config import Config
from database.models import initialize_db
from main import run_scan, run_scan_etf
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
    c.analyst_call_delay_s = 0
    return c


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_recommendation = AsyncMock(return_value="msg123")
    bot.send_etf_recommendation = AsyncMock(return_value="msg456")
    bot.send_ops_alert = AsyncMock()
    return bot


@contextlib.contextmanager
def stock_scan_patches(tickers, fund_side_effect):
    """Patch all run_scan dependencies — buy pass raises fund_side_effect per ticker."""
    with (
        patch("main.get_top_sp500_by_fundamentals", return_value=[]),
        patch("main.get_universe", return_value=tickers),
        patch("main.partition_watchlist", return_value=(tickers, [])),
        patch("main.queries.expire_stale_recommendations"),
        patch("main.queries.ticker_recommended_today", return_value=False),
        patch("main.queries.has_open_position", return_value=False),
        patch("main.queries.get_open_positions", return_value=[]),
        patch("main.create_analyst_client"),
        patch("main.create_fallback_client"),
        patch("main.fetch_fundamental_info", side_effect=fund_side_effect),
    ):
        yield


@contextlib.contextmanager
def etf_scan_patches(tickers, tech_side_effect):
    """Patch all run_scan_etf dependencies — loop raises tech_side_effect per ticker."""
    with (
        patch("main.get_watchlist", return_value=tickers),
        patch("main.partition_watchlist", return_value=([], tickers)),
        patch("main.queries.expire_stale_recommendations"),
        patch("main.queries.ticker_recommended_today", return_value=False),
        patch("main.queries.has_open_position", return_value=False),
        patch("main.create_analyst_client"),
        patch("main.create_fallback_client"),
        patch("main.fetch_technical_data", side_effect=tech_side_effect),
    ):
        yield


def _error_alerts(mock_bot):
    return [c.args[0] for c in mock_bot.send_ops_alert.call_args_list
            if c.args and "[ERROR]" in c.args[0]]


def _overflow_alerts(mock_bot):
    return [c.args[0] for c in mock_bot.send_ops_alert.call_args_list
            if c.args and "more errors not shown" in c.args[0]]


def _zero_rec_alerts(mock_bot):
    return [c.args[0] for c in mock_bot.send_ops_alert.call_args_list
            if c.args and "0 recommendations posted" in c.args[0]]


# ─── Stock scan error counter tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_stock_1_error_posts_1_alert(config, mock_bot):
    """Test 1: 1 failing ticker posts exactly 1 error alert with correct format."""
    with stock_scan_patches(["FAIL1"], ValueError("boom")):
        await run_scan(mock_bot, config)

    alerts = _error_alerts(mock_bot)
    assert len(alerts) == 1
    assert alerts[0] == "[ERROR] FAIL1: ValueError"


@pytest.mark.asyncio
async def test_stock_3_errors_posts_3_alerts_no_overflow(config, mock_bot):
    """Test 2: 3 failing tickers → exactly 3 error alerts, no overflow message."""
    with stock_scan_patches(["FAIL1", "FAIL2", "FAIL3"], ValueError("boom")):
        await run_scan(mock_bot, config)

    assert len(_error_alerts(mock_bot)) == 3
    assert len(_overflow_alerts(mock_bot)) == 0


@pytest.mark.asyncio
async def test_stock_5_errors_posts_3_alerts_and_overflow(config, mock_bot):
    """Test 3: 5 failing tickers → 3 error alerts + 1 overflow summary."""
    tickers = ["FAIL1", "FAIL2", "FAIL3", "FAIL4", "FAIL5"]
    with stock_scan_patches(tickers, ValueError("boom")):
        await run_scan(mock_bot, config)

    assert len(_error_alerts(mock_bot)) == 3
    overflow = _overflow_alerts(mock_bot)
    assert len(overflow) == 1
    assert "[2 more errors not shown" in overflow[0]


@pytest.mark.asyncio
async def test_stock_0_errors_posts_no_error_alerts(config, mock_bot):
    """Test 4: Empty universe → no error alerts posted."""
    with stock_scan_patches([], ValueError("boom")):
        await run_scan(mock_bot, config)

    assert len(_error_alerts(mock_bot)) == 0


# ─── ETF scan error counter tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_etf_5_errors_posts_3_alerts_and_overflow(config, mock_bot):
    """Test 5: ETF scan with 5 errors follows same 3-cap + overflow behavior."""
    tickers = ["ETF1", "ETF2", "ETF3", "ETF4", "ETF5"]
    with etf_scan_patches(tickers, ValueError("boom")):
        await run_scan_etf(mock_bot, config)

    assert len(_error_alerts(mock_bot)) == 3
    overflow = _overflow_alerts(mock_bot)
    assert len(overflow) == 1
    assert "[2 more errors not shown" in overflow[0]


# ─── Zero-rec alert prefix tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_etf_zero_rec_alert_has_etf_prefix(config, mock_bot):
    """Test 6: ETF zero-rec alert message starts with [ETF]."""
    with etf_scan_patches([], None):
        await run_scan_etf(mock_bot, config)

    zero_rec = _zero_rec_alerts(mock_bot)
    assert len(zero_rec) == 1
    assert zero_rec[0].startswith("[ETF]")


@pytest.mark.asyncio
async def test_stock_zero_rec_alert_has_no_etf_prefix(config, mock_bot):
    """Test 7: Stock zero-rec alert does NOT start with [ETF]."""
    with stock_scan_patches([], ValueError("boom")):
        await run_scan(mock_bot, config)

    zero_rec = _zero_rec_alerts(mock_bot)
    assert len(zero_rec) == 1
    assert not zero_rec[0].startswith("[ETF]")


# ─── Error message format test ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_error_alert_contains_type_not_message_detail(config, mock_bot):
    """Test 8: Alert contains exception type name only — not the full exception message."""
    with stock_scan_patches(["FAIL1"], ConnectionError("some detail message")):
        await run_scan(mock_bot, config)

    alerts = _error_alerts(mock_bot)
    assert len(alerts) == 1
    assert "ConnectionError" in alerts[0]
    assert "some detail message" not in alerts[0]
