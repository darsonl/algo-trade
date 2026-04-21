import pytest
import pandas as pd
from unittest.mock import MagicMock, PropertyMock
from screener.fundamentals import passes_fundamental_filter, fetch_eps_data
from config import Config


@pytest.fixture
def cfg():
    c = Config()
    c.min_dividend_yield = 0.02
    c.max_pe_ratio = 25.0
    c.min_earnings_growth = 0.05
    return c


def make_info(pe=15.0, div_yield=0.03, earnings_growth=0.10):
    return {
        "trailingPE": pe,
        "dividendYield": div_yield,
        "earningsGrowth": earnings_growth,
    }


def test_passes_when_all_criteria_met(cfg):
    assert passes_fundamental_filter(make_info(), cfg) is True


def test_fails_when_pe_too_high(cfg):
    assert passes_fundamental_filter(make_info(pe=30.0), cfg) is False


def test_fails_when_dividend_yield_too_low(cfg):
    assert passes_fundamental_filter(make_info(div_yield=0.005), cfg) is False


def test_fails_when_earnings_growth_too_low(cfg):
    assert passes_fundamental_filter(make_info(earnings_growth=0.02), cfg) is False


def test_fails_when_pe_is_none(cfg):
    info = make_info()
    info["trailingPE"] = None
    assert passes_fundamental_filter(info, cfg) is False


def test_passes_when_dividend_yield_is_none(cfg):
    # dividendYield=None skips the yield check — non-dividend payers are allowed
    info = make_info()
    info["dividendYield"] = None
    assert passes_fundamental_filter(info, cfg) is True


def test_passes_when_earnings_growth_is_none(cfg):
    # earningsGrowth=None skips the growth check — analyst decides instead
    info = make_info()
    info["earningsGrowth"] = None
    assert passes_fundamental_filter(info, cfg) is True


def test_passes_at_exact_boundary(cfg):
    # Exactly at limits should pass
    assert passes_fundamental_filter(
        make_info(pe=25.0, div_yield=0.02, earnings_growth=0.05), cfg
    ) is True


# ---------------------------------------------------------------------------
# fetch_eps_data tests (SIG-08)
# ---------------------------------------------------------------------------

def _make_income_stmt(eps_values, row_name="Diluted EPS"):
    """Build quarterly_income_stmt DataFrame with newest-first columns (yfinance order).
    eps_values is a list of 4 values in newest-first order.
    """
    timestamps = [
        pd.Timestamp("2025-12-31"),
        pd.Timestamp("2025-09-30"),
        pd.Timestamp("2025-06-30"),
        pd.Timestamp("2025-03-31"),
    ]
    return pd.DataFrame([eps_values], index=[row_name], columns=timestamps)


def test_fetch_eps_data_returns_4_quarters_chronological():
    """Test A: returns 4 quarters in oldest-first order."""
    mock_ticker = MagicMock()
    # newest-first order: Q4=0.61, Q3=0.48, Q2=0.52, Q1=0.45
    type(mock_ticker).quarterly_income_stmt = PropertyMock(
        return_value=_make_income_stmt([0.61, 0.48, 0.52, 0.45])
    )
    result = fetch_eps_data(mock_ticker)
    assert result is not None
    assert len(result) == 4
    assert result[0] == {"quarter": "Q1-2025", "eps": pytest.approx(0.45)}
    assert result[3] == {"quarter": "Q4-2025", "eps": pytest.approx(0.61)}


def test_fetch_eps_data_returns_none_when_stmt_none():
    """Test B: returns None when quarterly_income_stmt is None."""
    mock_ticker = MagicMock()
    type(mock_ticker).quarterly_income_stmt = PropertyMock(return_value=None)
    result = fetch_eps_data(mock_ticker)
    assert result is None


def test_fetch_eps_data_returns_none_when_stmt_empty():
    """Test C: returns None when quarterly_income_stmt is an empty DataFrame."""
    mock_ticker = MagicMock()
    type(mock_ticker).quarterly_income_stmt = PropertyMock(
        return_value=pd.DataFrame()
    )
    result = fetch_eps_data(mock_ticker)
    assert result is None


def test_fetch_eps_data_returns_none_when_diluted_eps_missing_row():
    """Test D: returns None when DataFrame has no 'Diluted EPS' row (only 'Basic EPS')."""
    mock_ticker = MagicMock()
    type(mock_ticker).quarterly_income_stmt = PropertyMock(
        return_value=_make_income_stmt([0.61, 0.48, 0.52, 0.45], row_name="Basic EPS")
    )
    result = fetch_eps_data(mock_ticker)
    assert result is None


def test_fetch_eps_data_filters_nan_quarters():
    """Test E: filters out NaN quarters; returns remaining entries in order."""
    import math
    mock_ticker = MagicMock()
    # Q3 (index position 1 in newest-first = 2025-09-30) is NaN
    type(mock_ticker).quarterly_income_stmt = PropertyMock(
        return_value=_make_income_stmt([0.61, float("nan"), 0.52, 0.45])
    )
    result = fetch_eps_data(mock_ticker)
    assert result is not None
    assert len(result) == 3
    # None of the returned values should be NaN
    for entry in result:
        assert not math.isnan(entry["eps"])
    # Should be in chronological order
    quarters = [e["quarter"] for e in result]
    assert quarters == sorted(quarters)


def test_fetch_eps_data_returns_none_when_all_nan():
    """Test F: returns None when all quarters are NaN."""
    mock_ticker = MagicMock()
    type(mock_ticker).quarterly_income_stmt = PropertyMock(
        return_value=_make_income_stmt(
            [float("nan"), float("nan"), float("nan"), float("nan")]
        )
    )
    result = fetch_eps_data(mock_ticker)
    assert result is None


def test_fetch_eps_data_swallows_exception():
    """Test G: returns None (no exception) when quarterly_income_stmt raises RuntimeError."""
    mock_ticker = MagicMock()
    type(mock_ticker).quarterly_income_stmt = PropertyMock(
        side_effect=RuntimeError("yfinance network error")
    )
    result = fetch_eps_data(mock_ticker)
    assert result is None
