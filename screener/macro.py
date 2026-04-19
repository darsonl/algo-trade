"""Macro context fetcher — SPY trend and VIX level for prompt enrichment."""
import logging
import yfinance as yf

logger = logging.getLogger(__name__)


def format_spy_trend(one_month_return: float) -> str:
    """Format SPY 1-month return as a direction label with percentage.

    Args:
        one_month_return: Fractional return (e.g., 0.032 for +3.2%, -0.014 for -1.4%)

    Returns:
        "Bullish (+3.2%)" or "Bearish (-1.4%)"
    """
    if one_month_return >= 0:
        return f"Bullish (+{one_month_return * 100:.1f}%)"
    return f"Bearish ({one_month_return * 100:.1f}%)"


def format_vix_level(vix_close: float) -> str:
    """Format VIX closing value as a numeric label with volatility description.

    Thresholds:
        < 20   → Low volatility
        20–30  → Elevated volatility
        > 30   → High volatility

    Args:
        vix_close: VIX closing price

    Returns:
        "18.4 (Low volatility)" or similar
    """
    if vix_close < 20:
        label = "Low volatility"
    elif vix_close <= 30:
        label = "Elevated volatility"
    else:
        label = "High volatility"
    return f"{vix_close:.1f} ({label})"


def compute_52w_position(
    current_price: float | None,
    low_52w: float | None,
    high_52w: float | None,
) -> str:
    """Compute 52-week range position label.

    Args:
        current_price: Current ticker price
        low_52w: 52-week low
        high_52w: 52-week high

    Returns:
        "Near high (87%)", "Near low (12%)", "Mid-range (50%)", or "N/A"
    """
    if current_price is None or low_52w is None or high_52w is None:
        return "N/A"
    if high_52w == low_52w:
        return "N/A"
    pct = (current_price - low_52w) / (high_52w - low_52w)
    pct_int = round(pct * 100)
    if pct >= 0.80:
        return f"Near high ({pct_int}%)"
    if pct <= 0.20:
        return f"Near low ({pct_int}%)"
    return f"Mid-range ({pct_int}%)"


def fetch_macro_context() -> dict:
    """Fetch macro context from yfinance: SPY trend direction (1m and 1y) and VIX level.

    SPY trend (1m): 1-month return expressed as "Bullish (+3.2%)" or "Bearish (-1.4%)"
    SPY trend (1y): 1-year return expressed as "Bullish (+12.5%)" or "Bearish (-8.5%)"
    VIX level: Latest close with volatility label "18.4 (Low volatility)"

    Returns:
        {"spy_trend_1m": str | None, "spy_trend_1y": str | None, "vix_level": str | None}
        On any failure, returns {"spy_trend_1m": None, "spy_trend_1y": None, "vix_level": None}
        and logs a warning.
    """
    try:
        # SPY: compute 1-month return
        spy = yf.Ticker("SPY")
        spy_hist = spy.history(period="1mo")
        first_close = spy_hist["Close"].iloc[0]
        last_close = spy_hist["Close"].iloc[-1]
        spy_return = (last_close - first_close) / first_close

        # SPY: compute 1-year return (reuse same spy Ticker object)
        spy_hist_1y = spy.history(period="1y")
        first_close_1y = spy_hist_1y["Close"].iloc[0]
        last_close_1y = spy_hist_1y["Close"].iloc[-1]
        spy_return_1y = (last_close_1y - first_close_1y) / first_close_1y

        # VIX: get latest close price (fast_info preferred, fallback to history)
        vix = yf.Ticker("^VIX")
        try:
            vix_close = vix.fast_info["lastPrice"]
        except (KeyError, TypeError, AttributeError):
            vix_hist = vix.history(period="5d")
            vix_close = vix_hist["Close"].iloc[-1]

        return {
            "spy_trend_1m": format_spy_trend(spy_return),
            "spy_trend_1y": format_spy_trend(spy_return_1y),
            "vix_level": format_vix_level(vix_close),
        }
    except Exception as exc:
        logger.warning("Failed to fetch macro context: %s", exc)
        return {"spy_trend_1m": None, "spy_trend_1y": None, "vix_level": None}
