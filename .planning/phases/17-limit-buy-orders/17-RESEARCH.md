# Phase 17: Limit Buy Orders - Research

**Researched:** 2026-05-17
**Domain:** schwab-py order execution, SQLite schema migration, Discord UI wiring
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Add `use_limit_buy: bool = os.getenv("USE_LIMIT_BUY", "true").lower() == "true"` to the `Config` dataclass — follows the `dry_run` / `paper_trading` pattern exactly.
- **D-02:** `USE_LIMIT_BUY` defaults to `true` — operator must explicitly opt out via `.env`.
- **D-03:** `equity_buy_limit` price must be a formatted string `f"{price:.2f}"` — raw float triggers DeprecationWarning in schwab-py today, future TypeError.
- **D-04:** Limit order duration defaults to GTC — DAY + late-afternoon approval = silently unfilled order that leaves DB recommendation stuck in "approved".
- **D-05:** When `USE_LIMIT_BUY=false`, the existing `place_order` (market) path is used unchanged.
- **D-06:** When `DRY_RUN=true`, no real order is placed regardless of `USE_LIMIT_BUY`. Dry run always records `order_type='market'` and `limit_price=None` in the trades row.
- **D-07:** Price field extended with newline + "as of HH:MM". Reuses existing Price inline field.
- **D-08:** Timestamp is always shown regardless of `USE_LIMIT_BUY`.
- **D-09:** HH:MM is 24-hour local time captured in `main.py` buy-scan loop, passed as `scan_time: str` kwarg.
- **D-10:** Limit live confirmation: `"Approved: buying 9 shares of AAPL at $52.34 (limit, GTC)."`
- **D-11:** Market/dry-run confirmation stays unchanged: `"[DRY RUN] Approved: buying 9 shares of AAPL at $52.34."`
- **D-12:** Add `limit_price REAL` and `order_type TEXT` to `trades` in `initialize_db` — both CREATE TABLE and ALTER TABLE migration blocks.
- **D-13:** `order_type` stores `'limit'` or `'market'` (lowercase).

### Claude's Discretion

- Whether to add a `build_limit_buy` function alongside `build_market_buy` in `orders.py`, or extend `place_order` with an optional `limit_price` param.
- Exact import: `from schwab.orders.equities import equity_buy_limit` — verify function name.
- Test strategy: unit tests for `build_limit_buy` / `place_limit_order`, config flag routing, and embed price-field format with `scan_time` kwarg.

### Deferred Ideas (OUT OF SCOPE)

- GTC unfill detection — scan re-runs checking open GTC orders.
- Cancel button in Discord for GTC orders.
- Limit order for sell path.
- Real-time fill status polling via Schwab API.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RISK-01 | User can approve a buy and have a limit order placed at the signal price | equity_buy_limit verified; requires .set_duration(GTC) call — see Focus Area 1 |
| RISK-02 | USE_LIMIT_BUY config flag controls limit order behavior, defaults to true | Config pattern verified in config.py lines 36–37; bool-from-env pattern confirmed |
| RISK-03 | limit_price and order_type stored in trades table for audit trail | SQLite REAL NULL insertion verified; additive migration pattern confirmed in models.py |
| RISK-04 | Approve embed shows scan-time price with "as of HH:MM" timestamp | datetime already imported in main.py line 6; send_recommendation wiring point confirmed at bot.py line 275 |
</phase_requirements>

---

## Summary

Phase 17 replaces the market buy execution path with a GTC limit order when `USE_LIMIT_BUY=true`. All seven source files requiring changes have been read and verified. The implementation is straightforward — no architectural surprises — but one critical finding differs from the pre-phase assumption: **`equity_buy_limit` defaults to DAY duration, not GTC**. The `build_limit_buy` function must explicitly call `.set_duration(Duration.GOOD_TILL_CANCEL)` before `.build()`. This is confirmed against the live schwab-py 1.5.1 library.

The remaining changes follow established patterns already present in the codebase: additive DB migration, optional kwargs on embed builders, and `@patch` mocking in bot tests. The `_call_place_order` internal helper is reusable by `place_limit_order` without modification. The `datetime` import is already in `main.py`. The `scan_time` parameter slots into `send_recommendation` / `ApproveRejectView.__init__` / `build_recommendation_embed` as the fifth keyword argument, following the same pattern used by `earnings_date` in Phase 16.

**Primary recommendation:** Add a new `build_limit_buy(ticker, shares, limit_price_str)` function in `orders.py` (alongside `build_market_buy`) and a mirrored `place_limit_order` that calls `_call_place_order` directly. This keeps the two order types cleanly separated and matches the existing `place_order` / `place_sell_order` symmetry.

---

## Standard Stack

### Core (already installed)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| schwab-py | 1.5.1 | Schwab order placement API | Already in requirements.txt |
| SQLite (stdlib) | — | `limit_price` / `order_type` storage | Already used for all DB ops |
| discord.py | — | Embed and view rendering | Already used throughout |

**No new dependencies required.** [VERIFIED: live import + version check]

---

## Architecture Patterns

### Recommended Project Structure (no changes)

The phase modifies 7 existing files. No new modules are created.

```
schwab_client/
├── orders.py          ← add build_limit_buy, place_limit_order
config.py              ← add use_limit_buy field
discord_bot/
├── bot.py             ← ApproveRejectView.__init__ + approve handler + send_recommendation
├── embeds.py          ← build_recommendation_embed scan_time kwarg
database/
├── models.py          ← trades CREATE TABLE + 2x ALTER TABLE blocks
└── queries.py         ← create_trade limit_price + order_type params
main.py                ← scan_time capture + pass to send_recommendation
```

### Pattern 1: build_limit_buy (mirrors build_market_buy exactly)

```python
# Source: verified against schwab-py 1.5.1 live import
from schwab.orders.equities import equity_buy_limit
from schwab.orders.common import Duration

def build_limit_buy(ticker: str, shares: int, limit_price_str: str) -> dict:
    """Return the JSON spec for a GTC limit buy order (no network call)."""
    spec = equity_buy_limit(ticker, shares, limit_price_str)
    spec.set_duration(Duration.GOOD_TILL_CANCEL)
    return spec.build()
```

**Critical:** `.set_duration(Duration.GOOD_TILL_CANCEL)` is REQUIRED. Without it the default is `DAY`, which violates D-04 and SC-5.

### Pattern 2: place_limit_order (mirrors place_order exactly)

```python
# Source: verified against orders.py existing place_order / _call_place_order structure
def place_limit_order(ticker: str, shares: int, limit_price: float, config, client=None) -> str:
    """Place a GTC limit buy order via the Schwab API."""
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)
    limit_price_str = f"{limit_price:.2f}"   # D-03: formatted string, not raw float
    spec = build_limit_buy(ticker, shares, limit_price_str)
    try:
        resp = _call_place_order(client, config.schwab_account_hash, spec)
        order_id = resp.headers.get("Location", "").split("/")[-1]
        logger.info("Placed limit order %s: %s x%d @ %s", order_id, ticker, shares, limit_price_str)
        return order_id or None
    except Exception as exc:
        logger.error("Limit order placement failed for %s: %s", ticker, exc)
        raise RuntimeError(f"Limit order placement failed for {ticker}: {exc}") from exc
```

`_call_place_order` is reusable unchanged — it takes `(client, account_hash, spec)` and is agnostic to order type. [VERIFIED: orders.py line 54]

### Pattern 3: ApproveRejectView routing in approve handler

The routing slot is at line 73 of bot.py, immediately after the exposure guard returns and inside `if not self.config.dry_run:`:

```python
# Current (bot.py line 73-74):
if not self.config.dry_run:
    order_id = place_order(self.ticker, shares, self.config)

# Phase 17 replacement:
if not self.config.dry_run:
    if self.config.use_limit_buy:
        order_id = place_limit_order(self.ticker, shares, self.price, self.config)
    else:
        order_id = place_order(self.ticker, shares, self.config)
```

Both idempotency guards (shares=0 check at line 48–53, exposure guard at lines 57–69) run BEFORE this branch. They apply equally to both limit and market paths. No guard changes needed.

### Pattern 4: create_trade extension

```python
# Source: verified against queries.py lines 62-82
def create_trade(
    db_path: str,
    recommendation_id: int,
    ticker: str,
    shares: float,
    price: float,
    order_id: str | None,
    side: str = "buy",
    cost_basis: float | None = None,
    limit_price: float | None = None,    # NEW
    order_type: str = "market",          # NEW
) -> int:
    conn = get_connection(db_path)
    cursor = conn.execute(
        """INSERT INTO trades
               (recommendation_id, ticker, shares, price, order_id, side, cost_basis, limit_price, order_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (recommendation_id, ticker, shares, price, order_id, side, cost_basis, limit_price, order_type),
    )
    ...
```

`create_trade` has exactly ONE caller for buys: `ApproveRejectView.approve` (bot.py line 76). The sell path calls `create_trade` with `side='sell'` at `SellApproveRejectView.approve` (bot.py line 133) — the new kwargs need defaults (`limit_price=None`, `order_type='market'`) so that call site needs zero changes.

### Pattern 5: scan_time capture and wiring

```python
# Source: verified against main.py line 6 (datetime already imported)
# Capture once before the ticker loop, after macro_context fetch:
scan_time = datetime.now().strftime("%H:%M")

# Pass to send_recommendation (main.py ~line 245):
message_id = await bot.send_recommendation(
    ...
    earnings_date=earnings_date_embed,
    scan_time=scan_time,   # NEW
)
```

`datetime` is already imported at main.py line 6 as `from datetime import date, datetime, timezone`. No new import needed.

### Pattern 6: embed price field with scan_time

```python
# Source: verified against embeds.py lines 30, 43-44 (earnings_date pattern)
def build_recommendation_embed(
    ...,
    scan_time: str | None = None,   # NEW
) -> discord.Embed:
    price_value = f"${price:.2f}"
    if scan_time is not None:
        price_value += f"\nas of {scan_time}"
    embed.add_field(name="Price", value=price_value, inline=True)
```

This modifies the existing Price field value rather than adding a new field — exactly as specified in D-07.

### Anti-Patterns to Avoid

- **Raw float to equity_buy_limit:** `equity_buy_limit('AAPL', 5, 52.34)` raises `UserWarning` in schwab-py 1.5.1 and is documented as a future `TypeError`. Always pass `f"{price:.2f}"`.
- **Skipping .set_duration:** Default duration from `equity_buy_limit` is `DAY`. Omitting `.set_duration(Duration.GOOD_TILL_CANCEL)` silently creates a DAY order — violates D-04 and SC-5.
- **Labeling dry-run as limit in DB:** D-06 locks `order_type='market'` and `limit_price=None` for dry runs regardless of `use_limit_buy` setting.
- **Extending `place_order` with optional `limit_price`:** Mixing market and limit logic in one function increases branching complexity. The `place_sell_order` / `place_order` precedent favors separate functions.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| GTC limit order JSON spec | Custom dict construction | `equity_buy_limit(...).set_duration(Duration.GOOD_TILL_CANCEL).build()` | schwab-py validates all required fields; hand-built dicts silently pass invalid payloads to the API |
| Price string formatting | `str(price)` or `round()` | `f"{price:.2f}"` | Ensures exactly 2 decimal places; schwab-py accepts `'52.30'` not `'52.3'` |

---

## Focus Area Findings

### FA-1: `_call_place_order` reusability for limit orders

`_call_place_order(client, account_hash, spec)` at orders.py line 53–55 is fully order-type agnostic — it calls `client.place_order(account_hash, spec)` regardless of what `spec` contains. `place_limit_order` can call it identically to `place_order`. No changes to `_call_place_order`. [VERIFIED: orders.py source + live confirmation]

### FA-2: SQLite REAL type with NULL for limit_price

SQLite `REAL` columns accept `NULL` insertion via Python `None` cleanly. Confirmed live:
```
(1, None, 'market')   ← dry_run row: limit_price IS NULL
(2, 52.34, 'limit')   ← live limit row: limit_price = 52.34
```
[VERIFIED: live sqlite3 test in this session]

### FA-3: `equity_buy_limit` import path

`from schwab.orders.equities import equity_buy_limit` works in schwab-py 1.5.1. Function signature: `equity_buy_limit(symbol, quantity, price)`. Add to the existing import line in `orders.py`:
```python
# Current line 5:
from schwab.orders.equities import equity_buy_market, equity_sell_market
# Change to:
from schwab.orders.equities import equity_buy_limit, equity_buy_market, equity_sell_market
```
Also needs: `from schwab.orders.common import Duration` (new import). [VERIFIED: live import]

### FA-4: `place_limit_order` price parameter — format internally or accept pre-formatted?

CONTEXT.md D-03 locks the pre-formatted string requirement. Taking `price: float` and formatting internally (`limit_price_str = f"{price:.2f}"`) is the cleaner interface — callers pass the same `self.price` float they already hold, and the formatting responsibility stays inside `orders.py`. This avoids repeated format strings at every call site. Recommendation: `place_limit_order(ticker, shares, limit_price: float, config, client=None)` — format internally.

### FA-5: Idempotency guards run before order-type branch

Verified against bot.py:
- Shares=0 guard: lines 48–54 (returns before `order_id = None`)
- Exposure guard: lines 57–69 (returns before `order_id = None`)
- Order type branch: starts at line 73 (`if not self.config.dry_run:`)

Both guards fire unconditionally for both limit and market paths. No guard modifications needed.

### FA-6: `datetime.now().strftime("%H:%M")` — import status

`datetime` is already imported at main.py line 6:
```python
from datetime import date, datetime, timezone
```
No new import needed. `scan_time = datetime.now().strftime("%H:%M")` works immediately. [VERIFIED: main.py source]

### FA-7: `send_recommendation` → `ApproveRejectView` wiring

`send_recommendation` in bot.py lines 260–277. Current signature:
```python
async def send_recommendation(self, rec_id, ticker, signal, reasoning, price,
                               dividend_yield, pe_ratio, confidence=None, earnings_date=None)
```
Current `ApproveRejectView` instantiation at line 275:
```python
view = ApproveRejectView(rec_id, ticker, price, self.config)
```
Phase 17 adds `scan_time: str | None = None` as a kwarg to `send_recommendation` and passes it to both `build_recommendation_embed` (for the embed Price field) and `ApproveRejectView.__init__` (so the view stores it for future use if needed, or it stays embed-only per D-07–D-09). Since D-09 says `scan_time` is passed to `build_recommendation_embed`, and the confirmation message uses `self.price` (already stored), `ApproveRejectView.__init__` does NOT need `scan_time` stored as an instance variable — it's only needed by the embed builder at post time, not at approve time.

Revised wiring (research recommendation):
```python
# send_recommendation — bot.py
async def send_recommendation(self, ..., scan_time: str | None = None):
    embed = build_recommendation_embed(..., scan_time=scan_time)
    view = ApproveRejectView(rec_id, ticker, price, self.config)  # unchanged
    msg = await _send_message(channel, embed, view)
```
**Conflict with canonical_refs:** CONTEXT.md lines 75 and 127 both state `scan_time` is added to `ApproveRejectView.__init__`. D-09 only names `build_recommendation_embed` and `send_recommendation`. The approve handler never uses scan_time — the confirmation message uses `self.price`. Research recommendation: do NOT add `scan_time` to `ApproveRejectView.__init__` — it would be a dead stored attribute. However, because canonical_refs explicitly lists it, the planner must make a deliberate choice. See Open Questions OQ-3. [VERIFIED: bot.py lines 260–277]

### FA-8: Test mock patterns for `place_limit_order`

Existing pattern (test_discord_buttons.py lines 46, 71):
```python
@patch("discord_bot.bot.place_order")
```
This works because `place_order` is imported directly into `discord_bot.bot` at line 13:
```python
from schwab_client.orders import place_order, place_sell_order
```
`place_limit_order` will be added to the same import. New tests patch it the same way:
```python
@patch("discord_bot.bot.place_limit_order")
@patch("discord_bot.bot.place_order")
@patch("discord_bot.bot.queries")
async def test_approve_limit_live_calls_place_limit_order(...):
```
The `_make_config` helper in test_discord_buttons.py does not set `use_limit_buy` — the new tests need to set `c.use_limit_buy = True` or `False` explicitly. [VERIFIED: test_discord_buttons.py lines 9–15]

---

## Common Pitfalls

### Pitfall 1: equity_buy_limit defaults to DAY, not GTC

**What goes wrong:** `equity_buy_limit('AAPL', 5, '52.34').build()` produces `'duration': 'DAY'`. An order approved at 3:45pm silently expires at market close with no fill notification — the DB recommendation stays `'approved'`, position never opens.

**Why it happens:** The schwab-py default mirrors the most common retail use case (day order), not the algo use case (GTC).

**How to avoid:** Always call `.set_duration(Duration.GOOD_TILL_CANCEL)` before `.build()` in `build_limit_buy`. Unit test should assert `spec["duration"] == "GOOD_TILL_CANCEL"`.

**Warning signs:** `spec["duration"] == "DAY"` in test output.

### Pitfall 2: `bool("false")` is `True` in Python

**What goes wrong:** `use_limit_buy: bool = bool(os.getenv("USE_LIMIT_BUY", "true"))` evaluates any non-empty string (including `"false"`) as `True`.

**How to avoid:** Follow the established pattern: `.lower() == "true"` — confirmed in config.py lines 36–37 for `dry_run` and `paper_trading`.

### Pitfall 3: `create_trade` call in sell path needs no changes but must not break

**What goes wrong:** `SellApproveRejectView.approve` (bot.py line 133) calls `create_trade` with positional and keyword args. After adding `limit_price` and `order_type` params to `create_trade`, the sell call site must still work without modification.

**How to avoid:** Give both new params defaults: `limit_price: float | None = None` and `order_type: str = "market"`. The sell path will naturally receive `order_type='market'` and `limit_price=None` without any change.

### Pitfall 4: scan_time captures loop start time, not per-ticker time

**What goes wrong:** If `scan_time` is captured inside the ticker loop, each ticker gets a slightly different timestamp. The intent is one "as of" time for the whole scan.

**How to avoid:** Capture `scan_time = datetime.now().strftime("%H:%M")` once before the `for ticker in universe:` loop (same layer as `macro_context` fetch, main.py ~line 98).

### Pitfall 5: ETF path receives no scan_time — must not break

**What goes wrong:** `build_etf_recommendation_embed` has a different signature from `build_recommendation_embed`. The ETF path calls `send_etf_recommendation` which is entirely separate.

**How to avoid:** `scan_time` kwarg is added ONLY to `build_recommendation_embed` and `send_recommendation`. `build_etf_recommendation_embed` and `send_etf_recommendation` are not modified — consistent with D-08 (timestamp always shown for stock BUY path) and the ETF exclusion from Phase 16.

---

## Code Examples

### build_limit_buy (verified pattern)

```python
# Source: verified against schwab-py 1.5.1 live import
from schwab.orders.equities import equity_buy_limit, equity_buy_market, equity_sell_market
from schwab.orders.common import Duration

def build_limit_buy(ticker: str, shares: int, limit_price_str: str) -> dict:
    """Return the JSON spec for a GTC limit buy order (no network call)."""
    spec = equity_buy_limit(ticker, shares, limit_price_str)
    spec.set_duration(Duration.GOOD_TILL_CANCEL)
    return spec.build()
```

Expected output: `{'session': 'NORMAL', 'duration': 'GOOD_TILL_CANCEL', 'orderType': 'LIMIT', 'price': '52.34', ...}`

### Config addition (verified pattern)

```python
# Source: config.py lines 36-37 (dry_run pattern)
use_limit_buy: bool = os.getenv("USE_LIMIT_BUY", "true").lower() == "true"
```

### DB migration (verified additive pattern)

```python
# Source: database/models.py lines 76-118 (additive migration pattern)

# In CREATE TABLE IF NOT EXISTS trades (...):
#   Add two columns:
#     limit_price REAL,
#     order_type TEXT

# Two new ALTER TABLE blocks after existing blocks (line 118):
try:
    conn.execute("ALTER TABLE trades ADD COLUMN limit_price REAL")
    conn.commit()
except sqlite3.OperationalError:
    pass  # Column already exists

try:
    conn.execute("ALTER TABLE trades ADD COLUMN order_type TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass  # Column already exists
```

### Confirmation message routing (D-10, D-11)

```python
# In ApproveRejectView.approve, after order placement:
if self.config.dry_run:
    label = "[DRY RUN] "
    msg = f"{label}Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f}."
elif self.config.use_limit_buy:
    msg = f"Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f} (limit, GTC)."
else:
    msg = f"Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f}."
await interaction.response.send_message(msg)
```

### Trades row values matrix (D-06, D-13)

| Condition | order_type | limit_price |
|-----------|------------|-------------|
| DRY_RUN=true, use_limit_buy=true | `'market'` | `None` |
| DRY_RUN=true, use_limit_buy=false | `'market'` | `None` |
| DRY_RUN=false, use_limit_buy=true | `'limit'` | `self.price` (float) |
| DRY_RUN=false, use_limit_buy=false | `'market'` | `None` |

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (confirmed, 430 tests green) |
| Config file | pytest.ini or implicit |
| Quick run command | `pytest tests/test_discord_buttons.py tests/test_schwab_orders.py tests/test_discord_embeds.py -q` |
| Full suite command | `pytest -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| RISK-01 | `use_limit_buy=True` live → `place_limit_order` called | unit | `pytest tests/test_discord_buttons.py -k limit -x` | ❌ Wave 0 |
| RISK-01 | `use_limit_buy=False` live → `place_order` called (unchanged) | unit | `pytest tests/test_discord_buttons.py -k market -x` | ❌ Wave 0 |
| RISK-01 | `build_limit_buy` produces LIMIT order type | unit | `pytest tests/test_schwab_orders.py -k limit -x` | ❌ Wave 0 |
| RISK-01 | `build_limit_buy` produces GOOD_TILL_CANCEL duration | unit | `pytest tests/test_schwab_orders.py -k gtc -x` | ❌ Wave 0 |
| RISK-01 | `build_limit_buy` sets price as string | unit | `pytest tests/test_schwab_orders.py -k price -x` | ❌ Wave 0 |
| RISK-02 | `USE_LIMIT_BUY=false` → `config.use_limit_buy=False` | unit | `pytest tests/test_config.py -k limit -x` | ❌ Wave 0 (new file or extend existing) |
| RISK-03 | `create_trade` called with `limit_price=self.price, order_type='limit'` when limit live | unit | `pytest tests/test_discord_buttons.py -k limit_trade -x` | ❌ Wave 0 |
| RISK-03 | `create_trade` called with `limit_price=None, order_type='market'` on dry run | unit | `pytest tests/test_discord_buttons.py -k dry_run -x` | ✅ (extend existing) |
| RISK-04 | Price field value contains "as of HH:MM" when scan_time present | unit | `pytest tests/test_discord_embeds.py -k scan_time -x` | ❌ Wave 0 |
| RISK-04 | Price field value is plain `$X.XX` when scan_time absent | unit | `pytest tests/test_discord_embeds.py -k no_scan_time -x` | ✅ (extend existing) |

### Sampling Rate

- **Per task commit:** `pytest tests/test_discord_buttons.py tests/test_schwab_orders.py tests/test_discord_embeds.py -q`
- **Per wave merge:** `pytest -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] New test functions in `tests/test_discord_buttons.py` — covers RISK-01 (limit routing), RISK-03 (trades row values)
- [ ] New test functions in `tests/test_schwab_orders.py` — covers RISK-01 (build_limit_buy correctness, GTC duration, price string)
- [ ] New test functions in `tests/test_discord_embeds.py` — covers RISK-04 (scan_time in Price field)
- [ ] `tests/test_config.py` — covers RISK-02 (`USE_LIMIT_BUY=false` sets `use_limit_buy=False`); check if file exists first

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| schwab-py | Order placement | ✓ | 1.5.1 | — |
| sqlite3 | DB migration | ✓ | stdlib | — |
| discord.py | Embed rendering | ✓ | installed | — |
| pytest | Test suite | ✓ | confirmed (430 tests pass) | — |

No missing dependencies. All required tools verified in current environment.

---

## Assumptions Log

All claims in this research were verified against live code or live library behavior. No unverified assumptions remain — items requiring a planner decision were promoted to Open Questions.

---

## Open Questions

1. **Does `test_config.py` already exist?**
   - What we know: `tests/test_schwab_auth.py` and `tests/test_schwab_orders.py` exist. `test_config.py` was not found in the directory listing.
   - What's unclear: Whether RISK-02 testing goes in a new `test_config.py` or appended to an existing file.
   - Recommendation: Check `ls tests/` at plan time; if absent, create `tests/test_config.py` with the `USE_LIMIT_BUY` test and existing `Config.validate()` edge cases (pre-work for Phase 18 TEST-10).

2. **Does limit-buy routing apply to ETF approvals? (planner decision required)**
   - What we know: `send_etf_recommendation` (bot.py line 324) creates `ApproveRejectView(rec_id, ticker, price or 0.0, self.config)` — the same view class as stock buys. The approve routing branch `if self.config.use_limit_buy: place_limit_order(...)` will fire for ETF approvals too when `USE_LIMIT_BUY=true`.
   - What's unclear: CONTEXT.md scope says 'Scope excludes: ETF scan path' but this refers to the scan loop and embed, not the Approve handler. ETFs and stocks share `ApproveRejectView.approve` unconditionally.
   - Options: (a) Accept ETF limit buys — strictly safer than market, no extra code at the view layer. (b) Gate on `recommendation.asset_type` queried from DB via `rec_id` — adds a DB read inside the handler. (c) Separate ETF approve view — significant scope increase.
   - Recommendation: Option (a). ETF limit buys are strictly safer. The scope exclusion in CONTEXT.md refers to scan/embed, not execution. Document the behavior in the plan so it is not a surprise.

3. **Should `scan_time` be stored on `ApproveRejectView.__init__` as an instance variable?**
   - What we know: CONTEXT.md canonical_refs line 75 and code_context line 127 both explicitly state `ApproveRejectView.__init__` needs `scan_time: str` as a 5th param. D-09 only mentions `build_recommendation_embed` and `send_recommendation`. The approve handler uses `self.price` — `scan_time` is never consumed at approve time.
   - What's unclear: Whether canonical_refs pre-dates the D-10/D-11 confirm-message decision (which uses `self.price`, not scan_time) or intentionally specifies a stored-but-unused param.
   - Recommendation: Do NOT add to `ApproveRejectView.__init__`. Adding it would be a dead stored attribute. If the planner follows canonical_refs verbatim and adds it as a kwarg-with-None-default, that is also safe — zero behavioral difference.

---

## Sources

### Primary (HIGH confidence)

- Live schwab-py 1.5.1 import — `equity_buy_limit` signature, default DAY duration, Duration enum, DeprecationWarning on float price [VERIFIED]
- Live sqlite3 test — REAL column accepts NULL cleanly [VERIFIED]
- `schwab_client/orders.py` source — `_call_place_order`, `place_order`, `build_market_buy` patterns [VERIFIED]
- `discord_bot/bot.py` source — `ApproveRejectView.__init__` line 39, `approve` handler lines 47–92, `send_recommendation` lines 260–277 [VERIFIED]
- `discord_bot/embeds.py` source — `build_recommendation_embed` Price field line 30, earnings_date pattern lines 43–44 [VERIFIED]
- `database/models.py` source — additive migration pattern lines 76–118 [VERIFIED]
- `database/queries.py` source — `create_trade` signature lines 62–82 [VERIFIED]
- `main.py` source — datetime import line 6, `send_recommendation` call site line 245, ticker loop structure [VERIFIED]
- `tests/test_discord_buttons.py` source — mock patterns for `place_order`, `queries`, `_make_config` helper [VERIFIED]
- `tests/test_schwab_orders.py` source — existing `build_market_buy` test structure [VERIFIED]
- `tests/test_discord_embeds.py` source — embed test patterns [VERIFIED]

### Secondary (MEDIUM confidence)

None — all claims were verifiable from live code or live library.

---

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH — schwab-py 1.5.1 installed and verified live
- Architecture: HIGH — all integration points verified against actual source files
- Pitfalls: HIGH — DAY vs GTC confirmed by live `build()` output; bool trap confirmed by Python semantics

**Research date:** 2026-05-17
**Valid until:** 2026-06-17 (schwab-py 1.5.1 API stable; Discord.py stable)
