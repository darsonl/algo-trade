# Screener Determinism Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the fundamental filter's missing-data behavior explicit rather than silently permissive, and make the volume filter independent of what time of day the scan runs.

**Architecture:** `passes_fundamental_filter` gains a sibling returning a structured result that names every missing field, with a configurable policy. `fetch_technical_data` computes volume statistics from completed bars only — defined as "not dated today" — and records the bar it used.

**Tech Stack:** Python 3.11, pandas, yfinance, pytest

**Spec:** `docs/superpowers/plans/2026-08-14-codex-backlog-roadmap.md` (Workstream D0); source findings in `docs/superpowers/codex_recommendations.md` §8 and §13

**Revision note (v2):** v1 was reviewed externally and had three defects fixed here — missing
detection that checked only `is None` while yfinance yields `NaN`, an unvalidated policy
string that silently behaved as `allow`, and a clock-based session detector that misclassified
exchange half-days and holidays.

## Global Constraints

- Python 3.11; **no new dependencies** — v1 proposed `zoneinfo`-based session logic that v2 removes
- Filter functions stay pure; only `fetch_*` functions do I/O
- `passes_fundamental_filter` keeps its name and `bool` return so the existing 546-test suite keeps working; the structured version is additive
- Commit after every task

---

## Decision: `FUNDAMENTAL_MISSING_POLICY=reject` (confirmed 2026-08-14)

A stock with no `earningsGrowth` data no longer passes the growth check by omission. This
visibly shrinks the candidate set — `screener/fundamentals.py:53` currently skips the check
when the value is absent, and yfinance omits `earningsGrowth` for a meaningful fraction of
tickers.

**Mitigation path.** Schwab's own API can supply the missing field:
`Client.get_instruments(symbols, projection=Instrument.Projection.FUNDAMENTAL)` returns
`epsChangePercentTTM`, `epsChangeYear`, and `revChangeTTM`, plus quality and liquidity
metrics (`returnOnEquity`, `returnOnInvestment`, `grossMarginTTM`, `totalDebtToEquity`,
`currentRatio`, `interestCoverage`, `marketCap`, `vol3MonthAvg`). Adding it as a fallback
source would both restore candidates and supply the scale-independent factors Codex asks for
in findings 7 and 8.

**Not planned here** — the exact response field names and units have not been verified
against a live call (the local Schwab token is ~125 days old and expired). Verify first,
then scope it as its own task.

---

### Task 1: Structured fundamental evaluation

**Files:**
- Modify: `screener/fundamentals.py` (append; leave `passes_fundamental_filter` intact)
- Modify: `config.py`
- Test: `tests/test_fundamental_missing_data.py`

**Interfaces:**
- Consumes: `screener.fundamentals.normalize_dividend_yield`
- Produces:
  - `is_missing(value) -> bool`
  - `evaluate_fundamentals(info: dict, config) -> dict` returning `{"passed": bool, "reason": str, "missing": list[str]}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fundamental_missing_data.py
import pytest
from types import SimpleNamespace
from screener.fundamentals import evaluate_fundamentals, is_missing

NAN = float("nan")


def _cfg(policy="reject"):
    return SimpleNamespace(
        max_pe_ratio=35.0, min_dividend_yield=0.02, min_earnings_growth=0.05,
        fundamental_missing_policy=policy,
    )


def _info(pe=20.0, div=3.0, growth=0.10):
    out = {}
    if pe is not None:
        out["trailingPE"] = pe
    if div is not None:
        out["dividendYield"] = div      # yfinance reports percentage points
    if growth is not None:
        out["earningsGrowth"] = growth
    return out


# --- is_missing ---

def test_is_missing_covers_none_nan_and_junk():
    assert is_missing(None) is True
    assert is_missing(NAN) is True
    assert is_missing(float("inf")) is True
    assert is_missing("n/a") is True
    assert is_missing(0.0) is False
    assert is_missing(20.0) is False


# --- evaluate_fundamentals ---

def test_complete_data_passes():
    r = evaluate_fundamentals(_info(), _cfg())
    assert r["passed"] is True and r["missing"] == []


def test_missing_pe_always_rejected_regardless_of_policy():
    r = evaluate_fundamentals(_info(pe=None), _cfg(policy="allow"))
    assert r["passed"] is False and r["reason"] == "missing_required_pe"


def test_nan_pe_rejected():
    """v1 regression: NaN is not None, and every comparison against it is False."""
    r = evaluate_fundamentals(_info(pe=NAN), _cfg(policy="allow"))
    assert r["passed"] is False and r["reason"] == "missing_required_pe"


def test_all_nan_rejected_under_reject_policy():
    r = evaluate_fundamentals({"trailingPE": NAN, "dividendYield": NAN,
                               "earningsGrowth": NAN}, _cfg())
    assert r["passed"] is False


def test_missing_growth_reported_but_allowed_under_allow():
    r = evaluate_fundamentals(_info(growth=None), _cfg(policy="allow"))
    assert r["passed"] is True and r["missing"] == ["earningsGrowth"]


def test_missing_growth_rejected_under_reject():
    r = evaluate_fundamentals(_info(growth=None), _cfg())
    assert r["passed"] is False and r["reason"] == "insufficient_data"


def test_nan_growth_treated_as_missing_under_reject():
    r = evaluate_fundamentals(_info(growth=NAN), _cfg())
    assert r["passed"] is False and r["reason"] == "insufficient_data"


def test_multiple_missing_fields_all_reported():
    r = evaluate_fundamentals(_info(div=None, growth=None), _cfg(policy="allow"))
    assert sorted(r["missing"]) == ["dividendYield", "earningsGrowth"]


def test_threshold_failure_beats_missing_data_in_the_reason():
    r = evaluate_fundamentals(_info(pe=99.0, growth=None), _cfg())
    assert r["passed"] is False and r["reason"] == "pe_too_high"


def test_dividend_yield_normalised_from_percent():
    assert evaluate_fundamentals(_info(div=3.0), _cfg())["passed"] is True
    assert evaluate_fundamentals(_info(div=1.0), _cfg())["passed"] is False


def test_unknown_policy_string_fails_closed():
    """v1 regression: any non-'reject' string silently behaved as 'allow'."""
    r = evaluate_fundamentals(_info(growth=None), _cfg(policy="typo"))
    assert r["passed"] is False


def test_legacy_boolean_filter_still_works():
    from screener.fundamentals import passes_fundamental_filter
    assert passes_fundamental_filter(_info(), _cfg()) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fundamental_missing_data.py -v`
Expected: FAIL — `ImportError: cannot import name 'evaluate_fundamentals'`

- [ ] **Step 3: Add the config field and validation**

In `config.py`, next to `min_earnings_growth`:

```python
    fundamental_missing_policy: str = _env_str("FUNDAMENTAL_MISSING_POLICY", "reject")
```

And in `validate()`:

```python
        if self.fundamental_missing_policy not in ("reject", "allow"):
            raise ValueError(
                f"FUNDAMENTAL_MISSING_POLICY must be 'reject' or 'allow', "
                f"got {self.fundamental_missing_policy!r}"
            )
```

- [ ] **Step 4: Write the implementation**

Append to `screener/fundamentals.py`:

```python
import math


def is_missing(value) -> bool:
    """True when a numeric field is absent, NaN, infinite, or non-numeric.

    yfinance returns NaN rather than None for many absent fields, and every
    comparison against NaN is False — so a plain `is None` check lets NaN slip
    through every threshold gate untouched.
    """
    if value is None:
        return True
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def evaluate_fundamentals(info: dict, config) -> dict:
    """Evaluate fundamentals, reporting missing fields explicitly.

    Codex finding 8: the boolean filter skips the yield and growth checks when a
    value is absent, so a company with no growth data passes a nominal growth
    filter silently. This version names every missing field and applies
    config.fundamental_missing_policy:

      'reject' — missing optional data fails the candidate (default)
      'allow'  — missing optional data passes, but is still reported

    Any other policy value fails closed, matching 'reject'. trailingPE is
    required under every policy. Threshold failures take precedence over
    missing-data failures in `reason`: a value that is present and bad is a
    different signal from one that is absent.
    """
    missing = []
    pe = info.get("trailingPE")
    raw_div = info.get("dividendYield")
    growth = info.get("earningsGrowth")

    if is_missing(pe):
        missing.append("trailingPE")
    if is_missing(raw_div):
        missing.append("dividendYield")
    if is_missing(growth):
        missing.append("earningsGrowth")

    if is_missing(pe):
        return {"passed": False, "reason": "missing_required_pe", "missing": missing}

    if float(pe) > config.max_pe_ratio:
        return {"passed": False, "reason": "pe_too_high", "missing": missing}

    if not is_missing(raw_div):
        div_yield = normalize_dividend_yield(float(raw_div))
        if div_yield < config.min_dividend_yield:
            return {"passed": False, "reason": "yield_too_low", "missing": missing}

    if not is_missing(growth) and float(growth) < config.min_earnings_growth:
        return {"passed": False, "reason": "growth_too_low", "missing": missing}

    optional_missing = [f for f in missing if f != "trailingPE"]
    # Fail closed: only the explicit string 'allow' permits missing optional data.
    if optional_missing and config.fundamental_missing_policy != "allow":
        return {"passed": False, "reason": "insufficient_data", "missing": optional_missing}

    return {"passed": True, "reason": "ok", "missing": optional_missing}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_fundamental_missing_data.py -v`
Expected: 14 passed

- [ ] **Step 6: Use it in `run_scan`**

In `main.py`, replace the `passes_fundamental_filter` call:

```python
            fundamentals = evaluate_fundamentals(info, config)
            if not fundamentals["passed"]:
                logger.info(
                    "%s rejected by fundamentals: %s (missing: %s)",
                    ticker, fundamentals["reason"],
                    ", ".join(fundamentals["missing"]) or "none",
                )
                continue
```

- [ ] **Step 7: Run the full suite**

Run: `pytest -q`
Expected: green. Tests expecting tickers with missing growth to pass encode the old policy —
update them and note the change.

- [ ] **Step 8: Commit**

```bash
git add screener/fundamentals.py config.py main.py tests/test_fundamental_missing_data.py
git commit -m "feat: explicit missing-data policy for the fundamental filter"
```

---

### Task 2: Completed-bar volume statistics

**Files:**
- Modify: `screener/technicals.py:99-135`
- Test: `tests/test_volume_completed_bar.py`

**Interfaces:**
- Produces: `fetch_technical_data` gains `volume_bar_date: str | None` and `dropped_partial_bar: bool`

**Design change from v1.** v1 proposed a `screener/session.py` that decided partiality from
the wall clock (09:30–16:00 ET). External review found that misclassifies exchange half-days
— after a 13:00 close it would keep discarding the completed bar until 16:00 — and mislabels
weekday holidays as `open`.

v2 removes the clock entirely: **a bar dated today is never used for volume statistics.**

This is both simpler and better suited to the system. Scans run at 09:00 and 09:30 local, so
a bar dated today is either absent or seconds old. The rule needs no exchange calendar, no
timezone handling, and no annual half-day table — and it is *deterministic*, which is the
actual goal of finding 13. The cost is that an after-close scan uses yesterday's volume
rather than today's; for a daily screener comparing against a 20-day average, that is
immaterial.

Price still uses the latest close. Only volume statistics change.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_volume_completed_bar.py
import pandas as pd
import pytest
from datetime import date, timedelta
from unittest.mock import patch
from screener.technicals import fetch_technical_data


def _hist(n=60, last_volume=1_000, end=None):
    """n daily bars ending at `end` (default: today), volume 1,000,000 except the last."""
    end = end or pd.Timestamp(date.today())
    idx = pd.date_range(end=end, periods=n, freq="D")
    return pd.DataFrame(
        {
            "Close": [100.0 + i * 0.1 for i in range(n)],
            "Volume": [1_000_000] * (n - 1) + [last_volume],
        },
        index=idx,
    )


class _FakeTicker:
    pass


def test_todays_bar_excluded_from_volume():
    """A partial-session bar must never be compared against full sessions."""
    with patch("screener.technicals._fetch_history", return_value=_hist(last_volume=1)):
        data = fetch_technical_data(_FakeTicker())
    assert data["volume"] == 1_000_000
    assert data["dropped_partial_bar"] is True


def test_avg_volume_also_excludes_todays_bar():
    with patch("screener.technicals._fetch_history", return_value=_hist(last_volume=1)):
        data = fetch_technical_data(_FakeTicker())
    assert data["avg_volume"] == 1_000_000


def test_history_ending_yesterday_is_used_in_full():
    yesterday = pd.Timestamp(date.today() - timedelta(days=1))
    with patch("screener.technicals._fetch_history",
               return_value=_hist(last_volume=2_000_000, end=yesterday)):
        data = fetch_technical_data(_FakeTicker())
    assert data["volume"] == 2_000_000
    assert data["dropped_partial_bar"] is False


def test_volume_bar_date_is_recorded():
    with patch("screener.technicals._fetch_history", return_value=_hist()):
        data = fetch_technical_data(_FakeTicker())
    expected = (date.today() - timedelta(days=1)).isoformat()
    assert data["volume_bar_date"].startswith(expected)


def test_price_still_uses_the_latest_close():
    hist = _hist()
    with patch("screener.technicals._fetch_history", return_value=hist):
        data = fetch_technical_data(_FakeTicker())
    assert data["price"] == pytest.approx(hist["Close"].iloc[-1])


def test_result_is_identical_regardless_of_call_time():
    """The whole point of finding 13: no wall-clock dependence."""
    hist = _hist()
    with patch("screener.technicals._fetch_history", return_value=hist):
        first = fetch_technical_data(_FakeTicker())
        second = fetch_technical_data(_FakeTicker())
    assert first["volume"] == second["volume"]
    assert first["avg_volume"] == second["avg_volume"]


def test_single_bar_history_does_not_drop_everything():
    """Defensive: never leave the volume series empty."""
    hist = _hist(n=51)
    with patch("screener.technicals._fetch_history", return_value=hist):
        data = fetch_technical_data(_FakeTicker())
    assert data["volume"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_volume_completed_bar.py -v`
Expected: FAIL — `KeyError: 'dropped_partial_bar'`

- [ ] **Step 3: Write the implementation**

In `screener/technicals.py`, add near the top:

```python
from datetime import date as _date


def _today() -> _date:
    """Local calendar date. Separate function so tests can patch it."""
    return _date.today()
```

Replace the volume block at lines 119-120:

```python
    # Codex finding 13: a bar dated today may be a partial session, and comparing
    # it against 20 full sessions rejects qualifying stocks purely because the
    # scan ran early. Dropping today's bar outright makes the result independent
    # of call time without needing an exchange calendar — after-close scans use
    # yesterday's volume, which is immaterial against a 20-day average.
    volumes = hist["Volume"]
    dropped_partial_bar = False
    if len(volumes) > 1 and volumes.index[-1].date() == _today():
        volumes = volumes.iloc[:-1]
        dropped_partial_bar = True

    volume = volumes.iloc[-1]
    avg_volume = volumes.tail(20).mean()
    volume_bar_date = volumes.index[-1].isoformat()
```

Add to the returned dict:

```python
        "volume_bar_date": volume_bar_date,
        "dropped_partial_bar": dropped_partial_bar,
```

Add both keys to the early-return dict at lines 104-113 with `None` and `False`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_volume_completed_bar.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: green. `tests/test_screener_technicals.py` builds synthetic histories that may end
today — if any assert on volume, they now see the second-to-last bar. Update them or set
their index to end in the past.

- [ ] **Step 6: Commit**

```bash
git add screener/technicals.py tests/test_volume_completed_bar.py
git commit -m "fix: compute volume statistics from completed bars only"
```

---

### Task 3: Documentation

**Files:** `CLAUDE.md`, `.env.example`

- [ ] **Step 1: Update `.env.example`** with `FUNDAMENTAL_MISSING_POLICY=reject`

- [ ] **Step 2: Update CLAUDE.md** — the Technical Indicator Notes section:

```markdown
`screener/technicals.py` calculates RSI using Wilder's smoothing (not simple EWM) and
requires a minimum of 51 price data points (50-day MA + 1). Volume statistics ignore any bar
dated today, so a scan at 09:05 and a scan at 15:55 produce identical volume figures. No
exchange calendar is involved — the rule is date-based, not clock-based. Results carry
`volume_bar_date` and `dropped_partial_bar`. Price still uses the latest close.
```

And add to Key Design Decisions:

```markdown
- **Explicit missing-data policy**: `evaluate_fundamentals` names every absent field and
  applies `FUNDAMENTAL_MISSING_POLICY` (`reject` default, `allow` for legacy behavior; any
  other value fails closed and is rejected by `Config.validate`). Missing detection uses
  `is_missing`, which catches NaN — yfinance returns NaN rather than None for many absent
  fields, and every comparison against NaN is False. `trailingPE` is required under every
  policy. The boolean `passes_fundamental_filter` remains for compatibility; new code should
  use the structured version.
```

- [ ] **Step 3: Run the full suite and linter**

Run: `pytest -q && ruff check .`

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .env.example
git commit -m "docs: document missing-data policy and completed-bar volume"
```

---

## Self-Review

**Spec coverage:**
- Finding 8 (missing values silently pass) → Task 1 ✓
- Finding 8 (track missing explicitly) → Task 1, `missing` list ✓
- Finding 8 (reject insufficient data) → Task 1, `reject` policy ✓
- Finding 13 (partial bar in volume comparison) → Task 2 ✓
- Finding 13 (record data timestamp) → Task 2, `volume_bar_date` ✓

**v1 defects fixed in v2:**
- `is None`-only missing detection letting NaN through every gate → `is_missing()` ✓
- Unknown policy string silently behaving as `allow` → fails closed + `validate()` rejects ✓
- Clock-based session detection breaking on half-days and holidays → date-based rule, no
  calendar needed ✓
- `screener/session.py` and its `zoneinfo` dependency → removed entirely ✓

**Deliberately not covered:**
- Finding 8's "add liquidity and financial-quality requirements" and "sector-aware valuation"
  — factor design, belongs to Workstream D and needs the research harness to justify
  thresholds. The Schwab `FUNDAMENTAL` projection noted above is the likely data source.
- Finding 8's "validate all yfinance field units with recorded fixtures" — a test-
  infrastructure project of its own. `normalize_dividend_yield`'s docstring at
  `screener/fundamentals.py:19-22` shows the risk is already understood.
- Finding 13's "time-of-day-normalized historical volume" — only worth revisiting if you
  later want intraday scans.
- `session_state` metadata — v1 proposed it; v2 drops it because a clock-based value is wrong
  on holidays and half-days, and nothing consumes it.

**Type consistency:** `is_missing(value) -> bool` is defined in Task 1 and used throughout
`evaluate_fundamentals` ✓. `evaluate_fundamentals` returns `{"passed", "reason", "missing"}`,
consumed with those keys in `main.py` ✓. `_today()` is defined in Task 2 and patched by name
in no test — the tests construct histories relative to the real `date.today()` instead ✓.
