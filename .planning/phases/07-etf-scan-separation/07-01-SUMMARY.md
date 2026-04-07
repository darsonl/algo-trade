---
phase: 07-etf-scan-separation
plan: 01
subsystem: screener/universe, database
tags: [etf, partition, universe, database, migration, tdd]
dependency_graph:
  requires: []
  provides: [partition_watchlist, etf_watchlist.txt, asset_type-column]
  affects: [screener/universe.py, database/models.py, database/queries.py]
tech_stack:
  added: []
  patterns: [allowlist-fallback, additive-migration, tdd-red-green]
key_files:
  created:
    - etf_watchlist.txt
  modified:
    - screener/universe.py
    - database/models.py
    - database/queries.py
    - watchlist.txt
    - tests/test_screener_universe.py
    - tests/test_database.py
decisions:
  - "No @_retry on partition_watchlist — per-ticker failures handled by allowlist fallback; entire function wrapped in asyncio.to_thread at call site (Plan 03)"
  - "yf import moved to module top level in universe.py per CLAUDE.md top-level import convention"
  - "_ETF_ALLOWLIST duplicated from fundamentals.py into universe.py — avoids circular import; single source of truth can be consolidated later"
metrics:
  duration: "~20 minutes"
  completed: "2026-04-07T12:25:51Z"
  tasks_completed: 2
  files_modified: 6
---

# Phase 7 Plan 1: ETF Scan Foundation Summary

**One-liner:** `partition_watchlist()` with yfinance quoteType + allowlist fallback, `etf_watchlist.txt` migration, and additive `asset_type` DB column.

## What Was Built

### Task 1: partition_watchlist + ETF watchlist files

- Added `_ETF_ALLOWLIST` constant to `screener/universe.py` (mirrors `fundamentals.py`)
- Added `import yfinance as yf` at module top level
- Added `partition_watchlist(tickers: list[str]) -> tuple[list[str], list[str]]`:
  - Queries `yf.Ticker(t).info.get("quoteType", "")` per ticker
  - Routes `"ETF"` → etfs list, everything else → stocks list
  - On any exception: checks `_ETF_ALLOWLIST` for fallback classification
  - Logs DEBUG on fallback: `"yfinance quoteType lookup failed for %s, using allowlist fallback"`
  - No `@_retry` decorator — per-ticker exception handled by allowlist
- Created `etf_watchlist.txt` with 10 ETF tickers (SPY, QQQ, VTI, IVV, VOO, VEA, BND, GLD, XLK, SCHD)
- Updated `watchlist.txt` to stocks-only (all ETF tickers removed)
- Added 5 tests in `tests/test_screener_universe.py` (11 total, all pass)

### Task 2: asset_type column migration

- Added `asset_type TEXT NOT NULL DEFAULT 'stock'` to `recommendations` CREATE TABLE DDL
- Added `ALTER TABLE recommendations ADD COLUMN asset_type TEXT DEFAULT 'stock'` migration block in `initialize_db()` (after existing migrations, try/except for idempotency)
- Added `asset_type: str = "stock"` param to `create_recommendation()` in `queries.py`
- Updated INSERT statement to include `asset_type` column
- Added 2 tests in `tests/test_database.py` (21 total, all pass)

## Verification

```
pytest tests/test_screener_universe.py tests/test_database.py -v
# 32 passed
```

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | cdb3a1c | feat(07-01): add partition_watchlist, etf_watchlist.txt, stocks-only watchlist.txt |
| 2 | 920afe1 | feat(07-01): add asset_type column to recommendations table |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — `partition_watchlist` is fully wired with real yfinance calls and allowlist fallback. The `etf_watchlist.txt` is populated with all 10 ETFs. The `asset_type` column is stored and retrievable. No placeholder data.

## Threat Flags

None — no new network endpoints, auth paths, or trust boundaries beyond those already documented in the plan's threat model (T-07-01 through T-07-05).

## Self-Check: PASSED

- [x] `screener/universe.py` contains `def partition_watchlist`
- [x] `screener/universe.py` contains `_ETF_ALLOWLIST`
- [x] `etf_watchlist.txt` exists with 10 ETF tickers including SPY
- [x] `watchlist.txt` contains no ETF tickers
- [x] `database/models.py` contains `asset_type TEXT NOT NULL DEFAULT 'stock'`
- [x] `database/models.py` contains `ALTER TABLE recommendations ADD COLUMN asset_type`
- [x] `database/queries.py` `create_recommendation` includes `asset_type: str = "stock"`
- [x] `tests/test_screener_universe.py` has 5 new partition_watchlist tests (11 total pass)
- [x] `tests/test_database.py` has `test_create_recommendation_with_asset_type_etf` and `test_create_recommendation_default_asset_type` (21 total pass)
- [x] Commit cdb3a1c exists
- [x] Commit 920afe1 exists
