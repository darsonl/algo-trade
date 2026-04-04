# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

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
          → build universe: watchlist.txt + S&P 500 from Wikipedia
          → for each ticker (skip if recommended today):
              1. yfinance fundamentals → fundamental filter (P/E, yield, growth)
              2. yfinance news headlines (5 max) → Claude API → BUY/HOLD/SKIP signal
              3. yfinance technicals → technical filter (RSI, MA50, volume)
              4. Write recommendation to DB, post Discord embed with Approve/Reject buttons
      → User clicks Approve → place Schwab market order (skipped if DRY_RUN=true)
```

### Module Responsibilities

| Module | Responsibility |
|---|---|
| `config.py` | Dataclass config loaded from `.env`; `validate()` called at startup |
| `main.py` | Orchestration, scheduler setup, `should_recommend()`, `run_scan()` |
| `screener/universe.py` | Watchlist loading, S&P 500 fetch, deduplication |
| `screener/fundamentals.py` | yfinance fundamental fetch + threshold filter |
| `screener/technicals.py` | RSI (Wilder's, 14-period), MA50, volume filter |
| `analyst/claude_analyst.py` | Prompt building, API call (primary + fallback provider), signal parsing |
| `analyst/news.py` | Fetch 5 headlines per ticker from yfinance |
| `discord_bot/bot.py` | `TradingBot` (discord.Client), slash commands, Approve/Reject buttons |
| `discord_bot/embeds.py` | Recommendation embed formatting (green/yellow/red) |
| `database/models.py` | SQLite schema: `recommendations` + `trades` tables |
| `database/queries.py` | CRUD for recommendations/trades, expiration, dupe check |
| `schwab_client/auth.py` | OAuth2 via `schwab-py`, token stored at `schwab_token.json` |
| `schwab_client/orders.py` | Market buy order construction, position parsing |

### Key Design Decisions

- **Two-stage filtering**: Fundamental filter runs before calling Claude (cheap check first), technical filter runs after Claude approves (avoids technical fetch on skipped tickers).
- **Dry-run by default**: `DRY_RUN=true` and `PAPER_TRADING=true` are the defaults; no orders are placed unless explicitly disabled.
- **24-hour recommendation expiry**: Stale records are expired at the start of each scan. The `should_recommend()` function in `main.py` is the single source of truth for dupe prevention.
- **Pure functions for testability**: `should_recommend()`, `configure_scheduler()`, prompt builders, and filter functions are all pure/stateless to enable unit testing without mocking the Discord client or Schwab API.
- **Analyst fallback provider**: When the primary analyst API call fails (quota exhausted, rate limit, network), `analyze_ticker()` automatically retries with a configurable fallback provider/model. Configured via `ANALYST_FALLBACK_PROVIDER`, `ANALYST_FALLBACK_API_KEY`, `ANALYST_FALLBACK_MODEL` in `.env`. Parse errors do not trigger the fallback — only API-level failures do.

### Configuration

All thresholds and credentials are set via `.env` (see `.env.example`). The `Config` dataclass in `config.py` maps every variable with typed defaults. Safety-critical flags:

```
DRY_RUN=true          # When true, Discord buttons log instead of placing orders
PAPER_TRADING=true    # When true, Schwab paper trading endpoint is used
MAX_POSITION_SIZE_USD=500
```

### Database Schema

**`recommendations`**: ticker, signal, reasoning, price, dividend_yield, pe_ratio, earnings_growth, status (`pending`/`approved`/`rejected`/`expired`), discord_message_id, created_at, expires_at

**`trades`**: recommendation_id (FK), ticker, shares, price, order_id, executed_at

### Technical Indicator Notes

`screener/technicals.py` calculates RSI using Wilder's smoothing (not simple EWM) and requires a minimum of 51 price data points (50-day MA + 1). Tests in `test_screener_technicals.py` use synthetic price series to validate RSI math directly.
