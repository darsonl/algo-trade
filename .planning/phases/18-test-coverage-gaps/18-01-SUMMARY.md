---
phase: 18-test-coverage-gaps
plan: 01
subsystem: testing
tags: [pytest, unittest.mock, analyst, fallback, parse-error]

# Dependency graph
requires:
  - phase: 17-limit-buy-orders
    provides: stable production codebase; analyst fallback chain already fully implemented

provides:
  - Full fallback matrix coverage across all three analyst functions (analyze_ticker, analyze_etf_ticker, analyze_sell_ticker)
  - 8 new test functions closing the analyze_sell_ticker blind spot and remaining matrix gaps

affects:
  - 18-02 (Config.validate tests)
  - 18-03 (quota exhaustion tests)

# Tech tracking
tech-stack:
  added: []
  patterns: [call_count dict + side_effect sequencing for multi-call fallback tests, keyword-arg calls to avoid positional-arg trap on analyze_sell_ticker]

key-files:
  created: []
  modified:
    - tests/test_analyst_claude.py

key-decisions:
  - "Parse errors and API errors converge on the same primary→fallback→fallback2 chain (D-01). No test asserts parse errors skip fallback."
  - "analyze_sell_ticker tests use keyword args exclusively to avoid the 13+ positional argument ordering trap."
  - "Template-echo string SIGNAL: <SELL|HOLD> proven to trigger parse error because parse_claude_response rejects angle-bracket tokens as invalid signals."

patterns-established:
  - "call_count dict pattern: call_count = {'n': 0} incremented inside side_effect for multi-call sequencing"
  - "Fallback test structure: patch _call_api → assert provider_used + call_count + signal"

requirements-completed: [TEST-09]

# Metrics
duration: 12min
completed: 2026-06-06
---

# Phase 18 Plan 01: Analyst Fallback Matrix Summary

**8 new tests closing the analyze_sell_ticker blind spot and completing the symmetric API-fail/parse-error fallback matrix across all three analyst functions**

## Performance

- **Duration:** 12 min
- **Started:** 2026-06-06T14:00:00Z
- **Completed:** 2026-06-06T14:12:00Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments

- Closed the analyze_sell_ticker coverage gap (was zero fallback tests; now has all four matrix cells plus propagate-when-no-fallback)
- Added the two missing analyze_ticker cells: API-fail→fallback and API-fail→fallback2 chain
- Added the missing analyze_etf_ticker cell: parse-error→fallback2 chain
- All 82 tests in test_analyst_claude.py + test_analyze_ticker.py pass with no regressions

## Task Commits

1. **Task 1: analyze_sell_ticker full fallback matrix** - `00bc995` (test)
2. **Task 2: Fill remaining analyze_ticker and analyze_etf_ticker gaps** - `f5d5add` (test)

## Files Created/Modified

- `tests/test_analyst_claude.py` - Added `analyze_sell_ticker` to imports; appended 8 new test functions in two sections

## Decisions Made

- Parse errors converge on the fallback chain identically to API errors (D-01 confirmed). The template-echo string `SIGNAL: <SELL|HOLD>` reliably triggers `ValueError` in `parse_claude_response` because the parser rejects angle-bracket tokens as invalid signals.
- `analyze_sell_ticker` tests use keyword arguments only — the function has 14 parameters and positional calls would silently misalign `fallback2_client`.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- TEST-09 complete. Fallback matrix is symmetric across all three analyst functions.
- Ready for 18-02 (Config.validate test suite) and 18-03 (quota exhaustion tests).

---
*Phase: 18-test-coverage-gaps*
*Completed: 2026-06-06*
