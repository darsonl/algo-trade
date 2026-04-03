---
phase: 05-position-monitoring
plan: 03
subsystem: discord-bot
tags: [positions, slash-command, pnl, yfinance, embed, tdd]
dependency_graph:
  requires: [05-01, 05-02]
  provides: [positions-slash-command, position-pnl-summary]
  affects: [discord_bot/bot.py, discord_bot/embeds.py]
tech_stack:
  added: []
  patterns: [asyncio.to_thread for non-blocking yfinance, local import for circular-import prevention, TDD red-green]
key_files:
  created:
    - screener/positions.py
  modified:
    - discord_bot/embeds.py
    - discord_bot/bot.py
    - tests/test_positions.py
decisions:
  - "Local import of get_position_summary inside _positions_command to prevent circular import at module load time"
  - "asyncio.to_thread wraps yfinance call to keep Discord event loop non-blocking"
  - "build_positions_embed truncates at 25 fields to respect Discord embed field limit"
  - "yfinance failure falls back to last_price from DB row; None last_price yields None pnl_pct"
metrics:
  duration_seconds: 143
  completed_date: "2026-04-03"
  tasks_completed: 2
  files_modified: 4
requirements: [POS-03, POS-04]
---

# Phase 05 Plan 03: /positions Slash Command Summary

One-liner: Discord `/positions` slash command displaying open holdings with live yfinance P&L%, backed by `screener/positions.py` helper and `build_positions_embed` formatter.

## Objective

Add the `/positions` slash command to give the operator real-time visibility into open holdings — ticker, shares, avg cost, current price, and P&L% — all from a single Discord slash command.

## Tasks Completed

### Task 1: screener/positions.py + build_positions_embed (TDD)

**Commit:** `3f479e2`

- Created `screener/positions.py` with `get_position_summary(db_path)` that:
  - Calls `get_open_positions` from `database.queries`
  - Fetches live price via `yf.Ticker(ticker).fast_info.last_price`
  - Falls back to DB `last_price` if yfinance raises any exception
  - Returns `None` for `pnl_pct` and `current_price` when no price is available
  - Computes P&L% as `(current - avg_cost) / avg_cost`

- Added `build_positions_embed(summaries)` to `discord_bot/embeds.py`:
  - Title: "Open Positions", color: blurple
  - One inline field per position with ticker, shares, avg cost, current price, P&L%
  - Truncates at 25 fields (Discord embed limit)
  - Sets `description = "No open positions."` for empty input

- Added 6 TDD tests to `tests/test_positions.py` (appended, not overwritten):
  - `test_get_position_summary_computes_pnl`
  - `test_get_position_summary_yfinance_fallback`
  - `test_get_position_summary_no_price_available`
  - `test_get_position_summary_empty`
  - `test_build_positions_embed_with_data`
  - `test_build_positions_embed_empty`

### Task 2: /positions slash command in bot.py

**Commit:** `63470dd`

- Updated `from discord_bot.embeds import build_recommendation_embed, build_positions_embed`
- Registered `/positions` command in `setup_hook` before `await self.tree.sync()`
- Added `_positions_command` method on `TradingBot`:
  - Uses `from screener.positions import get_position_summary` as local import (circular import prevention)
  - Calls `await asyncio.to_thread(get_position_summary, self.config.db_path)` (non-blocking)
  - Returns plain `"No open positions."` for empty state
  - Returns embed via `build_positions_embed(summaries)` otherwise
- Added 2 TDD tests:
  - `test_positions_command_sends_embed`
  - `test_positions_command_empty`

## Verification Results

```
pytest tests/test_positions.py -v   → 19 passed
pytest                              → 172 passed, 1 warning
```

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None. `get_position_summary` fetches live data from yfinance; fallback to DB `last_price` is intentional behavior, not a stub.

## Self-Check: PASSED

- `screener/positions.py` exists: FOUND
- `discord_bot/embeds.py` contains `build_positions_embed`: FOUND
- `discord_bot/bot.py` contains `_positions_command`: FOUND
- Commit `3f479e2` exists: FOUND
- Commit `63470dd` exists: FOUND
- Full test suite: 172 passed
