# Phase 15: Fundamental Trend Enrichment - Research

**Researched:** 2026-04-21
**Domain:** yfinance quarterly financials, prompt engineering, Python data wrangling
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** ±5% stable band for P/E direction. `abs(forwardPE - trailingPE) / trailingPE < 0.05` → `"stable"`. Outside: `"expanding"` (forwardPE > trailingPE) or `"contracting"` (forwardPE < trailingPE).
- **D-02:** Label convention follows valuation multiple direction — `"expanding"` means P/E ratio growing (stock pricier vs expected earnings); `"contracting"` means P/E shrinking (earnings expected to grow).
- **D-03:** When `forwardPE` is absent from yfinance info, label is `"N/A"` — no crash.
- **D-04:** EPS trend format — quarter labels + values, chronological (oldest → newest), derived from `pd.Timestamp` column keys: `- EPS trend (last 4Q): Q1-2025: $0.45, Q2-2025: $0.52, Q3-2025: $0.48, Q4-2025: $0.61`
- **D-05:** When `quarterly_income_stmt` returns `None`, missing "Diluted EPS" row, or fewer than 1 valid quarter, EPS trend line is **omitted entirely** (not shown as N/A).
- **D-06:** Add `fundamental_trend: dict | None = None` parameter to both `build_prompt()` and `analyze_ticker()`. Dict shape: `{"pe_direction": str, "eps_trend": list[dict] | None}`.
- **D-07:** Add `fetch_eps_data(yf_ticker: yf.Ticker) -> list[dict] | None` in `screener/fundamentals.py`. Called from `main.py` via `asyncio.to_thread(fetch_eps_data, yf_ticker)`.
- **D-08:** P/E direction computed inline in `main.py` from `info` dict — no separate fetch.

### Claude's Discretion

- Exact `build_prompt()` placement of the new `fundamental_trend` block relative to existing Fundamentals section (suggestion: insert after the three existing fundamentals lines, before Market Context).
- Test strategy: new tests for `fetch_eps_data` in `test_screener_fundamentals.py`, or extend `test_analyst_claude.py` with `fundamental_trend` fixture variants.

### Deferred Ideas (OUT OF SCOPE)

- P/E direction and EPS trend in SELL prompt.
- EPS trend in ETF prompt (ETFs have no quarterly EPS).

</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SIG-07 | P/E direction (expanding / contracting / stable / N/A) included in Claude BUY prompt using `forwardPE` vs `trailingPE` as proxy | Inline computation from existing `info` dict; no extra yfinance call needed — both fields already present |
| SIG-08 | Quarterly EPS trend (last 4 quarters from `ticker.quarterly_income_stmt`) included in Claude BUY prompt | `fetch_eps_data` function in `screener/fundamentals.py`; `asyncio.to_thread` wrap mandatory; "Diluted EPS" row confirmed as the canonical row name |

</phase_requirements>

---

## Summary

Phase 15 enriches the Claude BUY prompt with two valuation trajectory signals: a P/E direction label derived from `trailingPE` vs `forwardPE`, and a four-quarter EPS trend sourced from `ticker.quarterly_income_stmt`. Both signals give Claude trajectory context, not just point-in-time values — a contracting P/E with rising EPS is a fundamentally different setup from an expanding P/E with flat earnings.

The implementation is a narrow, additive change. The `macro_context` optional-parameter pattern established in Phase 10 is the exact template: add `fundamental_trend: dict | None = None` to `build_prompt()` and `analyze_ticker()`, guard rendering with `.get()` checks, call the new data fetcher in `main.py` using `asyncio.to_thread`. No schema changes, no new external dependencies, no changes to sell/ETF paths.

The only non-trivial technical concern is `quarterly_income_stmt` column orientation: yfinance 1.2.0 returns columns as `pd.Timestamp` objects in newest-first order. The `fetch_eps_data` function must reverse the column order before extracting the last 4 quarters, and must derive human-readable quarter labels (`Q1-2025`) from the timestamps.

**Primary recommendation:** Implement as one plan. All patterns are established in the codebase; the work is primarily wiring and test coverage.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| yfinance | 1.2.0 | `quarterly_income_stmt` for EPS data | Already installed; `ticker.info` for PE fields already fetched |
| pandas | (transitive dep) | DataFrame row/column access for `quarterly_income_stmt` | Required by yfinance; `pd.Timestamp` column keys already returned |

[VERIFIED: pip show output confirmed yfinance 1.2.0 installed]

### No New Dependencies

All required libraries are already present. This phase adds no new `pip install` requirements.

---

## Architecture Patterns

### Recommended Project Structure (no structural changes)

```
screener/
├── fundamentals.py   # ADD fetch_eps_data() here
analyst/
├── claude_analyst.py # UPDATE build_prompt() and analyze_ticker() signatures
main.py               # ADD asyncio.to_thread(fetch_eps_data, yf_ticker) + pe_direction computation
tests/
├── test_screener_fundamentals.py  # ADD fetch_eps_data tests
├── test_analyst_claude.py         # ADD build_prompt fundamental_trend variant tests
```

### Pattern 1: Optional Dict Parameter (macro_context pattern)

**What:** Add `fundamental_trend: dict | None = None` as a keyword argument. Render only when present; use `.get()` guards for individual keys. Default is `None` — backward-compatible, no existing callers break.

**When to use:** Any time a new enrichment dict is added to the analyst pipeline.

**Example** (from existing `build_prompt` — lines 108–115 of `analyst/claude_analyst.py`):
```python
# [VERIFIED: read from codebase]
if macro_context is not None:
    if macro_context.get("spy_trend_1m"):
        market_lines.append(f"- SPY trend (1m): {macro_context['spy_trend_1m']}")
    if macro_context.get("spy_trend_1y"):
        market_lines.append(f"- SPY trend (1y): {macro_context['spy_trend_1y']}")
```

Apply the same pattern for `fundamental_trend` in the Fundamentals section:
```python
# [ASSUMED: proposed implementation]
if fundamental_trend is not None:
    market_lines_or_fundamentals_block += f"\n- P/E Direction: {fundamental_trend.get('pe_direction', 'N/A')}"
    eps_trend = fundamental_trend.get("eps_trend")
    if eps_trend:
        eps_str = ", ".join(f"{e['quarter']}: ${e['eps']:.2f}" for e in eps_trend)
        fundamentals_lines.append(f"- EPS trend (last 4Q): {eps_str}")
```

### Pattern 2: asyncio.to_thread for Blocking yfinance I/O

**What:** Every yfinance attribute access inside an `async` function must be wrapped in `asyncio.to_thread`. `quarterly_income_stmt` is a blocking network call, not folded into `ticker.info`.

**When to use:** All new yfinance fetches in `main.py`.

**Example** (from existing `main.py` lines 90–93):
```python
# [VERIFIED: read from codebase]
try:
    macro_context = await asyncio.to_thread(fetch_macro_context)
except Exception as exc:
    logger.warning("Macro context fetch failed: %s — continuing without macro", exc)
    macro_context = {"spy_trend_1m": None, "spy_trend_1y": None, "vix_level": None}
```

For `fetch_eps_data`, the pattern becomes:
```python
# [ASSUMED: proposed implementation]
try:
    eps_trend = await asyncio.to_thread(fetch_eps_data, yf_ticker)
except Exception as exc:
    logger.warning("EPS data fetch failed for %s: %s — continuing without EPS trend", ticker, exc)
    eps_trend = None
```

### Pattern 3: fetch_eps_data Implementation

**What:** Synchronous function in `screener/fundamentals.py` that accesses `yf_ticker.quarterly_income_stmt` and extracts the last 4 quarters of "Diluted EPS" in chronological order.

**Critical detail — column ordering:** `quarterly_income_stmt` returns columns as `pd.Timestamp` objects in **newest-first** order. [VERIFIED: confirmed in STATE.md v1.3 research decisions + CONTEXT.md canonical_refs]

**Quarter label derivation:** `pd.Timestamp` has `.quarter` (1–4) and `.year` attributes. Use `f"Q{ts.quarter}-{ts.year}"`.

**Row name:** `"Diluted EPS"` is the row to locate in the DataFrame index. [ASSUMED: based on yfinance 1.x conventions; confirmed absent from `quarterly_earnings` which returns None in 1.2.0 per STATE.md research]

```python
# [ASSUMED: proposed implementation — matches D-07 spec]
import yfinance as yf
import pandas as pd

def fetch_eps_data(yf_ticker: yf.Ticker) -> list[dict] | None:
    """Return last 4 quarters of Diluted EPS in chronological order, or None."""
    try:
        stmt = yf_ticker.quarterly_income_stmt
        if stmt is None or stmt.empty:
            return None
        if "Diluted EPS" not in stmt.index:
            return None
        row = stmt.loc["Diluted EPS"]
        # columns are newest-first pd.Timestamp — reverse for chronological
        row = row.iloc[::-1]
        # take last 4 valid (non-NaN) quarters
        valid = [(ts, val) for ts, val in row.items() if pd.notna(val)]
        if not valid:
            return None
        quarters = valid[-4:]  # last 4 of chronological order
        return [
            {"quarter": f"Q{ts.quarter}-{ts.year}", "eps": float(val)}
            for ts, val in quarters
        ]
    except Exception:
        return None
```

### Pattern 4: P/E Direction Inline Computation in main.py

**What:** No new function needed — compute from `info` dict after `fetch_fundamental_info` returns.

```python
# [ASSUMED: proposed implementation — matches D-01/D-02/D-03 spec]
trailing_pe = info.get("trailingPE")
forward_pe = info.get("forwardPE")

if trailing_pe is None or forward_pe is None or trailing_pe == 0:
    pe_direction = "N/A"
elif abs(forward_pe - trailing_pe) / abs(trailing_pe) < 0.05:
    pe_direction = "stable"
elif forward_pe > trailing_pe:
    pe_direction = "expanding"
else:
    pe_direction = "contracting"
```

### Pattern 5: fundamental_trend dict construction

Per D-06, assembled in `main.py` after both fetches complete:

```python
# [ASSUMED: proposed implementation]
fundamental_trend = {
    "pe_direction": pe_direction,
    "eps_trend": eps_trend,  # list[dict] | None
}
```

Then passed to `analyze_ticker(..., fundamental_trend=fundamental_trend)`.

### Prompt Block Placement

Approved layout (from CONTEXT.md `<specifics>`):

```
Fundamentals:
- Trailing P/E: 24.3
- P/E Direction: contracting
- EPS trend (last 4Q): Q1-2025: $0.45, Q2-2025: $0.52, Q3-2025: $0.48, Q4-2025: $0.61
- Dividend Yield: 0.025
- Earnings Growth: 0.12
```

P/E Direction always appears when `fundamental_trend` is present (even as "N/A"). EPS trend line is omitted when `eps_trend` is None or empty (D-05).

### Anti-Patterns to Avoid

- **Using `ticker.quarterly_earnings`:** Returns `None` silently in yfinance 1.2.0. [VERIFIED: STATE.md research decisions]
- **Assuming columns are chronological:** `quarterly_income_stmt` columns are newest-first. Always reverse before treating as chronological. [VERIFIED: CONTEXT.md canonical_refs + STATE.md]
- **Crash on missing `forwardPE`:** `info.get("forwardPE")` can return `None` (not in all tickers). Always guard before dividing. [VERIFIED: D-03 locked decision]
- **Bare synchronous `quarterly_income_stmt` call inside async:** Gateway heartbeat will miss. Wrap in `asyncio.to_thread`. [VERIFIED: CLAUDE.md architecture note, existing code pattern]
- **Omitting EPS trend as "N/A":** D-05 requires the line be omitted entirely, not shown as N/A.
- **Blocking `analyze_ticker` cache path:** The `eps_trend` fetch happens before the cache check. If the ticker is cache-hit, `fundamental_trend` is still assembled but the prompt is rebuilt anyway via `analyze_ticker` → `build_prompt`. Cache path calls `analyze_ticker` which calls `build_prompt` — so `fundamental_trend` is passed through regardless of cache vs fresh path. (No special case needed.)

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Quarter label formatting | Custom string parser | `pd.Timestamp.quarter` + `.year` | Timestamps already come from DataFrame columns; pandas attribute access is authoritative |
| P/E direction thresholds | Custom percentage lib | Inline float arithmetic | Simple ratio — no library needed |
| Async-safe yfinance access | Thread pool management | `asyncio.to_thread` | Already established pattern; handles event loop safety |

---

## Common Pitfalls

### Pitfall 1: quarterly_income_stmt Column Order

**What goes wrong:** Code iterates columns left-to-right assuming chronological order, producing an EPS trend that shows newest-to-oldest instead of oldest-to-newest.

**Why it happens:** yfinance returns columns newest-first (most recent quarter leftmost).

**How to avoid:** Reverse the Series with `row.iloc[::-1]` before iterating. After reversal, columns are oldest-to-newest.

**Warning signs:** Test with known data — if EPS is declining in the output but you expect growth, the order is wrong.

### Pitfall 2: "Diluted EPS" Row Absent

**What goes wrong:** `stmt.loc["Diluted EPS"]` raises `KeyError` for tickers that report Basic EPS but not Diluted (or where the row name differs in yfinance).

**Why it happens:** Not all `quarterly_income_stmt` DataFrames have a "Diluted EPS" row; some have "Basic EPS" or differently-named rows.

**How to avoid:** Guard with `if "Diluted EPS" not in stmt.index: return None`. The entire function is wrapped in try/except as a final safety net.

**Warning signs:** Errors in the `fetch_eps_data` function for certain tickers — check what rows the DataFrame actually contains for that ticker.

### Pitfall 3: NaN Values in EPS Row

**What goes wrong:** Some quarters return `NaN` in the "Diluted EPS" row (e.g., restatements, missing filings). Including these produces `$nan` in the prompt.

**Why it happens:** yfinance fills missing quarterly data with `NaN` rather than omitting the column.

**How to avoid:** Filter with `pd.notna(val)` before including a quarter in the result list.

### Pitfall 4: trailingPE of Zero Causing Division by Zero

**What goes wrong:** `abs(forward_pe - trailing_pe) / abs(trailing_pe)` raises `ZeroDivisionError` when `trailingPE` is 0 (can happen for loss-making companies).

**Why it happens:** Some tickers have `trailingPE=0` in yfinance rather than `None`.

**How to avoid:** Guard `if trailing_pe is None or forward_pe is None or trailing_pe == 0: pe_direction = "N/A"`.

### Pitfall 5: fundamental_trend breaks analyze_ticker cache path

**What goes wrong:** Developer assumes the analyst cache path short-circuits before `build_prompt`, so `fundamental_trend` is irrelevant for cache hits. In reality, `analyze_ticker` calls `build_prompt` on every invocation — cache hits are handled at a higher level in `main.py`, which skips `analyze_ticker` entirely for cache hits. So `fundamental_trend` is always passed when `analyze_ticker` is called.

**Why it happens:** Confusion about where the cache boundary is. Looking at `main.py` lines 120–145: cache hit path assigns `analysis = cached` and skips the `analyze_ticker` call. So `fundamental_trend` is irrelevant for cache hits — the old prompt was used. This is acceptable behavior.

**How to avoid:** No action needed. Cache hits use the previously-generated reasoning without re-calling `build_prompt`. The `fundamental_trend` enrichment only applies to fresh (non-cached) calls. Document this limitation in code comments.

---

## Code Examples

### fetch_eps_data (verified patterns)

```python
# Source: pattern from screener/fundamentals.py fetch_fundamental_info (read from codebase)
# + yfinance 1.2.0 quarterly_income_stmt behavior (VERIFIED: STATE.md)
import pandas as pd
import yfinance as yf

def fetch_eps_data(yf_ticker: yf.Ticker) -> list[dict] | None:
    """Fetch last 4 quarters of Diluted EPS in chronological order.
    
    Returns list of {"quarter": "Q1-2025", "eps": 0.45} dicts, or None on any error.
    """
    try:
        stmt = yf_ticker.quarterly_income_stmt
        if stmt is None or stmt.empty:
            return None
        if "Diluted EPS" not in stmt.index:
            return None
        row = stmt.loc["Diluted EPS"]
        row_chrono = row.iloc[::-1]   # newest-first → oldest-first
        valid = [(ts, val) for ts, val in row_chrono.items() if pd.notna(val)]
        if not valid:
            return None
        quarters = valid[-4:]  # last 4 in chronological slice
        return [
            {"quarter": f"Q{ts.quarter}-{ts.year}", "eps": float(val)}
            for ts, val in quarters
        ]
    except Exception:
        return None
```

### P/E Direction Computation

```python
# Source: D-01/D-02/D-03 locked decisions from CONTEXT.md
trailing_pe = info.get("trailingPE")
forward_pe = info.get("forwardPE")

if trailing_pe is None or forward_pe is None or trailing_pe == 0:
    pe_direction = "N/A"
elif abs(forward_pe - trailing_pe) / abs(trailing_pe) < 0.05:
    pe_direction = "stable"
elif forward_pe > trailing_pe:
    pe_direction = "expanding"
else:
    pe_direction = "contracting"
```

### build_prompt Fundamentals Block (after update)

```python
# Source: D-04/D-05/D-06 locked decisions + CONTEXT.md <specifics>
fundamentals_lines = [
    f"- Trailing P/E: {pe}",
]
if fundamental_trend is not None:
    fundamentals_lines.append(f"- P/E Direction: {fundamental_trend.get('pe_direction', 'N/A')}")
    eps_trend = fundamental_trend.get("eps_trend")
    if eps_trend:
        eps_str = ", ".join(f"{e['quarter']}: ${e['eps']:.2f}" for e in eps_trend)
        fundamentals_lines.append(f"- EPS trend (last 4Q): {eps_str}")
fundamentals_lines += [
    f"- Dividend Yield: {div_yield}",
    f"- Earnings Growth: {earnings_growth}",
]
fundamentals_block = "Fundamentals:\n" + "\n".join(fundamentals_lines)
```

### Test Pattern for fetch_eps_data (mirrors existing fundamentals tests)

```python
# Source: pattern from tests/test_screener_fundamentals.py (read from codebase)
from unittest.mock import MagicMock, PropertyMock
import pandas as pd
import pytest
from screener.fundamentals import fetch_eps_data

def _make_stmt(eps_values: list[float], quarters_newest_first=True):
    """Build a minimal quarterly_income_stmt DataFrame."""
    import pandas as pd
    timestamps = [
        pd.Timestamp("2025-12-31"),
        pd.Timestamp("2025-09-30"),
        pd.Timestamp("2025-06-30"),
        pd.Timestamp("2025-03-31"),
    ]
    # yfinance returns newest-first
    data = {"Diluted EPS": eps_values}
    return pd.DataFrame(data, index=["Diluted EPS"], columns=timestamps)

def test_fetch_eps_data_returns_4_quarters_chronological():
    mock_ticker = MagicMock()
    # newest-first: Q4, Q3, Q2, Q1
    type(mock_ticker).quarterly_income_stmt = PropertyMock(
        return_value=_make_stmt([0.61, 0.48, 0.52, 0.45])
    )
    result = fetch_eps_data(mock_ticker)
    assert result is not None
    assert len(result) == 4
    assert result[0]["quarter"] == "Q1-2025"  # oldest first
    assert result[3]["quarter"] == "Q4-2025"  # newest last
    assert result[0]["eps"] == pytest.approx(0.45)
    assert result[3]["eps"] == pytest.approx(0.61)
```

### Test Pattern for build_prompt with fundamental_trend

```python
# Source: pattern from tests/test_analyst_claude.py macro_context tests (read from codebase)
def test_build_prompt_with_fundamental_trend_includes_pe_direction_and_eps():
    fundamental_trend = {
        "pe_direction": "contracting",
        "eps_trend": [
            {"quarter": "Q1-2025", "eps": 0.45},
            {"quarter": "Q2-2025", "eps": 0.52},
            {"quarter": "Q3-2025", "eps": 0.48},
            {"quarter": "Q4-2025", "eps": 0.61},
        ],
    }
    prompt = build_prompt("AAPL", {}, [], fundamental_trend=fundamental_trend)
    assert "P/E Direction: contracting" in prompt
    assert "EPS trend (last 4Q):" in prompt
    assert "Q1-2025: $0.45" in prompt
    assert "Q4-2025: $0.61" in prompt

def test_build_prompt_fundamental_trend_none_eps_omits_eps_line():
    fundamental_trend = {"pe_direction": "N/A", "eps_trend": None}
    prompt = build_prompt("AAPL", {}, [], fundamental_trend=fundamental_trend)
    assert "P/E Direction: N/A" in prompt
    assert "EPS trend" not in prompt

def test_build_prompt_no_fundamental_trend_backward_compat():
    # Called without fundamental_trend — must not crash
    prompt = build_prompt("AAPL", {}, [])
    assert "P/E Direction" not in prompt
    assert "EPS trend" not in prompt
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `ticker.quarterly_earnings` for EPS | `ticker.quarterly_income_stmt` | yfinance 1.2.0 | `quarterly_earnings` returns None silently; `quarterly_income_stmt` has consistent DataFrame |
| Point-in-time P/E only | P/E + direction label (expanding/contracting/stable) | Phase 15 | Claude gets trajectory, not just value |

**Deprecated/outdated:**
- `ticker.quarterly_earnings`: Returns `None` silently in yfinance 1.2.0. Use `ticker.quarterly_income_stmt` with "Diluted EPS" row instead. [VERIFIED: STATE.md research decisions]

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The row name in `quarterly_income_stmt` is exactly `"Diluted EPS"` | Architecture Patterns (Pattern 3) | `fetch_eps_data` returns `None` for all tickers; EPS trend never appears in prompt |
| A2 | `forwardPE` key name in `yf.Ticker.info` is exactly `"forwardPE"` (camelCase) | Architecture Patterns (Pattern 4) | P/E direction always returns "N/A"; silent data loss |
| A3 | `quarterly_income_stmt` column type is `pd.Timestamp` with `.quarter` and `.year` attributes | Architecture Patterns (Pattern 3) | Quarter label formatting fails with AttributeError |

**Note on A1:** The CONTEXT.md canonical refs and STATE.md both reference "Diluted EPS" as the correct row name. The surrounding `try/except` + `if "Diluted EPS" not in stmt.index: return None` guard ensures graceful fallback regardless.

**Note on A2:** `forwardPE` is widely used across the existing codebase for info dict access; `trailingPE` is already used in `build_prompt` and `passes_fundamental_filter`. [VERIFIED: read from codebase]

---

## Open Questions

1. **Cache invalidation for fundamental_trend**
   - What we know: The analyst cache key is a SHA-256 of sorted headlines. A cache hit short-circuits `analyze_ticker`, so the prompt is not rebuilt with `fundamental_trend`.
   - What's unclear: Should the fundamental_trend content be folded into the cache key so that EPS changes (same headlines, different EPS quarter) produce a fresh call?
   - Recommendation: Defer — the existing behavior (cache bypass via headlines change) is adequate for now. Document in code comment. Cache key expansion would be a separate decision with quota implications.

2. **`valid[-4:]` slice when fewer than 4 quarters available**
   - What we know: D-05 says omit the line when fewer than 1 valid quarter. The implementation returns `None` only for the empty case.
   - What's unclear: If only 2 valid quarters exist, should the line show 2 quarters or be omitted?
   - Recommendation: Show however many valid quarters exist (1–4). The D-05 omission rule applies only to the 0-quarter case. This is consistent with the spec's "1–4 entries" note in D-06.

---

## Environment Availability

Step 2.6: SKIPPED — this phase has no external dependencies beyond the project's existing stack. yfinance 1.2.0 is already installed and confirmed. No new tools, services, or CLIs required.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (version from requirements.txt) |
| Config file | none (pytest auto-discovery) |
| Quick run command | `pytest tests/test_screener_fundamentals.py tests/test_analyst_claude.py -x -q` |
| Full suite command | `pytest -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SIG-07 | P/E direction label in BUY prompt (expanding/contracting/stable/N/A) | unit | `pytest tests/test_analyst_claude.py -k "fundamental_trend" -x` | Wave 0 (new tests) |
| SIG-07 | forwardPE absent → "N/A" (no crash) | unit | `pytest tests/test_analyst_claude.py -k "pe_direction_na" -x` | Wave 0 |
| SIG-07 | trailingPE=0 → "N/A" (ZeroDivisionError guard) | unit | `pytest tests/test_analyst_claude.py -k "pe_direction_zero" -x` | Wave 0 |
| SIG-08 | fetch_eps_data returns 4 quarters chronological | unit | `pytest tests/test_screener_fundamentals.py -k "fetch_eps" -x` | Wave 0 |
| SIG-08 | fetch_eps_data returns None when stmt is None | unit | `pytest tests/test_screener_fundamentals.py -k "fetch_eps_none" -x` | Wave 0 |
| SIG-08 | fetch_eps_data returns None when "Diluted EPS" row absent | unit | `pytest tests/test_screener_fundamentals.py -k "fetch_eps_missing_row" -x` | Wave 0 |
| SIG-08 | fetch_eps_data filters NaN quarters | unit | `pytest tests/test_screener_fundamentals.py -k "fetch_eps_nan" -x` | Wave 0 |
| SIG-08 | EPS trend line omitted when eps_trend is None | unit | `pytest tests/test_analyst_claude.py -k "eps_omitted" -x` | Wave 0 |
| SIG-08 | EPS trend values in chronological order in prompt | unit | `pytest tests/test_analyst_claude.py -k "eps_chronological" -x` | Wave 0 |
| Both | build_prompt backward compat — no fundamental_trend arg | unit | `pytest tests/test_analyst_claude.py -k "backward_compat" -x` | ✅ (existing pattern to extend) |

### Sampling Rate

- **Per task commit:** `pytest tests/test_screener_fundamentals.py tests/test_analyst_claude.py -x -q`
- **Per wave merge:** `pytest -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/test_screener_fundamentals.py` — extend with `fetch_eps_data` tests (None return, missing row, NaN filter, valid 4Q, chronological order)
- [ ] `tests/test_analyst_claude.py` — extend with `fundamental_trend` fixture variants (present/absent/None eps_trend/pe_direction N/A)

*(Existing test infrastructure is already in place; only new test functions needed, no new files or framework setup)*

---

## Security Domain

This phase makes no changes involving authentication, user input, session management, or cryptography. The new `fetch_eps_data` function reads from yfinance (same trust level as existing `fetch_fundamental_info`). The `fundamental_trend` dict is constructed from `float` and `str` values derived from yfinance data — no user-controlled input reaches prompt construction.

ASVS V5 (Input Validation): The `pe_direction` string is one of four enum values (`"expanding"`, `"contracting"`, `"stable"`, `"N/A"`), constructed by code, not user input. EPS values are cast to `float()` which will raise on non-numeric data, caught by the `try/except` in `fetch_eps_data`. No injection surface.

---

## Sources

### Primary (HIGH confidence)
- Codebase: `analyst/claude_analyst.py` lines 92–136 — `build_prompt` with `macro_context` pattern (read directly)
- Codebase: `analyst/claude_analyst.py` lines 259–300 — `analyze_ticker` signature pattern (read directly)
- Codebase: `screener/fundamentals.py` — `fetch_fundamental_info` pattern (read directly)
- Codebase: `main.py` lines 80–197 — `asyncio.to_thread` patterns and call site (read directly)
- Codebase: `tests/test_analyst_claude.py` — existing test patterns for `build_prompt` (read directly)
- Codebase: `tests/test_screener_fundamentals.py` — existing test patterns for fundamentals (read directly)
- `.planning/phases/15-fundamental-trend-enrichment/15-CONTEXT.md` — locked decisions D-01 through D-08 (read directly)
- `.planning/STATE.md` — "Research Decisions (v1.3 — pre-phase)" section confirming `quarterly_income_stmt` behavior (read directly)

### Secondary (MEDIUM confidence)
- `.planning/REQUIREMENTS.md` — SIG-07, SIG-08 requirement text (read directly)

### Tertiary (LOW confidence — training data, flagged as ASSUMED)
- A1: "Diluted EPS" exact row name in `quarterly_income_stmt` — consistent with yfinance 1.x convention but not live-verified this session
- A2/A3: `pd.Timestamp` column type with `.quarter`/`.year` — consistent with pandas documentation

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — yfinance 1.2.0 confirmed installed; no new deps needed
- Architecture: HIGH — exact implementation patterns read directly from codebase; macro_context precedent is a 1:1 template
- Pitfalls: HIGH — column ordering, NaN handling, division-by-zero guard all derived from codebase reading + locked STATE.md research
- Test strategy: HIGH — existing test file patterns verified; Wave 0 gaps are additive only

**Research date:** 2026-04-21
**Valid until:** 2026-05-21 (stable — yfinance 1.2.0 locked; pandas API stable)
