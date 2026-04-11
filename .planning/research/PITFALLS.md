# Pitfalls Research — v1.2

**Domain:** Adding signal enrichment, confidence scoring, macro context, ETF scheduling, portfolio stats, and error surfacing to a live Python Discord trading bot
**Researched:** 2026-04-12
**Confidence:** HIGH — all pitfalls derived from direct codebase inspection of main.py, claude_analyst.py, technicals.py, config.py, queries.py, and PROJECT.md

---

## Summary

The highest risks in v1.2 share a common root: each new feature touches either the analyst prompt/parser pair, the yfinance `info` dict, or the APScheduler setup — three surfaces with established fragility in this codebase. Adding a fourth parse target (CONFIDENCE), a second scheduler job (ETF scheduled scan), and new yfinance field reads (sector, VIX, 52-week range) all extend existing fragile paths without replacing them. The analyst cache also becomes a hidden problem: cache hits return dicts without a `confidence` key, guaranteed to happen on the first post-deployment scan for any ticker with unchanged headlines. This is silent and affects Discord embed rendering, not scan correctness.

---

## Pitfalls

---

### P-01: Confidence line breaks the two-line parser

- **Risk**: `parse_claude_response` (claude_analyst.py lines 165-190) expects exactly `SIGNAL:` then `REASONING:`. Adding `CONFIDENCE:` requires the parser to handle a three-line contract. If the model omits CONFIDENCE, returns it inline with reasoning ("BUY — high confidence"), or labels it differently ("CONF:", "Confidence Level:"), `parse_claude_response` either silently ignores it or raises `ValueError` — both of which cause the entire ticker to be dropped via `except Exception: continue` in `run_scan`. No exception is logged at the Discord level; the scan just posts fewer recommendations.
- **Trigger**: Prompt instructs "return exactly three lines" but any LLM output that does not strictly comply. Claude models frequently paraphrase format instructions when responding to complex multi-field prompts.
- **Prevention**: Extend `parse_claude_response` using the existing `next(... startswith("CONFIDENCE:"), None)` pattern already used for SIGNAL and REASONING. Make CONFIDENCE optional in the parser — return `None` if absent rather than raising. Test with synthetic responses covering: missing CONFIDENCE, inline CONFIDENCE, reordered lines, alternate labels. Do not change the function's exception behavior for SIGNAL/REASONING; only CONFIDENCE should degrade gracefully.
- **Phase**: Phase adding SIG-04 (confidence scoring).

---

### P-02: yfinance `info` dict returns None silently for new fields

- **Risk**: `sector`, `fiftyTwoWeekHigh`, `fiftyTwoWeekLow`, and `netExpenseRatio` are all read from `yf_ticker.info`. yfinance is an unofficial scraper; these fields return `None`, an empty string, or are entirely absent with no exception. If prompt builders use `info["sector"]` (KeyError) or pass a raw `None` into an f-string format like `f"{sector.upper()}"` (AttributeError), the ticker is silently skipped. Worse, `f"Sector: {sector}"` renders as `"Sector: None"` — a valid string sent to the LLM, which degrades signal quality without triggering any error.
- **Trigger**: ETF tickers return sparse `info` dicts. `sector` is frequently absent for ETFs, SPACs, and foreign-listed ADRs. `fiftyTwoWeekHigh`/`Low` can be absent for recently-listed tickers. `netExpenseRatio` is always absent for stocks and sometimes absent for ETFs.
- **Prevention**: Use `.get("sector", "N/A")` everywhere — never direct index. Apply the same defensive pattern already established for `trailingPE`, `dividendYield`, and `earningsGrowth` in `build_prompt` (lines 92-94 of claude_analyst.py). Pass formatted strings to prompt builders, not raw values. Add unit tests with `info={}` confirming graceful fallback to "N/A" strings.
- **Phase**: Phase adding SIG-01 (sector), SIG-02 (macro context), SIG-03 (52-week range).

---

### P-03: SPY/VIX macro fetch is a bare blocking yfinance call risk

- **Risk**: The macro context fetch (SPY close, VIX level) will be a new yfinance call inside `run_scan`, which is an `async` function running on the Discord event loop. If written as a direct `yf.Ticker("SPY").history(...)` without `asyncio.to_thread`, it blocks the event loop and risks a Discord gateway heartbeat timeout. This is the exact class of bug that v1.1 Phase 8 spent a dedicated sweep to eliminate across 9 call sites — it is easy to reintroduce with a natural-looking one-liner.
- **Trigger**: Macro fetch written at the top of `run_scan` before the ticker loop as a convenience one-liner. The developer sees it as "just one quick call" rather than a blocking I/O call.
- **Prevention**: Wrap in `asyncio.to_thread` from the first commit. Model it like `get_top_sp500_by_fundamentals` — fetched once before the loop, wrapped, result passed down as a dict (e.g., `{"spy_trend": "uptrend", "vix": 18.2}`). Add a regression test asserting `asyncio.to_thread` was called for the macro fetch (patch `main.asyncio.to_thread` and assert it was invoked with a yfinance-related callable).
- **Phase**: Phase adding SIG-02 (macro context).

---

### P-04: Second APScheduler job conflicts with or overwrites the first

- **Risk**: `configure_scheduler` (main.py lines 39-48) adds jobs with IDs `scan_0`, `scan_1`, etc., using `replace_existing=True`. If the ETF scheduled scan is registered using the same ID pattern — or calls `configure_scheduler` again with ETF times — it silently overwrites existing stock scan jobs. Separately, if both scans fire at the same minute, they share analyst quota in a single run, exhausting `analyst_daily_limit` faster than designed.
- **Trigger**: ETF scan job registered inside `configure_scheduler` with no namespace separation, or scheduled at the same time as the stock scan without an offset.
- **Prevention**: Use a distinct ID namespace for ETF jobs (`etf_scan_0`, `etf_scan_1`). Add `ETF_SCAN_OFFSET_MINUTES` to Config (default 30 minutes after stock scan) and document in `.env.example`. Test that calling `configure_scheduler` for both stock and ETF times produces additive job counts — assert `scheduler.get_jobs()` has the expected count, not that some were replaced.
- **Phase**: Phase adding ETF-07 (scheduled ETF scan).

---

### P-05: Confidence badge crashes Discord embed when field is None

- **Risk**: When the parser returns `confidence=None` (cache hit, optional parse miss, or legacy response), any embed builder code that calls `confidence.upper()` or `f"[{confidence}]"` without a None guard raises `AttributeError`. The embed send fails, the recommendation is written to DB via `create_recommendation` but the Discord message is never posted, and `set_discord_message_id` is never called — leaving an orphaned `pending` row in the recommendations table with no Discord message.
- **Trigger**: Any code path in `discord_bot/embeds.py` or `bot.send_recommendation` that references `confidence` without checking for None. Cache hit paths are guaranteed to produce `confidence=None` after deployment (see P-09).
- **Prevention**: Default confidence to `"unknown"` or `""` before passing to the embed. Guard with `confidence or "N/A"` in the embed builder. Add an explicit test: `send_recommendation` called with `confidence=None` must not raise and must produce a valid embed.
- **Phase**: Phase adding SIG-04 (confidence badge in embed).

---

### P-06: `/stats` SQLite aggregates return None on empty or buy-only DB

- **Risk**: Win-rate and avg-gain queries on the `trades` table return `NULL` from SQLite when the table is empty or has no sell-side rows. In Python, `sqlite3` maps SQL `NULL` to Python `None`. Any code that formats this as `f"{win_rate:.1%}"` raises `TypeError: float argument required, not NoneType`. The `/stats` command fails visibly in Discord with an unhandled exception embed, or silently with an empty response if the exception is swallowed.
- **Trigger**: `/stats` called on a fresh DB, or a DB with only open positions and no closed round-trip trades (buy + matching sell). Also: dry-run trades have `order_id=None` — a P&L join that relies on `order_id` matching will produce incorrect results.
- **Prevention**: All aggregate queries must use `COALESCE(AVG(...), 0.0)` at the SQL level, or handle `None` return in Python before formatting. Define "closed trade" explicitly as a buy trade with a matching sell trade by ticker, and implement the join before building the embed. Add three tests: empty DB, buy-only DB, one complete round-trip.
- **Phase**: Phase adding PORT-02 (/stats command).

---

### P-07: Enriched prompts increase token usage and hit Gemini free-tier token cap before call-count cap

- **Risk**: Adding sector, SPY trend, VIX level, and 52-week range to every prompt increases token count per call by approximately 40-100 tokens. The `analyst_calls` table guards by call count (default limit 18), not by token count. The actual Gemini free-tier token/day limit (1M tokens/day for Flash, as of research cutoff) can now exhaust before the call-count guard fires, causing 429 errors that the retry logic handles — but which consume retry budget and add delay inside the scan loop.
- **Trigger**: Full universe scan (20+ tickers) on Gemini free tier after prompt enrichment with all four new context fields.
- **Prevention**: Keep macro context brief — one line each (e.g., `"SPY trend: above 200MA"`, `"VIX: 18.2"`). Do not repeat macro data per ticker; fetch it once and pass it as a compact prefix. Measure token count of the enriched prompt against a representative ticker before committing the implementation. Document the new approximate tokens-per-call in the phase plan so the quota math can be re-validated.
- **Phase**: Phase adding SIG-01 through SIG-03.

---

### P-08: Error surfacing to Discord creates a feedback loop on ops channel flood

- **Risk**: OPS-01 adds per-exception Discord ops alerts. If `bot.send_ops_alert` itself fails (Discord rate limit, channel misconfigured, bot not ready), and the failure is inside an exception handler that calls `send_ops_alert`, the error is silently swallowed with only a log line — safe. But if per-ticker errors trigger one `send_ops_alert` per ticker, a scan with 20 failing tickers floods the ops channel with 20 messages within seconds, hitting Discord's rate limit and causing the later alerts to silently fail.
- **Trigger**: yfinance returning errors for multiple tickers simultaneously (common during Yahoo Finance maintenance windows). Or Discord rate limit on the ops channel from a previous flood.
- **Prevention**: Never call `send_ops_alert` inside the per-ticker `except` block. Accumulate failures into a list during the loop, then send one summary alert after the loop completes: `"Scan complete: 3 tickers failed — AAPL, MSFT, TSLA"`. Wrap all `send_ops_alert` calls in their own `try/except` with `logger.error` as the only fallback — never a nested `send_ops_alert`.
- **Phase**: Phase adding OPS-01 (error surfacing).

---

### P-09: Cache hit returns analysis dict without confidence key — guaranteed on first post-deployment scan

- **Risk**: The analyst cache (`analyst_cache` table) stores `signal` and `reasoning` but not `confidence`. When `get_cached_analysis` returns a hit, the returned dict has no `confidence` key. All downstream code added for SIG-04 that reads `analysis.get("confidence")` will return `None` for every cache hit. This is not a rare edge case — it is guaranteed for every ticker with unchanged headlines on the first scan after deploying SIG-04. If the embed builder or DB write code assumes confidence is populated, cache-hit recommendations fail silently while cache-miss recommendations succeed, producing inconsistent scan behavior.
- **Trigger**: Any ticker whose headline SHA-256 hash matches a cache entry written before confidence scoring was deployed. Given the 24h cache TTL (if implemented) or unlimited cache (current state — no TTL in queries.py), this affects all previously-analyzed tickers.
- **Prevention**: Add `confidence TEXT` column to `analyst_cache` with `DEFAULT NULL` as a schema migration (additive, backward compatible with existing rows). Populate on new cache writes. In all readers, treat `confidence=None` as `"unknown"`. Test the cache-hit path explicitly: assert that `analysis` returned from `get_cached_analysis` used with `confidence=None` does not crash the embed builder.
- **Phase**: Phase adding SIG-04 — requires a DB migration coordinated with the confidence parser implementation.

---

### P-10: Expense ratio config field fails at scan time, not startup

- **Risk**: ETF-09 adds an expense ratio threshold filter. If `MAX_EXPENSE_RATIO` is added to Config as `float(os.getenv("MAX_EXPENSE_RATIO", "0.01"))`, a misconfigured `.env` value like `"0.5%"` (with percent sign) causes `ValueError: could not convert string to float` at Config construction. But if the field is introduced incorrectly — parsed lazily inside a filter function rather than at Config construction — the bot starts successfully and the error only surfaces per-ETF during the scan, causing every ETF to be silently skipped via `except Exception: continue`.
- **Trigger**: `.env` value contains a percent sign, or the field defaults to `""` which `float("")` raises on. Also: if the filter function does the parse rather than Config doing it.
- **Prevention**: Parse `MAX_EXPENSE_RATIO` in Config exactly like all other float fields — at dataclass construction time, with a safe default (`0.01`). Add validation in `Config.validate()` that `0 < max_expense_ratio < 1`. Add a test asserting that a Config with invalid `MAX_EXPENSE_RATIO` raises at construction, not at scan time.
- **Phase**: Phase adding ETF-09 (expense ratio threshold filter).

---

### P-11: Cache key is news headlines hash — prompt enrichment does not invalidate it

- **Risk**: The analyst cache key is `SHA-256(sorted(headlines))` — not a hash of the prompt. This means that when SIG-01 through SIG-03 enrich `build_prompt` with sector, macro, and 52-week data, tickers with unchanged headlines continue to hit the cache and receive old-format analyses (without sector/macro context) as if they were current. The enrichment only benefits tickers whose headlines changed since the last cache write. This is a correctness issue, not a crash — the system behaves normally, but the signal quality improvement from enrichment is invisible on cache-hit paths.
- **Trigger**: First several scans after deploying SIG-01 through SIG-03, for all tickers with stable news (e.g., blue-chips that have been in the scan universe for days).
- **Prevention**: Accept this as a known limitation and document it explicitly in the phase plan. Options to mitigate: (a) clear the analyst cache on deployment of SIG-01-03 (a one-time `DELETE FROM analyst_cache`), or (b) include a prompt version hash in the cache key. Option (a) is simpler and lower risk. Do not silently accept stale cache hits as enriched responses — communicate the limitation to the operator.
- **Phase**: Phase adding SIG-01 through SIG-03.

---

### P-12: Unrealized P&L aggregate requires live price fetch not in existing queries.py

- **Risk**: PORT-01 adds total unrealized P&L to `/positions`. The current `get_position_summary` in `screener/positions.py` fetches live prices per position. Aggregating unrealized P&L requires summing `(current_price - avg_cost) * shares` across all positions. This is a live price fetch inside an async Discord slash command handler — meaning it must be wrapped in `asyncio.to_thread`, must handle yfinance returning None for any ticker, and must handle positions with `shares=0` or `avg_cost=0` without dividing by zero. If any of these are not handled, the `/positions` embed either crashes or displays an incorrect total.
- **Trigger**: `/positions` called when any open position has a yfinance data unavailability, or when `avg_cost_usd` is 0 (edge case from dry-run trades where price was not confirmed).
- **Prevention**: Guard every position's contribution to the aggregate with `if current_price is not None and avg_cost > 0`. Display "P&L unavailable" for positions where live price fetch failed rather than omitting them or showing 0. Wrap the batch price fetch in `asyncio.to_thread`. Test with a position that has `avg_cost_usd=0`.
- **Phase**: Phase adding PORT-01 (unrealized P&L aggregate in /positions).

---

## Top 3 Risks

1. **P-01 — Confidence line breaks the two-line parser (HIGH impact)**: The current parser is the single failure point for every analyst signal. Breaking it silently drops recommendations for the entire scan. It is the highest regression risk because adding a required third output line is a breaking change to the format contract, and the `except Exception: continue` pattern in `run_scan` ensures failures are swallowed, not surfaced. The 252 existing tests all pass through this parser; any change to it requires comprehensive test coverage of the new format variants before shipping.

2. **P-09 — Cache hit returns no confidence field — guaranteed on first post-deployment scan (HIGH certainty)**: Unlike most pitfalls that are possible, this one is certain. Every ticker with unchanged headlines will hit the cache on the first scan after SIG-04 deploys and return a dict missing `confidence`. If embed rendering assumes the field exists, cache-hit tickers silently fail to post to Discord while cache-miss tickers succeed — an inconsistent, hard-to-diagnose failure mode. Must be addressed in the same phase as SIG-04, not as a follow-up.

3. **P-03 — SPY/VIX macro fetch blocks the Discord event loop (HIGH impact, hard to detect)**: The bot has a hard-won rule — every yfinance call must be in `asyncio.to_thread`. The macro fetch is the natural place to break this rule because it looks like a one-liner at the top of `run_scan`. A bare blocking call here causes intermittent gateway disconnects under load that manifest as the bot going offline mid-scan — not an obvious exception. v1.1 Phase 8 eliminated this class of bug across 9 call sites; v1.2 must not reintroduce it on the 10th.
