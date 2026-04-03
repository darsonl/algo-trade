---
phase: 05-position-monitoring
plan: 02
subsystem: approve-handler, run-scan
tags: [position-tracking, exposure-guard, skip-guard, discord-bot, tdd]
dependency_graph:
  requires: [05-01]
  provides: [05-03]
  affects: [discord_bot/bot.py, main.py]
tech_stack:
  added: []
  patterns: [exposure-guard, upsert-on-approve, skip-open-positions]
key_files:
  created: []
  modified:
    - discord_bot/bot.py
    - main.py
    - tests/test_discord_buttons.py
    - tests/test_run_scan.py
    - tests/test_main.py
decisions:
  - "Exposure guard uses last_price when available, falls back to avg_cost_usd for tickers without recent price update"
  - "has_open_position guard placed after ticker_recommended_today, before fetch_fundamental_info to short-circuit cheaply"
  - "upsert_position called after create_trade and before update_recommendation_status to ensure position record exists before status transitions"
metrics:
  duration_minutes: 25
  tasks_completed: 2
  files_modified: 5
  completed_date: "2026-04-03T08:19:31Z"
---

# Phase 5 Plan 02: Approve Handler Integration and Open-Position Skip Guard Summary

**One-liner:** Exposure guard blocks over-limit buys (ephemeral warning) and upsert_position wires position tracking into the approve flow; run_scan skips tickers with open positions via has_open_position guard.

## What Was Built

### Task 1 — Expose guard and position upsert in approve handler

`discord_bot/bot.py` (`ApproveRejectView.approve`):

- **Exposure guard** inserted after the `shares == 0` check, before `order_id = None`. Calls `queries.get_open_positions` to sum existing exposure (using `last_price` when non-None, falling back to `avg_cost_usd`). If `existing_total + new_exposure > max_position_size_usd`, sends an ephemeral Discord message containing "exceed" and returns early — no trade is placed.
- **Position upsert** inserted after `queries.create_trade(...)` and before `queries.update_recommendation_status(...)`. Calls `queries.upsert_position(db_path, ticker, shares, price)` to create or update the position record.

4 new tests added to `tests/test_discord_buttons.py`:
- `test_approve_exposure_guard_blocks`
- `test_approve_exposure_guard_allows`
- `test_approve_upserts_position`
- `test_approve_exposure_uses_last_price_fallback`

### Task 2 — Open-position skip guard in run_scan

`main.py` (`run_scan`):

- `queries.has_open_position(config.db_path, ticker)` guard added immediately after `ticker_recommended_today` check. If True, logs debug message and continues to next ticker — `fetch_fundamental_info` is never called.

2 new tests added to `tests/test_run_scan.py`:
- `test_run_scan_skips_open_position`
- `test_run_scan_allows_ticker_without_position`

`_full_patch` context manager updated to mock `queries.has_open_position` (default `False`) so all existing tests continue to work.

## Commits

| Hash    | Message |
|---------|---------|
| 80f05da | feat(05-02): add exposure guard and position upsert to approve handler |
| 8891995 | feat(05-02): add open-position skip guard to run_scan |
| 3ab0275 | fix(05-02): patch has_open_position in test_main cache integration tests |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed OperationalError in existing test_main.py cache integration tests**

- **Found during:** Full suite regression run after Task 2
- **Issue:** Two existing tests in `tests/test_main.py` called `run_scan` without mocking `queries.has_open_position`. After the new guard was added, these tests hit the real function against a `:memory:` DB with no `positions` table, causing `sqlite3.OperationalError: no such table: positions`.
- **Fix:** Added `patch("main.queries.has_open_position", return_value=False)` to both `test_run_scan_cache_hit_skips_analyze_ticker` and `test_run_scan_cache_miss_calls_analyze_ticker_and_caches`.
- **Files modified:** `tests/test_main.py`
- **Commit:** 3ab0275

## Acceptance Criteria Verification

- [x] `discord_bot/bot.py` contains `queries.get_open_positions(self.config.db_path)`
- [x] `discord_bot/bot.py` contains `queries.upsert_position(self.config.db_path, self.ticker, shares, self.price)`
- [x] `discord_bot/bot.py` contains `existing_total + new_exposure > self.config.max_position_size_usd`
- [x] `discord_bot/bot.py` contains `ephemeral=True`
- [x] `upsert_position` call appears AFTER `create_trade` and BEFORE `update_recommendation_status`
- [x] `tests/test_discord_buttons.py` contains all 4 new test functions
- [x] `main.py` contains `queries.has_open_position(config.db_path, ticker)`
- [x] `main.py` contains `'Skipping %s: open position exists'`
- [x] `has_open_position` guard appears AFTER `ticker_recommended_today` check and BEFORE `fetch_fundamental_info`
- [x] `tests/test_run_scan.py` contains `def test_run_scan_skips_open_position`
- [x] Full pytest suite: 164 passed, 0 failed

## Known Stubs

None.

## Self-Check: PASSED

- discord_bot/bot.py exists and contains `upsert_position`: FOUND
- main.py exists and contains `has_open_position`: FOUND
- tests/test_discord_buttons.py contains new test functions: FOUND
- tests/test_run_scan.py contains new test functions: FOUND
- Commits 80f05da, 8891995, 3ab0275: FOUND
- Full suite: 164 passed
