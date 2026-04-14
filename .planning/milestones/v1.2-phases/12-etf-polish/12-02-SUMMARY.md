---
phase: 12-etf-polish
plan: "02"
subsystem: discord
tags: [etf, embed, expense-ratio, config, display-flag]

# Dependency graph
requires:
  - phase: 12-etf-polish/12-01
    provides: ETF scan scheduler, etf_scan_hour/etf_scan_minute config fields

provides:
  - Config.etf_max_expense_ratio field (float, default 0.005, ETF_MAX_EXPENSE_RATIO env override)
  - build_etf_recommendation_embed accepts etf_max_expense_ratio param with flag logic
  - send_etf_recommendation threads etf_max_expense_ratio to embed builder
  - run_scan_etf passes config.etf_max_expense_ratio end-to-end

affects: [discord_bot, embeds, main-scan-etf, config-thresholds]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Strictly greater-than threshold comparison for display flags (expense_ratio > threshold, not >=)"
    - "Backward-compat None-default kwarg pattern for optional embed flags"
    - "Config float threshold pattern: float(os.getenv('KEY', 'default'))"

key-files:
  created:
    - tests/test_etf_expense_flag.py
  modified:
    - config.py
    - discord_bot/embeds.py
    - discord_bot/bot.py
    - main.py
    - .env.example
    - tests/test_discord_bot.py

key-decisions:
  - "Strict > comparison (not >=): expense_ratio equal to threshold is acceptable, no flag"
  - "etf_max_expense_ratio=None default preserves backward-compat for all existing call sites"
  - "Display-only flag per D-05: no filtering or trade gating on expense ratio"
  - "Warning format is hardcoded UTF-8 literal U+26A0 U+FE0F followed by (High) per D-06"

patterns-established:
  - "Embed flag pattern: None-safe guard + strict comparison + plain fallback"

requirements-completed:
  - ETF-09

# Metrics
duration: 15min
completed: 2026-04-13
---

# Phase 12 Plan 02: ETF Expense Ratio Flag Summary

**ETF embeds now visually flag high-cost funds via configurable threshold: expense_ratio > ETF_MAX_EXPENSE_RATIO renders "⚠️ 0.0075 (High)" in the Discord embed Expense Ratio field**

## Performance

- **Duration:** 15 min
- **Started:** 2026-04-13T10:25:00Z
- **Completed:** 2026-04-13T10:40:52Z
- **Tasks:** 3
- **Files modified:** 6 (+ 1 test file created)

## Accomplishments

- Added `etf_max_expense_ratio: float = float(os.getenv("ETF_MAX_EXPENSE_RATIO", "0.005"))` to Config dataclass
- Modified `build_etf_recommendation_embed` to accept `etf_max_expense_ratio: float | None = None` and render warning flag when `expense_ratio > threshold`
- Threaded `etf_max_expense_ratio` end-to-end: `run_scan_etf` -> `send_etf_recommendation` -> `build_etf_recommendation_embed`
- Created 8 tests covering all flag cases: above / equal / below / None-expense / None-threshold / default-no-param / config-default / config-env-override
- Documented `ETF_MAX_EXPENSE_RATIO=0.005` in `.env.example`

## Task Commits

Each task was committed atomically:

1. **Tasks 1+2: Config field + embed flag logic** - `83d2eb9` (feat)
2. **Task 3: Thread through bot.py, main.py, .env.example** - `35e8633` (feat)

**Plan metadata:** (docs commit — see below)

## Files Created/Modified

- `config.py` - Added `etf_max_expense_ratio` float field with 0.005 default
- `discord_bot/embeds.py` - Added `etf_max_expense_ratio` param and flag rendering logic to `build_etf_recommendation_embed`
- `discord_bot/bot.py` - Added `etf_max_expense_ratio` param to `send_etf_recommendation`, forwards to embed builder
- `main.py` - Passes `config.etf_max_expense_ratio` to `send_etf_recommendation` in `run_scan_etf`
- `.env.example` - Documented `ETF_MAX_EXPENSE_RATIO=0.005`
- `tests/test_etf_expense_flag.py` - Created with 8 tests (TDD RED then GREEN)
- `tests/test_discord_bot.py` - Updated mock assertion to include new `etf_max_expense_ratio=None` kwarg

## Decisions Made

- Strict `>` comparison (not `>=`): an ETF at exactly the threshold is acceptable cost, no flag per D-07
- `etf_max_expense_ratio=None` default on both `build_etf_recommendation_embed` and `send_etf_recommendation` preserves backward compatibility for all existing callers
- Flag is display-only (D-05): no scan filtering or trade gating tied to expense ratio value
- Warning emoji is hardcoded UTF-8 literal `⚠️` (U+26A0 U+FE0F) per D-06 — no user input reaches this code path (T-12-06 mitigated)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_discord_bot.py mock assertion to match new kwarg in embed call**
- **Found during:** Task 3 verification
- **Issue:** `test_send_etf_recommendation_posts_embed_and_returns_message_id` used old positional call format without `etf_max_expense_ratio=None`, causing assertion mismatch after bot.py was updated to call embed builder with explicit kwarg
- **Fix:** Updated `mock_build_embed.assert_called_once_with(...)` to include `etf_max_expense_ratio=None`
- **Files modified:** `tests/test_discord_bot.py`
- **Verification:** `pytest tests/test_discord_bot.py` passes; full suite 351 tests green
- **Committed in:** `35e8633` (Task 3 commit)

---

**Total deviations:** 1 auto-fixed (1 bug — test mock assertion out of sync with updated signature)
**Impact on plan:** Auto-fix required for test correctness. No scope creep.

## Issues Encountered

None — the mock assertion update was a straightforward consequence of the new kwarg being forwarded explicitly.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 12 ETF Polish is complete (both plans have SUMMARY files)
- ETF-09 requirement fulfilled: high-cost ETFs visibly flagged in Discord embeds
- Config default of 0.005 (0.5%) is operator-adjustable via `ETF_MAX_EXPENSE_RATIO` env var
- 351 tests passing, no regressions

## Self-Check: PASSED

- config.py: FOUND
- discord_bot/embeds.py: FOUND
- discord_bot/bot.py: FOUND
- main.py: FOUND
- tests/test_etf_expense_flag.py: FOUND
- 12-02-SUMMARY.md: FOUND
- Commit 83d2eb9: FOUND
- Commit 35e8633: FOUND
- 351 tests passing

---
*Phase: 12-etf-polish*
*Completed: 2026-04-13*
