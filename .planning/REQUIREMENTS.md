# Requirements: Algo Trade Bot — Milestone 1

**Defined:** 2026-03-30
**Core Value:** The bot must never place a real order without explicit human approval via Discord.

---

## v1 Requirements

### Refactoring

- [ ] **REF-01**: Global config singleton removed from config.py; main.py owns the single Config instance
- [ ] **REF-02**: Anthropic client instantiated once per scan, not once per ticker
- [ ] **REF-03**: yf.Ticker object shared between fundamentals and technicals fetchers for same ticker
- [ ] **REF-04**: Volume ratio threshold promoted to Config.min_volume_ratio (default 0.5)
- [ ] **REF-05**: Minimum data point constant derived from MA_WINDOW (not hardcoded 51)
- [ ] **REF-06**: All deferred function-body imports moved to module top level
- [ ] **REF-07**: Custom manual_scan event dispatch replaced with direct async call pattern
- [ ] **REF-08**: earnings_growth column added to recommendations table and populated in run_scan
- [ ] **REF-09**: parse_positions uses .get() with safe fallbacks (no KeyError on non-equity positions)
- [ ] **REF-10**: SQLite WAL mode enabled in get_connection

### Reliability

- [ ] **REL-01**: Structured logging with RotatingFileHandler (file + stdout, configurable level)
- [ ] **REL-02**: Retry logic (exponential backoff via tenacity) wrapping yfinance, Claude, Schwab, Discord calls
- [ ] **REL-03**: ops Discord alert posted when scan completes with zero recommendations
- [ ] **REL-04**: S&P 500 tickers cached to sp500_cache.json with 24h TTL; used on Wikipedia fetch failure
- [ ] **REL-05**: Startup warning logged (and Discord message sent) when DRY_RUN=false AND PAPER_TRADING=false
- [ ] **REL-06**: bot.send_recommendation uses fetch_channel instead of get_channel
- [ ] **REL-07**: place_order distinguishes dry-run None (intentional) from order failure (raises exception)
- [ ] **REL-08**: Startup channel validation in on_ready (fetch_channel, fail fast with clear error)

### Documentation

- [ ] **DOC-01**: .env.example created with all 14+ variables, placeholder values, and inline comments
- [ ] **DOC-02**: Docstrings added to run_scan, main entry point, on_ready, configure_scheduler
- [ ] **DOC-03**: schwab_client/auth.py documents OAuth first-run flow (port, timeout, callback_url requirement)
- [ ] **DOC-04**: passes_technical_filter docstring explains volume multiplier and RSI thresholds
- [ ] **DOC-05**: All public functions across screener/, analyst/, database/, discord_bot/, schwab_client/ have docstrings

### Testing

- [ ] **TEST-01**: run_scan tested end-to-end with mocked yfinance, Claude, Schwab, and Discord (happy path + error paths)
- [ ] **TEST-02**: ApproveRejectView.approve tested: DB status update, dry-run branch, order placement call
- [ ] **TEST-03**: ApproveRejectView.reject tested: DB status update, Discord response
- [ ] **TEST-04**: analyze_ticker tested with mocked anthropic.Anthropic client (BUY, HOLD, SKIP, error path)
- [ ] **TEST-05**: fetch_technical_data tested with mocked yf.Ticker (happy path, insufficient data, empty response)
- [ ] **TEST-06**: fetch_fundamental_info tested with mocked yf.Ticker (happy path, missing keys, empty dict)
- [ ] **TEST-07**: ticker_recommended_today timezone boundary tested (UTC vs local date edge)
- [ ] **TEST-08**: expire_stale_recommendations tested for boundary (expires exactly at now) and already-approved records

### Position Monitoring

- [ ] **POS-01**: positions table added to DB schema (id, ticker, shares, avg_cost_usd, entry_date, status: open/closed)
- [ ] **POS-02**: On trade approval, a position record is created or existing position is updated (avg cost)
- [ ] **POS-03**: get_open_positions query returns all open positions with current P&L (uses last known price)
- [ ] **POS-04**: /positions Discord slash command displays open holdings table (ticker, shares, avg cost, current price, P&L%)
- [ ] **POS-05**: Before approving a buy, check if position size + existing exposure exceeds MAX_POSITION_SIZE_USD
- [ ] **POS-06**: Daily scan skips BUY analysis for tickers where a position already exists (prevents stacking)

### Sell Signals

- [ ] **SELL-01**: Claude sell signal prompt built from position data + recent news (SELL/HOLD signal)
- [ ] **SELL-02**: Technical exit trigger: RSI > MAX_RSI threshold on held position generates SELL candidate
- [ ] **SELL-03**: Daily scan includes sell signal evaluation pass over open positions (after buy scan)
- [ ] **SELL-04**: Sell recommendation stored in recommendations table (signal='SELL', linked to position)
- [ ] **SELL-05**: Discord embed for sell recommendation (red color, shows entry price, current price, P&L)
- [ ] **SELL-06**: Approve/Reject buttons for sell (same pattern as buy) trigger market sell order via Schwab
- [ ] **SELL-07**: build_market_sell function added to schwab_client/orders.py
- [ ] **SELL-08**: On sell approval, position status updated to 'closed', trade record created for the sell
- [ ] **SELL-09**: On sell rejection, position remains open, recommendation marked rejected

---

## v2 Requirements

### Performance

- **PERF-01**: Scan parallelized with asyncio.gather + semaphore (configurable concurrency limit)
- **PERF-02**: Claude API rate limiting with inter-call throttle or token budget tracking
- **PERF-03**: DB connection pooling for scan batch operations

### Advanced Sell Logic

- **SELL-10**: Stop-loss trigger: auto-generate SELL signal when position P&L < -STOP_LOSS_PCT threshold
- **SELL-11**: Take-profit trigger: auto-generate SELL signal when P&L > TAKE_PROFIT_PCT threshold
- **SELL-12**: Configurable stop-loss and take-profit thresholds in .env

### Operations

- **OPS-01**: Approver role check in Discord — only users with a configured role can click Approve
- **OPS-02**: HTTP health check endpoint (aiohttp, configurable port)
- **OPS-03**: Structured JSON logging for external log parsing

---

## Out of Scope

| Feature | Reason |
|---------|--------|
| Multi-user role-based approval | Single operator assumed; adds Discord permission complexity |
| Options / bonds / non-equity | Schwab parse_positions not designed for it; out of trading scope |
| PostgreSQL migration | SQLite + WAL mode sufficient for single-operator use |
| External log shipping | Local rotation sufficient for v1 |
| Mobile app / web UI | Discord IS the UI |
| Real-time price streaming | Daily scan cadence is sufficient |

---

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| REF-01 to REF-10 | Phase 1 | Pending |
| REL-01 to REL-08 | Phase 2 | Pending |
| DOC-01 to DOC-05 | Phase 3 | Pending |
| TEST-01 to TEST-08 | Phase 4 | Pending |
| POS-01 to POS-06 | Phase 5 | Pending |
| SELL-01 to SELL-09 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 42 total
- Mapped to phases: 42
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-30*
*Last updated: 2026-03-30 — initial definition*
