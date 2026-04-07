import os
import pytest
from screener.universe import get_watchlist, get_universe


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
