# Design: Live-Trading Safety Hardening (Codex Phase 1) — v4

**Date:** 2026-08-14 (v4 revised 2026-08-15)
**Status:** Draft — v4, revising after the **fourth** external review returned *request changes*
(10 Critical, 1 High, 1 Medium)
**Milestone:** v1.4 (candidate)
**Prerequisite:** **`plans/2026-08-15-phase0-order-ledger-foundation.md` must land first.**
**Source:** `docs/superpowers/codex_recommendations.md` — findings 1, 2, 3, 10, roadmap item 1.6
**Review record:** `reviews/2026-08-15-spec-v3-review-prompt.md`

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
| C1 | **Kill switch never reaches the sink.** v2 §10 claimed "the sink re-reads it," but the §2 predicate was `execution_mode != "live" or dry_run` — no `trading_enabled` term anywhere in it. `/halt` during a pending approval did nothing. | New `risk/kill_switch.py` holds the runtime flag; the sink predicate reads it | §2, §12 |
| C2 | **`submit_unknown` reserved no capital and could not be resolved.** Absent from `COMMITTING_ORDER_STATUSES` (`execution-ledger.md:222`), so a possibly-live $500 order counted $0 against both ceilings. No operation could resolve one — `/reconcile` reads positions, which cannot distinguish "working order" from "no order." | Added to the committing set; `/resolve` uses `get_orders_for_account` to settle it | §3, §4 |
| C3 | **Broker reads fail open.** `get_positions` has no `raise_for_status`; a 401/429/500 body has no `securitiesAccount` key, so `parse_positions` returns `[]`. Exposure reads zero, the holding check sees nothing — **a broker outage opens the gate.** | `raise_for_status` on every broker read, strict parse, new `broker_unavailable` guard | §5, §7 |
| C4 | **Daily ceiling buckets on `localtime`.** `execution-ledger.md:304` uses `date(submitted_at,'localtime') = date('now','localtime')`, and v2 §8 explicitly *defended* it. Commit `36761da` invalidated that convention later the same day. On this UTC+8 host the 21:45 and 03:30 scans are **one** US session but **two** local dates, so `MAX_DAILY_NOTIONAL_USD` resets mid-session — $4,000 through a $2,000 ceiling. Identical to the bug that was doubling `ANALYST_DAILY_LIMIT`. | Range predicate over `market_session_bounds_utc()` | §9 |
| H1 | **`approved` blocks a ticker permanently.** The `completed` transition the index depends on existed nowhere — not in the repo, not in `/reconcile`, not in the ledger plan. | `recommendations.broker_order_id` + a named sweep, pulled into Phase 1 scope | §11 |

**Also in v3:** sells are bounded by limit orders too (v2 bounded only buys — §6), and a
process rule was added to prevent the defect class that produced C1, C2, and H1
(see "Process rule adopted in v3").

**Corrections made to the v3 draft before review, found by applying that same rule to v3
itself:**

| Defect in the v3 draft | Fix | Section |
|---|---|---|
| `REPLACED` was listed as a terminal broker status. It is not — the original order is dead but a **replacement is still working**, under an id we do not hold. Completing on it frees the ticker while a live order sits at the broker: fail-open in the exact spot the allowlist protects. | Removed from the allowlist; routed to `submit_unknown` | §11 |
| `/resolve` matched on symbol and a 5-minute window. This is a personal brokerage account — a **manual order you placed** in the Schwab app minutes later would match, and be silently adopted as the bot's. | Exact match on five fields; window narrowed to −30s/+120s; partial matches are ambiguous | §4 |
| A single 0-match was treated as proof no order landed. That assumes Schwab's order list is immediately consistent with acceptance. | Two zeros ≥ `RESOLVE_CONFIRM_DELAY_S` apart | §4 |
| Fail-closed on a stuck order meant a ticker could be **silently blocked forever** with no alert. | `STUCK_APPROVAL_ALERT_H` ops alert + manual override | §11 |
| Partial fills were unaddressed: status-only sweeping cannot see a partially-filled-then-cancelled order, so a real position goes unrecorded. | Stated as a **known gap** with its mitigations, and raised as review question Q2 | §11 |

Three of those five are the same shape as C1/C2/H1 — a claim that read as settled and named no
mechanism that made it true. The rule caught them in the author's own draft, which is some
evidence it works, and no evidence at all that it is sufficient.

### v4 revisions

The fourth external review returned **request changes: 10 Critical, 1 High, 1 Medium.** It
confirmed three things as sound — the four-status terminal allowlist, guard 5's ordering ahead
of every broker-data consumer, and the bare-UTC expiry predicate being correctly distinct from
day bucketing. Everything else in the surrounding state model, accounting, and resolution paths
was defective.

**Six of the ten Criticals are one defect.** Findings 1, 2, 5, 6, 9 and 11 all reduce to: *this
design requires durable per-order state, and this design's Scope excluded the table that holds
it.* Verified — `database/models.py` creates `recommendations`, `trades`, `analyst_cache`,
`positions`, `analyst_calls`, and nothing else. §9 step 8 nonetheless created "an order row",
§10 queried `FROM orders`, and §8 said the data came "from `trades`", which has no status column
and cannot express `submit_unknown` at all.

The scope boundary was inherited from v1 and survived three review rounds unexamined, because
each round reviewed this document's *contents* rather than its *edges*.

**Structural consequence:** the order ledger is extracted into
`plans/2026-08-15-phase0-order-ledger-foundation.md` and lands **before** this phase, inverting
the dependency both documents previously asserted. Ledger Task 6 (approval rewiring) and Task 8
(exposure inputs) move **into** this phase, so exactly one document owns the approval handler —
two documents owning it is what produced finding 2.

| # | Round-4 finding | Fix | Section |
|---|---|---|---|
| 1 | No storage can represent `submit_unknown` | Phase 0 prerequisite supplies the `orders` table | Scope, §4 |
| 2 | Ledger Task 6 reopens to `pending` after an ambiguous failure, inviting a duplicate real order | Task 6 absorbed here and rewritten against §3's classification | §9 |
| 3 | `/resolve` can safely auto-resolve neither a match nor a zero | **Report-only.** Five fields establish shape, not provenance | §4 |
| 4 | `REPLACED` routed to the original-submission matcher marks a live replacement as failed | Follow `replacingOrderCollection.orderId`; never match on the original | §11 |
| 5 | Daily notional priced at the reference quote, below the executable maximum | `order_commitment()` prices open orders at the limit | §10 |
| 6 | A partially-filled-then-cancelled order releases its **entire** daily budget | Commitment retains filled notional through terminal statuses | §10, §11 |
| 7 | Kill switch is neither linearizable nor durable | Halt persisted; one submission gate spanning check→dispatch | §2, §12 |
| 8 | Cross-process approvals can jointly exceed the ceilings | Cap check + reservation in one `BEGIN IMMEDIATE` transaction | §7 |
| 9 | Manual **broker** working orders are invisible to guards 9/10 | Preflight fetches broker working orders and merges by broker id | §8 |
| 10 | After-hours DAY orders queue to the next session, breaking session bucketing | **Q3 alternative rejected**; attribution recorded as unresolved | §11, Open Questions |
| 11 | The claimed manual exit for blocked statuses was never designed | Phase 0 Delta 3 — audited `resolve_order_manually` | §11 |
| 12 | The limit-sell premise is not encoded anywhere in the sell path | Premise corrected against `main.py:441`; marketable-limit policy | §6 |

Findings 4 and 11 were defects in fixes added *by v3 during the same session that wrote the
process rule against them.*

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
  limit orders (buy) and marketable limits (sell), `find_recent_orders` (report-only)
- `schwab_client/quotes.py` — **new**, live quote fetch
- `risk/preflight.py` — **new**, the pure guard table
- `risk/kill_switch.py` — **new**, runtime kill-switch state (§12)
- `discord_bot/bot.py` — both approval views, both scan commands, `/halt`, `/resume`, `/resolve`
- `database/queries.py` — claim predicate, day-notional query, recommendation completion
- `database/models.py` — partial unique index (the `orders` table itself comes from Phase 0)
- `main.py` — startup warning, scan lock wiring, terminal sweep
- `README.md`, `CLAUDE.md`, `.env.example`, `.planning/codebase/*.md` — doc debt

**Supplied by the Phase 0 prerequisite, not built here:** the `orders` table and its CRUD, the
status constants, `order_commitment()` / `get_day_notional()`, `mark_order_submit_unknown`, the
audited `resolve_order_manually`, broker status mapping, and `fetch_order`.

v3 tried to get by with a single `recommendations.broker_order_id` column; round-4 finding 1
established that the real minimum is the table. **That column is withdrawn** — Phase 0's
`orders` row carries the broker id, and a second place to record it would be a second source of
truth for exactly the state this phase must not get wrong.

**Absorbed from the ledger plan into this phase:** Task 6 (approve button creates an order
before submitting) and Task 8 (exposure from broker positions and reserved orders). Both touch
the approval path this document rewrites. Leaving Task 6 in a second document is what let it
specify an ambiguous-failure retry that contradicts §3 — finding 2.

**Not in scope:** fill application and the status poller (Workstream A, ledger Tasks 3/5/7);
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

v2 added a sink guard but checked only the two *mode* flags. v2 §10 simultaneously claimed the
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

**Round-4 finding 7 broke v3's version of this in two ways.** Both are fixed here.

*It was not durable.* `_enabled` defaulted to `True` and a restart restored the env default,
also `True`. An operator halts during an incident, the process restarts, the persistent Discord
buttons are still live — and trading silently re-enables without anyone running `/resume`. A
kill switch that a crash can clear is not a kill switch. **Halt state is persisted** and re-read
at startup; the env value is only the initial seed for a database that has never been written.

*It was not linearizable.* The sink read a boolean, then constructed a client, then dispatched
HTTP — with `await` boundaries throughout. `/halt` is a separate coroutine and can land in any
of them, so the worker could check `True`, `/halt` could set `False` and reply "halted", and the
worker could then submit. **One gate spans the final check through dispatch**, and `/halt`
acquires it, so `/halt` returns only once no submission is mid-flight. Requests already
dispatched are reported as in-flight/unknown rather than as stopped.

```python
# risk/kill_switch.py — imports nothing from schwab_client, discord, or config
_state: str = "UNINITIALIZED"          # UNINITIALIZED | ENABLED | HALTED
_gate = threading.RLock()              # spans final check -> HTTP dispatch


def init(db_path: str, env_default: bool) -> None:
    """Load persisted halt state; seed from env only on first ever run.

    Defaults to UNINITIALIZED, and is_enabled() is False until init() runs, so a
    code path that forgets to initialise fails closed rather than trading.
    """


def is_enabled() -> bool:
    return _state == "ENABLED"


def submission_gate():
    """Context manager held across the final is_enabled() check and the broker
    call. /halt acquires the same gate, so it cannot interleave."""
    return _gate


def halt(db_path: str, actor: str) -> None:     # /halt — persists, then acquires the gate
    ...


def resume(db_path: str, actor: str) -> None:   # /resume — persists
    ...
```

`_state` starting at `UNINITIALIZED` rather than `ENABLED` is the point: v3's default meant a
missed `init()` call left trading on.

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

**`/resolve` is report-only.** *(round-4 finding 3 / Q1 — this reverses v3.)*

v3 auto-resolved on an exact five-field match and on a twice-confirmed zero. Both were rejected,
and the reasoning is worth keeping because it generalises:

- **Five matching fields establish *shape*, not *provenance*.** This is not our order book — it
  is your brokerage account, and a manual order you place in the Schwab app can be identical on
  symbol, side, quantity, type and limit price. Narrowing the match reduces the probability of
  mis-attribution; it cannot make the inference valid. Schwab exposes no client-supplied
  correlation id, so nothing in the payload can say "this one is mine."
- **Two zeros are two samples from an API with no documented visibility bound.** v3 claimed the
  second reading "removes the assumption." It does not — it shortens the window in which a
  late-appearing order is wrongly marked `submit_failed` and has its capital released.
- **Two exact candidates may be two *bot* submissions.** v3 treated 2+ as merely ambiguous while
  reserving one order's notional. If both are ours, twice the capital is committed and the
  ledger reserves once.

So `/resolve` reads, ranks, and reports. It never writes order state on its own:

```python
# schwab_client/orders.py
@_retry
def find_recent_orders(config, symbol, since, until, client=None) -> list[dict]:
    """Broker orders entered in [since, until] whose symbol matches.

    Wraps Client.get_orders_for_account (schwab-py 1.5.1) through the validating
    wrapper. Every other field is compared by the caller, for display only.
    """
```

`main.py::report_unknown_submissions(config)` renders, per unresolved order, the candidates it
found and how each differs from what was submitted, then stops. A human resolves it with the
audited Phase 0 command:

```
/resolve <order-id> <adopt|confirmed-absent|keep-blocked> [broker-order-id]
```

which writes through `database/queries.py::resolve_order_manually` (Phase 0 Delta 3), refuses
any source status outside `UNRESOLVED_ORDER_STATUSES`, and records actor, timestamp and the
evidence string. Finding 11: v3 asserted this override existed and designed none.

**Reservation while unresolved is worst-case, not expected-case.** With multiple plausible
candidates the design cannot know how many are ours, so it reserves as if all of them are:
`sum(order_commitment(c) for c in candidates)`, floored at the submitted order's own commitment.
Over-reserving blocks trades that would have been fine; under-reserving places trades that blow
the ceiling. Only one of those is recoverable.

**Blast radius while unresolved:** capital reserved against *both* ceilings globally; the
*affected symbol* blocked from new buys and sells. Halting all trading would be
disproportionate — the ledger is untrustworthy for one symbol, not for the book. An unresolved
row surviving past one market session escalates to a repeated ops alert on every scan.

**Accepted cost of report-only:** an unresolved row freezes its capital and blocks its symbol
until a human acts, which on a long-horizon strategy can be days. That is the price of keeping
provenance a human judgement, and it is the right trade while the API offers no way to make the
judgement mechanically.

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

**Sells: v3's justification was wrong — round-4 finding 12.** v3 accepted that a limit sell can
miss a fast decline, on the grounds that "a sell is a thesis change, not an emergency exit,"
citing the long-term-hold decision that closed the stop-loss finding.

That premise is not encoded anywhere. `main.py:441` gates the sell pass on `check_exit_signals`
— RSI above threshold **and** MACD bearish — then asks the analyst. That is a short-horizon
technical reversal trigger. When it fires on a fast decline, a `quote × 0.995` DAY limit is
exactly the order most likely to miss, and the position stays open through the move the signal
fired on. The design justified a policy with a strategy the code does not implement.

Two coherent resolutions; this design takes the second and flags the first:

1. **Redefine sell generation** around durable thesis invalidation, matching the buy-and-hold
   premise. That is a signal-design change and belongs to Workstream D, not here.
2. **Match the instrument to the trigger that actually exists.** Sells use a **marketable
   limit** — priced *through* the bid by `APPROVAL_SLIPPAGE_BUFFER_PCT` rather than at
   `quote − buffer` — so it behaves like a market order for fill purposes while still carrying a
   worst-case price the guards validated. The bound is on how bad the fill may be, not on
   whether it happens.

Buys keep the passive `quote × (1 + buffer)` limit: a missed buy costs an opportunity, a missed
sell holds a position through the decline that triggered the exit. The asymmetry is deliberate
and is stated here so it is not "fixed" into symmetry later.

**This becomes wrong again if the sell trigger changes.** Whoever redefines exits per (1) must
revisit this section; a marketable limit is the wrong instrument for a genuine thesis-change
sell that has no urgency.

Verify Schwab's exact after-hours DAY handling during implementation and document what it does.

### 7. Approvals are serialized

Guards read `day_notional` and exposure, then claim, then submit. Nothing serialized that
sequence, so two approvals for *different* tickers could each read $1,500 against a $2,000
ceiling, each add $400, and both pass — $2,300 total. A per-ticker index cannot prevent a
cross-ticker cap breach.

One module-level `asyncio.Lock` in `discord_bot/bot.py` wraps the entire read→evaluate→claim→
submit sequence for both buy and sell approvals. Human click rates make contention irrelevant.

**That lock is not sufficient on its own — round-4 finding 8.** It is process-local, and the
unique index that backstops it is per-*ticker*, so it cannot stop a cross-*ticker* cap breach
between two processes. During an overlapping restart or deploy, two processes each read $1,500
against a $2,000 cap, each reserve $400, and both submit: $2,300.

So the cap check and the reservation are **one SQLite `BEGIN IMMEDIATE` transaction** against
the Phase 0 `orders` table:

```
BEGIN IMMEDIATE                       -- write lock taken up front, cross-process
  read day_notional + reservations    -- via order_commitment (§10)
  evaluate ceilings
  INSERT the order row (pending_submit)   -- the reservation IS the row
COMMIT
                                      -- only then submit to the broker
```

`BEGIN IMMEDIATE` acquires the write lock before the reads, so two processes serialise rather
than both reading a stale total. The reservation is the order row itself — there is no separate
reservation table to keep consistent. v3 said a DB-level reservation was "Workstream A's job";
with Phase 0 landing first, the table already exists and there is nothing left to defer.

The `asyncio.Lock` stays for in-process ordering and to keep the broker reads (quote, positions,
working orders) from interleaving; the transaction is what makes the ceiling actually global.

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

`day_notional` and reservations come from the Phase 0 `orders` table via `order_commitment()`
(§10). v3 said they would come "from `trades`" in the interim — round-4 finding 1 established
that `trades` cannot express the states involved, which is why Phase 0 exists.

**Working orders must include the broker's, not only ours — round-4 finding 9.** v3 (and ledger
Task 8) sourced working orders from the bot's local table alone. A buy you place manually in the
Schwab app can be *working and unfilled*: broker positions are empty, the local ledger has no
row, so guards 9 and 10 both pass and the bot submits a second order for a symbol that already
has one live. Preflight therefore fetches broker working orders on every evaluation, merges them
with local reservations **by broker order id** to avoid double-counting the same order, and
fails closed via guard 5 when that read fails.

Merging by broker id is what makes the union safe: a local row that has already been attached to
a broker id and the broker's own record of it are the same order, and counting it twice would
reject legitimate trades — the recoverable direction, but still wrong.

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

The query is `database/queries.py::get_day_notional`, supplied by **Phase 0 Delta 2**. It selects
the session's buy orders by a range predicate over `market_session_bounds_utc()` — which leaves
`submitted_at` unwrapped and therefore index-usable — and sums `order_commitment()` over them.
Like every other session-bucketed query in this repo it takes an optional `instant`, so tests
pin time without freezegun.

**Two round-4 defects live in how that sum is computed**, and both are fixed in Phase 0 rather
than here:

*Finding 5 — priced below the executable maximum.* v3's query summed
`requested_shares * reference_price` while §8 asserted the guards price at the limit. An order
can fill at `quote × 1.005`, so reserving the quote lets a second order through at the ceiling
boundary when both can fill above the cap. `order_commitment()` prices open and unresolved
orders at the **broker-rounded limit**.

*Finding 6 — a partial fill releases the whole budget.* `cancelled` and `expired` are terminal
and previously dropped the order's entire notional, even when four of ten shares had filled.
`order_commitment()` returns `filled_notional` for terminal orders, so what actually filled
stays committed and only the unfilled remainder is released.

Both defects also existed in `plans/2026-08-14-execution-ledger.md`, which is where the query
was to be copied from. That plan is now superseded for this function by Phase 0.

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

Two named pieces, on top of Phase 0:

1. **`schwab_client/orders.py::fetch_order(config, broker_order_id) -> dict`** — Phase 0
   Delta 5. Returns the **full validated payload**, not a status string. v3 specified `-> str`;
   round-4 finding 4 showed a bare string cannot carry `replacingOrderCollection` or
   `filledQuantity`, both of which the caller needs.
2. **`main.py::sweep_terminal_recommendations(config)`** — for every recommendation whose
   `orders` row is in `OPEN_ORDER_STATUSES`, map the payload's status and call
   `database/queries.py::complete_recommendation(db_path, rec_id, broker_status)` when it is
   terminal.

**`recommendations.broker_order_id` is withdrawn.** v3 added it as the minimum that would make
`completed` real; the broker id now lives on the Phase 0 `orders` row, which is the same place
the reservation lives. Two columns recording one fact is a divergence waiting to happen.

**Terminal is an allowlist, never a denylist:**

```python
TERMINAL_BROKER_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED"})
```

Schwab's status enum contains a literal `UNKNOWN` member (verified against 1.5.1, which also
spells it `CANCELED` with one L). A denylist — "terminal means not in the working set" — would
classify `UNKNOWN`, and any status Schwab adds later, as terminal and free the ticker on the
strength of no information. The allowlist defaults the unrecognized case to "still open."

**`REPLACED` is excluded — and must not be handed to any matcher.** An earlier v3 draft listed
it as terminal, which is fail-open: `REPLACED` means the original order is dead *and a new order
took its place*, still working, under an id we do not hold. v3 then corrected it by routing
`REPLACED` to `submit_unknown` and §4's resolution — **which round-4 finding 4 showed is a
second fail-open.** The replacement carries a different price and lies outside any window
anchored on the original submission, so a matcher finds nothing, and "nothing found" would mark
the submission failed while the replacement is live.

The payload, not a search, resolves it. Phase 0 Delta 4's `map_broker_status` extracts
`replacingOrderCollection[].orderId` and returns it alongside the status, so the sweep follows
the chain to the successor id and keeps watching. Only when the payload carries **no** successor
id does the order become `submit_unknown` for human resolution — never terminal. `PENDING_REPLACE`
is likewise not terminal.

The general rule this establishes: **when the broker tells you where the order went, follow the
pointer; never re-derive it by searching.** Searching was what made both the manual-order
mis-attribution (finding 3) and this defect possible.

**Partial fills — closed, not accepted.** v3 documented `CANCELED`/`EXPIRED` after a partial
fill as a known gap, on the grounds that `filledQuantity` was Workstream A's. Round-4 finding 6
established the gap is not survivable: the status-only rule released the order's *entire*
notional from the daily ceiling while leaving a real, unrecorded position, so a second approval
could consume the whole daily allowance again.

Phase 0's `order_commitment()` (Delta 2) reads `filled_shares` / `filled_notional`, so a
terminal order keeps whatever actually filled committed and releases only the remainder. Q2 from
round 4 confirmed the two mitigations v3 cited do exist — `run_reconciliation()` reports the
holding as untracked (`schwab_client/reconcile.py:21`), and guards 9/10 read broker positions —
but they cover duplicate-symbol and exposure while leaving **daily-notional accounting** wrong.
That is the part that had to come forward.

**Fail-closed everywhere in the sweep:** a failed broker read leaves the recommendation
`approved` and its order row open, which keeps the ticker blocked. Blocking is the safe
direction. Orders in `UNRESOLVED_ORDER_STATUSES` are never touched here — only §4 reports them
and only an operator resolves them.

**Fail-closed is still a silent failure.** A ticker parked in `AWAITING_MANUAL_REVIEW` for days
keeps its row open, and the index blocks that symbol with no notification. Not double-buying is
the right direction, but "silently stop trading a symbol forever" is not an acceptable resting
state. An order open longer than `STUCK_APPROVAL_ALERT_H` (default 24) raises a repeated ops
alert naming the ticker, its broker order id, and the last status seen. The exit is Phase 0's
audited `resolve_order_manually` — finding 11 was that v3 claimed this override existed and
designed none.

The sweep runs at the top of each scan (before the buy pass, so releases are visible to the
inserts that follow) and inside `/reconcile`. Insert catches `sqlite3.IntegrityError` and
skips the ticker. The table has 0 rows, so this applies with no backfill.

#### Rejected: the session-scoped index

v3 floated `UNIQUE(ticker, session_date)` as a way to get index safety without a broker read,
arguing from §6's DAY-duration decision that no working order can outlive its session.

**Round-4 finding 10 rejected it: the premise is false.** Schwab accepts regular-session orders
at any time and queues them for the *next* regular session, and `market_time.market_session_date()`
returns an Eastern **calendar** date with midnight-to-midnight bounds — not an exchange-session
assignment. A Friday-night or weekend order buckets to Friday or Saturday while remaining
actionable on Monday. The index would free the ticker, and the daily cap would reset, while the
queued order is still live.

Recorded here rather than deleted because the reasoning generalises: **a calendar date is not a
trading session**, and `market_time.py` deliberately has no exchange calendar. The same gap makes
after-hours *ceiling attribution* an open question — see Open Questions.

### 12. Kill switch

`TRADING_ENABLED` defaults to **`true`** — `EXECUTION_MODE` is already the opt-in, and a
second false-by-default flag would just be another thing to forget. (v1 said `true` in its
config table and `false` in its prose; this resolves that.)

`main.py` calls `kill_switch.init(config.db_path, config.trading_enabled)` once at startup.
`/halt` and `/resume` **persist** their new state, and `init` reads the persisted value —
`TRADING_ENABLED` seeds it only on a database that has never recorded one.

v3 said both "reset to the env value on restart," which round-4 finding 7 identified as a defect
rather than a feature: with the env default also `true`, an operator's halt evaporated on the
next restart while the persistent Discord buttons stayed live, silently re-enabling trading
without anyone running `/resume`. **A kill switch a crash can clear is not a kill switch.**

**`/halt`, `/resume`, and `/resolve` are all subject to the authorization
allowlist** — v1 allowlisted only `/halt`, so anyone could have cleared a halt.

Guard 2 reads `kill_switch.is_enabled()`, and **the sink reads it again** at submission time
(§2). Both readers now genuinely exist; in v2 only the first did.

The sink's re-read happens **inside `submission_gate()`**, held from that check through the
broker dispatch. v3 had the re-read but not the gate, so `/halt` could land in one of the
`await` boundaries between them — the worker checks `True`, `/halt` sets `False` and replies
"halted", the worker submits. `/halt` acquires the same gate, so it returns only once nothing is
mid-flight, and reports anything already dispatched as in-flight/unknown rather than stopped.

`/halt` cannot recall an order the broker has already accepted. Saying so plainly matters more
than the switch feeling absolute: the honest guarantee is *no new submissions after `/halt`
returns*, not *no orders exist*.

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
| `stuck_approval_alert_h` | `STUCK_APPROVAL_ALERT_H` | `24` (§11 — age at which an `approved` row starts alerting) |

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
| `/resolve` finds any number of candidates | **Reports only.** Row stays `submit_unknown` regardless; worst-case candidate notional stays reserved; only `resolve_order_manually` can transition it |
| Order-status sweep read raises | Recommendation stays `approved`; ticker stays blocked |
| Broker status unrecognized (incl. `UNKNOWN`) | Treated as non-terminal; ticker stays blocked |
| Broker status `REPLACED` / `PENDING_REPLACE` | **Not terminal.** Routed to `submit_unknown` — a live replacement order exists under an id we do not hold |
| `approved` row older than `STUCK_APPROVAL_ALERT_H` | Repeated ops alert; ticker stays blocked until `/resolve` overrides |
| Order partially filled then cancelled | **Known gap (§11).** Position not recorded by Phase 1; surfaced by `/reconcile` as untracked, and guards 9/10 still see it via broker positions |
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
| `test_submission_outcomes.py` | **New (C2).** The §3 classification matrix; `submit_unknown` counted by `COMMITTING_ORDER_STATUSES` and `BLOCKING_ORDER_STATUSES`; `submit_failed` releases |
| `test_resolve_reporting.py` | **New (v4, finding 3).** `/resolve` **never** mutates order status — asserted for 0, 1 exact, 1 partial and 2+ candidates, and for a raising broker call; worst-case reservation is the sum over candidates, floored at the order's own commitment; only `resolve_order_manually` transitions a row, and it refuses any source status outside `UNRESOLVED_ORDER_STATUSES` |
| `test_execution_mode.py` | Legacy var rejection, `simulated` startup failure, `dry_run` derivation, empty allowlist under `live` |
| `test_approval_flow.py` | Unauthorized user, wrong guild, expired button, drift block, quote outage, submission failure paths, recommendation NOT reopened |
| `test_approval_serialization.py` | Two concurrent approvals for different tickers cannot jointly exceed `MAX_DAILY_NOTIONAL_USD` |
| `test_scan_lock.py` | Concurrent `/scan` rejection, shared lock across stock and ETF |
| `test_claim_expiry.py` | SQL expiry claim, equality boundary, index covering `pending` **and** `approved` |
| `test_recommendation_completion.py` | **New (H1).** Terminal allowlist releases the index; `UNKNOWN`, `REPLACED`, `PENDING_REPLACE` and an invented status do **not**; `REPLACED` routes to `submit_unknown`; failed broker read leaves `approved`; `broker_order_id IS NULL` rows untouched; a ticker is buyable again after completion; a row past `STUCK_APPROVAL_ALERT_H` alerts and stays blocked |
| `test_day_notional_session.py` | **New (C4).** A 21:45 and an 03:30 order on this host land in **one** session bucket; the ceiling does not reset between them; DST spring-forward and fall-back boundaries |
| `test_limit_order_construction.py` | Buy buffer arithmetic, **sell buffer arithmetic**, DAY duration on both (not GTC), buy limit ≥ quote, sell limit ≤ quote, market builders deleted |

Existing tests that must keep passing: the 23 `dry_run = True` protection sites, and
`test_config.py`'s construction-time env reads.

Add `pytest.ini` with `asyncio_default_fixture_loop_scope = function`.

---

## Build sequence

**Prerequisite: `plans/2026-08-15-phase0-order-ledger-foundation.md` is complete and merged.**
Steps 8 onward read and write the `orders` table it creates. Steps 1–7 do not, so they can
proceed in parallel with Phase 0 if useful.

1. `test_broker_isolation.py` against current code — expect FAIL (no sink guard exists yet).
   This is the test that proves the defect before fixing it.
2. `risk/kill_switch.py` — persisted state, `UNINITIALIZED` default, `submission_gate()` —
   plus the sink guard in `schwab_client/orders.py`, `EXECUTION_MODE`, config, `validate()`,
   doc debt
3. `_checked` / `raise_for_status` on all broker reads + strict `parse_positions`
   **(round-3 C3 — a live defect in shipped code. Independent of Phase 0 and of every open
   question; land it first and alone if nothing else moves.)**
4. Remove `@_retry` from submission; the §3 outcome classification
5. `schwab_client/quotes.py`
6. Limit + DAY construction for buys, **marketable limit for sells** (§6); remove
   `USE_LIMIT_BUY`; delete the market builders
7. `risk/preflight.py` with its full 12-guard test table, guard 5 ordering included
8. Broker working-order fetch + merge-by-broker-id into the guard inputs (absorbed ledger
   Task 8; round-4 finding 9)
9. Rewire `ApproveRejectView`, then `SellApproveRejectView` — order row created before
   submission (absorbed ledger Task 6, **rewritten** against §3), approval lock, and the
   `BEGIN IMMEDIATE` cap-check-plus-reservation transaction (§7)
10. SQL claim predicate + `<=` expiry fix
11. `fetch_order` chain-following + `sweep_terminal_recommendations` +
    `complete_recommendation` — **then** the partial unique index, in that order, never the
    reverse
12. `/resolve` report-only rendering + reconcile wiring + the stuck-order alert
13. Scan lock + `/halt` + `/resume`
14. Full `pytest -q` and `ruff check .`

Step 11's ordering is the one v2 assumed away: the `approved`-covering uniqueness is not safe to
ship before the release valve that precedes it. Step 9 is the one v3 got wrong by leaving it in
a second document.

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

### Resolved by review round 4 — do not relitigate

v3 posed three judgment calls and asked the reviewer to argue rather than confirm them. All
three came back **against** v3's choice.

| | v3 proposed | Round-4 answer |
|---|---|---|
| **Q1** `/resolve` auto-resolve? | Auto-resolve on an exact 5-field match and a twice-confirmed zero | **Report-only.** Matching fields establish shape, not provenance; Schwab exposes no client correlation id and publishes no visibility bound that would make a zero meaningful (§4) |
| **Q2** status-only sweep? | Sufficient; `filledQuantity` is Workstream A's | **Bring it forward.** The cited mitigations are real but cover duplicate-symbol and exposure, not daily-notional accounting (§10, §11) |
| **Q3** session-scoped index? | Attractive; only option that shrinks the phase | **Rejected.** Schwab queues after-hours orders for the next regular session, so "nothing outlives a session" is false (§11) |

Q3's rejection is the expensive one: it was the only path that made this phase smaller, and its
failure is why Phase 0 exists instead.

### Still open

1. **After-hours ceiling attribution.** An order entered Friday night is bucketed to Friday's
   session by submission time but is actionable Monday. Which session's
   `MAX_DAILY_NOTIONAL_USD` should it consume? `market_session_date()` returns an Eastern
   *calendar* date and `market_time.py` deliberately has no exchange calendar, so this cannot be
   answered correctly today. Resolving it likely means an `intended_session_date` mapped through
   a real trading calendar. **Tracked in Phase 0 Open Question 1**; it is the residue of
   finding 10 that rejecting Q3 did not dispose of.
2. **Sell-signal coherence.** §6 now matches the sell instrument to the exit trigger that
   actually exists (`main.py:441`, RSI + MACD), rather than to a long-term-hold premise the code
   does not implement. The deeper incoherence stands: a buy-and-hold income strategy with a
   sensitive upside exit and no downside exit is backwards. Redefining exits is Workstream D,
   and doing so **requires revisiting §6**.
3. **Schwab `FUNDAMENTAL` projection** — `Client.get_instruments(symbols, projection=FUNDAMENTAL)`
   returns `epsChangePercentTTM`, `returnOnEquity`, `currentRatio`, `marketCap`. Could restore
   candidates lost to the `reject` missing-data policy *and* supply the scale-independent
   factors findings 7/8 want. **Unverified** — the Schwab token is ~125 days old and expired.
4. **schwab-py pin drift.** `requirements.txt:172` / `requirements.in:12` pin `1.4.0`; **1.5.1
   is installed** and every API fact in this document was verified against 1.5.1. Bump the pin
   and regenerate the lock with `uv pip compile` before implementing.
5. **Schwab order-list consistency.** `/resolve` is report-only partly because no visibility
   bound is published. If one exists and is short, a narrower automatic path may become
   defensible later — but that is a change to make on evidence, not on convenience.

---

## Backlog

Sequenced in `docs/superpowers/plans/2026-08-14-codex-backlog-roadmap.md`.

**The order changed in v4.** The ledger is no longer the successor to this phase — its storage
layer is the **prerequisite**. `plans/2026-08-15-phase0-order-ledger-foundation.md` takes ledger
Tasks 1, 2 and 4 (plus corrections) and lands first; this phase absorbs ledger Tasks 6 and 8;
the remainder of `plans/2026-08-14-execution-ledger.md` — fill application, the poller, its
scheduling — follows this phase as Workstream A.

Sequence: **Phase 0 → Phase 1 → Workstream A remainder → B / C / D.**

**Standing constraint from finding 6:** no backtest has been run and no forward sample exists.
The 569 passing tests validate software behavior, not predictive power. Every recommendation
remains an unvalidated research lead regardless of how safe the execution path becomes. This
design makes the bot *safe to operate*; it does not make it *worth operating*.
