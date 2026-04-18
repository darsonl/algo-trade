# Stack Research — v1.3 Risk & Signal Quality

**Project:** Algo Trade Bot — v1.3
**Researched:** 2026-04-18
**Scope:** Limit orders, earnings date awareness, EPS quarterly trend, /history command.
Existing validated stack (Python, discord.py, yfinance 1.2.0, anthropic SDK, schwab-py 1.5.1,
APScheduler, SQLite) is not re-litigated.

---

## Summary

No new pip packages are required for any of the four v1.3 features. All capabilities are present
in the installed versions of `schwab-py` (1.5.1) and `yfinance` (1.2.0). The work is entirely
integration — new function calls into existing libraries with data-shape awareness.

`equity_buy_limit` is a near-identical drop-in for `equity_buy_market` with one extra `price`
argument. The only gotcha is float serialisation: always call `round(limit_price, 2)` before
passing to schwab-py.

For yfinance earnings data, `ticker.calendar` and `ticker.earnings_history` are both separate
HTTP calls not folded into `ticker.info`, so each requires its own `asyncio.to_thread` wrap.
However, the earnings date needed for the Discord embed warning can be derived from
`info['earningsTimestamp']` — a field already in the `ticker.info` call that
`fetch_fundamental_info` already makes — at zero extra cost.

`ticker.quarterly_earnings` returns `None` under yfinance 1.2.0 and must not be used. The correct
EPS trend source is `ticker.earnings_history` (DataFrame, 4 rows, `epsActual` column).

PE direction (expanding/contracting) for the Claude prompt requires no extra network call —
`trailingPE` and `forwardPE` are both present in the already-fetched `ticker.info`.

The `/history` command needs no new library — only one new DB query and a Discord embed, both
using patterns already established in the codebase.

---

## New Dependencies

**None.** Do not add any packages.

Confirmed installed versions with verified API surface:

| Library | Installed | v1.3 Surface Needed | Status |
|---------|-----------|-------------------|--------|
| schwab-py | 1.5.1 | `equity_buy_limit(symbol, quantity, price)` | Verified present |
| yfinance | 1.2.0 | `ticker.calendar`, `ticker.earnings_history`, `info['earningsTimestamp']`, `info['forwardPE']` | Verified present |
| discord.py | (existing) | New `/history` slash command via existing `app_commands` pattern | No change |
| sqlite3 | stdlib | New `get_recent_closed_trades` query | No change |

---

## API Details

### schwab-py 1.5.1 — Limit Buy Order

**Import (add alongside existing imports in `orders.py`):**
```python
from schwab.orders.equities import equity_buy_market, equity_buy_limit, equity_sell_market
```

**Signature:**
```python
equity_buy_limit(symbol: str, quantity: int, price: float) -> OrderBuilder
```

**Built JSON spec — diff from market order** (two fields change, all others identical):
```json
{
  "session": "NORMAL",
  "duration": "DAY",
  "orderType": "LIMIT",          // was "MARKET"
  "price": "175.50",             // added; serialised as string by schwab-py
  "orderLegCollection": [
    { "instruction": "BUY", "instrument": { "assetType": "EQUITY", "symbol": "AAPL" }, "quantity": 1 }
  ],
  "orderStrategyType": "SINGLE"
}
```

**Price precision rule:** schwab-py serialises the price via Python's default float-to-str
conversion, which can produce unexpected decimal places. `equity_buy_limit('X', 1, 175.555555)`
produces `"price": "175.55"` (truncation behaviour). Always pass `round(limit_price, 2)` to
guarantee exactly two decimal places and avoid unintended limit prices.

**New builder function for `orders.py`:**
```python
def build_limit_buy(ticker: str, shares: int, limit_price: float) -> dict:
    """Return the JSON spec for a limit buy order (no network call)."""
    return equity_buy_limit(ticker, shares, round(limit_price, 2)).build()
```

**`place_order` integration:** Add `USE_LIMIT_BUY` boolean to `Config` (default `True`). The
limit price is the signal price already stored in the recommendation — no new yfinance call at
order time. Branch in `place_order`:
```python
def place_order(ticker: str, shares: int, config, client=None, limit_price: float | None = None) -> str:
    ...
    if config.use_limit_buy and limit_price is not None:
        spec = build_limit_buy(ticker, shares, limit_price)
    else:
        spec = build_market_buy(ticker, shares)
```

The `place_order` return path (`resp.headers.get("Location", "").split("/")[-1]`) and
`_call_place_order` retry wrapper are unchanged — limit orders use the same Schwab
`place_order` endpoint and return the same `Location` header.

`duration` defaults to `DAY`. A limit order placed after market close expires at the end of the
next trading session. This is the correct behaviour for a signal-price limit order; do not change
it to GTC.

---

### yfinance 1.2.0 — Earnings Date

**Two sources, one preferred:**

**Option A (preferred — zero extra cost):** `ticker.info` already contains `earningsTimestamp`.
This is populated in the same call that `fetch_fundamental_info` already makes. Use when only
the next earnings date is needed (Discord embed warning field).

```python
import datetime
ts = info.get('earningsTimestamp')
next_earnings: datetime.date | None = datetime.datetime.fromtimestamp(ts).date() if ts else None
is_estimate: bool = info.get('isEarningsDateEstimate', True)
```

**Option B (if estimate range is also needed):** `ticker.calendar` — separate HTTP call.

Attribute: `ticker.calendar` (separate HTTP request, ~0.06s in testing)
Return type: `dict` when data exists, `{}` (empty dict) when absent (ETFs, invalid/delisted tickers)

```python
cal = ticker.calendar  # must be wrapped in asyncio.to_thread
dates = cal.get('Earnings Date', []) if cal else []
next_earnings: datetime.date | None = dates[0] if dates else None
```

Observed shape (equities):
```python
{
    'Earnings Date': [datetime.date(2026, 5, 1)],  # always a list, first element is next date
    'Earnings High': 2.16,
    'Earnings Low': 1.75,
    'Earnings Average': 1.94361,
    'Revenue High': 112692596420,
    'Revenue Low': 105000000000,
    'Revenue Average': 109232144700,
    'Dividend Date': datetime.date(2026, 2, 12),
    'Ex-Dividend Date': datetime.date(2026, 2, 9),
}
```

Both Option A and Option B agreed on the date in all tested tickers (AAPL, MSFT, GOOGL, NVDA).
Prefer Option A for the embed warning to avoid an extra network call per ticker.

---

### yfinance 1.2.0 — EPS Quarterly Trend

**DO NOT USE: `ticker.quarterly_earnings`** — returns `None` under yfinance 1.2.0. This
attribute was removed or broken in the yfinance 1.x rewrite.

**PRIMARY: `ticker.earnings_history`** — separate HTTP call, ~0.4s cold

Return type: `DataFrame` or empty DataFrame. Never `None` (but may be empty).
Shape: 4 rows (last 4 reported quarters), ascending date order (oldest first in index).

Columns: `epsActual`, `epsEstimate`, `epsDifference`, `surprisePercent`
Index: quarterly `Timestamp` values

```python
eh = ticker.earnings_history  # must be wrapped in asyncio.to_thread
if eh is None or eh.empty:
    eps_trend = None
    eps_vals = None
else:
    eps_vals = eh['epsActual'].tolist()  # [q_oldest, q2, q3, q_newest]
    eps_trend = 'up' if eps_vals[-1] > eps_vals[0] else 'down'
    # For Claude prompt: format as "Q1=$1.65 Q2=$1.57 Q3=$1.85 Q4=$2.84 (trend: up)"
```

**FALLBACK: `ticker.quarterly_income_stmt`** — if `earnings_history` is empty
Return type: wide DataFrame, rows = line items, columns = quarter-end Timestamps, newest first.

```python
qi = ticker.quarterly_income_stmt  # must be wrapped in asyncio.to_thread
if qi is not None and not qi.empty and 'Basic EPS' in qi.index:
    eps_row = qi.loc['Basic EPS']         # newest-first Series
    eps_vals = list(reversed(eps_row.dropna().tolist()))  # flip to oldest-first
```

**PE direction (zero extra cost):**
Both `trailingPE` and `forwardPE` are present in `ticker.info`, already fetched by
`fetch_fundamental_info`. No new network call.

```python
trailing_pe = info.get('trailingPE')
forward_pe = info.get('forwardPE')
if trailing_pe and forward_pe:
    pe_direction = 'contracting' if forward_pe < trailing_pe else 'expanding'
else:
    pe_direction = None
# Add to Claude BUY prompt: "PE: 34.2 (trailing) → 28.9 (forward) [contracting]"
```

---

### /history Discord Command — No New Dependencies

The existing `trades` table has all columns needed. No schema changes required.

Required new DB query (add to `database/queries.py`, following existing patterns):
```python
def get_recent_closed_trades(db_path: str, limit: int = 20) -> list[sqlite3.Row]:
    """Return last `limit` sell trades ordered by most recent first.

    Returns rows with: ticker, price (exit), cost_basis (entry), shares, executed_at.
    Rows with cost_basis IS NULL are excluded (pre-migration trades).
    """
    conn = get_connection(db_path)
    rows = conn.execute(
        """SELECT ticker, price, cost_basis, shares, executed_at
           FROM trades
           WHERE side = 'sell' AND cost_basis IS NOT NULL
           ORDER BY executed_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return rows
```

P&L per trade: `(price - cost_basis) / cost_basis * 100` — same formula already used in
`get_trade_stats`. Register `/history` in `setup_hook` alongside `/scan`, `/positions`, `/stats`.
Call `get_recent_closed_trades` via `asyncio.to_thread` in the command handler.

---

## Version Gotchas

### yfinance 1.2.0

1. **`ticker.quarterly_earnings` returns `None`** — do not use. Use `ticker.earnings_history`
   instead. This broke silently in the 1.x rewrite with no deprecation warning.

2. **`ticker.calendar` returns `{}` (empty dict), not `None`, when absent** — guard with
   `cal.get('Earnings Date', [])` rather than `if cal['Earnings Date']`. ETFs (SPY), invalid
   tickers, and recently listed stocks all return `{}`. Tested: SPY → `{}`, fake ticker → `{}`.

3. **`ticker.earnings_history` can return an empty DataFrame** — guard with
   `if eh is None or eh.empty`. Observed empty for NKLA (never reported positive EPS). Non-empty
   for BRK-A, SPWR, BLNK. Always test both conditions.

4. **`earnings_history` index order is ascending (oldest first)** — `eh['epsActual'].tolist()`
   gives `[q_oldest, ..., q_newest]`. Compare `[-1] > [0]` for uptrend direction.

5. **`quarterly_income_stmt` column order is descending (newest first)** — the opposite of
   `earnings_history`. Reverse the list before directional comparison if using this fallback.

6. **Each of `calendar`, `earnings_history`, `quarterly_income_stmt` is a separate HTTP call** —
   each requires its own `asyncio.to_thread(...)` wrap. Calling any of these bare in an async
   function blocks the Discord gateway heartbeat, violating ASYNC-01..04 established in v1.1.
   `ticker.info` (already wrapped in `fetch_fundamental_info`) does not pre-cache these.

7. **`earningsTimestamp` in `ticker.info` duplicates `ticker.calendar` date** — both return the
   same next-earnings date in all tested tickers. The `info` field is free (no extra call);
   prefer it for the embed warning field.

### schwab-py 1.5.1

1. **Price serialised as `str(float)`, not `"%.2f"` formatted** — `equity_buy_limit` does not
   round for you. `175.555555` becomes `"175.55"` (Python's float repr truncation). Always pass
   `round(limit_price, 2)` to guarantee exactly two decimal places.

2. **Integer prices are padded to two decimal places** — `equity_buy_limit('X', 1, 175)` produces
   `"price": "175.00"`. Passing an int is safe; Python's `str(175.00)` gives `"175.0"` but
   schwab-py normalises it. Still: pass `round(float(price), 2)` for consistency.

3. **`equity_buy_limit` returns `OrderBuilder`, not `dict`** — call `.build()` exactly like
   `equity_buy_market`. The existing `_call_place_order` takes the spec dict; pattern is
   unchanged.

4. **Order lifetime is `DAY` by default** — correct for signal-price limit orders. Do not change
   to `GOOD_TILL_CANCEL`; GTC orders can fill at stale prices after news events.

5. **`place_order` `Location` header extraction is unchanged** — limit orders use the same
   Schwab endpoint and return the same `Location: /orders/{order_id}` header. No change to the
   `resp.headers.get("Location", "").split("/")[-1]` extraction.

---

## What NOT to Add

- **No new pip packages** — all four features are achievable with installed versions.
- **No GTC (Good Till Cancelled) limit orders** — `DAY` duration is correct. GTC orders persist
  across sessions and can fill at stale prices after earnings or macro events.
- **Do not use `ticker.quarterly_earnings`** — returns `None` in yfinance 1.2.0.
- **Do not add `ticker.calendar` call if only the next earnings date is needed** — derive it from
  `info['earningsTimestamp']` (already fetched by `fetch_fundamental_info`) at zero extra cost.
  Only add the `calendar` call if the earnings estimate range (High/Low/Average) is required in
  the Claude prompt.
- **No dedicated earnings API or external calendar service** — yfinance is the single data source;
  adding a second creates synchronisation risk and a new failure mode.
- **No new DB tables** — the existing `trades` table has all columns needed for `/history`.
  `get_recent_closed_trades` is a read-only query, no schema changes needed.
- **No schema migration scripts** — follow the established additive `ALTER TABLE ... ADD COLUMN`
  pattern inside `initialize_db` wrapped in `try/except sqlite3.OperationalError` for any new
  columns. No new columns are needed for v1.3.
- **No changes to the sell order path** — limit orders apply to BUY only. Sell orders remain
  market orders (slippage on exit is acceptable; missing an exit on a declining position is not).
