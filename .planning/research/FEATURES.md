# Features Research — v1.2

## Summary

Six new capabilities for v1.2 span three concerns: signal enrichment (confidence scoring, sector, macro context, 52-week range), portfolio analytics (/stats command), and operational visibility (error surfacing to Discord). All features are additive — no existing behavior changes — and the signal enrichment features share a single data-fetch boundary that must be centralized to avoid N×M yfinance calls per scan. The only meaningful complexity is /stats, which requires a careful win/loss definition and join logic, and macro context, which introduces a new async fetch boundary.

---

## Feature Analysis

### 1. Confidence Scoring (SIG-04)

**Table stakes behavior:**
The prompt instructs the analyst to output a third line: `CONFIDENCE: <high|medium|low>`. `parse_claude_response` is extended to extract this line when present. The value is stored in a new nullable `confidence` column on the `recommendations` table. The Discord embed displays it as a text badge — either appended to the embed title (`AAPL — BUY [HIGH]`) or as a dedicated inline field named `Confidence` with value `HIGH`. No routing or behavioral change occurs based on confidence; it is display-only.

**Edge cases:**
- LLM omits the CONFIDENCE line entirely. This is the most likely failure mode with small or fallback models that approximate the instruction format. The parser must tolerate absence gracefully: return `None` rather than raise `ValueError`, so a missing confidence line does not invalidate an otherwise valid signal. The existing `parse_claude_response` raises on missing SIGNAL or REASONING — confidence must NOT follow that pattern.
- LLM outputs variant spellings: `HIGH CONFIDENCE`, `High`, `MEDIUM/HIGH`, `confident`. Parser must normalize to `{high, medium, low}` with a case-insensitive strip and treat any unrecognized value as `None`.
- Existing analyst_cache rows (from before this feature ships) will have no confidence value. Cache hits return `{signal, reasoning}` without confidence. The embed must handle `confidence=None` cleanly — omit the badge entirely, do not show "N/A".
- The fallback provider (Gemma) is more likely than the primary to deviate from format. Confidence parse failures on fallback are expected and must not break the signal flow.
- The analyst cache key is SHA-256 of headlines. After SIG-04 ships, old cache entries will not have confidence. A cached response with `confidence=None` is still a valid cache hit — do not bust the cache.

**Design recommendation:**
Add `CONFIDENCE: <high|medium|low>` as an optional fourth output line in the prompt (after REASONING). Parse it with a separate `next(..., None)` scan inside `parse_claude_response` — same pattern already used for SIGNAL and REASONING lines, but with `None` fallback instead of `raise`. Store in a new `confidence TEXT` nullable column on `recommendations` (additive migration). In the embed, display as a field only when non-None. Do not encode confidence into embed color — the existing signal-color mapping (green/yellow/red) is already meaningful and should not be overloaded with a second dimension. Use `HIGH` / `MED` / `LOW` text labels in an inline field for clean readability.

**Complexity:** Low. Parser extension is small. The only non-trivial concern is the backward-compatible handling of cache hits that predate the feature.

---

### 2. Sector Enrichment (SIG-01)

**Table stakes behavior:**
The yfinance `Ticker.info` dict returns a `sector` key for most stocks. The GICS-aligned taxonomy yfinance exposes uses strings like: `"Technology"`, `"Healthcare"`, `"Financial Services"`, `"Consumer Cyclical"`, `"Consumer Defensive"`, `"Industrials"`, `"Basic Materials"`, `"Energy"`, `"Utilities"`, `"Real Estate"`, `"Communication Services"`. The sector string is injected into `build_prompt` and `build_sell_prompt` as a single labeled line in the Fundamentals block: `- Sector: Technology`. For ETFs, sector is not meaningful (ETFs return `None` or a fund category, not a GICS sector) and must be omitted from `build_etf_prompt`.

**Edge cases:**
- `sector` is `None` or missing from `info` for some tickers — newly listed companies, foreign ADRs with incomplete yfinance data, shell companies. Must default to omitting the line rather than inserting `"None"` or `"Unknown"` into the prompt, as a string label with no content adds noise.
- The `info` fetch for `sector` is already made in `fetch_fundamental_info` — the sector value is available in the existing `info` dict at zero additional API cost. No extra yfinance call required.
- ETF path: `build_etf_prompt` currently receives no `info` dict, only `tech_data` and `expense_ratio`. Do not add an `info` fetch to the ETF path for sector alone. Omit sector from ETF prompts entirely.

**Design recommendation:**
Pass `sector = info.get("sector")` from the `info` dict already fetched in `run_scan`. Add `sector: str | None = None` as a parameter to `build_prompt` and `build_sell_prompt`. Insert `- Sector: {sector}` in the Fundamentals block guarded by `if sector`. Do not pass sector to `build_etf_prompt`. No DB schema change needed — sector is a prompt input, not a stored output. This is the simplest of the signal enrichment features and the right one to implement first to prove the prompt injection pattern.

**Complexity:** Low. Zero additional yfinance calls; sector is already in the fetched `info` dict.

---

### 3. Macro Context in Prompts (SIG-02)

**Table stakes behavior:**
Before the per-ticker scan loop begins, fetch SPY close prices for the past 5 trading days and compute the 5-day return as `(close[-1] - close[-6]) / close[-6]`. Fetch `^VIX` last close. Characterize both as human-readable strings and inject them into a `Market Context:` block added to `build_prompt`, `build_sell_prompt`, and `build_etf_prompt`.

SPY trend strings:
- `"SPY up {pct}% over 5 days"` when pct > 0.1%
- `"SPY down {pct}% over 5 days"` when pct < -0.1%
- `"SPY flat over 5 days"` when |pct| <= 0.1%

VIX characterization (operationally standard thresholds):
- VIX < 15: `"VIX: {val} (low volatility)"`
- VIX 15–25: `"VIX: {val} (elevated volatility)"`
- VIX 25–35: `"VIX: {val} (high volatility)"`
- VIX > 35: `"VIX: {val} (extreme volatility)"`

These thresholds are consistent with how volatility regimes are discussed in quantitative finance literature and practitioner usage. VIX < 15 is historically complacent/low-vol; VIX > 30 is broadly accepted as the "fear" threshold.

**Edge cases:**
- SPY or VIX data fetch fails or returns empty (yfinance outage, rate limiting, weekend with stale data). Must NOT abort the scan. The macro context block is fully optional — when the fetch fails, the prompt block is omitted and the scan proceeds without it. This is the most important edge case to handle correctly.
- Both SPY and VIX must be fetched exactly once per scan run, not once per ticker. A per-ticker macro fetch would add ~20–30 extra yfinance calls per scan and reliably trigger rate limits. Store results as scan-level locals, passed as parameters to prompt builders.
- The fetch must be wrapped in `asyncio.to_thread` because it is a blocking yfinance call inside an async function — this is the Phase 8 rule that applies universally to all yfinance I/O in the codebase.
- On weekends and holidays, `yf.download(period="1wk")` returns the most recent available closes, which is correct behavior.

**Design recommendation:**
Add a `fetch_macro_context(spy_ticker, vix_ticker) -> dict` function in `screener/technicals.py` (or a new `screener/macro.py` if the file becomes crowded). Returns `{"spy_trend": str | None, "vix_label": str | None}` — both keys always present, values may be `None` on fetch failure. Called once at the top of `run_scan` and `run_scan_etf` via `asyncio.to_thread`. Add `macro_context: dict | None = None` to all three prompt builders. Insert the `Market Context:` block only when `macro_context` is non-None and has at least one non-None value. This keeps prompt builders pure and testable with mock context dicts.

**Complexity:** Medium. The fetch logic is simple, but the `asyncio.to_thread` placement, the per-scan (not per-ticker) fetch requirement, and the three-prompt injection surface area each require care. Test coverage needs to mock the macro fetch without breaking the 252 existing tests.

---

### 4. 52-Week Range Position (SIG-03)

**Table stakes behavior:**
Compute where the current price falls in the 52-week high/low range as a percentage: `position = (price - low_52w) / (high_52w - low_52w) * 100`. Express in the prompt as a human-readable label with the raw numbers:

- 0–20%: `"near 52-week low"`
- 20–40%: `"in lower half of 52-week range"`
- 40–60%: `"mid-range"`
- 60–80%: `"in upper half of 52-week range"`
- 80–100%: `"near 52-week high"`

Full prompt line: `"- 52-week range: $142.10 – $198.45 (near high, 91%)"`.

yfinance exposes `fiftyTwoWeekHigh` and `fiftyTwoWeekLow` in the `Ticker.info` dict, which is already fetched in `fetch_fundamental_info` for stocks. No additional network call is required for the stock path.

**Edge cases:**
- `high_52w == low_52w`: division by zero on a newly listed ticker or data error. Guard with `if high_52w != low_52w` and omit the line if the range collapses.
- `fiftyTwoWeekHigh` or `fiftyTwoWeekLow` missing from `info`: omit the line. Expected for some ADRs and newly listed tickers.
- ETF path: `build_etf_prompt` does not receive an `info` dict today. Adding a full `info` fetch to the ETF path just for 52-week range is disproportionate. For ETFs, 52-week range requires either: (a) fetching `info` (new call), (b) using `yf.Ticker.fast_info` (new interface not currently used in the codebase), or (c) extending `fetch_technical_data` to use `period="1y"` instead of `period="3mo"`. Option (c) doubles data fetched per ETF. All three options add meaningful new surface area. Recommend: stock path only for v1.2, ETF path deferred.

**Design recommendation:**
Add a `format_52w_range(price, low_52w, high_52w) -> str | None` pure function (returns `None` when data is missing or range is zero). Call it in `build_prompt` and `build_sell_prompt` using values from the `info` dict: `info.get("fiftyTwoWeekHigh")` and `info.get("fiftyTwoWeekLow")`. Add `range_52w: str | None = None` parameter to both builders. Insert the formatted string as a line in the Fundamentals block when non-None. Do not extend `fetch_technical_data` or modify the ETF path for v1.2.

**Complexity:** Low for stocks (data already in `info` dict). Skip ETFs for v1.2.

---

### 5. /stats Discord Command (PORT-02)

**Table stakes behavior:**
The `/stats` slash command queries closed positions and their associated sell trades to produce:
- **Win rate**: `wins / total_closed_positions * 100%`. A win is any closed position where `sell_trade.price > position.avg_cost_usd`.
- **Avg gain %**: mean P&L% across winning positions only. `(sell_price - avg_cost) / avg_cost * 100`.
- **Avg loss %**: mean P&L% across losing positions only (displayed as a negative number).
- **Total closed**: count of all closed positions.

Show two time windows: all-time and rolling 30-day. All-time is the primary metric; 30-day is secondary context. The rolling window is filtered by `t.executed_at >= date('now', '-30 days')` on the sell trade.

**Data join:**
The data lives in `positions` (rows with `status='closed'`) and `trades` (rows with `side='sell'`). The join is:
```sql
SELECT t.price AS exit_price, p.avg_cost_usd, t.executed_at
FROM positions p
JOIN trades t ON t.ticker = p.ticker
WHERE p.status = 'closed' AND t.side = 'sell'
```
This join is correct given the current schema constraint that one closed position has exactly one sell trade.

**Edge cases:**
- No closed trades yet: embed shows `"No closed trades yet."` Do not divide by zero in any win rate or average calculation.
- All trades are wins (avg loss = N/A) or all trades are losses (avg gain = N/A): display `"N/A"` rather than `"0%"` for a category with no members.
- Multiple sell trades for one position: the current schema does not support partial exits — a sell closes the full position. The join above may return duplicate rows if this constraint is violated (e.g., manual DB edits). Add `GROUP BY p.id` or `LIMIT 1` on the sell trade subquery as a safeguard.
- The `positions` table has no `exit_date` column — closed-at time must be inferred from `trades.executed_at` where `side='sell'`. The 30-day window filter applies to `t.executed_at`, not to any position field.
- DRY_RUN trades: in dry-run mode, orders are not placed but positions may or may not be updated depending on implementation. Stats should only reflect actual trades (where `order_id IS NOT NULL` or a dry-run flag exists). Currently there is no dry-run marker on the `trades` table — this is a pre-existing gap, not a new one. Document as a known limitation.

**Design recommendation:**
Add a `get_closed_trade_stats(db_path: str, days: int | None = None) -> dict` function in `database/queries.py`. It returns `{"win_rate": float | None, "avg_gain_pct": float | None, "avg_loss_pct": float | None, "total_closed": int}`. `days=None` means all-time; `days=30` means rolling 30 days. Add `build_stats_embed(all_time: dict, rolling_30: dict) -> discord.Embed` in `discord_bot/embeds.py`. Register `/stats` as a slash command in `discord_bot/bot.py`. No schema additions required — the existing `positions` and `trades` tables contain all needed data.

**Complexity:** Medium. The query requires a careful join, correct handling of the no-trades case, and accurate win/loss categorization. The schema is adequate but requires understanding the exact relationship between `positions`, `trades`, and how the close operation works in the codebase.

---

### 6. Error Surfacing to Discord Ops Channel (OPS-01)

**Table stakes behavior:**
When `run_scan` or `run_scan_etf` encounters an unhandled exception at the scan level (not caught by the per-ticker try/except), post the error to `ops_channel_id` as a minimal red embed before returning. The bot does not crash — the scheduler handler catches, posts, and exits the scan gracefully. Per-ticker exceptions that are already caught and logged locally should NOT be re-posted to Discord unless they represent a hard failure (both primary and fallback analyst providers failing for the same ticker).

Error categories that matter most, in priority order:
1. **Scan-level failures**: `run_scan` fails before or after the ticker loop (DB connection failure, Discord channel lookup failure). These are currently silent — no log entry, no Discord alert, scheduler quietly moves on.
2. **Analyst hard failures**: Both primary and fallback providers raise for the same ticker. Currently logged but not surfaced to Discord.
3. **Unexpected per-ticker exceptions**: `AttributeError`, `KeyError`, `TypeError` from unexpected yfinance return shapes. These appear only in the rotating log file.

The existing zero-recommendation ops alert (v1.0, REL-03) is a business-level condition, not a technical error. It must remain separate and should not be modified.

**Edge cases:**
- The ops channel itself is unavailable (misconfigured `OPS_CHANNEL_ID`, channel deleted, bot lacks permission). The error-posting code must not raise a secondary exception that kills the bot. Wrap the post in `try/except` and fall back to `logger.error`.
- Raw exception messages must not be posted verbatim to Discord. Stack traces from external SDKs occasionally include API key fragments in error context. Post `type(exc).__name__` and `str(exc)[:300]` only. Log the full `traceback.format_exc()` to the file logger.
- Alert storm during yfinance outage: if yfinance is down, every ticker in a 20-ticker scan will raise, producing 20 individual Discord error posts. Implement a per-scan error budget: post at most 3 per-ticker error embeds per scan run, then post one summary message: `"...and N more errors in this scan run"`.
- The per-scan error counter must reset between scan runs. A module-level counter is sufficient — no DB storage needed.

**Design recommendation:**
Create a `post_ops_error(channel, ticker: str | None, exc: Exception) -> None` async helper in `discord_bot/bot.py` or a new `discord_bot/ops.py`. Posts a red embed with `ticker` (if provided), `type(exc).__name__`, and `str(exc)[:300]`. Wrap the main scan body in a top-level `try/except Exception as scan_exc` that calls this helper. For per-ticker error budget: maintain a local `errors_posted = 0` counter inside `run_scan`; increment on each per-ticker error post; stop posting (but continue logging) when `errors_posted >= 3`. Post a summary embed if additional errors occur beyond the budget.

**Complexity:** Low. The structural pattern is straightforward. The error-budget counter (max 3 per scan) adds minimal complexity but prevents the most common operational failure mode during yfinance outages.

---

## Feature Dependencies

```
SIG-01 (sector)       — uses existing info dict, zero new fetches, no dependencies
SIG-03 (52-week range) — uses existing info dict for stocks, no new fetches, no dependencies
SIG-04 (confidence)   — extends parse_claude_response, must be backward-compatible (None on cache hits)
SIG-02 (macro context) — requires new async fetch at scan level, affects all three prompt builders
PORT-02 (/stats)       — depends on existing positions + trades schema, independent of signal features
OPS-01 (error surfacing) — independent of all signal features

Recommended build order for signal enrichment features:
  1. SIG-01 (sector) — proves the prompt injection pattern at lowest risk
  2. SIG-03 (52-week range) — same info dict, same injection pattern
  3. SIG-04 (confidence) — parser change, test cache-miss and cache-hit paths
  4. SIG-02 (macro context) — new fetch, new async boundary, three-prompt injection surface

SIG-04 cache note: existing analyst_cache rows will not have confidence data.
  Cache hits return {signal, reasoning} without confidence. The embed must handle
  confidence=None as "omit badge." Do not bust the cache on SIG-04 ship.

SIG-02 macro context affects build_prompt, build_sell_prompt, and build_etf_prompt.
  All three must be updated in the same changeset to avoid prompt inconsistency.

PORT-02 /stats and OPS-01 error surfacing are fully independent of each other and
  of all signal enrichment features. They can be built in any order.
```

---

## Anti-Features

### Routing or gating behavior based on confidence score
Confidence is a display badge only. Do not use it to suppress low-confidence recommendations, require double-approval for low-confidence signals, or change the embed color based on confidence. LLM confidence self-assessments are not reliably calibrated and should not drive business logic in a human-approval-required system.

### Per-ticker macro context fetch
Fetching SPY and VIX once per ticker inside the scan loop would add ~20–30 extra yfinance calls per scan and reliably trigger rate limits. Macro context must be fetched exactly once per scan run at the scan level, before the ticker loop begins.

### /stats showing open position unrealized P&L
The `/stats` command should reflect realized P&L on closed trades only. Unrealized P&L belongs in `/positions` (PORT-01). Mixing realized and unrealized in one command makes win rate meaningless and confuses performance attribution between "what I've banked" and "what I'm sitting on."

### Full traceback posts to Discord ops channel
Posting `traceback.format_exc()` to Discord is a security risk (stack frames from external SDK errors occasionally include API key fragments or internal paths) and produces unreadable walls of text in a chat channel. Post exception type and message only; full tracebacks belong in the rotating log file.

### 52-week range for ETFs via extended history fetch
Extending `fetch_technical_data` from `period="3mo"` to `period="1y"` to compute 52-week range for ETFs roughly doubles the data fetched per ticker and increases scan time. ETF 52-week range should be deferred to a later milestone or sourced from `info` if already fetched, without changing the history period that all existing technical calculations depend on.

### Rolling 90-day window in /stats
Three time windows (all-time, 30-day, 90-day) in a Discord embed creates a cluttered field layout without proportionate value. A daily-scan bot with a small watchlist accumulates at most ~30 closed positions per month; 30-day rolling plus all-time is the meaningful range for operational review.

### Confidence stored in analyst_cache
The analyst cache key is SHA-256 of headlines. Adding confidence to the cache schema creates complexity around cache invalidation and backward compatibility. Confidence is a per-response output — store it in `recommendations.confidence`, not in `analyst_cache`. The cache hit path simply returns `confidence=None` for pre-SIG-04 entries.
