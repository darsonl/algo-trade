# Architecture: v1.3 Feature Integration

**Project:** Algo Trade Bot — v1.3 Risk & Signal Quality
**Researched:** 2026-04-18
**Scope:** Four targeted features integrated into existing screener → analyst → Discord → Schwab pipeline

---

## Summary

v1.3 adds four features to an already-mature pipeline. None require new modules or schema redesigns. The risk surface is small: one new Schwab order builder function, two new yfinance calls that extend an existing fetcher, one new DB query with a JOIN-free design, and three new test files filling known gaps.

The critical discipline is the project's existing "additive only" rule for DB schema changes and "asyncio.to_thread for all yfinance I/O" rule. Both apply here and must be observed.

All four features fit cleanly into existing seams without touching the core scan loop's control flow.

---

## Limit Order Data Flow

### Current flow (market order)

```
run_scan() → create_recommendation(price=tech_data["price"]) → send_recommendation(price=...)
  → ApproveRejectView.__init__(price=self.price)
  → approve() → place_order(ticker, shares, config)
                   → build_market_buy(ticker, shares)  ← no price involved
```

`price` is stored in `recommendations.price` at scan time and passed into `ApproveRejectView` as `self.price`. It is already threaded through cleanly. The gap is only in `place_order` and `build_market_buy` — they ignore price entirely.

### Integration point

`schwab_client/orders.py` is the only file that changes on the execution side. The data flow already carries `price` end-to-end:

1. `tech_data["price"]` → `create_recommendation(price=...)` → DB row
2. `send_recommendation(price=...)` → `ApproveRejectView(price=self.price)`
3. `approve()` already has `self.price` — pass it to `place_order`

No new columns. No new parameters in `run_scan` or `send_recommendation`.

### New: `build_limit_buy`

```python
# schwab_client/orders.py — add alongside build_market_buy
from schwab.orders.equities import equity_buy_limit

def build_limit_buy(ticker: str, shares: int, limit_price: float) -> dict:
    """Return the JSON spec for a limit buy order at limit_price."""
    return equity_buy_limit(ticker, shares, limit_price).build()
```

Verify `equity_buy_limit` name and signature against current schwab-py docs before shipping — this is a third-party wrapper.

### Modified: `place_order`

```python
def place_order(ticker: str, shares: int, config, limit_price: float | None = None, client=None) -> str:
    ...
    if config.use_limit_buy and limit_price is not None:
        spec = build_limit_buy(ticker, shares, limit_price)
    else:
        spec = build_market_buy(ticker, shares)
    ...
```

### Modified: `ApproveRejectView.approve`

```python
order_id = place_order(self.ticker, shares, self.config, limit_price=self.price)
```

`self.price` is already available. The limit price is the signal price recorded at scan time, which is the correct semantics — the operator approved the recommendation at that price, so the limit matches their expectation.

### Config addition

```python
# config.py — add to Config dataclass
use_limit_buy: bool = os.getenv("USE_LIMIT_BUY", "true").lower() == "true"
```

Add `USE_LIMIT_BUY=true` to `.env.example`. No validate() change needed — this is a behavioral flag, not a credential.

### What does NOT change

- `run_scan` — no change
- `send_recommendation` — no change
- `create_recommendation` / `queries.py` — no change
- `recommendations` schema — no change
- `SellApproveRejectView` — sell orders stay market orders; limit sells are out of scope

### Edge cases

- **Price staleness:** `self.price` was fetched at scan time and may be stale by the time the operator clicks Approve. This is a known limitation; limit orders already behave correctly in this case — the order simply won't fill if price has moved past the limit. Document in `.env.example` under `USE_LIMIT_BUY`.
- **`price <= 0`:** `compute_share_quantity` already guards against this; `place_order` will only be reached if `shares > 0`. No additional guard needed.
- **Dry run:** `place_order` is only called when `not self.config.dry_run`. The `use_limit_buy` branch is inside `place_order`, so dry run continues to log instead of calling Schwab.

---

## Fundamentals Extension Pattern

### Current state

`fetch_fundamental_info(yf_ticker)` returns `yf_ticker.info` — a single dict from the yfinance info endpoint. The dict contains static fundamentals (`trailingPE`, `dividendYield`, etc.) but not calendar or quarterly earnings series.

### New data needed

| Data | yfinance attribute | Return type | Use |
|------|-------------------|-------------|-----|
| Next earnings date | `yf_ticker.calendar` | dict with `Earnings Date` key | Embed field: warning if < 7 days away |
| Quarterly EPS series | `yf_ticker.quarterly_earnings` | DataFrame with `Earnings` column, index = quarter | P/E direction + EPS trend in prompt |

Both are separate network calls from `.info`. Both are blocking and must be wrapped in `asyncio.to_thread` — but because they are inside `fetch_fundamental_info` (which is already wrapped at its call sites), they inherit the thread context without additional wrapping at the call site in `run_scan`.

### Recommendation: extend `fetch_fundamental_info`, not separate calls

**Rationale:** `fetch_fundamental_info` is already the single yfinance fetch point for fundamental data in `run_scan`. Returning an enriched dict from one function keeps `run_scan`'s control flow unchanged and avoids adding two more `await asyncio.to_thread(...)` call sites in the scan loop. The function is already retried via `@_retry` — the new calls inherit that retry.

### New implementation shape

```python
@_retry
def fetch_fundamental_info(yf_ticker: yf.Ticker) -> dict:
    info = yf_ticker.info

    next_earnings = None
    try:
        cal = yf_ticker.calendar  # dict: {'Earnings Date': [Timestamp, ...], ...}
        dates = cal.get("Earnings Date") or []
        if dates:
            next_earnings = dates[0].date() if hasattr(dates[0], "date") else None
    except Exception:
        pass  # empty dict is common for stocks without upcoming earnings

    eps_trend = None
    pe_direction = None
    try:
        qe = yf_ticker.quarterly_earnings  # DataFrame: index=quarter, 'Earnings' column
        if qe is not None and not qe.empty and "Earnings" in qe.columns:
            series = qe["Earnings"].dropna().tolist()
            if len(series) >= 2:
                eps_trend = series[-4:]  # last 4 quarters, chronological
                pe_direction = "expanding" if series[-1] > series[-2] else "contracting"
    except Exception:
        pass

    return {**info, "next_earnings_date": next_earnings, "eps_trend": eps_trend, "pe_direction": pe_direction}
```

### Where the new fields flow

- `next_earnings_date` → `send_recommendation` → `build_recommendation_embed` → new "Earnings" embed field
- `eps_trend` + `pe_direction` → `build_prompt` → new lines in the Fundamentals block of the Claude prompt
- Neither field flows to `passes_fundamental_filter` — they are signal-enrichment only, not filter gates

### Changes required

| File | Change |
|------|--------|
| `screener/fundamentals.py` | Extend `fetch_fundamental_info` as above |
| `analyst/claude_analyst.py` | `build_prompt` adds P/E direction + EPS trend lines to Fundamentals block |
| `discord_bot/embeds.py` | `build_recommendation_embed` adds "Earnings" field when `next_earnings_date` is not None |
| `discord_bot/bot.py` | `send_recommendation` adds `next_earnings_date` kwarg; passes to embed builder |
| `main.py` | `run_scan` passes `info.get("next_earnings_date")` to `send_recommendation` |

All new kwargs must default to `None` to preserve backward compatibility with existing tests.

### Config addition

```python
earnings_warning_days: int = int(os.getenv("EARNINGS_WARNING_DAYS", "7"))
```

The embed builder computes days from today inline; no DB column needed. Warning is display-only — no behavioral change to scan filtering.

### yfinance silent-empty risk

`yf_ticker.calendar` frequently returns an empty dict for stocks without upcoming earnings. `yf_ticker.quarterly_earnings` returns `None` or an empty DataFrame for non-earnings issuers (REITs, some ETFs). Both cases are handled by the try/except pattern. Fields default to `None`; the embed and prompt builders treat `None` as absent (no field rendered).

---

## History Query Design

### What `/history` needs

Last 20 closed round-trips with ticker, exit date, exit price, entry price (cost basis), P&L%.

### Data model

`trades` has `side` ('buy'/'sell'), `cost_basis` (on sell rows only, set since v1.2), `price`, `shares`, `ticker`, `executed_at`, `recommendation_id`.

A closed trade is a matched buy + sell for the same ticker. The linkage is through `positions.avg_cost_usd` at the time of the sell — already captured in `trades.cost_basis` during `SellApproveRejectView.approve()`.

### Query design: no JOIN needed

The sell-side trade row is self-contained. It already contains entry price (`cost_basis`), exit price (`price`), shares, ticker, and executed_at. This matches the pattern of `get_trade_stats()` exactly:

```sql
SELECT
    ticker,
    executed_at AS exit_date,
    price       AS exit_price,
    shares,
    cost_basis  AS entry_price,
    ROUND((price - cost_basis) / cost_basis * 100, 2) AS pnl_pct
FROM trades
WHERE side = 'sell'
  AND cost_basis IS NOT NULL
  AND cost_basis > 0
ORDER BY executed_at DESC
LIMIT 20
```

### New query function

```python
def get_closed_trades(db_path: str, limit: int = 20) -> list[sqlite3.Row]:
    """Return the last `limit` closed sell trades with P&L data, newest first.

    Only returns rows where cost_basis IS NOT NULL AND cost_basis > 0
    (pre-migration rows and zero-cost anomalies excluded).
    """
    conn = get_connection(db_path)
    rows = conn.execute(
        """SELECT ticker, executed_at, price AS exit_price, shares,
                  cost_basis AS entry_price,
                  ROUND((price - cost_basis) / cost_basis * 100, 2) AS pnl_pct
           FROM trades
           WHERE side = 'sell' AND cost_basis IS NOT NULL AND cost_basis > 0
           ORDER BY executed_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return rows
```

### Edge cases

| Case | Behavior |
|------|----------|
| Sell trade with `cost_basis IS NULL` (pre-migration row) | Excluded by WHERE clause — consistent with `get_trade_stats()` |
| `cost_basis = 0` (anomaly) | Excluded by `cost_basis > 0` guard; avoids division by zero in pnl_pct |
| Dry-run sell trade | Included — no `dry_run` marker in `trades`; documented limitation carried from v1.2 |
| No closed trades yet | Returns empty list; bot sends "No trade history yet." plain message |
| Position re-opened after close | No issue — sell row's `cost_basis` is point-in-time from when the sell was approved |

### New slash command: `/history`

Add registration in `TradingBot.setup_hook()` and handler:

```python
async def _history_command(self, interaction: discord.Interaction):
    from database.queries import get_closed_trades
    rows = await asyncio.to_thread(get_closed_trades, self.config.db_path)
    if not rows:
        await interaction.response.send_message("No trade history yet.")
        return
    embed = build_history_embed(rows)
    await interaction.response.send_message(embed=embed)
```

`build_history_embed` goes in `discord_bot/embeds.py`. Each row gets one inline field: ticker, exit date, exit price, P&L%. Truncate at 20 rows (Discord embed limit is 25 fields; 20 rows fits within the limit).

### No schema migration needed

`cost_basis` column was added in v1.2. `get_closed_trades` is a pure read query. No new columns, no new tables.

---

## Test Mock Patterns

### 1. Analyst fallback logic

**Gap:** `analyze_ticker` fallback path (primary raises → fallback client used) is not tested.

**What to mock:** Mock `_call_api` directly, not the SDK clients. `_call_api` is the boundary between the retry decorator and the network. This avoids reproducing the exact OpenAI/Anthropic SDK response shape in every test.

```python
# tests/test_analyst_fallback.py
from unittest.mock import MagicMock, patch, call
from analyst.claude_analyst import analyze_ticker
from config import Config

def _make_config():
    c = Config()
    c.analyst_provider = "gemini"
    c.analyst_model = "gemini-2.5-flash"
    c.analyst_fallback_provider = "gemma"
    c.analyst_fallback_model = "gemma-3-27b-it"
    c.analyst_call_delay_s = 0
    return c

GOOD_RESPONSE = "SIGNAL: BUY\nREASONING: Strong fundamentals.\nCONFIDENCE: high"

def test_fallback_used_when_primary_fails():
    config = _make_config()
    primary_client = MagicMock()
    fallback_client = MagicMock()

    with patch("analyst.claude_analyst._call_api") as mock_call:
        mock_call.side_effect = [Exception("quota exceeded"), GOOD_RESPONSE]
        result = analyze_ticker("AAPL", {}, [], config, primary_client, fallback_client)

    assert result["signal"] == "BUY"
    assert result["provider_used"] == "gemma"
    assert mock_call.call_count == 2

def test_primary_used_when_it_succeeds():
    config = _make_config()
    with patch("analyst.claude_analyst._call_api", return_value=GOOD_RESPONSE) as mock_call:
        result = analyze_ticker("AAPL", {}, [], config, MagicMock(), MagicMock())
    assert result["provider_used"] == "gemini"
    assert mock_call.call_count == 1

def test_raises_when_primary_fails_and_no_fallback():
    config = _make_config()
    with patch("analyst.claude_analyst._call_api", side_effect=Exception("quota")):
        import pytest
        with pytest.raises(Exception, match="quota"):
            analyze_ticker("AAPL", {}, [], config, MagicMock(), fallback_client=None)

def test_raises_when_both_providers_fail():
    config = _make_config()
    with patch("analyst.claude_analyst._call_api", side_effect=Exception("fail")):
        import pytest
        with pytest.raises(Exception):
            analyze_ticker("AAPL", {}, [], config, MagicMock(), MagicMock())

def test_parse_error_does_not_trigger_fallback():
    """ValueError from parse_claude_response must propagate, not trigger fallback."""
    config = _make_config()
    bad_response = "INVALID RESPONSE FORMAT"
    with patch("analyst.claude_analyst._call_api", return_value=bad_response) as mock_call:
        import pytest
        with pytest.raises(ValueError):
            analyze_ticker("AAPL", {}, [], config, MagicMock(), MagicMock())
    assert mock_call.call_count == 1  # fallback was NOT called
```

The last test is critical: `parse_claude_response(text)` is called after the try/except block in `analyze_ticker` (lines 294-295 of `claude_analyst.py`). Parse errors propagate to the caller without triggering the fallback. This must be verified by the test.

### 2. Config validation

**Gap:** `Config.validate()` error paths are untested.

**Mock pattern:** No mocks needed. Construct `Config()` and overwrite individual fields — the dataclass reads env vars at instantiation, so field overwrite after construction is clean. This pattern is already used in `test_run_scan.py` (`c.db_path = ":memory:"`).

```python
# tests/test_config_validation.py
import pytest
from config import Config

def _valid_config():
    """Return a Config with all required fields set."""
    c = Config()
    c.schwab_app_key = "key"
    c.schwab_app_secret = "secret"
    c.schwab_account_hash = "hash"
    c.discord_token = "tok"
    c.discord_channel_id = 12345
    c.analyst_provider = "claude"
    c.analyst_api_key = "sk-key"
    return c

def test_validate_raises_on_missing_schwab_app_key():
    c = _valid_config()
    c.schwab_app_key = ""
    with pytest.raises(ValueError, match="SCHWAB_APP_KEY"):
        c.validate()

def test_validate_raises_on_missing_discord_token():
    c = _valid_config()
    c.discord_token = ""
    with pytest.raises(ValueError, match="DISCORD_TOKEN"):
        c.validate()

def test_validate_raises_on_missing_anthropic_key_for_claude_provider():
    c = _valid_config()
    c.analyst_provider = "claude"
    c.analyst_api_key = ""
    c.anthropic_api_key = ""
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        c.validate()

def test_validate_accepts_anthropic_api_key_as_alternative():
    c = _valid_config()
    c.analyst_provider = "claude"
    c.analyst_api_key = ""
    c.anthropic_api_key = "sk-real"
    c.validate()  # must not raise

def test_validate_passes_with_all_required_fields():
    _valid_config().validate()  # must not raise
```

### 3. run_scan quota exhaustion path

**Gap:** The path where both primary and fallback quota counters are exhausted causes `continue` — the ticker is skipped with a log warning. This path exists at `main.py` lines 126-141 (buy pass) and 265-270 (sell pass).

**Mock pattern:** Reuse the `_full_patch` context manager pattern from `test_run_scan.py`. The key is `queries.get_analyst_call_count_today` returning a value `>= analyst_daily_limit` for all calls. The mock is called twice per ticker (once for primary, once for fallback), so `return_value=5` satisfies both with `analyst_daily_limit=5`.

```python
# tests/test_run_scan_quota.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from config import Config
from main import run_scan

@pytest.mark.asyncio
async def test_run_scan_skips_ticker_when_quota_exhausted():
    bot = MagicMock()
    bot.send_recommendation = AsyncMock(return_value="msg_1")
    bot.send_ops_alert = AsyncMock()
    bot.send_sell_recommendation = AsyncMock()

    config = Config()
    config.db_path = ":memory:"
    config.analyst_daily_limit = 5
    config.analyst_provider = "gemini"
    config.analyst_fallback_provider = "gemma"

    fund_info = {"trailingPE": 20.0, "dividendYield": 0.03, "earningsGrowth": 0.10}
    tech_data = {"price": 150.0, "rsi": 55.0, "ma50": 140.0, "volume": 1_200_000, "avg_volume": 1_000_000}

    with patch("main.get_top_sp500_by_fundamentals", return_value=[]), \
         patch("main.get_universe", return_value=["AAPL"]), \
         patch("main.queries.expire_stale_recommendations"), \
         patch("main.queries.ticker_recommended_today", return_value=False), \
         patch("main.queries.has_open_position", return_value=False), \
         patch("main.queries.get_open_positions", return_value=[]), \
         patch("main.yf.Ticker"), \
         patch("main.create_analyst_client", return_value=MagicMock()), \
         patch("main.create_fallback_client", return_value=MagicMock()), \
         patch("main.fetch_macro_context", return_value={}), \
         patch("main.fetch_fundamental_info", return_value=fund_info), \
         patch("main.passes_fundamental_filter", return_value=True), \
         patch("main.fetch_news_headlines", return_value=["headline"]), \
         patch("main.queries.get_cached_analysis", return_value=None), \
         patch("main.queries.get_analyst_call_count_today", return_value=5), \
         patch("main.analyze_ticker") as mock_analyze, \
         patch("main.fetch_technical_data", return_value=tech_data), \
         patch("main.queries.create_recommendation") as mock_create_rec:

        await run_scan(bot, config)

    mock_analyze.assert_not_called()
    mock_create_rec.assert_not_called()
    bot.send_ops_alert.assert_awaited()  # zero-rec alert
```

**Additional quota test:** Add a second test for the cache-hit path — when `get_cached_analysis` returns a cached result, the quota guard is never reached, so `analyze_ticker` is not called even though quota is exhausted:

```python
@pytest.mark.asyncio
async def test_run_scan_uses_cache_when_quota_exhausted():
    """Cache hit bypasses quota guard entirely."""
    # Same setup as above but get_cached_analysis returns a cached BUY
    cached = {"signal": "BUY", "reasoning": "Cached.", "confidence": "high"}
    with patch(...), \
         patch("main.queries.get_cached_analysis", return_value=cached), \
         patch("main.queries.get_analyst_call_count_today", return_value=5), \
         patch("main.analyze_ticker") as mock_analyze, \
         patch("main.passes_technical_filter", return_value=True), \
         patch("main.queries.create_recommendation", return_value=1):
        await run_scan(bot, config)

    mock_analyze.assert_not_called()  # cache was used, no API call
    mock_create_rec.assert_called_once()  # recommendation was still posted
```

This test documents the intentional behavior: cache hits bypass both the quota guard and the quota increment (see `main.py` lines 120-124 — cached path skips directly to `tech_data` fetch).

### General mock discipline

Follow the project's established pattern: `patch("main.module.function")` (patch at import site), not `patch("module.function")`. `asyncio.to_thread` is transparent — patched functions are called synchronously at the `main.py` call site when the target is patched.

---

## Build Order

### Dependency chain

```
Config additions (use_limit_buy, earnings_warning_days)
    ↓
Phase 1: schwab_client/orders.py: build_limit_buy + place_order(limit_price)
         discord_bot/bot.py: ApproveRejectView.approve passes limit_price=self.price
    ↓
Phase 2: screener/fundamentals.py: extend fetch_fundamental_info
         analyst/claude_analyst.py: extend build_prompt
         discord_bot/embeds.py: add Earnings field to build_recommendation_embed
         discord_bot/bot.py: send_recommendation(next_earnings_date)
         main.py: pass next_earnings_date to send_recommendation
    ↓
Phase 3: database/queries.py: add get_closed_trades
         discord_bot/embeds.py: add build_history_embed
         discord_bot/bot.py: _history_command + setup_hook registration
    ↓
Phase 4: tests/test_analyst_fallback.py
         tests/test_config_validation.py
         tests/test_run_scan_quota.py
```

### Why this order

- Phase 1 is self-contained, touches only the order execution path, and can be validated with a unit test for `build_limit_buy` before any scan-loop changes.
- Phase 2 must follow Phase 1 because both touch `discord_bot/bot.py` and `discord_bot/embeds.py` — consolidating those changes reduces merge surface.
- Phase 3 is independent of Phases 1 and 2 (different files entirely) and can run in parallel if desired. Listed last only because it has the smallest risk surface.
- Phase 4 (tests) closes last. `test_run_scan_quota.py` tests existing code paths, so it benefits from the scan loop being confirmed stable after Phase 2.

---

## Component Boundaries: New vs Modified

### New functions/classes

| Item | File |
|------|------|
| `build_limit_buy` | `schwab_client/orders.py` |
| `get_closed_trades` | `database/queries.py` |
| `build_history_embed` | `discord_bot/embeds.py` |
| `_history_command` | `discord_bot/bot.py` |
| `tests/test_analyst_fallback.py` | `tests/` |
| `tests/test_config_validation.py` | `tests/` |
| `tests/test_run_scan_quota.py` | `tests/` |

### Modified functions

| Item | File | Change |
|------|------|--------|
| `place_order` | `schwab_client/orders.py` | Add `limit_price` kwarg; conditional spec builder |
| `fetch_fundamental_info` | `screener/fundamentals.py` | Merge calendar + quarterly_earnings into returned dict |
| `build_prompt` | `analyst/claude_analyst.py` | Add P/E direction + EPS trend lines to Fundamentals block |
| `build_recommendation_embed` | `discord_bot/embeds.py` | Add optional `next_earnings_date` field |
| `send_recommendation` | `discord_bot/bot.py` | Add `next_earnings_date` kwarg; pass to embed builder |
| `ApproveRejectView.approve` | `discord_bot/bot.py` | Pass `limit_price=self.price` to `place_order` |
| `run_scan` | `main.py` | Pass `info.get("next_earnings_date")` to `send_recommendation` |
| `Config` | `config.py` | Add `use_limit_buy`, `earnings_warning_days` |
| `setup_hook` | `discord_bot/bot.py` | Register `/history` command |

### Unchanged

- `database/models.py` — no schema changes required
- `screener/technicals.py`, `screener/exit_signals.py`, `screener/macro.py`, `screener/universe.py`
- `schwab_client/auth.py`, `place_sell_order`, `build_market_sell`
- `SellApproveRejectView` — sell path unchanged
- `run_scan_etf` — ETF path unchanged (no earnings date enrichment for ETFs)
- `build_etf_prompt`, `analyze_sell_ticker`, `analyze_etf_ticker`

---

## Confidence Assessment

| Area | Confidence | Basis |
|------|------------|-------|
| Limit order data flow | HIGH | Code read: `self.price` is in scope in `approve()`; schwab-py has limit order support |
| Fundamentals extension pattern | HIGH | Code read: `fetch_fundamental_info` returns a plain dict; yfinance `.calendar` and `.quarterly_earnings` are standard attributes |
| History query design | HIGH | Code read: `cost_basis` column exists on sell rows since v1.2; query mirrors `get_trade_stats` exactly |
| Test mock patterns | HIGH | Code read: existing patterns in `test_run_scan.py` are directly reusable |
| yfinance silent-empty behavior | MEDIUM | Known project fragility (documented in PROJECT.md); try/except pattern is the established mitigation |
| schwab-py `equity_buy_limit` function name | MEDIUM | schwab-py is a third-party wrapper — verify exact function name and signature against current schwab-py source before shipping Phase 1 |
