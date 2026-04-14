---
phase: 10-prompt-signal-enrichment
plan: "02"
subsystem: main + analyst
tags: [macro-context, pipeline-wiring, asyncio, integration-tests]
dependency_graph:
  requires: [10-01]
  provides: [macro_context pipeline wiring, analyze_* macro params]
  affects: [main.py, analyst/claude_analyst.py, tests/test_run_scan.py, tests/test_sell_scan.py]
tech_stack:
  added: []
  patterns: [asyncio.to_thread for macro fetch, belt-and-suspenders try/except, ExitStack for test patches]
key_files:
  created: []
  modified:
    - main.py
    - analyst/claude_analyst.py
    - tests/test_run_scan.py
    - tests/test_sell_scan.py
decisions:
  - "macro_context fetched once per scan via asyncio.to_thread before ticker loop in both run_scan and run_scan_etf (D-02)"
  - "Outer try/except in main.py wraps asyncio.to_thread(fetch_macro_context) — belt-and-suspenders since fetch_macro_context already has internal guard (T-10-04)"
  - "sell_info fetched with asyncio.to_thread(fetch_fundamental_info) in sell pass for sector/52w enrichment (D-14/D-16)"
  - "_full_patch refactored to ExitStack to avoid Python 3.14 nested-block limit (Rule 1 auto-fix)"
metrics:
  duration: "~10 minutes"
  completed: "2026-04-12"
  tasks_completed: 1
  files_changed: 4
  tests_added: 4
  tests_total: 302
---

# Phase 10 Plan 02: Macro Context Pipeline Wiring Summary

**One-liner:** Macro context fetched once per scan via asyncio.to_thread and threaded through analyze_ticker, analyze_sell_ticker, and analyze_etf_ticker to the prompt builders.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add macro_context to analyze_* functions and wire in main.py | f6036e6 | analyst/claude_analyst.py, main.py, tests/test_run_scan.py, tests/test_sell_scan.py |

## What Was Built

### analyst/claude_analyst.py

All three analyze functions gained `macro_context: dict | None = None` parameter and forward it to their prompt builders:

- `analyze_ticker(..., macro_context: dict | None = None)` — passes `macro_context=macro_context` to `build_prompt`
- `analyze_etf_ticker(..., macro_context: dict | None = None)` — passes `macro_context=macro_context` to `build_etf_prompt`
- `analyze_sell_ticker(..., macro_context: dict | None = None, info: dict | None = None)` — passes both to `build_sell_prompt`

All callers that don't pass these params continue to work via default `None`.

### main.py

- Added `from screener.macro import fetch_macro_context` import
- In `run_scan()`: macro fetched once before ticker loop with `await asyncio.to_thread(fetch_macro_context)` wrapped in try/except; fallback to `{"spy_trend": None, "vix_level": None}` on any exception
- In `run_scan()` buy pass: `analyze_ticker` call updated to include `macro_context=macro_context`
- In `run_scan()` sell pass: added `sell_info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)` fetch, then `analyze_sell_ticker` call updated with `macro_context=macro_context, info=sell_info`
- In `run_scan_etf()`: same macro fetch pattern before ETF ticker loop; `analyze_etf_ticker` call updated with `macro_context=macro_context`

### tests/test_run_scan.py

- Refactored `_full_patch` context manager from chained `with` to `ExitStack` (Python 3.14 nested-block limit hit with 21 patches)
- Added `fetch_macro_context` mock to `_full_patch` (returns valid macro dict by default)
- Added `fetch_macro_context` mock to `test_run_scan_excludes_etfs_from_stock_universe` patches list (index shift adjusted)
- New tests:
  - `test_run_scan_macro_fetch_failure_scan_continues` — macro exception does not abort scan (D-06)
  - `test_run_scan_passes_macro_to_analyze_ticker` — verify `macro_context` kwarg forwarded correctly
  - `test_run_scan_passes_none_macro_to_analyze_ticker_on_fetch_failure` — verify None dict passed on failure

### tests/test_sell_scan.py

- Added `@patch("main.fetch_macro_context", ...)` decorator to all 8 existing sell scan tests with correct argument order in function signatures
- New test:
  - `test_sell_pass_passes_macro_context_to_analyze_sell_ticker` — verify `macro_context` and `info` kwargs forwarded to `analyze_sell_ticker`

## Test Results

- **tests/test_run_scan.py**: 13 tests (was 10, +3 new)
- **tests/test_sell_scan.py**: 10 tests (was 9, +1 new)
- **Total suite**: 302 tests passing, 0 failures (was 298 after Plan 01)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Python 3.14 nested `with` block limit in _full_patch**
- **Found during:** Task 1 — first test run
- **Issue:** Adding `fetch_macro_context` mock as 21st nested `with` clause triggered `SyntaxError: too many statically nested blocks` in Python 3.14
- **Fix:** Refactored `_full_patch` context manager from chained `with` statement to `ExitStack` pattern (same approach already used in `test_run_scan_excludes_etfs_from_stock_universe`)
- **Files modified:** tests/test_run_scan.py
- **Commit:** f6036e6 (same task commit)

## Threat Flags

No new security surface introduced. Macro context dict flows read-only through the pipeline as intended (T-10-05 accepted).

## Self-Check: PASSED

- `main.py` imports `fetch_macro_context`: FOUND
- `grep -c "asyncio.to_thread(fetch_macro_context)" main.py` = 2: CONFIRMED
- `grep -c "macro_context=macro_context" main.py` = 3: CONFIRMED
- `macro_context` in `analyze_ticker`, `analyze_sell_ticker`, `analyze_etf_ticker`: CONFIRMED
- Commit f6036e6 exists: CONFIRMED
- 302 tests passing: CONFIRMED
