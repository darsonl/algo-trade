# Feature Landscape: ETF Scan Separation

**Domain:** ETF scanning and recommendation for a human-approval Discord trading bot
**Researched:** 2026-04-06
**Milestone:** v1.1 (ETF + Async)

---

## Context: What Already Exists

The stock scan pipeline is fully operational and must not be disturbed. The current
`passes_fundamental_filter` already contains a `_ETF_ALLOWLIST` set that returns `True`
for SPY/QQQ/VTI/IVV/VOO/VEA/BND/GLD/XLK/SCHD — meaning ETFs currently silently bypass the
fundamental filter and enter the stock analyst prompt. This is the existing fragility:
ETF tickers reach `build_prompt()` with fundamentals that are all `N/A` (ETFs have no
trailing P/E, no earnings growth) and receive a stock-framed analysis.

The fix is NOT allowlist expansion. The fix is a separate scan path.

---

## Table Stakes

Features that must exist for the ETF scan to be considered "done."

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| `partition_watchlist(tickers)` → `(stocks, etfs)` | ETFs and stocks must enter different pipelines; mixing causes meaningless analysis | Low | Pure function, testable without mocks. Partition by checking `yf.info["quoteType"] == "ETF"` OR a static set. Static set preferred — avoids a yfinance call just for routing. |
| ETF-specific analyst prompt (`build_etf_prompt`) | Stock prompt sends P/E, earnings growth — fields ETFs don't have. Sending N/A to the LLM wastes tokens and degrades signal quality. | Low | Prompt must include: trend vs MA50, RSI, MACD direction, expense ratio if available, recent news headlines. Framing must be "should I enter this ETF position" not "is this stock undervalued." |
| ETF technical filter (`passes_etf_filter`) | ETFs need lighter entry criteria than stocks. The stock `passes_technical_filter` requires RSI <= 70, price >= MA50, volume >= 50% avg. This is appropriate for ETFs too but the threshold values may differ (ETFs are lower volatility; tighter RSI cap is defensible). | Low | Reuse `passes_technical_filter` function directly — its checks are asset-agnostic. No new filter logic needed, only possibly different Config thresholds via new Config fields. |
| `/scan_etf` Discord slash command | Operator needs ability to trigger ETF scan independently. Same pattern as `/scan`. | Low | Mirrors existing `/scan` command wiring in `discord_bot/bot.py`. |
| ETF recommendation stored in `recommendations` table | DB schema must accept ETF recs without breaking existing stock rec queries. | Low | Additive only. Existing schema already has nullable `pe_ratio`, `earnings_growth`. Add `asset_type` column (`stock`/`etf`) defaulting to `stock` for backward compat. |
| ETF embed differentiation | Operator must immediately know whether a Discord embed is for a stock or ETF. Sending an ETF rec with "P/E: N/A" and "Earnings Growth: N/A" is noise that degrades trust. | Low | New `build_etf_recommendation_embed` function in `embeds.py` that shows: Price, Expense Ratio (if available), RSI, MACD direction, Trend (above/below MA50). Reuse same color scheme. |
| ETF universe sourcing | Must have a known list of ETFs to scan; cannot rely on S&P 500 Wikipedia fetch (that's stock-only). | Low | Hardcode `etf_watchlist.txt` (or extend `watchlist.txt` with an `[ETF]` section). Static file is sufficient for single-operator use at this scale. |
| Skip-owned guard for ETFs | Same as stocks: if an ETF position is already open, skip it in the buy scan. | Low | Reuse existing `queries.has_open_position()` — asset-type-agnostic. |

---

## Differentiators

Features that add value beyond table stakes. Not required for the scan to work, but meaningfully
improve operator experience or signal quality.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Expense ratio in ETF prompt and embed | ETFs with high expense ratios (e.g., >0.75%) are structurally worse long-term holdings. Surfacing this helps the operator make better approval decisions. | Low | `yf.Ticker(t).info` often returns `annualReportExpenseRatio`. Pass to prompt as `Expense Ratio: {val}` or `N/A`. No extra API call needed. |
| Trend label in embed ("Above MA50" / "Below MA50") | More scannable than raw numbers. Operator sees at a glance whether the ETF is in an uptrend. | Low | Already computed in `fetch_technical_data`; just format it differently in the ETF embed. |
| Separate ETF ops alert | If ETF scan produces 0 recs, the existing ops alert fires. A dedicated `[ETF]` prefix in the alert message helps the operator distinguish ETF scan silence from stock scan silence. | Low | One-line change in `run_scan_etf` equivalent of the 0-rec warning path. |
| `run_scan_etf` as a standalone coroutine (not bolt-on to `run_scan`) | Keeps the stock scan loop clean and makes each path independently testable. The existing `run_scan` is already 130+ lines; entangling ETF logic inside it would make both harder to maintain. | Medium | New `run_scan_etf(bot, config)` coroutine in `main.py`. Shares `create_analyst_client`, `create_fallback_client`, quota guard, and `asyncio.to_thread` wrappers with `run_scan` but has its own loop. |
| Scheduled ETF scan at a different time | ETFs are macro instruments. Running the ETF scan slightly after the stock scan (e.g., 9:15 AM vs 9:00 AM) gives the operator a cleaner approval queue rather than interleaving stock and ETF embeds. | Low | Config field `ETF_SCAN_TIMES` defaulting to `SCAN_TIMES` value. One additional scheduler job registration in `configure_scheduler`. |

---

## Anti-Features

Features to explicitly NOT build.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| ETF fundamental filter (P/E, dividend yield, earnings growth) | ETFs don't have earnings growth. Dividend yield exists for some (SCHD, BND) but is not a valid entry signal for broad-market ETFs (SPY, QQQ). Applying the stock fundamental filter to ETFs is the exact bug being fixed. | Skip fundamental filter entirely for ETFs. The technical filter (RSI, MA50, volume) is the entry gate. |
| Dynamic ETF universe from an external API | Calling an ETF screener API introduces a new external dependency, a new failure mode, and complexity that isn't needed for a single-operator bot with a fixed handful of ETFs. | Keep a static `etf_watchlist.txt` the operator controls. |
| Separate DB tables for ETF recommendations | ETF recs and stock recs have the same lifecycle (pending → approved/rejected → executed → expired). Two tables would require duplicating all CRUD queries. | Add `asset_type` column to existing `recommendations` table. Additive, backward-compatible. |
| ETF-specific sell pipeline | The existing RSI+MACD sell gate + Claude SELL analysis works for ETFs too. ETFs trend more smoothly than individual stocks, making RSI/MACD a reasonable exit signal. | Reuse the existing sell pipeline. The sell prompt already accepts N/A fundamentals gracefully. |
| Removing `_ETF_ALLOWLIST` from `fundamentals.py` | The allowlist is the current safety valve; removing it before the new partition logic is in place would cause ETF tickers to fail the fundamental filter and disappear from the scan entirely. | Keep the allowlist until `partition_watchlist` is wired end-to-end and tested. Remove in the same commit that activates the new path. |
| Merging `/scan` and `/scan_etf` into a single command with a flag | `--type etf` flags add parsing complexity and make the Discord command harder to use. The operator just wants to hit a button. | Two distinct slash commands: `/scan` for stocks, `/scan_etf` for ETFs. |
| LLM-based ETF classification | Asking the analyst "is this ticker an ETF?" wastes tokens and is unreliable. Classification must be deterministic. | Static list or `yf.info["quoteType"]` check. |

---

## Feature Dependencies

```
partition_watchlist
  → requires: etf_watchlist.txt (or extended watchlist.txt format)
  → blocks: run_scan_etf, /scan_etf command

build_etf_prompt
  → requires: fetch_technical_data (already exists)
  → blocks: run_scan_etf

passes_etf_filter
  → can reuse: passes_technical_filter (no new code needed)
  → blocks: run_scan_etf

run_scan_etf (coroutine)
  → requires: partition_watchlist, build_etf_prompt, passes_etf_filter
  → requires: create_analyst_client, create_fallback_client (already exist)
  → requires: asyncio.to_thread wrappers (Phase 8 wraps these; ETF scan benefits automatically)
  → blocks: /scan_etf command wiring, scheduler registration

build_etf_recommendation_embed
  → requires: asset_type column in recommendations table
  → blocks: /scan_etf command displaying correct embed

DB schema migration (asset_type column)
  → blocks: storing ETF recs, querying by type

/scan_etf Discord command
  → requires: run_scan_etf, build_etf_recommendation_embed
  → is the final integration point

_ETF_ALLOWLIST removal from fundamentals.py
  → requires: partition_watchlist active end-to-end
  → is the last cleanup step
```

---

## MVP Recommendation

Build in this order for the minimal working ETF scan:

1. `partition_watchlist(tickers) -> (stocks, etfs)` — pure function, tested independently
2. `build_etf_prompt(ticker, tech_data, headlines)` — pure function, mirrors `build_prompt`
3. DB migration: add `asset_type TEXT NOT NULL DEFAULT 'stock'` to `recommendations`
4. `build_etf_recommendation_embed(...)` — new function in `embeds.py`
5. `run_scan_etf(bot, config)` — new coroutine in `main.py`, mirrors `run_scan` structure
6. `/scan_etf` slash command in `discord_bot/bot.py`
7. Remove `_ETF_ALLOWLIST` from `fundamentals.py` once path is verified

Defer: separate ETF scan schedule (`ETF_SCAN_TIMES`) — the operator can always run `/scan_etf` manually. Scheduled ETF scan is a one-liner addition after the core path works.

---

## ETF Analyst Prompt Design

The ETF prompt must NOT include P/E, earnings growth, or dividend yield as primary signals.
It SHOULD include:

- Ticker and ETF name/category if available (e.g., "SPY — S&P 500 index ETF")
- Trend context: whether price is above or below MA50, by what percentage
- Momentum: RSI value and whether it's approaching overbought (>65 territory is worth noting for ETFs)
- MACD direction: bullish or bearish crossover
- Expense ratio: if available, note it; >0.50% is worth flagging
- Recent news headlines: macro events, Fed decisions, sector rotations matter more for ETFs than for individual stocks

The analyst signal should remain `BUY | HOLD | SKIP` — no new signal types. The `parse_claude_response`
function is unchanged. The only new piece is `build_etf_prompt` that frames the question differently.

---

## Existing Code Reuse Map

| New ETF feature | Existing component reused as-is | Notes |
|---|---|---|
| ETF technical gate | `passes_technical_filter` | No changes needed |
| ETF price/MA50/RSI/MACD data | `fetch_technical_data` | No changes needed |
| ETF news headlines | `fetch_news_headlines` | No changes needed |
| ETF analyst call + fallback | `analyze_ticker` | Pass ETF prompt via `build_etf_prompt` |
| ETF analyst cache | `queries.get_cached_analysis` / `set_cached_analysis` | Cache key is still SHA-256 of headlines |
| ETF quota guard | Same quota check in `run_scan` | Copy block into `run_scan_etf` |
| ETF skip-owned guard | `queries.has_open_position` | No changes needed |
| ETF 24h dedup | `queries.ticker_recommended_today` | No changes needed |
| ETF Schwab order | `schwab_client/orders.py` | ETF buys are market orders like stocks |
| ETF sell pipeline | Entire existing sell loop in `run_scan` | ETFs enter sell evaluation the same way |

---

## Phase-Specific Complexity Notes

| Area | Complexity | Reason |
|------|------------|--------|
| `partition_watchlist` | Low | Pure function, two lines of set-difference logic |
| `build_etf_prompt` | Low | Copy of `build_prompt` with different field set |
| `passes_etf_filter` | Low | Reuse of existing function, no new code |
| DB migration | Low | Single `ALTER TABLE` statement, additive |
| `build_etf_recommendation_embed` | Low | Copy of `build_recommendation_embed` with different fields |
| `run_scan_etf` | Medium | Must be structured like `run_scan` but ETF-aware; quota guard and async wrappers must be copied correctly |
| `/scan_etf` command | Low | Direct mirror of existing `/scan` command wiring |
| Scheduler integration | Low | One additional `scheduler.add_job` call |
| Test coverage | Medium | `run_scan_etf` needs the same test patterns as `run_scan` — mock yfinance, mock analyst, mock Discord |

---

## Sources

- Codebase analysis: `screener/fundamentals.py` (confirmed `_ETF_ALLOWLIST` pattern)
- Codebase analysis: `analyst/claude_analyst.py` (confirmed `build_prompt` structure)
- Codebase analysis: `screener/technicals.py` (confirmed `passes_technical_filter` is asset-agnostic)
- Codebase analysis: `discord_bot/embeds.py` (confirmed embed structure)
- Codebase analysis: `main.py` (confirmed `run_scan` coroutine structure)
- Codebase analysis: `config.py` (confirmed Config dataclass fields)
- Codebase analysis: `.planning/PROJECT.md` (confirmed v1.1 milestone requirements)
- Confidence: HIGH — all findings are grounded in direct code reading, not external research
