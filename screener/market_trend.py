"""Recession and fear gauges for /market_trend.

Six readings, each classified calm / watch / warning / unknown:

- Yield curve (2s10s): FRED DGS10 - DGS2. Inversion is a warning, and so is
  UN-inversion within a year -- historically the recession tends to arrive as
  the curve re-steepens, not at the moment it inverts. The 3-month direction is
  named (bear/bull flattener/steepener); a bear flattener is the path into an
  inversion, so it is a watch.
- VIX: <20 calm, 20-30 elevated, >30 high fear.
- Emergency rate cut: a cut in FRED DFEDTARU that does not land on, or the
  weekday after, a scheduled FOMC decision (`fomc_calendar.py`).
- MOVE (Treasury implied volatility): <80 calm, 80-120 watch, >120 warning.
- SKEW (demand for crash protection): trend of the 20d vs 60d average, 1-year
  percentile, and a note when it is high while VIX is calm.
- VVIX (volatility of VIX): <90 calm, 90-110 watch, >110 warning.

Yields come from FRED, not Yahoo, on purpose: Yahoo has no 2-year cash yield,
and its nearest symbol (`2YY=F`, a futures contract) read 4.38 against the
Treasury's 4.56 on 2026-09-10 -- a gap wide enough to fake an inversion.

The classifiers are pure. `fetch_market_trend` isolates every indicator, so a
FRED outage blanks two readings rather than the whole command, and it never
raises.
"""
from __future__ import annotations

import logging
import math
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import fmean
from typing import Callable, Sequence

import yfinance as yf

from market_time import market_session_date
from screener.fomc_calendar import SCHEDULED_DECISIONS

logger = logging.getLogger(__name__)

Series = list[tuple[date, float]]

_STATUS_RANK = {"calm": 0, "unknown": 1, "watch": 2, "warning": 3}
_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
_TIMEOUT_S = 10

CURVE = "Yield curve (2s10s)"
VIX = "VIX"
RATE_CUT = "Emergency rate cut"
MOVE = "MOVE"
SKEW = "SKEW"
VVIX = "VVIX"


@dataclass(frozen=True)
class Reading:
    name: str
    status: str  # calm | watch | warning | unknown
    value: str
    detail: str
    as_of: date | None = None
    level: float | None = None


def worst_status(readings: Sequence[Reading]) -> str:
    """warning > watch > unknown > calm. An outage outranks calm, never a real warning."""
    if not readings:
        return "unknown"
    return max((r.status for r in readings), key=_STATUS_RANK.__getitem__)


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _bp(delta_pct: float) -> str:
    return f"{round(delta_pct * 100):+d}bp"


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ─── Level gauges ───────────────────────────────────────────────────────────

def _classify_level(name, level, as_of, low, high, labels) -> Reading:
    """Three bands: < low, low..high inclusive, > high."""
    if not _finite(level):
        return Reading(name, "unknown", "n/a", "No usable reading", as_of)
    if level < low:
        status, detail = labels[0]
    elif level <= high:
        status, detail = labels[1]
    else:
        status, detail = labels[2]
    return Reading(name, status, f"{level:.2f}", detail, as_of, float(level))


def classify_vix(level: float | None, as_of: date | None = None) -> Reading:
    return _classify_level(VIX, level, as_of, 20, 30, (
        ("calm", "Calm (below 20)"),
        ("warning", "Elevated (20-30): equity markets nervous"),
        ("warning", "High fear (above 30)"),
    ))


def classify_move(level: float | None, as_of: date | None = None) -> Reading:
    return _classify_level(MOVE, level, as_of, 80, 120, (
        ("calm", "Calm bond market (below 80)"),
        ("watch", "Normal-to-elevated rate volatility (80-120)"),
        ("warning", "Bond market stress (above 120)"),
    ))


def classify_vvix(level: float | None, as_of: date | None = None) -> Reading:
    return _classify_level(VVIX, level, as_of, 90, 110, (
        ("calm", "Calm (below 90)"),
        ("watch", "Volatility of volatility picking up (90-110)"),
        ("warning", "Unstable volatility, sharp VIX moves likely (above 110)"),
    ))


# ─── Yield curve ────────────────────────────────────────────────────────────

_CURVE_LOOKBACK = timedelta(days=91)
_INVERSION_MEMORY = timedelta(days=365)
_LITTLE_CHANGED_BP = 10


def classify_yield_curve(y2: Series, y10: Series) -> Reading:
    """Level and 3-month direction of the 10Y - 2Y spread, on dates BOTH series have."""
    two, ten = dict(y2), dict(y10)
    common = sorted(set(two) & set(ten))
    if not common:
        return Reading(CURVE, "unknown", "n/a", "No overlapping 2Y/10Y data")

    now = common[-1]
    earlier = [d for d in common if d <= now - _CURVE_LOOKBACK]
    if not earlier:
        return Reading(CURVE, "unknown", "n/a", "Need 3 months of yield history", now)
    then = earlier[-1]

    spread_now = ten[now] - two[now]
    spread_then = ten[then] - two[then]
    d2, d10 = two[now] - two[then], ten[now] - ten[then]
    value = f"{_bp(spread_now)} (10Y {ten[now]:.2f}% - 2Y {two[now]:.2f}%)"

    change = spread_now - spread_then
    if abs(round(change * 100)) < _LITTLE_CHANGED_BP:
        direction, shape = "Spread little changed over 3m", None
    else:
        shape = ("Bear " if (d2 + d10) / 2 > 0 else "Bull ") + ("flattener" if change < 0 else "steepener")
        direction = f"{shape} over 3m"
    direction += f" (2Y {_bp(d2)}, 10Y {_bp(d10)})"

    was_inverted = any(
        ten[d] - two[d] < 0 for d in common if now - _INVERSION_MEMORY < d < now
    )
    if spread_now < 0:
        status, headline = "warning", "Inverted: 2Y above 10Y, a classic recession warning"
    elif was_inverted:
        status, headline = "warning", "Un-inverted within the last year: recessions have tended to follow this"
    elif shape == "Bear flattener":
        status, headline = "watch", "Short rates rising faster than long: the path toward inversion"
    else:
        status, headline = "calm", "Positive slope"
    return Reading(CURVE, status, value, f"{headline} · {direction}", now, spread_now)


# ─── Emergency rate cuts ────────────────────────────────────────────────────

def _next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def is_scheduled_change(effective: date, schedule: Sequence[date] = SCHEDULED_DECISIONS) -> bool:
    """True when a target change took effect on a scheduled decision day or the weekday after.

    FRED records BOTH: the Dec 2015 and Dec 2016 hikes on the decision day,
    every change since on the following weekday. Accepting only one misfiles
    the other group as an emergency.
    """
    return any(effective == d or effective == _next_weekday(d) for d in schedule)


def target_changes(target: Series) -> list[tuple[date, float, float]]:
    """(effective date, old, new) for every change in the series."""
    changes, prev = [], None
    for d, value in target:
        if prev is not None and value != prev:
            changes.append((d, prev, value))
        prev = value
    return changes


def classify_rate_cuts(
    target: Series,
    today: date,
    schedule: Sequence[date] = SCHEDULED_DECISIONS,
    lookback_days: int = 365,
) -> Reading:
    target = [(d, v) for d, v in target if d <= today]  # "as of today" means nothing later
    if not target:
        return Reading(RATE_CUT, "unknown", "n/a", "No fed funds target data")
    as_of, current = target[-1]
    value = f"{current:.2f}%"
    if today > max(schedule):
        # Past the calendar, a scheduled cut and an emergency look identical.
        return Reading(
            RATE_CUT, "unknown", value,
            f"FOMC calendar ends {max(schedule)}: extend screener/fomc_calendar.py",
            as_of, current,
        )

    changes = target_changes(target)
    cutoff = today - timedelta(days=lookback_days)
    emergencies = [
        c for c in changes
        if c[0] > cutoff and c[2] < c[1] and not is_scheduled_change(c[0], schedule)
    ]
    if emergencies:
        listed = "; ".join(f"{_bp(new - old)} effective {d}" for d, old, new in reversed(emergencies))
        return Reading(
            RATE_CUT, "warning", value,
            f"Unscheduled cut: {listed}. Emergency cuts have signalled recession arriving",
            as_of, current,
        )

    if not changes:
        return Reading(RATE_CUT, "calm", value, "No target changes on record", as_of, current)
    d, old, new = changes[-1]
    kind = "scheduled" if is_scheduled_change(d, schedule) else "unscheduled"
    return Reading(
        RATE_CUT, "calm", value,
        f"No emergency cut in 12 months · last change {_bp(new - old)} effective {d} ({kind})",
        as_of, current,
    )


# ─── SKEW ───────────────────────────────────────────────────────────────────

_SKEW_HIGH = 150
_SKEW_TREND_BAND = 0.02


def classify_skew(closes: Series, vix_level: float | None) -> Reading:
    if len(closes) < 60:
        return Reading(SKEW, "unknown", "n/a", "Need 60 sessions of SKEW history")
    values = [v for _, v in closes]
    level = values[-1]
    avg20, avg60 = fmean(values[-20:]), fmean(values[-60:])
    rel = (avg20 - avg60) / avg60
    trend = "rising" if rel > _SKEW_TREND_BAND else "falling" if rel < -_SKEW_TREND_BAND else "flat"

    year = values[-252:]
    below = sum(v < level for v in year)
    equal = sum(v == level for v in year)
    pct = round((below + 0.5 * equal) / len(year) * 100)

    detail = f"Crash hedging {trend} (20d avg {avg20:.1f} vs 60d {avg60:.1f}) · {_ordinal(pct)} pct of 1y"
    status = "calm"
    if level >= _SKEW_HIGH:
        status = "watch"
        if _finite(vix_level) and vix_level < 20:
            detail += " · heavy tail hedging while VIX looks calm"
    return Reading(SKEW, status, f"{level:.2f}", detail, closes[-1][0], level)


# ─── Fetching ───────────────────────────────────────────────────────────────

def parse_fred_csv(text: str) -> Series:
    lines = text.strip().splitlines()
    header = lines[0].lower() if lines else ""
    if not header.startswith(("observation_date,", "date,")):
        # A 200 carrying an HTML error page must not parse to "no data".
        raise ValueError(f"not a FRED CSV: {header[:60]!r}")
    series = []
    for line in lines[1:]:
        day, _, raw = line.partition(",")
        raw = raw.strip()
        if raw in ("", "."):
            continue
        value = float(raw)
        if math.isfinite(value):
            series.append((date.fromisoformat(day.strip()), value))
    return series


def fetch_fred_series(series_id: str, start: date | None = None) -> Series:
    url = _FRED_CSV.format(series_id=series_id)
    if start is not None:
        url += f"&cosd={start.isoformat()}"
    request = urllib.request.Request(url, headers={"User-Agent": "algo-trade/market_trend"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
        return parse_fred_csv(response.read().decode("utf-8"))


def fetch_yf_closes(symbol: str, period: str = "1y") -> Series:
    hist = yf.Ticker(symbol).history(period=period, timeout=_TIMEOUT_S)
    closes = [
        (ts.date(), float(close)) for ts, close in hist["Close"].items() if _finite(float(close))
    ]
    if not closes:
        raise ValueError(f"no closes for {symbol}")
    return closes


def _guard(name: str, build: Callable[[], Reading]) -> Reading:
    try:
        return build()
    except Exception as exc:
        logger.warning("market_trend: %s unavailable: %s", name, exc)
        return Reading(name, "unknown", "n/a", f"Data unavailable ({type(exc).__name__}: {exc})"[:200])


def _latest(classify, symbol: str) -> Reading:
    closes = fetch_yf_closes(symbol)
    as_of, level = closes[-1]
    return classify(level, as_of)


def fetch_market_trend(today: date | None = None) -> list[Reading]:
    """All six readings, in display order. Never raises. Blocking: call via asyncio.to_thread."""
    today = today or market_session_date()
    start = today - timedelta(days=800)  # a year of inversion memory plus margin

    curve = _guard(CURVE, lambda: classify_yield_curve(
        fetch_fred_series("DGS2", start=start), fetch_fred_series("DGS10", start=start),
    ))
    vix = _guard(VIX, lambda: _latest(classify_vix, "^VIX"))
    cuts = _guard(RATE_CUT, lambda: classify_rate_cuts(fetch_fred_series("DFEDTARU", start=start), today))
    move = _guard(MOVE, lambda: _latest(classify_move, "^MOVE"))
    skew = _guard(SKEW, lambda: classify_skew(fetch_yf_closes("^SKEW"), vix.level))
    vvix = _guard(VVIX, lambda: _latest(classify_vvix, "^VVIX"))
    return [curve, vix, cuts, move, skew, vvix]
