# Analyst Token Minimization Design

**Date:** 2026-04-01
**Status:** Approved

## Problem

The analyst is called once per ticker that passes the fundamental filter. With a full S&P 500 universe (~500+ tickers), this exhausts the Gemini free-tier daily quota (`generativelanguage.googleapis.com` returning HTTP 429 with `limit: 0`). The current tenacity retry also ignores the `retryDelay` hint in the 429 response body, burning all 3 retry attempts in seconds.

## Goals

- Reduce total analyst API calls per scan
- Prevent mid-scan 429 rate limit errors
- Handle 429 gracefully when it does occur

## Non-Goals

- Batching multiple tickers into a single API call
- Changing the prompt format or response schema
- Switching LLM providers

---

## Section 1: Universe Narrowing

### What

Replace the full S&P 500 list (~500 tickers) with a pre-filtered top 50 ranked by EPS and ROE.

### How

Add `get_top_sp500_by_fundamentals(n: int = 50) -> list[str]` to `screener/universe.py`.

- Fetches `trailingEps` and `returnOnEquity` from yfinance for each S&P 500 ticker
- Scores each ticker by combined rank: `eps_rank + roe_rank` (lower = better)
- Returns the top `n` tickers by combined rank
- Cached in-memory for 24h using the same TTL pattern as the existing S&P 500 cache — the ~500 yfinance info calls only happen once per day

### Config

New field in `config.py`:
```
TOP_SP500_COUNT=10   # default: 10 (Gemini free tier: 20 RPD — 10 S&P + ~10 watchlist ≈ 20 calls/day)
```

### Impact

`run_scan()` in `main.py` replaces `get_sp500_tickers()` with `get_top_sp500_by_fundamentals()`. Universe shrinks from ~500 to ~20 tickers (10 S&P picks + ~10 watchlist), fitting within Gemini's 20 RPD free-tier limit.

---

## Section 2: Analyst Result Cache

### What

Cache analyst results in SQLite keyed by `(ticker, headline_hash)`. A ticker is only re-analyzed when its news changes.

### Schema

New table in `database/models.py`:

```sql
CREATE TABLE IF NOT EXISTS analyst_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    headline_hash TEXT NOT NULL,
    signal      TEXT NOT NULL,
    reasoning   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(ticker, headline_hash)
);
```

### Cache Key

`headline_hash` = SHA-256 of headlines sorted alphabetically and joined with `\n`. Computed in `run_scan()` after `fetch_news_headlines()`.

### New Queries

Two new functions in `database/queries.py`:

- `get_cached_analysis(db_path, ticker, headline_hash) -> dict | None` — returns `{signal, reasoning}` on hit, `None` on miss
- `set_cached_analysis(db_path, ticker, headline_hash, signal, reasoning)` — upserts a cache row

### Flow Change in `run_scan()`

```
headlines = fetch_news_headlines(ticker)
headline_hash = sha256_of(headlines)

cached = get_cached_analysis(db_path, ticker, headline_hash)
if cached:
    analysis = cached          # skip API call
else:
    analysis = analyze_ticker(...)
    set_cached_analysis(db_path, ticker, headline_hash, ...)
```

### Cache Invalidation

The cache is keyed on content, not time — no explicit TTL needed. When headlines change, the hash changes and a fresh analysis is triggered automatically.

---

## Section 3: Rate Limiting and 429 Handling

### What

Two changes to `analyst/claude_analyst.py`:

1. **Inter-call delay** — sleep before each analyst API call to stay under the free-tier RPM cap
2. **Respect `retryDelay`** — parse the retry delay from the 429 response body instead of using fixed exponential backoff

### Inter-call Delay

New config field:
```
ANALYST_CALL_DELAY_S=12.0   # default: 12.0 seconds (Gemini free tier: 5 RPM → 60s ÷ 5 = 12s minimum)
```

`analyze_ticker()` calls `time.sleep(config.analyst_call_delay_s)` before invoking `_call_api()`. At 12s/call, 20 tickers = ~4 minutes total scan time, at or under Gemini's 5 RPM free-tier limit. TPM impact is negligible: 5 RPM × ~500 tokens/call ≈ 2,500 TPM, well under the 250k TPM ceiling.

### Retry Delay Parsing

Replace the current tenacity `wait_exponential(min=2, max=30)` with a custom wait callable:

```python
def _wait_for_retry(retry_state) -> float:
    exc = retry_state.outcome.exception()
    # Parse "retryDelay": "28s" from the 429 error body if present
    if exc and hasattr(exc, 'response'):
        try:
            delay_str = exc.response.json()['error']['details'][-1]['retryDelay']
            return float(delay_str.rstrip('s')) + 1
        except Exception:
            pass
    # Fallback: exponential backoff
    return min(2 ** retry_state.attempt_number, 60)
```

This ensures tenacity sleeps exactly as long as the API requests, rather than retrying instantly. The `retryDelay` parsing targets the Gemini error body structure (`error.details[].retryDelay`); for Claude (Anthropic SDK) and OpenAI, the fallback exponential path is used.

### Note on Event Loop Blocking

The scan currently runs on the Discord bot's event loop via `run_coroutine_threadsafe(...).result()`. The synchronous `time.sleep()` calls in tenacity block the event loop, causing Discord heartbeat warnings. This is a separate issue outside this design's scope.

---

## Files Changed

| File | Change |
|---|---|
| `screener/universe.py` | Add `get_top_sp500_by_fundamentals()` with 24h cache |
| `config.py` | Add `TOP_SP500_COUNT`, `ANALYST_CALL_DELAY_S` |
| `database/models.py` | Add `analyst_cache` table |
| `database/queries.py` | Add `get_cached_analysis()`, `set_cached_analysis()` |
| `main.py` | Use new universe fn, add headline hash + cache check in `run_scan()` |
| `analyst/claude_analyst.py` | Add inter-call delay, replace tenacity wait with `_wait_for_retry` |

---

## Expected Outcome

| Metric | Before | After |
|---|---|---|
| Tickers reaching analyst | ~500+ | ~20 (10 S&P + ~10 watchlist) |
| Analyst calls on repeated scans | same each day | 0 for unchanged news |
| Calls per day (cold cache) | ~500+ | ≤20 — fits Gemini 20 RPD free tier |
| Calls per minute | uncontrolled | ≤5 — fits Gemini 5 RPM free tier (12s delay) |
| Tokens per minute | uncontrolled | ~2,500 TPM — well under 250k TPM free tier |
| 429 on RPM limit | frequent | eliminated by 12s delay |
| 429 retry behavior | 3 retries in ~6s | respects API-specified delay |
