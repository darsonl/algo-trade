---
gsd_state_version: 1.0
milestone: v1.2
milestone_name: Signal Quality & Portfolio Analytics
status: verifying
last_updated: "2026-04-13T10:41:55.533Z"
last_activity: 2026-04-13
progress:
  total_phases: 5
  completed_phases: 4
  total_plans: 7
  completed_plans: 7
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-12)

**Core value:** The bot must never place a real order without explicit human approval via Discord.
**Current focus:** Phase 12 — etf-polish

## Current Position

Phase: 12 (etf-polish) — EXECUTING
Plan: 2 of 2
Status: Phase complete — ready for verification
Last activity: 2026-04-13

---

## Milestone

**Milestone v1.2:** Signal Quality & Portfolio Analytics
**Started:** 2026-04-12
**Phases:** 9–13 (5 phases, 10 requirements)

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
| 13 | Portfolio Analytics | 🔄 Not started |

---

## Codebase Map

Generated: 2026-03-30
Location: .planning/codebase/

- STACK.md — Python 3.x, discord.py, yfinance, anthropic, schwab-py, APScheduler, SQLite
- INTEGRATIONS.md — Discord API, Anthropic Claude API, Schwab OAuth2, Yahoo Finance (unofficial)
- ARCHITECTURE.md — screener → analyst → Discord → Schwab pipeline, daily APScheduler cron
- STRUCTURE.md — module dependency graph, entry points, config loading pattern
- CONVENTIONS.md — snake_case, dataclasses, pure functions, pytest, type hints
- TESTING.md — 252 tests green as of v1.1
- CONCERNS.md — full audit: security, reliability, tech debt, missing features, test gaps

---

## Key Context

- Discord channel ID was previously set to guild ID — corrected to #general (1487794996735377541)
- Bot re-authorized 2026-03-30 with Send Messages, Embed Links, Read Message History
- DRY_RUN=true, PAPER_TRADING=true in .env — safe defaults confirmed
- ETF scan live: `/scan_etf` command, `etf_watchlist.txt` (10 ETFs), ETF-aware analyst prompt
- All 9 blocking yfinance calls wrapped in asyncio.to_thread — gateway heartbeat safe
- Phase 10 complete: sector, SPY trend, VIX, 52-week range injected into all BUY/SELL/ETF prompts via screener/macro.py

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

## Next Action

Phase 12 complete — verify with `/gsd-verify-work 12`, then plan Phase 13 (Portfolio Analytics) with `/gsd-plan-phase 13`
