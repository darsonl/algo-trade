"""Tests for position reconciliation (RISK-05) — diff, report, orchestration, /reconcile."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from config import Config
from schwab_client.reconcile import diff_positions, format_reconciliation_report
from main import run_reconciliation


# --- helpers ---

def _db_pos(ticker="AAPL", shares=10.0):
    return {"ticker": ticker, "shares": shares}


def _broker_pos(symbol="AAPL", quantity=10.0, asset_type="EQUITY"):
    return {
        "symbol": symbol,
        "quantity": quantity,
        "avg_price": 100.0,
        "market_value": quantity * 100.0,
        "asset_type": asset_type,
    }


def _clean(diff):
    return not (diff["phantom"] or diff["untracked"] or diff["mismatched"])


# --- diff_positions ---

def test_diff_matching_positions_is_clean():
    diff = diff_positions([_db_pos()], [_broker_pos()])
    assert _clean(diff)


def test_diff_both_empty_is_clean():
    assert _clean(diff_positions([], []))


def test_diff_db_position_missing_at_broker_is_phantom():
    diff = diff_positions([_db_pos("AAPL", 10)], [])
    assert diff["phantom"] == [{"ticker": "AAPL", "db_shares": 10}]
    assert not diff["untracked"] and not diff["mismatched"]


def test_diff_zero_broker_quantity_is_phantom():
    diff = diff_positions([_db_pos("AAPL", 10)], [_broker_pos("AAPL", quantity=0.0)])
    assert [p["ticker"] for p in diff["phantom"]] == ["AAPL"]


def test_diff_broker_position_missing_in_db_is_untracked():
    diff = diff_positions([], [_broker_pos("MSFT", 5)])
    assert diff["untracked"] == [{"ticker": "MSFT", "broker_shares": 5}]
    assert not diff["phantom"] and not diff["mismatched"]


def test_diff_share_count_difference_is_mismatched():
    diff = diff_positions([_db_pos("AAPL", 10)], [_broker_pos("AAPL", quantity=7.0)])
    assert diff["mismatched"] == [
        {"ticker": "AAPL", "db_shares": 10, "broker_shares": 7.0}
    ]
    assert not diff["phantom"] and not diff["untracked"]


def test_diff_cash_equivalent_not_reported_untracked():
    """Sweep/money-market instruments at the broker are never bot-managed."""
    diff = diff_positions([], [_broker_pos("SWVXX", 500, asset_type="MUTUAL_FUND"),
                               _broker_pos("ZFV919", 100, asset_type="CASH_EQUIVALENT")])
    assert diff["untracked"] == []


def test_diff_unknown_asset_type_still_reported_untracked():
    # Empty asset_type (older parse output) must not hide real holdings
    diff = diff_positions([], [_broker_pos("NVDA", 3, asset_type="")])
    assert [u["ticker"] for u in diff["untracked"]] == ["NVDA"]


def test_diff_non_equity_still_matches_db_position():
    """A DB ticker held at the broker under any asset type is not phantom."""
    diff = diff_positions(
        [_db_pos("VOO", 2)], [_broker_pos("VOO", 2, asset_type="COLLECTIVE_INVESTMENT")]
    )
    assert _clean(diff)


def test_diff_ticker_match_is_case_insensitive():
    diff = diff_positions([_db_pos("aapl", 10)], [_broker_pos("AAPL", 10)])
    assert _clean(diff)


# --- format_reconciliation_report ---

def test_report_none_when_clean():
    diff = diff_positions([_db_pos()], [_broker_pos()])
    assert format_reconciliation_report(diff) is None


def test_report_contains_all_sections_and_tickers():
    diff = {
        "phantom": [{"ticker": "AAPL", "db_shares": 10.0}],
        "untracked": [{"ticker": "MSFT", "broker_shares": 5.0}],
        "mismatched": [{"ticker": "JNJ", "db_shares": 4.0, "broker_shares": 2.0}],
    }
    report = format_reconciliation_report(diff)
    assert "PHANTOM AAPL" in report
    assert "UNTRACKED MSFT" in report
    assert "MISMATCH JNJ" in report
    assert "No automatic changes" in report


def test_report_formats_whole_shares_without_trailing_zero():
    diff = {"phantom": [{"ticker": "AAPL", "db_shares": 10.0}], "untracked": [], "mismatched": []}
    assert "10 share(s)" in format_reconciliation_report(diff)


# --- run_reconciliation ---

def _make_config(dry_run=False):
    c = Config()
    c.db_path = ":memory:"
    c.dry_run = dry_run
    return c


def _make_bot():
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    return bot


@pytest.mark.asyncio
@patch("main.get_positions")
async def test_run_reconciliation_skipped_in_dry_run(mock_get_positions):
    result = await run_reconciliation(_make_bot(), _make_config(dry_run=True))
    assert "skipped" in result.lower()
    mock_get_positions.assert_not_called()


@pytest.mark.asyncio
@patch("main.queries.get_open_positions", return_value=[])
@patch("main.get_positions", side_effect=RuntimeError("auth expired"))
async def test_run_reconciliation_fetch_failure_returns_message(mock_get_positions, mock_db):
    bot = _make_bot()
    result = await run_reconciliation(bot, _make_config())
    assert "failed" in result.lower()


@pytest.mark.asyncio
@patch("main.queries.get_open_positions", return_value=[])
@patch("main.get_positions", side_effect=RuntimeError("auth expired"))
async def test_run_reconciliation_fetch_failure_posts_ops_alert(mock_get_positions, mock_db):
    """A safety monitor that cannot run must say so.

    This previously logged a warning and returned quietly. Reconciliation is the
    RISK-05 monitor: silent failure manufactures confidence rather than removing
    it, and a get_positions crash went unnoticed for months because of it.
    """
    bot = _make_bot()
    await run_reconciliation(bot, _make_config())
    bot.send_ops_alert.assert_awaited_once()


@pytest.mark.asyncio
@patch("main.queries.get_open_positions", return_value=[])
@patch("main.get_positions", side_effect=RuntimeError("auth expired"))
async def test_run_reconciliation_fetch_failure_alert_names_the_cause(mock_get_positions, mock_db):
    """The alert must carry the error, so the channel is actionable without the logs."""
    bot = _make_bot()
    await run_reconciliation(bot, _make_config())
    assert "auth expired" in bot.send_ops_alert.call_args[0][0]


@pytest.mark.asyncio
@patch("main.queries.get_open_positions", return_value=[])
@patch("main.get_positions", side_effect=RuntimeError("auth expired"))
async def test_run_reconciliation_fetch_failure_distinguishable_from_drift(
    mock_get_positions, mock_db
):
    """'Could not check' must not read as 'checked and found drift'."""
    bot = _make_bot()
    await run_reconciliation(bot, _make_config())
    message = bot.send_ops_alert.call_args[0][0]
    assert "PHANTOM" not in message and "UNTRACKED" not in message
    assert "could not" in message.lower()


@pytest.mark.asyncio
@patch("main.queries.get_open_positions", return_value=[])
@patch("main.get_positions", side_effect=RuntimeError("auth expired"))
async def test_run_reconciliation_fetch_failure_silent_when_alerts_suppressed(
    mock_get_positions, mock_db
):
    """/reconcile renders the result itself, so it must not also be alerted."""
    bot = _make_bot()
    await run_reconciliation(bot, _make_config(), alert_on_discrepancy=False)
    bot.send_ops_alert.assert_not_awaited()


@pytest.mark.asyncio
@patch("main.queries.get_open_positions", return_value=[_db_pos("AAPL", 10)])
@patch("main.get_positions", return_value=[_broker_pos("AAPL", 10)])
async def test_run_reconciliation_clean_no_alert(mock_get_positions, mock_db):
    bot = _make_bot()
    result = await run_reconciliation(bot, _make_config())
    assert "clean" in result.lower()
    bot.send_ops_alert.assert_not_awaited()


@pytest.mark.asyncio
@patch("main.queries.get_open_positions", return_value=[_db_pos("AAPL", 10)])
@patch("main.get_positions", return_value=[])
async def test_run_reconciliation_discrepancy_posts_ops_alert(mock_get_positions, mock_db):
    bot = _make_bot()
    result = await run_reconciliation(bot, _make_config())
    assert "PHANTOM AAPL" in result
    bot.send_ops_alert.assert_awaited_once()
    assert "PHANTOM AAPL" in bot.send_ops_alert.call_args[0][0]


@pytest.mark.asyncio
@patch("main.queries.get_open_positions", return_value=[_db_pos("AAPL", 10)])
@patch("main.get_positions", return_value=[])
async def test_run_reconciliation_alert_suppressed_for_command_path(mock_get_positions, mock_db):
    """alert_on_discrepancy=False (the /reconcile path) returns the report without an ops alert."""
    bot = _make_bot()
    result = await run_reconciliation(bot, _make_config(), alert_on_discrepancy=False)
    assert "PHANTOM AAPL" in result
    bot.send_ops_alert.assert_not_awaited()


# --- /reconcile slash command ---

def _make_interaction():
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_reconcile_command_sends_callback_result():
    from discord_bot.bot import TradingBot
    bot = MagicMock()
    bot._reconcile_callback = AsyncMock(return_value="Reconciliation clean: 2 open position(s) match Schwab.")
    interaction = _make_interaction()

    await TradingBot._reconcile_command(bot, interaction)

    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once_with(
        "Reconciliation clean: 2 open position(s) match Schwab."
    )


@pytest.mark.asyncio
async def test_reconcile_command_without_callback():
    from discord_bot.bot import TradingBot
    bot = MagicMock()
    bot._reconcile_callback = None
    interaction = _make_interaction()

    await TradingBot._reconcile_command(bot, interaction)

    interaction.response.send_message.assert_awaited_once()
    assert "not configured" in interaction.response.send_message.call_args[0][0]
