---
phase: 14-trade-history-command
plan: "01"
subsystem: discord-commands
tags: [slash-command, trade-history, sqlite, embed, tdd]
dependency_graph:
  requires: [database/queries.py, discord_bot/embeds.py, discord_bot/bot.py]
  provides: [get_closed_trades, build_history_embed, /history command]
  affects: [discord_bot/bot.py setup_hook, tests/test_database.py, tests/test_embeds.py, tests/test_discord_bot.py]
tech_stack:
  added: []
  patterns: [asyncio.to_thread DB call, code-block embed description, TDD red-green]
key_files:
  created: []
  modified:
    - database/queries.py
    - discord_bot/embeds.py
    - discord_bot/bot.py
    - tests/test_database.py
    - tests/test_embeds.py
    - tests/test_discord_bot.py
decisions:
  - "get_closed_trades uses ? parameterised LIMIT (not f-string) per T-14-02 mitigration"
  - "build_history_embed header: 'Ticker  Entry     Exit      P&L%     Date' per D-02"
  - "Empty-state exact string: 'No closed trades yet.' (no embed, no ephemeral) per D-04/SC-3"
  - "asyncio.to_thread wraps DB call in _history_command per CLAUDE.md gateway-safety invariant"
metrics:
  duration: ~20 minutes
  completed: "2026-04-19"
  tasks_completed: 3
  files_modified: 6
---

# Phase 14 Plan 01: Trade History Command Summary

**One-liner:** `/history` slash command with `get_closed_trades` DB query + `build_history_embed` code-block table — last 20 closed sell trades, newest-first, with entry/exit/P&L%/date.

## What Was Built

### Task 1 — `get_closed_trades` (database/queries.py)

New function added immediately after `get_trade_stats`:

```python
def get_closed_trades(db_path: str, limit: int = 20) -> list[dict]
```

- Filter: `WHERE side = 'sell' AND cost_basis IS NOT NULL ORDER BY executed_at DESC LIMIT ?`
- No JOIN — sell-side-only SELECT (SC-4, D-05, D-06)
- Returns `list[dict]` with keys: `ticker` (str), `price` (float), `cost_basis` (float), `executed_at` (str)
- `limit` passed as `?` parameter binding — never f-string interpolated (T-14-02)

### Task 2 — `build_history_embed` (discord_bot/embeds.py)

New embed builder added after `build_stats_embed`:

```python
def build_history_embed(trades: list[dict]) -> discord.Embed
```

- Title: `"Trade History"`, color: `discord.Color.blurple()`
- Description: triple-backtick code-block table
- Header row: `Ticker  Entry     Exit      P&L%     Date`
- Example data row: `AAPL     100.00    110.00    +10.0%  2026-04-01`
- P&L% signed: `+` for gains (pnl >= 0), `-` for losses; one decimal
- Date: ISO `YYYY-MM-DD` stripped from `executed_at` (first 10 chars)

### Task 3 — `/history` command (discord_bot/bot.py)

- `build_history_embed` added to top-level import on line 12
- `history` command registered in `setup_hook` (after `stats`, before `await self.tree.sync()`)
- `_history_command` handler:
  - `asyncio.to_thread(get_closed_trades, self.config.db_path)` — gateway-safe
  - Empty state: `interaction.response.send_message("No closed trades yet.")` — plain string, no embed
  - Non-empty state: `interaction.response.send_message(embed=build_history_embed(trades))`

## Test Counts Added

| File | Tests Added | Behaviors Covered |
|------|-------------|-------------------|
| tests/test_database.py | 7 | empty, buy-only, null exclusion, ordering, limit=20, default limit, shape |
| tests/test_embeds.py | 9 | title, color, code-block fence, header, row count, gain sign, loss sign, date format, ticker+prices |
| tests/test_discord_bot.py | 3 | empty-state plain string, embed path, to_thread usage |
| **Total** | **19** | |

Full suite: **393 tests green** (was 372 before this phase).

## Success Criteria Verification

- [x] SC-1: `get_closed_trades(db_path, limit=20)` returns up to 20 rows newest-first
- [x] SC-2: each rendered row contains ticker, entry (cost_basis), exit (price), P&L% with sign, ISO date
- [x] SC-3: empty-state sends exact plain string `"No closed trades yet."` (no embed, no ephemeral)
- [x] SC-4: SQL contains `WHERE side = 'sell' AND cost_basis IS NOT NULL` with no JOIN keyword
- [x] OPS-02: `/history` registered in `setup_hook`, synced via `await self.tree.sync()`
- [x] All new tests pass; full suite green

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all data paths wired end-to-end through DB query to embed render.

## Threat Flags

No new threat surface introduced. All T-14-xx threats addressed:
- T-14-02 (Tampering): `?` parameterised LIMIT binding confirmed in `database/queries.py`
- T-14-05 (DoS): `LIMIT 20` + `asyncio.to_thread` confirmed in `discord_bot/bot.py`

## Self-Check: PASSED

Files exist:
- database/queries.py — `get_closed_trades` present
- discord_bot/embeds.py — `build_history_embed` present
- discord_bot/bot.py — `_history_command` + `name="history"` present

Commits exist:
- a3fd178 — feat(14-01): add get_closed_trades query function
- fa25ec4 — feat(14-01): add build_history_embed builder
- 35f703f — feat(14-01): register /history slash command and handler
