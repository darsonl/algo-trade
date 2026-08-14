# Design: Live-Trading Safety Hardening (Codex Phase 1) — v3

**Date:** 2026-08-14 (v3 revised 2026-08-15)
**Status:** Approved (v3, revised after third external review)
**Milestone:** v1.4 (candidate)
**Source:** `docs/superpowers/codex_recommendations.md` — findings 1, 2, 3, 10, roadmap item 1.6

---

## Summary

The bot ran with `DRY_RUN=false` / `PAPER_TRADING=false` against a real Schwab account.
`algo_trade.db` holds 0 recommendations, 0 trades, 0 positions — no live order has ever been
placed. **As of 2026-08-15 `.env` is disarmed (`DRY_RUN=true`)** pending this phase; the risk
is entirely prospective.

Verified defects:

1. **`PAPER_TRADING` is inert.** `schwab_client/auth.py:20` never receives it; it appears only
   in the startup warning at `main.py:703`. Schwab's Trader API has no paper endpoint, so
   there is nothing for it to select. `README.md:58`, `README.md:129`, `CLAUDE.md:87`,
   `CLAUDE.md:109` and four `.planning/codebase/*.md` files assert a protection that cannot
   exist.
2. **Approvals are unauthenticated and unvalidated.** `ApproveRejectView.approve`
   (`discord_bot/bot.py:55`) never inspects `interaction.user`; `claim_recommendation`
   (`database/queries.py:36`) has no `expires_at` predicate; quantity, exposure, and price all
   derive from the scan-time quote.
3. **Concurrent scans can duplicate.** `bot.py:386` / `:394` spawn unlocked
   `asyncio.create_task` scans; no uniqueness constraint covers active recommendations.
4. **Exposure is doubly stale** — `avg_cost_usd` for held positions (because `last_price` is
   normally `NULL`) against a scan-price new position.
5. **Broker reads fail open.** `get_positions` (`schwab_client/orders.py:135`) never calls
   `raise_for_status`; an error body parses to `[]`, i.e. "the account holds nothing."

### Revision history

**v2** addressed 2 Critical and 5 High defects found in v1 by external review:

| v1 defect | Fix |
|---|---|
| `@_retry` on `_call_place_order` could resubmit an order the broker already accepted; v1 called this "already correct" | Retry removed from submission; ambiguous outcomes become `submit_unknown` and are never auto-reopened |
| `EXECUTION_MODE` was never enforced where orders leave; `schwab_client/orders.py` was not even in scope | Sink guard requiring **both** flags to agree; `orders.py` added to scope |
| Daily-notional and portfolio guards were check-then-act across concurrent approvals | Whole guard→claim→submit sequence serialized under one lock |
| The pending-only unique index stopped protecting the moment status became `approved` | Index covers `('pending','approved')`; recommendations move to `completed` when their order terminates |
| A validated quote could not constrain a market fill | All buys are **limit orders at `quote × (1 + buffer)`, DAY duration** |
| `TRADING_ENABLED` documented as `true` in one place and `false` in another; `/resume` not allowlisted | Default `true`; both `/halt` and `/resume` allowlisted |
| No same-symbol guard; sell quantity never revalidated | Guards added |

### v3 revisions

The third external review found **3 Critical defects and 1 High defect in v2 itself**. A
fourth was found while re-reading v2 against `master`. All five are fixed here.

| # | v2 defect | Fix | Section |
|---|---|---|---|
| C1 | **Kill switch never reaches the sink.** §10 claimed "the sink re-reads it," but the §2 predicate was `execution_mode != "live" or dry_run` — no `trading_enabled` term anywhere in it. `/halt` during a pending approval did nothing. | New `risk/kill_switch.py` holds the runtime flag; the sink predicate reads it | §2, §10 |
| C2 | **`submit_unknown` reserved no capital and could not be resolved.** Absent from `COMMITTING_ORDER_STATUSES` (`execution-ledger.md:222`), so a possibly-live $500 order counted $0 against both ceilings. No operation could resolve one — `/reconcile` reads positions, which cannot distinguish "working order" from "no order." | Added to the committing set; `/resolve` uses `get_orders_for_account` to settle it | §3, §4 |
| C3 | **Broker reads fail open.** `get_positions` has no `raise_for_status`; a 401/429/500 body has no `securitiesAccount` key, so `parse_positions` returns `[]`. Exposure reads zero, the holding check sees nothing — **a broker outage opens the gate.** | `raise_for_status` on every broker read, strict parse, new `broker_unavailable` guard | §5, §7 |
| C4 | **Daily ceiling buckets on `localtime`.** `execution-ledger.md:304` uses `date(submitted_at,'localtime') = date('now','localtime')`, and v2 §8 explicitly *defended* it. Commit `36761da` invalidated that convention later the same day. On this UTC+8 host the 21:45 and 03:30 scans are **one** US session but **two** local dates, so `MAX_DAILY_NOTIONAL_USD` resets mid-session — $4,000 through a $2,000 ceiling. Identical to the bug that was doubling `ANALYST_DAILY_LIMIT`. | Range predicate over `market_session_bounds_utc()` | §9 |
| H1 | **`approved` blocks a ticker permanently.** The `completed` transition the index depends on existed nowhere — not in the repo, not in `/reconcile`, not in the ledger plan. | `recommendations.broker_order_id` + a named sweep, pulled into Phase 1 scope | §11 |

**Also in v3:** sells are bounded by limit orders too (v2 bounded only buys — §6), and a
process rule was added to prevent the defect class that produced C1, C2, and H1 (§14).

---

## The scheduling problem

```
SCAN_TIMES = 21:45, 03:30      (machine-local; SCAN_TIMEZONE unset)
```

**Stock scans run when US markets are closed.** Recommendations are posted overnight and
approved in the morning, plausibly before the 09:30 open.

This breaks an assumption in v1. The drift guard compares the scan price to a "live" quote —
but between 21:45 and 09:30 both are the *same previous close*. Drift computes to ≈0, the
guard passes trivially, and a market order then absorbs the entire opening gap. A
`QUOTE_MAX_AGE_S` of 60 seconds would meanwhile reject every pre-open approval.

**A quote is fresh relative to the next opportunity to trade, not to wall-clock age.** v2
therefore makes quote staleness session-aware and relies on the limit price — not the drift
check — as the binding price control outside regular hours.

Note that these same two scan times are what make C4 dangerous: they straddle local midnight
but sit inside one US session.

---

## Scope

**Modified**
- `config.py` — `EXECUTION_MODE`, new safety fields, `validate()` rules
- `schwab_client/orders.py` — **sink guard**, retry removal, `raise_for_status`, DAY-duration
  limit orders (buy **and** sell), `get_order_status`, `find_recent_orders`
- `schwab_client/quotes.py` — **new**, live quote fetch
- `risk/preflight.py` — **new**, the pure guard table
- `risk/kill_switch.py` — **new**, runtime kill-switch state (§10)
- `discord_bot/bot.py` — both approval views, both scan commands, `/halt`, `/resume`, `/resolve`
- `database/queries.py` — claim predicate, day-notional query, recommendation completion
- `database/models.py` — partial unique index, `broker_order_id` column
- `main.py` — startup warning, scan lock wiring, terminal sweep
- `README.md`, `CLAUDE.md`, `.env.example`, `.planning/codebase/*.md` — doc debt

**Newly in scope for v3 (was Workstream A):** the minimum order-status read needed to make
`completed` real — one column, one broker call, one sweep function. See §11 for why this
cannot be deferred.

**Not in scope:** the full `orders` table, fill quantities, real fill prices (Workstream A);
backtesting; ETF gating; cache keying; universe ranking; exit rules.

---

## Architecture

### 1. `EXECUTION_MODE` replaces `dry_run` + `paper_trading`

| Value | Meaning | Status |
|---|---|---|
| `dry_run` | Buttons log; the sink refuses to submit | **Default** |
| `live` | Real orders against the real Schwab account | Opt-in |
| `simulated` | Reserved for the Workstream A broker adapter | Fails startup today |

```python
execution_mode: str = _env_str("EXECUTION_MODE", "dry_run")

def __post_init__(self):
    self.dry_run = self.execution_mode != "live"
```

`dry_run` remains a **derived, assignable** field. It appears 7 times in source but 23 test
sites set it to `True` specifically to stay off live Schwab, and CLAUDE.md documents that as
the required convention. Making it read-only would silently strip protection from any test
that was missed — see §2 for how safety is preserved without that churn.

`paper_trading` is deleted outright.

**Migration is loud.** `validate()` raises if `DRY_RUN` or `PAPER_TRADING` appear in
`os.environ` at all:

```
DRY_RUN and PAPER_TRADING have been replaced by EXECUTION_MODE.
Your current settings map to: EXECUTION_MODE=dry_run
Remove both legacy variables from .env.
```

Silently deriving the new value from legacy variables would reintroduce exactly the
unopted-into safety this phase exists to remove. `EXECUTION_MODE=simulated` raises
`NotImplementedError` naming Workstream A.

### 2. The sink guard — all three conditions must agree  *(fixes C1)*

v1's central failure: every guard lived in a pure function that *advises*, and nothing
enforced anything at the point of irreversibility. All three order wrappers reached
`client.place_order` unconditionally.

v2 added a sink guard but checked only the two *mode* flags. §10 simultaneously claimed the
sink re-read the kill switch, which it did not — so `/halt` pressed during a pending approval
was cosmetic. v3 puts the kill switch in the predicate:

```python
# schwab_client/orders.py
from risk import kill_switch


def _assert_live_execution(config) -> None:
    """Refuse to submit unless BOTH mode flags AND the kill switch agree.

    Two independent mode flags exist for compatibility (execution_mode is the
    env surface; dry_run is what 23 tests set to stay off live Schwab).
    Requiring agreement means a disagreement fails CLOSED: the illegal
    execution_mode='dry_run' + dry_run=False state is blocked by the first
    clause, and a test that sets only dry_run=True is protected by the second.

    kill_switch.is_enabled() is read HERE, not passed in, because /halt must be
    able to stop an approval that is already past its preflight check. A value
    captured at guard time would be stale by exactly the window that matters.
    """
    if config.execution_mode != "live" or config.dry_run:
        raise RuntimeError(
            f"order submission blocked: execution_mode={config.execution_mode!r}, "
            f"dry_run={config.dry_run!r} — both must indicate live trading"
        )
    if not kill_switch.is_enabled():
        raise RuntimeError(
            "order submission blocked: trading is halted (/halt). "
            "Run /resume to re-enable."
        )
```

Called at the top of `place_limit_order` and `place_limit_sell_order` — *before* `get_client`,
so a non-live mode never even constructs an authenticated broker client.

This is what makes "structurally incapable of ordering" true rather than aspirational.

**Why a module and not `config.trading_enabled`:** `Config` reads env at construction
(CLAUDE.md), and several call sites construct their own `Config()`. A flag flipped by `/halt`
on one instance would not be visible on another. `risk/kill_switch.py` holds one process-wide
value:

```python
# risk/kill_switch.py — imports nothing from schwab_client, discord, or config
_enabled: bool = True


def init(enabled: bool) -> None:      # called once from main.py startup with config.trading_enabled
    global _enabled
    _enabled = enabled


def is_enabled() -> bool:
    return _enabled


def halt() -> None:                   # /halt
    global _enabled
    _enabled = False


def resume() -> None:                 # /resume
    global _enabled
    _enabled = True
```

`risk/preflight.py` imports it for guard 2; `schwab_client/orders.py` imports it for the sink.
Neither direction creates a cycle because `kill_switch` imports nothing.

### 3. Order submission is never retried; outcomes are classified  *(fixes C2, part 1)*

`schwab_client/orders.py:68` currently decorates `_call_place_order` with `@_retry`
(3 attempts). **A timeout after Schwab accepts is an unknown outcome, not a failure** — the
retry can submit the same buy twice. There is no idempotency key in the Schwab order API.

- **Remove `@_retry` from `_call_place_order`.** Reads (`get_order`, `get_orders_for_account`,
  `get_quote`, `get_account`) keep their retry; only submission loses it.
- The recommendation is **not** reopened to `pending`. v1 reopened it, inviting a second
  human approval and a second real order. It stays `approved`.

v2 mapped every submission exception to `submit_unknown`. That is over-broad: a definitive
HTTP rejection is not ambiguous, and treating it as unknown reserves capital that was never
committed. v3 classifies:

| Outcome | Meaning | Status |
|---|---|---|
| 2xx with a `Location` header | Accepted | `submitted` + `broker_order_id` |
| 2xx with **no** `Location` header | Accepted but unidentifiable | `submit_unknown` |
| 4xx **other than** 408/429 | Broker definitively refused | `submit_failed` |
| 408, 429, any 5xx | May or may not have landed | `submit_unknown` |
| Timeout / connection error | May or may not have landed | `submit_unknown` |

Only `submit_failed` releases capital. Everything else reserves it (§4).

```
⚠️ AAPL order outcome UNKNOWN — the broker call failed after submission.
   The order may or may not exist at Schwab.
   $500.00 is reserved against your ceilings until this is resolved.
   AAPL is blocked for new buys. Run /resolve, or check Schwab directly.
```

### 4. `submit_unknown` reserves capital and can be resolved  *(fixes C2, part 2)*

**Capital reservation.** In `database/queries.py` (and mirrored in the Workstream A ledger
plan, which must be updated in the same change):

```python
OPEN_ORDER_STATUSES       = ("pending_submit", "submitted", "working", "partially_filled")
UNRESOLVED_ORDER_STATUSES = ("submit_unknown",)
TERMINAL_ORDER_STATUSES   = ("filled", "cancelled", "rejected", "submit_failed")

# Anything that might have committed real capital. An unknown outcome is
# assumed committed — assuming otherwise is the fail-open direction.
COMMITTING_ORDER_STATUSES = OPEN_ORDER_STATUSES + UNRESOLVED_ORDER_STATUSES + ("filled",)

# Anything that blocks a second buy of the same symbol.
BLOCKING_ORDER_STATUSES   = OPEN_ORDER_STATUSES + UNRESOLVED_ORDER_STATUSES
```

`submit_unknown` is in both derived sets. It is deliberately **not** in
`TERMINAL_ORDER_STATUSES` — nothing may sweep it away automatically.

**Resolution.** `/reconcile` cannot settle this: it reads positions, and an unfilled working
order produces no position, which is indistinguishable from no order at all. Resolution needs
the *order* endpoint.

```python
# schwab_client/orders.py
@_retry
def find_recent_orders(config, symbol, since, until, client=None) -> list[dict]:
    """Return orders entered in [since, until] matching `symbol`.

    Wraps Client.get_orders_for_account (verified present in schwab-py 1.5.1).
    Filters by symbol client-side; the endpoint filters only by time and status.
    """
```

`main.py::resolve_unknown_submissions(config)` then applies, per unresolved row, a search
window of `[submitted_at − 5 min, now]`:

| Broker result | Resolution |
|---|---|
| Call **raises** | Stay `submit_unknown`. Never resolve on a failed read. |
| **0** matching orders | The order never landed → `submit_failed`; capital released; recommendation → `completed` |
| **Exactly 1** match | Attach its `orderId` → `submitted`; the §11 sweep takes over |
| **2 or more** matches | Ambiguous → stay `submit_unknown`; ops alert naming every candidate order id; a human decides |

Exposed as `/resolve` (allowlisted, §10) and run automatically at the top of
`run_reconciliation()`.

**Blast radius while unresolved:** the notional is reserved against *both* ceilings globally,
but only the *affected symbol* is blocked from new buys. Halting all trading would be
disproportionate — the ledger is untrustworthy for one symbol, not for the book. An
unresolved row surviving past one market session escalates to a repeated ops alert on every
scan.

### 5. Every broker read validates before it parses  *(fixes C3)*

`get_positions` is the live instance, but the rule is general — a JSON error body is
structurally a valid dict, and `.get()` chains turn it into a confident empty answer.

```python
def _checked(resp):
    """Raise on any non-2xx before a caller can parse an error body as data."""
    resp.raise_for_status()          # httpx.Response.raise_for_status, verified present
    return resp.json()
```

Applied to `get_account`, `get_quote`, `get_order`, and `get_orders_for_account`.

`parse_positions` is tightened in the same change: a payload with no `securitiesAccount` key
raises `ValueError` instead of returning `[]`. Two independent layers, because
`raise_for_status` cannot catch a 200 response with an unexpected shape.

**Guard consequence.** Guards that depend on broker data (exposure, holdings, sell quantity)
must not run on absent data. `evaluate_trade` takes broker inputs that may be a sentinel
failure value, and returns `broker_unavailable` rather than evaluating:

```
⚠️ Blocked: could not read your Schwab account (HTTP 429)
   Exposure and holdings are unverifiable, so no order was placed.
   Recommendation left pending; try again shortly.
```

This is the single change that makes "everything fails closed" literally true. Under v2 a
broker outage *opened* the two guards that exist to bound size.

### 6. All orders are limit orders, DAY duration

Market orders carry no price, so a validated quote cannot constrain the fill. With scans at
21:45 and approvals before the open, a market order absorbs the full overnight gap.

- **Buy** limit = `quote × (1 + APPROVAL_SLIPPAGE_BUFFER_PCT)`, default **0.5%** —
  comfortably inside the 2% drift tolerance, so the fill is bounded within the band the
  guards validated.
- **Sell** limit = `quote × (1 − APPROVAL_SLIPPAGE_BUFFER_PCT)`, via `equity_sell_limit`
  (verified present in schwab-py 1.5.1). **New in v3** — v2 bounded buys but left sells as
  market orders, so the sell path retained exactly the unbounded-fill defect the buy path was
  fixed for.
- **DAY duration**, replacing Phase 17's GTC, on both sides. GTC allowed Monday's thesis to
  fill Thursday and let unfilled orders silently reserve exposure indefinitely.
- `USE_LIMIT_BUY` is removed; limit is no longer optional. `build_limit_buy` drops
  `.set_duration(Duration.GOOD_TILL_CANCEL)`. `build_market_buy` and `build_market_sell` are
  deleted rather than left as loaded guns.

**Accepted consequence, buys:** an approval made when the market is closed produces a DAY
order that may expire unfilled. That is correct — you approved against a closed-market price.
It is no longer *silent*: the §11 sweep reports `expired`.

**Accepted consequence, sells:** a limit sell can miss a fast decline. This is acceptable
*for this strategy specifically* — the stop-loss decision (finding 9) was "won't fix, by
design: long-term hold," which means a sell is a thesis change, not an emergency exit. If a
protective-exit requirement is ever added, this decision must be revisited; a limit order is
the wrong instrument for one.

Verify Schwab's exact after-hours DAY handling during implementation and document what it does.

### 7. Approvals are serialized

Guards read `day_notional` and exposure, then claim, then submit. Nothing serialized that
sequence, so two approvals for *different* tickers could each read $1,500 against a $2,000
ceiling, each add $400, and both pass — $2,300 total. A per-ticker index cannot prevent a
cross-ticker cap breach.

One module-level `asyncio.Lock` in `discord_bot/bot.py` wraps the entire read→evaluate→claim→
submit sequence for both buy and sell approvals. Human click rates make contention
irrelevant, and the alternative (a DB-level reservation table) is Workstream A's job.

### 8. `risk/preflight.py` — the guard table

```python
def evaluate_trade(request, quote, broker_positions, working_orders,
                   day_notional, config, now) -> Decision
```

`TradeRequest`, `Quote`, and `Decision` live in `risk/preflight.py`, which imports nothing
from `schwab_client` or `discord` (it imports `risk.kill_switch`, which imports nothing).
`schwab_client/quotes.py` imports `Quote` from it, never the reverse.
`check_authorization(request, config) -> Decision | None` is a module-level function returning
`None` when authorized; `evaluate_trade` calls it as guard 1 and the button calls it directly
pre-defer.

Guards renumbered in v3; two are new.

| # | `reason_code` | Fails when | Buy | Sell |
|---|---|---|---|---|
| 1 | `unauthorized` | user not in allowlist, or guild/channel mismatch | ✓ | ✓ |
| 2 | `trading_disabled` | `kill_switch.is_enabled()` is false | ✓ | ✓ |
| 3 | `expired` | `now >= expires_at` | ✓ | ✓ |
| 4 | `quote_unavailable` | quote missing, or stale **for the current session** | ✓ | ✓ |
| 5 | `broker_unavailable` | **new (C3)** — any broker read needed by 8–11 failed | ✓ | ✓ |
| 6 | `price_drift` | `abs(quote − scan)/scan > tolerance` | ✓ | — |
| 7 | `size_zero` | shares at the **limit** price round to 0 | ✓ | — |
| 8 | `daily_notional` | today's committed buy notional + this order > ceiling | ✓ | — |
| 9 | `portfolio_exposure` | broker market value + reservations + this order > ceiling | ✓ | — |
| 10 | `duplicate_symbol` | a position or blocking order already exists for the ticker | ✓ | — |
| 11 | `unresolved_order` | **new (C2)** — a `submit_unknown` row exists for this ticker | ✓ | ✓ |
| 12 | `sell_quantity` | requested shares ≤ 0 or > current broker holding | — | ✓ |

**Guard 1 is first** so an unauthorized clicker learns nothing about the book — rejection
messages are a side channel.

**Guard 5 precedes every guard that consumes broker data.** Ordering is load-bearing, not
cosmetic: if 5 ran after 9, guard 9 would already have evaluated against the empty list.

**Guards 7–9 price at the limit**, not the scan price and not the raw quote, so the ceiling is
computed against the maximum the order can actually cost.

**Guard 4 is session-aware.** `QUOTE_MAX_AGE_S` applies during regular hours. Outside them the
guard accepts the last close — a 60-second rule would reject every pre-open approval, which is
when this system is designed to be used. The limit price, not quote freshness, is the binding
control after hours, and the embed says so.

**Guard 10** stops a second buy of a symbol you already hold. **Guard 11** is separate from 10
on purpose: the operator message differs ("you already hold this" vs "go verify this in
Schwab"), and 11 applies to sells too — selling into an unknown order state can oversell.

**Guard 12.** The sell view captures `self.shares` at post time; the position can shrink
before you click. Revalidate against the broker.

`day_notional` and reservations come from the `orders` table once Workstream A lands. Until
then they come from `trades`, and the build sequence notes the swap.

### 9. Approval path

```
1. check_authorization(...)      pure, pre-defer → ephemeral reject
2. interaction.response.defer()
3. ── acquire approval lock ──
4. fetch live quote              asyncio.to_thread
5. gather broker positions, working orders, day_notional   (may fail → guard 5)
6. evaluate_trade(...) -> Decision
7. claim_recommendation(...)     atomic, expiry in the SQL predicate
8. create order row (pending_submit)
9. submit limit DAY order; classify outcome per §3
10. ── release lock ──
```

Authorization runs before `defer()` because it is pure and instant, letting an unauthorized
click get a private reply. `evaluate_trade` re-checks it as guard 1.

### 10. Two different time predicates, and which is which  *(fixes C4)*

v2 conflated these and then explicitly told the reader not to fix it. Both are needed; they
are not interchangeable.

**Expiry is an instant comparison — UTC.**

```sql
UPDATE recommendations SET status = ?
 WHERE id = ? AND status = 'pending' AND expires_at > datetime('now')
```

A Python-side check before the claim is a TOCTOU race against the expiry sweep. `expires_at`
is `datetime('now','+24 hours')` (`models.py:52`), which is UTC, so the comparison is UTC.
Nothing about a calendar day is involved.

`expire_stale_recommendations` currently uses `expires_at < datetime('now')`, leaving an
exact-second equality where a row is neither expirable nor claimable. Change it to `<=`.

**The daily ceiling is a calendar-day bucket — US market session.**

v2 said this query "deliberately uses the opposite modifier," meaning `'localtime'`. That was
correct for v1's codebase and wrong by the time v2 shipped: commit `36761da` established
`market_time.py` and CLAUDE.md now states that **both** `'localtime'` and bare UTC are
forbidden for market-day bucketing. On this UTC+8 host the 21:45 and 03:30 scans belong to one
US session but two local dates, so a `'localtime'` ceiling **resets mid-session** and admits
double the configured notional — the same failure that was doubling `ANALYST_DAILY_LIMIT`.

```python
from market_time import market_session_bounds_utc

start, end = market_session_bounds_utc(instant)
...
"""SELECT COALESCE(SUM(requested_shares * reference_price), 0.0) AS total
     FROM orders
    WHERE side = 'buy'
      AND status IN (...)
      AND submitted_at >= ? AND submitted_at < ?"""
```

The range predicate leaves `submitted_at` unwrapped and therefore index-usable, per
`market_time.market_session_bounds_utc`'s docstring. Like every other session-bucketed query
in this repo, it takes an optional `instant` so tests can pin time without freezegun.

**`docs/superpowers/plans/2026-08-14-execution-ledger.md:304` must be corrected in the same
change** — it is the source this query was to be copied from.

### 11. Concurrency, duplicate prevention, and a real `completed`  *(fixes H1)*

**One shared `asyncio.Lock`** covering `run_scan` *and* `run_scan_etf` — not one each, since a
symbol can appear in both paths. `/scan` and `/scan_etf` reply "a scan is already running".

**A partial unique index** — the real backstop, since a lock protects one process:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_rec_per_ticker
  ON recommendations(ticker) WHERE status IN ('pending', 'approved');
```

v1 covered `'pending'` only, so protection lapsed the instant the claim flipped a row to
`approved` — precisely the window between claim and order submission.

Covering `approved` requires a release valve, **and v2 did not build one.** It named a
`completed` transition owned by "Workstream A's poller; until then, `/reconcile`" — but the
poller does not exist, `/reconcile` reads positions and never writes recommendation status,
and no ledger-plan step created it. As written, the first buy of any ticker would block that
ticker forever. This is why the minimum order-status read is pulled into Phase 1 rather than
deferred: **the index change is unsafe without it, and they must ship together.**

Three named pieces:

1. **`database/models.py`** — `ALTER TABLE recommendations ADD COLUMN broker_order_id TEXT`,
   using the existing try/`sqlite3.OperationalError`/pass migration idiom.
2. **`schwab_client/orders.py::get_order_status(order_id, config) -> str`** — wraps
   `Client.get_order(order_id, account_hash)` (verified present in 1.5.1) through `_checked`.
3. **`main.py::sweep_terminal_recommendations(config)`** — for every recommendation with
   `status='approved' AND broker_order_id IS NOT NULL`, read the broker status and call
   `database/queries.py::complete_recommendation(db_path, rec_id, broker_status)` when it is
   terminal.

**Terminal is an allowlist, never a denylist:**

```python
TERMINAL_BROKER_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED", "REPLACED"})
```

Schwab's status enum contains a literal `UNKNOWN` member (verified against 1.5.1, which also
spells it `CANCELED` with one L). A denylist — "terminal means not in the working set" — would
classify `UNKNOWN`, and any status Schwab adds later, as terminal and free the ticker on the
strength of no information. The allowlist defaults the unrecognized case to "still open."

**Fail-closed everywhere in the sweep:** a failed broker read leaves the row `approved`, which
keeps the ticker blocked. Blocking is the safe direction. Rows with `status='approved' AND
broker_order_id IS NULL` are exactly the `submit_unknown` cases and are never touched here —
only §4 resolves those.

The sweep runs at the top of each scan (before the buy pass, so releases are visible to the
inserts that follow) and inside `/reconcile`. Insert catches `sqlite3.IntegrityError` and
skips the ticker. The table has 0 rows, so this applies with no backfill.

### 12. Kill switch

`TRADING_ENABLED` defaults to **`true`** — `EXECUTION_MODE` is already the opt-in, and a
second false-by-default flag would just be another thing to forget. (v1 said `true` in its
config table and `false` in its prose; this resolves that.)

`main.py` calls `kill_switch.init(config.trading_enabled)` once at startup. `/halt` calls
`kill_switch.halt()`; `/resume` calls `kill_switch.resume()`; both reset to the env value on
restart. **`/halt`, `/resume`, and `/resolve` are all subject to the authorization
allowlist** — v1 allowlisted only `/halt`, so anyone could have cleared a halt.

Guard 2 reads `kill_switch.is_enabled()`, and **the sink reads it again** at submission time
(§2). Both readers now genuinely exist; in v2 only the first did.

---

## Config

| Field | Env var | Default |
|---|---|---|
| `execution_mode` | `EXECUTION_MODE` | `dry_run` |
| `trading_enabled` | `TRADING_ENABLED` | `true` |
| `allowed_discord_user_ids` | `ALLOWED_DISCORD_USER_IDS` | `""` (deny all) |
| `discord_guild_id` | `DISCORD_GUILD_ID` | `0` |
| `approval_price_tolerance_pct` | `APPROVAL_PRICE_TOLERANCE_PCT` | `2.0` |
| `approval_slippage_buffer_pct` | `APPROVAL_SLIPPAGE_BUFFER_PCT` | `0.5` |
| `quote_max_age_s` | `QUOTE_MAX_AGE_S` | `60` (regular hours only) |
| `max_daily_notional_usd` | `MAX_DAILY_NOTIONAL_USD` | `2000.0` |

`validate()` requires a non-empty allowlist when `execution_mode == "live"`. An empty
allowlist means **deny all**, never allow all.

**Recommended alongside this phase:** set `SCAN_TIMEZONE=America/New_York` so `SCAN_TIMES`
reads as market time (`09:45,15:30`) instead of Taipei time needing mental conversion. It does
not affect correctness — `market_time.py` handles bucketing regardless — but every other time
in this design is stated in ET, and the config is the one place that is not.

---

## Error handling

Everything fails **closed**.

| Failure | Behavior |
|---|---|
| Quote fetch raises or is stale in-session | `quote_unavailable`, no order, **no fallback to scan price** |
| Broker positions/orders read raises | `broker_unavailable`, no order, recommendation left `pending` |
| Order submission definitively refused (4xx) | Order → `submit_failed`, capital released, recommendation → `completed` |
| Order submission ambiguous (timeout, 5xx, 408, 429, missing `Location`) | Order → `submit_unknown`, capital **reserved**, symbol blocked, ops alert, recommendation **stays claimed** |
| `/resolve` broker read raises | Row stays `submit_unknown`. Never resolved on a failed read. |
| `/resolve` finds 2+ candidate orders | Row stays `submit_unknown`, ops alert lists every candidate id |
| Order-status sweep read raises | Recommendation stays `approved`; ticker stays blocked |
| Broker status unrecognized (incl. `UNKNOWN`) | Treated as non-terminal; ticker stays blocked |
| Duplicate active recommendation | `IntegrityError` caught, ticker skipped, logged |
| Scan already running | Slash command replies without spawning |
| Legacy env var present | Startup raises with the mapping message |
| Mode flags disagree at the sink | `RuntimeError`, no submission |
| Kill switch engaged at the sink | `RuntimeError`, no submission, even mid-approval |

Falling back to the scan price on a quote outage would silently restore the exact bug this
design removes. Reopening a `submit_unknown` recommendation would invite a duplicate real
order. Treating an unreadable account as an empty one would open the size guards. All three
are called out rather than left implicit.

Drift rejection leaves the recommendation `pending` so the next scan re-evaluates it:

```
⚠️ Blocked: AAPL moved +3.1% since scan
   Scan price:  $184.20  (21:45 ET)
   Live quote:  $189.91  (08:32 ET, market closed — last close)
   Tolerance:   2.0%

Order NOT placed. Recommendation left pending;
next scan will re-evaluate with fresh technicals.
```

---

## Testing

TDD throughout.

| File | Covers |
|---|---|
| `test_broker_isolation.py` | Sink guard: order wrappers raise in every non-live flag combination, including `execution_mode='dry_run'` + `dry_run=False` and `execution_mode='live'` + `dry_run=True`. **Write first.** |
| `test_kill_switch.py` | `/halt`, `/resume`, `/resolve` all allowlisted; `init` from config; **sink raises when halted even with both mode flags live**; guard 2 and the sink read the same module state |
| `test_broker_read_failures.py` | **New (C3).** `raise_for_status` fires on 401/429/500 for account, quote, order, and order-list reads; `parse_positions` raises on a body with no `securitiesAccount`; `evaluate_trade` returns `broker_unavailable` rather than evaluating guards 8–11 on absent data |
| `test_preflight.py` | All 12 guards table-driven, plus boundaries: drift exactly at tolerance, exposure exactly at ceiling, quote exactly at max age, shares rounding to 0, session-aware staleness in and out of hours, guard-5-before-guard-9 ordering |
| `test_submission_outcomes.py` | **New (C2).** The §3 classification matrix; `submit_unknown` counted by `COMMITTING_ORDER_STATUSES` and `BLOCKING_ORDER_STATUSES`; `submit_failed` releases; `/resolve` for 0 / 1 / 2+ matches and for a raising broker call |
| `test_execution_mode.py` | Legacy var rejection, `simulated` startup failure, `dry_run` derivation, empty allowlist under `live` |
| `test_approval_flow.py` | Unauthorized user, wrong guild, expired button, drift block, quote outage, submission failure paths, recommendation NOT reopened |
| `test_approval_serialization.py` | Two concurrent approvals for different tickers cannot jointly exceed `MAX_DAILY_NOTIONAL_USD` |
| `test_scan_lock.py` | Concurrent `/scan` rejection, shared lock across stock and ETF |
| `test_claim_expiry.py` | SQL expiry claim, equality boundary, index covering `pending` **and** `approved` |
| `test_recommendation_completion.py` | **New (H1).** Terminal allowlist releases the index; `UNKNOWN` and an invented status do **not**; failed broker read leaves `approved`; `broker_order_id IS NULL` rows untouched; a ticker is buyable again after completion |
| `test_day_notional_session.py` | **New (C4).** A 21:45 and an 03:30 order on this host land in **one** session bucket; the ceiling does not reset between them; DST spring-forward and fall-back boundaries |
| `test_limit_order_construction.py` | Buy buffer arithmetic, **sell buffer arithmetic**, DAY duration on both (not GTC), buy limit ≥ quote, sell limit ≤ quote, market builders deleted |

Existing tests that must keep passing: the 23 `dry_run = True` protection sites, and
`test_config.py`'s construction-time env reads.

Add `pytest.ini` with `asyncio_default_fixture_loop_scope = function`.

---

## Build sequence

1. `test_broker_isolation.py` against current code — expect FAIL (no sink guard exists yet).
   This is the test that proves the defect before fixing it.
2. `risk/kill_switch.py` + sink guard in `schwab_client/orders.py` + `EXECUTION_MODE` + config
   + `validate()` + doc debt
3. `_checked` / `raise_for_status` on all broker reads + strict `parse_positions` **(C3 — this
   is a live defect in shipped code and can land independently of everything else)**
4. Remove `@_retry` from submission; the §3 outcome classification
5. `schwab_client/quotes.py`
6. `risk/preflight.py` with its full 12-guard test table, guard 5 ordering included
7. Limit + DAY construction for buy **and** sell; remove `USE_LIMIT_BUY`; delete the market
   builders
8. Rewire `ApproveRejectView`, then `SellApproveRejectView`; add the approval lock
9. SQL claim predicate + `<=` expiry fix + session-bucketed day notional (**and correct
   `execution-ledger.md:304`**)
10. `broker_order_id` column + `get_order_status` + `sweep_terminal_recommendations` +
    `complete_recommendation` — **then** the partial unique index, in that order, never the
    reverse
11. `submit_unknown` reservation sets + `/resolve` + reconcile wiring
12. Scan lock + `/halt` + `/resume`
13. Full `pytest -q` and `ruff check .`

Steps 10 and 11 are the ones v2 assumed away. Neither the index (step 10's tail) nor the
`approved`-covering uniqueness is safe to ship before the release valve that precedes it.

---

## Process rule adopted in v3

Three of the five v3 defects — C1, C2, and H1 — have one shape: **a claim in prose that named
no implementation.** "The sink re-reads it." "Workstream A's poller; until then, `/reconcile`."
"The opposite modifier is deliberate." Each reads as a settled decision and each was false.
Self-review cannot catch this class, because the reviewer already believes the claim.

**Rule:** every statement in a spec that asserts some component handles a case must name the
file and function that does it, and that name must be checkable — either it exists on `master`
today, or it appears in this document's build sequence. A claim naming a component that exists
in neither is a defect, not a design.

C3 is a different shape and needs its own rule: **defensive parsing of an unvalidated
response converts an error into confident data.** `.get()` chains guard against missing
fields, which is exactly what an error body looks like. Validate the transport before parsing
the payload, always.

---

## Open questions

1. **Schwab `FUNDAMENTAL` projection** — `Client.get_instruments(symbols, projection=FUNDAMENTAL)`
   returns `epsChangePercentTTM`, `returnOnEquity`, `currentRatio`, `marketCap`. Could restore
   candidates lost to the `reject` missing-data policy *and* supply the scale-independent
   factors findings 7/8 want. **Unverified** — the Schwab token is ~125 days old and expired.
2. **Sell-pass coherence.** Given long-term hold, the RSI>70 + MACD-bearish exit is a
   short-horizon reversal trigger that does not match the thesis: no downside exit, sensitive
   upside exit. Recorded in the roadmap, unresolved. §6's limit-sell decision depends on this
   answer staying "sells are thesis changes, not emergency exits."
3. **schwab-py pin drift.** `requirements.txt:172` / `requirements.in:12` pin `1.4.0`; **1.5.1
   is installed** and every API fact in this document was verified against 1.5.1. Bump the pin
   and regenerate the lock with `uv pip compile` before implementing.

---

## Backlog

Sequenced in `docs/superpowers/plans/2026-08-14-codex-backlog-roadmap.md`. Workstream A
(execution ledger) is the direct successor and supplies the `orders` table this design's
guards 8–11 will read from. v3 pulls forward only the single column and single broker read
that the unique index cannot ship without; the rest of the ledger is unchanged.

**Standing constraint from finding 6:** no backtest has been run and no forward sample exists.
The 569 passing tests validate software behavior, not predictive power. Every recommendation
remains an unvalidated research lead regardless of how safe the execution path becomes. This
design makes the bot *safe to operate*; it does not make it *worth operating*.
