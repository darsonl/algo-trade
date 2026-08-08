# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (requirements.txt is a generated lock — see note below)
pip install -r requirements.txt

# Update dependencies: edit requirements.in (direct deps only), then regenerate
# the fully-pinned lock with uv. Never hand-edit requirements.txt.
#   uv pip compile requirements.in --universal --python-version 3.11 -o requirements.txt

# Run the bot (opens browser on first run for Schwab OAuth2)
python main.py

# Run all tests
pytest

# Run a single test file
pytest tests/test_screener_technicals.py

# Run tests with verbose output
pytest -v

# Run a single test by name
pytest tests/test_analyst_claude.py::test_parse_buy_signal -v
```

## Architecture

**Algo Trade** is an automated stock screener that posts Claude AI-generated BUY recommendations to Discord for human approval before executing trades via the Schwab API.

### Execution Flow

```
python main.py
  → Config.validate() (fast-fail if Schwab/Discord/Anthropic keys missing)
  → DB init (SQLite, creates tables if absent)
  → Discord bot + APScheduler start
  → Daily cron at SCAN_HOUR:SCAN_MINUTE (default 9:00 AM)
      → run_scan():
          → expire stale recommendations (>24h)
          → build universe: partition_watchlist() splits watchlist.txt into (stocks, etfs)
                            + S&P 500 from Wikipedia (top 10 by EPS+ROE, 24h cached)
          → for each ticker (skip if recommended today or has open position):
              1. yfinance fundamentals → fundamental filter (P/E, yield, growth)
              2. yfinance news headlines (5 max) → Claude API → BUY/HOLD/SKIP signal
              3. yfinance technicals → technical filter (RSI, MA50, volume)
              4. Write recommendation to DB, post Discord embed with Approve/Reject buttons
          → sell pass (after buy pass): iterate open positions
              → check_exit_signals (RSI > threshold AND MACD bearish)
              → analyze_sell_ticker → SELL/HOLD signal
              → Post red Discord embed with SellApproveRejectView
      → User clicks Approve → place Schwab market order (skipped if DRY_RUN=true)
      → /scan_etf command → run_scan_etf(): ETF-only path, skips fundamental filter,
                            uses build_etf_prompt, posts ETF recommendations
```

### Module Responsibilities

| Module | Responsibility |
|---|---|
| `config.py` | Dataclass config loaded from `.env`; `validate()` called at startup |
| `main.py` | Orchestration, scheduler setup, `should_recommend()`, `run_scan()` |
| `screener/universe.py` | Watchlist loading, S&P 500 fetch, deduplication |
| `screener/fundamentals.py` | yfinance fundamental fetch + threshold filter |
| `screener/technicals.py` | RSI (Wilder's, 14-period), MA50, volume filter |
| `screener/macro.py` | Prompt enrichment: SPY 1m/1y trend, VIX level, 52-week range position. All formatters are pure; `fetch_macro_context()` swallows failures and returns `None` values so a macro outage never blocks a scan |
| `screener/exit_signals.py` | Two-gate sell signal: RSI > sell_rsi_threshold AND MACD bearish |
| `screener/positions.py` | `get_position_summary` — live yfinance price + P&L% per open position |
| `analyst/claude_analyst.py` | Prompt building, API call (primary + fallback provider), signal parsing |
|  | Also: `build_sell_prompt`/`analyze_sell_ticker`, `build_etf_prompt`/`analyze_etf_ticker` |
| `analyst/news.py` | Fetch 5 headlines per ticker from yfinance |
| `discord_bot/bot.py` | `TradingBot` (discord.Client), slash commands, Approve/Reject buttons |
| `discord_bot/embeds.py` | Recommendation embed formatting (green/yellow/red) |
| `database/models.py` | SQLite schema: `recommendations` + `trades` tables |
| `database/queries.py` | CRUD for recommendations/trades, expiration, dupe check |
| `schwab_client/auth.py` | OAuth2 via `schwab-py`, token stored at `schwab_token.json` |
| `schwab_client/orders.py` | Market buy order construction, position parsing |
| `schwab_client/reconcile.py` | Pure DB-vs-broker position diff (`diff_positions`) + report formatting |

### Key Design Decisions

- **Two-stage filtering**: Fundamental filter runs before calling Claude (cheap check first), technical filter runs after Claude approves (avoids technical fetch on skipped tickers).
- **Dry-run by default**: `DRY_RUN=true` and `PAPER_TRADING=true` are the defaults; no orders are placed unless explicitly disabled.
- **24-hour recommendation expiry**: Stale records are expired at the start of each scan. The `should_recommend()` function in `main.py` is the single source of truth for dupe prevention.
- **Pure functions for testability**: `should_recommend()`, `configure_scheduler()`, prompt builders, and filter functions are all pure/stateless to enable unit testing without mocking the Discord client or Schwab API.
- **Analyst fallback provider**: When the primary analyst call fails, `analyze_ticker()` (and `analyze_etf_ticker`/`analyze_sell_ticker`) falls through the chain `primary → fallback → fallback2`. Configured via `ANALYST_FALLBACK_PROVIDER`/`_API_KEY`/`_MODEL` and `ANALYST_FALLBACK2_*` in `.env`. Both API-level failures (quota exhausted, rate limit, network) **and** parse errors (`ValueError` from `parse_claude_response`, e.g. a template-echo response) trigger the fallback; the failure only propagates once no further fallback client is configured.
- **asyncio.to_thread for all yfinance I/O**: Every yfinance call inside async functions (`fetch_fundamental_info`, `fetch_news_headlines`, `fetch_technical_data`, `partition_watchlist`, `get_top_sp500_by_fundamentals`) must be wrapped in `await asyncio.to_thread(...)` to prevent blocking the Discord gateway heartbeat. Zero bare synchronous yfinance calls on the event loop.
- **Two-gate sell signal**: `check_exit_signals` requires BOTH RSI above threshold AND MACD bearish (macd_line < signal_line). Either condition alone does not trigger a sell recommendation.
- **Analyst quota tracking**: `analyst_calls` table tracks daily call counts per provider. Counting happens per provider **attempt** (the `on_attempt` callback passed from `main.py` into `_run_with_fallbacks`, invoked before each provider call) — failed calls burn provider quota just like successes, so they count too. Cache hits bypass both the quota guard and the increment. Configured via `ANALYST_DAILY_LIMIT` (default 18) to respect Gemini free-tier limits.
- **Local-date day bucketing**: all "today" logic uses the machine-local calendar date — `ticker_recommended_today` and `positions.entry_date` use SQLite's `'localtime'` modifier (timestamps stay UTC in storage), and quota tracking uses Python `date.today()`. The scheduler also runs machine-local unless `SCAN_TIMEZONE` (IANA name, e.g. `America/New_York`) is set for DST-proof market alignment. Don't reintroduce bare `date('now')` day comparisons — they roll over at UTC midnight (early evening US time).
- **ETF bypass**: ETFs are partitioned out of the stock scan by `partition_watchlist()` using `yfinance quoteType`. They run through `run_scan_etf()` which skips `passes_fundamental_filter` entirely and uses `build_etf_prompt` (no earnings/P/E context).
- **sell_blocked flag**: After a rejected sell, `sell_blocked=True` prevents re-triggering the sell signal for the same position on the same day. Auto-resets when RSI drops back below threshold.
- **Position reconciliation (report-only)**: `run_reconciliation()` in `main.py` compares DB open positions against the Schwab account (RISK-05: positions are recorded on order acknowledgement, not fill). Runs before each scan's sell pass and via `/reconcile`. It NEVER mutates positions — discrepancies (phantom / untracked / mismatched) are posted as ops alerts for human correction. Skipped entirely when `DRY_RUN=true` (simulated positions have no broker counterpart). Tests that call `run_scan` must set `config.dry_run = True` (or patch `main.get_positions`) so the suite never touches the live Schwab API.
- **Config reads env at construction, not import**: `Config` fields use `field(default_factory=lambda: os.getenv(...))` (via the `_env_str/_int/_float/_bool` helpers), so `Config()` reflects the environment at call time. The old `= os.getenv(...)` defaults froze at import, forcing `importlib.reload(config)` in env-dependent tests — don't reintroduce that pattern. `test_config.py`'s reload-based USE_LIMIT_BUY tests still pass (now via construction-time reads).
- **Analyst enrichment built only on cache miss**: in `run_scan`, the `fundamental_trend` block (`fetch_eps_data` → `quarterly_income_stmt`, a slow network call) is computed inside the cache-miss branch, after `get_cached_analysis`. A cache hit skips it. The earnings block stays *before* the cache check because `earnings_date_embed` is shown on the recommendation embed even on a hit.
- **S&P 500 ranking — two-tier 24h cache**: `get_top_sp500_by_fundamentals` (~500 yfinance `.info` calls, 10-20 min) caches in-memory, then on disk at `sp500_top_cache.json` (gitignored) so a restart doesn't repay the cost. Both tiers store the **full** ranking and slice per `top_sp500_count` on read, so changing that count never invalidates the cache.
- **Channel object memoized**: `TradingBot._resolve_channel()` fetches the configured channel via `fetch_channel` once and caches it on the instance; the `send_*` methods reuse it instead of an API round-trip per post.

### Configuration

All thresholds and credentials are set via `.env` (see `.env.example`). The `Config` dataclass in `config.py` maps every variable with typed defaults. Safety-critical flags:

```
DRY_RUN=true          # When true, Discord buttons log instead of placing orders
PAPER_TRADING=true    # When true, Schwab paper trading endpoint is used
MAX_POSITION_SIZE_USD=500
```

### Database Schema

**`recommendations`**: ticker, signal, reasoning, price, dividend_yield, pe_ratio, earnings_growth, status (`pending`/`approved`/`rejected`/`expired`), discord_message_id, created_at, expires_at

**`trades`**: recommendation_id (FK), ticker, shares, price, order_id, executed_at, side (`buy`/`sell`)

**`positions`**: ticker, shares, avg_price, created_at, updated_at, sell_blocked (bool)

**`analyst_cache`**: cache_key (SHA-256 of headlines), provider, signal, reasoning, created_at

**`analyst_calls`**: PRIMARY KEY (date, provider), call_count — daily quota tracking per provider

### Technical Indicator Notes

`screener/technicals.py` calculates RSI using Wilder's smoothing (not simple EWM) and requires a minimum of 51 price data points (50-day MA + 1). Tests in `test_screener_technicals.py` use synthetic price series to validate RSI math directly.

### Discord Slash Commands

- `/scan` — manually trigger stock scan (same as scheduled daily run)
- `/scan_etf` — manually trigger ETF-only scan
- `/positions` — display open positions with live P&L embed
- `/stats` — win rate and P&L stats for closed trades
- `/history` — last 20 closed trades
- `/reconcile` — compare DB open positions against the Schwab account (report-only; skipped in DRY_RUN)

### Test Suite

546 tests as of 2026-08-08. Run with `pytest -q` (~35s). Key test files:
- `test_screener_technicals.py` — RSI math with synthetic price series
- `test_exit_signals.py` — RSI + MACD gate (16 tests, 2×2 matrix)
- `test_sell_scan.py` — run_scan sell pass integration (9 tests)
- `test_sell_buttons.py` — SellApproveRejectView async handlers (9 tests)
- `test_positions.py` — positions CRUD including weighted-avg price (11 tests)
- `test_discord_buttons.py` — ApproveRejectView handlers (10 tests)
