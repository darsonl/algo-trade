# Pitfalls Research — v1.3

**Domain:** Adding limit orders, yfinance earnings data, trade history, and test coverage to a live Python Discord trading bot
**Researched:** 2026-04-19
**Confidence:** HIGH — all pitfalls derived from direct codebase inspection plus live `python -c` verification of actual yfinance and schwab-py behavior

---

## Summary

v1.3 adds five features across four distinct subsystems: Schwab limit order API, yfinance calendar/income_stmt, SQLite history JOIN, and test infrastructure. Each subsystem has a distinct failure mode. The highest-priority risks are: (1) limit price staleness — the price stored at scan time becomes the limit price for an order placed hours later when the user finally clicks Approve, and (2) the `/history` JOIN producing duplicate rows when a ticker has been bought and sold more than once, silently inflating P&L calculations. The yfinance earnings pitfalls are real but lower-severity because the existing defensive patterns (`info.get(...)`, `None` guards, `asyncio.to_thread`) translate directly — the main trap is assuming `ticker.calendar` and `ticker.quarterly_income_stmt` behave uniformly across all tickers. Test infrastructure pitfalls center on Config env var leakage between test sessions and the mocking strategy for private functions.

---

## Limit Orders Pitfalls

---

### L-01: Float price passed to `equity_buy_limit` triggers DeprecationWarning, will break in future schwab-py version

**What goes wrong:** `equity_buy_limit(ticker, shares, 150.005)` emits `UserWarning: passing floats to set_price and set_stop_price is deprecated and will be removed soon. Please update your code to pass prices as strings instead.` Verified live against the installed schwab-py. A future schwab-py point release will convert this warning to a `TypeError`, silently breaking all limit orders in production the next time the library is updated.

**Why it happens:** The existing `build_market_buy` passes an `int` (shares) and `str` (ticker), but the limit price will be derived from `info.get("regularMarketPrice")` — a Python `float`. The natural implementation is `equity_buy_limit(ticker, shares, price)` with `price` as a float.

**Prevention:** Pass the limit price as a string formatted to the appropriate decimal places: `f"{price:.2f}"`. Use two decimal places for prices above $1.00, and up to four decimal places for penny stocks below $1.00 (Schwab accepts up to 4 decimal places). Add `build_limit_buy(ticker, shares, price_str)` in `schwab_client/orders.py` mirroring the existing `build_market_buy` pattern. The function signature should accept a `str` for price, not `float`, to make the caller responsible for formatting — this prevents accidental float drift.

**Phase:** Phase implementing limit buy orders.

---

### L-02: Limit price is stale when user approves hours after the scan ran

**What goes wrong:** The recommendation is created at scan time (e.g., 09:00 AM) with `price = info["regularMarketPrice"]` stored in the `recommendations` table. When the user clicks Approve at 3:45 PM, `ApproveRejectView` uses `self.price` — the price captured at view construction from the DB row at scan time. The limit order is placed at a price that is hours old. For volatile tickers, the actual price may have moved 2-5% away from the limit, causing the order to either: (a) never fill if the stock moved up past the limit, leaving an unfilled DAY order that expires silently, or (b) fill immediately at a price far below the current market if the stock dropped, which is actually favorable but unexpected.

**Why it happens:** `ApproveRejectView.__init__` receives `price` from `send_recommendation` which reads from the DB row. The price is never refreshed between scan time and approval time. This is intentional for market orders (price is approximate anyway), but for limit orders, the limit price is a contract — the broker will not fill above it.

**Prevention:** Two options: (a) Fetch a fresh quote at Approve time and use that as the limit price — this requires a yfinance call inside the async button handler, wrapped in `asyncio.to_thread`, which adds latency and a potential failure path. (b) Store the limit price in the recommendations table and add a "price may be stale" warning in the Discord embed showing how long ago the recommendation was created. Option (b) is lower risk and consistent with the existing pattern. The embed should display `"Limit: $X.XX (as of HH:MM)"` and the operator is informed before clicking. Document in `.env.example` that `USE_LIMIT_BUY=true` means the limit price is the scan-time price.

**Phase:** Phase implementing limit buy orders.

---

### L-03: DAY duration limit orders expire silently — no notification when unfilled

**What goes wrong:** `equity_buy_limit` defaults to `duration=DAY`. If the scan runs at 09:00 and the user approves at 09:30, but the stock never trades at or below the limit price before market close, the order expires unfilled at 4:00 PM. The `trades` table has no row for this trade (no fill = no `create_trade` call). The `recommendations` table still has `status='approved'`. The position is not opened. The operator has no indication that the order expired — the Discord approval message shows "Approved" but no follow-up notification of the unfill. Worse, the recommendation is `approved` in the DB, which prevents the dupe-check from recommending the same ticker tomorrow via `should_recommend()`.

**Why it happens:** `place_order` and `place_sell_order` return the order ID and call `create_trade` immediately — they do not poll for fill status. Schwab order status polling is not implemented and is explicitly out of scope for this milestone.

**Prevention:** For v1.3, set duration to `GOOD_TILL_CANCEL` by default (or make it configurable via `LIMIT_ORDER_DURATION` in Config, defaulting to `GTC`). GTC orders remain open until filled or manually cancelled, which is more appropriate for end-of-day Discord approval workflows. Document the trade-off: GTC orders must be manually cancelled if the recommendation expires before it fills, and the operator must monitor open orders in the Schwab app. Add a note to the Approve confirmation message: `"Limit order placed (GTC) at $X.XX — monitor Schwab for fill status."` The existing dupe prevention (`should_recommend`) already guards against re-recommending the same ticker within 24h regardless of fill status.

**Phase:** Phase implementing limit buy orders.

---

### L-04: `USE_LIMIT_BUY` config flag not guarded at validate() time

**What goes wrong:** If `USE_LIMIT_BUY` is parsed as `bool(os.getenv("USE_LIMIT_BUY", "true"))` (incorrect) rather than `os.getenv("USE_LIMIT_BUY", "true").lower() == "true"` (correct), then any non-empty string value in `.env` evaluates to `True` — including `USE_LIMIT_BUY=false`. This mirrors a known Python trap and produces a bot that always places limit orders regardless of operator intent.

**Why it happens:** `bool("false")` is `True` in Python. The Config pattern used by all other bool fields in this codebase (`os.getenv(...).lower() == "true"`) is correct, but a new contributor unfamiliar with the pattern may use `bool(...)` directly.

**Prevention:** Copy the exact pattern from `dry_run` and `paper_trading` fields: `os.getenv("USE_LIMIT_BUY", "true").lower() == "true"`. Add a test: `Config` constructed with `USE_LIMIT_BUY=false` in the env has `use_limit_buy=False`. This test will catch the `bool(str)` mistake immediately.

**Phase:** Phase implementing limit buy orders.

---

## yfinance Data Pitfalls

---

### Y-01: `ticker.calendar` returns `{}` for ETFs, not `None` — silent empty dict

**What goes wrong:** For ETF tickers (e.g., SPY), `ticker.calendar` returns an empty dict `{}` — verified live. Code that checks `if cal is None` will not guard against this. Code that does `cal["Earnings Date"]` on the empty dict raises `KeyError`. Code that does `cal.get("Earnings Date")` returns `None` silently, which is correct behavior — but if downstream code then formats `None` as a date string it crashes.

**Why it happens:** The existing ETF path (`run_scan_etf`) already skips fundamentals, but if a `fetch_earnings_date` helper is added to `screener/fundamentals.py` and called from both scan paths, it will silently receive an ETF ticker. The empty dict is yfinance's way of indicating "no calendar data."

**Prevention:** Guard with `cal.get("Earnings Date")`, not `cal["Earnings Date"]`. Return `None` from any helper when the earnings date list is empty or missing. Do not call the earnings date helper from `run_scan_etf` at all — ETFs have no earnings calendar and the feature is described as a field on the "Discord BUY embed" for stocks only.

**Phase:** Phase adding earnings date warning to BUY embed.

---

### Y-02: `ticker.calendar["Earnings Date"]` is always a `list[datetime.date]`, but can have zero or multiple entries

**What goes wrong:** Verified live for AAPL, MSFT, GOOGL, T, NVDA, BRK-B — `Earnings Date` is always a `list` of `datetime.date` objects when present, never a bare `datetime.date` or a string. However, this is an unofficial scraper — the format can vary. A list with zero items (`[]`) is possible for tickers without upcoming earnings estimates. A list with two items occurs when the estimate range spans two dates.

**Why it happens:** Code that assumes `calendar["Earnings Date"]` is a single value (not a list) will fail with `AttributeError` or `TypeError` when trying to format it as a date string, or will silently show only the first date when two are present.

**Prevention:** Always index with `[0]` after confirming `len(list) > 0`. Preferred pattern:

```python
dates = cal.get("Earnings Date") or []
earnings_date = dates[0] if dates else None
```

`datetime.date` objects can be formatted directly with `str(earnings_date)` or `earnings_date.strftime("%b %d")` — no Timestamp conversion needed. Do not call `.strftime` on a `None` value.

**Phase:** Phase adding earnings date warning to BUY embed.

---

### Y-03: `ticker.quarterly_income_stmt` — columns are Timestamps, not quarter label strings, and the DataFrame is empty for ETFs

**What goes wrong:** `quarterly_income_stmt` (the correct attribute for EPS trend) has Timestamp column headers (`Timestamp('2025-12-31 00:00:00')`) sorted newest-first. Verified live for AAPL. For ETFs (SPY), the DataFrame is empty — `empty=True` with no rows. Code that assumes columns are quarter strings like `"Q1 2025"` will fail. Code that accesses EPS via `df["Q1 2025"]` will raise `KeyError`.

**Why it happens:** `ticker.quarterly_earnings` is deprecated (DeprecationWarning confirmed in live testing) and returns `None` for AAPL under the current yfinance version. The correct source is `ticker.quarterly_income_stmt` with the row `"Diluted EPS"` or `"Basic EPS"`. Developers who consult old Stack Overflow answers or pre-2024 documentation may use the deprecated attribute.

**Prevention:** Use `ticker.quarterly_income_stmt`. Access EPS via `df.loc["Diluted EPS"]` after confirming the row exists with `"Diluted EPS" in df.index`. Columns are `pd.Timestamp` — use `.dt` or convert to ISO string with `str(col.date())` for display. Guard with `if df is None or df.empty: return None` before any index access. Wrap the entire call in `asyncio.to_thread` — `quarterly_income_stmt` is a blocking HTTP call, not a cached property.

**Phase:** Phase adding EPS quarterly trend to Claude BUY prompt.

---

### Y-04: NaN values in the EPS row — `math.isnan` not equivalent to pandas `pd.isna`

**What goes wrong:** `df.loc["Diluted EPS"]` returns a pandas Series. Individual values in the Series can be `float('nan')` (Python NaN) or `pd.NA`. Using `if value is None` will not catch NaN. Using `math.isnan(value)` will raise `TypeError` on `pd.NA`. The correct guard is `pd.isna(value)`.

**Why it happens:** Quarterly income statements have gaps — a quarter may have no reported EPS if the earnings report was restated, or the most recent quarter may not yet have final figures. The latest column (newest quarter) is most likely to contain NaN if the report was just filed.

**Prevention:** Use `pd.isna(value)` to filter the EPS series before computing trend. Example:

```python
eps_series = df.loc["Diluted EPS"].dropna()
if len(eps_series) < 2:
    return None  # insufficient data for trend
```

Never use `math.isnan`, `value is None`, or `value == float('nan')` on pandas data.

**Phase:** Phase adding EPS quarterly trend to Claude BUY prompt.

---

### Y-05: P/E direction requires both `trailingPE` and `forwardPE` — `forwardPE` is frequently absent

**What goes wrong:** P/E direction ("expanding" or "contracting") is computed from `trailingPE` vs `forwardPE` from `ticker.info`. `forwardPE` is absent for many tickers — particularly small-caps, recent IPOs, and tickers without analyst coverage. `info.get("forwardPE")` returns `None` in these cases. Code that computes `"expanding" if forwardPE < trailingPE else "contracting"` raises `TypeError` when `forwardPE` is `None`.

**Why it happens:** `forwardPE` requires analyst EPS estimates to be available on Yahoo Finance. The fundamental filter already permits tickers without earnings data through (`earningsGrowth` is optional per `passes_fundamental_filter`). Those same tickers will have no `forwardPE`.

**Prevention:** Return `"N/A"` or omit the P/E direction field from the prompt when `forwardPE` is None. Do not infer direction from `trailingPE` alone — a single value has no direction. The `build_prompt` function already handles this pattern for `earningsGrowth`. Keep the same defensive style: `if pe is None or forward_pe is None: pe_direction = "N/A"`.

**Phase:** Phase adding P/E direction to Claude BUY prompt.

---

## History Query Pitfalls

---

### H-01: Naive JOIN on `ticker` produces duplicate rows when ticker has been bought and sold multiple times

**What goes wrong:** A `/history` query written as `JOIN trades s ON s.ticker = b.ticker AND s.side = 'sell' WHERE b.side = 'buy'` produces a cartesian product when a ticker has been bought and sold more than once. Two buy rows for AAPL joined against two sell rows for AAPL produces four result rows — inflating the trade count, duplicating P&L entries, and presenting a false history to the operator.

**Why it happens:** The `trades` table schema has no explicit linkage between buy and sell trades for the same position. A buy trade is linked to its BUY recommendation via `recommendation_id`. A sell trade is linked to its SELL recommendation via `recommendation_id`. But there is no column in the sell trade row that points to the corresponding buy trade row. `recommendation_id` on a sell trade points to a SELL-signal recommendation row, not the original BUY recommendation.

**Prevention:** Two viable approaches:

(a) **Temporal pairing** — join the most recent buy before each sell using `b.executed_at < s.executed_at` with a `ROW_NUMBER()` or `MAX()` subquery. This is correct for the existing schema and requires no migration.

(b) **Schema extension** — add a `buy_trade_id` column to the `trades` table, populated when a sell trade is recorded. This enables exact pairing but requires a migration and a change to `create_trade` and `SellApproveRejectView`.

For v1.3, option (a) avoids a schema change. A safe working query:

```sql
SELECT s.ticker, s.price AS exit_price, s.cost_basis, s.executed_at AS sell_date,
       (s.price - s.cost_basis) / s.cost_basis AS pnl_pct
FROM trades s
WHERE s.side = 'sell' AND s.cost_basis IS NOT NULL
ORDER BY s.executed_at DESC
LIMIT 20
```

The sell trade already contains `cost_basis` (the `avg_cost_usd` from the position at sell time, recorded via `SellApproveRejectView`). The entry price is captured in `cost_basis`. There is no need to join to the buy trade at all for the `/history` view — the sell row is self-contained.

**Phase:** Phase implementing `/history` slash command.

---

### H-02: `cost_basis IS NULL` on sell trades from pre-v1.2 data silently excludes them from history

**What goes wrong:** The `cost_basis` column was added to the `trades` table as a migration in v1.2. Any sell trades recorded before that migration have `cost_basis = NULL`. A `/history` query that computes `(exit_price - cost_basis) / cost_basis` raises `ZeroDivisionError` or returns `NULL` when `cost_basis` is `NULL` or `0`. The `get_trade_stats` function already guards this with `WHERE cost_basis IS NOT NULL` — the same guard must be applied to `/history`.

**Why it happens:** The `initialize_db` migration adds `cost_basis REAL` with no `DEFAULT` value, so existing rows get `NULL`. Pre-migration sell trades exist in any DB that has been running since before v1.2.

**Prevention:** Filter with `WHERE s.side = 'sell' AND s.cost_basis IS NOT NULL` in the `/history` query, matching the existing `get_trade_stats` pattern. Display a note in the `/history` embed footer: `"Trades before v1.2 excluded (no cost basis)"` if any NULL-cost_basis sell rows exist. Do not attempt to infer the cost basis from the buy trade at query time — this produces incorrect results when averaged positions were held.

**Phase:** Phase implementing `/history` slash command.

---

### H-03: Buy-with-no-sell rows cause zero results if query requires a matching sell

**What goes wrong:** A query that requires a JOIN between buy and sell for the same ticker will return zero rows for any currently-open position (no sell recorded yet) and for positions where the sell was rejected. The `/history` command result will appear empty even if trades exist — confusing the operator.

**Why it happens:** The query design in H-01 naive JOIN approach excludes all open positions correctly by design, but if the query is written as an INNER JOIN requiring both sides, a DB with only open positions (new deployment, no closed trades yet) returns an empty embed with no explanation.

**Prevention:** Use the sell-side-only query approach from H-01. The sell trade row is the authoritative record of a closed trade. If no sell rows exist, display `"No closed trades yet."` explicitly rather than an empty embed or a silent no-response. Test with an empty DB, a buy-only DB, and a DB with one complete round-trip.

**Phase:** Phase implementing `/history` slash command.

---

## Test Coverage Pitfalls

---

### T-01: Config instantiation in tests leaks env vars between test sessions

**What goes wrong:** `Config()` calls `load_dotenv()` at module import time (line 6 of `config.py`). If a test sets `os.environ["USE_LIMIT_BUY"] = "false"` without cleaning up, subsequent tests in the same session that instantiate `Config()` silently inherit the value. This causes intermittent test failures that are order-dependent — the test suite passes in isolation but fails when run with `pytest` (all tests in sequence).

**Why it happens:** `Config` is a plain dataclass whose fields read `os.getenv(...)` at instantiation time, not at class definition time. Each `Config()` call re-reads from `os.environ`. Tests that mutate `os.environ` without cleanup pollute the process environment for the entire test session.

**Prevention:** Use `monkeypatch.setenv("KEY", "value")` (pytest's `monkeypatch` fixture) for all env var overrides in tests — it automatically restores the original value after each test. Alternatively, use `unittest.mock.patch.dict(os.environ, {"KEY": "value"})` as a context manager. Never mutate `os.environ` directly in test code. Apply the same pattern already established in the existing test suite (inspect `test_analyst_claude.py` and `test_discord_buttons.py` for the current convention before adding new tests).

**Phase:** Phase adding test coverage for config validation and analyst fallback logic.

---

### T-02: Mocking `_call_api` directly patches a private name but not the bound reference

**What goes wrong:** `_call_api` is a module-level function in `analyst/claude_analyst.py`. Mocking it as `mock.patch("analyst.claude_analyst._call_api")` works correctly when called within `analyze_ticker`, which resolves the name at call time. However, if tests for the fallback path test `_should_retry` directly (patching `_parse_retry_delay`), they may interact with the `@_retry` decorator which is bound at import time. Patching the decorated function after import does not change the retry behavior — only patching the internal mechanism does.

**Why it happens:** The `@_retry` decorator wraps `_call_api` into a new callable at import time. The `retry` object stores a reference to the original function. Patching `_call_api` in the module namespace replaces what `analyze_ticker` sees at the `_call_api(client, model, prompt)` call site — this works. But tests that try to assert retry behavior by patching the `_retry` decorator itself or `tenacity` internals will not work as expected.

**Prevention:** For fallback path tests, mock `_call_api` at the `analyst.claude_analyst._call_api` name and configure the mock's `side_effect` to raise an exception on the first call and return a valid response on the second. This simulates primary failure + fallback without touching tenacity internals. For `_should_retry` unit tests, call `_should_retry(exc)` directly with a synthetic exception object — no patching needed. Confirmed: the existing test suite already patches `_call_api` at the module level; the fallback tests should follow the same pattern.

**Phase:** Phase adding test coverage for analyst fallback logic.

---

### T-03: Test for `run_scan` quota exhaustion path requires both `get_analyst_call_count_today` and the scan loop to be exercised

**What goes wrong:** The quota guard in `run_scan` (presumed: calls `get_analyst_call_count_today` before `analyze_ticker`) must be tested by exercising the actual scan loop with a DB state where count equals `analyst_daily_limit`. A test that patches `get_analyst_call_count_today` to return a high value but does not run `run_scan` end-to-end will not catch integration bugs — for example, if the quota check is placed after the cache check (wrong order), a test that exercises only the quota function misses the ordering bug entirely.

**Why it happens:** `run_scan` is a large async function with multiple guards in sequence (dupe check, fundamental filter, quota check, cache check, API call). Testing one guard in isolation does not verify the guard fires at the right point in the execution order.

**Prevention:** Write integration tests for `run_scan` quota exhaustion that mock the DB state (set `call_count = analyst_daily_limit`) and run the full `run_scan` function (with yfinance and Schwab mocked). Assert that: (a) no `analyze_ticker` call was made, (b) the scan completes without error, (c) zero recommendations are created, (d) the ops-channel zero-rec alert fires (if applicable). Use the existing `test_run_scan.py` as the template — it already runs `run_scan` end-to-end with mocked dependencies.

**Phase:** Phase adding test coverage for quota exhaustion path.

---

## Prevention Checklist

Before shipping each v1.3 phase, verify:

**Limit Orders**
- [ ] `equity_buy_limit` price argument is a formatted string (`f"{price:.2f}"`), never a raw float
- [ ] `USE_LIMIT_BUY` Config field uses `.lower() == "true"` pattern, not `bool(str)`
- [ ] Limit order duration is `GOOD_TILL_CANCEL` (or configurable) — not `DAY`
- [ ] Approve button embed displays scan-time price and a staleness warning (e.g., "as of HH:MM")
- [ ] `build_limit_buy` is a pure function in `schwab_client/orders.py` — testable without network

**yfinance Earnings Data**
- [ ] `ticker.calendar` accessed with `.get("Earnings Date") or []` — never direct index
- [ ] Earnings date extracted as `dates[0]` after `len(dates) > 0` check
- [ ] `ticker.quarterly_income_stmt` used (not deprecated `quarterly_earnings`)
- [ ] EPS NaN guard uses `pd.isna()` or `.dropna()` — not `math.isnan` or `is None`
- [ ] `forwardPE` guarded: if None, P/E direction returns "N/A"
- [ ] All new yfinance calls (`quarterly_income_stmt`, `calendar`) wrapped in `asyncio.to_thread`
- [ ] ETF tickers do not reach earnings date or EPS trend code paths

**History Query**
- [ ] `/history` query uses sell-side rows only (`WHERE side='sell'`) — no JOIN to buy rows
- [ ] `cost_basis IS NOT NULL` filter applied — pre-v1.2 rows excluded explicitly
- [ ] Empty result case displays `"No closed trades yet."` — not an empty embed
- [ ] Three tests: empty DB, buy-only DB, one complete round-trip

**Test Coverage**
- [ ] Env var overrides use `monkeypatch.setenv` or `patch.dict(os.environ)` — never direct `os.environ` mutation
- [ ] Fallback path tests mock `analyst.claude_analyst._call_api` with `side_effect` sequence
- [ ] Quota exhaustion test runs `run_scan` end-to-end with mocked DB state, not just the guard function
- [ ] `Config.validate()` test covers the new `USE_LIMIT_BUY` field with invalid value
