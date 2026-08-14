# Design: Live-Trading Safety Hardening (Codex Phase 1)

**Date:** 2026-08-14
**Status:** Approved
**Milestone:** v1.4 (candidate)
**Source:** `codex_recommendations.md` — findings 1, 2, 3, 10, and roadmap item 1.6

---

## Summary

The bot currently runs with `DRY_RUN=false` and `PAPER_TRADING=false` against a real
Schwab account. The database holds 0 recommendations, 0 trades, and 0 positions, so no
live order has ever been placed — the risk is entirely prospective.

Four defects make the current live path unsafe:

1. `PAPER_TRADING` is inert. `schwab_client/auth.py:20` never receives it; it appears in
   exactly one place, the startup warning at `main.py:703`. Schwab's Trader API has **no
   paper/sandbox endpoint**, so there is nothing the flag could point at. `README.md:58`,
   `README.md:129`, `CLAUDE.md:87`, `CLAUDE.md:109` and four `.planning/codebase/*.md`
   files all currently assert a protection that cannot exist.
2. Approvals are unauthenticated and unvalidated. `ApproveRejectView.approve`
   (`discord_bot/bot.py:55`) never inspects `interaction.user`, guild, or channel;
   `claim_recommendation` (`database/queries.py:36`) filters on `status='pending'` with no
   `expires_at` predicate; quantity, exposure, and limit price all derive from the
   scan-time price with no live quote.
3. Concurrent scans can duplicate. `bot.py:386` and `bot.py:394` spawn unrestricted
   `asyncio.create_task` scans, and there is no uniqueness constraint on active
   recommendations.
4. Portfolio exposure is computed from `avg_cost_usd` (because `last_price` is normally
   `NULL`) against a scan-price new position — stale in both terms simultaneously.

This design fixes all four plus adds emergency controls. It explicitly does **not** build
the execution ledger, the research harness, or the signal redesign; those are sequenced in
the Backlog section.

---

## Scope

**Modified**
- `config.py` — `EXECUTION_MODE` enum, new safety fields, `validate()` rules
- `discord_bot/bot.py` — both approval views, both scan commands
- `database/queries.py` — claim predicate, day-notional query
- `database/models.py` — partial unique index
- `schwab_client/quotes.py` — **new**, live quote fetch
- `risk/preflight.py` — **new**, the pure guard function
- `main.py` — startup warning, scan lock wiring
- `README.md`, `CLAUDE.md`, `.env.example`, `.planning/codebase/*.md` — doc debt

**Not in scope:** order lifecycle states, fill confirmation, real fill prices,
backtesting, ETF gating, cache keying, universe ranking, exit rules.

---

## Architecture

### 1. `EXECUTION_MODE` replaces `dry_run` + `paper_trading`

Two booleans encode four states; only two are real, and `live + paper` is the dangerous
fiction. One enum makes the illegal states unrepresentable.

| Value | Meaning | Status |
|---|---|---|
| `dry_run` | Buttons log; no broker order path reachable | **Default** |
| `live` | Real orders against the real Schwab account | Opt-in |
| `simulated` | Reserved for the Phase 2 broker adapter | Fails startup today |

```python
execution_mode: str = _env_str("EXECUTION_MODE", "dry_run")

def __post_init__(self):
    self.dry_run = self.execution_mode != "live"
```

`dry_run` survives as a **derived, assignable** field. It appears 7 times in source (3
files) but 48 times in tests, and CLAUDE.md documents `config.dry_run = True` as the
required convention for any test touching `run_scan`. Deriving it in `__post_init__`
keeps every one of those working while making the environment surface a single value.

`paper_trading` is deleted outright.

**Migration is loud, not silent.** `validate()` raises if `DRY_RUN` or `PAPER_TRADING`
appear in `os.environ` at all:

```
DRY_RUN and PAPER_TRADING have been replaced by EXECUTION_MODE.
Your current settings map to: EXECUTION_MODE=live
Remove both legacy variables from .env.
```

Deriving the new value silently from legacy variables would reintroduce exactly the class
of unopted-into safety this phase exists to eliminate. `EXECUTION_MODE=simulated` raises
`NotImplementedError` naming Phase 2.

### 2. New config fields

| Field | Env var | Default | Purpose |
|---|---|---|---|
| `execution_mode` | `EXECUTION_MODE` | `dry_run` | Above |
| `trading_enabled` | `TRADING_ENABLED` | `true` | Kill switch boot default |
| `allowed_discord_user_ids` | `ALLOWED_DISCORD_USER_IDS` | `""` | Comma-separated ints |
| `discord_guild_id` | `DISCORD_GUILD_ID` | `0` | Expected guild |
| `approval_price_tolerance_pct` | `APPROVAL_PRICE_TOLERANCE_PCT` | `2.0` | Drift ceiling |
| `quote_max_age_s` | `QUOTE_MAX_AGE_S` | `60` | Quote staleness limit |
| `max_daily_notional_usd` | `MAX_DAILY_NOTIONAL_USD` | `2000.0` | Daily spend ceiling |

`validate()` requires a non-empty `allowed_discord_user_ids` when
`execution_mode == "live"`. An empty allowlist means **deny all**, never allow all — a
misconfigured allowlist must fail closed.

### 3. `risk/preflight.py` — the guard table

One pure function, no I/O:

```python
def evaluate_trade(request, quote, positions, day_notional, config, now) -> Decision
```

```python
@dataclass(frozen=True)
class TradeRequest:
    rec_id: int
    ticker: str
    side: str                    # "buy" | "sell"
    scan_price: float
    expires_at: datetime         # UTC
    user_id: int
    guild_id: int | None
    channel_id: int | None

@dataclass(frozen=True)
class Quote:
    price: float
    as_of: datetime              # UTC

@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason_code: str
    message: str                 # user-facing
    shares: int
    effective_price: float
```

`TradeRequest`, `Quote`, and `Decision` all live in `risk/preflight.py`, which imports
nothing from `schwab_client` or `discord`. `schwab_client/quotes.py` imports `Quote` from
`risk/preflight.py`, not the reverse — the pure module stays at the bottom of the
dependency graph so its tests need no broker or Discord imports at all.

`check_authorization(request, config) -> Decision | None` is a module-level function in the
same file, returning `None` when authorized. `evaluate_trade` calls it as guard 1, and
`ApproveRejectView` calls it directly pre-defer.

Guards evaluate in this fixed order; first failure wins.

| # | `reason_code` | Fails when | Finding |
|---|---|---|---|
| 1 | `unauthorized` | `user_id` not in allowlist, or guild/channel mismatch | 2 |
| 2 | `trading_disabled` | kill switch engaged | 1.6 |
| 3 | `expired` | `now >= expires_at` | 2 |
| 4 | `quote_unavailable` | quote missing, or `now - as_of > quote_max_age_s` | 2 |
| 5 | `price_drift` | `abs(quote - scan)/scan > tolerance` | 2 |
| 6 | `size_zero` | shares at the **live** quote round to 0 | — |
| 7 | `daily_notional` | today's approved notional + this order > ceiling | 1.6 |
| 8 | `portfolio_exposure` | exposure at the **live** quote > `max_portfolio_usd` | 10 |

Two ordering decisions are load-bearing:

- **Authorization is guard 1** so an unauthorized clicker learns nothing about the book —
  not exposure, not whether the recommendation expired. Rejection messages are a side
  channel; ordering secret-preserving checks first is the same discipline as constant-time
  comparison.
- **Guards 6–8 use the live quote, never `request.scan_price`.** This is what actually
  closes finding 10: today's check at `bot.py:69` sums `avg_cost_usd` against a scan-price
  new position, so a rising market inflates exposure past the ceiling in both terms at
  once.

Every input is a parameter, never a lookup. That makes the boundary cases — drift at
exactly tolerance, exposure exactly at the ceiling, a quote exactly at max age — table
entries rather than mock setups.

`day_notional` is supplied by a new query in `database/queries.py`:

```sql
SELECT COALESCE(SUM(shares * price), 0.0) FROM trades
 WHERE side = 'buy'
   AND date(executed_at, 'localtime') = date('now', 'localtime')
```

Buys only — a sell does not consume the daily buy budget. This one **is** a calendar-day
comparison, so it uses `'localtime'` per the CLAUDE.md convention, unlike the expiry
predicate in §6. The two live three sections apart and use opposite modifiers on purpose;
implementers should not "fix" either to match the other.

**Sell path:** `SellApproveRejectView` runs guards 1–4 only. Drift and exposure do not
apply when reducing risk. Leaving the sell button unauthenticated while hardening the buy
button would be a half-fix.

### 4. `schwab_client/quotes.py`

```python
def get_quote(ticker: str, config, client=None) -> Quote
```

Wraps `schwab.client.Client.get_quote(symbol)` (confirmed present in schwab-py 1.5.1),
mirroring the structure of `schwab_client/orders.py` — lazy `get_client` import, `@_retry`
with the same tenacity policy, `RuntimeError` on failure. Real-time and from the same
broker that executes, which yfinance's delayed feed is not.

Called from the button via `asyncio.to_thread`, per the project's rule that no synchronous
broker or yfinance I/O touches the event loop.

### 5. Approval path, rewired

Current order is quantity → exposure → claim → defer → order. Three of those use the stale
price, and the claim lands before anything meaningful is verified.

```
1. check_authorization(...)      pure, pre-defer → ephemeral reject
2. interaction.response.defer()  buys the 3s window before network I/O
3. fetch live quote              asyncio.to_thread
4. gather positions + day_notional
5. evaluate_trade(...) -> Decision
6. claim_recommendation(...)     atomic, expiry now in the SQL predicate
7. place order at decision.effective_price / .shares
8. record trade + position
```

**Authorization runs before `defer()`** because it is pure and instant, letting an
unauthorized click get a private ephemeral reply while a legitimate one gets the public
thread. `evaluate_trade` re-checks it as guard 1; the pre-check is pure defence in depth.

**The claim moves after the guards.** Today a drift-rejected recommendation would already
be stamped `approved` and lost.

### 6. Expiry as a SQL predicate

```sql
UPDATE recommendations SET status = ?
 WHERE id = ? AND status = 'pending' AND expires_at > datetime('now')
```

A Python-side `if now >= expires_at` before the claim is a TOCTOU race against the expiry
sweep; the same atomic statement is the only correct version.

This comparison is **UTC**, not `localtime`. `expires_at` is stored as
`datetime('now', '+24 hours')` (`models.py:52`), which is UTC. CLAUDE.md's rule about
`'localtime'` day bucketing applies to *calendar-day* comparisons and must not be applied
here.

### 7. Concurrency

Two layers, because they fail differently.

**One shared `asyncio.Lock`** on the bot instance covering `run_scan` *and* `run_scan_etf`
— not one lock each. A symbol can appear in both paths, so separate locks would not
prevent the duplicate. `/scan` and `/scan_etf` reply "a scan is already running" rather
than spawning a second task.

**A partial unique index**, the real backstop since a lock protects only one process:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_pending_rec_per_ticker
  ON recommendations(ticker) WHERE status = 'pending';
```

Insert catches `sqlite3.IntegrityError` and skips the ticker. The table has 0 rows, so
this applies with no backfill.

### 8. Kill switch

`TRADING_ENABLED=false` as the boot default, plus a `/halt` slash command flipping an
in-memory flag on the running process (and `/resume` to clear it), resetting to the env
value on restart. A kill switch that needs a restart is not much of a kill switch while
watching a bad fill. `/halt` is itself subject to the authorization allowlist.

---

## Error handling

Everything fails **closed**.

| Failure | Behavior |
|---|---|
| Quote fetch raises or returns stale | `quote_unavailable`, no order, **no fallback to scan price** |
| Order placement raises | Release claim to `pending`, tell the user to verify in Schwab (current behavior, already correct) |
| Duplicate pending insert | `IntegrityError` caught, ticker skipped, logged |
| Scan already running | Slash command replies without spawning |
| Legacy env var present | Startup raises with the mapping message |

Falling back to the scan price on a quote outage would silently restore the exact bug this
design removes, which is why it is called out rather than left implicit.

**Drift rejection leaves the recommendation `pending`**, so the next scan re-evaluates it
with fresh technicals. User-facing message:

```
⚠️ Blocked: AAPL moved +3.1% since scan
   Scan price:  $184.20  (09:00)
   Live quote:  $189.91  (14:32)
   Tolerance:   2.0%

Order NOT placed. Recommendation left pending;
next scan will re-evaluate with fresh technicals.
```

---

## Testing

TDD throughout. Maps nearly one-to-one onto the missing-test list in
`codex_recommendations.md`.

| File | Covers |
|---|---|
| `test_broker_isolation.py` | Full scan + approve under `dry_run`, asserting `place_order`, `place_limit_order`, `place_sell_order` are never called. **Write this first** — it is the test that would have caught the original `PAPER_TRADING` defect. |
| `test_preflight.py` | All 8 guards table-driven, plus boundaries: drift at exactly tolerance, exposure exactly at ceiling, quote exactly at max age, shares rounding to 0 |
| `test_execution_mode.py` | Legacy var rejection, `simulated` startup failure, `dry_run` derivation, empty allowlist under `live` |
| `test_approval_flow.py` | Unauthorized user, wrong guild, expired button, drift block, quote outage, sell-path guards 1–4 |
| `test_scan_lock.py` | Concurrent `/scan` rejection, shared lock across stock and ETF |
| `test_claim_expiry.py` | SQL-level expiry claim, unique-index duplicate rejection |

Existing tests that must keep passing unchanged: the 48 `dry_run` references, and
`test_config.py`'s construction-time env reads.

Also configure `asyncio_default_fixture_loop_scope` in `pytest.ini` to clear the
pytest-asyncio warning noted in the review.

---

## Build sequence

1. `test_broker_isolation.py` against current code — expect it to pass in `dry_run`, then
   keep it green through every subsequent step
2. `EXECUTION_MODE` + config fields + `validate()` rules + doc debt
3. `schwab_client/quotes.py`
4. `risk/preflight.py` with its full test table
5. Rewire `ApproveRejectView`, then `SellApproveRejectView`
6. SQL claim predicate + partial unique index
7. Scan lock + `/halt` and `/resume`
8. Full `pytest -q` and Ruff

---

## Backlog — Codex findings 5–13, sequenced

Not built here. Recorded so the review does not rot as an untracked file.

**Next: execution ledger (Codex Phase 2, findings 4, 10 remainder).** Separate orders from
trades from positions; track `submitted / working / partially_filled / filled / cancelled /
rejected`; poll broker order status; build positions only from confirmed executions; record
real fill price, quantity, time, fees. Until this lands, `/stats` win rates are computed
from quoted prices and cannot evaluate the strategy. This blocks all validation work.

**Then: analysis integrity (findings 5, 12).** Cache key must include the feature snapshot,
model, provider, and prompt version, with a TTL; prefer caching raw news over the final
decision. Delimit headline text as untrusted data (prompt-injection surface at
`analyst/claude_analyst.py:121`). Record provider, model, prompt version, inputs, and
response per recommendation. Add a deterministic ETF technical gate so an LLM `BUY` is not
the sole authority.

**Then: research harness (Codex Phase 3, finding 6).** Point-in-time, event-driven,
walk-forward, survivorship-aware. Depends on the execution ledger for a truthful
comparison baseline.

**Then: signal redesign (Codex Phase 4, findings 7, 8, 9, 11, 13).** Scale-independent
factors replacing raw EPS/ROE rank sums; explicit missing-data policy; protective stops and
time exits alongside the RSI+MACD reversal rule; ETF overlap awareness; completed-bar
volume comparison.

**Standing constraint from finding 6:** no backtest has been run and no forward sample
exists. The 546 passing tests validate software behavior, not predictive power. Every
recommendation remains an unvalidated research lead regardless of how safe the execution
path becomes. This design makes the bot *safe to operate*; it does not make it *worth
operating*.
