# Plan: Phase 0 — Order Ledger Foundation

**Date:** 2026-08-15
**Status:** Draft (unreviewed)
**Supersedes ordering in:** `plans/2026-08-14-execution-ledger.md` ("Phase 1 must land first")
**Blocks:** `specs/2026-08-14-live-trading-safety-design.md` (Phase 1 safety)
**Source:** review round 4, findings 1, 2, 4, 5, 6, 9, 11

---

## Why this phase exists

Review round 4 returned 10 Critical findings against the Phase 1 safety spec. **Six of them
(1, 2, 5, 6, 9, 11) are one defect**: Phase 1's design requires durable per-order state, and
Phase 1's Scope explicitly excludes the table that would hold it.

Verified: `database/models.py` creates exactly five tables — `recommendations`, `trades`,
`analyst_cache`, `positions`, `analyst_calls`. There is no `orders` table. Meanwhile the safety
spec creates "an order row" (§9 step 8), queries `FROM orders` (§10), and needs somewhere to
keep reserved notional, the attempted quantity and limit, submission state, and the last broker
status seen. §8 says these come "from `trades`" until Workstream A lands — but `trades` has no
status column and cannot express `submit_unknown` at all.

So the dependency runs the opposite way from what both documents assert. The ledger plan's
Global Constraints say "Phase 1 must land first"; in fact **Phase 1 cannot be implemented until
the order ledger exists.** This phase extracts the minimum storage substrate, ahead of both.

**This phase is deliberately inert.** It adds tables, columns, constants, and pure functions.
It rewires no approval path, places no orders, and changes no runtime behavior. Nothing in it
can place a trade, which is why it is safe to land before the safety guards do.

---

## The three-way split

`plans/2026-08-14-execution-ledger.md` is decomposed rather than reordered:

| Ledger task | Goes to | Why |
|---|---|---|
| **Task 1** — `orders` table, `trades` columns, CRUD | **Phase 0** | The substrate every finding above needs |
| **Task 2** — broker status mapping and response parsing | **Phase 0** | Pure functions; Phase 1's sweep consumes them |
| **Task 4** — fetch order status from Schwab | **Phase 0** | Phase 1's sweep calls it |
| **Task 6** — approve button creates an order before submitting | **Phase 1** | Rewires the approval path, which the safety spec also rewires. Two documents editing one handler is what produced finding 2. |
| **Task 8** — exposure from broker positions and reserved orders | **Phase 1** | It is a preflight guard input, and finding 9 changes what it must read |
| **Tasks 3, 5, 7, 9** — fill application, poller, scheduling, docs | **Workstream A** | Genuinely post-Phase-1 |

Sequence becomes **Phase 0 → Phase 1 → Workstream A remainder.**

---

## Global Constraints

Inherited from `plans/2026-08-14-execution-ledger.md` (Python 3.11, `get_cursor`, `asyncio.to_thread`
for broker I/O, `CREATE TABLE IF NOT EXISTS` / `try: ALTER TABLE / except sqlite3.OperationalError`,
module-level `DB_PATH` with an autouse `fresh_db` fixture, commit after every task), plus:

- **Day-bucketing uses the US market session** via `market_time.market_session_bounds_utc()` as a
  range predicate. Never `'localtime'`, never bare UTC. See `CLAUDE.md` and commit `36761da`.
- **No test in this phase may reach live Schwab.** Task 4 is the only broker-touching work and is
  tested against fixtures.

---

## Adopt as written

Ledger **Task 1**, **Task 2**, and **Task 4** are adopted as specified in
`plans/2026-08-14-execution-ledger.md`, including their test suites, *except* for the deltas
below. They are not restated here — duplicating 900 lines would create exactly the drift that
produced finding 2, where two documents specified the same handler differently.

---

## Delta 1 — Status constants  *(findings 1, 6)*

Replaces the constants block in Task 1 Step 4. Already partially applied to the ledger plan on
2026-08-15; this is the complete target state.

```python
OPEN_ORDER_STATUSES       = ("pending_submit", "submitted", "working", "partially_filled")
UNRESOLVED_ORDER_STATUSES = ("submit_unknown",)
TERMINAL_ORDER_STATUSES   = ("filled", "cancelled", "rejected", "submit_failed", "expired")

# Might have committed real capital. submit_unknown is included deliberately: the
# broker call was ambiguous, so the order MAY exist. Assuming otherwise is the
# fail-open direction.
COMMITTING_ORDER_STATUSES = OPEN_ORDER_STATUSES + UNRESOLVED_ORDER_STATUSES + ("filled",)

# Blocks a second buy of the same symbol.
BLOCKING_ORDER_STATUSES   = OPEN_ORDER_STATUSES + UNRESOLVED_ORDER_STATUSES
```

**`cancelled` and `expired` are terminal but not free.** Finding 6: an order that partially
fills and is then cancelled would, under a status-only rule, drop its *entire* notional from the
daily ceiling while leaving a real position. Commitment is therefore computed per-order from
quantities, not from status alone — see Delta 2.

---

## Delta 2 — Commitment is priced at the limit, and survives partial fills  *(findings 5, 6)*

Replaces `get_day_notional` in Task 1 Step 4. Two defects in one function.

**Finding 5:** the query sums `requested_shares * reference_price`, while the safety spec §8
asserts guards price at the limit. An order can execute at `quote × 1.005`, so reserving the
quote lets a second order pass at the ceiling boundary when both can fill above the cap.

**Finding 6:** a terminal status must not release a commitment that actually filled.

```python
def order_commitment(row: dict) -> float:
    """Dollars this order should count against the ceilings, right now.

    Open / unresolved: the maximum it can still cost — the broker-rounded LIMIT
    price, not the reference quote (finding 5).
    Terminal: only what actually filled (finding 6), so a partially-filled-then-
    cancelled order keeps its real commitment instead of releasing all of it.
    """
    if row["status"] in TERMINAL_ORDER_STATUSES:
        return float(row["filled_notional"] or 0.0)
    unit = row["limit_price"] if row["limit_price"] is not None else row["reference_price"]
    remaining = float(row["requested_shares"]) - float(row["filled_shares"] or 0.0)
    return float(row["filled_notional"] or 0.0) + max(remaining, 0.0) * float(unit)


def get_day_notional(db_path: str, instant: datetime | None = None) -> float:
    """This SESSION's committed buy notional. See order_commitment for pricing.

    "Today" is the US MARKET SESSION date. An earlier draft used
    date(submitted_at,'localtime'), the convention commit 36761da removed: on this
    UTC+8 host SCAN_TIMES=21:45,03:30 are one session but two local dates, so a
    localtime bucket resets the ceiling mid-session and admits double
    MAX_DAILY_NOTIONAL_USD. The range predicate also leaves submitted_at
    index-usable. `instant` is injectable so tests can pin time without freezegun.
    """
    start, end = market_session_bounds_utc(instant)
    with get_cursor(db_path) as conn:
        rows = conn.execute(
            """SELECT status, requested_shares, reference_price, limit_price,
                      filled_shares, filled_notional
                 FROM orders
                WHERE side = 'buy'
                  AND submitted_at >= ? AND submitted_at < ?""",
            (start, end),
        ).fetchall()
    return sum(order_commitment(dict(r)) for r in rows)
```

`order_commitment` is a pure function over a row dict and is table-driven tested independently
of SQLite.

**Open question for review:** an order entered after hours is bucketed to the session date of
its *submission*, but Schwab queues it for the next regular session (finding 10). Which
session's ceiling should it consume? Delta 6 records this as unresolved.

---

## Delta 3 — `submit_unknown` is representable and auditable  *(findings 1, 11)*

Task 1 provides `mark_order_submit_failed` only. Add:

```python
def mark_order_submit_unknown(db_path, order_id, reason) -> None:
    """Ambiguous submission outcome — the order MAY exist at the broker."""

def record_broker_status(db_path, order_id, broker_status, payload_json) -> None:
    """Store the last status seen and its raw payload, for audit and for the
    stuck-order alert. Never changes `status` — mapping is the caller's job."""

def resolve_order_manually(db_path, order_id, resolution, actor, evidence) -> None:
    """Operator override for an order that cannot be resolved automatically.

    resolution ∈ {'adopt', 'confirmed_absent', 'keep_blocked'}. Finding 11: the
    safety spec claimed a manual exit existed and designed none. This is it.
    Writes an audit row; refuses any source status outside
    UNRESOLVED_ORDER_STATUSES.
    """
```

New columns on `orders` (same `ALTER TABLE` idiom): `last_broker_status TEXT`,
`last_broker_payload TEXT`, `last_checked_at TEXT`, `resolution TEXT`,
`resolved_by TEXT`, `resolved_at TEXT`, `resolution_evidence TEXT`.

**No zero-observation counters.** v3 designed a twice-confirmed-zero auto-resolution; review
round 4 (Q1) rejected auto-resolution entirely — five matching fields establish *shape*, not
*provenance*, and Schwab publishes no visibility bound that would make a zero meaningful. With
`/resolve` report-only, there is no automatic transition to persist. This delta is smaller than
v3's design, not larger.

---

## Delta 4 — Status mapping keeps replacements alive  *(finding 4)*

Applies to Task 2.

```python
TERMINAL_BROKER_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED"})
```

Allowlist, never denylist: Schwab's enum contains a literal `UNKNOWN` member (verified against
installed schwab-py **1.5.1**, which also spells it `CANCELED` with one L). A denylist would
free a ticker on the strength of an unrecognized status.

**`REPLACED` and `PENDING_REPLACE` are not terminal**, and — finding 4 — must *not* be routed to
any matcher that searches for the original submission. Replacement creates a new order under a
new id, at a possibly different price, outside any window anchored on the original. A matcher
would find nothing, and a "nothing found" rule would mark the submission failed while the
replacement is working.

Instead, `map_broker_status` extracts `replacingOrderCollection[].orderId` from the payload and
returns it alongside the status, so the caller follows the chain to the successor id rather than
guessing. If the payload has no successor id, the order becomes `submit_unknown` for human
resolution — never terminal.

---

## Delta 5 — `get_order_status` returns the payload, not a string  *(finding 4)*

Applies to Task 4, and to the safety spec §11 which specified `-> str`.

A bare status string cannot carry `replacingOrderCollection`, `filledQuantity`, or the average
fill price — and Deltas 2 and 4 need all three. Signature becomes:

```python
def fetch_order(config, broker_order_id, client=None) -> dict:
    """Full order payload from Client.get_order (schwab-py 1.5.1), through the
    validating wrapper — raise_for_status BEFORE parsing, so an error body can
    never be read as data (safety spec §5 / round-3 C3)."""
```

---

## Delta 6 — Recorded as unresolved, not silently decided

- **After-hours session attribution** (finding 10). Schwab accepts regular-session orders at any
  time and queues them for the next regular session, but `market_session_date()` returns an
  Eastern *calendar* date with midnight-to-midnight bounds — not an exchange-session assignment.
  A Friday-night order buckets to Friday while remaining actionable Monday. Phase 0 buckets on
  submission time and **flags this as an open decision** rather than inventing an exchange
  calendar. Resolving it likely means an `intended_session_date` column populated through a real
  trading calendar. **This finding also killed the Q3 session-scoped-index alternative.**
- **Cross-process reservation** (finding 8). Making the cap check and the reservation one
  `BEGIN IMMEDIATE` transaction belongs in Phase 1, where the check lives. Phase 0 must not
  block it: no long-lived read transactions, and `create_order` must be callable inside a
  caller-supplied transaction.

---

## Build sequence

1. Ledger Task 1 as written, **with Deltas 1, 2, 3** — table, `trades` columns, CRUD, constants,
   `order_commitment`, `get_day_notional`, the audit columns
2. `test_order_commitment.py` — table-driven over the pure function: open vs terminal, partial
   fill retained, limit-vs-reference pricing, `filled_shares` exceeding `requested_shares`
3. `test_day_notional_session.py` — a 21:45 and an 03:30 order on this host land in **one**
   bucket; the ceiling does not reset between them; DST spring-forward and fall-back
4. Ledger Task 2 as written, **with Delta 4** — status mapping, `REPLACED` non-terminal and
   chain-following, `UNKNOWN` and invented statuses non-terminal
5. Ledger Task 4 as written, **with Delta 5** — `fetch_order` returning the validated payload
6. `test_manual_resolution.py` — `resolve_order_manually` accepts only unresolved source states,
   writes the audit row, and refuses an unknown resolution value
7. Full `pytest -q` and `ruff check .`

**Explicitly not in this phase:** ledger Tasks 3, 5, 6, 7 (fill application, poller, approval
rewiring, scheduling) and anything that submits an order.

---

## Verification

- 569 existing tests keep passing; this phase is additive.
- No test constructs a live Schwab client. Task 4's tests use fixture payloads.
- `initialize_db` on an existing `algo_trade.db` adds the table and columns without data loss.
  The live DB has 0 rows in every table, so there is nothing to migrate.

---

## Open questions

1. **After-hours session attribution** — Delta 6. Needs a real trading calendar to resolve
   properly; `market_time.py` deliberately does not have one.
2. **Does `filled_notional` come from the broker or from `filledQuantity × price`?** Task 3
   (Workstream A) applies fills. Until it lands, `filled_shares`/`filled_notional` stay 0 and
   `order_commitment` degrades to "full limit-priced commitment for everything open" — which is
   conservative, and therefore the correct direction to be wrong in. Confirm that is acceptable
   for Phase 1 rather than a reason to pull Task 3 forward too.
3. **Is `expired` in `TERMINAL_ORDER_STATUSES` correct** given DAY orders and after-hours
   queueing? It interacts with open question 1.

---

## Standing constraint

No backtest exists and no forward sample exists. This phase makes order state *recordable*; it
does not make any recommendation more likely to be right.
