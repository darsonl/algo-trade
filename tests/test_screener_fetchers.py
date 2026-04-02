import pandas as pd
import pytest
from unittest.mock import MagicMock
from screener.technicals import fetch_technical_data, MIN_HISTORY_BARS
from screener.fundamentals import fetch_fundamental_info


# --- helpers ---

def _make_price_df(n: int, start: float = 100.0, step: float = 0.5) -> pd.DataFrame:
    """Return a DataFrame with n rows of monotonically increasing Close prices and flat Volume."""
    prices = [start + i * step for i in range(n)]
    return pd.DataFrame({"Close": prices, "Volume": [1_000_000] * n})


def _make_mock_ticker(df: pd.DataFrame) -> MagicMock:
    mock = MagicMock()
    mock.history.return_value = df
    return mock


# --- fetch_technical_data (TEST-05) ---

def test_fetch_technical_data_happy_path_returns_all_keys():
    df = _make_price_df(MIN_HISTORY_BARS + 10)
    result = fetch_technical_data(_make_mock_ticker(df))
    assert set(result.keys()) == {"rsi", "price", "ma50", "volume", "avg_volume"}
    assert result["price"] is not None
    assert result["rsi"] is not None
    assert result["ma50"] is not None
    assert result["volume"] == 1_000_000


def test_fetch_technical_data_price_is_last_close():
    df = _make_price_df(MIN_HISTORY_BARS + 5, start=100.0, step=1.0)
    result = fetch_technical_data(_make_mock_ticker(df))
    expected_price = df["Close"].iloc[-1]
    assert abs(result["price"] - expected_price) < 0.01


def test_fetch_technical_data_insufficient_data_returns_all_none():
    df = _make_price_df(20)  # well below MIN_HISTORY_BARS (51)
    result = fetch_technical_data(_make_mock_ticker(df))
    assert all(v is None for v in result.values())


def test_fetch_technical_data_exact_min_history_bars_passes():
    df = _make_price_df(MIN_HISTORY_BARS)
    result = fetch_technical_data(_make_mock_ticker(df))
    assert result["price"] is not None


def test_fetch_technical_data_empty_dataframe_returns_all_none():
    result = fetch_technical_data(_make_mock_ticker(pd.DataFrame()))
    assert all(v is None for v in result.values())


def test_fetch_technical_data_one_below_min_returns_all_none():
    df = _make_price_df(MIN_HISTORY_BARS - 1)
    result = fetch_technical_data(_make_mock_ticker(df))
    assert all(v is None for v in result.values())


# --- fetch_fundamental_info (TEST-06) ---

def test_fetch_fundamental_info_returns_info_dict():
    info = {"trailingPE": 22.5, "dividendYield": 0.03, "earningsGrowth": 0.08}
    mock = MagicMock()
    mock.info = info
    result = fetch_fundamental_info(mock)
    assert result == info


def test_fetch_fundamental_info_handles_empty_dict():
    mock = MagicMock()
    mock.info = {}
    result = fetch_fundamental_info(mock)
    assert result == {}


def test_fetch_fundamental_info_returns_partial_dict():
    """yfinance may omit fields -- fetch_fundamental_info returns whatever .info gives."""
    info = {"trailingPE": 18.0}
    mock = MagicMock()
    mock.info = info
    result = fetch_fundamental_info(mock)
    assert result["trailingPE"] == 18.0
    assert "dividendYield" not in result
