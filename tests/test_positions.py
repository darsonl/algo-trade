import pytest
import sqlite3
import os
from unittest.mock import patch, MagicMock
from database.models import initialize_db
from database.queries import (
    create_position,
    update_position,
    get_open_positions,
    has_open_position,
    close_position,
    upsert_position,
)

DB_PATH = "test_positions.db"


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def test_initialize_db_creates_positions_table():
    """positions table must exist after initialize_db runs."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()
    assert "positions" in tables


def test_create_position_inserts_row_and_returns_id():
    """create_position inserts a row with status='open' and returns a positive id."""
    pos_id = create_position(DB_PATH, "AAPL", shares=10, avg_cost_usd=150.0)
    assert pos_id > 0
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM positions WHERE ticker = 'AAPL'").fetchone()
    conn.close()
    assert row is not None
    assert row["ticker"] == "AAPL"
    assert row["shares"] == 10
    assert row["avg_cost_usd"] == 150.0
    assert row["status"] == "open"


def test_create_position_reentry_after_close():
    """create_position on a closed position re-opens it via ON CONFLICT upsert."""
    create_position(DB_PATH, "MSFT", shares=5, avg_cost_usd=300.0)
    close_position(DB_PATH, "MSFT")
    # Verify it's closed before re-entry
    assert has_open_position(DB_PATH, "MSFT") is False
    # Re-enter with new values
    create_position(DB_PATH, "MSFT", shares=8, avg_cost_usd=310.0)
    assert has_open_position(DB_PATH, "MSFT") is True
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM positions WHERE ticker = 'MSFT' AND status = 'open'"
    ).fetchone()
    conn.close()
    assert row["shares"] == 8
    assert row["avg_cost_usd"] == 310.0


def test_update_position_weighted_avg_cost():
    """Weighted avg cost: 5@$100 + 5@$120 = $110.00 avg, 10 shares total."""
    create_position(DB_PATH, "GOOG", shares=5, avg_cost_usd=100.0)
    update_position(DB_PATH, "GOOG", new_shares=5, buy_price=120.0)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT shares, avg_cost_usd FROM positions WHERE ticker = 'GOOG'"
    ).fetchone()
    conn.close()
    assert row["shares"] == 10.0
    assert abs(row["avg_cost_usd"] - 110.0) < 0.01


def test_get_open_positions_returns_only_open():
    """get_open_positions returns only rows with status='open'."""
    create_position(DB_PATH, "AAPL", shares=10, avg_cost_usd=150.0)
    create_position(DB_PATH, "MSFT", shares=5, avg_cost_usd=300.0)
    create_position(DB_PATH, "GOOG", shares=2, avg_cost_usd=180.0)
    close_position(DB_PATH, "GOOG")
    open_positions = get_open_positions(DB_PATH)
    assert len(open_positions) == 2
    tickers = {row["ticker"] for row in open_positions}
    assert "AAPL" in tickers
    assert "MSFT" in tickers
    assert "GOOG" not in tickers


def test_has_open_position_returns_true_for_open():
    """has_open_position returns True when an open position exists."""
    create_position(DB_PATH, "AAPL", shares=10, avg_cost_usd=150.0)
    assert has_open_position(DB_PATH, "AAPL") is True


def test_has_open_position_returns_false_for_closed():
    """has_open_position returns False when position is closed."""
    create_position(DB_PATH, "AAPL", shares=10, avg_cost_usd=150.0)
    close_position(DB_PATH, "AAPL")
    assert has_open_position(DB_PATH, "AAPL") is False


def test_has_open_position_returns_false_for_nonexistent():
    """has_open_position returns False when ticker has no record at all."""
    assert has_open_position(DB_PATH, "NVDA") is False


def test_close_position_sets_status_closed():
    """close_position sets status='closed' on an open position row."""
    create_position(DB_PATH, "JNJ", shares=3, avg_cost_usd=160.0)
    assert has_open_position(DB_PATH, "JNJ") is True
    close_position(DB_PATH, "JNJ")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status FROM positions WHERE ticker = 'JNJ'"
    ).fetchone()
    conn.close()
    assert row["status"] == "closed"


def test_upsert_position_creates_when_no_open_position():
    """upsert_position calls create_position when no open position exists."""
    upsert_position(DB_PATH, "VYM", shares=4, price=110.0)
    assert has_open_position(DB_PATH, "VYM") is True
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT shares, avg_cost_usd FROM positions WHERE ticker = 'VYM' AND status = 'open'"
    ).fetchone()
    conn.close()
    assert row["shares"] == 4
    assert row["avg_cost_usd"] == 110.0


def test_upsert_position_updates_when_open_position_exists():
    """upsert_position calls update_position when an open position already exists."""
    create_position(DB_PATH, "VYM", shares=4, avg_cost_usd=100.0)
    upsert_position(DB_PATH, "VYM", shares=4, price=120.0)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT shares, avg_cost_usd FROM positions WHERE ticker = 'VYM' AND status = 'open'"
    ).fetchone()
    conn.close()
    # 4@100 + 4@120 = 8 shares at $110 avg
    assert row["shares"] == 8.0
    assert abs(row["avg_cost_usd"] - 110.0) < 0.01


# ---------------------------------------------------------------------------
# Plan 03: get_position_summary and build_positions_embed tests
# ---------------------------------------------------------------------------

def _make_row(ticker, shares, avg_cost, last_price=None):
    """Create a mock sqlite3.Row-like dict for position rows."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "ticker": ticker,
        "shares": shares,
        "avg_cost_usd": avg_cost,
        "last_price": last_price,
    }[key]
    return row


@patch("screener.positions.get_open_positions")
@patch("screener.positions.yf")
def test_get_position_summary_computes_pnl(mock_yf, mock_get_open):
    """get_position_summary fetches current price and computes P&L% correctly."""
    row1 = _make_row("AAPL", 5, 100.0)
    row2 = _make_row("MSFT", 3, 100.0)
    mock_get_open.return_value = [row1, row2]

    ticker1 = MagicMock()
    ticker1.fast_info.last_price = 110.0
    ticker2 = MagicMock()
    ticker2.fast_info.last_price = 90.0
    mock_yf.Ticker.side_effect = [ticker1, ticker2]

    from screener.positions import get_position_summary
    results = get_position_summary("any.db")

    assert len(results) == 2
    assert results[0]["ticker"] == "AAPL"
    assert results[0]["pnl_pct"] == pytest.approx(0.1)
    assert results[1]["ticker"] == "MSFT"
    assert results[1]["pnl_pct"] == pytest.approx(-0.1)


@patch("screener.positions.get_open_positions")
@patch("screener.positions.yf")
def test_get_position_summary_yfinance_fallback(mock_yf, mock_get_open):
    """When yfinance raises, falls back to last_price from DB row."""
    row = _make_row("GOOG", 2, 100.0, last_price=105.0)
    mock_get_open.return_value = [row]
    mock_yf.Ticker.side_effect = Exception("network error")

    from screener.positions import get_position_summary
    results = get_position_summary("any.db")

    assert len(results) == 1
    assert results[0]["current_price"] == 105.0
    assert results[0]["pnl_pct"] == pytest.approx(0.05)


@patch("screener.positions.get_open_positions")
@patch("screener.positions.yf")
def test_get_position_summary_no_price_available(mock_yf, mock_get_open):
    """When yfinance raises and last_price is None, pnl_pct and current_price are None."""
    row = _make_row("NVDA", 1, 200.0, last_price=None)
    mock_get_open.return_value = [row]
    mock_yf.Ticker.side_effect = Exception("timeout")

    from screener.positions import get_position_summary
    results = get_position_summary("any.db")

    assert len(results) == 1
    assert results[0]["current_price"] is None
    assert results[0]["pnl_pct"] is None


@patch("screener.positions.get_open_positions")
def test_get_position_summary_empty(mock_get_open):
    """get_position_summary returns empty list when no open positions exist."""
    mock_get_open.return_value = []

    from screener.positions import get_position_summary
    results = get_position_summary("any.db")

    assert results == []


def test_build_positions_embed_with_data():
    """build_positions_embed produces embed with title 'Open Positions' and one field per position."""
    from discord_bot.embeds import build_positions_embed
    summaries = [
        {"ticker": "AAPL", "shares": 5, "avg_cost_usd": 100.0, "current_price": 110.0, "pnl_pct": 0.1},
        {"ticker": "MSFT", "shares": 3, "avg_cost_usd": 200.0, "current_price": 190.0, "pnl_pct": -0.05},
    ]
    embed = build_positions_embed(summaries)
    assert embed.title == "Open Positions"
    assert len(embed.fields) == 2
    assert embed.fields[0].name == "AAPL"
    assert embed.fields[1].name == "MSFT"


def test_build_positions_embed_empty():
    """build_positions_embed with empty list sets description to 'No open positions.'"""
    from discord_bot.embeds import build_positions_embed
    embed = build_positions_embed([])
    assert embed.description == "No open positions."
    assert len(embed.fields) == 0
