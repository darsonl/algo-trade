import yfinance as yf
from config import Config
from tenacity import retry, stop_after_attempt, wait_exponential

_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)


def passes_fundamental_filter(info: dict, config: Config) -> bool:
    """
    Return True only if all available fundamental criteria are met.

    Expects keys: 'trailingPE', 'dividendYield', 'earningsGrowth'
    (matching yfinance Ticker.info keys).
    ETFs are handled by the separate ETF scan pipeline and should not reach this filter.

    Missing-data policy:
    - trailingPE: required — reject if absent (valuation is non-negotiable).
    - dividendYield: optional — skip yield check if absent; non-dividend payers allowed.
    - earningsGrowth: optional — skip growth check if absent; let the analyst judge.
    """
    pe = info.get("trailingPE")
    div_yield = info.get("dividendYield")
    earnings_growth = info.get("earningsGrowth")

    if pe is None:
        return False

    if pe > config.max_pe_ratio:
        return False
    if div_yield is not None and div_yield < config.min_dividend_yield:
        return False
    if earnings_growth is not None and earnings_growth < config.min_earnings_growth:
        return False

    return True


@_retry
def fetch_fundamental_info(yf_ticker: yf.Ticker) -> dict:
    """Fetch fundamental data for a ticker via a pre-built yf.Ticker object."""
    return yf_ticker.info
