---
phase: 05-position-monitoring
plan: 01
subsystem: database
tags: [sqlite, positions, crud, schema, tdd]
dependency_graph:
  requires: []
  provides: [positions-table, position-crud-functions]
  affects: [05-02, 05-03]
tech_stack:
  added: []
  patterns: [open-commit-close, ON CONFLICT upsert, weighted-average-cost]
key_files:
  created:
    - tests/test_positions.py
  modified:
    - database/models.py
    - database/queries.py
decisions:
  - "Used ON CONFLICT(ticker) DO UPDATE SET for re-entry support instead of DELETE+INSERT — preserves row id and simplifies re-open logic"
  - "update_position computes weighted avg in a single SQL expression to avoid a round-trip fetch"
metrics:
  duration: ~10 minutes
  completed: 2026-04-03
  tasks_completed: 1
  files_modified: 3
---

# Phase 05 Plan 01: Positions Table and CRUD Summary

Positions SQLite table with six CRUD functions (create, update, get_open, has_open, close, upsert) using ON CONFLICT upsert for re-entry and an in-SQL weighted average cost formula.

## What Was Implemented

### database/models.py

Added `positions` DDL to the `initialize_db` executescript block, after the `analyst_cache` table:

```sql
CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL UNIQUE,
    shares       REAL NOT NULL,
    avg_cost_usd REAL NOT NULL,
    entry_date   TEXT NOT NULL DEFAULT (date('now')),
    status       TEXT NOT NULL DEFAULT 'open',
    last_price   REAL,
    last_updated TEXT
);
```

### database/queries.py

Six new functions appended, all following the open/commit/close pattern:

| Function | Behavior |
|---|---|
| `create_position` | INSERT with ON CONFLICT upsert to re-open closed positions |
| `update_position` | Weighted avg cost in a single SQL UPDATE expression |
| `get_open_positions` | SELECT WHERE status='open' ORDER BY entry_date ASC |
| `has_open_position` | SELECT id → bool |
| `close_position` | UPDATE status='closed' WHERE status='open' |
| `upsert_position` | Dispatches to create or update based on has_open_position |

### tests/test_positions.py

11 test functions covering all 6 query functions:

- `test_initialize_db_creates_positions_table` — schema creation
- `test_create_position_inserts_row_and_returns_id` — insert + field validation
- `test_create_position_reentry_after_close` — ON CONFLICT re-open path
- `test_update_position_weighted_avg_cost` — numeric assertion: 5@$100 + 5@$120 = $110.00
- `test_get_open_positions_returns_only_open` — filter correctness (2 open, 1 closed → returns 2)
- `test_has_open_position_returns_true_for_open` — open ticker
- `test_has_open_position_returns_false_for_closed` — closed ticker
- `test_has_open_position_returns_false_for_nonexistent` — no row at all
- `test_close_position_sets_status_closed` — status update
- `test_upsert_position_creates_when_no_open_position` — dispatch to create
- `test_upsert_position_updates_when_open_position_exists` — dispatch to update with weighted avg

## Test Results

```
tests/test_positions.py  11 passed
tests/test_database.py   19 passed (1 pre-existing flaky timing test)
```

The single failure (`test_expire_stale_does_not_expire_at_exact_now`) is a pre-existing timing-sensitive test that compares two consecutive `datetime('now')` SQLite calls and occasionally sees them in the same second. Confirmed pre-existing by running the test in isolation on the unmodified codebase — it passed. No changes introduced regressions.

## Deviations from Plan

None — plan executed exactly as written. All SQL, parameter ordering, and function signatures match the plan specification verbatim.

## Decisions Made

1. **ON CONFLICT upsert over DELETE+INSERT**: The plan specified `ON CONFLICT(ticker) DO UPDATE SET`. Kept as specified — avoids losing the row id and keeps re-entry logic entirely in SQL with no extra round-trip.
2. **Weighted avg in single SQL expression**: `(shares * avg_cost_usd + ? * ?) / (shares + ?)` computed in the UPDATE avoids fetching current values into Python. Exactly as plan specified.

## Known Stubs

None. All 6 functions are fully wired with real SQL. No placeholder data or TODO markers.

## Commits

- `24faef6` — feat(05-01): add positions table DDL and 6 position CRUD query functions

## Self-Check: PASSED

- `database/models.py` contains "CREATE TABLE IF NOT EXISTS positions" — FOUND
- `database/models.py` contains "ticker       TEXT NOT NULL UNIQUE" — FOUND
- `database/models.py` contains "status       TEXT NOT NULL DEFAULT 'open'" — FOUND
- `database/queries.py` exports all 6 functions — FOUND
- `database/queries.py` contains "ON CONFLICT(ticker) DO UPDATE SET" — FOUND
- `database/queries.py` contains "(shares * avg_cost_usd + ? * ?) / (shares + ?)" — FOUND
- `tests/test_positions.py` has 11 test functions (>= 8 required) — FOUND
- Commit 24faef6 exists — FOUND
