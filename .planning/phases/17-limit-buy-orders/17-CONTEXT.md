# Phase 17: Limit Buy Orders - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Replace market buy execution with limit buy at the scan-time price. When `USE_LIMIT_BUY=true`, Approve calls `equity_buy_limit` (GTC) instead of `equity_buy_market`. The `trades` table gains `limit_price` and `order_type` columns for audit trail. The BUY embed Price field gains an "as of HH:MM" staleness timestamp so the operator can assess price age before approving.

> **[SUPERSEDED 2026-06-06]** The default was reversed: `USE_LIMIT_BUY` now defaults to `false`, so limit-order execution is **opt-in** until the live limit-order UAT (see VERIFICATION) passes. The phase goal "every approved BUY places a limit order" therefore now holds only when the operator explicitly enables the flag; the out-of-the-box path remains a market order. See D-02 below.

Scope excludes: sell path, ETF scan path, confirmation on GTC unfill, any cancel/expiry notification (deferred to REQUIREMENTS.md Future Requirements).

</domain>

<decisions>
## Implementation Decisions

### Config

- **D-01:** Add `use_limit_buy: bool = os.getenv("USE_LIMIT_BUY", "true").lower() == "true"` to the `Config` dataclass — follows the `dry_run` / `paper_trading` pattern exactly. *(Default later reversed to `"false"` — see D-02.)*
- **D-02:** ~~`USE_LIMIT_BUY` defaults to `true` — operator must explicitly opt out via `.env`.~~ **[SUPERSEDED 2026-06-06]** `USE_LIMIT_BUY` now defaults to `false` — operator must explicitly opt **in** via `.env`. Rationale: keep the conservative market-order path as the out-of-the-box default until the live limit-order UAT is confirmed against the Schwab broker endpoint; limit execution is enabled deliberately rather than silently on first run.

### Order Execution

- **D-03:** `equity_buy_limit` price must be a formatted string `f"{price:.2f}"` — raw float triggers DeprecationWarning in schwab-py today, future TypeError.
- **D-04:** Limit order duration defaults to GTC — DAY + late-afternoon approval = silently unfilled order that leaves DB recommendation stuck in "approved".
- **D-05:** When `USE_LIMIT_BUY=false`, the existing `place_order` (market) path is used unchanged — no behavior change except config routing.
- **D-06:** When `DRY_RUN=true`, no real order is placed regardless of `USE_LIMIT_BUY`. Dry run always records `order_type='market'` and `limit_price=None` in the trades row — avoids labeling a non-executed intent as "limit" in the audit trail.

### Embed — Staleness Timestamp

- **D-07:** The Price field value is extended with a newline + "as of HH:MM" — e.g.:
  ```
  $52.34
  as of 09:05
  ```
  This reuses the existing Price inline field; no extra embed slot consumed.
- **D-08:** The timestamp is **always shown** regardless of `USE_LIMIT_BUY` — operator always knows how stale the price is, even in market order mode. Simpler embed code (no config-conditional formatting).
- **D-09:** HH:MM is 24-hour local time at the moment the recommendation is created (captured in `main.py` buy-scan loop, same layer as `earnings_date` extraction). Pass as `scan_time: str` kwarg to `build_recommendation_embed` and `send_recommendation`.

### Approval Confirmation Message

- **D-10:** When `USE_LIMIT_BUY=true` and `DRY_RUN=false`, confirmation message reads:
  ```
  Approved: buying 9 shares of AAPL at $52.34 (limit, GTC).
  ```
- **D-11:** When `USE_LIMIT_BUY=false` or `DRY_RUN=true`, confirmation message stays unchanged:
  ```
  [DRY RUN] Approved: buying 9 shares of AAPL at $52.34.
  ```
  (Market fallback keeps the existing wording — no parenthetical added.)

### Database Migration

- **D-12:** Add `limit_price REAL` and `order_type TEXT` columns to `trades` in `initialize_db` — both in the `CREATE TABLE IF NOT EXISTS` (for fresh DBs) and in individual `ALTER TABLE` try/except blocks (for existing DBs). Follows the additive migration pattern used for `side`, `cost_basis`, `confidence`.
- **D-13:** `order_type` stores `'limit'` or `'market'` (lowercase) — consistent with the lowercase string convention used for `signal`, `side`, `confidence`.

### Claude's Discretion

- Whether to add a `build_limit_buy` function alongside `build_market_buy` in `orders.py`, or extend `place_order` with an optional `limit_price` param — either is clean; planner picks the better fit.
- Exact import: `from schwab.orders.equities import equity_buy_limit` — verify function name against schwab-py docs before planning.
- Test strategy: unit tests for `build_limit_buy` / `place_limit_order`, config flag routing, and embed price-field format with `scan_time` kwarg.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Core files to modify

- `schwab_client/orders.py` — add `equity_buy_limit` builder + `place_limit_order` (or extend `place_order`) with GTC duration
- `config.py` — add `use_limit_buy: bool` field following `dry_run` pattern
- `discord_bot/bot.py` — `ApproveRejectView.approve`: route to limit vs market based on `config.use_limit_buy`; update confirmation message; accept `scan_time` on `ApproveRejectView.__init__`
- `discord_bot/bot.py` — `TradingBot.send_recommendation`: accept + pass `scan_time` kwarg to view and embed
- `discord_bot/embeds.py` — `build_recommendation_embed`: accept `scan_time: str | None = None`; append `\nas of HH:MM` to Price field value
- `database/models.py` — `initialize_db`: add `limit_price REAL` and `order_type TEXT` to `trades` CREATE TABLE + ALTER TABLE migration blocks
- `database/queries.py` — `create_trade`: accept `limit_price: float | None` and `order_type: str` params
- `main.py` — capture `scan_time = datetime.now().strftime("%H:%M")` in buy-scan loop; pass to `send_recommendation` and store for Approve flow

### Test files to update

- `tests/test_discord_buttons.py` — `ApproveRejectView.approve` with `use_limit_buy=True` (limit path) and `use_limit_buy=False` (market path)
- `tests/test_discord_embeds.py` — Price field with `scan_time` present/absent
- `tests/test_schwab_orders.py` (create if absent) — `build_limit_buy` correctness, price string formatting
- `tests/test_analyst_claude.py` — no changes expected (analyst is unaffected by order type)

### Reference implementations (read for patterns)

- `config.py` lines 36–37 — `dry_run` and `paper_trading` bool-from-env pattern — `use_limit_buy` follows exactly
- `schwab_client/orders.py` `build_market_buy` / `place_order` — limit buy mirrors this structure
- `discord_bot/bot.py` lines 46–92 — `ApproveRejectView.approve` full flow — limit routing slot is after `if not self.config.dry_run:`
- `database/models.py` lines 76–119 — additive migration pattern for new columns — `limit_price` and `order_type` follow this exactly
- `discord_bot/embeds.py` lines 43–44 — Phase 16 `earnings_date` optional field — `scan_time` follows same optional-kwarg approach (but modifies an existing field value rather than adding a new field)

### Requirements

- `RISK-01` in `.planning/REQUIREMENTS.md` — limit buy on Approve
- `RISK-02` in `.planning/REQUIREMENTS.md` — `USE_LIMIT_BUY` config flag
- `RISK-03` in `.planning/REQUIREMENTS.md` — `limit_price`/`order_type` in trades table
- `RISK-04` in `.planning/REQUIREMENTS.md` — scan-time price with staleness timestamp in embed

### Success criteria (locked — do not renegotiate)

- SC-1: Approving a BUY calls `equity_buy_limit` with price as formatted string when `USE_LIMIT_BUY=true`
- SC-2: `USE_LIMIT_BUY=false` falls back to existing market order path
- SC-3: Trades table records `limit_price` and `order_type` for every executed buy
- SC-4: BUY embed Price field shows scan-time price with "as of HH:MM" timestamp
- SC-5: Limit orders use GTC duration — not DAY

### Prior research decisions (locked — from STATE.md v1.3 pre-phase)

- `equity_buy_limit` price must be formatted string `f"{price:.2f}"` — raw float triggers DeprecationWarning
- Limit duration defaults to GTC — DAY + late approval = silent unfill that locks DB recommendation
- `USE_LIMIT_BUY` must use `.lower() == "true"` Config pattern — `bool("false")` is `True` in Python

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- `build_market_buy(ticker, shares)` in `orders.py` — exact template for `build_limit_buy(ticker, shares, limit_price_str)`
- `place_order(ticker, shares, config, client=None)` — limit variant mirrors this signature
- `ApproveRejectView.__init__(rec_id, ticker, price, config)` — needs `scan_time: str` added as 5th param
- `dry_run` config bool pattern (line 37 config.py) — `use_limit_buy` is identical in structure

### Established Patterns

- All optional embed params use keyword args with `None` default — `scan_time: str | None = None` follows this
- `initialize_db` additive migration: each new column gets its own `try: ALTER TABLE / except OperationalError: pass` block
- `create_trade` in `database/queries.py` is the single write path for trades — extend there, not at call sites
- `queries.create_trade(...)` is called only from `ApproveRejectView.approve` for buys — single change point

### Integration Points

- `bot.py` line 74: `place_order(self.ticker, shares, self.config)` — this becomes `place_limit_order(...)` when `config.use_limit_buy`
- `bot.py` line 76–83: `queries.create_trade(...)` — add `limit_price` and `order_type` kwargs here
- `bot.py` line 275: `ApproveRejectView(rec_id, ticker, price, self.config)` — add `scan_time` param
- `main.py` buy-scan loop — add `scan_time = datetime.now().strftime("%H:%M")` before the ticker loop, pass to `send_recommendation`

</code_context>

<specifics>
## Specific Ideas

- Price field value with timestamp:
  ```
  $52.34
  as of 09:05
  ```
  Implemented as `f"${price:.2f}\nas of {scan_time}"` when `scan_time is not None`, else `f"${price:.2f}"`.

- Confirmation message variants:
  - Limit live: `"Approved: buying 9 shares of AAPL at $52.34 (limit, GTC)."`
  - Market live: `"Approved: buying 9 shares of AAPL at $52.34."`
  - Dry run (either mode): `"[DRY RUN] Approved: buying 9 shares of AAPL at $52.34."`

- Trades row values:
  - Limit + live: `order_type='limit'`, `limit_price=52.34`
  - Market + live: `order_type='market'`, `limit_price=None`
  - Dry run: `order_type='market'`, `limit_price=None`, `order_id=None`

</specifics>

<deferred>
## Deferred Ideas

- GTC unfill detection — scan re-runs could check for open GTC orders and notify operator; out of scope for Phase 17 (listed in REQUIREMENTS.md Future Requirements)
- Cancel button in Discord for GTC orders — out of scope (REQUIREMENTS.md Out of Scope)
- Limit order for sell path — sell uses market order; no change requested
- Real-time fill status polling via Schwab API — out of scope per PROJECT.md

</deferred>

---

*Phase: 17-limit-buy-orders*
*Context gathered: 2026-05-17*
