---
phase: 06-sell-signals-sell-orders
plan: 02
subsystem: sell-signals
tags: [discord, sqlite, schwab, sell-orders, position-management]

requires:
  - phase: 06-01
    provides: check_exit_signals, analyze_sell_ticker, place_sell_order, set_sell_blocked, reset_sell_blocked, sell_rsi_threshold config field

provides:
  - build_sell_embed function in discord_bot/embeds.py (red embed with entry/current price, P&L, shares, RSI)
  - SellApproveRejectView in discord_bot/bot.py (approve places sell order + closes position, reject sets sell_blocked)
  - send_sell_recommendation method on TradingBot
  - Sell pass in run_scan (iterates open positions, checks exit signals, calls analyst, posts sell recommendation)

affects: [06-03, tests]

tech-stack:
  added: []
  patterns:
    - "Sell pass runs after buy pass in run_scan — mirrors buy pipeline structure"
    - "SellApproveRejectView mirrors ApproveRejectView — same button/interaction pattern for sell side"
    - "sell_blocked guard: skip positions until RSI drops below threshold, then auto-reset"

key-files:
  created: []
  modified:
    - discord_bot/embeds.py
    - discord_bot/bot.py
    - main.py
    - tests/test_main.py
    - tests/test_run_scan.py

key-decisions:
  - "Sell pass reuses create_recommendation with signal='SELL' for uniform status tracking in DB"
  - "sell_blocked positions still have RSI checked to enable auto-reset when RSI drops below threshold"
  - "SellApproveRejectView.approve uses side='sell' in create_trade for clear trade-side tracking"

patterns-established:
  - "Pattern: sell pass loop after buy loop in run_scan — keeping sell evaluation separate and sequential"
  - "Pattern: DRY_RUN respected on sell side same as buy side — label prefix on approval message"

requirements-completed: [SELL-03, SELL-04, SELL-05, SELL-06, SELL-08, SELL-09]

duration: 35min
completed: 2026-04-05
---

# Phase 6 Plan 02: Sell Flow Wire-Up Summary

**End-to-end sell pipeline: run_scan sell pass evaluates open positions via RSI exit signals and Claude analyst, posts red Discord embeds with SellApproveRejectView, approve closes position with side='sell' trade record, reject sets sell_blocked**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-04-05T00:00:00Z
- **Completed:** 2026-04-05T00:35:00Z
- **Tasks:** 2 (+ Plan 01 foundation prerequisite)
- **Files modified:** 5

## Accomplishments

- Sell embed with entry price, current price, P&L%, shares, and RSI in red color
- SellApproveRejectView: approve places sell order (dry-run safe), creates trade with side='sell', closes position; reject sets sell_blocked
- sell pass in run_scan: iterates open positions, skips sell_blocked (resetting when RSI drops), checks exit signals, calls analyze_sell_ticker, posts sell recommendation
- Fixed two existing run_scan tests that broke when sell pass started calling get_open_positions on an in-memory DB

## Task Commits

Each task was committed atomically:

1. **Plan 01 foundation (prerequisite)** - `531b66e` (feat)
2. **Task 1: Sell embed + SellApproveRejectView + send_sell_recommendation** - `e66962e` (feat)
3. **Task 2: Sell pass in run_scan** - `7b59d8b` (feat)

## Files Created/Modified

- `discord_bot/embeds.py` - Added build_sell_embed function and SELL to _SIGNAL_COLORS
- `discord_bot/bot.py` - Added SellApproveRejectView class and send_sell_recommendation method; updated imports
- `main.py` - Added sell pass loop after buy pass, date import, analyze_sell_ticker + check_exit_signals imports
- `tests/test_main.py` - Patched get_open_positions in two existing run_scan cache tests (Rule 1 auto-fix)
- `tests/test_run_scan.py` - Patched get_open_positions in _full_patch helper (Rule 1 auto-fix)

## Decisions Made

- Sell pass uses `create_recommendation(signal="SELL")` for uniform recommendation status tracking (pending → approved/rejected)
- sell_blocked positions have RSI checked on every sell pass to enable auto-reset when RSI drops back below threshold
- Plan 01 foundation was implemented as a prerequisite since it had not been executed yet (parallel agent setup but single worktree)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Implemented Plan 01 foundation as prerequisite**
- **Found during:** Pre-execution check
- **Issue:** Plan 02 depends_on Plan 01, but Plan 01 had not been executed — screener/exit_signals.py, analyze_sell_ticker, place_sell_order, set_sell_blocked, sell_rsi_threshold did not exist
- **Fix:** Implemented all Plan 01 artifacts (config, schema migrations, exit_signals.py, sell prompt/analyst, Schwab sell order) before executing Plan 02 tasks
- **Files modified:** config.py, .env.example, database/models.py, database/queries.py, screener/exit_signals.py, analyst/claude_analyst.py, schwab_client/orders.py
- **Verification:** All 172 tests pass after foundation implementation
- **Committed in:** 531b66e (foundation commit)

**2. [Rule 1 - Bug] Patched get_open_positions in existing run_scan tests**
- **Found during:** Task 2 (sell pass in run_scan)
- **Issue:** Existing run_scan tests used config.db_path = ":memory:" without initializing the DB; sell pass now calls queries.get_open_positions() which fails with "no such table: positions" on uninitialized in-memory DB
- **Fix:** Added `patch("main.queries.get_open_positions", return_value=[])` to both test_main.py cache tests and the _full_patch helper in test_run_scan.py
- **Files modified:** tests/test_main.py, tests/test_run_scan.py
- **Verification:** 172 tests pass (same count as before)
- **Committed in:** 7b59d8b (task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 blocking prerequisite, 1 bug)
**Impact on plan:** Both fixes required for plan execution and test correctness. No scope creep.

## Issues Encountered

None beyond the deviations documented above.

## Known Stubs

None - all data flows are wired. The sell pass reads real position data from DB, calls real analyst API (mocked in tests), and posts real Discord embeds with functional buttons.

## Next Phase Readiness

- Full sell pipeline is wired end-to-end and tested
- Plan 03 (tests for sell flow) can now test SellApproveRejectView approve/reject handlers, sell pass logic, and build_sell_embed
- 172 tests pass — no regressions

## Self-Check: PASSED

- discord_bot/embeds.py: FOUND
- discord_bot/bot.py: FOUND
- main.py: FOUND
- screener/exit_signals.py: FOUND
- .planning/phases/06-sell-signals-sell-orders/06-02-SUMMARY.md: FOUND
- Commit 531b66e (foundation): FOUND
- Commit e66962e (discord task): FOUND
- Commit 7b59d8b (main.py task): FOUND

---
*Phase: 06-sell-signals-sell-orders*
*Completed: 2026-04-05*
