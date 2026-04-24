---
status: complete
phase: 16-earnings-date-warning
source: [16-01-SUMMARY.md]
started: 2026-04-25T00:00:00Z
updated: 2026-04-25T00:10:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Full test suite passes (no regressions)
expected: Run `pytest -q` — all 418 tests pass, 0 failures.
result: pass

### 2. Earnings date embed tests (SIG-05)
expected: Run `pytest tests/test_discord_embeds.py -k "earnings_date" -q` — exactly 6 tests collected and all pass.
result: pass

### 3. Earnings date prompt tests (SIG-06)
expected: Run `pytest tests/test_analyst_claude.py -k "earnings_date" -q` — exactly 5 tests collected and all pass.
result: pass

### 4. main.py earningsTimestamp wiring
expected: `_ts = info.get("earningsTimestamp")` present; `earnings_date_embed` and `earnings_date_prompt` computed; both wired into `analyze_ticker` and `bot.send_recommendation`. `from datetime import date, datetime, timezone` at line 6.
result: pass

### 5. ETF path excluded
expected: No `earnings_date` argument in `analyze_etf_ticker` or `build_etf_recommendation_embed` call sites in main.py.
result: pass

### 6. Past/absent timestamps show N/A in embed
expected: `test_embed_earnings_date_shows_na_when_none` and `test_embed_earnings_date_shows_na_string` both pass.
result: pass

### 7. Warning prefix for near-term earnings
expected: `test_embed_earnings_date_warning_prefix` passes — ⚠️ prefix rendered as-is by embed builder.
result: pass

## Summary

total: 7
passed: 7
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none]
