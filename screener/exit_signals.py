"""Exit signal detection for open positions."""
from config import Config


def check_exit_signals(technical_data: dict, config: Config) -> bool:
    """Return True if technical data indicates a sell exit signal.

    Two-gate check per D-01:
    1. RSI > config.sell_rsi_threshold (overbought)
    2. MACD bearish: macd_line < signal_line (downward momentum confirmation per D-10)

    Returns False if either value is None (insufficient data — fail-safe).
    This prevents RSI-only false signals in strong uptrends.
    """
    rsi = technical_data.get("rsi")
    macd_line = technical_data.get("macd_line")
    signal_line = technical_data.get("signal_line")

    if rsi is None or macd_line is None or signal_line is None:
        return False

    rsi_triggered = rsi > config.sell_rsi_threshold
    macd_bearish = macd_line < signal_line  # MACD histogram < 0

    return rsi_triggered and macd_bearish
