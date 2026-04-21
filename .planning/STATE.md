---
gsd_state_version: 1.0
milestone: v1.3
milestone_name: Risk & Signal Quality
status: executing
last_updated: "2026-04-21T13:05:57.640Z"
last_activity: 2026-04-21
progress:
  total_phases: 6
  completed_phases: 3
  total_plans: 3
  completed_plans: 3
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-14)

**Core value:** The bot must never place a real order without explicit human approval via Discord.
**Current focus:** Phase 14 — Trade History Command (not started)

## Current Position

Phase: 16
Plan: Not started
Status: Ready to execute
Last activity: 2026-04-21

---

## Milestone

**Milestone v1.3:** Risk & Signal Quality
**Started:** 2026-04-18
**Phases:** 14–18 (5 phases)

---

## Phase Status

| Phase | Name | Status |
|-------|------|--------|
| 1 | Refactoring & Code Quality | ✅ Complete |
| 2 | Reliability & Error Handling | ✅ Complete |
| 2.5 | Analyst Token Minimization | ✅ Complete |
| 3 | Documentation | ✅ Complete |
| 4 | Test Coverage Expansion | ✅ Complete |
| 5 | Position Monitoring | ✅ Complete |
| 6 | Sell Signals & Sell Orders | ✅ Complete |
| 7 | ETF Scan Separation | ✅ Complete (3/3 plans) |
| 8 | Asyncio Event Loop Fix | ✅ Complete (1/1 plan) |
| 9 | Ops Hardening | ✅ Complete (1/1 plan) |
| 10 | Prompt Signal Enrichment | ✅ Complete (2/2 plans) |
| 11 | Confidence Scoring | ✅ Complete (2/2 plans) |
| 12 | ETF Polish | ✅ Complete (2/2 plans) |
| 13 | Portfolio Analytics | ✅ Complete (1/1 plan) |
| 14 | Trade History Command | 🔄 Not started |
| 15 | Fundamental Trend Enrichment | 🔄 Not started |
| 16 | Earnings Date Warning | 🔄 Not started |
| 17 | Limit Buy Orders | 🔄 Not started |
| 14.1 | SPY 1-year trend signal | 🔄 Not started |
| 18 | Test Coverage Gaps | 🔄 Not started |

---

## Codebase Map

Generated: 2026-03-30
Location: .planning/codebase/

- STACK.md — Python 3.x, discord.py, yfinance, anthropic, schwab-py, APScheduler, SQLite
- INTEGRATIONS.md — Discord API, Anthropic Claude API, Schwab OAuth2, Yahoo Finance (unofficial)
- ARCHITECTURE.md — screener → analyst → Discord → Schwab pipeline, daily APScheduler cron
- STRUCTURE.md — module dependency graph, entry points, config loading pattern
- CONVENTIONS.md — snake_case, dataclasses, pure functions, pytest, type hints
- TESTING.md — 372 tests green as of v1.2
- CONCERNS.md — full audit: security, reliability, tech debt, missing features, test gaps

---

## Key Context

- Discord channel ID was previously set to guild ID — corrected to #general (1487794996735377541)
- Bot re-authorized 2026-03-30 with Send Messages, Embed Links, Read Message History
- DRY_RUN=true, PAPER_TRADING=true in .env — safe defaults confirmed
- ETF scan live: `/scan_etf` command, `etf_watchlist.txt` (10 ETFs), ETF-aware analyst prompt
- All 9 blocking yfinance calls wrapped in asyncio.to_thread — gateway heartbeat safe
- Phase 10 complete: sector, SPY trend, VIX, 52-week range injected into all BUY/SELL/ETF prompts via screener/macro.py
- Phase 11 complete: confidence scoring end-to-end (parse → DB → embed badge)
- Phase 12 complete: ETF scheduled scan + expense ratio threshold flag
- Phase 13 complete: /positions total P&L footer + /stats win rate command

## Key Decisions (Phase 11)

- `parse_claude_response` returns `confidence=None` for missing/invalid values, never raises — scan continues unaffected
- `confidence_idx` used as `end_idx` in extra_lines slice to prevent confidence value leaking into reasoning text
- `confidence` stored as lowercase string ('high'/'medium'/'low') or NULL — no default value in schema
- `confidence.capitalize()` converts lowercase DB values to title-case at Discord render time
- `analysis.get('confidence')` is the canonical safe access pattern throughout main.py — None propagates safely when key absent

## Key Decisions (Phase 12 Plan 01)

- `configure_scheduler` reused for ETF job via `times` + `job_id_prefix` kwargs (D-03) — keeps scheduling logic in one place
- ETF job IDs use `etf_scan_` prefix to avoid collision with stock `scan_` IDs
- ETF default 09:30 (30-min offset from stock 09:00) to avoid yfinance rate-limit contention
- `ETF_SCAN_TIMES` multi-time support explicitly deferred (D-02)

## Key Decisions (Phase 12 Plan 02)

- Strict `>` comparison (not `>=`): at-threshold ETFs are acceptable cost, no flag per D-07
- `etf_max_expense_ratio=None` default preserves backward-compat for all existing call sites
- Display-only flag per D-05: no scan filtering or trade gating on expense ratio value
- Warning emoji is hardcoded UTF-8 literal U+26A0 U+FE0F per D-06 — no user input reaches this field

## Key Decisions (Phase 13)

- `pnl_usd` computed at `get_position_summary()` layer, not embed layer — separation of concerns
- Footer omitted entirely when all positions lack price data; `(partial)` suffix when some are missing
- `get_trade_stats()` returns `None` (not `{}`) for no-data case — callers send plain text, not empty embed
- `cost_basis` fetched from DB (`avg_cost_usd`), never from Discord interaction payload (T-13-02 mitigated)
- Break-even (sell_price >= cost_basis) counts as win per plan spec

## Research Decisions (v1.3 — pre-phase)

- `/history` query is sell-side-only SELECT (`WHERE side='sell' AND cost_basis IS NOT NULL`) — no JOIN to avoid cartesian duplicates on multi-trade tickers
- `equity_buy_limit` price must be a formatted string `f"{price:.2f}"` — raw float triggers DeprecationWarning today, future TypeError
- Limit order duration defaults to GTC (not DAY) — DAY + late approval = silent unfill that locks DB recommendation
- `USE_LIMIT_BUY` must use `.lower() == "true"` Config pattern — `bool("false")` is `True` in Python
- Earnings date sourced from `info["earningsTimestamp"]` (already fetched) — zero extra network cost
- `ticker.quarterly_income_stmt` (not `ticker.quarterly_earnings`) for EPS trend — `quarterly_earnings` returns None silently in yfinance 1.2.0
- `quarterly_income_stmt` columns are newest-first `pd.Timestamp` — reverse before extracting last 4 quarters
- New yfinance attributes (`quarterly_income_stmt`) need `asyncio.to_thread` wrapping — not folded into `ticker.info`

## Accumulated Context

### Roadmap Evolution

- Phase 14.1 inserted after Phase 14: SPY 1-year trend signal (INSERTED)

## Next Action

Run `/gsd-plan-phase 14` to plan Phase 14 (Trade History Command).
