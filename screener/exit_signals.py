"""Exit signal detection for open positions."""
from config import Config


def check_exit_signals(technical_data: dict, config: Config) -> bool:
    """Return True only when BOTH exit gates are met (per D-01/D-10):

    Gate 1: RSI > config.sell_rsi_threshold (overbought check).
    Gate 2: MACD bearish confirmation — macd_line < signal_line.

    Returns False if RSI, macd_line, or signal_line is None (fail-safe:
    insufficient data is treated as no exit signal).
    """
    rsi = technical_data.get("rsi")
    macd_line = technical_data.get("macd_line")
    signal_line = technical_data.get("signal_line")

    if rsi is None or macd_line is None or signal_line is None:
        return False

    return rsi > config.sell_rsi_threshold and macd_line < signal_line
