---
phase: 13-portfolio-analytics
plan: 01
subsystem: database
tags: [sqlite, discord, positions, pnl, stats, trading]

# Dependency graph
requires:
  - phase: 12-etf-polish
    provides: ETF scan separation, positions table with avg_cost_usd
  - phase: 6-sell-signals
    provides: SellApproveRejectView, create_trade, trades table
provides:
  - pnl_usd key in get_position_summary() per position
  - /positions embed footer with total unrealized P&L (full/partial/omitted)
  - /stats slash command with win rate, avg gain, avg loss on closed trades
  - cost_basis column in trades table (additive migration)
  - get_trade_stats() DB-only aggregation returning None or stats dict
  - create_trade() extended with backward-compatible cost_basis kwarg
  - SellApproveRejectView.approve populates cost_basis from open position avg_cost_usd
affects:
  - future sell flow phases
  - any phase reading trades table

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "DB-only aggregation: get_trade_stats uses no external API, pure SQL from trades table"
    - "Additive migration: ALTER TABLE ADD COLUMN inside try/except for idempotency"
    - "cost_basis fetched from DB (avg_cost_usd), never from Discord interaction payload (T-13-02)"
    - "asyncio.to_thread for get_trade_stats call in _stats_command"

key-files:
  created:
    - .planning/phases/13-portfolio-analytics/13-01-SUMMARY.md
  modified:
    - screener/positions.py
    - discord_bot/embeds.py
    - discord_bot/bot.py
    - database/models.py
    - database/queries.py
    - tests/test_positions.py
    - tests/test_embeds.py
    - tests/test_sell_buttons.py
    - tests/test_database.py

key-decisions:
  - "cost_basis stored as avg_cost_usd from open position at sell-approval time, never from Discord payload (security: T-13-02)"
  - "Footer omitted entirely when all pnl_usd are None; appends (partial) when some are None"
  - "get_trade_stats returns None (not empty dict) when no qualifying sell trades exist — callers send plain text"
  - "Break-even (sell_price == cost_basis) counts as win per plan spec"
  - "build_stats_embed is a pure function accepting the stats dict; no DB access in embed layer"

patterns-established:
  - "pnl_usd computed at get_position_summary layer, not at embed layer — separation of concerns"
  - "Footer aggregation: valid = [s for s in summaries if s.get('pnl_usd') is not None]"

requirements-completed: [PORT-01, PORT-02]

# Metrics
duration: 5min
completed: 2026-04-14
---

# Phase 13 Plan 01: Portfolio Analytics Summary

**Unrealized P&L footer in /positions + /stats command with win rate and avg gain/loss backed by a cost_basis column migration in trades**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-04-14T00:07:46Z
- **Completed:** 2026-04-14T00:12:13Z
- **Tasks:** 3
- **Files modified:** 9

## Accomplishments

- PORT-01: `get_position_summary()` now returns `pnl_usd` per position; `build_positions_embed()` computes a footer aggregating total unrealized P&L in dollars and percentage with graceful partial/none handling
- PORT-02: `trades` table gains a `cost_basis REAL` column via additive migration; `get_trade_stats()` aggregates closed sell trades; `SellApproveRejectView.approve` populates `cost_basis` from the open position's `avg_cost_usd`
- New `/stats` slash command registered in `setup_hook()`, calls `get_trade_stats` via `asyncio.to_thread`, sends stats embed or plain-text fallback; 372 tests green (120 new)

## Task Commits

Each task was committed atomically:

1. **Task 1: pnl_usd data layer** - `36dc8ab` (feat)
2. **Task 2: positions footer + /stats command** - `d1d5a94` (feat)
3. **Task 3: DB migration + get_trade_stats + cost_basis population** - `fd36c77` (feat)

_All tasks used TDD: failing tests written first, then implementation._

## Files Created/Modified

- `screener/positions.py` — Added `pnl_usd` key computed as `(current_price - avg_cost_usd) * shares`
- `discord_bot/embeds.py` — Extended `build_positions_embed` with footer logic; added `build_stats_embed()`
- `discord_bot/bot.py` — Imported `build_stats_embed`; registered `/stats` command; added `_stats_command` handler; `SellApproveRejectView.approve` now fetches and passes `cost_basis`
- `database/models.py` — Added `ALTER TABLE trades ADD COLUMN cost_basis REAL` migration block
- `database/queries.py` — Extended `create_trade()` with `cost_basis` kwarg; added `get_trade_stats()`
- `tests/test_positions.py` — 7 new tests: 3 for pnl_usd data layer, 4 for footer behavior
- `tests/test_embeds.py` — 4 new tests for `build_stats_embed`
- `tests/test_sell_buttons.py` — 1 new test verifying cost_basis populated from open position
- `tests/test_database.py` — 9 new tests for migration, create_trade cost_basis, get_trade_stats aggregates

## Decisions Made

- `cost_basis` fetched from DB (`get_open_positions → avg_cost_usd`), never from Discord interaction payload — mitigates T-13-02 tampering threat
- Footer omitted entirely when all positions lack price data; `(partial)` suffix when some are missing — communicates data quality to operator
- `get_trade_stats` returns `None` (not `{}`) for no-data case — callers send plain text, not empty embed
- Break-even treated as win (sell_price >= cost_basis) per plan spec

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required. The `cost_basis` column migration is additive and runs automatically on next `initialize_db()` call.

## Next Phase Readiness

- PORT-01 and PORT-02 complete; `/positions` footer and `/stats` command ready for operator use
- No blockers for subsequent phases

---
*Phase: 13-portfolio-analytics*
*Completed: 2026-04-14*
