# Roadmap: Algo Trade Bot — Milestone 1

**Goal:** Refactor, harden, document, test, and extend with position monitoring and sell functionality.
**Core Value:** The bot must never place a real order without explicit human approval via Discord.
**Defined:** 2026-03-30

---

## Phase 1: Refactoring & Code Quality

**Goal:** Clean up the existing codebase so every subsequent phase builds on a solid foundation.

**Deliverables:**
- Global config singleton removed from config.py; main.py owns Config instance
- Anthropic client created once per scan (not per ticker)
- yf.Ticker object shared between fundamentals + technicals fetchers
- Volume ratio, min data points promoted to named constants / Config fields
- All deferred imports moved to module top level
- Custom manual_scan event replaced with direct async call
- earnings_growth column added to recommendations table
- parse_positions made safe with .get() fallbacks
- SQLite WAL mode enabled

**Requirements:** REF-01 to REF-10
**Verification:** All existing tests still pass; `pytest` green after refactor

---

## Phase 2: Reliability & Error Handling

**Goal:** Make the bot production-safe — no silent failures, graceful degradation on API issues.

**Deliverables:**
- Structured logging: RotatingFileHandler to `logs/algo_trade.log`, configurable log level via .env
- Retry logic via `tenacity`: all yfinance, Claude, Schwab, and Discord calls wrapped with exponential backoff
- Ops Discord alert: if scan completes with 0 recommendations, post a warning to the channel
- S&P 500 cache: `sp500_cache.json` with 24h TTL; Wikipedia fetch falls back to cache on failure
- Live trading safety: startup warning (log + Discord message) when DRY_RUN=false AND PAPER_TRADING=false
- bot.send_recommendation: `get_channel` → `fetch_channel`
- place_order: exception on real order failure; None only for dry-run
- on_ready: validate channel exists at startup, fail fast with clear error

**Requirements:** REL-01 to REL-08
**Verification:** Simulate network failures (mock exceptions) — bot logs errors and continues; ops alert fires on zero-result scan

---

## Phase 2.5: Analyst Token Minimization

**Goal:** Prevent Gemini free-tier rate limit exhaustion (5 RPM / 20 RPD) by narrowing the analyst call universe, caching results by news content, throttling inter-call rate, and respecting API-specified retry delays on 429s.

**Deliverables:**
- `get_top_sp500_by_fundamentals(config)` in universe.py: ranks S&P 500 by EPS+ROE, returns top `TOP_SP500_COUNT` (default 10), 24h in-memory cache
- `run_scan()` uses `get_top_sp500_by_fundamentals` instead of `get_sp500_tickers` (universe: ~20 tickers)
- `analyst_cache` SQLite table keyed by `(ticker, headline_hash)`; `get_cached_analysis`/`set_cached_analysis` in queries.py
- `run_scan()` checks analyst cache before each `analyze_ticker` call; stores result after API call
- `ANALYST_CALL_DELAY_S=12.0` config field; `analyze_ticker` sleeps N seconds before `_call_api` (<=5 RPM on Gemini free tier)
- `_wait_for_retry` callable in claude_analyst.py parses Gemini `retryDelay` from 429 body; exponential fallback for other providers

**Requirements:** TOK-01 to TOK-06
**Plans:** 4 plans (completed)

Plans:
- [x] 02.5-01-PLAN.md -- Config fields, universe fn
- [x] 02.5-02-PLAN.md -- analyst_cache schema + queries
- [x] 02.5-03-PLAN.md -- Inter-call delay + _wait_for_retry
- [x] 02.5-04-PLAN.md -- Cache flow + universe swap in run_scan

**Verification:** Universe narrows to ~20 tickers; repeated scan with unchanged headlines makes 0 analyst calls; 12s delay enforces <=5 RPM; 100 tests green

**Post-completion addition (2026-04-03):** Analyst fallback provider — when primary API call fails after retries (quota exhausted, rate limit), `analyze_ticker()` automatically retries with a configurable fallback model. Currently configured with Gemma 4 31B (same Gemini key, 15 RPM / 1.5K RPD).

---

## Phase 3: Documentation

**Goal:** Every public function is documented; new contributors can set up and understand the bot without reading source.

**Deliverables:**
- `.env.example` with all 14+ variables, placeholder values, and inline comments (marks required vs optional)
- Docstrings on `run_scan`, `main`, `on_ready`, `configure_scheduler`
- `schwab_client/auth.py`: OAuth first-run flow documented (port 8182, browser prompt, callback_url match requirement)
- `passes_technical_filter`: volume multiplier and RSI threshold explained in docstring
- All public functions across screener/, analyst/, database/, discord_bot/, schwab_client/ have docstrings

**Requirements:** DOC-01 to DOC-05
**Verification:** No public function missing a docstring (grep check); .env.example covers all Config fields

---

## Phase 4: Test Coverage Expansion

**Goal:** Cover the untested pipeline — run_scan, button handlers, API fetchers — with meaningful tests.

**Deliverables:**
- `test_run_scan.py`: end-to-end run_scan with mocked yfinance, Claude, Schwab, Discord — happy path + error paths
- `test_discord_buttons.py`: ApproveRejectView.approve (DB update, dry-run branch, order call) + reject
- `test_analyze_ticker.py`: analyze_ticker with mocked anthropic.Anthropic (BUY, HOLD, SKIP, ValueError propagation)
- `test_screener_fetchers.py`: fetch_technical_data + fetch_fundamental_info with mocked yf.Ticker
- `test_database.py` extended: ticker_recommended_today timezone boundary, expire_stale edge cases
- All new tests follow existing pytest conventions (no live API calls, synthetic data)

**Requirements:** TEST-01 to TEST-08
**Verification:** `pytest -v` passes; new tests cover all described scenarios

---

## Phase 5: Position Monitoring

**Goal:** Track what the bot owns — open positions with cost basis, P&L, and exposure checks.

**Deliverables:**
- `positions` table: `(id, ticker, shares, avg_cost_usd, entry_price, entry_date, status, last_price, last_updated)`
- `database/queries.py`: create_position, update_position (avg cost on additional buy), get_open_positions, close_position
- On buy approval: position record created or updated in positions table
- Before buy approval: check combined position exposure vs MAX_POSITION_SIZE_USD; warn if over limit
- Daily scan: skip BUY analysis for tickers where position status='open'
- `/positions` Discord slash command: embed table of open holdings (ticker, shares, avg cost, est. P&L%)
- `screener/positions.py`: get_position_summary helper (fetch last price via yfinance, compute P&L)

**Requirements:** POS-01 to POS-06
**Plans:** 3 plans

Plans:
- [ ] 05-01-PLAN.md -- positions table DDL + 6 CRUD query functions + tests
- [ ] 05-02-PLAN.md -- approve handler exposure check + position upsert + run_scan skip guard
- [ ] 05-03-PLAN.md -- /positions slash command + get_position_summary helper + embed

**Verification:** `/positions` command shows correct holdings after test buys in dry-run; exposure check blocks over-limit approvals

---

## Phase 6: Sell Signals & Sell Orders

**Goal:** Complete the trading loop — the bot can recommend and execute sells with the same human-approval flow as buys.

**Deliverables:**
- `analyst/claude_analyst.py`: `build_sell_prompt(ticker, position, news)` + `analyze_sell_ticker()` → SELL/HOLD signal
- `screener/exit_signals.py`: `check_exit_signals(position, technical_data)` → bool (RSI > MAX_RSI, configurable)
- Daily scan: after buy pass, iterate open positions → run exit signal check + Claude SELL analysis
- SELL recommendations stored in `recommendations` table (signal='SELL', status=pending)
- `discord_bot/embeds.py`: `build_sell_embed(ticker, signal, reasoning, entry_price, current_price, pnl_pct)` — red theme
- `discord_bot/bot.py`: `SellApproveRejectView` — same button pattern, calls `place_sell_order`
- `schwab_client/orders.py`: `build_market_sell(ticker, shares)` + `place_sell_order(config, ticker, shares)`
- On sell approval: trade record created (side='sell'), position closed in positions table
- On sell rejection: recommendation marked 'rejected', position remains open

**Requirements:** SELL-01 to SELL-09
**Verification:** In dry-run mode, approve a sell → log shows sell order details, position status='closed', trade record created with side='sell'

---

## Milestone Complete

**Definition of Done:**
- [ ] All 42 v1 requirements verified
- [ ] `pytest` passes with no failures
- [ ] Bot runs `python main.py` in dry-run: daily scan executes, buy + sell recommendations posted to Discord, buttons functional
- [ ] `.env.example` documents all required configuration
- [ ] No public function is missing a docstring
- [ ] PAPER_TRADING=true sell flow verified end-to-end in Schwab sandbox

---

## Phase Sequence

```
Phase 1 (Refactoring)
    |
Phase 2 (Reliability)      <- depends on clean code from Phase 1
    |
Phase 2.5 (Token Minimization) <- depends on reliability layer from Phase 2
    |
Phase 3 (Documentation)    <- can run in parallel with Phase 2.5, shown sequential for simplicity
    |
Phase 4 (Testing)          <- tests the refactored + hardened code
    |
Phase 5 (Position Monitoring)   <- DB schema + queries needed before sell logic
    |
Phase 6 (Sell Signals & Orders) <- depends on positions table from Phase 5
```

---
*Roadmap defined: 2026-03-30*
*Last updated: 2026-04-03 — Phase 5 complete; backlog 999.1 added*

---

## Backlog

### Phase 999.2: Skip Fundamental Filtering for ETFs (BACKLOG)

**Goal:** ETFs in the watchlist (SPY, QQQ, GLD, BND, etc.) are incorrectly dropped by the fundamental filter because they have no earnings growth, low/no dividend yield, and high/no P/E ratio. The filter was designed for individual stocks.

**Context:** Fix by detecting ETFs via `yfinance quoteType='ETF'` and bypassing `passes_fundamental_filter` for them — ETFs pass directly to the analyst. Individual stock thresholds remain unchanged.

**Requirements:** TBD
**Plans:** 0 plans

Plans:
- [ ] TBD (promote with /gsd:review-backlog when ready)

---

### Phase 999.1: Asyncio Event Loop Blocking — yfinance Calls in run_scan (BACKLOG)

**Goal:** Prevent Discord gateway heartbeat blocks during daily scans by offloading synchronous yfinance calls off the event loop.

**Context:** `fetch_fundamental_info`, `fetch_news_headlines`, and `get_top_sp500_by_fundamentals` are synchronous blocking calls currently running directly on the Discord event loop inside `run_scan`. Each call takes 1-3 seconds; across a full universe scan this blocks the heartbeat for 10+ seconds, risking gateway disconnection. Only `analyze_ticker` is currently wrapped in `asyncio.to_thread`. Fix: wrap the three yfinance calls in `asyncio.to_thread` as well.

**Requirements:** TBD
**Plans:** 0 plans

Plans:
- [ ] TBD (promote with /gsd:review-backlog when ready)
