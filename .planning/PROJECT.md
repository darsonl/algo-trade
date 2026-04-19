# Algo Trade Bot — Project

## What This Is

An automated stock and ETF screener that uses Claude AI to generate BUY and SELL recommendations, posts them to Discord with Approve/Reject buttons, and executes trades via the Schwab brokerage API. The bot runs daily scans for stocks and a separate `/scan_etf` path for ETFs, monitors open positions with live P&L and portfolio stats, and enforces human-in-the-loop approval for every order. A full sell pipeline (RSI+MACD two-gate exit + Claude SELL analysis) closes the trading loop. Claude signals are enriched with sector, macro (SPY MAs, VIX, DXY), and 52-week range context, and include a confidence badge.

## Core Value

The bot must never place a real order without explicit human approval via Discord.

## Current Milestone: v1.3 Risk & Signal Quality

**Goal:** Harden trade execution with limit orders, enrich Claude signals with earnings and fundamental trend data, add trade history visibility, and close critical test gaps.

**Target features:**
- Limit buy orders at signal price (configurable `USE_LIMIT_BUY`, default true)
- Earnings date warning field in Discord BUY embed
- P/E direction (expanding/contracting) + EPS quarterly trend in Claude BUY prompt
- `/history` Discord slash command: last 20 closed trades with entry/exit/P&L
- Test coverage: analyst fallback logic, config validation, run_scan quota exhaustion path

## Requirements

### Validated

- ✓ Daily scheduled scan via APScheduler — v1.0
- ✓ Stock universe from watchlist.txt + top-10 S&P 500 by fundamentals — v1.0
- ✓ Fundamental filter (P/E, dividend yield, earnings growth) — v1.0
- ✓ Claude AI BUY/HOLD/SKIP signal from news headlines — v1.0
- ✓ Technical filter (RSI, MA50, volume) — v1.0
- ✓ Discord embed with Approve/Reject buttons — v1.0
- ✓ Schwab market buy order execution — v1.0
- ✓ SQLite database for recommendations + trades — v1.0
- ✓ Dry-run and paper trading safety flags — v1.0
- ✓ Global config singleton removed; main.py owns Config instance — v1.0 (REF-01)
- ✓ Anthropic client + yf.Ticker shared per-scan (not per-ticker) — v1.0 (REF-02, REF-03)
- ✓ Hardcoded thresholds promoted to Config constants — v1.0 (REF-04, REF-05)
- ✓ Structured logging with RotatingFileHandler (file + stdout) — v1.0 (REL-01)
- ✓ Retry logic (tenacity) on all external API calls — v1.0 (REL-02)
- ✓ Ops Discord alert on zero-recommendation scans — v1.0 (REL-03)
- ✓ S&P 500 cache to sp500_cache.json with 24h TTL — v1.0 (REL-04)
- ✓ Live trading startup warning when DRY_RUN=false + PAPER_TRADING=false — v1.0 (REL-05)
- ✓ Analyst result cache by SHA-256 news hash (DB-backed) — v1.0 (TOK-03, TOK-04)
- ✓ Universe narrowed to top-10 S&P by EPS+ROE; 24h in-memory cache — v1.0 (TOK-01, TOK-02)
- ✓ 12s inter-call delay + 429 retryDelay parsing for Gemini rate limits — v1.0 (TOK-05, TOK-06)
- ✓ Analyst fallback provider (Gemma 4 31B) on primary quota exhaustion — v1.0
- ✓ Per-provider daily analyst quota tracking (analyst_calls table) — v1.0
- ✓ All public functions documented; .env.example covers all Config fields — v1.0 (DOC-01..05)
- ✓ 222-test suite: run_scan, Discord buttons, sell flow, DB edge cases, analyzer — v1.0 (TEST-01..08)
- ✓ Positions table with weighted avg cost, exposure guard, skip-owned guard — v1.0 (POS-01..06)
- ✓ /positions Discord slash command with live P&L — v1.0 (POS-04)
- ✓ Full sell pipeline: RSI+MACD two-gate exit, Claude SELL analysis, Discord embed, Schwab sell order — v1.0 (SELL-01..09)
- ✓ ETF scan separation: `partition_watchlist`, `etf_watchlist.txt`, `asset_type` column, `/scan_etf` command — v1.1 (ETF-01..06)
- ✓ ETF analyst path: `build_etf_prompt` (RSI/MACD/expense ratio, no P/E), `analyze_etf_ticker` — v1.1 (ETF-03)
- ✓ Asyncio event loop hardened: all 9 blocking yfinance calls wrapped in `asyncio.to_thread` — v1.1 (ASYNC-01..04)
- ✓ Scan exceptions posted to Discord ops channel, capped at 3/run with overflow summary — v1.2 (OPS-01)
- ✓ ETF zero-rec alert prefixed with `[ETF]` to distinguish from stock scan silence — v1.2 (ETF-08)
- ✓ Sector name added to Claude BUY/SELL/ETF prompt via `screener/macro.py` — v1.2 (SIG-01)
- ✓ SPY trend (1m + 1y) + VIX level added to Claude BUY/SELL/ETF prompt — v1.2/v1.3 (SIG-02, validated in Phase 14.1)
- ✓ 52-week range position added to Claude BUY/SELL prompt — v1.2 (SIG-03)
- ✓ Claude outputs confidence level (high/medium/low); displayed as badge in Discord embed — v1.2 (SIG-04)
- ✓ Scheduled ETF scan at configurable time offset (default 09:30) via APScheduler — v1.2 (ETF-07)
- ✓ ETF expense ratio threshold flag in Discord embed (`ETF_MAX_EXPENSE_RATIO`) — v1.2 (ETF-09)
- ✓ Total unrealized P&L aggregate in `/positions` embed footer — v1.2 (PORT-01)
- ✓ `/stats` slash command: win rate, avg gain %, avg loss % on closed trades — v1.2 (PORT-02)
- ✓ `/history` slash command: last 20 closed trades with entry/exit/P&L%/date in code-block embed — v1.3 (OPS-02, validated in Phase 14)

### Active

*(none — v1.3 requirements not yet defined)*

### Out of Scope

- Multi-user role-based approval — single operator assumed
- Options, bonds, or non-equity instruments — equity only
- External log shipping (Datadog, CloudWatch) — local file rotation sufficient
- PostgreSQL migration — SQLite with WAL mode sufficient for single-operator use
- HTTP health check endpoint — Discord heartbeat sufficient
- Async parallelization of scan — sequential acceptable for current scale
- Mobile app / web UI — Discord IS the UI
- Parallel ticker scanning via asyncio.gather — explicitly out of scope
- Dynamic ETF universe from external API — static etf_watchlist.txt sufficient
- ETF_SCAN_TIMES config field — single scheduled time sufficient for current use
- Separate Discord ops channel (`DISCORD_OPS_CHANNEL_ID`) — OPS-01 posts to trading channel; separation deferred
- DRY_RUN trade marker in `trades` table — `/stats` includes dry-run trades; documented limitation
- Confidence-based BUY filtering or yellow embed coloring — display-only badge; no behavioral change

## Context

Shipped v1.2 with 372 tests green across 3 milestones, 13 phases, 29 plans.

Tech stack: Python 3.x, discord.py, yfinance, anthropic SDK, schwab-py, APScheduler, SQLite.

Key fragility: yfinance is an unofficial scraper — silent empty returns are a known risk. All blocking yfinance calls are off the event loop (Phase 8 sweep), eliminating gateway disconnect risk.

Key safety: DRY_RUN=true and PAPER_TRADING=true are defaults; live trading requires explicit opt-in.

Signal enrichment live: every BUY/SELL/ETF prompt includes sector, SPY trend (1m + 1y), VIX, 52-week range, and a confidence badge. `screener/macro.py` fetches macro context once per scan, returning `spy_trend_1m`, `spy_trend_1y`, and `vix_level`.

Portfolio analytics live: `/positions` shows total unrealized P&L; `/stats` shows win rate and avg gain/loss on closed trades with `cost_basis` column in `trades` table.

## Constraints

- **Tech stack**: Python + discord.py + yfinance + anthropic + schwab-py — no new languages
- **Safety**: DRY_RUN default must never be changed in source code — operator controls via .env
- **Database**: SQLite only — WAL mode improvement acceptable, no ORM migration
- **Backward compatibility**: existing DB schema changes must be additive (new columns/tables only)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Two-stage filtering (fundamentals before technicals) | Avoids expensive technical fetch on tickers Claude will skip | ✓ Good |
| Pure functions for testability | Enables unit tests without Discord/Schwab mocks | ✓ Good |
| SQLite over PostgreSQL | Single operator, no concurrency requirement | ✓ Good — WAL + migrations covered it |
| Config singleton → main.py ownership | Forced test env setup; now injected cleanly | ✓ Good — resolved Phase 1 |
| Top-level imports (not deferred) + mock.patch | Avoids import-side-effect issues in tests | ✓ Good |
| Analyst cache by SHA-256 news hash | Idempotent: same headlines → 0 API calls on rescan | ✓ Good |
| Universe narrowed to top-10 S&P by fundamentals | Cuts ~490 tickers → ~20; Gemini free tier viable | ✓ Good |
| Gemma 4 31B fallback on same Gemini key | 15 RPM / 1.5K RPD vs 5 RPM / 20 RPD; free tier headroom | ✓ Good |
| RSI+MACD two-gate exit signal | Single-indicator exits are noisy; conjunction reduces false positives | ✓ Good |
| Human approval required for every order | Core value; operator trust requires full visibility | ✓ Good — never compromised |
| ETF scan separated (not bypassed in stock flow) | Clean separation beats ETF-bypass hacks in fundamental filter | ✓ Good — v1.1 |
| `partition_watchlist` wrapped at call site, not inside function | Keeps function pure/testable; caller owns async boundary | ✓ Good — v1.1 |
| asyncio.to_thread for all yfinance I/O (not asyncio.gather) | Unblocks event loop without concurrency; simpler + safer | ✓ Good — v1.1 |
| `fetch_macro_context` returns None dict on any exception | Scan continues without enrichment rather than aborting — v1.2 | ✓ Good |
| Macro context fetched once per scan, threaded through analyze_* | Single yfinance fetch per scan, not per ticker — v1.2 | ✓ Good |
| `parse_claude_response` returns `confidence=None` for missing/invalid values | Scan continues unaffected; confidence omitted from embed — v1.2 | ✓ Good |
| `cost_basis` fetched from DB (`avg_cost_usd`), never from Discord payload | Prevents tampering via interaction payload (T-13-02) — v1.2 | ✓ Good |
| `get_trade_stats()` returns `None` for no-data case | Callers send plain text, not empty embed — v1.2 | ✓ Good |
| ETF scheduled scan via `configure_scheduler` reuse with `job_id_prefix` kwarg | Keeps scheduling logic in one place, avoids duplication — v1.2 | ✓ Good |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-19 — Phase 14.1 complete: spy_trend_1m + spy_trend_1y in fetch_macro_context()*
