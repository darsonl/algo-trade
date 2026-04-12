"""Tests for screener/macro.py — macro context fetcher for SPY trend and VIX level."""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from screener.macro import (
    format_spy_trend,
    format_vix_level,
    compute_52w_position,
    fetch_macro_context,
)


# --- format_spy_trend ---

def test_format_spy_trend_bullish():
    result = format_spy_trend(0.032)
    assert result == "Bullish (+3.2%)"


def test_format_spy_trend_bearish():
    result = format_spy_trend(-0.014)
    assert result == "Bearish (-1.4%)"


def test_format_spy_trend_zero():
    """Zero return is treated as bullish (>= 0)."""
    result = format_spy_trend(0.0)
    assert result.startswith("Bullish")


def test_format_spy_trend_large_positive():
    result = format_spy_trend(0.15)
    assert result == "Bullish (+15.0%)"


def test_format_spy_trend_large_negative():
    result = format_spy_trend(-0.10)
    assert result == "Bearish (-10.0%)"


# --- format_vix_level ---

def test_format_vix_level_low_volatility():
    result = format_vix_level(18.4)
    assert result == "18.4 (Low volatility)"


def test_format_vix_level_elevated_volatility():
    result = format_vix_level(25.0)
    assert result == "25.0 (Elevated volatility)"


def test_format_vix_level_high_volatility():
    result = format_vix_level(35.7)
    assert result == "35.7 (High volatility)"


def test_format_vix_level_boundary_20():
    """VIX exactly 20 is Elevated volatility."""
    result = format_vix_level(20.0)
    assert result == "20.0 (Elevated volatility)"


def test_format_vix_level_boundary_30():
    """VIX exactly 30 is Elevated volatility."""
    result = format_vix_level(30.0)
    assert result == "30.0 (Elevated volatility)"


def test_format_vix_level_just_above_30():
    """VIX just above 30 is High volatility."""
    result = format_vix_level(30.1)
    assert result == "30.1 (High volatility)"


# --- compute_52w_position ---

def test_compute_52w_position_near_high():
    """80% is near high."""
    result = compute_52w_position(current_price=180, low_52w=100, high_52w=200)
    assert result == "Near high (80%)"


def test_compute_52w_position_near_low():
    """10% is near low."""
    result = compute_52w_position(current_price=110, low_52w=100, high_52w=200)
    assert result == "Near low (10%)"


def test_compute_52w_position_mid_range():
    """50% is mid-range."""
    result = compute_52w_position(current_price=150, low_52w=100, high_52w=200)
    assert result == "Mid-range (50%)"


def test_compute_52w_position_none_current():
    result = compute_52w_position(current_price=None, low_52w=100, high_52w=200)
    assert result == "N/A"


def test_compute_52w_position_none_low():
    result = compute_52w_position(current_price=150, low_52w=None, high_52w=200)
    assert result == "N/A"


def test_compute_52w_position_none_high():
    result = compute_52w_position(current_price=150, low_52w=100, high_52w=None)
    assert result == "N/A"


def test_compute_52w_position_equal_high_low():
    """Division by zero guard: high == low returns N/A."""
    result = compute_52w_position(current_price=150, low_52w=150, high_52w=150)
    assert result == "N/A"


def test_compute_52w_position_at_high():
    """Exactly at high (100%) is near high."""
    result = compute_52w_position(current_price=200, low_52w=100, high_52w=200)
    assert result == "Near high (100%)"


def test_compute_52w_position_at_low():
    """Exactly at low (0%) is near low."""
    result = compute_52w_position(current_price=100, low_52w=100, high_52w=200)
    assert result == "Near low (0%)"


# --- fetch_macro_context (mocked) ---

def test_fetch_macro_context_returns_correct_shape_on_success():
    """fetch_macro_context returns dict with spy_trend and vix_level strings on success."""
    spy_closes = pd.Series([100.0] + [103.2] * 19)  # ~3.2% return
    vix_closes = pd.Series([18.4] * 5)

    mock_spy_ticker = MagicMock()
    mock_spy_ticker.history.return_value = pd.DataFrame({"Close": spy_closes})

    mock_vix_ticker = MagicMock()
    mock_vix_ticker.fast_info = {"lastPrice": 18.4}

    def mock_ticker(symbol):
        if symbol == "SPY":
            return mock_spy_ticker
        if symbol == "^VIX":
            return mock_vix_ticker
        return MagicMock()

    with patch("screener.macro.yf.Ticker", side_effect=mock_ticker):
        result = fetch_macro_context()

    assert "spy_trend" in result
    assert "vix_level" in result
    assert result["spy_trend"] is not None
    assert result["vix_level"] is not None
    assert "Bullish" in result["spy_trend"]
    assert "Low volatility" in result["vix_level"]


def test_fetch_macro_context_returns_none_values_on_exception():
    """fetch_macro_context returns {'spy_trend': None, 'vix_level': None} on any exception."""
    with patch("screener.macro.yf.Ticker", side_effect=RuntimeError("network error")):
        result = fetch_macro_context()

    assert result == {"spy_trend": None, "vix_level": None}


def test_fetch_macro_context_bearish_spy():
    """fetch_macro_context returns Bearish when SPY 1-month return is negative."""
    spy_closes = pd.Series([100.0] + [98.6] * 19)  # ~-1.4% return
    vix_closes = pd.Series([25.0] * 5)

    mock_spy_ticker = MagicMock()
    mock_spy_ticker.history.return_value = pd.DataFrame({"Close": spy_closes})

    mock_vix_ticker = MagicMock()
    mock_vix_ticker.fast_info = {"lastPrice": 25.0}

    def mock_ticker(symbol):
        if symbol == "SPY":
            return mock_spy_ticker
        if symbol == "^VIX":
            return mock_vix_ticker
        return MagicMock()

    with patch("screener.macro.yf.Ticker", side_effect=mock_ticker):
        result = fetch_macro_context()

    assert "Bearish" in result["spy_trend"]
    assert "Elevated volatility" in result["vix_level"]


def test_fetch_macro_context_vix_fallback_to_history():
    """fetch_macro_context falls back to VIX history when fast_info['lastPrice'] raises KeyError."""
    spy_closes = pd.Series([100.0] + [103.2] * 19)

    mock_spy_ticker = MagicMock()
    mock_spy_ticker.history.return_value = pd.DataFrame({"Close": spy_closes})

    mock_vix_ticker = MagicMock()
    # fast_info raises KeyError
    mock_vix_ticker.fast_info = {}
    vix_history = pd.DataFrame({"Close": pd.Series([22.0, 23.0, 21.5, 22.5, 35.1])})
    mock_vix_ticker.history.return_value = vix_history

    def mock_ticker(symbol):
        if symbol == "SPY":
            return mock_spy_ticker
        if symbol == "^VIX":
            return mock_vix_ticker
        return MagicMock()

    with patch("screener.macro.yf.Ticker", side_effect=mock_ticker):
        result = fetch_macro_context()

    assert result["vix_level"] is not None
    assert "High volatility" in result["vix_level"]
