---
phase: 06-sell-signals-sell-orders
plan: "05"
subsystem: analyst-quota
tags: [quota, rate-limiting, config, database, queries, analyst]
dependency_graph:
  requires: [06-01, 06-02, 06-03]
  provides: [D-11-quota-tracking]
  affects: [main.py, analyst/claude_analyst.py, database/queries.py, database/models.py, config.py]
tech_stack:
  added: []
  patterns: [sqlite-upsert-on-conflict, per-provider-quota-counter]
key_files:
  created: []
  modified:
    - config.py
    - database/models.py
    - database/queries.py
    - analyst/claude_analyst.py
    - main.py
    - tests/test_sell_scan.py
    - tests/test_run_scan.py
    - tests/test_main.py
decisions:
  - "Default analyst_daily_limit=18 (below Gemini 20 RPD free tier, gives 2-call buffer)"
  - "ON CONFLICT DO UPDATE for atomic increment avoids race conditions in future multi-thread use"
  - "Cache hits bypass quota guard — no API call made means no quota consumed"
  - "provider_used attached to result dict (not passed separately) for minimal API surface change"
metrics:
  duration_minutes: 20
  completed_date: "2026-04-05"
  tasks_completed: 2
  files_modified: 8
requirements: [SELL-01, SELL-02, SELL-03]
---

# Phase 6 Plan 05: Analyst Quota Infrastructure Summary

Per-provider daily quota tracking system (D-11) — Config field, DB table, query helpers, provider_used in analyze functions, quota guards in main.py buy and sell passes.

## What Was Built

**Config** (`config.py`): Added `analyst_daily_limit: int = int(os.getenv("ANALYST_DAILY_LIMIT", "18"))` after `sell_rsi_threshold`. Default 18 leaves 2-call buffer below Gemini's 20 RPD free tier.

**Database** (`database/models.py`): Added `analyst_calls` table to the `initialize_db` executescript block. Schema: `date TEXT`, `provider TEXT`, `count INTEGER DEFAULT 0`, `PRIMARY KEY (date, provider)`. Composite primary key enables independent per-provider tracking.

**Query helpers** (`database/queries.py`): Two new functions under `# --- Analyst quota tracking (D-11) ---`:
- `get_analyst_call_count_today(db_path, provider)` — returns today's call count for provider, 0 if no row exists
- `increment_analyst_call_count(db_path, provider)` — INSERT ... ON CONFLICT DO UPDATE SET count = count + 1 for atomic upsert

**Analyze functions** (`analyst/claude_analyst.py`): Both `analyze_ticker` and `analyze_sell_ticker` now return `{"signal", "reasoning", "provider_used"}`. `provider_used` is set to `config.analyst_provider` before the try block, overridden to `config.analyst_fallback_provider` in the except branch when fallback is used.

**Quota guards** (`main.py`):
- Buy pass: quota guard inserted in the `else:` branch (cache miss path) before `analyze_ticker`. Checks primary + fallback counts against `config.analyst_daily_limit`. If both exhausted: logs warning, `continue`. After successful call: `increment_analyst_call_count(config.db_path, analysis["provider_used"])`.
- Sell pass: same guard before `analyze_sell_ticker`. After successful call: same increment.
- Cache hits in buy pass skip both the guard and the increment (no API call = no quota consumed).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test mock fixtures missing provider_used key**

- **Found during:** Task 2 verification (pytest run)
- **Issue:** `analyze_ticker` and `analyze_sell_ticker` mocks in test_run_scan.py, test_main.py, and test_sell_scan.py returned dicts without `provider_used`. The new `increment_analyst_call_count(config.db_path, analysis["provider_used"])` call crashed with `KeyError: 'provider_used'`.
- **Fix:** Updated all mock return values to include `"provider_used": "gemini"`. Added `queries.get_analyst_call_count_today` and `queries.increment_analyst_call_count` patches to `_full_patch()` in test_run_scan.py (and inline patches in test_main.py) to prevent "no such table: analyst_calls" errors on `:memory:` DBs.
- **Files modified:** `tests/test_sell_scan.py`, `tests/test_run_scan.py`, `tests/test_main.py`
- **Commit:** 5868923

The plan anticipated this fix and documented it under the verification section: "update the mock fixtures in test_sell_scan.py to return provider_used".

**2. [Rule 1 - Bug] Parallel 06-04 execution modified analyst/claude_analyst.py and main.py concurrently**

- **Found during:** Task 2 — tool notification revealed linter changes to both files
- **Issue:** Plan 06-04 added MACD parameters to `build_sell_prompt` and `analyze_sell_ticker` while this plan was running. The `analyze_sell_ticker` signature gained `macd_line` and `signal_line` kwargs. The `analyze_sell_ticker` call in `main.py` gained `macd_line=tech_data.get("macd_line")` and `signal_line=tech_data.get("signal_line")` kwargs.
- **Fix:** No fix needed — 06-04's changes were compatible with this plan's changes. The `provider_used` tracking was already in place in the modified function. The `increment_analyst_call_count` call was positioned after the full `analyze_sell_ticker` call (which already included the MACD kwargs). Result: clean integration of both plans.
- **Files modified:** None additional

## Commits

| Hash | Type | Description |
|------|------|-------------|
| a100170 | feat | analyst quota infrastructure — Config field, DB table, query helpers |
| 5868923 | feat | provider_used in analyze functions and quota guard in main.py |

## Verification Results

- `Config().analyst_daily_limit` == 18 (default)
- `analyst_calls` table created by `initialize_db`
- `get_analyst_call_count_today` returns 0 for new provider, correct count after increments
- `increment_analyst_call_count` creates row on first call, increments atomically on subsequent
- `analyze_ticker["provider_used"]` == `config.analyst_provider` on primary success
- `analyze_sell_ticker["provider_used"]` == same behavior
- `main.py` buy and sell passes both have quota guard + increment
- 222 tests green (was 216 before this plan)

## Known Stubs

None. All quota tracking is wired end-to-end: Config -> DB table -> query helpers -> analyze functions -> main.py guards.
