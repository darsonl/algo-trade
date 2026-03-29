import pandas as pd
from config import Config


def compute_rsi(prices: pd.Series, period: int = 14) -> float:
    """
    Compute RSI using Wilder's smoothing method.
    Raises ValueError if fewer than period+1 data points are provided.
    """
    if len(prices) < period + 1:
        raise ValueError(
            f"Need at least {period + 1} prices to compute RSI with period={period}, "
            f"got {len(prices)}"
        )

    deltas = prices.diff().dropna()
    gains = deltas.clip(lower=0)
    losses = (-deltas).clip(lower=0)

    # Seed with simple average for first period
    avg_gain = gains.iloc[:period].mean()
    avg_loss = losses.iloc[:period].mean()

    # Wilder's smoothing for remaining values
    for gain, loss in zip(gains.iloc[period:], losses.iloc[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def passes_technical_filter(ticker_data: dict, config: Config) -> bool:
    """
    Return True only if all technical criteria are met.

    Expects keys: 'rsi', 'price', 'ma50', 'volume', 'avg_volume'
    """
    rsi = ticker_data.get("rsi")
    price = ticker_data.get("price")
    ma50 = ticker_data.get("ma50")
    volume = ticker_data.get("volume")
    avg_volume = ticker_data.get("avg_volume")

    if any(v is None for v in (rsi, price, ma50, volume, avg_volume)):
        return False

    if rsi > config.max_rsi:
        return False
    if price < ma50:
        return False
    if volume < avg_volume * 0.5:
        return False

    return True


def fetch_technical_data(ticker: str) -> dict:
    """Fetch OHLCV history and compute technical indicators for a ticker."""
    import yfinance as yf

    yf_ticker = yf.Ticker(ticker)
    hist = yf_ticker.history(period="3mo")

    if hist.empty or len(hist) < 51:
        return {
            "rsi": None,
            "price": None,
            "ma50": None,
            "volume": None,
            "avg_volume": None,
        }

    rsi = compute_rsi(hist["Close"])
    price = hist["Close"].iloc[-1]
    ma50 = hist["Close"].tail(50).mean()
    volume = hist["Volume"].iloc[-1]
    avg_volume = hist["Volume"].tail(20).mean()

    return {
        "rsi": rsi,
        "price": price,
        "ma50": ma50,
        "volume": volume,
        "avg_volume": avg_volume,
    }
