"""Tests for screener/exit_signals.py — SELL-02.

Gate logic: RSI > config.sell_rsi_threshold (per D-01).
Returns False if RSI is None (insufficient data) or any value missing.
"""
from config import Config
from screener.exit_signals import check_exit_signals


def _config(threshold=70.0):
    c = Config()
    c.sell_rsi_threshold = threshold
    return c


def test_rsi_above_threshold_returns_true():
    """RSI strictly above threshold — should trigger sell signal."""
    config = _config(70.0)
    assert check_exit_signals({"rsi": 75.0}, config) is True


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
    """Custom high threshold (80) — RSI 85 should trigger."""
    config = _config(80.0)
    assert check_exit_signals({"rsi": 85.0}, config) is True


def test_custom_threshold_low():
    """Custom low threshold (60) — RSI 65 should trigger."""
    config = _config(60.0)
    assert check_exit_signals({"rsi": 65.0}, config) is True


def test_rsi_just_above_threshold():
    """RSI barely above threshold (70.1 > 70.0) — should trigger."""
    config = _config(70.0)
    assert check_exit_signals({"rsi": 70.1}, config) is True


def test_extra_keys_in_dict_do_not_affect_result():
    """Extra fields in technical_data should be ignored, only RSI matters."""
    config = _config(70.0)
    data = {"rsi": 72.0, "macd_line": -0.5, "signal_line": 0.2, "price": 170.0}
    assert check_exit_signals(data, config) is True
