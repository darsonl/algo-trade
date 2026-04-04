# Phase 6: Sell Signals & Sell Orders - Context

**Gathered:** 2026-04-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Complete the trading loop — the bot evaluates open positions for exit signals and posts SELL
recommendations with the same human-approval flow as buys. Scope is SELL-01 through SELL-09.

Stop-loss / take-profit auto-triggers (SELL-10/11/12) are explicitly deferred to a later phase.

</domain>

<decisions>
## Implementation Decisions

### Sell Trigger Gates
- **D-01:** Three-stage gate — RSI check first (cheap), then MACD confirmation (still cheap), then analyst call.
  A SELL recommendation is only posted if **all three** conditions are true:
  1. `rsi > SELL_RSI_THRESHOLD` (overbought check via `screener/exit_signals.py`)
  2. MACD bearish confirmation: `macd_line < signal_line` (i.e., MACD histogram < 0) — prevents false sell signals when RSI is temporarily elevated but trend is still bullish
  3. `analyze_sell_ticker` returns `SELL`
  Rationale: RSI alone fires too often in strong uptrends. MACD confirmation filters out overbought-but-still-rising positions.
- **D-02:** Separate `SELL_RSI_THRESHOLD` config field in `.env` — distinct from `MAX_RSI` (buy filter).
  Typical value will be higher (e.g. 70 for overbought) vs buy threshold (e.g. 55).
- **D-10:** MACD parameters are fixed: fast=12, slow=26, signal=9 (standard). No config fields — these
  are industry-standard values unlikely to need tuning. `compute_macd` lives in `screener/technicals.py`
  alongside `compute_rsi`. `fetch_technical_data` returns `macd_line`, `signal_line`, `macd_histogram`
  in addition to existing fields. Minimum data requirement: 26 bars (slow EMA period); MACD fields
  are `None` if insufficient data, causing `check_exit_signals` to return `False` (fail-safe).
- **D-11:** Daily analyst quota is tracked in the DB (`analyst_calls` table: `date TEXT, provider TEXT,
  count INT, PRIMARY KEY (date, provider)`). Each provider has its own independent counter — primary
  and fallback are different models with separate rate limits and must not share a counter.
  A single config field `ANALYST_DAILY_LIMIT` (default `18`) is applied independently per provider.
  To know which provider actually responded (primary vs fallback), `analyze_ticker` and
  `analyze_sell_ticker` include a `"provider_used"` key in their return dict (value is the provider
  name string, e.g. `"gemini"` or `"openrouter"`). `run_scan` uses this to increment the correct
  provider's counter. Pre-call quota check uses `config.analyst_provider`'s current count; if
  primary is exhausted the call still proceeds (fallback may have quota), and the returned
  `provider_used` determines which counter is incremented post-call. If both providers are
  exhausted the analyst function raises, `run_scan` catches it, logs a warning, and skips the ticker
  without incrementing either counter.
- **D-03:** Reuse the **same `analyze_ticker` function + fallback mechanism** from Phase 2.5 for sell
  analysis. Do NOT duplicate `_call_api` or fallback logic. The sell prompt is a different prompt
  string fed into the same pipeline. `ANALYST_CALL_DELAY_S` applies to sell calls too.

### Post-Rejection Behavior
- **D-04:** After a SELL recommendation is rejected, the position gets `sell_blocked = True`.
  The position will NOT generate another SELL rec until RSI drops back below `SELL_RSI_THRESHOLD`
  in a subsequent daily scan, at which point `sell_blocked` is reset to `False`.
- **D-05:** `sell_blocked` is a new `BOOLEAN` column on the `positions` table (default `False`).
  Schema migration: `ALTER TABLE positions ADD COLUMN sell_blocked BOOLEAN DEFAULT 0`.
- **D-06:** The daily sell scan checks `sell_blocked` before the RSI check — blocked positions are
  skipped entirely. RSI is only fetched if needed (not pre-fetching for blocked positions).

### Sell Prompt Richness
- **D-07:** The sell prompt includes **full position context** alongside headlines:
  - Entry price (`avg_cost_usd` from positions table)
  - Current price (from technical data)
  - P&L % (computed from entry vs current)
  - Hold duration in days (current date minus `entry_date`)
  - RSI value (already computed for the exit signal check — reuse it)
  - Up to 5 recent headlines (same as buy flow)
- **D-08:** Signal is **SELL/HOLD binary**. Parser expects "SELL" or "HOLD" (same parse_signal logic
  as buy flow — no new parser needed, just different valid tokens).

### Stop-Loss / Take-Profit
- **D-09:** SELL-10, SELL-11, SELL-12 are **deferred** — not part of Phase 6.
  Phase 6 scope is SELL-01 to SELL-09 only.

### Claude's Discretion
- Whether `build_sell_prompt` is a standalone function in `claude_analyst.py` or a different
  module — planner decides based on code organisation.
- Exact wording / structure of the sell prompt text.
- `SellApproveRejectView` button styling (color, emoji) — follow existing `ApproveRejectView` pattern.
- Whether `analyze_sell_ticker` is a thin wrapper calling `analyze_ticker` with a sell prompt,
  or whether `analyze_ticker` is refactored to accept a prompt-builder callable.
- `check_exit_signals` implementation details in `screener/exit_signals.py`.

</decisions>

<specifics>
## Specific Ideas

- User explicitly asked to reuse the same Gemini-backed `analyze_ticker` + fallback pipeline —
  no new API client, no new rate-limit logic. The sell analyst is just a different prompt.
- `ANALYST_CALL_DELAY_S` must be respected for sell analyst calls to stay within Gemini free-tier
  (5 RPM / 20 RPD). The combined buy + sell call count per scan matters.
- `sell_blocked` reset happens in the scan loop itself when RSI is computed — no separate job.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Sell Phase Scope
- `.planning/ROADMAP.md` §Phase 6 — deliverables SELL-01 to SELL-09
- `.planning/REQUIREMENTS.md` §SELL-01 to SELL-09 — acceptance criteria for each requirement

### Existing Patterns to Mirror
- `discord_bot/bot.py` — `ApproveRejectView` buy approve/reject pattern; `TradingBot.setup_hook` for slash command registration
- `discord_bot/embeds.py` — `build_recommendation_embed` buy embed to mirror for sell (add entry_price, current_price, pnl_pct fields)
- `analyst/claude_analyst.py` — `analyze_ticker`, `_call_api`, `_wait_for_retry`, fallback logic — MUST NOT be duplicated
- `screener/technicals.py` — `compute_rsi`, `fetch_technical_data` — reuse for exit signal check
- `schwab_client/orders.py` — `build_market_buy`, `place_order` — pattern for sell counterpart
- `database/queries.py` — `create_trade`, `close_position`, `get_open_positions`, `upsert_position`
- `database/models.py` — positions table schema; trades table (no `side` column currently — needs addition)

### Configuration
- `config.py` — Config dataclass; `SELL_RSI_THRESHOLD` must be added as a new field with default (e.g. 70). No MACD config fields needed (per D-10 — fixed parameters). `ANALYST_DAILY_LIMIT` must be added (default 18, per D-11).
- `database/models.py` — `analyst_calls` table: `date TEXT PRIMARY KEY, count INT NOT NULL DEFAULT 0`. Must be created in `initialize_db` and not require migration (new table, not a column addition).
- `database/queries.py` — `get_analyst_call_count_today(db_path)` and `increment_analyst_call_count(db_path)` for daily quota tracking (per D-11).
- `.env.example` — must document `SELL_RSI_THRESHOLD`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `compute_rsi` / `fetch_technical_data` (technicals.py): already computes RSI. `compute_macd` will be added here alongside it. `fetch_technical_data` will be extended to return `macd_line`, `signal_line`, `macd_histogram`. `check_exit_signals` evaluates both RSI and MACD gates (per D-01, D-10).
- `analyze_ticker` + fallback (claude_analyst.py): sell analyst reuses this entirely. Only difference is the prompt string fed in.
- `ApproveRejectView` (bot.py): `SellApproveRejectView` mirrors this — same button pattern, different callback (calls `place_sell_order`, calls `close_position`, creates trade with `side='sell'`).
- `build_recommendation_embed` (embeds.py): sell embed extends this with entry_price, current_price, pnl_pct fields and red color.
- `get_open_positions` / `close_position` / `has_open_position` (queries.py): Phase 5 already built all DB helpers needed for sell flow.
- `build_market_buy` / `place_order` (orders.py): sell counterpart `build_market_sell` + `place_sell_order` follows identical structure.

### Schema Changes Required
- `positions` table: add `sell_blocked BOOLEAN DEFAULT 0`
- `trades` table: add `side TEXT DEFAULT 'buy'` (distinguish buy vs sell records)
  Migration: `ALTER TABLE trades ADD COLUMN side TEXT DEFAULT 'buy'`

### Integration Point
- `run_scan()` in `main.py`: after the buy pass completes, iterate `get_open_positions()` for the sell pass.

</code_context>

<deferred>
## Deferred Ideas

- **Stop-loss auto-trigger** (SELL-10): Auto SELL rec when P&L < -STOP_LOSS_PCT. Deferred.
- **Take-profit auto-trigger** (SELL-11): Auto SELL rec when P&L > TAKE_PROFIT_PCT. Deferred.
- **STOP_LOSS_PCT / TAKE_PROFIT_PCT config** (SELL-12): .env fields for above triggers. Deferred.

</deferred>

---

*Phase: 06-sell-signals-sell-orders*
*Context gathered: 2026-04-04 via /gsd:discuss-phase*
