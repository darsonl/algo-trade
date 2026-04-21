import logging
import pandas as pd
import yfinance as yf
from config import Config
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

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
    # yfinance occasionally returns dividendYield as a percentage (e.g. 2.5 instead of 0.025)
    if div_yield is not None and div_yield > 1:
        div_yield = div_yield / 100
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


def fetch_eps_data(yf_ticker: yf.Ticker) -> list[dict] | None:
    """Return last 4 quarters of Diluted EPS in chronological order (oldest → newest).

    Returns a list of {"quarter": "Q{1-4}-{YYYY}", "eps": float} dicts (1-4 entries),
    or None when the statement is unavailable, the "Diluted EPS" row is absent,
    all values are NaN, or any exception occurs.

    Per D-07 and SIG-08: quarterly_income_stmt columns are newest-first pd.Timestamp
    objects and must be reversed before extracting chronological order.
    """
    try:
        stmt = yf_ticker.quarterly_income_stmt
        if stmt is None or stmt.empty:
            return None
        if "Diluted EPS" not in stmt.index:
            return None
        row = stmt.loc["Diluted EPS"]
        row_chrono = row.iloc[::-1]  # newest-first → oldest-first
        valid = [(ts, val) for ts, val in row_chrono.items() if pd.notna(val)]
        if not valid:
            return None
        quarters = valid[-4:]  # up to 4 most recent chronological entries
        return [
            {"quarter": f"Q{ts.quarter}-{ts.year}", "eps": float(val)}
            for ts, val in quarters
        ]
    except Exception as exc:
        logger.debug("fetch_eps_data failed: %s", exc, exc_info=True)
        return None
