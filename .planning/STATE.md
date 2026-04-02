# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-30)

**Core value:** The bot must never place a real order without explicit human approval via Discord.
**Current focus:** Phase 2.5 — Analyst Token Minimization (planned, ready to execute)

---

## Milestone

**Milestone 1:** Refactor, Harden, Test, and Extend with Sell Functionality
**Started:** 2026-03-30
**Phases:** 6 total

---

## Phase Status

| Phase | Name | Status |
|-------|------|--------|
| 1 | Refactoring & Code Quality | ✅ Complete |
| 2 | Reliability & Error Handling | ✅ Complete |
| 2.5 | Analyst Token Minimization | 📋 Planned |
| 3 | Documentation | ⬜ Not started |
| 4 | Test Coverage Expansion | ⬜ Not started |
| 5 | Position Monitoring | ⬜ Not started |
| 6 | Sell Signals & Sell Orders | ⬜ Not started |

---

## Codebase Map

Generated: 2026-03-30
Location: .planning/codebase/

- STACK.md — Python 3.x, discord.py, yfinance, anthropic, schwab-py, APScheduler, SQLite
- INTEGRATIONS.md — Discord API, Anthropic Claude API, Schwab OAuth2, Yahoo Finance (unofficial)
- ARCHITECTURE.md — screener → analyst → Discord → Schwab pipeline, daily APScheduler cron
- STRUCTURE.md — module dependency graph, entry points, config loading pattern
- CONVENTIONS.md — snake_case, dataclasses, pure functions, pytest, type hints
- TESTING.md — existing coverage: technicals, fundamentals filter, analyst parsing, DB, Schwab parse
- CONCERNS.md — full audit: security, reliability, tech debt, missing features, test gaps

---

## Key Context

- Discord channel ID was previously set to guild ID — corrected to #general (1487794996735377541)
- Bot re-authorized 2026-03-30 with Send Messages, Embed Links, Read Message History
- test_discord_live.py confirmed bot posts embeds successfully
- DRY_RUN=true, PAPER_TRADING=true in .env — safe defaults confirmed

---

## Next Action

Phase 2.5 planned (2 plans, 2 waves, all TOK-01–TOK-06 covered, checker passed). Run `/gsd:execute-phase 2.5` to execute.

### Phase 1 Completed (2026-03-31)
All REF-01 through REF-10 applied. Key changes:
- WAL mode, `earnings_growth` schema + migration, safe `parse_positions` (DB/Schwab)
- `MA_WINDOW`/`MIN_HISTORY_BARS` constants, `min_volume_ratio` in Config
- All deferred imports moved to module top level
- `Config()` singleton removed; `main()` owns its own instance
- Shared `yf.Ticker` per ticker (halves yfinance network calls)
- Single `anthropic.Anthropic` client per scan (reuses HTTP connection pool)
- `bot.dispatch("manual_scan")` replaced with `asyncio.create_task` via stored callback
