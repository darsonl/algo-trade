---
phase: 07-etf-scan-separation
plan: 02
subsystem: analyst, discord_bot
tags: [etf, analyst, prompt, embed, tdd]
dependency_graph:
  requires: [07-01]
  provides: [build_etf_prompt, analyze_etf_ticker, build_etf_recommendation_embed]
  affects: [analyst/claude_analyst.py, discord_bot/embeds.py]
tech_stack:
  added: []
  patterns: [tdd-red-green, none-safe-formatting, fallback-provider-pattern]
key_files:
  created: []
  modified:
    - analyst/claude_analyst.py
    - discord_bot/embeds.py
    - tests/test_analyst_claude.py
    - tests/test_discord_embeds.py
decisions:
  - "build_etf_prompt uses None-safe formatting for all numeric params (price, ma50, rsi, macd_line, signal_line, macd_histogram, expense_ratio) — shows N/A if missing"
  - "analyze_etf_ticker follows exact same return shape as analyze_ticker (signal, reasoning, provider_used) for quota tracking consistency"
  - "build_etf_recommendation_embed reuses _SIGNAL_COLORS map — green/yellow/red consistent with stock embeds"
  - "MA50 Trend field shows 'Above MA50' or 'Below MA50' relative to price rather than raw value — human-readable"
metrics:
  duration: "~20 minutes"
  completed: "2026-04-07T12:34:00Z"
  tasks_completed: 2
  files_modified: 4
---

# Phase 7 Plan 2: ETF Analyst and Embed Summary

**One-liner:** `build_etf_prompt` with RSI/MACD/expense ratio (no stock fundamentals), `analyze_etf_ticker` matching `analyze_ticker` return shape, and `build_etf_recommendation_embed` with ETF-specific fields and [ETF] title tag.

## What Was Built

### Task 1: build_etf_prompt and analyze_etf_ticker

Added to `analyst/claude_analyst.py`:

- `build_etf_prompt(ticker, headlines, rsi, macd_line, signal_line, macd_histogram, expense_ratio, price, ma50)`:
  - Produces an ETF-focused prompt with RSI (14), MACD Line, Signal Line, Histogram, and Expense Ratio
  - Excludes P/E, Dividend Yield, and Earnings Growth (stock-only fundamentals)
  - None-safe: all numeric params show "N/A" if missing
  - Logs DEBUG when `expense_ratio` is None (per D-07)
  - Ends with the standard `SIGNAL: <BUY|HOLD|SKIP>` / `REASONING:` format (per D-03)
  - Includes headlines block (same pattern as `build_prompt` — per D-01)

- `analyze_etf_ticker(ticker, headlines, tech_data, expense_ratio, config, client, fallback_client)`:
  - Calls `build_etf_prompt` (not `build_prompt`)
  - Follows same fallback provider pattern as `analyze_ticker`
  - Returns `{"signal": str, "reasoning": str, "provider_used": str}` — identical shape
  - Respects `config.analyst_call_delay_s`

Added 8 new ETF tests in `tests/test_analyst_claude.py` (22 total, all pass).

### Task 2: build_etf_recommendation_embed

Added to `discord_bot/embeds.py`:

- `build_etf_recommendation_embed(ticker, signal, reasoning, price, rsi, ma50, expense_ratio)`:
  - Title format: `"{ticker} — {signal} [ETF]"`
  - Reuses `_SIGNAL_COLORS` (BUY=green, HOLD=yellow, SKIP=red)
  - Fields: Price, RSI, MA50 Trend, Expense Ratio
  - MA50 Trend shows "Above MA50" or "Below MA50" relative to price; "N/A" if either is None
  - None-safe: RSI and Expense Ratio show "N/A" if missing
  - Raises `ValueError` on invalid signal (consistent with `build_recommendation_embed`)

Added 6 new ETF embed tests in `tests/test_discord_embeds.py` (18 total, all pass).

## Verification

```
pytest tests/test_analyst_claude.py tests/test_discord_embeds.py -v
# 40 passed

pytest  (full suite)
# 236 passed, 1 warning
```

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 RED | d69a25e | test(07-02): add failing ETF prompt and analyze_etf_ticker tests (RED) |
| 1 GREEN | f2348c6 | feat(07-02): add build_etf_prompt and analyze_etf_ticker to claude_analyst.py |
| 2 RED | 66650e4 | test(07-02): add failing ETF embed tests (RED) |
| 2 GREEN | c8915c6 | feat(07-02): add build_etf_recommendation_embed to embeds.py |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all functions are fully implemented. `build_etf_prompt` produces real prompts with real data. `analyze_etf_ticker` makes real API calls via `_call_api`. `build_etf_recommendation_embed` produces real Discord embeds. No placeholder data.

## Threat Flags

None — no new network endpoints, auth paths, or trust boundaries beyond those documented in the plan's threat model (T-07-06 through T-07-08). `parse_claude_response` validation (T-07-06) is reused unchanged.

## Self-Check: PASSED

- [x] `analyst/claude_analyst.py` contains `def build_etf_prompt(`
- [x] `analyst/claude_analyst.py` contains `def analyze_etf_ticker(`
- [x] `build_etf_prompt` output contains "RSI", "MACD", "Expense Ratio"
- [x] `build_etf_prompt` output does NOT contain "Trailing P/E", "Dividend Yield", "Earnings Growth"
- [x] `build_etf_prompt` output contains "SIGNAL: <BUY|HOLD|SKIP>"
- [x] `analyze_etf_ticker` returns dict with keys "signal", "reasoning", "provider_used"
- [x] `discord_bot/embeds.py` contains `def build_etf_recommendation_embed(`
- [x] ETF embed title contains "[ETF]"
- [x] ETF embed has fields: "Price", "RSI", "MA50 Trend", "Expense Ratio"
- [x] `tests/test_analyst_claude.py` has 8 new ETF test functions (22 total pass)
- [x] `tests/test_discord_embeds.py` has 6 new ETF embed test functions (18 total pass)
- [x] `pytest tests/test_analyst_claude.py tests/test_discord_embeds.py` exits 0 (40 passed)
- [x] Full suite `pytest` exits 0 (236 passed)
- [x] Commits d69a25e, f2348c6, 66650e4, c8915c6 exist
