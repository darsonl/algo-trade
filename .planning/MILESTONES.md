# Milestones

## v1.2 Signal Quality & Portfolio Analytics (Shipped: 2026-04-14)

**Phases completed:** 5 phases, 8 plans, 16 tasks

**Key accomplishments:**

- Confidence scoring data layer: `_VALID_CONFIDENCE` allowlist, three prompt builders updated, parser extracts `high/medium/low` with boundary-safe reasoning extraction, nullable `confidence` columns on both DB tables with idempotent migrations, cache and recommendation queries updated — 16 new tests, zero regressions across 316-test suite
- Confidence badge wired end-to-end: all three embed builders display optional Confidence field, bot send methods forward confidence, main.py passes `analysis.get("confidence")` to cache writes, DB creation, and Discord sends — 14 new tests (11 embed + 3 wiring), 331 total, zero regressions
- APScheduler ETF job registered at bot startup via etf_scan_ prefixed IDs, configurable via ETF_SCAN_HOUR/ETF_SCAN_MINUTE (default 09:30), fully independent from the 09:00 stock scan job
- ETF embeds now visually flag high-cost funds via configurable threshold: expense_ratio > ETF_MAX_EXPENSE_RATIO renders "⚠️ 0.0075 (High)" in the Discord embed Expense Ratio field

---

## v1.1 ETF + Async (Shipped: 2026-04-11)

**Phases completed:** 2 phases (7–8), 4 plans, 252 tests green
**Timeline:** 5 days (2026-04-07 → 2026-04-11), 32 commits
**Codebase:** 90 files changed, +5,116/−6,829 lines

**Key accomplishments:**

1. ETF scan foundation — `partition_watchlist()` splits watchlist by yfinance quoteType with allowlist fallback; `etf_watchlist.txt` separates ETF universe; `asset_type` column added to recommendations table
2. ETF analyst path — `build_etf_prompt` focused on RSI/MACD/expense ratio (no P/E, no earnings growth); `analyze_etf_ticker` with same return shape + quota tracking as stock path
3. ETF Discord integration — `build_etf_recommendation_embed` with `[ETF]` title tag, MA50 Trend, Expense Ratio fields; `/scan_etf` slash command + `run_scan_etf` coroutine; `_ETF_ALLOWLIST` removed from fundamentals.py
4. Asyncio event loop hardened — all 9 blocking yfinance calls (fetch_fundamental_info ×2, fetch_news_headlines ×3, fetch_technical_data ×4) wrapped in `asyncio.to_thread` across buy, sell, and ETF passes; zero bare synchronous yfinance calls remain on the event loop

**Archive:** `.planning/milestones/v1.1-ROADMAP.md`, `.planning/milestones/v1.1-REQUIREMENTS.md`

---

## v1.0 MVP (Shipped: 2026-04-06)

**Phases completed:** 7 phases (1, 2, 2.5, 3, 4, 5, 6), 17 plans, 222 tests green
**Timeline:** 9 days (2026-03-28 → 2026-04-06), 81 commits
**Codebase:** 15,278 Python LOC, 98 files changed (+13,515 lines)

**Key accomplishments:**

1. Refactored codebase foundations — eliminated global config singleton, shared yf.Ticker + single Anthropic client per scan, promoted hardcoded thresholds to Config constants
2. Structured reliability layer — RotatingFileHandler logging, tenacity retry on all external APIs, S&P 500 JSON cache, live trading startup warning
3. Gemini free-tier defense — analyst cache by SHA-256 news hash, top-10 S&P universe narrowing, 12s inter-call throttle, 429 retryDelay parsing, Gemma 4 31B fallback provider
4. Position tracking system — positions table with weighted avg cost, exposure guard on buys, open-position skip guard, `/positions` Discord command with live P&L
5. Full sell pipeline — RSI+MACD two-gate exit signal, Claude SELL analysis, red Discord embeds with P&L, `SellApproveRejectView`, Schwab market sell order
6. Per-provider analyst daily quota tracking — analyst_calls DB table, quota guard in main.py, provider_used field in analyze functions

**Known Gaps (proceeded with incomplete requirements):**

Documentation gap only — all work was completed but requirements REF-01..10, REL-01..08, TOK-01..06, DOC-01..05, TEST-01..08, POS-04, POS-05, POS-06 were not formally checked off in REQUIREMENTS.md as phases completed. Functionality is present in the codebase.

**Archive:** `.planning/milestones/v1.0-ROADMAP.md`, `.planning/milestones/v1.0-REQUIREMENTS.md`

---
