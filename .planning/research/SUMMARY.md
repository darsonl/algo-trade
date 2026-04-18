# Research Summary — v1.3 Risk & Signal Quality

**Project:** Algo Trade Bot — v1.3
**Domain:** Automated Discord-approval stock trading bot — signal quality and risk management additions
**Researched:** 2026-04-18
**Confidence:** HIGH

---

## Key Decisions

Things the plan must get right — especially the surprises from research.

**1. Limit order price must be a formatted string, never a raw float.**
`equity_buy_limit` already emits a `DeprecationWarning` when passed a Python `float`. A future schwab-py point release will convert this to a `TypeError`. Every call site must pass `f"{price:.2f}"`. This is not optional.

**2. `ticker.quarterly_earnings` is broken in yfinance 1.2.0 — use `ticker.quarterly_income_stmt` instead.**
`quarterly_earnings` returns `None` silently. The correct attribute is `quarterly_income_stmt`, rows indexed by line-item string, columns by `pd.Timestamp` (newest-first). Access EPS via `df.loc["Diluted EPS"]` after confirming the row exists.

**3. The `/history` query must be sell-side-only — no JOIN to buy rows.**
A JOIN on `ticker` produces cartesian-product duplicates when a ticker has been traded more than once. The sell trade row is self-contained: `cost_basis` (entry price), `price` (exit), `shares`, `ticker`, `executed_at` are all present since v1.2. Use a single-table SELECT with `WHERE side='sell' AND cost_basis IS NOT NULL`.

**4. `USE_LIMIT_BUY` bool must use the `.lower() == "true"` Config pattern, not `bool(str)`.**
`bool("false")` is `True` in Python. Every other bool field in Config uses `.lower() == "true"`. A test must assert that `USE_LIMIT_BUY=false` in env sets `use_limit_buy=False`.

**5. Limit order duration: default GTC, not DAY.**
A user approving at 3:45 PM with `DAY` duration gets a silent non-fill and the recommendation stays `approved` in the DB, blocking tomorrow's dupe check. Default to `GOOD_TILL_CANCEL`, document in `.env.example`, show scan-time price + timestamp staleness in the Approve embed.

**6. All new yfinance attributes are separate blocking HTTP calls — each needs asyncio.to_thread.**
`ticker.calendar`, `ticker.earnings_history`, and `ticker.quarterly_income_stmt` are not folded into `ticker.info`. Extending `fetch_fundamental_info` (already wrapped at its call site) is the cleanest path — new calls inherit the existing thread context.

**7. `earningsTimestamp` in `ticker.info` is free — prefer it over a separate `ticker.calendar` call.**
Both return the same next-earnings date. `ticker.info` is already fetched. Use `info.get("earningsTimestamp")` for the embed warning field at zero extra network cost.

---

## Stack Additions

No new pip packages required.

| Library | Version | v1.3 Surface |
|---------|---------|--------------|
| schwab-py | 1.5.1 | `equity_buy_limit(symbol, quantity, price_str)` — add to `orders.py` |
| yfinance | 1.2.0 | `info["earningsTimestamp"]` (free), `ticker.quarterly_income_stmt` (new `asyncio.to_thread` call) |
| discord.py | existing | `/history` slash command via existing `app_commands` pattern |
| sqlite3 | stdlib | One new read-only query in `database/queries.py` |

Two additive DB columns on `trades` (`order_type TEXT DEFAULT 'market'`, `limit_price REAL`) follow the established `initialize_db` try/except pattern.

---

## Feature Table Stakes

| Feature | Classification | Key Constraint |
|---------|---------------|----------------|
| Limit buy order on Approve | Differentiator | Price as `f"{price:.2f}"` string; `USE_LIMIT_BUY` defaults true; GTC duration default |
| Earnings date warning in BUY embed | Table stakes | From `info["earningsTimestamp"]` at zero extra cost; visual flag within 7 days; omitted when absent |
| Quarterly EPS trend in Claude prompt | Differentiator | `quarterly_income_stmt` not `quarterly_earnings`; pre-computed directional label |
| P/E direction in Claude prompt | Differentiator | `trailingPE` vs `forwardPE` from already-fetched `info`; "N/A" when `forwardPE` is None |
| `/history` slash command | Table stakes | Sell-side-only query; last 20 closed trades with P&L%; "No closed trades yet." on empty |

**Explicitly deferred:** GTC cancel button, real-time fill tracking, `/history` filtering/pagination, earnings BMO/AMC timing, historical P/E via price/EPS division.

---

## Critical Pitfalls

**1. Float limit price triggers DeprecationWarning → future TypeError (L-01)**
Pass `f"{price:.2f}"` as string. Already breaking under warnings in the installed version.

**2. DAY duration + late Approve = silent unfilled order that locks DB recommendation (L-03)**
Stays `approved`, blocks dupe check indefinitely. Fix: default GTC.

**3. `ticker.quarterly_earnings` returns None silently — use `quarterly_income_stmt` (Y-03)**
Verified live. Guard with `if df is None or df.empty`. Use `pd.isna()`/`.dropna()` not `math.isnan`.

**4. Naive JOIN on `ticker` for `/history` produces duplicate P&L rows (H-01)**
Two round-trips for same ticker = four JOIN rows. Use sell-side-only SELECT.

**5. Direct `os.environ` mutation in tests leaks state between sessions (T-01)**
All env var overrides must use `monkeypatch.setenv` or `patch.dict(os.environ, {...})`.

---

## Build Order Recommendation

### Phase 14: Trade History Command (`/history`)
Pure read path, zero execution risk. Validates sell-side query before any limit-order work.
**Files:** `database/queries.py`, `discord_bot/embeds.py`, `discord_bot/bot.py`
**Avoid:** H-01 (no JOIN), H-02 (cost_basis IS NOT NULL guard), H-03 (empty result message)

### Phase 15: Fundamental Trend in Claude Prompt
Prompt-only enrichment. P/E direction uses already-fetched `info` (zero extra cost). One new `asyncio.to_thread` call for `quarterly_income_stmt`.
**Files:** `screener/fundamentals.py`, `analyst/claude_analyst.py`
**Avoid:** Y-03 (use `quarterly_income_stmt`), Y-04 (`pd.isna`), Y-05 (`forwardPE` None guard)

### Phase 16: Earnings Date Warning in BUY Embed
Depends on Phase 15 having extended `fetch_fundamental_info`. Fully isolated behind try/except.
**Files:** `screener/fundamentals.py`, `discord_bot/embeds.py`, `discord_bot/bot.py`, `analyst/claude_analyst.py`, `main.py`
**Avoid:** Y-01 (empty dict for ETFs — skip in `run_scan_etf`), Y-02 (index `[0]` after length check)

### Phase 17: Limit Buy Orders
Only execution-path change. Built last so Phases 14–16 are stable before touching `ApproveRejectView.approve`.
**Files:** `schwab_client/orders.py`, `config.py`, `database/models.py`, `discord_bot/bot.py`, `.env.example`
**Avoid:** L-01 (string price), L-02 (staleness warning), L-03 (GTC default), L-04 (`.lower() == "true"`)
**Pre-flight:** Inspect `site-packages/schwab/orders/equities.py` to confirm `equity_buy_limit` name and `duration` param spelling.

### Phase 18: Test Coverage Gaps
Closes untested paths. Run last so scan loop is stable.
**Delivers:** `tests/test_analyst_fallback.py`, `tests/test_config_validation.py`, `tests/test_run_scan_quota.py`
**Avoid:** T-01 (`monkeypatch.setenv`), T-02 (mock at module name), T-03 (full `run_scan` integration)

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | API surfaces verified against installed library versions via live execution |
| Features | HIGH | Four well-scoped features with explicit anti-feature lists |
| Architecture | HIGH | Direct codebase read; integration points at function-signature level |
| Pitfalls | HIGH | Live `python -c` verification against actual installed yfinance 1.2.0 and schwab-py 1.5.1 |

**Overall confidence: HIGH**

### Gaps to Address
- `quarterly_income_stmt` column format: newest-first `pd.Timestamp` columns — reverse before extracting last 4 quarters.
- Limit order duration default: GTC chosen over DAY — document operator responsibility for managing open GTC orders in Schwab app.
- `forwardPE` absence rate in watchlist tickers unknown — P/E direction will display "N/A" for tickers without analyst coverage.

---

*Research completed: 2026-04-18 — Ready for roadmap: yes*
