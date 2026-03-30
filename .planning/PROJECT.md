# Algo Trade Bot — Milestone 1

## What This Is

An automated stock screener that uses Claude AI to generate BUY and SELL recommendations, posts them to Discord with Approve/Reject buttons, and executes trades via the Schwab brokerage API. The bot runs daily scans, monitors open positions, and gives the operator human-in-the-loop control over every order.

## Core Value

The bot must never place a real order without explicit human approval via Discord.

## Requirements

### Validated

<!-- Existing features confirmed working -->

- ✓ Daily scheduled scan via APScheduler — Phase 0 (existing)
- ✓ Stock universe from watchlist.txt + S&P 500 Wikipedia scrape — Phase 0 (existing)
- ✓ Fundamental filter (P/E, dividend yield, earnings growth) — Phase 0 (existing)
- ✓ Claude AI BUY/HOLD/SKIP signal from news headlines — Phase 0 (existing)
- ✓ Technical filter (RSI, MA50, volume) — Phase 0 (existing)
- ✓ Discord embed with Approve/Reject buttons — Phase 0 (existing)
- ✓ Schwab market buy order execution — Phase 0 (existing)
- ✓ SQLite database for recommendations + trades — Phase 0 (existing)
- ✓ Dry-run and paper trading safety flags — Phase 0 (existing)

### Active

- [ ] Refactor: remove global config singleton, move deferred imports, fix custom event pattern
- [ ] Refactor: share yf.Ticker object across fetchers, instantiate Anthropic client once per scan
- [ ] Refactor: promote hardcoded thresholds to Config, derive computed constants
- [ ] Reliability: structured logging with file rotation
- [ ] Reliability: retry logic for all external API calls (yfinance, Claude, Schwab, Discord)
- [ ] Reliability: ops alert to Discord when scan produces zero recommendations
- [ ] Reliability: S&P 500 cache with TTL
- [ ] Reliability: live trading warning when DRY_RUN=false + PAPER_TRADING=false
- [ ] Reliability: fix `get_channel` → `fetch_channel` in bot.py
- [ ] Reliability: distinguish order failure vs dry-run None in place_order
- [ ] Documentation: create .env.example with all variables and comments
- [ ] Documentation: docstrings on run_scan, main, on_ready, auth OAuth flow
- [ ] Testing: test run_scan pipeline with full mocks
- [ ] Testing: test ApproveRejectView approve/reject handlers
- [ ] Testing: test analyze_ticker with mocked Anthropic client
- [ ] Testing: test fetch_technical_data and fetch_fundamental_info with mocked yfinance
- [ ] Testing: test timezone behavior in ticker_recommended_today
- [ ] Position monitoring: positions table (ticker, shares, avg_cost, entry_date, status)
- [ ] Position monitoring: Discord /positions command showing holdings + P&L
- [ ] Position monitoring: check existing exposure before allowing duplicate buy
- [ ] Sell signals: Claude SELL/HOLD analysis on held positions
- [ ] Sell signals: technical exit triggers (RSI overbought, MA cross)
- [ ] Sell orders: build_market_sell in schwab_client, Discord Approve/Reject flow

### Out of Scope

- Multi-user role-based approval — single operator assumed for v1
- Options, bonds, or non-equity instruments — equity only, Schwab parse_positions would need extension
- External log shipping (Datadog, CloudWatch) — local file rotation sufficient for v1
- PostgreSQL migration — SQLite with WAL mode sufficient for single-operator use
- HTTP health check endpoint — Discord heartbeat sufficient for v1
- Async parallelization of scan — sequential scan acceptable for v1 scale

## Context

- Brownfield project: all existing features are working and tested at a basic level
- Codebase map completed 2026-03-30; full concerns audit available at .planning/codebase/CONCERNS.md
- Key fragility: yfinance is an unofficial scraper — silent empty returns are a known risk
- Key safety: DRY_RUN=true and PAPER_TRADING=true are defaults; live trading requires explicit opt-in
- Discord channel confirmed working as of 2026-03-30 (channel ID corrected from guild ID)
- Bot permissions: Send Messages, Embed Links, Read Message History (re-authorized 2026-03-30)

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
| SQLite over PostgreSQL | Single operator, no concurrency requirement | ✓ Good — WAL mode will cover edge cases |
| Global config singleton in config.py | Convenience but forces test env setup | ⚠️ Revisit — moving to main.py ownership in Phase 1 |
| Deferred imports inside functions | Avoids test import side effects | ⚠️ Revisit — use top-level imports + mock.patch instead |

---
*Last updated: 2026-03-30 — initial milestone planning*
