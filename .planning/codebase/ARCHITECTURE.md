# Architecture

**Analysis Date:** 2026-03-30

## Pattern Overview

**Overall:** Event-driven pipeline with human-in-the-loop approval gate

**Key Characteristics:**
- Screener → Analyst → Discord → (human approval) → Broker pipeline
- Async Discord bot drives the event loop; APScheduler fires the daily scan from a background thread
- SQLite persistence tracks every recommendation from creation through approval/rejection/expiry
- All side-effectful operations (yfinance calls, Claude API, Schwab API) are isolated in leaf modules; orchestration lives in `main.py`

## Layers

**Configuration:**
- Purpose: Load and validate all credentials and thresholds at startup
- Location: `config.py`
- Contains: `Config` dataclass with typed fields, `validate()` fast-fail method
- Depends on: `python-dotenv`, `os.getenv`
- Used by: Every module that needs a threshold or credential

**Screener:**
- Purpose: Build the ticker universe and apply pre-analyst filters
- Location: `screener/`
- Contains: `universe.py` (ticker list), `fundamentals.py` (P/E, yield, growth filter), `technicals.py` (RSI, MA50, volume filter)
- Depends on: `yfinance`, `pandas`, `config.py`
- Used by: `main.py` (`run_scan`)

**Analyst:**
- Purpose: Fetch news and call the Claude API for a BUY/HOLD/SKIP signal
- Location: `analyst/`
- Contains: `news.py` (headline fetch), `claude_analyst.py` (prompt build, API call, response parse)
- Depends on: `yfinance` (news), `anthropic` SDK, `config.py`
- Used by: `main.py` (`run_scan`)

**Database:**
- Purpose: Persist recommendations and trades; provide dupe-check and expiry
- Location: `database/`
- Contains: `models.py` (schema + connection helper), `queries.py` (all CRUD)
- Depends on: `sqlite3` (stdlib only)
- Used by: `main.py`, `discord_bot/bot.py`

**Discord Bot:**
- Purpose: Post recommendation embeds with interactive Approve/Reject buttons; expose `/scan` slash command
- Location: `discord_bot/`
- Contains: `bot.py` (`TradingBot` client, `ApproveRejectView`), `embeds.py` (embed builder)
- Depends on: `discord.py`, `config.py`, `database/queries.py`, `discord_bot/embeds.py`
- Used by: `main.py`

**Schwab Client:**
- Purpose: OAuth2 auth and market order placement
- Location: `schwab_client/`
- Contains: `auth.py` (OAuth2 token management), `orders.py` (order build, place, position fetch)
- Depends on: `schwab-py` SDK
- Used by: `discord_bot/bot.py` (conditionally, only when `dry_run=False`)

**Orchestration:**
- Purpose: Wire all layers together; own the scan loop and scheduler setup
- Location: `main.py`
- Contains: `run_scan()`, `should_recommend()`, `configure_scheduler()`, `main()`
- Depends on: All layers above
- Used by: Process entry point only

## Data Flow

**Daily Scan Pipeline:**

1. APScheduler fires `run_scan(bot, config)` at `SCAN_HOUR:SCAN_MINUTE` (default 09:00)
2. `expire_stale_recommendations(config.db_path)` — marks `pending` records past `expires_at` as `expired`
3. `get_universe(watchlist_path, sp500)` — merges `watchlist.txt` with S&P 500 tickers from Wikipedia, deduplicated, watchlist-first
4. For each ticker in universe:
   a. `ticker_recommended_today()` — skip if already recommended today (status not `expired`/`rejected`)
   b. `fetch_fundamental_info(ticker)` → yfinance `.info` dict
   c. `passes_fundamental_filter(info, config)` — checks P/E ≤ `max_pe_ratio`, yield ≥ `min_dividend_yield`, earnings growth ≥ `min_earnings_growth`; skips ticker if any fail
   d. `fetch_news_headlines(ticker)` — up to 5 headlines from yfinance `.news`
   e. `analyze_ticker(ticker, info, headlines, config)` — builds structured prompt, calls `claude-opus-4-6`, parses `SIGNAL: BUY|HOLD|SKIP` + `REASONING:` response
   f. `fetch_technical_data(ticker)` — 3-month OHLCV history; computes RSI (Wilder's, 14-period), MA50, last volume, 20-day avg volume; returns `None` values if < 51 bars
   g. `should_recommend(signal, tech_data, config)` — returns `True` only if `signal == "BUY"` AND all technical checks pass
   h. `create_recommendation(...)` — writes row to `recommendations` table with `status='pending'`
   i. `bot.send_recommendation(...)` — posts Discord embed (green BUY color) with `ApproveRejectView` buttons
   j. `set_discord_message_id(...)` — back-fills `discord_message_id` on the recommendation row

**Approval Flow:**

1. Human clicks "Approve" on Discord embed
2. `compute_share_quantity(price, max_position_usd)` — whole shares, floor division
3. If `dry_run=False`: `place_order(ticker, shares, config)` → Schwab API market buy
4. `create_trade(...)` — writes row to `trades` table linked by `recommendation_id`
5. `update_recommendation_status(..., "approved")` — closes the recommendation

**Rejection Flow:**

1. Human clicks "Reject"
2. `update_recommendation_status(..., "rejected")`

**Manual Scan:**

1. User issues `/scan` slash command in Discord
2. `_scan_command` dispatches `"manual_scan"` event
3. `on_manual_scan` handler calls `run_scan(bot, config)` directly

## Key Abstractions

**Config dataclass:**
- Purpose: Single source of truth for all thresholds and credentials
- Location: `config.py`
- Pattern: Immutable dataclass instantiated once as module-level `config` singleton; passed explicitly to every function that needs it (no global access except at `main.py` import time)

**should_recommend() pure function:**
- Purpose: Combines Claude signal + technical filter into a single yes/no decision; enables unit testing without network calls
- Location: `main.py` lines 26–30
- Pattern: Takes pre-fetched data dicts, returns bool — no I/O

**passes_fundamental_filter() / passes_technical_filter() pure functions:**
- Purpose: Threshold comparisons against `Config`; no side effects
- Locations: `screener/fundamentals.py` lines 4–25, `screener/technicals.py` lines 35–57
- Pattern: Accept plain dicts + `Config`, return bool

**build_prompt() / parse_claude_response() pure functions:**
- Purpose: Prompt assembly and response parsing isolated from the API call
- Location: `analyst/claude_analyst.py` lines 7–60
- Pattern: Pure string → string and string → dict transforms; independently testable

**ApproveRejectView:**
- Purpose: Discord UI component that carries recommendation state and executes the approval path
- Location: `discord_bot/bot.py` lines 22–65
- Pattern: `discord.ui.View` subclass with `timeout=None` (persistent until interacted)

## Entry Points

**Process start:**
- Location: `main.py:main()`
- Triggers: `python main.py`
- Responsibilities: `Config.validate()`, `initialize_db()`, construct `TradingBot`, register `on_ready`/`on_manual_scan` handlers, call `bot.run()`

**on_ready event:**
- Location: `main.py` lines 123–133 (inner function)
- Triggers: Discord connection established
- Responsibilities: Register and start APScheduler with `configure_scheduler()`

**Scheduled scan:**
- Location: `main.py:run_scan()` (async)
- Triggers: APScheduler CronTrigger at configured hour:minute; `asyncio.run_coroutine_threadsafe` bridges the background scheduler thread to the bot's async event loop

## Scheduling Model

APScheduler `BackgroundScheduler` runs in a daemon thread alongside the `asyncio` event loop owned by `discord.Client`. The scheduler is only started after `on_ready` fires (guaranteeing the bot's loop exists). The bridge pattern used:

```python
asyncio.run_coroutine_threadsafe(
    run_scan(bot, config), bot.loop
).result()
```

This blocks the scheduler thread until the scan completes, preventing overlapping scan runs. The job is registered with `id="daily_scan"` and `replace_existing=True` to allow re-registration without duplication.

## Error Handling

**Strategy:** Per-ticker exception isolation; scan loop continues on any single-ticker failure

**Patterns:**
- `run_scan` wraps each ticker in `try/except Exception` → `logger.error(...)` → `continue`
- `get_sp500_tickers()` failure is caught at the scan level; scan proceeds with watchlist-only universe
- `fetch_technical_data` returns a dict of `None` values (not an exception) when < 51 bars available; `passes_technical_filter` treats any `None` value as a filter failure
- `parse_claude_response` raises `ValueError` on malformed API response; propagates up to the per-ticker handler
- `place_order` catches all Schwab API exceptions internally and returns `None` (logged as error)

## Cross-Cutting Concerns

**Logging:** `logging.basicConfig` at INFO level in `main()`. Every module acquires `logging.getLogger(__name__)`. No structured logging framework.

**Validation:** `Config.validate()` called once at startup. Filter functions return `False` (not raise) on missing data. Response parsing raises `ValueError` on invalid format.

**Authentication:** Schwab OAuth2 token persisted at `schwab_token.json` (project root); `schwab-py` handles refresh automatically. Token file must not be committed (in `.gitignore`).

**Dry Run / Paper Trading:** `DRY_RUN=true` (default) causes `ApproveRejectView.approve` to log instead of calling `place_order`. `PAPER_TRADING=true` (default) is passed to `schwab-py` to hit the paper trading endpoint when orders are placed.

---

*Architecture analysis: 2026-03-30*
