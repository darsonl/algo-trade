---
phase: 18-test-coverage-gaps
plan: "03"
subsystem: testing
tags: [pytest, unittest.mock, quota-guard, analyst, run_scan]

requires:
  - phase: 18-test-coverage-gaps
    provides: "Phase context and quota guard code at main.py:180-207 (buy) and main.py:325-352 (sell)"

provides:
  - "Buy-path quota exhaustion test: all three providers exhausted -> analyze_ticker skipped"
  - "Sell-path quota exhaustion test: all three providers exhausted -> analyze_sell_ticker skipped"
  - "TEST-11 (D-03) fully covered: both scan paths, three-way AND guard, no false greens"

affects: [future-test-phases, analyst-quota-guard]

tech-stack:
  added: []
  patterns:
    - "Quota exhaustion testing: patch get_analyst_call_count_today return_value=limit, all three non-empty provider slots, cache miss forced"
    - "Non-vacuous assert_not_called: sibling positive test proves path IS reached when guard not active"

key-files:
  created: []
  modified:
    - tests/test_main.py
    - tests/test_sell_scan.py

key-decisions:
  - "All three provider slots (gemini/deepseek/openai) set non-empty — unset fallback2 would short-circuit to analyst_daily_limit and trip the guard for free (false green)"
  - "get_cached_analysis=None forced on buy test — cache hit bypasses guard entirely, making assert_not_called vacuous"
  - "TECH_DATA_OVERBOUGHT used on sell test — TECH_DATA_NORMAL skips the position before the guard is evaluated (different false green)"
  - "Discrimination checks run and documented: sibling positive tests prove assert_not_called is non-vacuous"

patterns-established:
  - "Quota exhaustion test pattern: three real provider names + mock returning limit + cache miss = genuine guard exercise"

requirements-completed: [TEST-11]

duration: 15min
completed: 2026-06-06
---

# Phase 18 Plan 03: Test Coverage Gaps — Quota Exhaustion Summary

**Quota-exhaustion guard tests for both run_scan paths: buy skips analyze_ticker and sell skips analyze_sell_ticker when all three providers (gemini/deepseek/openai) hit the daily limit**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-06-06T00:00:00Z
- **Completed:** 2026-06-06
- **Tasks:** 2 of 2
- **Files modified:** 2

## Accomplishments

- Added `test_run_scan_skips_analyze_ticker_when_all_providers_exhausted` to `tests/test_main.py`: buy-path quota guard (main.py:198-207) verified to skip analyze_ticker when all three providers are at the daily limit
- Added `test_sell_pass_skips_analyze_sell_ticker_when_all_providers_exhausted` to `tests/test_sell_scan.py`: sell-path quota guard (main.py:343-352) verified to skip analyze_sell_ticker with TECH_DATA_OVERBOUGHT ensuring check_exit_signals fires first
- Discrimination checks confirm assert_not_called() is non-vacuous in both cases: sibling positive tests (cache_miss and posts_sell_recommendation) reach the analyze calls when guard is inactive

## Task Commits

1. **Task 1: Buy-path quota exhaustion test** - `558ce6f` (test)
2. **Task 2: Sell-path quota exhaustion test** - `da09b34` (test)

## Files Created/Modified

- `tests/test_main.py` - Added `test_run_scan_skips_analyze_ticker_when_all_providers_exhausted`
- `tests/test_sell_scan.py` - Added `test_sell_pass_skips_analyze_sell_ticker_when_all_providers_exhausted`

## Decisions Made

- Three real provider names configured (gemini/deepseek/openai) — unset fallback2 would auto-trip the guard via the empty-name shortcut (main.py:196), making the test pass without testing the third slot
- Cache miss forced in buy test (get_cached_analysis=None) — a cache hit returns before the guard is evaluated, creating a vacuous assert_not_called
- TECH_DATA_OVERBOUGHT (RSI=75, MACD bearish) used in sell test — TECH_DATA_NORMAL routes past check_exit_signals before reaching the guard (different vacuous pass)
- Decorator placed as topmost @patch in sell test (bottom-up injection: mock_count is last mock arg before config) following the established pattern in test_sell_scan.py

## Discrimination Checks (documented per acceptance criteria)

Both discrimination checks pass, proving assert_not_called() is non-vacuous:

- `pytest tests/test_main.py -q -k "cache_miss or exhausted"` — 2 passed (sibling positive test reaches analyze_ticker)
- `pytest tests/test_sell_scan.py -q -k "posts_sell_recommendation or exhausted"` — 2 passed (sibling positive test reaches analyze_sell_ticker)

Full success-criterion run: `pytest tests/test_main.py tests/test_sell_scan.py -q` — 31 passed

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- TEST-11 (D-03) fully covered: quota exhaustion guard on both scan paths verified
- Phase 18 all three plans complete (18-01, 18-02, 18-03)
- Ready for milestone wrap-up

---
*Phase: 18-test-coverage-gaps*
*Completed: 2026-06-06*
