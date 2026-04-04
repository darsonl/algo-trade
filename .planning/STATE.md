# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-30)

**Core value:** The bot must never place a real order without explicit human approval via Discord.
**Current focus:** Phase 5 — Position Monitoring (next after Phase 4 complete)

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
| 4 | Test Coverage Expansion | ✅ Complete |
| 5 | Position Monitoring | ✅ Complete (3/3 plans done) |
| 6 | Sell Signals & Sell Orders | 🔄 In Progress (2/3 plans done) |

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

Phase 6 Plan 03: Tests for sell flow (SellApproveRejectView handlers, sell pass, build_sell_embed). Run `/gsd:execute-phase 6`.

### Phase 6 Plan 02 Completed (2026-04-05)
End-to-end sell pipeline wired: sell pass in run_scan, red Discord embeds, SellApproveRejectView (SELL-03/04/05/06/08/09).
- discord_bot/embeds.py: build_sell_embed (red embed with entry/current price, P&L%, shares, RSI) + SELL in _SIGNAL_COLORS
- discord_bot/bot.py: SellApproveRejectView (approve: place_sell_order + create_trade(side='sell') + close_position; reject: set_sell_blocked) + send_sell_recommendation on TradingBot
- main.py: sell pass loop after buy pass — iterates open positions, skips sell_blocked (RSI auto-reset), check_exit_signals, analyze_sell_ticker, send_sell_recommendation
- Rule 1 auto-fix: patched get_open_positions in test_main.py + test_run_scan.py (in-memory DB had no positions table)
- Foundation prerequisite (Plan 01) also implemented: sell_rsi_threshold config, sell_blocked + side schema migrations, screener/exit_signals.py, build_sell_prompt + analyze_sell_ticker, build_market_sell + place_sell_order
- Commits: 531b66e (foundation), e66962e (discord), 7b59d8b (main.py); 172 tests green

### Production Fixes Applied (2026-04-03)
Two issues discovered during first live run after Phase 5:

1. **get_top_sp500_by_fundamentals blocked event loop** — was called synchronously inside async run_scan, blocking Discord heartbeat for 110+ seconds. Fixed: wrapped in `asyncio.to_thread` (commit ae66e64). Backlog 999.1 tracks remaining yfinance blocking calls (fetch_fundamental_info, fetch_news_headlines).

2. **Production DB missing tables** — algo_trade.db was missing analyst_cache and positions tables (created in Phase 2.5 and Phase 5 respectively). Fixed: ran initialize_db manually against production DB. Root cause: bot was started with stale binary before new code was running. Not a code bug.

3. **Watchlist ETFs filtered out by fundamental filter** — SPY, QQQ etc. have no earnings growth / low dividend yield and fail the stock-oriented fundamental filter. April 3 scan returned 0 recs (also partly due to tariff sell-off). Backlog 999.2 tracks ETF bypass. No code change needed now.

### Phase 5 Plan 03 Completed (2026-04-03)
/positions slash command with live P&L display (POS-03, POS-04).
- Created screener/positions.py: get_position_summary fetches yfinance fast_info.last_price with DB fallback; computes pnl_pct per position
- Added build_positions_embed to discord_bot/embeds.py: inline fields per position, truncates at 25, "No open positions." for empty
- discord_bot/bot.py: /positions registered in setup_hook, _positions_command uses asyncio.to_thread (non-blocking), local import for circular-import prevention
- 8 new tests (6 summary+embed, 2 command); 172 tests green
- Commits: 3f479e2, 63470dd

### Phase 5 Plan 02 Completed (2026-04-03)
Exposure guard + position upsert in approve handler; open-position skip guard in run_scan (POS-02, POS-05, POS-06).
- discord_bot/bot.py: exposure guard blocks buys exceeding MAX_POSITION_SIZE_USD (ephemeral); upsert_position called on successful approve
- main.py: has_open_position guard skips tickers with open positions before fundamental filter
- 6 new tests (4 button, 2 run_scan); fixed 2 existing test_main.py tests (Rule 1 auto-fix)
- Commits: 80f05da, 8891995, 3ab0275; 164 tests green

### Phase 5 Plan 01 Completed (2026-04-03)
Positions table DDL and 6 CRUD query functions (POS-01, POS-02, POS-03).
- Added positions table to initialize_db executescript in database/models.py
- Added create_position (ON CONFLICT upsert for re-entry), update_position (weighted avg), get_open_positions, has_open_position, close_position, upsert_position to database/queries.py
- Created tests/test_positions.py with 11 tests; all 11 pass
- Weighted avg verified: 5@$100 + 5@$120 = $110.00
- Commit: 24faef6

### Analyst Fallback Provider Added (2026-04-03, outside GSD phases)
Added optional fallback analyst provider to handle Gemini quota exhaustion gracefully.
- `config.py`: 3 new fields — `analyst_fallback_provider`, `analyst_fallback_api_key`, `analyst_fallback_model`
- `analyst/claude_analyst.py`: `create_fallback_client()` + fallback try/except in `analyze_ticker()`; only API failures trigger fallback (not parse errors)
- `main.py`: creates `fallback_client` once per scan, passes to `analyze_ticker`
- `.env`: configured with `ANALYST_FALLBACK_PROVIDER=gemini`, same Gemini API key, `ANALYST_FALLBACK_MODEL=gemma-4-31b-it` (15 RPM / 1.5K RPD free tier)
- `.env.example`: documents new vars with GitHub Models as the recommended alternative

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
