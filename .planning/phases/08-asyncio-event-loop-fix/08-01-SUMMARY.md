---
phase: 08-asyncio-event-loop-fix
plan: 01
subsystem: main.py / scan pipeline
tags: [asyncio, event-loop, yfinance, discord, reliability]
dependency_graph:
  requires: []
  provides: [async-safe-scan-pipeline]
  affects: [main.py, run_scan, run_scan_etf]
tech_stack:
  added: []
  patterns: [asyncio.to_thread for blocking I/O offload]
key_files:
  created: []
  modified:
    - main.py
decisions:
  - "Single commit for all 3 tasks since all changes target one file (main.py); logical separation by comment"
  - "fetch_news_headlines keyword arg preserved through asyncio.to_thread — positional+keyword pattern matches plan spec"
metrics:
  duration: "~10 minutes"
  completed: "2026-04-09"
  tasks_completed: 3
  files_modified: 1
requirements:
  - ASYNC-01
  - ASYNC-02
  - ASYNC-03
  - ASYNC-04
---

# Phase 08 Plan 01: Asyncio Event Loop Fix Summary

**One-liner:** Wrapped all 9 synchronous yfinance I/O calls in `asyncio.to_thread` across buy pass, sell pass, and ETF pass — eliminating Discord gateway heartbeat blocks during daily scans.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Wrap buy pass yfinance calls (ASYNC-01) | 0789349 | main.py |
| 2 | Wrap sell pass yfinance calls (ASYNC-02) | 0789349 | main.py |
| 3 | Wrap ETF pass calls + P8-audit comments (ASYNC-03, ASYNC-04) | 0789349 | main.py |

## What Was Done

**Task 1 — Buy pass (ASYNC-01):**
- `fetch_fundamental_info(yf_ticker)` wrapped at line 89
- `fetch_news_headlines(...)` wrapped at lines 93-95
- `fetch_technical_data(yf_ticker)` wrapped at line 136

**Task 2 — Sell pass (ASYNC-02):**
- `fetch_technical_data` in `sell_blocked` RSI-reset branch wrapped at line 187
- `fetch_technical_data` in main sell evaluation wrapped at line 197
- `fetch_news_headlines` in main sell evaluation wrapped at lines 215-217

**Task 3 — ETF pass + audit comments (ASYNC-03, ASYNC-04):**
- `fetch_technical_data` in ETF loop wrapped at line 311
- `fetch_fundamental_info` in ETF loop wrapped at line 314
- `fetch_news_headlines` in ETF loop wrapped at lines 321-323
- `# P8-audit: already wrapped (hotfix ae66e64)` added to `get_top_sp500_by_fundamentals` call (line 62)
- `# P8-audit: already wrapped (Phase 7)` added to both `partition_watchlist` call sites (lines 70, 293)

## Verification

```
grep -c "await asyncio.to_thread(fetch_technical_data" main.py  → 4
grep -c "await asyncio.to_thread(fetch_fundamental_info" main.py → 2
fetch_news_headlines wrapped sites                               → 3
grep -c "P8-audit" main.py                                       → 3
bare unwrapped calls                                             → 0
pytest -q                                                        → 252 passed
```

## Deviations from Plan

None — plan executed exactly as written. All 9 unwrapped calls wrapped, all 3 audit comments added, 252 tests green without any test file modifications.

## Known Stubs

None.

## Threat Flags

None — this phase modifies only the execution threading model of existing I/O calls. No new trust boundaries, data flows, or authentication changes were introduced.

## Self-Check: PASSED

- [x] main.py modified with all 9 wraps + 3 audit comments
- [x] Commit 0789349 exists
- [x] 252 tests pass
- [x] Zero bare synchronous yfinance I/O calls remain
