# External Integrations

**Analysis Date:** 2026-03-30

## APIs & External Services

**Brokerage:**
- Charles Schwab API - Places market buy orders and reads account positions
  - SDK/Client: `schwab-py` 1.4.0
  - Auth: OAuth2 — see "Authentication & Identity" section below
  - Endpoint selection: `PAPER_TRADING=true` uses Schwab paper trading endpoint; `false` uses live endpoint
  - Used in: `schwab_client/auth.py`, `schwab_client/orders.py`

**AI / LLM:**
- Anthropic Claude API - Generates BUY/HOLD/SKIP signals with reasoning for each ticker
  - SDK/Client: `anthropic` 0.40.0
  - Auth: API key via `ANTHROPIC_API_KEY` env var
  - Model: `claude-opus-4-6`
  - Max tokens per call: `256`
  - Used in: `analyst/claude_analyst.py` — `analyze_ticker()` calls `client.messages.create()`
  - Called once per ticker that passes the fundamental filter during a scan

**Chat / Human Approval:**
- Discord API - Delivers trade recommendations as embeds with Approve/Reject buttons; receives human approval before order placement
  - SDK/Client: `discord.py` 2.4.0
  - Auth: Bot token via `DISCORD_TOKEN` env var
  - Gateway intents: `discord.Intents.default()` (no privileged intents required)
  - Slash commands: `/scan` (triggers immediate scan)
  - Used in: `discord_bot/bot.py`, `discord_bot/embeds.py`
  - Slash command tree synced to Discord at bot startup in `TradingBot.setup_hook()`

**Market Data:**
- Yahoo Finance (via yfinance) - Provides stock fundamentals, price history, and news headlines; no authentication required
  - SDK/Client: `yfinance` 0.2.51
  - Auth: None (public, unauthenticated)
  - Used in:
    - `screener/fundamentals.py` — `yf.Ticker(ticker).info` for P/E, dividend yield, earnings growth
    - `screener/technicals.py` — `yf.Ticker(ticker).history(period="3mo")` for RSI, MA50, volume
    - `analyst/news.py` — `yf.Ticker(ticker).news` for up to 5 recent headlines
  - S&P 500 ticker list fetched from Wikipedia HTML table (`screener/universe.py`) — not via yfinance

## Data Storage

**Databases:**
- SQLite (embedded, no server)
  - Connection: `DB_PATH` env var (defaults to `algo_trade.db` at project root)
  - Client: stdlib `sqlite3` — raw SQL, no ORM
  - Schema managed in: `database/models.py` — `initialize_db()` creates tables if absent
  - Queries in: `database/queries.py`

**File Storage:**
- `schwab_token.json` at project root — Schwab OAuth2 token persisted to disk by `schwab-py`
- `watchlist.txt` at project root — user-managed list of ticker symbols, one per line

**Caching:**
- None — every scan fetches fresh data from all external sources

## Authentication & Identity

**Schwab OAuth2:**
- Implementation: `schwab.auth.easy_client()` called in `schwab_client/auth.py`
- First run: opens a browser window for the user to complete OAuth2 authorization; writes token to `schwab_token.json`
- Subsequent runs: loads token from `schwab_token.json` and refreshes automatically via `schwab-py`
- Required config: `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`, `SCHWAB_CALLBACK_URL` (default `https://127.0.0.1`), `SCHWAB_ACCOUNT_HASH`

**Discord Bot Token:**
- Implementation: `bot.run(config.discord_token)` in `main.py`
- Required config: `DISCORD_TOKEN`
- Bot must be added to the target server with permissions to read/send messages in the configured channel

**Anthropic API Key:**
- Implementation: `anthropic.Anthropic(api_key=config.anthropic_api_key)` in `analyst/claude_analyst.py`
- Required config: `ANTHROPIC_API_KEY`

## Monitoring & Observability

**Error Tracking:**
- None — no Sentry or similar

**Logs:**
- stdlib `logging` at `INFO` level, formatted with timestamp, level, and logger name
- Configured in `main.py` `main()` via `logging.basicConfig()`
- Per-module loggers used in: `main.py`, `schwab_client/orders.py`, `discord_bot/bot.py`
- Errors during per-ticker processing are caught and logged; scan continues to next ticker

## CI/CD & Deployment

**Hosting:**
- Local machine — designed as a long-running process started with `python main.py`
- No Dockerfile, `docker-compose.yml`, or cloud deployment config detected

**CI Pipeline:**
- None detected

## Data Flow Between Integrations

**Daily scan flow (triggered by APScheduler at `SCAN_HOUR:SCAN_MINUTE`):**

```
APScheduler cron
  → screener/universe.py: reads watchlist.txt + fetches S&P 500 from Wikipedia HTML
  → screener/fundamentals.py: yfinance → P/E, dividend yield, earnings growth
  → [fundamental filter: skip ticker if thresholds not met]
  → analyst/news.py: yfinance → up to 5 news headlines
  → analyst/claude_analyst.py: Anthropic Claude API → BUY/HOLD/SKIP signal + reasoning
  → screener/technicals.py: yfinance → RSI, MA50, volume
  → [technical filter: skip if RSI >= MAX_RSI or MA50/volume fails]
  → database/queries.py: write recommendation to SQLite
  → discord_bot/bot.py: send embed + Approve/Reject buttons to Discord channel
```

**User approval flow (triggered by Discord button click):**

```
User clicks Approve button in Discord
  → discord_bot/bot.py: ApproveRejectView.approve()
  → [if DRY_RUN=false] schwab_client/orders.py: place_order() → Schwab API market buy
  → database/queries.py: create_trade() + update_recommendation_status("approved")
  → Discord interaction response with confirmation message
```

## Webhooks & Callbacks

**Incoming:**
- `SCHWAB_CALLBACK_URL` (default `https://127.0.0.1`) — used only during the one-time OAuth2 authorization flow; Schwab redirects the browser to this URL after login, and `schwab-py` captures the authorization code from it

**Outgoing:**
- None — all integrations are initiated by the bot (pull model), not by external webhooks

---

*Integration audit: 2026-03-30*
