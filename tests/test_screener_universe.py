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


def test_partition_watchlist_populates_info_sink():
    """info_sink captures the fetched .info per ticker so the caller can reuse it
    instead of fetching the (heavy) .info a second time."""
    def mock_ticker(t):
        m = MagicMock()
        m.info = {"quoteType": "ETF" if t == "SPY" else "EQUITY", "trailingPE": 21.0}
        return m

    info_sink = {}
    with patch("screener.universe.yf") as mock_yf:
        mock_yf.Ticker.side_effect = mock_ticker
        stocks, etfs = partition_watchlist(["AAPL", "SPY"], info_sink)

    # Return value unchanged (2-tuple); sink populated for every successfully-fetched ticker.
    assert stocks == ["AAPL"] and etfs == ["SPY"]
    assert set(info_sink) == {"AAPL", "SPY"}
    assert info_sink["AAPL"]["trailingPE"] == 21.0
    assert info_sink["SPY"]["quoteType"] == "ETF"


def test_partition_watchlist_info_sink_omits_failed_tickers():
    """Tickers that hit the allowlist fallback (raised before .info) are absent from
    info_sink, so the caller knows to fetch them itself."""
    def mock_ticker(t):
        if t == "SPY":
            raise Exception("timeout")
        m = MagicMock()
        m.info = {"quoteType": "EQUITY"}
        return m

    info_sink = {}
    with patch("screener.universe.yf") as mock_yf:
        mock_yf.Ticker.side_effect = mock_ticker
        partition_watchlist(["AAPL", "SPY"], info_sink)

    assert "AAPL" in info_sink
    assert "SPY" not in info_sink


def test_get_watchlist_reads_etf_watchlist(tmp_path):
    """Test 5: get_watchlist reads etf_watchlist.txt correctly."""
    p = tmp_path / "etf_watchlist.txt"
    p.write_text("# ETF watchlist\nSPY\nQQQ\nVTI\n")
    tickers = get_watchlist(str(p))
    assert tickers == ["SPY", "QQQ", "VTI"]


# --- get_top_sp500_by_fundamentals rank-sum scoring (C2) ---

def test_get_top_sp500_uses_rank_sum_not_value_sum(monkeypatch):
    """Rank-sum surfaces a balanced high-ROE name over a high-EPS / low-ROE name that the
    old `eps + roe` score ranked first (EPS dollar magnitude dominated the sum)."""
    from types import SimpleNamespace
    import screener.universe as u

    data = {
        "MEGA_EPS": {"trailingEps": 1000.0, "returnOnEquity": 0.01},
        "BAL1":     {"trailingEps": 50.0,   "returnOnEquity": 0.40},
        "BAL2":     {"trailingEps": 40.0,   "returnOnEquity": 0.35},
        "BAL3":     {"trailingEps": 30.0,   "returnOnEquity": 0.30},
    }

    def mock_ticker(t):
        m = MagicMock()
        m.info = data[t]
        return m

    u._top_sp500_cache = {}  # bypass the in-memory daily cache
    monkeypatch.setattr(u, "get_sp500_tickers", lambda: list(data))
    monkeypatch.setattr(u.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(u.yf, "Ticker", mock_ticker)

    top = u.get_top_sp500_by_fundamentals(SimpleNamespace(top_sp500_count=1))

    # EPS ranks: MEGA_EPS=1, BAL1=2, BAL2=3, BAL3=4. ROE ranks: BAL1=1, BAL2=2, BAL3=3,
    # MEGA_EPS=4. Composites: BAL1=3 (best), MEGA_EPS=5, BAL2=5, BAL3=7 -> rank-sum picks BAL1.
    assert top == ["BAL1"]
    # The OLD eps+roe formula would have picked MEGA_EPS — proves behavior genuinely changed.
    old_winner = max(data, key=lambda t: data[t]["trailingEps"] + data[t]["returnOnEquity"])
    assert old_winner == "MEGA_EPS"


def test_rank_desc_handles_ties():
    """_rank_desc gives tied values the same (competition) rank."""
    from screener.universe import _rank_desc
    ranks = _rank_desc([(10.0, "A"), (10.0, "B"), (5.0, "C")])
    assert ranks["A"] == 1 and ranks["B"] == 1  # tie share rank 1
    assert ranks["C"] == 3  # 1 + two strictly-greater values
