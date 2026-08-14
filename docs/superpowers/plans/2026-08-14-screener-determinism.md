# Screener Determinism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the fundamental filter's missing-data behavior explicit rather than silently permissive, and make the volume filter independent of what time of day the scan runs.

**Architecture:** `passes_fundamental_filter` gains a sibling that returns a structured result naming every missing field, with a configurable policy for what missing data means. `fetch_technical_data` drops the current session's partial bar before computing volume statistics, and records the data timestamp and session state alongside every indicator.

**Tech Stack:** Python 3.11, pandas, yfinance, zoneinfo, pytest

**Spec:** `docs/superpowers/plans/2026-08-14-codex-backlog-roadmap.md` (Workstream D0); source findings in `codex_recommendations.md` §8 and §13

## Global Constraints

- Python 3.11; `zoneinfo` from the stdlib — no new dependencies
- Filter functions stay pure; only `fetch_*` functions do I/O
- `passes_fundamental_filter` keeps its current name and `bool` return so existing callers and the 546-test suite keep working; the structured version is additive
- Test files use module-level fixtures matching `tests/test_screener_technicals.py`
- Commit after every task

---

## Decision required before Task 1

`FUNDAMENTAL_MISSING_POLICY` defaults to **`reject`** in this plan, meaning a stock with no
`earningsGrowth` data no longer passes the growth check by omission.

**This visibly shrinks your candidate set.** Today `screener/fundamentals.py:53` skips the
growth check when the value is absent, and yfinance omits `earningsGrowth` for a meaningful
fraction of tickers. Switching the default to `reject` is what Codex finding 8 asks for
("reject insufficient data or assign a documented uncertainty penalty"), but it is a
behavior change, not a bug fix.

If you would rather keep today's behavior while gaining visibility, set the default to
`allow` — the filter still records and logs which fields were missing, so you can measure
the impact before changing the policy. **Confirm which default you want before Task 1.**

---

### Task 1: Structured fundamental evaluation

**Files:**
- Modify: `screener/fundamentals.py` (append; leave `passes_fundamental_filter` intact)
- Modify: `config.py`
- Test: `tests/test_fundamental_missing_data.py`

**Interfaces:**
- Consumes: `screener.fundamentals.normalize_dividend_yield`
- Produces: `evaluate_fundamentals(info: dict, config) -> dict` returning
  `{"passed": bool, "reason": str, "missing": list[str]}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fundamental_missing_data.py
import pytest
from types import SimpleNamespace
from screener.fundamentals import evaluate_fundamentals


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


def test_complete_data_passes():
    r = evaluate_fundamentals(_info(), _cfg())
    assert r["passed"] is True and r["missing"] == []


def test_missing_pe_always_rejected_regardless_of_policy():
    r = evaluate_fundamentals(_info(pe=None), _cfg(policy="allow"))
    assert r["passed"] is False
    assert r["reason"] == "missing_required_pe"
    assert "trailingPE" in r["missing"]


def test_missing_growth_is_reported_even_when_allowed():
    r = evaluate_fundamentals(_info(growth=None), _cfg(policy="allow"))
    assert r["passed"] is True
    assert r["missing"] == ["earningsGrowth"]


def test_missing_growth_rejected_under_reject_policy():
    r = evaluate_fundamentals(_info(growth=None), _cfg(policy="reject"))
    assert r["passed"] is False
    assert r["reason"] == "insufficient_data"
    assert r["missing"] == ["earningsGrowth"]


def test_multiple_missing_fields_all_reported():
    r = evaluate_fundamentals(_info(div=None, growth=None), _cfg(policy="allow"))
    assert sorted(r["missing"]) == ["dividendYield", "earningsGrowth"]


def test_failing_threshold_beats_missing_data_in_the_reason():
    """A present-but-failing value is a threshold rejection, not a data problem."""
    r = evaluate_fundamentals(_info(pe=99.0, growth=None), _cfg(policy="reject"))
    assert r["passed"] is False
    assert r["reason"] == "pe_too_high"


def test_dividend_yield_is_normalised_from_percent():
    """3.0 from yfinance means 3%, which clears a 2% minimum."""
    assert evaluate_fundamentals(_info(div=3.0), _cfg())["passed"] is True
    assert evaluate_fundamentals(_info(div=1.0), _cfg())["passed"] is False


def test_legacy_boolean_filter_still_works():
    from screener.fundamentals import passes_fundamental_filter
    assert passes_fundamental_filter(_info(), _cfg()) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fundamental_missing_data.py -v`
Expected: FAIL — `ImportError: cannot import name 'evaluate_fundamentals'`

- [ ] **Step 3: Add the config field**

In `config.py`, next to `min_earnings_growth`:

```python
    fundamental_missing_policy: str = _env_str("FUNDAMENTAL_MISSING_POLICY", "reject")
```

- [ ] **Step 4: Write the implementation**

Append to `screener/fundamentals.py`:

```python
def evaluate_fundamentals(info: dict, config) -> dict:
    """Evaluate fundamentals, reporting missing fields explicitly.

    Codex finding 8: the boolean filter skips the yield and growth checks when
    the value is absent, so a company with no growth data passes a nominal
    growth filter silently. This version names every missing field and applies
    config.fundamental_missing_policy:

      'reject' — missing optional data fails the candidate (default)
      'allow'  — missing optional data passes, but is still reported

    trailingPE is required under both policies; valuation is non-negotiable.
    Threshold failures take precedence over missing-data failures in `reason`,
    because a value that is present and bad is a different signal from absent.
    """
    missing = []

    pe = info.get("trailingPE")
    raw_div = info.get("dividendYield")
    growth = info.get("earningsGrowth")

    if pe is None:
        missing.append("trailingPE")
    if raw_div is None:
        missing.append("dividendYield")
    if growth is None:
        missing.append("earningsGrowth")

    if pe is None:
        return {"passed": False, "reason": "missing_required_pe", "missing": missing}

    if pe > config.max_pe_ratio:
        return {"passed": False, "reason": "pe_too_high", "missing": missing}

    div_yield = normalize_dividend_yield(raw_div)
    if div_yield is not None and div_yield < config.min_dividend_yield:
        return {"passed": False, "reason": "yield_too_low", "missing": missing}

    if growth is not None and growth < config.min_earnings_growth:
        return {"passed": False, "reason": "growth_too_low", "missing": missing}

    optional_missing = [f for f in missing if f != "trailingPE"]
    if optional_missing and config.fundamental_missing_policy == "reject":
        return {"passed": False, "reason": "insufficient_data", "missing": optional_missing}

    return {"passed": True, "reason": "ok", "missing": optional_missing}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_fundamental_missing_data.py -v`
Expected: 8 passed

- [ ] **Step 6: Use it in `run_scan`**

In `main.py`, replace the `passes_fundamental_filter` call with:

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
Expected: green. If `tests/test_scan.py` expects tickers with missing growth to pass, those
assertions encode the old policy — update them and note the change.

- [ ] **Step 8: Commit**

```bash
git add screener/fundamentals.py config.py main.py tests/test_fundamental_missing_data.py
git commit -m "feat: explicit missing-data policy for the fundamental filter"
```

---

### Task 2: Detect a partial session bar

**Files:**
- Create: `screener/session.py`
- Test: `tests/test_session_state.py`

**Interfaces:**
- Consumes: nothing (pure; stdlib `zoneinfo` only)
- Produces:
  - `MARKET_TZ: ZoneInfo`
  - `is_partial_bar(bar_date, now_et) -> bool`
  - `session_state(now_et) -> str` returning `pre_open | open | closed | weekend`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_state.py
from datetime import datetime, date
from zoneinfo import ZoneInfo
from screener.session import is_partial_bar, session_state, MARKET_TZ

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_todays_bar_during_market_hours_is_partial():
    # Wednesday 2026-08-12, 11:00 ET — mid-session
    assert is_partial_bar(date(2026, 8, 12), _et(2026, 8, 12, 11, 0)) is True


def test_todays_bar_after_close_is_complete():
    assert is_partial_bar(date(2026, 8, 12), _et(2026, 8, 12, 16, 30)) is False


def test_todays_bar_before_open_is_not_partial():
    """Before 09:30 there is no session bar for today yet; anything dated today is stale."""
    assert is_partial_bar(date(2026, 8, 12), _et(2026, 8, 12, 8, 0)) is False


def test_yesterdays_bar_is_always_complete():
    assert is_partial_bar(date(2026, 8, 11), _et(2026, 8, 12, 11, 0)) is False


def test_session_state_values():
    assert session_state(_et(2026, 8, 12, 8, 0)) == "pre_open"
    assert session_state(_et(2026, 8, 12, 11, 0)) == "open"
    assert session_state(_et(2026, 8, 12, 17, 0)) == "closed"
    # 2026-08-15 is a Saturday
    assert session_state(_et(2026, 8, 15, 11, 0)) == "weekend"


def test_exactly_at_open_is_open():
    assert session_state(_et(2026, 8, 12, 9, 30)) == "open"


def test_exactly_at_close_is_closed():
    assert session_state(_et(2026, 8, 12, 16, 0)) == "closed"


def test_market_tz_is_new_york():
    assert str(MARKET_TZ) == "America/New_York"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_session_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screener.session'`

- [ ] **Step 3: Write the implementation**

```python
# screener/session.py
"""US market session state, used to keep signals independent of scan time.

Codex finding 13: the volume filter compares the latest daily bar against a
20-bar average. Mid-session that latest bar is a partial day, so a qualifying
stock is rejected purely because the scan ran early.

Uses regular-hours boundaries only and does not model market holidays — a
holiday simply produces no bar for that date, which the partial-bar check
handles correctly since it compares against the bar's own date.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")

_OPEN = time(9, 30)
_CLOSE = time(16, 0)


def session_state(now_et: datetime) -> str:
    """Return 'weekend' | 'pre_open' | 'open' | 'closed' for an ET datetime."""
    if now_et.weekday() >= 5:
        return "weekend"
    current = now_et.time()
    if current < _OPEN:
        return "pre_open"
    if current >= _CLOSE:
        return "closed"
    return "open"


def is_partial_bar(bar_date: date, now_et: datetime) -> bool:
    """True when bar_date is today's bar and the session is still in progress.

    Only a bar dated today can be partial, and only while the market is open.
    Before the open, a bar dated today would be a data artifact rather than a
    live session, so it is treated as complete rather than dropped.
    """
    return bar_date == now_et.date() and session_state(now_et) == "open"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_session_state.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add screener/session.py tests/test_session_state.py
git commit -m "feat: market session state and partial-bar detection"
```

---

### Task 3: Drop the partial bar from volume statistics

**Files:**
- Modify: `screener/technicals.py:99-135` (`fetch_technical_data`)
- Test: `tests/test_volume_completed_bar.py`

**Interfaces:**
- Consumes: `screener.session.is_partial_bar`, `screener.session.MARKET_TZ`
- Produces: `fetch_technical_data` gains `data_timestamp: str` and `session_state: str` keys

- [ ] **Step 1: Write the failing test**

```python
# tests/test_volume_completed_bar.py
import pandas as pd
import pytest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
from screener.technicals import fetch_technical_data

ET = ZoneInfo("America/New_York")


def _hist(n=60, last_volume=1_000):
    """n daily bars ending today; all but the last have volume 1,000,000."""
    idx = pd.date_range(end=pd.Timestamp("2026-08-12"), periods=n, freq="D")
    return pd.DataFrame(
        {
            "Close": [100.0 + i * 0.1 for i in range(n)],
            "Volume": [1_000_000] * (n - 1) + [last_volume],
        },
        index=idx,
    )


class _FakeTicker:
    pass


def test_partial_bar_excluded_from_volume_during_session():
    """A tiny partial-session volume must not be compared against full sessions."""
    with patch("screener.technicals._fetch_history", return_value=_hist()), \
         patch("screener.technicals._now_et", return_value=datetime(2026, 8, 12, 11, 0, tzinfo=ET)):
        data = fetch_technical_data(_FakeTicker())
    assert data["volume"] == 1_000_000       # yesterday's completed bar
    assert data["session_state"] == "open"


def test_completed_bar_used_after_close():
    with patch("screener.technicals._fetch_history", return_value=_hist(last_volume=2_000_000)), \
         patch("screener.technicals._now_et", return_value=datetime(2026, 8, 12, 17, 0, tzinfo=ET)):
        data = fetch_technical_data(_FakeTicker())
    assert data["volume"] == 2_000_000       # today's bar is complete
    assert data["session_state"] == "closed"


def test_avg_volume_also_excludes_the_partial_bar():
    with patch("screener.technicals._fetch_history", return_value=_hist(last_volume=1)), \
         patch("screener.technicals._now_et", return_value=datetime(2026, 8, 12, 11, 0, tzinfo=ET)):
        data = fetch_technical_data(_FakeTicker())
    assert data["avg_volume"] == 1_000_000   # not dragged down by the 1-share bar


def test_data_timestamp_recorded():
    with patch("screener.technicals._fetch_history", return_value=_hist()), \
         patch("screener.technicals._now_et", return_value=datetime(2026, 8, 12, 11, 0, tzinfo=ET)):
        data = fetch_technical_data(_FakeTicker())
    assert data["data_timestamp"].startswith("2026-08-11")   # the bar actually used


def test_price_still_uses_the_latest_close():
    """Only volume statistics change; price must stay live."""
    hist = _hist()
    with patch("screener.technicals._fetch_history", return_value=hist), \
         patch("screener.technicals._now_et", return_value=datetime(2026, 8, 12, 11, 0, tzinfo=ET)):
        data = fetch_technical_data(_FakeTicker())
    assert data["price"] == pytest.approx(hist["Close"].iloc[-1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_volume_completed_bar.py -v`
Expected: FAIL — `AttributeError: module 'screener.technicals' has no attribute '_now_et'`

- [ ] **Step 3: Write the implementation**

In `screener/technicals.py`, add near the top:

```python
from datetime import datetime
from screener.session import MARKET_TZ, is_partial_bar, session_state


def _now_et() -> datetime:
    """Current time in market timezone. Separate function so tests can patch it."""
    return datetime.now(MARKET_TZ)
```

Replace the volume block at lines 119-120:

```python
    now_et = _now_et()
    volumes = hist["Volume"]
    last_bar_date = hist.index[-1].date()

    # Codex finding 13: a mid-session bar is a partial day. Comparing it against
    # 20 full sessions rejects qualifying stocks purely because the scan ran early.
    if is_partial_bar(last_bar_date, now_et) and len(volumes) > 1:
        volumes = volumes.iloc[:-1]

    volume = volumes.iloc[-1]
    avg_volume = volumes.tail(20).mean()
    data_timestamp = volumes.index[-1].isoformat()
```

Add to the returned dict:

```python
        "data_timestamp": data_timestamp,
        "session_state": session_state(now_et),
```

Also add both keys to the early-return dict at lines 104-113 with values `None` and
`session_state(_now_et())` respectively, so the shape is consistent.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_volume_completed_bar.py -v`
Expected: 5 passed

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: green. `tests/test_screener_technicals.py` builds synthetic histories whose last
index may be dated today — if any fail, they are asserting on the partial-bar path and
should patch `_now_et` to an after-close time.

- [ ] **Step 6: Commit**

```bash
git add screener/technicals.py tests/test_volume_completed_bar.py
git commit -m "fix: exclude partial session bars from volume statistics"
```

---

### Task 4: Documentation

**Files:** `CLAUDE.md`, `.env.example`

- [ ] **Step 1: Update `.env.example`** with `FUNDAMENTAL_MISSING_POLICY=reject`

- [ ] **Step 2: Update CLAUDE.md** — the Technical Indicator Notes section:

```markdown
`screener/technicals.py` calculates RSI using Wilder's smoothing (not simple EWM) and
requires a minimum of 51 price data points (50-day MA + 1). Volume statistics exclude the
current session's partial bar (`screener/session.is_partial_bar`), so a scan at 09:05 and a
scan at 15:55 see the same volume figures. Every technical result carries `data_timestamp`
(the bar actually used) and `session_state`.
```

And add to Key Design Decisions:

```markdown
- **Explicit missing-data policy**: `evaluate_fundamentals` names every absent field and
  applies `FUNDAMENTAL_MISSING_POLICY` (`reject` default, `allow` for legacy behavior).
  `trailingPE` is required under both. The older boolean `passes_fundamental_filter` remains
  for compatibility but new code should use the structured version.
```

- [ ] **Step 3: Run the full suite and linter**

Run: `pytest -q && ruff check .`

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .env.example
git commit -m "docs: document missing-data policy and session-aware volume"
```

---

## Self-Review

**Spec coverage:**
- Finding 8 (missing values silently pass) → Task 1 ✓
- Finding 8 (track missing explicitly) → Task 1, `missing` list ✓
- Finding 8 (reject insufficient data) → Task 1, `reject` policy ✓
- Finding 13 (partial bar in volume comparison) → Tasks 2, 3 ✓
- Finding 13 (record data timestamp and session state) → Task 3 ✓

**Deliberately not covered:**
- Finding 8's "add liquidity and financial-quality requirements" and "use sector-aware
  valuation measures" — these are factor design, which belongs to Workstream D and needs the
  research harness to justify any specific threshold.
- Finding 8's "validate all yfinance field units with recorded fixtures and contract tests" —
  worth doing, but it is a test-infrastructure project of its own. The
  `normalize_dividend_yield` docstring at `screener/fundamentals.py:19-22` shows the risk is
  already understood; a contract-test suite should be scoped separately.
- Finding 13's "compare intraday volume against time-of-day-normalized historical volume" —
  the completed-bar approach solves the stated problem far more simply. Only worth revisiting
  if you later want intraday scans.

**Type consistency:** `evaluate_fundamentals` returns `{"passed", "reason", "missing"}` in
Task 1 and is consumed with those keys in `main.py` ✓. `is_partial_bar(bar_date, now_et)`
and `session_state(now_et)` signatures in Task 2 match their call sites in Task 3 ✓.
`_now_et` is defined in Task 3 and patched by name in the Task 3 tests ✓.
