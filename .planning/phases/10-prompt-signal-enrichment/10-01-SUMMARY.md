---
phase: 10-prompt-signal-enrichment
plan: "01"
subsystem: screener + analyst
tags: [macro-context, prompt-enrichment, yfinance, tdd]
dependency_graph:
  requires: []
  provides: [screener/macro.py, macro_context prompt parameter]
  affects: [analyst/claude_analyst.py, tests/test_macro.py, tests/test_analyst_claude.py, tests/test_sell_prompt.py]
tech_stack:
  added: [screener/macro.py]
  patterns: [pure-function prompt builders, yfinance fetch with fallback, TDD red-green]
key_files:
  created:
    - screener/macro.py
    - tests/test_macro.py
  modified:
    - analyst/claude_analyst.py
    - tests/test_analyst_claude.py
    - tests/test_sell_prompt.py
decisions:
  - "fetch_macro_context returns None dict on any exception — scan continues without enrichment (D-06)"
  - "Sector and 52-week range always in stock prompts (N/A fallback); SPY/VIX only when macro available (D-11)"
  - "ETF prompt gets SPY trend + VIX only, no Sector or 52-week range (D-10)"
  - "compute_52w_position exported from screener.macro and imported by claude_analyst"
metrics:
  duration: "~12 minutes"
  completed: "2026-04-12"
  tasks_completed: 2
  files_changed: 5
  tests_added: 35
  tests_total: 298
---

# Phase 10 Plan 01: Prompt Signal Enrichment — Macro Context Summary

**One-liner:** SPY trend and VIX level injected into BUY/SELL/ETF prompts via new screener/macro.py module with full N/A fallback when data is unavailable.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create screener/macro.py with fetch_macro_context() | 167b544 | screener/macro.py, tests/test_macro.py |
| 2 | Add macro_context parameter to all three prompt builders | d8c0753 | analyst/claude_analyst.py, tests/test_analyst_claude.py, tests/test_sell_prompt.py |

## What Was Built

### screener/macro.py

New module with four exported functions:

- `format_spy_trend(one_month_return)` — formats SPY 1-month return as "Bullish (+3.2%)" or "Bearish (-1.4%)"
- `format_vix_level(vix_close)` — formats VIX close as "18.4 (Low volatility)" with thresholds at 20 and 30
- `compute_52w_position(current_price, low_52w, high_52w)` — returns "Near high (87%)", "Near low (12%)", "Mid-range (50%)", or "N/A" for missing/equal values
- `fetch_macro_context()` — fetches SPY 1-month return and VIX last close from yfinance; returns `{"spy_trend": None, "vix_level": None}` on any exception

### analyst/claude_analyst.py

All three prompt builders enriched with `macro_context: dict | None = None` parameter:

- **build_prompt**: Market Context block inserted between Fundamentals and Recent news headlines. Contains Sector (always), SPY trend + VIX (when macro available), 52-week range (always). N/A fallback for missing sector or 52w data.
- **build_sell_prompt**: Same Market Context block. Added `info: dict | None = None` parameter to supply sector and 52w data. When both info and macro_context are None, block is omitted entirely.
- **build_etf_prompt**: Market Context block with SPY trend + VIX only (no Sector, no 52w per D-10). Omitted entirely when macro_context is None.

All callers pass no macro_context (default None), so all existing tests remain green.

## Prompt Shape (BUY example with macro)

```
Market Context:
- Sector: Technology
- SPY trend: Bullish (+3.2%)
- VIX: 18.4 (Low volatility)
- 52-week range: Near high (80%)
```

## Test Results

- **tests/test_macro.py**: 24 tests — pure function coverage + mocked fetch
- **tests/test_analyst_claude.py**: +11 new tests (macro enrichment for build_prompt + build_etf_prompt)
- **tests/test_sell_prompt.py**: +4 new tests (macro enrichment for build_sell_prompt)
- **Total suite**: 298 tests passing, 0 failures (was 252 at Phase 8, 298 now)

## Deviations from Plan

None — plan executed exactly as written.

## Threat Flags

No new security surface introduced. SPY/VIX are public market data formatted as floats with fixed precision; no injection vector exists. fetch_macro_context catches all exceptions per T-10-02 mitigation.

## Self-Check: PASSED

- `screener/macro.py` exists: FOUND
- `tests/test_macro.py` exists: FOUND
- Commit 167b544 exists: FOUND
- Commit d8c0753 exists: FOUND
- 298 tests passing: CONFIRMED
