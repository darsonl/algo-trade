---
phase: 09-ops-hardening
plan: 01
status: complete
completed_at: "2026-04-12"
tests_added: 8
tests_total: 260
---

# Plan 09-01 Summary — Ops Hardening

## What Was Done

### Task 1: Error posting in `run_scan` and `run_scan_etf` (`main.py`)

Added `error_count` / `errors_posted` counters to both scan functions:

- **Per-ticker errors** post `[ERROR] {ticker}: {ExceptionType}` to Discord via `send_ops_alert`, capped at 3 per run.
- **Overflow** message `[N more errors not shown — check logs]` posted after the loop when `error_count > 3`.
- **ETF zero-rec alert** now prefixed with `[ETF]`: `"[ETF] ETF scan complete: 0 recommendations posted."`
- **Stock zero-rec alert** unchanged (no prefix).
- Existing `logger.error(...)` lines preserved.
- `sqlite3.OperationalError` handler in `run_scan_etf` and the sell pass left untouched.

### Task 2: Unit tests (`tests/test_ops_hardening.py`)

8 new tests:

| # | Test | Coverage |
|---|------|----------|
| 1 | `test_stock_1_error_posts_1_alert` | Single error, exact format `[ERROR] FAIL1: ValueError` |
| 2 | `test_stock_3_errors_posts_3_alerts_no_overflow` | Cap boundary — no overflow at exactly 3 |
| 3 | `test_stock_5_errors_posts_3_alerts_and_overflow` | 5 errors → 3 posted + overflow `[2 more errors not shown` |
| 4 | `test_stock_0_errors_posts_no_error_alerts` | Empty universe — no error alerts |
| 5 | `test_etf_5_errors_posts_3_alerts_and_overflow` | ETF scan follows same cap + overflow behavior |
| 6 | `test_etf_zero_rec_alert_has_etf_prefix` | ETF zero-rec alert starts with `[ETF]` |
| 7 | `test_stock_zero_rec_alert_has_no_etf_prefix` | Stock zero-rec alert has no `[ETF]` prefix |
| 8 | `test_error_alert_contains_type_not_message_detail` | `ConnectionError("some detail")` → alert contains `ConnectionError`, not `some detail` |

### Task 3: Full test suite

260 tests pass (252 existing + 8 new). Zero regressions.

## Verification

```
grep -c "send_ops_alert.*ERROR" main.py     → 2
grep -c "more errors not shown" main.py     → 2
grep "[ETF] ETF scan complete" main.py      → found
grep "Scan complete: 0" main.py             → found (no [ETF] prefix)
pytest -q                                   → 260 passed
```

## Requirements Satisfied

- **OPS-01**: Per-ticker exceptions post `[ERROR] {ticker}: {ExceptionType}`, capped at 3 with overflow summary, in both `run_scan` and `run_scan_etf`.
- **ETF-08**: ETF zero-rec alert reads `[ETF] ETF scan complete: 0 recommendations posted.`; stock alert unchanged.
