# Algo Trade

A stock screener that does the boring research every morning — fundamentals, news, technicals — hands the shortlist to an LLM for a BUY/HOLD/SKIP call, and posts the survivors to Discord with Approve/Reject buttons.

**Nothing trades until a human taps Approve.**

That last part is the design, not a safety afterthought. This automates the *research*, not the *decision*.

---

## What it does

Once a day (09:00 local by default), the bot:

1. **Builds a universe** — your `watchlist.txt`, plus the top 10 S&P 500 names ranked by EPS + ROE (scraped from Wikipedia, cached 24h).
2. **Screens on fundamentals** — dividend yield, P/E, earnings growth. Fails fast and cheap.
3. **Asks an analyst LLM** — feeds up to 5 recent headlines plus macro context (SPY 1m/1y trend, VIX level, 52-week range position) and asks for a BUY/HOLD/SKIP with reasoning.
4. **Screens on technicals** — RSI (Wilder's, 14-period), price vs. 50-day MA, volume ratio.
5. **Posts what survives** to Discord as an embed with Approve / Reject buttons.

Then it does a **sell pass** over everything you already own: if RSI is above threshold **and** MACD has turned bearish, it asks the analyst whether to exit and posts a red SELL embed with its own approve/reject.

You tap a button. Only then does an order go to Schwab — and only if `DRY_RUN=false`.

## What you actually see

A BUY recommendation arrives as a Discord embed:

```
┌─────────────────────────────────────────┐
│  KO — BUY                               │
│  Coca-Cola's Q3 beat on volume growth   │
│  in Latin America, and the dividend is  │
│  covered at 68% of FCF...               │
│                                         │
│  Price      Dividend Yield   P/E Ratio  │
│  $61.42     3.1%             24.8       │
│                                         │
│  Confidence      Next Earnings          │
│  High            2026-10-21             │
│                                         │
│     [ ✅ Approve ]    [ ❌ Reject ]      │
└─────────────────────────────────────────┘
```

SELL embeds are red and show entry price, current price, P&L%, shares, and RSI. ETF embeds swap the fundamental fields for RSI, MA50 trend, and expense ratio.

Buttons survive a restart — views are re-registered as persistent on startup, so a recommendation posted before a crash is still clickable after it.

## Why it's built this way

The interesting parts of this codebase are the constraints, not the features.

**Filter order is an economics decision.** The fundamental screen runs *before* the LLM call; the technical screen runs *after* it. Cheap checks first, and never pay for a technical fetch on a ticker the analyst already rejected.

**A bad parse is an outage.** The analyst falls through a chain: `primary → fallback → fallback2`. API failures trigger it (quota, rate limit, network) — but so do *parse* failures. A free-tier model that echoes the prompt template back at you is functionally identical to a 429, so it's handled identically.

**Safety is the default, not a flag you remember.** `DRY_RUN=true` and `PAPER_TRADING=true` ship on. Real money requires opting in, twice. There's also a hard portfolio ceiling (`MAX_PORTFOLIO_USD`) that blocks the Approve button rather than warning about it.

**Reconciliation reports; it never corrects.** Positions are recorded when Schwab *acknowledges* an order, not when it fills, so DB and broker can legitimately drift. `/reconcile` diffs them and posts an ops alert for a human. It has no code path that mutates positions to match the broker — guessing which side is right is exactly the kind of decision this system doesn't make on its own.

**Never block the event loop.** Discord's gateway heartbeat dies if you stall the loop, and yfinance is synchronous. Every yfinance call inside an async function is wrapped in `asyncio.to_thread`. Zero exceptions.

## Setup

Requires Python 3.11+.

```bash
git clone <repo-url> && cd "algo trade"
pip install -r requirements.txt
cp .env.example .env    # then fill it in
python main.py
```

On first run a browser opens for Schwab's OAuth2 flow; the token is cached in `schwab_token.json` and refreshed automatically after that.

You'll need three sets of credentials:

| Service | Where | Needed for |
|---|---|---|
| **Schwab** | [developer.schwab.com](https://developer.schwab.com) | App key, secret, account hash. Callback URL must match what you registered. |
| **Discord** | [discord.com/developers](https://discord.com/developers/applications) | Bot token + channel ID (enable Developer Mode → right-click channel → Copy ID). |
| **Analyst LLM** | Anthropic, Google, OpenAI, GitHub Models, or DeepSeek | At least one API key. See below. |

`Config.validate()` runs at startup and fails immediately with a named variable if anything is missing — you won't get a half-configured bot that dies at 09:00.

### Choosing an analyst provider

Simplest path is Claude:

```env
ANALYST_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...
```

Or run a free-tier primary with paid fallbacks behind it:

```env
ANALYST_PROVIDER=gemini
ANALYST_API_KEY=...
ANALYST_MODEL=gemini-3.1-flash-lite

ANALYST_FALLBACK_PROVIDER=github        # free GPT-4o-mini via GitHub Models
ANALYST_FALLBACK_API_KEY=<PAT with models:read>

ANALYST_FALLBACK2_PROVIDER=deepseek
ANALYST_FALLBACK2_API_KEY=sk-...
```

`ANALYST_DAILY_LIMIT` (default 18) caps calls per provider per day to stay inside free-tier quotas, and `ANALYST_CALL_DELAY_S` (default 12s) paces them under 5 RPM. Identical headline sets hit a SHA-256 cache and cost nothing.

## Discord commands

| Command | What it does |
|---|---|
| `/scan` | Run the stock scan now (same as the scheduled job) |
| `/scan_etf` | ETF-only scan — skips fundamentals, uses an ETF-specific prompt |
| `/positions` | Open positions with live prices and P&L |
| `/stats` | Win rate, average gain/loss across closed trades |
| `/history` | Last 20 closed trades |
| `/reconcile` | Diff DB positions against the Schwab account (report-only) |

## Configuration

Everything lives in `.env` — see `.env.example` for the annotated full list. The flags that matter most:

```env
DRY_RUN=true                # Approve logs the order instead of placing it
PAPER_TRADING=true          # use Schwab's paper endpoint
MAX_POSITION_SIZE_USD=500   # ceiling per trade
MAX_PORTFOLIO_USD=20000     # total exposure ceiling; Approve is blocked above it
```

Screener thresholds (`MIN_DIVIDEND_YIELD`, `MAX_PE_RATIO`, `MIN_EARNINGS_GROWTH`, `MAX_RSI`, `SELL_RSI_THRESHOLD`, `MIN_VOLUME_RATIO`) are all tunable without touching code.

Set `SCAN_TIMEZONE=America/New_York` if you want the schedule pinned to market hours across DST rather than to your machine's clock.

## How the code is laid out

```
main.py                     orchestration, scheduler, run_scan(), run_reconciliation()
config.py                   typed dataclass config; validate() at startup

screener/
  universe.py               watchlist + S&P 500 fetch, stock/ETF partition
  fundamentals.py           yfinance fundamentals + threshold filter
  technicals.py             RSI (Wilder's), MA50, volume filter
  exit_signals.py           two-gate sell: RSI high AND MACD bearish
  macro.py                  SPY trend, VIX level, 52-week position for prompts
  positions.py              live price + P&L per open position

analyst/
  claude_analyst.py         prompt building, provider fallback chain, parsing
  news.py                   headline fetch (yfinance, or Alpha Vantage if keyed)

discord_bot/
  bot.py                    TradingBot, slash commands, approve/reject views
  embeds.py                 embed formatting

schwab_client/
  auth.py                   OAuth2 via schwab-py
  orders.py                 order construction, position parsing
  reconcile.py              pure DB-vs-broker diff

database/
  models.py                 SQLite schema
  queries.py                CRUD, expiration, dupe checks
```

### A reading path

If you want to understand this in an hour, read in this order:

1. `main.py` → `run_scan()` — the entire system as one function
2. `screener/universe.py` — where candidates come from
3. `analyst/claude_analyst.py` → `build_prompt` / `parse_claude_response` — the LLM boundary, and the most brittle seam in the project
4. `discord_bot/bot.py` → the `approve` handler — where software stops and a person starts
5. `tests/test_run_scan.py` — the executable spec for step 1

## Tests

```bash
pytest          # 546 tests, ~35s
pytest -q
pytest tests/test_screener_technicals.py
pytest tests/test_analyst_claude.py::test_parse_buy_signal -v
```

Roughly 6,000 lines of tests against 3,200 lines of application code. That ratio is deliberate: `should_recommend()`, the prompt builders, `diff_positions()`, and every filter are pure functions specifically so they can be tested without mocking Discord or Schwab. `test_screener_technicals.py` validates the RSI math against synthetic price series rather than trusting the implementation.

Tests that reach `run_scan` must set `config.dry_run = True` so the suite never touches a live brokerage account.

## Dependencies

`requirements.txt` is a **generated lock file** — don't hand-edit it. Add direct dependencies to `requirements.in`, then:

```bash
uv pip compile requirements.in --universal --python-version 3.11 -o requirements.txt
```

## Scope and limitations

Worth being clear about what this is not:

- **No backtesting.** There's no historical simulation and no evidence the screening criteria are profitable. The thresholds are reasonable-looking defaults, not fitted parameters.
- **The LLM is one filter among several**, and it sees only headlines — not filings, not transcripts, not price history. Treat its reasoning as a prompt for your own thinking, not as analysis.
- **Market orders by default.** Fills are whatever the market gives you.
- **Single account, long-only, US equities and ETFs.** No shorts, no options, no multi-account support.
- **Positions are recorded on acknowledgement, not fill** — hence `/reconcile` existing at all.

## Disclaimer

This is personal software for managing a personal account. It is not investment advice, and it comes with no warranty. You are responsible for every order you approve. Run it with `DRY_RUN=true` for a good while and read what it suggests before you even think about changing that.
