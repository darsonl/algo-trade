---
phase: 12-etf-polish
plan: "01"
subsystem: scheduler
tags: [apscheduler, config, etf, cron, scheduler]

# Dependency graph
requires:
  - phase: 09-ops-hardening
    provides: configure_scheduler function and single stock scan job pattern
  - phase: 07-etf-support
    provides: run_scan_etf pipeline and partition_watchlist ETF separation
provides:
  - _parse_etf_scan_times() helper in config.py
  - Config.etf_scan_hour / etf_scan_minute / etf_scan_times fields (default 9/30/["09:30"])
  - configure_scheduler times + job_id_prefix kwargs for multi-job scheduling
  - Second configure_scheduler call in on_ready wiring daily ETF job (etf_scan_0)
  - Startup log "ETF scheduler started — daily ETF scan at HH:MM"
  - 12 unit tests in tests/test_etf_scheduler.py
affects: [12-etf-polish, 13-any-future-scheduler-work]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "configure_scheduler accepts times + job_id_prefix kwargs; defaults preserve backward compat for stock scan"
    - "_parse_etf_scan_times() mirrors _parse_scan_times() — single-time list from two env vars"
    - "ETF job IDs prefixed etf_scan_ to avoid collision with stock scan_ IDs"

key-files:
  created:
    - tests/test_etf_scheduler.py
  modified:
    - config.py
    - main.py
    - .env.example

key-decisions:
  - "ETF_SCAN_HOUR=9 / ETF_SCAN_MINUTE=30 defaults give 30-minute offset from stock scan at 09:00 to avoid yfinance rate-limit contention (D-01)"
  - "configure_scheduler reused for ETF job via times+job_id_prefix kwargs rather than a new function (D-03)"
  - "ETF_SCAN_TIMES multi-time support explicitly deferred (D-02) — single-time only in this plan"

patterns-established:
  - "Multi-job scheduler pattern: call configure_scheduler twice in on_ready with distinct prefixes"
  - "Config ETF fields mirror stock scan fields: etf_scan_hour/etf_scan_minute/etf_scan_times"

requirements-completed:
  - ETF-07

# Metrics
duration: 18min
completed: 2026-04-13
---

# Phase 12 Plan 01: ETF Scheduled Scan Summary

**APScheduler ETF job registered at bot startup via etf_scan_ prefixed IDs, configurable via ETF_SCAN_HOUR/ETF_SCAN_MINUTE (default 09:30), fully independent from the 09:00 stock scan job**

## Performance

- **Duration:** 18 min
- **Started:** 2026-04-13T10:16:00Z
- **Completed:** 2026-04-13T10:34:36Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments
- Added `_parse_etf_scan_times()` helper and three `etf_scan_*` fields to `Config` dataclass — mirrors existing `_parse_scan_times` / `scan_*` pattern exactly
- Generalized `configure_scheduler` with `times` and `job_id_prefix` kwargs; backward-compatible defaults mean existing stock scan call site is unchanged
- Wired second `configure_scheduler` call in `on_ready` for daily ETF job with `etf_scan_` prefix and startup log line matching D-04 spec
- Wrote 12 unit tests (TDD RED then GREEN) covering helper defaults, zero-padding, env overrides, job ID non-collision, trigger time independence; 343 total tests pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Add ETF scheduler config fields and helper** - `1251f36` (test — TDD RED + GREEN config)
2. **Task 2: Generalize configure_scheduler and wire ETF scheduler** - `3d87a7d` (feat)
3. **Task 3: Document ETF_SCAN_HOUR/ETF_SCAN_MINUTE in .env.example** - `e445504` (docs)

_Note: Task 1 commit includes both the failing tests (RED) and the config.py implementation (GREEN) — staged together after both phases confirmed._

## Files Created/Modified
- `tests/test_etf_scheduler.py` — 12 unit tests for _parse_etf_scan_times, Config ETF fields, and configure_scheduler ETF job registration
- `config.py` — _parse_etf_scan_times() helper + etf_scan_hour/etf_scan_minute/etf_scan_times fields
- `main.py` — configure_scheduler generalized with times+job_id_prefix; on_ready wires ETF job; ETF startup log added
- `.env.example` — ETF_SCAN_HOUR=9 and ETF_SCAN_MINUTE=30 documented in Scheduler section

## Decisions Made
- Reused `configure_scheduler` for ETF job (D-03) instead of a new function — keeps scheduling logic in one place, tested once
- Default ETF time 09:30 (30-minute offset from stock scan 09:00) to avoid yfinance rate-limit contention when both scans run on the same machine
- No `ETF_SCAN_TIMES` multi-time env var added — explicitly deferred per REQUIREMENTS.md D-02

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- ETF-07 complete; ETF scan now runs unattended daily at 09:30 by default
- Ready for Phase 12 Plan 02 (ETF-09: expense ratio flagging in Discord embeds)
- No blockers

---
*Phase: 12-etf-polish*
*Completed: 2026-04-13*
