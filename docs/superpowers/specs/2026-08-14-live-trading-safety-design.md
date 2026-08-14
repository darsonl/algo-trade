# Design: Live-Trading Safety Hardening (Codex Phase 1) — v2

**Date:** 2026-08-14
**Status:** Approved (v2, revised after external review)
**Milestone:** v1.4 (candidate)
**Source:** `codex_recommendations.md` — findings 1, 2, 3, 10, roadmap item 1.6

---

## Summary

The bot runs with `DRY_RUN=false` / `PAPER_TRADING=false` against a real Schwab account.
`algo_trade.db` holds 0 recommendations, 0 trades, 0 positions — no live order has ever been
placed. The risk is entirely prospective.

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

### v2 revisions

External review of v1 found 2 Critical and 5 High defects **in the design itself**. All are
addressed here:

| v1 defect | Fix |
|---|---|
| `@_retry` on `_call_place_order` could resubmit an order the broker already accepted; v1 called this "already correct" | Retry removed from submission; ambiguous outcomes become `submit_unknown` and are never auto-reopened |
| `EXECUTION_MODE` was never enforced where orders leave; `schwab_client/orders.py` was not even in scope | Sink guard requiring **both** flags to agree; `orders.py` added to scope |
| Daily-notional and portfolio guards were check-then-act across concurrent approvals | Whole guard→claim→submit sequence serialized under one lock |
| The pending-only unique index stopped protecting the moment status became `approved` | Index covers `('pending','approved')`; recommendations move to `completed` when their order terminates |
| A validated quote could not constrain a market fill | All buys are **limit orders at `quote × (1 + buffer)`, DAY duration** |
| `TRADING_ENABLED` documented as `true` in one place and `false` in another; `/resume` not allowlisted | Default `true`; both `/halt` and `/resume` allowlisted |
| No same-symbol guard; sell quantity never revalidated | Guards 9 and 10 added |

Plus one defect neither review found, surfaced by reading the live config — see below.

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

---

## Scope

**Modified**
- `config.py` — `EXECUTION_MODE`, new safety fields, `validate()` rules
- `schwab_client/orders.py` — **sink guard**, retry removal, DAY-duration limit orders
- `schwab_client/quotes.py` — **new**, live quote fetch
- `risk/preflight.py` — **new**, the pure guard table
- `discord_bot/bot.py` — both approval views, both scan commands, `/halt`, `/resume`
- `database/queries.py` — claim predicate, day-notional query, recommendation completion
- `database/models.py` — partial unique index
- `main.py` — startup warning, scan lock wiring
- `README.md`, `CLAUDE.md`, `.env.example`, `.planning/codebase/*.md` — doc debt

**Not in scope:** order lifecycle states, fill confirmation, real fill prices (Workstream A);
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
Your current settings map to: EXECUTION_MODE=live
Remove both legacy variables from .env.
```

Silently deriving the new value from legacy variables would reintroduce exactly the
unopted-into safety this phase exists to remove. `EXECUTION_MODE=simulated` raises
`NotImplementedError` naming Workstream A.

### 2. The sink guard — both flags must agree

v1's central failure: every guard lived in a pure function that *advises*, and nothing
enforced anything at the point of irreversibility. All three order wrappers reached
`client.place_order` unconditionally.

```python
def _assert_live_execution(config) -> None:
    """Refuse to submit unless BOTH mode flags agree this is live trading.

    Two independent flags exist for compatibility (execution_mode is the env
    surface; dry_run is what 23 tests set to stay off live Schwab). Requiring
    agreement means a disagreement fails CLOSED: the illegal
    execution_mode='dry_run' + dry_run=False state is blocked by the first
    clause, and a test that sets only dry_run=True is protected by the second.
    """
    if config.execution_mode != "live" or config.dry_run:
        raise RuntimeError(
            f"order submission blocked: execution_mode={config.execution_mode!r}, "
            f"dry_run={config.dry_run!r} — both must indicate live trading"
        )
```

Called at the top of `place_order`, `place_limit_order`, and `place_sell_order` — *before*
`get_client`, so a non-live mode never even constructs an authenticated broker client.

This is what makes "structurally incapable of ordering" true rather than aspirational.

### 3. Order submission is never retried

`schwab_client/orders.py:68` currently decorates `_call_place_order` with `@_retry`
(3 attempts). **A timeout after Schwab accepts is an unknown outcome, not a failure** — the
retry can submit the same buy twice. There is no idempotency key in the Schwab order API.

v2:
- **Remove `@_retry` from `_call_place_order`.** Reads (`get_order`, `get_quote`,
  `get_account`) keep their retry; only submission loses it.
- On any submission exception, the order row moves to **`submit_unknown`**, not
  `submit_failed` — the truthful state.
- The recommendation is **not** reopened to `pending`. v1 reopened it, inviting a second
  human approval and a second real order. It stays `approved` and an ops alert asks for
  manual verification in Schwab.

```
⚠️ AAPL order outcome UNKNOWN — the broker call failed after submission.
   The order may or may not exist at Schwab. Check your account before
   approving anything else for this symbol. Recommendation left claimed.
```

### 4. All buys are limit orders, DAY duration

Market orders carry no price, so a validated quote cannot constrain the fill. With scans at
21:45 and approvals before the open, a market order absorbs the full overnight gap.

- Limit price = `quote × (1 + APPROVAL_SLIPPAGE_BUFFER_PCT)`, default **0.5%** — comfortably
  inside the 2% drift tolerance, so the fill is bounded within the band the guards validated.
- **DAY duration**, replacing Phase 17's GTC. GTC allowed Monday's thesis to fill Thursday
  and let unfilled orders silently reserve exposure indefinitely.
- `USE_LIMIT_BUY` is removed; limit is no longer optional. `build_limit_buy` drops
  `.set_duration(Duration.GOOD_TILL_CANCEL)`.

**Accepted consequence:** an approval made when the market is closed produces a DAY order
that may expire unfilled. That is correct — you approved against a closed-market price. It is
no longer *silent*: Workstream A's poller reports `cancelled`/`expired`, and until that lands,
`/reconcile` surfaces it. Verify Schwab's exact after-hours DAY handling during implementation
and document what it does.

### 5. Approvals are serialized

Guards read `day_notional` and exposure, then claim, then submit. Nothing serialized that
sequence, so two approvals for *different* tickers could each read $1,500 against a $2,000
ceiling, each add $400, and both pass — $2,300 total. A per-ticker index cannot prevent a
cross-ticker cap breach.

One module-level `asyncio.Lock` in `discord_bot/bot.py` wraps the entire read→evaluate→claim→
submit sequence for both buy and sell approvals. Human click rates make contention
irrelevant, and the alternative (a DB-level reservation table) is Workstream A's job.

### 6. `risk/preflight.py` — the guard table

```python
def evaluate_trade(request, quote, broker_positions, working_orders,
                   day_notional, config, now) -> Decision
```

`TradeRequest`, `Quote`, and `Decision` live in `risk/preflight.py`, which imports nothing
from `schwab_client` or `discord`. `schwab_client/quotes.py` imports `Quote` from it, never
the reverse. `check_authorization(request, config) -> Decision | None` is a module-level
function returning `None` when authorized; `evaluate_trade` calls it as guard 1 and the button
calls it directly pre-defer.

| # | `reason_code` | Fails when | Buy | Sell |
|---|---|---|---|---|
| 1 | `unauthorized` | user not in allowlist, or guild/channel mismatch | ✓ | ✓ |
| 2 | `trading_disabled` | kill switch engaged | ✓ | ✓ |
| 3 | `expired` | `now >= expires_at` | ✓ | ✓ |
| 4 | `quote_unavailable` | quote missing, or stale **for the current session** | ✓ | ✓ |
| 5 | `price_drift` | `abs(quote − scan)/scan > tolerance` | ✓ | — |
| 6 | `size_zero` | shares at the **limit** price round to 0 | ✓ | — |
| 7 | `daily_notional` | today's committed buy notional + this order > ceiling | ✓ | — |
| 8 | `portfolio_exposure` | broker market value + reservations + this order > ceiling | ✓ | — |
| 9 | `duplicate_symbol` | a position or working buy order already exists for the ticker | ✓ | — |
| 10 | `sell_quantity` | requested shares ≤ 0 or > current broker holding | — | ✓ |

**Guard 1 is first** so an unauthorized clicker learns nothing about the book — rejection
messages are a side channel.

**Guards 6–8 price at the limit**, not the scan price and not the raw quote, so the ceiling is
computed against the maximum the order can actually cost.

**Guard 4 is session-aware.** `QUOTE_MAX_AGE_S` applies during regular hours. Outside them the
guard accepts the last close — a 60-second rule would reject every pre-open approval, which is
when this system is designed to be used. The limit price, not quote freshness, is the binding
control after hours, and the embed says so.

**Guard 9 is new.** Nothing previously stopped a second buy of a symbol you already hold.

**Guard 10 is new.** The sell view captures `self.shares` at post time; the position can shrink
before you click. Revalidate against the broker.

`day_notional` and reservations come from the `orders` table once Workstream A lands. Until
then they come from `trades`, and the spec's build sequence notes the swap.

### 7. Approval path

```
1. check_authorization(...)      pure, pre-defer → ephemeral reject
2. interaction.response.defer()
3. ── acquire approval lock ──
4. fetch live quote              asyncio.to_thread
5. gather broker positions, working orders, day_notional
6. evaluate_trade(...) -> Decision
7. claim_recommendation(...)     atomic, expiry in the SQL predicate
8. create order row (pending_submit)
9. submit limit DAY order; attach broker id, or mark submit_unknown
10. ── release lock ──
```

Authorization runs before `defer()` because it is pure and instant, letting an unauthorized
click get a private reply. `evaluate_trade` re-checks it as guard 1.

### 8. Expiry as a SQL predicate, consistently

```sql
UPDATE recommendations SET status = ?
 WHERE id = ? AND status = 'pending' AND expires_at > datetime('now')
```

A Python-side check before the claim is a TOCTOU race against the expiry sweep.

**Comparison is UTC**, not `localtime` — `expires_at` is `datetime('now','+24 hours')`
(`models.py:52`), which is UTC. CLAUDE.md's `'localtime'` rule governs *calendar-day*
bucketing and must not be applied here. The daily-notional query three sections away
deliberately uses the opposite modifier; do not "fix" either to match.

`expire_stale_recommendations` currently uses `expires_at < datetime('now')`, leaving an
exact-second equality where a row is neither expirable nor claimable. Change it to `<=`.

### 9. Concurrency and duplicate prevention

**One shared `asyncio.Lock`** covering `run_scan` *and* `run_scan_etf` — not one each, since a
symbol can appear in both paths. `/scan` and `/scan_etf` reply "a scan is already running".

**A partial unique index** — the real backstop, since a lock protects one process:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_rec_per_ticker
  ON recommendations(ticker) WHERE status IN ('pending', 'approved');
```

v1 covered `'pending'` only, so protection lapsed the instant the claim flipped a row to
`approved` — precisely the window between claim and order submission.

Covering `approved` requires a release valve, or a ticker would be blocked forever after its
first buy. Recommendations move to **`completed`** when their order reaches a terminal state
(Workstream A's poller; until then, `/reconcile`). Insert catches `sqlite3.IntegrityError` and
skips the ticker. The table has 0 rows, so this applies with no backfill.

### 10. Kill switch

`TRADING_ENABLED` defaults to **`true`** — `EXECUTION_MODE` is already the opt-in, and a
second false-by-default flag would just be another thing to forget. (v1 said `true` in its
config table and `false` in its prose; this resolves that.)

`/halt` flips an in-memory flag on the running process; `/resume` clears it; both reset to the
env value on restart. **Both are subject to the authorization allowlist** — v1 allowlisted
only `/halt`, so anyone could have cleared a halt.

Guard 2 checks the switch, and the sink re-reads it — `/halt` during an in-flight approval
must stop the order, which a preflight-only check cannot do.

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

---

## Error handling

Everything fails **closed**.

| Failure | Behavior |
|---|---|
| Quote fetch raises or is stale in-session | `quote_unavailable`, no order, **no fallback to scan price** |
| Order submission raises | Order → `submit_unknown`, ops alert, recommendation **stays claimed** |
| Duplicate active recommendation | `IntegrityError` caught, ticker skipped, logged |
| Scan already running | Slash command replies without spawning |
| Legacy env var present | Startup raises with the mapping message |
| Flags disagree at the sink | `RuntimeError`, no submission |

Falling back to the scan price on a quote outage would silently restore the exact bug this
design removes. Reopening a `submit_unknown` recommendation would invite a duplicate real
order. Both are called out rather than left implicit.

Drift rejection leaves the recommendation `pending` so the next scan re-evaluates it:

```
⚠️ Blocked: AAPL moved +3.1% since scan
   Scan price:  $184.20  (21:45)
   Live quote:  $189.91  (08:32, market closed — last close)
   Tolerance:   2.0%

Order NOT placed. Recommendation left pending;
next scan will re-evaluate with fresh technicals.
```

---

## Testing

TDD throughout.

| File | Covers |
|---|---|
| `test_broker_isolation.py` | Sink guard: `place_order`/`place_limit_order`/`place_sell_order` raise in every non-live flag combination, including `execution_mode='dry_run'` + `dry_run=False` and `execution_mode='live'` + `dry_run=True`. **Write first.** |
| `test_preflight.py` | All 10 guards table-driven, plus boundaries: drift exactly at tolerance, exposure exactly at ceiling, quote exactly at max age, shares rounding to 0, session-aware staleness in and out of hours |
| `test_execution_mode.py` | Legacy var rejection, `simulated` startup failure, `dry_run` derivation, empty allowlist under `live` |
| `test_approval_flow.py` | Unauthorized user, wrong guild, expired button, drift block, quote outage, `submit_unknown` on submission failure, recommendation NOT reopened |
| `test_approval_serialization.py` | Two concurrent approvals for different tickers cannot jointly exceed `MAX_DAILY_NOTIONAL_USD` |
| `test_scan_lock.py` | Concurrent `/scan` rejection, shared lock across stock and ETF |
| `test_claim_expiry.py` | SQL expiry claim, equality boundary, index covering `pending` **and** `approved`, release on `completed` |
| `test_limit_order_construction.py` | Buffer arithmetic, DAY duration (not GTC), limit ≥ quote |
| `test_kill_switch.py` | `/halt` and `/resume` both allowlisted; sink re-read stops an in-flight approval |

Existing tests that must keep passing: the 23 `dry_run = True` protection sites, and
`test_config.py`'s construction-time env reads.

Add `pytest.ini` with `asyncio_default_fixture_loop_scope = function`.

---

## Build sequence

1. `test_broker_isolation.py` against current code — expect FAIL (no sink guard exists yet).
   This is the test that proves the defect before fixing it.
2. Sink guard in `schwab_client/orders.py` + `EXECUTION_MODE` + config + `validate()` + doc debt
3. Remove `@_retry` from submission; `submit_unknown` handling
4. `schwab_client/quotes.py`
5. `risk/preflight.py` with its full 10-guard test table
6. Limit + DAY order construction; remove `USE_LIMIT_BUY`
7. Rewire `ApproveRejectView`, then `SellApproveRejectView`; add the approval lock
8. SQL claim predicate + `<=` expiry fix + partial unique index + `completed` transition
9. Scan lock + `/halt` + `/resume`
10. Full `pytest -q` and `ruff check .`

---

## Backlog

Sequenced in `docs/superpowers/plans/2026-08-14-codex-backlog-roadmap.md`. Workstream A
(execution ledger) is the direct successor and supplies the `orders` table this design's
guards 7–9 will read from.

**Standing constraint from finding 6:** no backtest has been run and no forward sample exists.
The 546 passing tests validate software behavior, not predictive power. Every recommendation
remains an unvalidated research lead regardless of how safe the execution path becomes. This
design makes the bot *safe to operate*; it does not make it *worth operating*.
