# Algo Trade Bot — Project

## What This Is

An automated stock screener that uses Claude AI to generate BUY and SELL recommendations, posts them to Discord with Approve/Reject buttons, and executes trades via the Schwab brokerage API. The bot runs daily scans, monitors open positions with live P&L, and enforces human-in-the-loop approval for every order. A full sell pipeline (RSI+MACD two-gate exit + Claude SELL analysis) closes the trading loop.

## Core Value

The bot must never place a real order without explicit human approval via Discord.

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

### Active

- [ ] ETF scan separation: `/scan_etf` Discord command, skip fundamental filter for ETFs (Phase 7)
- [ ] Async event loop fix: wrap all blocking yfinance calls in asyncio.to_thread (Phase 8)

### Out of Scope

- Multi-user role-based approval — single operator assumed
- Options, bonds, or non-equity instruments — equity only
- External log shipping (Datadog, CloudWatch) — local file rotation sufficient
- PostgreSQL migration — SQLite with WAL mode sufficient for single-operator use
- HTTP health check endpoint — Discord heartbeat sufficient
- Async parallelization of scan — sequential acceptable for current scale
- Mobile app / web UI — Discord IS the UI

## Context

Shipped v1.0 with 15,278 LOC Python across 9 days (2026-03-28 → 2026-04-06), 81 commits, 222 tests green.

Tech stack: Python 3.x, discord.py, yfinance, anthropic SDK, schwab-py, APScheduler, SQLite.

Key fragility: yfinance is an unofficial scraper — silent empty returns are a known risk. Backlog 999.1 tracks remaining blocking yfinance calls on the Discord event loop (fetch_fundamental_info, fetch_news_headlines, fetch_technical_data — Phase 8 will wrap all of them).

Key safety: DRY_RUN=true and PAPER_TRADING=true are defaults; live trading requires explicit opt-in. Production DB confirmed working with all tables present post-Phase 5 hotfix.

ETF issue noted: SPY, QQQ etc. fail the stock-oriented fundamental filter (no earnings growth). Phase 7 separates ETF scan path.

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

## Current Milestone: v1.1 ETF + Async

**Goal:** Add ETF scan capability and eliminate all blocking yfinance calls from the Discord event loop.

**Target features:**
- ETF scan separation (`/scan_etf` command, `partition_watchlist`, ETF-aware analyst prompt)
- Asyncio event loop fix (wrap all remaining yfinance calls in `asyncio.to_thread`)

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
*Last updated: 2026-04-06 after v1.1 milestone started*
