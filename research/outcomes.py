"""Forward price marks for shadow observations.

Every observation is marked against SPY over the same window, so each row
carries its own market-relative result and no later analysis has to re-derive
one. Absolute return alone would mostly measure the market.

BOTH LEGS ARE TOTAL RETURNS, ON ONE BASIS. That sentence is the whole design,
and it was false until this module was rewritten:

  `reference_price` is a raw live quote frozen at the moment of the screen.
  Every close Yahoo serves is on the basis as of FETCH TIME. So the two ends of
  the stock's return sat on different bases, and two corporate actions walked
  through the gap.

  SPLITS. Yahoo back-applies them to every close it serves -- verified to do so
  even with `auto_adjust=False`, so "fetch it unadjusted" is not an available
  fix. A 10:1 split inside a window turned a +10% holding into -89%.

  DIVIDENDS, the larger of the two. The benchmark's entry is a PAST date
  fetched NOW, so it absorbs every SPY distribution since -- making the
  benchmark leg a TOTAL return. `reference_price` absorbs nothing, making the
  stock leg a PRICE return. Every row was therefore biased against the stock by
  roughly its own yield less SPY's. This system screens ON dividend yield, so
  the bias was correlated with the variable under test and would not average
  out. Measured 2026-08-23: a one-year-old close came back 1.10% below the
  traded close for SPY and 2.75% below for KO.

Both factors are now reconstructed over the window (session_date, as_of] from
the `Dividends` and `Stock Splits` columns, rather than inherited from
fetch-time adjustment, so a mark taken late equals a mark taken promptly. They
are stored beside the result: a correction that cannot be inspected cannot be
audited, the same argument as `reference_price_source`.

STILL NOT IDENTICAL, and deliberately so: the stock enters at its intraday
screen price while SPY enters at that session's close. Re-basing corrects for
corporate actions ONLY -- substituting the session close would discard the
price a human would actually have transacted at, which is a different metric.
The gap is systematic across every cohort, so it cancels in a cohort
comparison; it does not cancel in an absolute one.

See docs/superpowers/specs/2026-08-21-strategy-validation-design.md.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import yfinance as yf

from database import queries

logger = logging.getLogger(__name__)

# Calendar days, not trading days: the mark is "the last close on or before
# this date", so weekends and holidays resolve backwards to a real bar.
HORIZONS = {"1w": 7, "1m": 30, "3m": 90, "6m": 180}

BENCHMARK = "SPY"

# Marking runs at the FRONT of a market-timed scan, before the universe is
# built, and every fetch is serial with a 10s yfinance timeout. Left unbounded,
# a backlog -- or simply a large universe across four horizons -- can push the
# scan past the moment it was scheduled for. These bound the delay instead.
# Nothing is lost: an observation not marked this scan is still due next scan.
MAX_MARKS_PER_RUN = 200
MARKING_TIME_BUDGET_S = 120.0


def compute_return(entry, exit_) -> float | None:
    """Percentage return, or None when it cannot be computed.

    A zero or absent entry price yields None rather than 0.0: booking a fake
    flat trade would put a fabricated observation into the sample, which is the
    same failure mode as booking a zero fill for an order that really filled.
    """
    if not entry or exit_ is None:
        return None
    if not math.isfinite(entry) or not math.isfinite(exit_):
        return None
    return (exit_ - entry) / entry * 100.0


def _in_window(hist, after: str, through: str):
    """Rows with `after` < date <= `through`. Half-open at the entry end.

    An action ON the session date is already reflected in the price we screened
    at, so including it would correct for something that never happened.
    """
    dates = hist.index.strftime("%Y-%m-%d")
    return hist[(dates > after) & (dates <= through)]


def split_factor(hist, after: str, through: str) -> float:
    """Cumulative split ratio over (after, through], or 1.0 when there is none.

    Yahoo back-applies splits to every close it serves -- VERIFIED to do so even
    with auto_adjust=False, so "fetch unadjusted" is not an available fix. The
    entry price predates them, so it must be divided by this to reach the same
    basis as the exit close.
    """
    factor = 1.0
    for value in _in_window(hist, after, through)["Stock Splits"]:
        # `if value:` alone is TRUE for NaN, which multiplied the factor to NaN
        # and took the whole mark with it -- and NaN reaches SQLite as NULL.
        ratio = float(value)
        if math.isfinite(ratio) and ratio:
            factor *= ratio
    return factor


def dividend_factor(hist, after: str, through: str) -> float | None:
    """Cumulative dividend adjustment over (after, through], or None.

    Yahoo's own convention: each ex-date contributes (1 - D / prior close), and
    they compound. Applying it to the entry basis makes the STOCK leg a total
    return -- which is what the benchmark leg already is, because `bench_entry`
    is a past date fetched now and so absorbs every dividend since. Leaving
    only the stock leg on a price basis biases every row by the stock's yield
    less SPY's, and this system screens ON dividend yield.

    Computed over the window rather than read off fetch-time adjustment, so a
    mark taken late equals a mark taken promptly.

    None -- never 1.0 -- when a dividend has no prior close to be priced
    against. A skipped dividend yields a factor that looks clean and silently
    under-corrects; the caller leaves the mark pending instead.
    """
    dates = hist.index.strftime("%Y-%m-%d")
    factor = 1.0
    for position, (date, amount) in enumerate(zip(dates, hist["Dividends"])):
        amount = float(amount)
        if not math.isfinite(amount) or not amount:
            continue
        if not (after < date <= through):
            continue
        if position == 0:
            return None
        prior_close = float(hist["Close"].iloc[position - 1])
        if not math.isfinite(prior_close) or prior_close <= 0:
            return None
        factor *= 1.0 - amount / prior_close
    return factor


def adjusted_entry(entry_price, split: float, dividend) -> float | None:
    """`entry_price` moved onto the basis the exit close is quoted on.

    The intraday decision price is KEPT -- it is the price a human would
    actually have transacted at, and re-basing corrects only for the corporate
    actions, never for the drift between the decision and that day's close.
    Substituting the session close would be a different metric.

    None whenever the window cannot be corrected, so `compute_return` yields
    None and the caller leaves the mark pending. Guarding the zero split factor
    here keeps a division-by-zero out of a job that runs inside a scan.
    """
    if not entry_price or entry_price <= 0 or dividend is None or not split:
        return None
    if not all(math.isfinite(v) for v in (entry_price, split, dividend)):
        return None
    return entry_price / split * dividend


def close_on_or_before(hist, as_of: str) -> float | None:
    """Last close at or before `as_of` within an already-fetched window.

    Horizons are CALENDAR days, so a mark date falls on a weekend or holiday
    often; it resolves backwards to a real bar. It never resolves FORWARD --
    the window is fetched with a tail past `as_of`, and taking the last row
    outright would mark against a price the horizon has not reached.
    """
    if hist is None or len(hist) == 0:
        return None
    upto = hist[hist.index.strftime("%Y-%m-%d") <= as_of]
    for value in reversed(upto["Close"].tolist()):
        close = float(value)
        if math.isfinite(close):
            return close
    return None


class Mark(NamedTuple):
    """One leg of one forward mark, with the correction that produced it.

    The factors are stored alongside the result, not thrown away: a mark whose
    correction cannot be inspected cannot be audited or recomputed, which is
    the same argument as `reference_price_source`.
    """
    exit_close: float
    adjusted_entry_price: float
    split_factor: float
    dividend_factor: float
    return_pct: float


def mark_from_window(hist, session_date: str, as_of: str,
                     entry_price) -> Mark | None:
    """Mark `entry_price` against `as_of` within one fetched window, or None.

    Serves BOTH legs. The stock leg passes its intraday `reference_price`; the
    benchmark leg passes its own `session_date` close. That is the whole fix:
    the two legs were previously computed by different routes, one landing on a
    total return and the other on a price return.

    None means "not markable from this window" and the caller leaves the
    horizon pending. Every partial failure funnels here rather than into a
    half-corrected number.
    """
    exit_close = close_on_or_before(hist, as_of)
    if exit_close is None:
        return None
    split = split_factor(hist, session_date, as_of)
    dividend = dividend_factor(hist, session_date, as_of)
    entry = adjusted_entry(entry_price, split, dividend)
    if entry is None:
        return None
    return_pct = compute_return(entry, exit_close)
    if return_pct is None:
        return None
    return Mark(exit_close, entry, split, dividend, return_pct)


# The window starts this far before the session date so the entry close always
# has a real bar behind it -- long weekends, and the prior close a dividend on
# the session date's own successor must be priced against.
_WINDOW_LEAD_DAYS = 10


def _fetch_window(ticker: str, start: str, end: str):
    """Daily bars, dividends and splits for ONE ticker over [start, end).

    `auto_adjust=False` so `Close` carries the dividend the `Dividends` column
    reports, rather than one already netted out of it. It does NOT make the
    series unadjusted for splits -- Yahoo back-applies those to every close it
    serves either way, which is why the split factor has to be reconstructed
    from the `Stock Splits` column rather than avoided.
    """
    return yf.Ticker(ticker).history(
        start=start, end=end, auto_adjust=False, actions=True)


async def _window_cached(cache: dict, ticker: str,
                         session_date: str, as_of: str):
    """`_fetch_window` memoised for the duration of ONE marking run.

    Keyed on the whole window, so every observation sharing a session date and
    horizon shares one benchmark fetch -- otherwise a scan pays for SPY once
    per candidate.

    Per-run rather than module-level on purpose: bars for past dates are
    immutable, but the tail of the window is not, and a cache that outlived the
    run would freeze an intraday bar into every later mark.

    A raising fetch is deliberately NOT cached -- the caller treats it as a
    per-observation failure and the next observation deserves a fresh attempt.
    """
    key = (ticker, session_date, as_of)
    if key not in cache:
        start = (datetime.strptime(session_date, "%Y-%m-%d")
                 - timedelta(days=_WINDOW_LEAD_DAYS)).strftime("%Y-%m-%d")
        end = (datetime.strptime(as_of, "%Y-%m-%d")
               + timedelta(days=1)).strftime("%Y-%m-%d")
        # Resolved as a module global at call time so tests can monkeypatch it.
        cache[key] = await asyncio.to_thread(_fetch_window, ticker, start, end)
    return cache[key]



async def mark_due_outcomes(config, instant=None) -> int:
    """Fill in every matured, unrecorded mark. Returns the count. NEVER RAISES.

    Runs at scan start, so it inherits the same contract as the recorder: a
    research job must not be able to abort the scan it runs inside. Failures are
    per observation, so one delisted ticker cannot stop every other mark -- the
    same rule the terminal-order sweep follows.

    BOUNDED: at most MAX_MARKS_PER_RUN marks and MARKING_TIME_BUDGET_S seconds.
    This job sits in front of a market-timed scan, so an unbounded backlog would
    delay the scan itself. Anything skipped is still due on the next run.

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
    started = time.monotonic()
    for horizon, days in HORIZONS.items():
        if marked >= MAX_MARKS_PER_RUN or (
                time.monotonic() - started) >= MARKING_TIME_BUDGET_S:
            logger.info(
                "Marking budget reached (%d marks, %.0fs); the rest stay due",
                marked, time.monotonic() - started)
            break
        cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            due = queries.pending_shadow_marks(
                config.db_path, horizon, cutoff,
                limit=MAX_MARKS_PER_RUN - marked)
        except Exception:
            logger.exception("Could not list pending %s marks; continuing", horizon)
            continue
        for row in due:
            if (time.monotonic() - started) >= MARKING_TIME_BUDGET_S:
                logger.info(
                    "Marking time budget reached after %d marks; the rest stay due",
                    marked)
                break
            as_of = (datetime.strptime(row["session_date"], "%Y-%m-%d")
                     + timedelta(days=days)).strftime("%Y-%m-%d")
            try:
                hist = await _window_cached(
                    cache, row["ticker"], row["session_date"], as_of)
                mark = mark_from_window(
                    hist, row["session_date"], as_of, row["reference_price"])
                if mark is None:
                    logger.warning(
                        "No usable %s window for %s at %s; leaving the mark "
                        "pending", horizon, row["ticker"], as_of)
                    continue
                # The benchmark runs through the SAME function, on the same
                # window convention -- that identity IS the fix. Computing the
                # two legs by different routes is what left one a total return
                # and the other a price return.
                bench_hist = await _window_cached(
                    cache, BENCHMARK, row["session_date"], as_of)
                bench = mark_from_window(
                    bench_hist, row["session_date"], as_of,
                    close_on_or_before(bench_hist, row["session_date"]))
                queries.record_shadow_outcome(
                    config.db_path, row["id"], horizon, as_of, mark, bench)
                marked += 1
            except Exception:
                logger.exception(
                    "Could not mark %s for %s at %s; continuing",
                    horizon, row["ticker"], as_of)
                continue
    return marked
