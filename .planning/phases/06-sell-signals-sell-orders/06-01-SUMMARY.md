---
phase: 06-sell-signals-sell-orders
plan: 01
subsystem: sell-signals
tags: [sell, config, database, technicals, analyst, schwab, macd]
dependency_graph:
  requires: []
  provides: [sell-config-fields, schema-migrations, exit-signal-checker, sell-prompt, schwab-sell-order]
  affects: [config.py, database/models.py, database/queries.py, screener/technicals.py, screener/exit_signals.py, analyst/claude_analyst.py, schwab_client/orders.py]
tech_stack:
  added: []
  patterns: [two-gate-exit-signal, per-provider-quota-tracking, sell-prompt-with-macd-context]
key_files:
  created:
    - screener/exit_signals.py
  modified:
    - config.py
    - .env.example
    - database/models.py
    - database/queries.py
    - screener/technicals.py
    - analyst/claude_analyst.py
    - schwab_client/orders.py
    - tests/test_screener_fetchers.py
decisions:
  - "Two-gate exit signal: RSI > sell_rsi_threshold AND MACD bearish required (per D-01/D-10) — prevents RSI-only false signals in strong uptrends"
  - "Per-provider quota tracking: analyst_calls table keyed by (date, provider) so Gemini and fallback providers have independent counters (per D-11)"
  - "SELL added to _VALID_SIGNALS: parse_claude_response accepts SELL without parser changes (per D-08)"
  - "analyze_ticker now returns provider_used to enable callers to increment the correct quota counter (per D-11)"
metrics:
  duration_seconds: 309
  completed_date: "2026-04-04"
  tasks_completed: 2
  files_modified: 8
  files_created: 1
---

# Phase 6 Plan 01: Sell Signal Foundation Summary

Foundation layer: config fields, schema migrations, MACD-gated exit signals, sell prompt builder, per-provider quota tracking, and Schwab sell order functions — all importable building blocks for the sell scan loop in Plan 02.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Config fields, schema migrations, DB query helpers | 40de9e7 | config.py, .env.example, database/models.py, database/queries.py |
| 2 | MACD computation, exit signal checker, sell prompt, Schwab sell order | 08e7efa | screener/technicals.py, screener/exit_signals.py, analyst/claude_analyst.py, schwab_client/orders.py |

## What Was Built

### Config Fields (config.py)
- `sell_rsi_threshold: float = 70.0` — distinct from `max_rsi` (buy filter); controls when positions are flagged for sell analysis
- `analyst_daily_limit: int = 18` — shared cap across buy + sell analyst calls; leaves 2-call buffer below Gemini's 20 RPD ceiling
- Both documented in `.env.example`

### Schema Migrations (database/models.py)
- `analyst_calls` table: `(date TEXT, provider TEXT, count INTEGER)` with composite PK `(date, provider)` — per-provider daily quota tracking
- `positions.sell_blocked BOOLEAN DEFAULT 0` — added via CREATE TABLE + ALTER TABLE migration pattern
- `trades.side TEXT DEFAULT 'buy'` — added via CREATE TABLE + ALTER TABLE migration pattern

### DB Query Helpers (database/queries.py)
- `create_trade` extended with optional `side` parameter (default `'buy'`)
- `set_sell_blocked(db_path, ticker)` — sets sell_blocked=1 on open position
- `reset_sell_blocked(db_path, ticker)` — sets sell_blocked=0 on open position
- `get_analyst_call_count_today(db_path, provider)` — returns today's call count for the given provider
- `increment_analyst_call_count(db_path, provider)` — upserts and increments today's row for the given provider

### MACD Computation (screener/technicals.py)
- `compute_macd(prices, fast=12, slow=26, signal=9)` — standard EWM MACD; returns `(None, None, None)` when insufficient data (fail-safe)
- `fetch_technical_data` extended to include `macd_line`, `signal_line`, `macd_histogram` in its returned dict

### Exit Signal Checker (screener/exit_signals.py)
- `check_exit_signals(technical_data, config)` — pure function, two-gate: RSI > threshold AND MACD bearish (macd_line < signal_line)
- Returns False when any value is None — fail-safe for insufficient data

### Sell Prompt + Analyst (analyst/claude_analyst.py)
- `"SELL"` added to `_VALID_SIGNALS` — parser accepts SELL without changes
- `build_sell_prompt(ticker, entry_price, current_price, pnl_pct, hold_days, rsi, macd_line, signal_line, headlines)` — includes full position context and MACD direction label
- `analyze_sell_ticker(...)` — thin wrapper feeding sell prompt into the same `_call_api` + fallback pipeline; returns `provider_used` in result dict
- `analyze_ticker` updated to also return `provider_used` in result dict

### Schwab Sell Orders (schwab_client/orders.py)
- `build_market_sell(ticker, shares)` — returns JSON spec for market sell order via `equity_sell_market`
- `place_sell_order(ticker, shares, config, client)` — mirrors `place_order` but uses `build_market_sell`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test_fetch_technical_data_happy_path_returns_all_keys**
- **Found during:** Task 2 post-verification (`pytest tests/ -x -q`)
- **Issue:** Test had hardcoded `set(result.keys()) == {"rsi", "price", "ma50", "volume", "avg_volume"}` — failed because fetch_technical_data now returns 3 additional MACD keys
- **Fix:** Updated expected key set to include `"macd_line"`, `"signal_line"`, `"macd_histogram"`
- **Files modified:** tests/test_screener_fetchers.py
- **Commit:** 08e7efa (included in Task 2 commit)

## Test Results

- 172 tests pass (up from 172 before — no new tests in this plan; Plan 02 will add sell-signal tests)
- All existing tests unbroken

## Known Stubs

None — all functions are fully implemented with real logic. No placeholder returns.

## Self-Check: PASSED
