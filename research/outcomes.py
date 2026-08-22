"""Forward price marks for shadow observations.

Every observation is marked against SPY over the identical window, so each row
carries its own market-relative result and no later analysis has to re-derive
one. Absolute return alone would mostly measure the market.

See docs/superpowers/specs/2026-08-21-strategy-validation-design.md.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import yfinance as yf

from database import queries

logger = logging.getLogger(__name__)

# Calendar days, not trading days: the mark is "the last close on or before
# this date", so weekends and holidays resolve backwards to a real bar.
HORIZONS = {"1w": 7, "1m": 30, "3m": 90, "6m": 180}

BENCHMARK = "SPY"


def compute_return(entry, exit_) -> float | None:
    """Percentage return, or None when it cannot be computed.

    A zero or absent entry price yields None rather than 0.0: booking a fake
    flat trade would put a fabricated observation into the sample, which is the
    same failure mode as booking a zero fill for an order that really filled.
    """
    if not entry or exit_ is None:
        return None
    return (exit_ - entry) / entry * 100.0


def _close_on_or_before(ticker: str, as_of: str) -> float | None:
    """Last close at or before `as_of` (YYYY-MM-DD), or None."""
    end = datetime.strptime(as_of, "%Y-%m-%d") + timedelta(days=1)
    start = end - timedelta(days=10)  # enough to clear a long weekend
    hist = yf.Ticker(ticker).history(start=start.date(), end=end.date())
    if hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


async def _close_cached(cache: dict, ticker: str, as_of: str) -> float | None:
    """`_close_on_or_before` memoised for the duration of ONE marking run.

    Every observation sharing a session date also shares its benchmark entry
    and exit closes, so fetching them inside the per-row loop multiplies a
    scan's network cost by the size of the universe for no extra information.

    Per-run rather than module-level on purpose: a close for a past date is
    immutable, but "the last close on or before today" is not, and a cache that
    outlived the run would freeze an intraday value into every later mark.

    A raising fetch is deliberately NOT cached -- the caller treats it as a
    per-observation failure and the next observation deserves a fresh attempt.
    """
    key = (ticker, as_of)
    if key not in cache:
        # Resolved as a module global at call time so tests can monkeypatch it.
        cache[key] = await asyncio.to_thread(_close_on_or_before, ticker, as_of)
    return cache[key]


async def mark_due_outcomes(config, instant=None) -> int:
    """Fill in every matured, unrecorded mark. Returns the count. NEVER RAISES.

    Runs at scan start, so it inherits the same contract as the recorder: a
    research job must not be able to abort the scan it runs inside. Failures are
    per observation, so one delisted ticker cannot stop every other mark -- the
    same rule the terminal-order sweep follows.

    A price that could not be read is NOT recorded. `pending_shadow_marks`
    excludes any observation that already has a row for the horizon, so writing
    a NULL mark would convert one transient yfinance outage into permanently
    missing data with no way to retry it. Leaving the row pending costs a repeat
    fetch on the next scan; recording it costs the observation. This is the
    same rule as `fills_observed` in the order sweep -- a terminal status is not
    permission to book a zero.
    """
    now = instant or datetime.now(timezone.utc)
    cache: dict = {}
    marked = 0
    for horizon, days in HORIZONS.items():
        cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            due = queries.pending_shadow_marks(config.db_path, horizon, cutoff)
        except Exception:
            logger.exception("Could not list pending %s marks; continuing", horizon)
            continue
        for row in due:
            as_of = (datetime.strptime(row["session_date"], "%Y-%m-%d")
                     + timedelta(days=days)).strftime("%Y-%m-%d")
            try:
                price = await _close_cached(cache, row["ticker"], as_of)
                if price is None:
                    logger.warning(
                        "No close for %s at %s; leaving the %s mark pending",
                        row["ticker"], as_of, horizon)
                    continue
                bench = await _close_cached(cache, BENCHMARK, as_of)
                bench_entry = await _close_cached(
                    cache, BENCHMARK, row["session_date"])
                queries.record_shadow_outcome(
                    config.db_path, row["id"], horizon, as_of, price,
                    compute_return(row["reference_price"], price),
                    bench, compute_return(bench_entry, bench),
                )
                marked += 1
            except Exception:
                logger.exception(
                    "Could not mark %s for %s at %s; continuing",
                    horizon, row["ticker"], as_of)
                continue
    return marked
