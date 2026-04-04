"""Tests for screener/exit_signals.py — SELL-02.

Gate logic (two-gate, per D-01/D-10):
  Gate 1: RSI > config.sell_rsi_threshold
  Gate 2: macd_line < signal_line (MACD bearish confirmation)
Returns False if RSI, macd_line, or signal_line is None (fail-safe).
"""
from config import Config
from screener.exit_signals import check_exit_signals


def _config(threshold=70.0):
    c = Config()
    c.sell_rsi_threshold = threshold
    return c


def test_rsi_above_threshold_returns_true():
    """RSI strictly above threshold with MACD bearish — should trigger sell signal."""
    config = _config(70.0)
    assert check_exit_signals({"rsi": 75.0, "macd_line": -0.5, "signal_line": 0.2}, config) is True


def test_rsi_at_threshold_returns_false():
    """RSI exactly at threshold is not strictly greater — no signal."""
    config = _config(70.0)
    assert check_exit_signals({"rsi": 70.0}, config) is False


def test_rsi_below_threshold_returns_false():
    """RSI below threshold — no sell signal."""
    config = _config(70.0)
    assert check_exit_signals({"rsi": 50.0}, config) is False


def test_rsi_none_returns_false():
    """None RSI is treated as insufficient data — fail-safe returns False."""
    config = _config(70.0)
    assert check_exit_signals({"rsi": None}, config) is False


def test_empty_dict_returns_false():
    """Missing RSI key returns False — fail-safe."""
    config = _config(70.0)
    assert check_exit_signals({}, config) is False


def test_custom_threshold_high():
    """Custom high threshold (80) — RSI 75 should not trigger."""
    config = _config(80.0)
    assert check_exit_signals({"rsi": 75.0}, config) is False


def test_custom_threshold_high_rsi_above():
    """Custom high threshold (80) — RSI 85 with MACD bearish should trigger."""
    config = _config(80.0)
    assert check_exit_signals({"rsi": 85.0, "macd_line": -0.5, "signal_line": 0.2}, config) is True


def test_custom_threshold_low():
    """Custom low threshold (60) — RSI 65 with MACD bearish should trigger."""
    config = _config(60.0)
    assert check_exit_signals({"rsi": 65.0, "macd_line": -0.5, "signal_line": 0.2}, config) is True


def test_rsi_just_above_threshold():
    """RSI barely above threshold (70.1 > 70.0) with MACD bearish — should trigger."""
    config = _config(70.0)
    assert check_exit_signals({"rsi": 70.1, "macd_line": -0.5, "signal_line": 0.2}, config) is True


def test_extra_keys_in_dict_do_not_affect_result():
    """Extra fields in technical_data are fine. RSI=72>70 (gate 1) + macd_line=-0.5<signal_line=0.2 (gate 2) — both pass."""
    config = _config(70.0)
    data = {"rsi": 72.0, "macd_line": -0.5, "signal_line": 0.2, "price": 170.0}
    assert check_exit_signals(data, config) is True


# --- MACD 2x2 matrix tests (added for gap closure 06-04) ---

def test_rsi_high_macd_bearish_returns_true():
    """RSI above threshold AND MACD bearish (macd_line < signal_line) — both gates pass."""
    config = _config(70.0)
    data = {"rsi": 75.0, "macd_line": -0.5, "signal_line": 0.2}
    assert check_exit_signals(data, config) is True


def test_rsi_high_macd_bullish_returns_false():
    """RSI above threshold but MACD is bullish (macd_line > signal_line) — MACD gate blocks sell."""
    config = _config(70.0)
    data = {"rsi": 75.0, "macd_line": 0.5, "signal_line": 0.2}
    assert check_exit_signals(data, config) is False


def test_rsi_low_macd_bearish_returns_false():
    """MACD bearish but RSI below threshold — RSI gate blocks sell."""
    config = _config(70.0)
    data = {"rsi": 55.0, "macd_line": -0.5, "signal_line": 0.2}
    assert check_exit_signals(data, config) is False


def test_macd_line_none_returns_false():
    """macd_line is None — fail-safe, return False regardless of RSI."""
    config = _config(70.0)
    data = {"rsi": 75.0, "macd_line": None, "signal_line": 0.2}
    assert check_exit_signals(data, config) is False


def test_signal_line_none_returns_false():
    """signal_line is None — fail-safe, return False regardless of RSI."""
    config = _config(70.0)
    data = {"rsi": 75.0, "macd_line": -0.5, "signal_line": None}
    assert check_exit_signals(data, config) is False


def test_both_macd_values_none_returns_false():
    """Both MACD values None — fail-safe, return False."""
    config = _config(70.0)
    data = {"rsi": 75.0, "macd_line": None, "signal_line": None}
    assert check_exit_signals(data, config) is False
