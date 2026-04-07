import os
import pytest
from unittest.mock import patch, MagicMock
from screener.universe import get_watchlist, get_universe, partition_watchlist


@pytest.fixture
def tmp_watchlist(tmp_path):
    p = tmp_path / "watchlist.txt"
    p.write_text("# comment\nAAPL\n\nJNJ\n VYM \n")
    return str(p)


def test_get_watchlist_returns_tickers(tmp_watchlist):
    tickers = get_watchlist(tmp_watchlist)
    assert "AAPL" in tickers
    assert "JNJ" in tickers


def test_get_watchlist_strips_whitespace(tmp_watchlist):
    tickers = get_watchlist(tmp_watchlist)
    assert "VYM" in tickers
    assert " VYM " not in tickers


def test_get_watchlist_skips_comments_and_blank_lines(tmp_watchlist):
    tickers = get_watchlist(tmp_watchlist)
    assert all(not t.startswith("#") for t in tickers)
    assert all(t != "" for t in tickers)
    assert len(tickers) == 3


def test_get_watchlist_empty_file(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("# just a comment\n\n")
    assert get_watchlist(str(p)) == []


def test_get_universe_deduplicates(tmp_path):
    p = tmp_path / "watchlist.txt"
    p.write_text("AAPL\nMSFT\n")
    # Inject a fake sp500 list that overlaps with watchlist
    extra = ["MSFT", "GOOG"]
    universe = get_universe(str(p), extra_tickers=extra)
    assert universe.count("MSFT") == 1
    assert "AAPL" in universe
    assert "GOOG" in universe


def test_get_universe_returns_uppercase(tmp_path):
    p = tmp_path / "watchlist.txt"
    p.write_text("aapl\n")
    universe = get_universe(str(p), extra_tickers=[])
    assert "AAPL" in universe


# --- partition_watchlist tests ---

def test_partition_watchlist_classifies_via_yfinance():
    """Test 1: partition_watchlist uses yfinance quoteType to classify tickers."""
    def mock_ticker(t):
        m = MagicMock()
        if t == "SPY":
            m.info = {"quoteType": "ETF"}
        else:
            m.info = {"quoteType": "EQUITY"}
        return m

    with patch("screener.universe.yf") as mock_yf:
        mock_yf.Ticker.side_effect = mock_ticker
        stocks, etfs = partition_watchlist(["AAPL", "SPY"])

    assert stocks == ["AAPL"]
    assert etfs == ["SPY"]


def test_partition_watchlist_falls_back_to_allowlist_on_exception():
    """Test 2: partition_watchlist falls back to _ETF_ALLOWLIST when yfinance raises."""
    with patch("screener.universe.yf") as mock_yf:
        mock_yf.Ticker.side_effect = Exception("network error")
        stocks, etfs = partition_watchlist(["SPY", "AAPL"])

    # SPY is in _ETF_ALLOWLIST → goes to etfs; AAPL is not → goes to stocks
    assert "SPY" in etfs
    assert "AAPL" in stocks


def test_partition_watchlist_handles_mixed_availability():
    """Test 3: partition_watchlist handles some tickers raising, some returning quoteType."""
    def mock_ticker(t):
        if t == "SPY":
            raise Exception("timeout")
        m = MagicMock()
        if t == "QQQ":
            m.info = {"quoteType": "ETF"}
        else:
            m.info = {"quoteType": "EQUITY"}
        return m

    with patch("screener.universe.yf") as mock_yf:
        mock_yf.Ticker.side_effect = mock_ticker
        stocks, etfs = partition_watchlist(["AAPL", "SPY", "QQQ"])

    # SPY fails → allowlist fallback → etfs; QQQ → quoteType ETF → etfs; AAPL → stocks
    assert "AAPL" in stocks
    assert "SPY" in etfs
    assert "QQQ" in etfs


def test_partition_watchlist_empty_input():
    """Test 4: partition_watchlist returns empty lists for empty input."""
    with patch("screener.universe.yf"):
        stocks, etfs = partition_watchlist([])

    assert stocks == []
    assert etfs == []


def test_get_watchlist_reads_etf_watchlist(tmp_path):
    """Test 5: get_watchlist reads etf_watchlist.txt correctly."""
    p = tmp_path / "etf_watchlist.txt"
    p.write_text("# ETF watchlist\nSPY\nQQQ\nVTI\n")
    tickers = get_watchlist(str(p))
    assert tickers == ["SPY", "QQQ", "VTI"]
