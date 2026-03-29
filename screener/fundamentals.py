from config import Config


def passes_fundamental_filter(info: dict, config: Config) -> bool:
    """
    Return True only if all fundamental criteria are met.

    Expects keys: 'trailingPE', 'dividendYield', 'earningsGrowth'
    (matching yfinance Ticker.info keys).
    """
    pe = info.get("trailingPE")
    div_yield = info.get("dividendYield")
    earnings_growth = info.get("earningsGrowth")

    if pe is None or div_yield is None or earnings_growth is None:
        return False

    if pe > config.max_pe_ratio:
        return False
    if div_yield < config.min_dividend_yield:
        return False
    if earnings_growth < config.min_earnings_growth:
        return False

    return True


def fetch_fundamental_info(ticker: str) -> dict:
    """Fetch fundamental data for a ticker via yfinance."""
    import yfinance as yf
    return yf.Ticker(ticker).info
