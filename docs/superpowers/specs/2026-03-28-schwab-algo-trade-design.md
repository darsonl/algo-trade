# Schwab Algo Trading Bot — Design Spec
**Date:** 2026-03-28
**Status:** Approved

---

## Context

The user wants to maximize passive income from their Charles Schwab account using a semi-automated trading bot. The bot will identify investment opportunities using a 3-layer signal pipeline (fundamentals → AI/sentiment → technicals), surface recommendations via Discord for manual approval, then execute approved trades through the Schwab API.

---

## Goals

- Generate passive income through dividend stocks + algorithmically selected growth positions
- Keep the user in control — no trade executes without explicit Discord approval
- Beginner-friendly codebase: one command to start, clean module separation, no containers
- Support paper trading first before touching real funds

---

## Signal Pipeline (Priority Order)

1. **Fundamental filter (always first)** — P/E ratio, dividend yield, earnings growth rate, payout ratio thresholds
2. **AI/sentiment analysis (second)** — Claude API receives ticker + financials + recent news headlines, returns BUY / HOLD / SKIP + 2-3 sentence reasoning
3. **Technical confirmation (third)** — RSI not overbought, price above 50-day MA, volume check

A stock must pass all three layers to become a recommendation.

---

## Architecture

```
main.py (scheduler + orchestrator)
├── screener/         — universe selection, fundamental + technical filters
├── analyst/          — news fetching, Claude API integration
├── discord_bot/      — recommendation embeds, Approve/Reject buttons
├── schwab_client/    — OAuth2 auth, order placement
└── database/         — SQLite, trade history, recommendation tracking
```

---

## Project Structure

```
algo trade/
├── main.py
├── config.py
├── .env                     # API keys — never committed
├── requirements.txt
├── screener/
│   ├── universe.py          # S&P 500 + custom watchlist
│   ├── fundamentals.py      # P/E, dividend yield, earnings growth filters
│   └── technicals.py        # RSI, moving averages, volume
├── analyst/
│   ├── news.py              # fetches recent headlines per ticker
│   └── claude_analyst.py    # Claude API signal + reasoning
├── discord_bot/
│   ├── bot.py               # Discord client, slash commands
│   └── embeds.py            # recommendation card formatting
├── schwab_client/
│   ├── auth.py              # OAuth2 flow
│   └── orders.py            # place orders, get positions
└── database/
    ├── models.py            # SQLite schema
    └── queries.py           # read/write helpers
```

---

## Data Flow

```
1. Scheduler triggers scan (9:00 AM daily, configurable)
2. Screener fetches stock universe (S&P 500 + watchlist)
3. Fundamental filter — drops non-qualifying stocks
4. Claude AI analysis — signal + reasoning per candidate
5. Technical confirmation — final pass/fail gate
6. Discord embed — ticker, price, signal, reasoning, dividend yield
   → ✅ Approve  ❌ Reject buttons
7. On Approve → Schwab places order → logged to SQLite
```

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Schwab / Claude API down | Skip scan, log error, notify Discord |
| Missing fundamental data | Filter stock out silently |
| Schwab order rejected | Discord alert with rejection reason |
| Duplicate ticker same day | Deduplicated via DB check |
| Recommendation not acted on in 24h | Auto-expires, marked stale in DB |
| API keys accidentally committed | `.gitignore` prevents this from day one |

---

## Key Dependencies

| Library | Purpose |
|---|---|
| `schwab-py` | Official Schwab API Python client |
| `anthropic` | Claude API SDK |
| `discord.py` | Discord bot framework |
| `yfinance` | Stock fundamentals + news headlines |
| `pandas` | Data manipulation for screening |
| `apscheduler` | Job scheduling |
| `sqlite3` | Built-in Python, no install needed |
| `python-dotenv` | Load `.env` secrets |

---

## Verification Plan

1. **Paper trading first** — connect to Schwab paper trading account, not live funds
2. **`DRY_RUN=true` flag** — skips actual order placement, exercises all other logic
3. **`/scan now` Discord command** — manual trigger for on-demand testing
4. **1-week paper trading review** — inspect SQLite DB to confirm full cycle is recorded correctly before going live

---

## Out of Scope (v1)

- Options / covered calls
- Portfolio rebalancing
- Mobile app / web dashboard
- Multi-account support
- Backtesting engine
