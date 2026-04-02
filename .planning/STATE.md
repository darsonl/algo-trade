# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-30)

**Core value:** The bot must never place a real order without explicit human approval via Discord.
**Current focus:** Phase 3 — Documentation (next after Phase 2.5 merge)

---

## Milestone

**Milestone 1:** Refactor, Harden, Test, and Extend with Sell Functionality
**Started:** 2026-03-30
**Phases:** 6 total (+1 inserted)

---

## Phase Status

| Phase | Name | Status |
|-------|------|--------|
| 1 | Refactoring & Code Quality | ✅ Complete |
| 2 | Reliability & Error Handling | ✅ Complete |
| 2.5 | Analyst Token Minimization | ✅ Complete |
| 3 | Documentation | ✅ Complete |
| 4 | Test Coverage Expansion | 🔄 In progress (2/N plans) |
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

## Phase 2.5 Context

- Source: approved design spec docs/superpowers/specs/2026-04-01-analyst-token-minimization-design.md
- 4 plans executed: Wave 1 (config+universe, DB cache, rate limiting) -> Wave 2 (main.py integration)
- Branch merged: feat/analyst-token-minimization (100 tests green)
- Config defaults corrected post-merge: TOP_SP500_COUNT=10, ANALYST_CALL_DELAY_S=12.0

## Next Action

Continue Phase 4 Test Coverage Expansion — plans 04-01 and 04-02 complete.

### Phase 4 Plan 02 Completed (2026-04-03)
Tests for Discord button handlers and DB edge cases (TEST-02, TEST-03, TEST-07, TEST-08).
- Created tests/test_discord_buttons.py: 10 async tests for ApproveRejectView approve/reject
- Extended tests/test_database.py: 13 new tests for ticker_recommended_today and expire_stale boundary semantics
- Deviation: discord.py wraps @discord.ui.button methods into Button objects; used view.approve.callback.callback(view, ...) pattern
- Commit: ccfbaab; 147 tests green

### Phase 3 Completed (2026-04-02)
All DOC-01 through DOC-05 applied. Key changes:
- Rewrote .env.example with all 14+ Config fields, both analyst provider paths (ANTHROPIC_API_KEY legacy + ANALYST_* multi-provider)
- Docstrings added to get_connection, initialize_db (database/models.py)
- Docstrings added to all 8 CRUD functions in database/queries.py
- Docstring added to build_recommendation_embed (discord_bot/embeds.py)
- Class + method docstrings for ApproveRejectView, TradingBot, setup_hook, send_recommendation (discord_bot/bot.py)
- Docstrings for main() and nested on_ready() (main.py)
- Expanded get_client docstring covering port 8182, callback_url match, token file (schwab_client/auth.py)
- Expanded passes_technical_filter docstring referencing config.max_rsi, config.min_volume_ratio, None-check (screener/technicals.py)
- 100 tests green; single atomic commit 07a193e

### Phase 2.5 Completed (2026-04-02)
All TOK-01 through TOK-06 applied. Key changes:
- get_top_sp500_by_fundamentals(config) in universe.py — top 10 S&P by EPS+ROE, 24h in-memory cache
- analyst_cache table + get_cached_analysis/set_cached_analysis in queries.py
- run_scan() — SHA-256 headline hash, cache check before/store after analyze_ticker
- analyze_ticker() — time.sleep(config.analyst_call_delay_s) before _call_api
- _wait_for_retry in claude_analyst.py — parses Gemini retryDelay from 429 body

### Phase 1 Completed (2026-03-31)
All REF-01 through REF-10 applied. Key changes:
- WAL mode, earnings_growth schema + migration, safe parse_positions (DB/Schwab)
- MA_WINDOW/MIN_HISTORY_BARS constants, min_volume_ratio in Config
- All deferred imports moved to module top level
- Config() singleton removed; main() owns its own instance
- Shared yf.Ticker per ticker (halves yfinance network calls)
- Single anthropic.Anthropic client per scan (reuses HTTP connection pool)
- bot.dispatch("manual_scan") replaced with asyncio.create_task via stored callback
