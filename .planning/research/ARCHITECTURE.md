# Architecture Research — v1.2

## Summary

v1.2 adds seven features to an existing, well-structured pipeline. Most changes are additive: new arguments flow through existing call chains, new prompt sections are injected into existing prompt builders, and one new scheduler job mirrors the existing stock scan job. The only structural addition is a new `screener/macro.py` module; everything else is changes to existing modules. The largest single-file change is `claude_analyst.py` (prompt format + response parser + three prompt builder signatures). No existing module needs a rewrite.

---

## Integration Points

### Feature 1: Macro Context (SPY trend + VIX) — SIG-02

- **Modules changed**: `main.py` (call site + pass-through to analyze functions), `analyst/claude_analyst.py` (`build_prompt`, `build_sell_prompt`, `build_etf_prompt` — new "Market Context" section; `analyze_ticker`, `analyze_sell_ticker`, `analyze_etf_ticker` — new `macro_context` kwarg)
- **New modules**: `screener/macro.py` — pure synchronous function `fetch_macro_context(config) -> dict` returning `{"spy_trend": str, "vix_level": float | None}`. SPY trend is derived from 5d vs 20d moving average of SPY close price using two `yf.download` or `yf.Ticker` calls. VIX level is the most recent close of `^VIX`. Both are standard yfinance calls.
- **Data flow change**: `run_scan()` and `run_scan_etf()` each call `await asyncio.to_thread(fetch_macro_context, config)` once at the top of the function, before the ticker loop. The returned dict is passed into `analyze_ticker`, `analyze_sell_ticker`, and `analyze_etf_ticker` as `macro_context=macro_context`. The prompt builders add a new "Market Context" block: `SPY Trend: {spy_trend} / VIX: {vix_level}`. `fetch_macro_context` must be a sync function; the `asyncio.to_thread` boundary lives in `main.py`, consistent with the project's existing pattern.
- **Build order position**: Early — pure function with no dependency on other v1.2 features. Every other feature touching prompts benefits from stable prompt builder signatures. Bundle with SIG-01/SIG-03 to touch `build_prompt` once.

---

### Feature 2: Sector Fetch — SIG-01

- **Modules changed**: `analyst/claude_analyst.py` (`build_prompt`, `build_sell_prompt` — new `sector` param; ETF prompt omits sector — funds have no single sector), `main.py` (extract `info.get("sector")` after `fetch_fundamental_info` in buy pass; in sell pass, `info` is not currently fetched — add one `fetch_fundamental_info` call or read from the `positions` row if sector is stored there)
- **New modules**: None. `yfinance info["sector"]` is already returned by `fetch_fundamental_info` (which calls `yf_ticker.info`). Zero new I/O for the buy pass.
- **Data flow change**: In `run_scan` buy pass, `sector = info.get("sector")` extracted after the existing `fetch_fundamental_info` call. Passed to `analyze_ticker(... sector=sector)`. In the sell pass, `info` is not currently fetched per position — a lightweight `fetch_fundamental_info` call is needed, wrapped in `asyncio.to_thread`. Alternatively, if the sector is stable (it is for stocks), fetch it once and store in `positions` table as an optional column; retrieve from DB instead of making a live yfinance call on the sell pass. The DB-storage approach eliminates a yfinance call per position per sell scan — recommended given the project's sensitivity to yfinance call count.
- **Build order position**: Early — bundle with SIG-02 and SIG-03 to update prompt builder signatures in a single pass.

---

### Feature 3: 52-Week Range — SIG-03

- **Modules changed**: `analyst/claude_analyst.py` (`build_prompt`, `build_sell_prompt` — new `fifty_two_week_high` and `fifty_two_week_low` params), `main.py` (extract from `info` dict)
- **New modules**: None. `info["fiftyTwoWeekHigh"]` and `info["fiftyTwoWeekLow"]` are already present in the `yf.Ticker.info` dict returned by `fetch_fundamental_info`. Zero new network calls.
- **Data flow change**: In `run_scan` buy pass, `week52_high = info.get("fiftyTwoWeekHigh")` and `week52_low = info.get("fiftyTwoWeekLow")` extracted after the existing `fetch_fundamental_info` call. Passed to `analyze_ticker` as new kwargs. Formatted in `build_prompt` as `52-Week Range: ${week52_low:.2f} – ${week52_high:.2f}` under the Fundamentals section. For the sell pass, same extraction from the `info` dict (requires the sell pass `fetch_fundamental_info` call described in SIG-01).
- **Build order position**: Early — zero new I/O, trivially bundled with SIG-01 and SIG-02.

---

### Feature 4: Confidence Scoring — SIG-04

- **Modules changed**: `analyst/claude_analyst.py` (`build_prompt`, `build_sell_prompt`, `build_etf_prompt` — new third output line in prompt; `parse_claude_response` — extract CONFIDENCE line, add `"confidence"` to returned dict), `discord_bot/embeds.py` (`build_recommendation_embed`, `build_sell_embed`, `build_etf_recommendation_embed` — new `confidence` param, rendered as embed footer), `discord_bot/bot.py` (`send_recommendation`, `send_sell_recommendation`, `send_etf_recommendation` — new `confidence` param passed to embed builders), `main.py` (pass `analysis["confidence"]` through to `send_*` methods)
- **New modules**: None.
- **Data flow change**: Prompts gain a third required output line: `CONFIDENCE: <high|medium|low>`. `parse_claude_response` is extended to find a line starting with `CONFIDENCE:`, parse the value, and return it as `result["confidence"]`. If the line is absent (cache hit from pre-v1.2 prompt format), default to `"medium"`. The `analysis` dict returned by `analyze_ticker` now carries `{"signal", "reasoning", "confidence", "provider_used"}`. `run_scan` passes `confidence=analysis.get("confidence", "medium")` to `bot.send_recommendation`, which passes it to `build_recommendation_embed`, where it renders as `embed.set_footer(text=f"Confidence: {confidence.upper()}")`. The analyst cache currently stores only `signal` and `reasoning` — cache hits will not have a stored confidence value. Do not add a cache column for v1.2; defaulting cache hits to "medium" is acceptable. The prompt hash includes macro/sector/52-week content that changes with v1.2, so most pre-v1.2 cache entries will be cache misses on first run anyway (different prompt → different headlines hash key is unchanged, but prompt content changes do not affect the cache key — see Notes section).
- **Build order position**: Mid — depends on prompt builder signatures being stable (SIG-01/02/03 merged first). Touching `parse_claude_response` and all three embed functions is the largest cross-module change in v1.2.

---

### Feature 5: ETF Scheduled Scan — ETF-07

- **Modules changed**: `main.py` (`on_ready` — register second APScheduler job for `run_scan_etf`; or extend `configure_scheduler` to accept an optional etf job function), `config.py` (new `etf_scan_times: list` field parsed from `ETF_SCAN_TIMES` env var, defaulting to stock scan time + 30 minutes)
- **New modules**: None.
- **Data flow change**: Currently `configure_scheduler` registers only the stock scan. A second `configure_etf_scheduler(scheduler, config, etf_job_fn)` function (mirroring `configure_scheduler`) is the cleanest approach — it keeps both functions pure and independently testable. `on_ready` calls both. The 30-minute offset between stock scan and ETF scan prevents simultaneous yfinance + analyst API load. Note: the v1.1 ARCHITECTURE.md recommended a combined `run_full_scan` wrapper to avoid two independent scheduler jobs and quota contention. That recommendation still applies — a combined job is preferable to two independent jobs if the sequential ordering matters for quota accounting. However, if independent manual triggering via `/scan` and `/scan_etf` must also work, the current design (separate `_scan_callback` and `_scan_etf_callback`) already supports that correctly. The scheduled job can call both in sequence from a single lambda.
- **Build order position**: Mid — standalone change with no dependency on signal enrichment features. Can be developed in parallel with SIG features on a separate branch.

---

### Feature 6: /stats Command — PORT-02

- **Modules changed**: `discord_bot/bot.py` (new `_stats_command` registered in `setup_hook`), `discord_bot/embeds.py` (new `build_stats_embed` function), `database/queries.py` (new `get_trade_stats(db_path) -> dict` function)
- **New modules**: None.
- **Data flow change**: `get_trade_stats` queries existing `trades` and `positions` tables. The query joins sell-side trades (`trades WHERE side='sell'`) to their corresponding position rows (`positions WHERE status='closed' AND ticker=trades.ticker`) to recover `avg_cost_usd` as cost basis. It returns `{"total_closed": int, "win_count": int, "loss_count": int, "win_rate": float, "avg_gain_pct": float | None, "avg_loss_pct": float | None}`. Win is defined as `trade.price > position.avg_cost_usd`. The `positions` table retains closed rows with `status='closed'` and `avg_cost_usd` intact (verified: `close_position` in queries.py only sets `status='closed'`; the cost basis survives). No new DB tables or columns required — all data is already present.
- **Build order position**: Late — fully independent of all signal enrichment features. Requires only existing DB schema. Can be built last without blocking anything.

---

### Feature 7: Error Surfacing to Ops Channel — OPS-01 + ETF-08

- **Modules changed**: `main.py` (`run_scan` — wrap full scan body in outer `try/except Exception` that calls `await bot.send_ops_alert`; `run_scan_etf` — same outer guard), `discord_bot/bot.py` — `send_ops_alert` already exists but posts to `config.discord_channel_id`. No separate ops channel exists today. If a separate ops channel is desired, add `discord_ops_channel_id: int` to `Config` with a fallback to `discord_channel_id`. ETF-08: one-line change in `run_scan_etf` — change `"ETF scan complete: 0 recommendations posted."` to `"[ETF] ETF scan complete: 0 recommendations posted."`.
- **New modules**: None.
- **Data flow change**: Currently, per-ticker exceptions are caught with `continue` but scan-level exceptions (e.g., DB schema error, `partition_watchlist` hard failure) propagate through the APScheduler thread and are silently dropped by the scheduler. The outer `try/except` in `run_scan` wraps everything after `expire_stale_recommendations` and before the sell pass. The sell pass gets its own outer guard or is included in the same wrapper. `run_scan_etf` already handles `sqlite3.OperationalError` with an early return and ops alert — extend this to a general `except Exception` wrapper. `send_ops_alert` is already implemented and available on `bot`.
- **Build order position**: First — lowest risk, highest dev-time value. Wrap both scan functions before any other work so errors during v1.2 development are surfaced to Discord rather than silently lost in scheduler threads.

---

## Suggested Build Order

1. **OPS-01 + ETF-08** (first): Outer exception wrapper on `run_scan` and `run_scan_etf`. One-line `[ETF]` prefix fix. No dependencies, immediately improves error visibility for all subsequent feature work.

2. **SIG-01 + SIG-02 + SIG-03** (together): Sector, macro context, and 52-week range all extend the same three prompt builder signatures in `claude_analyst.py` and the same `analyze_ticker` / `analyze_sell_ticker` / `analyze_etf_ticker` call sites in `main.py`. Touching these signatures three separate times creates unnecessary merge complexity. New `screener/macro.py` module is the only new file introduced here.

3. **SIG-04** (after step 2): Confidence scoring requires the prompt builders to be in their final signature state before adding the third output line. Updates `parse_claude_response`, all three embed builders, and the three `send_*` methods in `bot.py`.

4. **ETF-07** (after step 3, or parallel branch): Second APScheduler job. Independent of signal enrichment. Add `etf_scan_times` to `Config`. Extend `configure_scheduler` or add `configure_etf_scheduler`.

5. **PORT-01** (total unrealized P&L in `/positions`): Aggregate sum across position summaries in `build_positions_embed`. Minor embed change, no new DB query needed — sum the per-position P&L values already computed by `get_position_summary`.

6. **PORT-02** (`/stats` command): New query, new embed, new slash command. All data already in DB. No dependencies on steps 2–4.

---

## Schema Changes

**No new tables required for v1.2.**

**Optional additive columns:**

`positions` table — if storing sector to avoid a live yfinance call on the sell pass (SIG-01):
```sql
ALTER TABLE positions ADD COLUMN sector TEXT DEFAULT NULL;
```
Written at position open time from `info.get("sector")`. Eliminates one `fetch_fundamental_info` call per position per sell-pass scan run. Recommended if the sell pass sector fetch proves noisy on yfinance (common for yfinance silent-empty returns on small caps).

`recommendations` table — if storing confidence for future analytics (SIG-04):
```sql
ALTER TABLE recommendations ADD COLUMN confidence TEXT DEFAULT 'medium';
```
Not required for v1.2 embed display (confidence is embed-only). Useful for future `/stats` extensions (e.g., win rate by confidence tier). Mark as deferred unless the roadmap explicitly calls for it.

`analyst_cache` table — do NOT add a confidence column for v1.2. Cache hits default to "medium" sentinel. The cost of a wrong confidence badge on a cache hit is low; the complexity of cache versioning is not worth it for this release.

**Config additions** (`.env` fields, not DB):
- `ETF_SCAN_TIMES` — comma-separated HH:MM list for ETF scheduled scan. Defaults to stock scan time + 30 minutes.
- `DISCORD_OPS_CHANNEL_ID` — optional separate channel for ops alerts. Defaults to `DISCORD_CHANNEL_ID` if unset.

---

## Notes on Existing Patterns to Preserve

**asyncio.to_thread discipline**: `fetch_macro_context` must be synchronous; the thread boundary lives in `main.py`. This is consistent with all other screener functions (`fetch_fundamental_info`, `fetch_technical_data`, etc.). Do not add an async version to `macro.py`.

**analyst cache key**: The cache key is `(ticker, headline_hash)`. The prompt content (sector, macro, 52-week) does not factor into the key — a cache hit from a pre-v1.2 entry will be used even though the enriched prompt would have produced a different response. This is a known limitation. On v1.2 first deployment, truncating `analyst_cache` ensures all responses are generated with the new prompt. Document this as a deployment step.

**configure_scheduler purity**: Keep `configure_scheduler` a pure function. Do not read `config` inside it beyond what is already passed. For the ETF scheduler, add a parallel `configure_etf_scheduler(scheduler, config, etf_job_fn)` rather than adding optional parameters to the existing function. Both functions remain independently testable.

**Quota guard duplication**: The quota guard block (check primary + fallback count, skip if exhausted) appears three times in `main.py` (buy pass, sell pass, ETF pass). v1.2 does not add a fourth. A `_quota_exhausted(config, db_path) -> bool` helper extraction is a worthwhile cleanup but not required for correctness.

**send_ops_alert channel routing**: `send_ops_alert` currently posts to `config.discord_channel_id`. If `DISCORD_OPS_CHANNEL_ID` is added to Config, update `send_ops_alert` to use `config.discord_ops_channel_id` with a fallback to `config.discord_channel_id`. Existing deployments without the new env var will see no behavior change.
