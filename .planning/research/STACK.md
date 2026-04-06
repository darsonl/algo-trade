# Technology Stack

**Project:** Algo Trade Bot — v1.1 ETF + Async
**Researched:** 2026-04-06
**Scope:** Additions and changes needed for ETF scan separation and asyncio event loop hardening only. Existing validated stack is not re-litigated.

---

## Existing Stack (Unchanged)

No version changes to existing dependencies. The v1.0 stack is fully adequate for both new features.

| Technology | Pinned Version | Role |
|------------|---------------|------|
| Python | 3.9+ (implicit) | Runtime — asyncio.to_thread requires 3.9 |
| discord.py | 2.4.0 | Bot client, slash commands, event loop |
| yfinance | 0.2.51 | Market data, quoteType ETF detection |
| anthropic / openai | 0.40.0 / >=1.0.0 | LLM analyst clients |
| schwab-py | 1.4.0 | Brokerage order execution |
| APScheduler | 3.10.4 | Cron-based scan scheduling |
| tenacity | >=8.0.0 | Retry decorator on all external I/O |
| pandas | 2.2.3 | OHLCV processing, indicator math |
| SQLite (stdlib) | — | Recommendations, trades, positions |

**No new packages are required for v1.1.**

---

## New Capabilities and Their Implementation Approach

### 1. ETF Type Detection

**Recommendation:** Use `yfinance Ticker.info["quoteType"]` directly.

| Field | Value for ETF | Value for Stock | Confidence |
|-------|--------------|-----------------|------------|
| `info["quoteType"]` | `"ETF"` | `"EQUITY"` | HIGH |

**Why this is sufficient:**
yfinance 0.2.x surfaces `quoteType` from Yahoo Finance's internal security classification. It returns `"ETF"` for all major ETFs (SPY, QQQ, VTI, IVV, VOO, SCHD, GLD, etc.) and `"EQUITY"` for common stocks. This is populated on the same `.info` dict already fetched by `fetch_fundamental_info`, so ETF classification costs zero extra network calls — the info dict is already in memory when `passes_fundamental_filter` is called.

The existing `_ETF_ALLOWLIST` in `fundamentals.py` was a stopgap that hardcodes specific tickers to bypass the fundamental filter. It has two known failure modes: tickers not in the allowlist still fail the filter, and the list requires manual maintenance. `quoteType` is the correct fix.

**Reliability caveat (MEDIUM confidence):** yfinance is an unofficial scraper. The `quoteType` field has been consistently present across 0.2.x releases but Yahoo Finance can change their response schema. The mitigation is a `.get("quoteType", "")` with a fallback to `False` (treat unknown type as non-ETF), which is already the conservative default for the existing codebase.

**Alternatives considered and rejected:**

| Alternative | Why Rejected |
|-------------|-------------|
| Maintain `_ETF_ALLOWLIST` | Manual, incomplete, breaks on any ETF not in list |
| `info["fundFamily"]` | Only present on mutual funds and some ETFs, not reliable for ETFs universally |
| Separate API (e.g. OpenFIGI, IEX Cloud) | Adds a new dependency and API key for a classification already in the existing yfinance response |
| `info["typeDisp"]` | Also present (`"ETF"` / `"Equity"`) but less commonly documented than `quoteType` |

**Implementation:** A `partition_watchlist(tickers, config)` function in `screener/universe.py` (or a new `screener/etf.py`) that iterates the universe, calls `yf.Ticker(t).info.get("quoteType")`, and splits into `stocks: list[str]` and `etfs: list[str]`. Since this is a blocking yfinance call, it must be wrapped in `asyncio.to_thread` when called from the Discord event loop.

---

### 2. ETF-Specific Analyst Prompt

**Recommendation:** Add `build_etf_prompt(ticker, info, headlines)` to the existing `analyst/claude_analyst.py`.

No new libraries. No new LLM providers. The existing `_call_api`, `parse_claude_response`, and fallback pipeline are reused without modification.

**Why a separate prompt function:**
The stock `build_prompt` injects `trailingPE`, `dividendYield`, and `earningsGrowth` — fields that are `None` for ETFs (hence the original allowlist hack). An ETF prompt should instead inject trend/momentum signals: AUM, expense ratio (`annualReportExpenseRatio`), 52-week return (`fiftyTwoWeekHighChange`), and category. Sending the stock prompt for an ETF confuses the LLM with `N/A` fundamental data, producing low-quality reasoning.

**Fields available in yfinance `info` for ETFs (MEDIUM confidence, training data):**

| Field | ETF Meaning |
|-------|------------|
| `info["totalAssets"]` | AUM in USD |
| `info["annualReportExpenseRatio"]` | Expense ratio (e.g. 0.0003 for SPY) |
| `info["ytdReturn"]` | Year-to-date return |
| `info["threeYearAverageReturn"]` | 3-year average annual return |
| `info["category"]` | Morningstar category string |
| `info["navPrice"]` | Net asset value |

These should be injected into the ETF prompt instead of PE/yield/earnings fields.

**Implementation:** `build_etf_prompt` follows the same two-line response contract (`SIGNAL: <BUY|HOLD|SKIP>` / `REASONING: ...`) already enforced by `parse_claude_response`. No parser changes needed.

---

### 3. Asyncio Event Loop Hardening

**Recommendation:** Wrap all remaining blocking yfinance calls with `asyncio.to_thread()`.

**Current state of blocking calls in `main.py`:**

| Call | Location in main.py | Currently Wrapped? |
|------|--------------------|--------------------|
| `get_top_sp500_by_fundamentals` | run_scan line 61 | YES — already fixed |
| `analyze_ticker` | run_scan line 112 | YES — already fixed |
| `analyze_sell_ticker` | run_scan line 225 | YES — already fixed |
| `fetch_fundamental_info(yf_ticker)` | run_scan line 82 | NO |
| `fetch_news_headlines(ticker)` | run_scan line 86 | NO |
| `fetch_technical_data(yf_ticker)` | run_scan line 127 | NO |
| `fetch_technical_data(yf_ticker)` (sell pass) | run_scan line 179 | NO |
| `fetch_news_headlines(ticker)` (sell pass) | run_scan line 205 | NO |

**Pattern to apply (HIGH confidence — stdlib asyncio 3.9+):**

```python
# Before (blocks event loop)
info = fetch_fundamental_info(yf_ticker)

# After (offloads to thread pool)
info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)
```

`asyncio.to_thread` is available from Python 3.9. The project already uses it three times in `main.py` (lines 61, 112, 225), so the pattern is established and tested. The underlying functions (`fetch_fundamental_info`, `fetch_technical_data`, `fetch_news_headlines`) are synchronous and thread-safe — they create no shared mutable state.

**Why `asyncio.to_thread` over alternatives:**

| Alternative | Why Not |
|-------------|---------|
| `loop.run_in_executor(None, fn, *args)` | Older API; `asyncio.to_thread` is the 3.9+ idiomatic replacement. No functional difference for this use case. |
| Make yfinance calls truly async | yfinance has no async API; wrapping with `aiohttp` would require reimplementing all yfinance internals |
| `concurrent.futures.ThreadPoolExecutor` manually | More verbose; `asyncio.to_thread` uses the default executor which is already a `ThreadPoolExecutor` |

**APScheduler interaction note (HIGH confidence):** The scheduler already uses `asyncio.run_coroutine_threadsafe(run_scan(...), bot.loop).result()` in `main.py` line 337. This correctly bridges the background scheduler thread to the bot's event loop. No changes needed here — wrapping the blocking calls in `asyncio.to_thread` within `run_scan` is sufficient.

**The `yf.Ticker(ticker)` constructor itself** is not blocking (it does not make network calls until `.info`, `.history`, or `.news` is accessed). Only the property accesses trigger I/O. This means `yf_ticker = yf.Ticker(ticker)` can remain in the synchronous outer loop; only the method calls need wrapping.

---

## What NOT to Add

| Temptation | Why to Resist |
|------------|--------------|
| `aiohttp` / `httpx` async HTTP | yfinance does not expose an async interface; patching it would be a maintenance trap |
| `asyncio.gather` for parallel ticker scanning | Out of scope per PROJECT.md; sequential is acceptable at current scale |
| New ETF data provider (IEX, Polygon, Alpha Vantage) | `quoteType` from existing yfinance call is sufficient for classification |
| `pytest-anyio` or `anyio` | pytest-asyncio 0.24.0 already covers all async test needs |
| ORM / sqlalchemy | Out of scope per PROJECT.md constraints |
| Background task queue (Celery, RQ) | Out of scope; APScheduler + asyncio.to_thread is sufficient |

---

## Version Lock Recommendation

Pin yfinance at `0.2.51`. Do not upgrade during this milestone. yfinance minor versions frequently change which fields are returned in `.info`, and the ETF detection depends on `quoteType` being present. Validate `quoteType` presence before upgrading.

---

## Integration Points

### `screener/universe.py` (or new `screener/etf.py`)
- Add `partition_watchlist(tickers: list[str]) -> tuple[list[str], list[str]]`
- Calls `yf.Ticker(t).info.get("quoteType", "")` per ticker
- Returns `(stocks, etfs)` tuple
- Must be called via `asyncio.to_thread` from the event loop

### `analyst/claude_analyst.py`
- Add `build_etf_prompt(ticker, info, headlines) -> str`
- Reuses `parse_claude_response`, `_call_api`, `analyze_ticker` signature pattern
- Add `analyze_etf_ticker(...)` that calls `build_etf_prompt` instead of `build_prompt`

### `main.py` — `run_scan`
- Wrap `fetch_fundamental_info`, `fetch_news_headlines`, `fetch_technical_data` calls with `asyncio.to_thread`
- Add `/scan_etf` command path that calls `partition_watchlist`, then ETF-specific pipeline

### `discord_bot/bot.py` — `setup_hook`
- Register `/scan_etf` slash command (mirrors existing `/scan` pattern)

### `config.py`
- No new fields required for Phase 7/8 features

### `database/` (additive only)
- No schema changes required for ETF scan separation
- ETF recommendations can share the existing `recommendations` table with a `ticker_type` column addition if desired (additive, backward-compatible)

---

## Sources

- Codebase analysis: `main.py`, `screener/fundamentals.py`, `screener/technicals.py`, `analyst/claude_analyst.py`, `discord_bot/bot.py`, `config.py` (read directly, HIGH confidence)
- `requirements.txt`: pinned versions confirmed from repo (HIGH confidence)
- `asyncio.to_thread` availability: Python 3.9+ stdlib docs (HIGH confidence — already used 3 times in the existing codebase)
- `yfinance quoteType` field: training knowledge of yfinance 0.2.x API (MEDIUM confidence — unofficial scraper, field presence not guaranteed across Yahoo Finance changes; mitigation is `.get()` with fallback)
- ETF info fields (`totalAssets`, `ytdReturn`, etc.): training knowledge of yfinance ETF ticker behavior (MEDIUM confidence — validate against actual SPY/QQQ `.info` dict before finalizing prompt)
