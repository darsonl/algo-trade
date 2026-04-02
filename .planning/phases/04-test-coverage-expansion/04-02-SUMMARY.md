---
phase: "04"
plan: "02"
subsystem: tests
tags: [discord, database, async, mocking]
dependency_graph:
  requires: [discord_bot.bot.ApproveRejectView, database.queries.ticker_recommended_today, database.queries.expire_stale_recommendations]
  provides: [TEST-02, TEST-03, TEST-07, TEST-08]
  affects: []
tech_stack:
  added: [pytest-asyncio]
  patterns: [discord.ui.Button.callback.callback invocation pattern for bypassing Button wrapper]
key_files:
  created:
    - tests/test_discord_buttons.py
  modified:
    - tests/test_database.py
decisions:
  - discord.py wraps @discord.ui.button methods into a Button object — call via view.approve.callback.callback(view, interaction, button) to invoke the underlying async function in tests
metrics:
  duration: "~8 minutes"
  completed: "2026-04-02T21:03:26Z"
  tasks_completed: 2
  files_changed: 2
---

# Phase 4 Plan 02: Discord Button Handlers and DB Edge Cases Summary

Async tests for Discord Approve/Reject button handlers and extended DB edge case coverage for ticker_recommended_today and expire_stale boundary semantics.

## Tasks Completed

### Task 1: Create tests/test_discord_buttons.py

10 async tests covering `ApproveRejectView`:

- Approve dry-run: does not call `place_order`, creates trade with `order_id=None`
- Approve live: calls `place_order` with correct args, stores returned order_id in trade record
- Approve status: calls `update_recommendation_status` with "approved", sends confirmation message
- Approve zero-shares guard: price > max_position_size_usd yields 0 shares, sends ephemeral message, no trade or order created
- Reject: sets status to "rejected", sends confirmation, does not call `place_order`

### Task 2: Extend tests/test_database.py

**Change 2a:** Added `ticker_recommended_today` to the import block.

**Change 2b:** Appended 13 new tests:

- `ticker_recommended_today` (8 tests, TEST-07): covers fresh pending, different ticker, empty DB, expired status, rejected status, approved status, UTC yesterday boundary, UTC today boundary
- `expire_stale_recommendations` edge cases (5 tests, TEST-08): fresh rec stays pending, approved rec not overwritten, rejected rec not overwritten, 1-second-past boundary expires, exact-now boundary stays pending (strict `<` semantics)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] discord.py Button wrapper incompatibility**
- **Found during:** Task 1 — all 10 Discord tests failed with `TypeError: 'Button' object is not callable`
- **Issue:** The plan specified calling `await view.approve(interaction, MagicMock())` directly, but this version of discord.py wraps `@discord.ui.button`-decorated methods into a `discord.ui.button.Button` object. The Button is not directly callable.
- **Fix:** Discovered the underlying async function is accessible at `view.approve.callback.callback`. Added `_call_approve` and `_call_reject` helper functions that invoke `view.approve.callback.callback(view, interaction, button)` and `view.reject.callback.callback(view, interaction, button)` respectively. No source files were modified.
- **Files modified:** `tests/test_discord_buttons.py` only
- **Commit:** ccfbaab

## Results

| File | Tests | Status |
|---|---|---|
| tests/test_discord_buttons.py | 10 | 10 passed |
| tests/test_database.py | 19 (6 pre-existing + 13 new) | 19 passed |
| Full suite | 147 | 147 passed |

## Self-Check: PASSED

- tests/test_discord_buttons.py: FOUND
- tests/test_database.py: FOUND
- commit ccfbaab: FOUND
