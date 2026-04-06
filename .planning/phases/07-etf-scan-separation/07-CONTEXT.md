# Phase 7: ETF Scan Separation - Context

**Gathered:** 2026-04-07
**Status:** Ready for planning

<domain>
## Phase Boundary

Add a `/scan_etf` Discord slash command that runs ETFs through a dedicated scan path — skipping `passes_fundamental_filter` entirely and using a tailored analyst prompt focused on trend/technical signals. The existing `/scan` command continues to run only on stocks (watchlist.txt after ETFs are migrated out).

New capabilities introduced: `partition_watchlist()`, `run_scan_etf()`, `build_etf_prompt()`, `build_etf_recommendation_embed()`, `etf_watchlist.txt`, additive DB migration (`asset_type` column).

Out of scope: scheduled ETF scan (ETF-07), separate ops alert prefix (ETF-08), expense ratio threshold filter (ETF-09), Phase 8 asyncio wraps.

</domain>

<decisions>
## Implementation Decisions

### ETF Analyst Prompt
- **D-01:** `build_etf_prompt` includes news headlines alongside technical data (same fetch pattern as stock prompt — headlines add macro/sentiment context for ETFs). Headlines fetched via `fetch_news_headlines`.
- **D-02:** Replace P/E / dividend yield / earnings growth context with RSI, MACD, and expense ratio fields.
- **D-03:** Same BUY/HOLD/SKIP signal format as stocks — no new signal types. Reuses existing signal parsing, DB status column, embed routing, and `should_recommend()` logic unchanged.

### watchlist.txt Split
- **D-04:** Migrate all 10 current ETF tickers from `watchlist.txt` to `etf_watchlist.txt` as part of this phase. `watchlist.txt` becomes stocks-only going forward (starts empty after migration — user adds stock tickers separately).
- **D-05:** `partition_watchlist()` in `universe.py` uses yfinance `quoteType` for detection, with `_ETF_ALLOWLIST` (currently in `fundamentals.py`) as static fallback for rate-limit edge cases.
- **D-06:** Remove `_ETF_ALLOWLIST` from `fundamentals.py` and the ETF bypass in `passes_fundamental_filter` only after `partition_watchlist` is verified end-to-end (ETF-06). This is a two-step: implement first, remove bypass second.

### Expense Ratio
- **D-07:** Expense ratio sourced from yfinance `annualReportExpenseRatio` field. If `None`, display `N/A` in embed and prompt (consistent with how stock prompt handles missing P/E). Log a `DEBUG`-level warning when `None` so missing data is visible in logs.

### ASYNC-03 (Phase 7 scope)
- **D-08:** `partition_watchlist()` must be wrapped in `asyncio.to_thread` at its call site in `run_scan_etf()` — this is a Phase 7 requirement (ASYNC-03), not deferred to Phase 8.

### Claude's Discretion
- Exact RSI/MACD/expense ratio field ordering within `build_etf_prompt`
- `build_etf_recommendation_embed` color scheme — may reuse same green/yellow/red signal-color map as stocks
- Whether `run_scan_etf` shares code with `run_scan` (e.g., common helper) or is written independently

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` §ETF-01 through ETF-06 — Full ETF scan separation requirements with acceptance criteria
- `.planning/REQUIREMENTS.md` §ASYNC-03 — partition_watchlist asyncio.to_thread requirement (Phase 7 scope)

### Existing Code to Extend
- `screener/universe.py` — Home for new `partition_watchlist()` function; existing `get_watchlist()`, `get_universe()` patterns to follow
- `screener/fundamentals.py` — Contains `_ETF_ALLOWLIST` (to be removed in ETF-06 step) and `passes_fundamental_filter` ETF bypass (to be removed)
- `analyst/claude_analyst.py` — Contains `build_prompt()` and `build_sell_prompt()` as reference patterns for `build_etf_prompt()`
- `discord_bot/bot.py` — Contains existing slash command registration pattern (`/scan`, `/positions`) for new `/scan_etf` command
- `discord_bot/embeds.py` — Contains `build_recommendation_embed()` as reference pattern for `build_etf_recommendation_embed()`
- `main.py` — Contains `run_scan()` as structural reference for `run_scan_etf()`
- `database/models.py` — `initialize_db` executescript pattern for additive `asset_type` column migration

### Config and Data
- `watchlist.txt` — Currently holds 10 ETF tickers (SPY, QQQ, VTI, IVV, VOO, VEA, BND, GLD, XLK, SCHD); Phase 7 migrates these to `etf_watchlist.txt`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `get_watchlist(path)` in `universe.py` — reads a file line-by-line, skips comments/blanks. `etf_watchlist.txt` loader can call this directly with the new path.
- `_ETF_ALLOWLIST` in `fundamentals.py` — the exact 10 tickers to seed `etf_watchlist.txt` and use as the static fallback in `partition_watchlist`.
- `@_retry` decorator in `analyst/claude_analyst.py` and `screener/fundamentals.py` — apply to `partition_watchlist`'s yfinance calls.
- `_SIGNAL_COLORS` in `discord_bot/embeds.py` — green/yellow/red map already handles BUY/HOLD/SKIP; ETF embed can reuse it.
- `analyze_ticker()` / `analyze_sell_ticker()` return `provider_used` — `analyze_etf_ticker()` should follow the same return shape for quota tracking consistency.

### Established Patterns
- Analyst prompt format: two-line response (signal on line 1, reasoning on line 2). `build_etf_prompt` must instruct Claude to follow the same format so existing `parse_signal()` works unchanged.
- Config injection: all functions that need thresholds receive `config: Config` as a parameter — not globals.
- `asyncio.to_thread` wrapping: `get_top_sp500_by_fundamentals` (hotfix ae66e64) is the established pattern for wrapping blocking yfinance calls.
- Analyst quota guard + increment in `main.py` buy pass — `run_scan_etf()` must include the same guard/increment pattern before/after `analyze_etf_ticker()`.

### Integration Points
- `run_scan_etf()` in `main.py` needs the same parameters as `run_scan()` (`bot`, `config`) plus the DB connection for quota tracking and recommendation writes.
- `/scan_etf` slash command in `bot.py`: registers in `setup_hook` alongside `/scan`, calls `asyncio.create_task(run_scan_etf(...))` pattern.
- `asset_type='etf'` column in `recommendations` table — additive migration, existing stock rows implicitly `asset_type='stock'` (or NULL until backfill).

</code_context>

<specifics>
## Specific Ideas

- No specific UI references provided — standard Discord embed style (same green/yellow/red as stock embeds) is fine.
- ETF prompt should omit P/E, dividend yield, and earnings growth entirely — these fields are meaningless for ETFs and would confuse the analyst.

</specifics>

<deferred>
## Deferred Ideas

- ETF-07: Scheduled ETF scan at a time offset — future requirement, already in REQUIREMENTS.md backlog
- ETF-08: Separate `[ETF]` ops alert prefix — future requirement
- ETF-09: Expense ratio threshold filter (flag ETFs >0.50% in embed) — future requirement

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 07-etf-scan-separation*
*Context gathered: 2026-04-07*
