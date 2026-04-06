# Architecture Patterns

**Project:** Algo Trade Bot — v1.1 ETF + Async
**Researched:** 2026-04-06
**Scope:** ETF scan separation + asyncio.to_thread hardening

---

## Existing Architecture (v1.0 Baseline)

```
main.py
  run_scan(bot, config)
    ├─ get_top_sp500_by_fundamentals(config)       ← asyncio.to_thread ✓
    ├─ get_universe(watchlist_path, sp500)
    └─ for each ticker:
         ├─ yf.Ticker(ticker)                      ← BLOCKING on event loop
         ├─ fetch_fundamental_info(yf_ticker)       ← BLOCKING on event loop
         ├─ passes_fundamental_filter(info, config)
         ├─ fetch_news_headlines(ticker)            ← BLOCKING on event loop
         ├─ analyze_ticker(...)                     ← asyncio.to_thread ✓
         ├─ fetch_technical_data(yf_ticker)         ← BLOCKING on event loop
         └─ bot.send_recommendation(...)
    └─ sell pass:
         ├─ yf.Ticker(ticker)                      ← BLOCKING on event loop
         ├─ fetch_technical_data(yf_ticker)         ← BLOCKING on event loop
         ├─ fetch_news_headlines(ticker)            ← BLOCKING on event loop
         └─ analyze_sell_ticker(...)               ← asyncio.to_thread ✓
```

**Existing partial workaround in fundamentals.py:** `_ETF_ALLOWLIST` hardcodes 10 ETF symbols that bypass the fundamental filter (`passes_fundamental_filter` returns `True` for them). This is a silent bypass, not a proper scan separation — ETFs still receive the stock-oriented analyst prompt.

---

## Recommended Architecture for v1.1

### Component Changes Overview

| Component | Change Type | What Changes |
|-----------|-------------|--------------|
| `screener/universe.py` | New function | `partition_watchlist(watchlist) -> (stocks, etfs)` |
| `screener/fundamentals.py` | No change | `_ETF_ALLOWLIST` can be removed once partition is live |
| `analyst/claude_analyst.py` | New function | `build_etf_prompt(ticker, tech_data, headlines)` |
| `analyst/claude_analyst.py` | New function | `analyze_etf_ticker(ticker, tech_data, headlines, config, client, fallback_client)` |
| `main.py` | New function | `run_scan_etf(bot, config)` |
| `main.py` | Modified | `main()` — schedule ETF scan + register `/scan_etf` command |
| `discord_bot/bot.py` | New command | `/scan_etf` slash command in `setup_hook` + `_scan_etf_command` handler |
| All call sites | Wrap | All remaining blocking yfinance calls → `asyncio.to_thread` |

---

## Component Boundaries

### Where `partition_watchlist` Lives

**Decision: `screener/universe.py`**

Rationale:
- Universe construction is already `universe.py`'s responsibility. `get_watchlist`, `get_universe`, `get_top_sp500_by_fundamentals` all live there.
- `partition_watchlist` is the natural next step after building the universe: you have a list of tickers and need to classify them by asset type.
- Keeps `main.py` clean: `universe = get_universe(...); stocks, etfs = partition_watchlist(universe)`.
- The function makes a yfinance `quoteType` fetch per ticker — it is inherently I/O-bound and blocking, so it must be called inside `asyncio.to_thread` at the `main.py` call site.

Signature:
```python
def partition_watchlist(tickers: list[str]) -> tuple[list[str], list[str]]:
    """
    Classify each ticker as stock or ETF using yfinance quoteType.
    Returns (stocks, etfs). Tickers where quoteType is missing default to stocks.
    """
```

### `run_scan_etf` vs a flag on `run_scan`

**Decision: Separate function `run_scan_etf`**

A flag (`mode="etf"`) on `run_scan` would require branching at every decision point in the pipeline — fundamental filter skip, prompt selection, Discord command routing — making `run_scan` harder to test and reason about.

`run_scan_etf` is a parallel pipeline that:
1. Takes the `etfs` list from `partition_watchlist`.
2. Skips `passes_fundamental_filter` entirely.
3. Calls `analyze_etf_ticker` (trend/momentum/macro prompt) instead of `analyze_ticker`.
4. Calls `fetch_technical_data` (reused unchanged — RSI/MA50/volume apply to ETFs).
5. Calls `should_recommend` (reused unchanged).
6. Posts via `bot.send_recommendation` (reused unchanged — same embed + Approve/Reject flow).

The only shared infrastructure is the analyst quota guard, `should_recommend`, `fetch_technical_data`, `fetch_news_headlines`, and the Discord posting path. These are all pure enough to reuse without coupling the two scan flows together.

**Scheduler integration:** `configure_scheduler` schedules one job function. After v1.1, `main.py` will schedule both `run_scan` and `run_scan_etf` — either on the same cron time, or with separate times configurable via `Config`. The simplest approach is a single combined wrapper:

```python
async def run_full_scan(bot: TradingBot, config: Config) -> None:
    """Run stock scan + ETF scan in sequence."""
    stocks, etfs = await asyncio.to_thread(partition_watchlist, universe)
    await run_scan(bot, config, tickers=stocks)
    await run_scan_etf(bot, config, tickers=etfs)
```

This avoids scheduling two separate jobs and keeps one daily "scan complete" ops alert.

Alternatively, `run_scan` and `run_scan_etf` can each accept an optional `tickers` override for slash commands, while the scheduler always calls `run_full_scan`. Either way the core functions remain independently callable and testable.

---

## Asyncio Wrapping Strategy

### Inline `asyncio.to_thread` vs Named Async Wrappers

**Decision: Inline `asyncio.to_thread` at call site in `main.py`**

Two options considered:

**Option A — Inline:**
```python
info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)
headlines = await asyncio.to_thread(fetch_news_headlines, ticker)
tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)
```

**Option B — Named async wrappers in each module:**
```python
# fundamentals.py
async def fetch_fundamental_info_async(yf_ticker) -> dict:
    return await asyncio.to_thread(fetch_fundamental_info, yf_ticker)
```

**Why inline wins here:**
- The codebase already uses inline `asyncio.to_thread` for `get_top_sp500_by_fundamentals` and `analyze_ticker`. Consistency matters for readability.
- The sync versions remain pure and directly testable without async fixtures — a property the project explicitly values (see CLAUDE.md "Pure functions for testability").
- Named async wrappers add a new public API surface to each module, doubling the function count for things that are purely infrastructure concerns, not domain logic.
- `main.py` is the only async consumer of these functions. There is no second async consumer that would benefit from a reusable async wrapper.

**Exception — `yf.Ticker(ticker)` construction:** The `yf.Ticker(ticker)` constructor call in the scan loops (buy pass and sell pass) should also be wrapped:
```python
yf_ticker = await asyncio.to_thread(yf.Ticker, ticker)
```
The constructor does not perform network I/O itself (it is lazy), but wrapping it is low-cost and future-proof if yfinance's internal behavior changes.

**Correction on `fetch_news_headlines`:** This function internally constructs `yf.Ticker(ticker)` and accesses `.news`. It is entirely I/O. Wrap at call site:
```python
headlines = await asyncio.to_thread(fetch_news_headlines, ticker)
```

### Complete Inventory of Blocking Calls to Wrap

| Location | Call | Phase |
|----------|------|-------|
| `main.py` buy pass | `yf.Ticker(ticker)` | Phase 8 |
| `main.py` buy pass | `fetch_fundamental_info(yf_ticker)` | Phase 8 |
| `main.py` buy pass | `fetch_news_headlines(ticker)` | Phase 8 |
| `main.py` buy pass | `fetch_technical_data(yf_ticker)` | Phase 8 |
| `main.py` sell pass | `yf.Ticker(ticker)` (×2, sell-blocked check + main) | Phase 8 |
| `main.py` sell pass | `fetch_technical_data(yf_ticker)` (×2) | Phase 8 |
| `main.py` sell pass | `fetch_news_headlines(ticker)` | Phase 8 |
| `universe.py` | `partition_watchlist` (new) | Phase 7 — must be async-safe from day one |
| `main.py` | `partition_watchlist(universe)` call | Phase 7 |

`get_top_sp500_by_fundamentals` is already wrapped — no change needed.
`analyze_ticker` and `analyze_sell_ticker` are already wrapped — no change needed.

---

## Data Flow — New ETF Path

```
/scan_etf command OR daily cron
  → run_full_scan(bot, config)
       ├─ build universe (watchlist + sp500)
       ├─ asyncio.to_thread(partition_watchlist, universe)
       │    └─ yfinance quoteType per ticker → (stocks, etfs)
       │
       ├─ run_scan(bot, config, tickers=stocks)        ← existing path, unchanged logic
       │    └─ fundamental → analyst (stock prompt) → technical
       │
       └─ run_scan_etf(bot, config, tickers=etfs)      ← new path
            └─ for each etf:
                 ├─ asyncio.to_thread(fetch_news_headlines, ticker)
                 ├─ asyncio.to_thread(analyze_etf_ticker, ...)
                 ├─ asyncio.to_thread(fetch_technical_data, yf_ticker)
                 ├─ should_recommend(signal, tech_data, config)   ← reused
                 └─ bot.send_recommendation(...)                  ← reused
```

---

## Patterns to Follow

### Pattern 1: Analyst Prompt Separation

`build_etf_prompt` should be in `analyst/claude_analyst.py` alongside `build_prompt` (stock) and `build_sell_prompt`. The ETF prompt omits P/E and earnings growth (not meaningful for ETFs) and instead emphasizes:
- Trend direction (price vs MA50)
- Momentum (RSI)
- Macro context (from headlines)
- Sector/asset class represented

`analyze_etf_ticker` follows the exact structure of `analyze_ticker` — same `_call_api` + fallback pipeline, same `parse_claude_response` reuse, same quota guard pattern in `main.py`. No new parsing logic needed; the existing SIGNAL/REASONING format works.

### Pattern 2: `_ETF_ALLOWLIST` Retirement

`fundamentals.py` currently has `_ETF_ALLOWLIST` as a hardcoded bypass. Once `partition_watchlist` correctly routes ETFs to `run_scan_etf`, ETFs will never reach `passes_fundamental_filter`. The allowlist can be removed in Phase 7. However, removal should happen only after `partition_watchlist` is tested and confirmed to correctly classify SPY, QQQ, VTI, IVV, VOO, VEA, BND, GLD, XLK, SCHD — the exact tickers currently in the allowlist.

### Pattern 3: Quota Guard Reuse

The analyst quota guard block (check primary count, check fallback count, skip if both exhausted) appears twice in `run_scan` — once for the buy analyst call, once for the sell analyst call. `run_scan_etf` will need a third copy. If deduplication is desired, extract a `_quota_exhausted(config, db_path) -> bool` helper in `main.py`. This is a moderate-priority cleanup; functional duplication is acceptable for v1.1 if time is constrained.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: ETF Flag on `run_scan`

**What:** Adding `mode: str = "stock"` or `is_etf: bool = False` parameter to `run_scan`.

**Why bad:** Creates branching inside an already 100+ line function. The two paths (stock vs ETF) differ at three points: fundamental filter, analyst prompt, and (eventually) exit signal thresholds. A flag turns a linear pipeline into a conditional tangle and makes the function harder to test — you must test every combination of flag states.

**Instead:** `run_scan_etf` as a separate function. Share what's genuinely shared (quota guard helper, `should_recommend`, technical fetch, Discord posting) via extraction, not branching.

### Anti-Pattern 2: Async Wrappers as New Module API

**What:** Adding `fetch_fundamental_info_async`, `fetch_technical_data_async`, `fetch_news_headlines_async` as exported functions in their respective modules.

**Why bad:** The sync versions are the tested, documented API. Adding async wrappers doubles the surface area and creates ambiguity about which version to call. Tests that currently mock `fetch_fundamental_info` would need to also mock the async wrapper. The wrapping is an orchestration concern, not a domain concern.

**Instead:** Wrap inline at the `main.py` call site with `asyncio.to_thread`. One pattern, one place.

### Anti-Pattern 3: Constructing `yf.Ticker` Inside Threaded Functions

**What:** Having `fetch_fundamental_info` or `fetch_technical_data` internally call `yf.Ticker(ticker)` when passed a ticker string instead of a pre-built object.

**Why bad:** The current design correctly passes a pre-built `yf.Ticker` object to these functions — this means one `yf.Ticker` construction per ticker per scan, not multiple. Changing to string-based arguments would hide the object construction inside the thread and make it harder to reason about where yfinance state is created.

**Instead:** Keep passing pre-built `yf.Ticker` objects. Wrap `yf.Ticker(ticker)` construction itself in `asyncio.to_thread` at the scan loop level.

### Anti-Pattern 4: Scheduling Two Independent Scan Jobs

**What:** Adding a separate APScheduler job for ETF scan, running on a different trigger.

**Why bad:** Two scan jobs run independently — they both fetch news, both consume analyst quota, and neither knows about the other's state. If the ETF scan fires while the stock scan is mid-execution, the quota guard counts will be in a partially-consumed state. With a sequential combined scan, quota consumption is deterministic.

**Instead:** One `run_full_scan` coroutine scheduled by APScheduler, which internally runs stock scan then ETF scan in sequence.

---

## Build Order Consideration: Phase 7 Before Phase 8

Phase 7 (ETF path) introduces `partition_watchlist` and `run_scan_etf`. Both call the same blocking functions that Phase 8 wraps. The recommended build order is:

**Phase 7 first:**
- Implement `partition_watchlist` — wrap it with `asyncio.to_thread` from day one since it is new code.
- Implement `run_scan_etf` — write its yfinance calls as `asyncio.to_thread` inline from the start (new code, no migration needed).
- Leave existing `run_scan` blocking calls as-is — Phase 8 will fix them.
- This means after Phase 7, `run_scan_etf` is async-clean, but `run_scan` still has blocking calls. Acceptable for a development milestone.

**Phase 8 second:**
- Wrap the remaining blocking calls in `run_scan` (buy pass and sell pass).
- At this point the entire scan pipeline is event-loop safe.
- Phase 8 is purely mechanical — no logic changes, only wrapping — so it can be validated purely by confirming the event loop does not block (no new test logic required beyond confirming existing tests still pass).

**Why not Phase 8 first:** Wrapping existing calls first and then adding the ETF path saves nothing — the ETF path is new code and can be written async-clean regardless of Phase 8 status. Phase 7 produces a working feature; Phase 8 is a reliability hardening pass.

---

## Scalability Considerations

| Concern | Current (v1.1 scope) | If universe grows |
|---------|---------------------|-------------------|
| `partition_watchlist` latency | One `yfinance.info` call per ticker in watchlist; at ~20 tickers this is fast enough synchronously | If universe grows to 50+ tickers, consider caching quoteType results (24h TTL, same pattern as sp500_cache.json) |
| ETF scan quota consumption | ETFs skip fundamental filter but still call analyst; at 5-10 ETFs this is minimal | Add ETF-specific quota config field if ETF analyst calls start competing with stock calls |
| Sequential scan | run_scan then run_scan_etf sequential is fine for current scale | PROJECT.md explicitly lists "async parallelization of scan" as Out of Scope |

---

## Sources

- Code analysis: `main.py`, `screener/universe.py`, `screener/fundamentals.py`, `screener/technicals.py`, `analyst/claude_analyst.py`, `analyst/news.py`, `discord_bot/bot.py` — HIGH confidence (direct inspection)
- `_ETF_ALLOWLIST` discovery: `screener/fundamentals.py` line 12 — HIGH confidence (direct inspection)
- Async wrapping pattern: existing `asyncio.to_thread` usage at `main.py` lines 61, 112, 225 — HIGH confidence (direct inspection)
- PROJECT.md constraints and Out of Scope decisions — HIGH confidence (direct inspection)
