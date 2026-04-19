---
phase: 14-trade-history-command
reviewed: 2026-04-19T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - database/queries.py
  - discord_bot/bot.py
  - discord_bot/embeds.py
  - tests/test_database.py
  - tests/test_discord_bot.py
  - tests/test_embeds.py
findings:
  critical: 0
  warning: 1
  info: 2
  total: 3
status: issues_found
---

# Phase 14: Code Review Report

**Reviewed:** 2026-04-19T00:00:00Z
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Phase 14 adds the `/history` slash command: `get_closed_trades` DB query (`database/queries.py`), `build_history_embed` embed builder (`discord_bot/embeds.py`), the `_history_command` handler (`discord_bot/bot.py`), and 16 new tests split across `tests/test_database.py`, `tests/test_discord_bot.py`, and `tests/test_embeds.py`. All 79 tests pass.

The implementation is clean and follows established project patterns — `asyncio.to_thread`, empty-state handled at the command layer, and a plain-dict contract between query and embed. One warning-level crash path exists in the embed builder when `cost_basis` is zero; two info-level items relate to test quality.

## Warnings

### WR-01: ZeroDivisionError when `cost_basis` is zero in `build_history_embed`

**File:** `discord_bot/embeds.py:170`
**Issue:** The P&L percentage is computed as `(exit_price - entry) / entry * 100.0` with no guard against `entry == 0`. The DB query filters `cost_basis IS NOT NULL` but does not filter `cost_basis = 0`. While `avg_cost_usd` is set from a real buy-price and upstream guards (`compute_share_quantity`) prevent zero-price approvals, a direct DB insertion or data-migration artifact could produce a `cost_basis = 0.0` row. When `/history` renders such a row the command handler will raise an unhandled `ZeroDivisionError`, causing Discord to receive no reply and logging a traceback.

**Fix:**
```python
# discord_bot/embeds.py  line ~170
pnl_pct = (exit_price - entry) / entry * 100.0 if entry != 0 else 0.0
```
Alternatively, add `AND cost_basis > 0` to the `get_closed_trades` SQL filter so the embed never receives a zero-basis row.

## Info

### IN-01: Redundant `get_closed_trades` mock in `test_history_command_empty_state_plain_string`

**File:** `tests/test_discord_bot.py:172`
**Issue:** The test patches both `database.queries.get_closed_trades` (stored as `mock_fn`) and `asyncio.to_thread`. Because `asyncio.to_thread` is fully replaced first, the `get_closed_trades` patch is never invoked and `mock_fn` is never referenced in the test body. The redundant `with patch(...)` block adds noise and could mislead a future reader into thinking the function-level mock is what drives the empty-list return.

**Fix:** Remove the outer `patch("database.queries.get_closed_trades", ...)` context manager; keep only the `asyncio.to_thread` patch which is the actual control point.
```python
# Before (lines 172-174):
with patch("database.queries.get_closed_trades", return_value=[]) as mock_fn:
    with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=[]):
        await TradingBot._history_command(bot, interaction)

# After:
with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=[]):
    await TradingBot._history_command(bot, interaction)
```

### IN-02: Ordering test only asserts the first element

**File:** `tests/test_database.py:602`
**Issue:** `test_get_closed_trades_ordering_newest_first` inserts three sell trades with timestamps `2026-04-10`, `2026-04-12`, and `2026-04-15`, then asserts only that `result[0]["executed_at"] == "2026-04-15T10:00:00"`. The full descending order (`result[1]` should be `2026-04-12`, `result[2]` should be `2026-04-10`) is not verified, so a bug that returned only the most-recent row first but left the rest in insertion order would pass undetected.

**Fix:**
```python
assert result[0]["executed_at"] == "2026-04-15T10:00:00"
assert result[1]["executed_at"] == "2026-04-12T10:00:00"
assert result[2]["executed_at"] == "2026-04-10T10:00:00"
```

---

_Reviewed: 2026-04-19T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
