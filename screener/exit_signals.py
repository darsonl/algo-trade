"""Exit signal detection for open positions."""
from config import Config


def check_exit_signals(technical_data: dict, config: Config) -> bool:
    """Return True if technical data indicates a sell exit signal.

    Currently checks only RSI > config.sell_rsi_threshold (per D-01).
    Returns False if RSI is None (insufficient data).
    """
    rsi = technical_data.get("rsi")
    if rsi is None:
        return False
    return rsi > config.sell_rsi_threshold
