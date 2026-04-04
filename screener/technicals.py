import yfinance as yf
import pandas as pd
from config import Config
from tenacity import retry, stop_after_attempt, wait_exponential

_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)

MA_WINDOW = 50
MIN_HISTORY_BARS = MA_WINDOW + 1  # 51: 50-day MA needs 50 bars, RSI needs +1 for diff


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


def compute_macd(
    prices: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float | None, float | None, float | None]:
    """Compute MACD line, signal line, and histogram using standard EWM.

    Returns (macd_line, signal_line, histogram) as floats, or (None, None, None)
    if there is insufficient data (fewer than slow+signal bars required).
    Per D-10: fixed parameters, no config fields needed.
    """
    if len(prices) < slow + signal:
        return None, None, None
    fast_ema = prices.ewm(span=fast, adjust=False).mean()
    slow_ema = prices.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(histogram.iloc[-1])


def passes_technical_filter(ticker_data: dict, config: Config) -> bool:
    """
    Return True only if all three technical criteria are met: RSI <= config.max_rsi,
    price >= ma50, and volume >= avg_volume * config.min_volume_ratio.

    Expects keys: 'rsi', 'price', 'ma50', 'volume', 'avg_volume'. Returns False if any value is None.
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
    if volume < avg_volume * config.min_volume_ratio:
        return False

    return True


@_retry
def _fetch_history(yf_ticker):
    return yf_ticker.history(period="3mo")


def fetch_technical_data(yf_ticker: yf.Ticker) -> dict:
    """Fetch OHLCV history and compute technical indicators via a pre-built yf.Ticker object."""
    hist = _fetch_history(yf_ticker)

    if hist.empty or len(hist) < MIN_HISTORY_BARS:
        return {
            "rsi": None,
            "price": None,
            "ma50": None,
            "volume": None,
            "avg_volume": None,
            "macd_line": None,
            "signal_line": None,
            "macd_histogram": None,
        }

    closes = hist["Close"]
    rsi = compute_rsi(closes)
    price = closes.iloc[-1]
    ma50 = closes.tail(50).mean()
    volume = hist["Volume"].iloc[-1]
    avg_volume = hist["Volume"].tail(20).mean()
    macd_line, signal_line, macd_histogram = compute_macd(closes)

    return {
        "rsi": rsi,
        "price": price,
        "ma50": ma50,
        "volume": volume,
        "avg_volume": avg_volume,
        "macd_line": macd_line,
        "signal_line": signal_line,
        "macd_histogram": macd_histogram,
    }
