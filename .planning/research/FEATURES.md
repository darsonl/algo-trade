# Feature Landscape: v1.3 Risk & Signal Quality

**Domain:** Automated Discord-approval trading bot — targeted v1.3 feature additions
**Researched:** 2026-04-18
**Scope:** Limit orders, earnings date warnings, fundamental trend in Claude prompt, /history command

---

## Summary

Four features are targeted for v1.3. All are self-contained additions to the existing codebase with no architectural rewrites. The highest-complexity feature is limit orders because it introduces a new async lifecycle (placed → filled/expired) that the current architecture has no concept of. The other three are low-to-medium complexity enrichments that augment existing data paths without new lifecycle concerns.

All yfinance calls must go through `asyncio.to_thread` per the established ASYNC-01..04 pattern. No new libraries are required for earnings dates, fundamental trend, or trade history. Limit orders require one new `schwab-py` function (`equity_buy_limit`) and an additive DB schema migration for `order_type` and `limit_price` columns on `trades`.

---

## Limit Orders UX

### What it is

A limit buy order places the order at a price no higher than the signal price. It only executes if the market reaches that price. If not filled by end of regular trading (DAY duration), the order expires without creating a position.

### How it works in a Discord-approval workflow

The Approve button path already calls `place_order` and receives an `order_id`. Limit orders slot into this path cleanly with one addition: the order may expire unfilled, requiring a follow-up notification.

**Standard pattern:**

1. User clicks Approve. Bot places limit buy at signal price (or `signal_price * (1 - LIMIT_PRICE_OFFSET_PCT)`). Stores `order_id` + `limit_price` in `trades` row.
2. At the start of the next scan (or a lightweight scheduled check), bot queries `client.get_orders_for_account()` filtering for WORKING status.
3. If a limit buy is still WORKING after market close, bot posts a Discord message: "AAPL limit order unfilled at $182.50. Expired."
4. Mark the `trades` row `status='expired_unfilled'` (new nullable column) and do NOT create a position row.

**Duration: DAY is the only correct choice.** The bot re-evaluates signals daily. A GTC order that fills two days later is a position based on a stale signal — the bot did not reconfirm RSI, fundamentals, or news against the new fill date. DAY duration makes each approval atomic to the session. This is a firm recommendation, not a preference.

### schwab-py support

`equity_buy_limit(ticker, shares, price)` exists in `schwab.orders.equities`. Default duration is DAY. Parameters: symbol, quantity, limit price as float. The `place_order` function in `schwab_client/orders.py` already extracts `order_id` from the Location response header — this path works identically for limit orders. Cancellation uses `client.cancel_order(account_hash, order_id)` (confirmed in schwab-py client docs). Confidence: MEDIUM.

### What happens when a limit order expires unfilled

DAY limit orders on Schwab automatically expire at market close (4 PM ET). The bot does not need to cancel them — they self-expire. The bot only needs to:

1. Detect the expiry at the next scan start by querying order status
2. Post a Discord notification for operator awareness
3. Mark the DB record accordingly

A manual "Cancel" button in the Discord embed adds UI complexity without meaningful operational value — the order will self-expire at market close anyway. Skip cancel-button UX for v1.3.

### Expected behavior specification

| State | Trigger | Bot Action |
|-------|---------|-----------|
| Approved | User clicks Approve | Place limit order; store order_id + limit_price in trades row |
| Filled (full) | Order fill detected at scan start | `upsert_position` as normal; no extra notification |
| Unfilled (DAY expired) | Schwab status = EXPIRED or CANCELLED; detected at scan start | Discord message: "{ticker} limit order expired unfilled at ${limit_price}"; mark trade record |
| DRY_RUN=true | Any approval | Log "DRY_RUN: would place limit buy {ticker} @ ${limit_price}" — existing DRY_RUN path unchanged |

### What NOT to build for limit orders

- Real-time fill tracking via Schwab streaming — adds streaming dependency, out of scope
- GTC duration option — stale-signal fill risk, explicitly excluded
- Cancel button in Discord embed — DAY orders self-expire; cancel button adds async View complexity for no operational gain
- Partial fill handling — `shares` becomes fractional mismatch; reconciliation with `upsert_position` non-trivial; defer to v1.4

### Schema changes needed

Two additive `ALTER TABLE` migrations on `trades`:
- `order_type TEXT DEFAULT 'market'` — identifies limit vs market trades
- `limit_price REAL` — nullable; set only when `order_type = 'limit'`

Both follow the established `initialize_db` migration pattern.

### Complexity

Medium. New `build_limit_buy` / `place_limit_order` in `schwab_client/orders.py`. `USE_LIMIT_BUY` config flag (default true per PROJECT.md). `ApproveRejectView.approve` branches on the flag. Unfilled-order detection at scan start is the trickiest part — requires a new Schwab API call with no existing wrapper.

### Dependency on existing code

- `schwab_client/orders.py` — add `build_limit_buy`, `place_limit_order`
- `config.py` — add `use_limit_buy: bool = True`, `limit_price_offset_pct: float = 0.0`
- `discord_bot/bot.py` — `ApproveRejectView.approve` branches on `config.use_limit_buy`
- `database/models.py` / `database/queries.py` — schema migration + new query to fetch open limit orders by ticker
- `main.py` — `run_scan()` prologue: call `check_unfilled_limit_orders()` before ticker loop

---

## Earnings Date Display

### What information is most useful

In a Discord BUY embed, traders want to know:

1. **Date** — primary concern, especially when earnings are within 1-2 weeks of the signal
2. **EPS estimate** — secondary; frames the risk ("consensus expects $1.87")
3. **Days until** — the most scannable format: "in 6 days" is immediately actionable; a calendar date requires mental arithmetic

What traders do NOT need in this context:
- Historical beat/miss data — Claude's news analysis already covers the most recent earnings report
- EPS actual from prior quarter — redundant with existing `earnings_growth` field
- Before-market-open vs after-market-close timing — useful but not reliably available from yfinance for future dates; omit

**Best display format:** `Apr 29 — in 6 days (est. $1.87)` or `~Apr 28–May 2 (week est.)` when Yahoo only has a range. Apply a visual flag when within 7 calendar days: `!! Apr 29 — IN 6 DAYS (est. $1.87)`.

### yfinance data availability

`ticker.calendar` returns a dict with key `"Earnings Date"` as a list of timestamps (range) or a single timestamp. Also has `"EPS Estimate"` when available. This is the correct field for upcoming earnings — it reflects the next scheduled date, not historical ones.

`ticker.get_earnings_dates(limit=12)` returns a DataFrame with both historical and future dates. Rows where `Reported EPS` is NaN are future estimates. The `EPS Estimate` column contains the forward consensus when available.

Known fragility: yfinance earnings data is scraped from Yahoo Finance and has multiple documented `KeyError: 'Earnings Date'` failures (GitHub issues #2143, #2123, #1932). Treatment: wrap the entire fetch in `try/except`, return `None` on any error. The embed field shows "N/A" and the prompt line is omitted. This matches the existing defensive pattern for all yfinance calls.

Confidence: MEDIUM — field names confirmed from yfinance docs and issue discussions; exact return structure varies per ticker and has broken historically.

### Claude prompt inclusion

Add one line to the Fundamentals block when earnings data is available:

```
- Next Earnings: Apr 29 (est. $1.87, in 6 days)
```

When only a range is known:
```
- Next Earnings: ~Apr 28–May 2
```

Claude will naturally flag proximity risk in its REASONING when earnings are close — no prompt instruction change needed. The model recognizes earnings proximity as a volatility event without explicit prompting.

### Complexity

Low-medium. New `fetch_earnings_info(ticker_obj) -> dict | None` in `screener/earnings.py` (new file, keeps fundamentals.py clean). Wrapped in `asyncio.to_thread` at call site. Returns `{date_str: str, eps_estimate: float | None, days_until: int | None}` or `None` on any exception. Two additive changes: `build_prompt` gains `earnings_info: dict | None = None`; `build_recommendation_embed` gains `earnings_info: dict | None = None`.

### Dependency on existing code

- New `screener/earnings.py` — `fetch_earnings_info` function
- `analyst/claude_analyst.py` — `build_prompt` gains optional `earnings_info` param; adds 1 line to Fundamentals block when non-None
- `discord_bot/embeds.py` — `build_recommendation_embed` gains optional `earnings_info` param; adds 1 inline field when non-None
- `main.py` — `run_scan()` fetches earnings info per ticker via `asyncio.to_thread`; no position in ETF path (ETFs have no meaningful earnings date)

---

## Fundamental Trend in Prompt

### What it is

Instead of point-in-time P/E and earnings growth, give Claude directional context: is P/E expanding or contracting? Is quarterly EPS growing or shrinking?

### How this is typically surfaced to an AI analyst

The most effective format for LLM prompt injection is a pre-computed, labeled trend descriptor — not raw numbers. Giving the model `"P/E expanding (18.2 → 21.4)"` is better than giving it both numbers and asking it to derive direction. This is the same pattern already used for SPY trend (`"bullish"` / `"bearish"` not raw MA values) and VIX (`"low volatility"` not just the number).

**Recommended prompt format:**

```
Fundamental Trend:
- P/E direction: contracting (24.1 trailing → 20.8 forward, earnings growing)
- Quarterly EPS: $0.82, $0.91, $0.95, $1.02 (accelerating)
```

Two to four quarters of EPS is the right depth. More than four adds token cost without meaningfully improving the BUY/HOLD/SKIP decision. Fewer than two provides no trend signal. Four quarters corresponds to one full year of data, which is the standard analyst review window.

The direction label (`expanding`, `contracting`, `stable`) must be pre-computed by the bot. Do not leave direction derivation to the LLM — the model may hallucinate arithmetic or give inconsistent interpretations.

### yfinance data sources

**P/E direction:** yfinance does not provide historical trailing P/E snapshots. Computing true historical P/E requires dividing historical price by historical quarterly EPS — expensive and fragile.

Best available approach: compare `trailingPE` vs `forwardPE` from the existing `info` dict:
- `trailingPE > forwardPE` → earnings expected to grow → "contracting (earnings growing)"
- `trailingPE < forwardPE` → earnings expected to shrink → "expanding (earnings shrinking)"
- Missing either → omit the P/E direction line

This is a free computation on already-fetched `info` data. No extra API calls.

**EPS quarterly trend:** `ticker.quarterly_earnings` (DataFrame with `Earnings` column, last 4 quarters) or `ticker.get_earnings(freq='quarterly')`. Returns actual reported EPS per quarter. Extract the `Earnings` column as a list of the 4 most recent values, oldest first.

yfinance silent-empty-return risk applies here — `quarterly_earnings` may return None or an empty DataFrame for some tickers. Return `None` gracefully.

Confidence: MEDIUM — `quarterly_earnings` attribute confirmed in yfinance reference docs; reliable for S&P 500 stocks, less so for small caps and ETFs. ETF path: skip entirely (ETFs have no meaningful P/E or quarterly EPS).

### Complexity

Low-medium. New `compute_fundamental_trend(info: dict, quarterly_eps: list | None) -> dict | None` pure function in `screener/fundamentals.py`. One new `asyncio.to_thread` call per ticker for `quarterly_earnings`. `build_prompt` gains `fundamental_trend: dict | None = None` — adds a `Fundamental Trend:` block (2-3 lines) when non-None, silently omitted when None. No new embed fields — this is prompt enrichment only.

### Dependency on existing code

- `screener/fundamentals.py` — new `compute_fundamental_trend` helper; `fetch_fundamental_info` may return or new separate call fetches `quarterly_earnings`
- `analyst/claude_analyst.py` — `build_prompt` gains `fundamental_trend` optional param; adds block after existing Fundamentals section
- `main.py` — fetch quarterly earnings per ticker via `asyncio.to_thread`; pass to `analyze_ticker`
- ETF path: no changes — `build_etf_prompt` and `analyze_etf_ticker` unchanged

---

## Trade History Command

### What fields traders most want to see

Based on standard trading journal conventions:

| Field | Why | Source |
|-------|-----|--------|
| Ticker | What was traded | `trades.ticker` |
| Entry date | When position opened | `positions.entry_date` |
| Exit date | When position closed | `trades.executed_at` (side=sell) |
| Entry price | Cost basis | `trades.cost_basis` (on sell row) |
| Exit price | Sell execution price | `trades.price` (on sell row) |
| P&L % | Realized return | computed: (exit - entry) / entry |
| Hold duration | Days held | exit date minus entry date |
| Shares | Position size | `trades.shares` |

Fields to omit: order IDs (internal bookkeeping, not actionable), Claude reasoning text (too verbose for list view), confidence badge (meaningful at signal time, not for reviewing closed trades).

### Sort order

Newest closed trade first (`executed_at DESC`). Universal convention for trade history — operator wants to see recent performance immediately.

### How many to show

20 trades as default. Discord embeds cap at 25 fields. At 1 field per trade (compact multi-line value), 20 trades fits cleanly within the limit. Format:

```
Field name:  AAPL  (Apr 2 → Apr 14, 12 days)
Field value: Entry: $182.50  Exit: $191.30
             P&L: +4.8%  (8 shares)
```

This is 1 Discord field per trade. 20 fields = exactly within the 25-field limit with 5 to spare for a header or summary footer.

### Filtering and pagination

Do NOT implement in v1.3.

- Filtering (by ticker, date range, win/loss) adds slash command option parsing and query branching that duplicates `/stats` aggregate functionality. The use case for filtering `/history` at current scale (daily scans, single operator, ~20 total closed trades) does not exist yet.
- Pagination (prev/next buttons) requires a custom View with button handlers and timeout management — another async interaction path with tests. For a 20-trade hard cap, pagination adds complexity with no operational benefit.

### DB query shape

The `trades` table has `cost_basis` on sell rows (added in v1.2). The `positions` table has `entry_date`. A JOIN between the sell trade and its corresponding closed position gives all needed fields:

```sql
SELECT t.ticker, t.shares, t.cost_basis, t.price AS exit_price,
       t.executed_at, p.entry_date
FROM trades t
JOIN positions p ON p.ticker = t.ticker
WHERE t.side = 'sell'
  AND t.cost_basis IS NOT NULL
ORDER BY t.executed_at DESC
LIMIT 20
```

This join is clean because `positions` has a UNIQUE constraint on `ticker` — one row per ticker regardless of open/closed status. `entry_date` is preserved on closed positions.

New query function: `get_closed_trade_history(db_path: str, limit: int = 20) -> list[sqlite3.Row]`.

### Complexity

Low. New DB query (clean JOIN on existing schema, no schema changes), new `build_history_embed` in `discord_bot/embeds.py`, new `/history` slash command in `discord_bot/bot.py`. Mirrors the `/stats` command pattern exactly.

### Dependency on existing code

- `database/queries.py` — new `get_closed_trade_history` function
- `discord_bot/embeds.py` — new `build_history_embed`
- `discord_bot/bot.py` — new `/history` slash command (same pattern as `/stats`)
- No schema changes

---

## Anti-Features (What NOT to Build)

### GTC (good-till-cancel) limit orders as default or option

The bot re-evaluates signals daily. A GTC fill two days later is a position the bot did not reconfirm against current RSI, fundamentals, or news. The human-approval guarantee weakens because the human approved based on a signal that is now 24-48 hours stale. DAY duration keeps each approval atomic to the trading session.

### Real-time fill tracking via Schwab streaming

Adds a Schwab streaming WebSocket subscription, connection lifecycle management, and reconnect logic — an entirely new async event source. The daily-scan architecture does not need sub-minute fill notification. Poll at scan start instead.

### Cancel button in Discord embed for limit orders

DAY limit orders self-expire at market close. A cancel button adds a new async View class, button handler, and Schwab `cancel_order` integration for the sole benefit of cancelling 2-3 hours early. Not worth the test surface area.

### Partial fill handling for limit orders

If 50 of 100 approved shares fill, position size diverges from the recommendation. Reconciling fractional fills with `upsert_position` (weighted average cost) and the recommendation record requires non-trivial state management. Treat fills as all-or-nothing for v1.3; flag for v1.4.

### Historical P/E trend via price/EPS division

Fetching 1-year price history plus quarterly EPS history to compute historical trailing P/E at each quarter end doubles the per-ticker data fetch. The `trailingPE` vs `forwardPE` comparison delivers 80% of the directional signal for free from the already-fetched `info` dict.

### Earnings BMO/AMC timing

Before-market-open vs after-market-close timing matters for gap risk but is not reliably available from yfinance for future earnings dates. Yahoo Finance often shows "TAS" (time as scheduled) for upcoming earnings. Including an unreliable field creates more confusion than value; omit for v1.3.

### EPS consensus beat/miss history in the embed

Historical beat/miss data is already surfaced by Claude's news analysis if headlines cover the most recent earnings. A separate embed field duplicates information and adds a fragile yfinance scrape with no incremental UX value.

### /history filtering and pagination for v1.3

At current scale (single operator, daily scans, ~20 total closed trades at v1.3 ship time), filtering and pagination have no operational use case. `/stats` covers aggregate analysis. Add only when trade count grows beyond comfortable single-embed display (>25 closed trades).

### Scheduled earnings pre-scan filter (skip tickers with near-term earnings)

Too opinionated. Some traders buy into earnings momentum; others avoid it. The correct UX is surfacing the warning in the embed and prompt and letting the operator decide via Approve/Reject. Automatic suppression removes a human decision the operator should be making.

---

## Feature Dependencies Map

```
Limit Orders
  - schwab_client/orders.py: add build_limit_buy, place_limit_order
  - config.py: USE_LIMIT_BUY flag, LIMIT_PRICE_OFFSET_PCT
  - trades schema migration: order_type, limit_price columns
  - main.py scan prologue: check_unfilled_limit_orders before ticker loop
  - discord_bot/bot.py: ApproveRejectView.approve branches on config.use_limit_buy

Earnings Date Warning
  - New screener/earnings.py: fetch_earnings_info
  - analyst/claude_analyst.py: build_prompt gains earnings_info param
  - discord_bot/embeds.py: build_recommendation_embed gains earnings_info param
  - main.py: asyncio.to_thread fetch per ticker (stock path only)
  - No schema changes

Fundamental Trend
  - screener/fundamentals.py: compute_fundamental_trend helper
  - analyst/claude_analyst.py: build_prompt gains fundamental_trend param
  - main.py: asyncio.to_thread fetch quarterly_earnings per ticker
  - No schema changes, no new embed fields (prompt-only enrichment)
  - ETF path: unchanged

/history Command
  - database/queries.py: get_closed_trade_history (JOIN positions + trades)
  - discord_bot/embeds.py: build_history_embed
  - discord_bot/bot.py: /history slash command
  - No schema changes
```

## MVP Recommendation: Phase Ordering for v1.3

Build in this order to minimize risk:

1. **/history** — pure read path, no execution risk, establishes new embed pattern, validates the JOIN before any schema work
2. **Fundamental Trend** — prompt-only enrichment, uses already-fetched `info` fields for P/E direction, one new `asyncio.to_thread` call for quarterly EPS
3. **Earnings Date Warning** — new yfinance scrape path (fragility risk), isolated behind try/except returning None; small additive changes to embed and prompt
4. **Limit Orders** — execution-path change with new lifecycle concerns; build last so existing features are stable before introducing order state management

---

## Table Stakes vs Differentiators

| Feature | Category | Rationale |
|---------|----------|-----------|
| Limit orders | Differentiator | Market orders are functional; limit orders reduce slippage but add lifecycle complexity |
| Earnings date warning | Table stakes | Any serious equity bot should warn before earnings — missing this creates surprise gap risk |
| Fundamental trend | Differentiator | Point-in-time P/E is already present; trend direction materially improves Claude signal quality |
| /history command | Table stakes | Without trade history, there is no feedback loop; operators expect to review what the bot did |

---

## Sources

- schwab-py order templates (equity_buy_limit, Duration options): [schwab-py order-templates docs](https://github.com/alexgolec/schwab-py/blob/main/docs/order-templates.rst) — MEDIUM confidence
- schwab-py cancel_order, get_orders_for_account: [HTTP Client docs](https://schwab-py.readthedocs.io/en/latest/client.html) — MEDIUM confidence
- yfinance earnings dates (calendar, get_earnings_dates): [yfinance Ticker reference](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_earnings_dates.html) — MEDIUM confidence
- yfinance earnings fragility: GitHub issues #2143, #2123, #1932 on ranaroussi/yfinance — HIGH confidence (multiple documented failures)
- Discord embed field limit (25 fields): Discord API documentation — HIGH confidence
- P/E direction via trailing vs forward PE comparison: standard quantitative finance practice — HIGH confidence
