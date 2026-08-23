import logging
import math
from typing import NamedTuple

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


def normalize_dividend_yield(raw: float | None) -> float | None:
    """Convert a yfinance dividendYield value (percent) to a fraction.

    yfinance >= 0.2.55 reports dividendYield in percentage points (KO -> 2.57,
    AAPL -> 0.37); this codebase uses fractions internally (0.0257, 0.0037).
    The pinned yfinance version in requirements.txt must match this assumption —
    pre-0.2.55 versions returned fractions and would be divided twice.
    """
    if raw is None:
        return None
    return raw / 100


class Verdict(NamedTuple):
    """One gate's decision, the criterion that produced it, and its settings.

    The thresholds travel WITH the decision rather than being looked up later,
    because config lives in `.env` and never reaches the database -- a
    threshold moved mid-sample would otherwise redefine a cohort silently.

    `failed_on` is not redundant with `thresholds`: recomputing which criterion
    failed from the stored inputs only reproduces it while the gate's LOGIC is
    unchanged, not merely its parameters.
    """
    passed: bool
    failed_on: str | None
    thresholds: dict


def evaluate_fundamentals(info: dict, config: Config) -> Verdict:
    """The fundamental gate, with its reasoning exposed.

    Short-circuits, so a candidate failing several criteria is recorded against
    the FIRST -- reporting a later one would misattribute the rejection, and
    reporting all of them would imply the gate evaluated all of them.

    Missing-data policy is unchanged:
    - trailingPE: required -- reject if absent (valuation is non-negotiable).
    - dividendYield: optional -- skip if absent; non-dividend payers allowed.
    - earningsGrowth: optional -- skip if absent; let the analyst judge.
    """
    thresholds = {
        "max_pe_ratio": config.max_pe_ratio,
        "min_dividend_yield": config.min_dividend_yield,
        "min_earnings_growth": config.min_earnings_growth,
    }

    def _fail(criterion):
        return Verdict(False, criterion, thresholds)

    pe = info.get("trailingPE")
    div_yield = normalize_dividend_yield(info.get("dividendYield"))
    earnings_growth = info.get("earningsGrowth")

    if pe is None:
        return _fail("pe_missing")
    if pe > config.max_pe_ratio:
        return _fail("pe_above_max")
    if div_yield is not None and div_yield < config.min_dividend_yield:
        return _fail("yield_below_min")
    if earnings_growth is not None and earnings_growth < config.min_earnings_growth:
        return _fail("growth_below_min")
    return Verdict(True, None, thresholds)


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


# The price fields we will accept, in preference order, paired with the
# provenance string stored beside the price. `previousClose` is deliberately
# ABSENT: it belongs to a different session, so substituting it silently moves
# the holding-period start back a day and the forward return would cover a
# window the benchmark does not.
_SCREEN_PRICE_FIELDS = (
    ("currentPrice", "info.currentPrice"),
    ("regularMarketPrice", "info.regularMarketPrice"),
)


def screen_price(info) -> tuple[float | None, str | None]:
    """The price a candidate was screened at, and where it came from.

    Returns `(price, source)` or `(None, None)`. ONE price source for EVERY
    candidate that got as far as having an `.info` dict, so the funnel's
    cohorts are priced identically. The technical-stage price
    (`closes.iloc[-1]`) is NOT interchangeable with this: yfinance history is
    auto-adjusted and fetched minutes later from a different endpoint, so
    mixing the two in one column would make a cohort comparison measure
    provenance as much as outcome.

    TOTAL BY CONTRACT -- it never raises, for any input. Callers pass this as an
    ARGUMENT to `_record_shadow`, so it is evaluated before that wrapper's try
    block: a raise here would turn a genuine rejection into an `error`
    observation and could fire an ops alert, which is instrumentation
    corrupting the data it exists to record.

    Rejects more than "falsy": booleans (isinstance(True, int) is True, so a
    naive check stores a price of 1.0), NaN and infinity (both are floats, and
    NaN compares False to everything), strings (a string means the field
    changed shape, and coercing would hide that), and anything non-positive.
    """
    try:
        for key, source in _SCREEN_PRICE_FIELDS:
            value = info.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            value = float(value)
            if not math.isfinite(value) or value <= 0:
                continue
            return value, source
    except Exception:
        # Never raises. An unexpected shape means no price, not an exception.
        logger.debug("screen_price could not read a price; continuing", exc_info=True)
    return None, None
