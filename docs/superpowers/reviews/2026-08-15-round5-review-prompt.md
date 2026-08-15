# Review prompt — Phase 0 + Phase 1 spec v4 (round 5)

Run with:

```bash
codex exec --skip-git-repo-check < docs/superpowers/reviews/2026-08-15-round5-review-prompt.md
```

A long prompt passed as a command-line *argument* silently produces empty output. Pipe the file.

---

You are reviewing two design documents for an automated stock trading bot that places real
orders against a real Schwab brokerage account. Find defects **in the design**, before it is
implemented.

## Read these, in this order

1. `docs/superpowers/plans/2026-08-15-phase0-order-ledger-foundation.md` — **new this round,
   never externally reviewed.** The storage layer; a prerequisite for everything else.
2. `docs/superpowers/specs/2026-08-14-live-trading-safety-design.md` — Phase 1, at **v4**.
   The "v4 revisions" table lists what changed and why.
3. `docs/superpowers/plans/2026-08-14-execution-ledger.md` — Workstream A. Phase 0 adopts its
   Tasks 1/2/4 *by reference plus deltas*, and Tasks 6/8 moved out of it into Phase 1. Check
   that split is coherent and that nothing fell between the documents.
4. `CLAUDE.md` — project conventions, especially market-session day bucketing and the venv rule.
5. The code these touch: `schwab_client/orders.py`, `database/queries.py`,
   `database/models.py`, `discord_bot/bot.py`, `config.py`, `market_time.py`, `main.py`,
   `schwab_client/reconcile.py`.

**Verify every claim against the repository. Do not take either document's word for anything.**

## Context

This bot posts AI-generated BUY recommendations to Discord; a human clicks Approve; the bot
places an order. It is currently **disarmed** (`DRY_RUN=true`) with 0 rows in every table — no
live order has ever been placed. The risk is entirely prospective.

Sequence under review: **Phase 0 → Phase 1 → the remainder of the ledger plan.**

## Review history — four rounds, four sets of Criticals

| Round | Result |
|---|---|
| 1 → v2 | 2 Critical, 5 High |
| 2 → v2 revisions | — |
| 3 → v3 | 3 Critical, 1 High (+1 found by the author re-reading against master) |
| 4 → v4 | **10 Critical, 1 High, 1 Medium** |

Round 4's headline: **six of its ten Criticals reduced to one fact** — Phase 1 required durable
per-order state while Phase 1's own Scope excluded the table holding it. That boundary was
inherited from v1 and survived three rounds because **each round reviewed the document's
contents and never its edges.** Phase 0 exists because of that finding.

## Three defect shapes that keep recurring here

Weight these when reading.

**1. A prose claim that names no implementation.** "The sink re-reads it." "Reconciliation
resolves it." "`/resolve` accepts a manual override." Each read as a settled decision; none
existed. v4 adopts a rule against this ("Process rule adopted in v3") — but both documents were
written by the same author that rule is meant to police. **Treat every "X handles this" as
unverified until you find X.**

**2. Defensive parsing of an unvalidated response turns an error into confident data.**
`get_positions` had no `raise_for_status`, so an HTTP 401 body flowed through `.get()` chains
and emerged as `[]` — "the account holds nothing" — making a broker outage *open* the
position-size guards. Now fixed; look for the same shape elsewhere.

**3. NEW, and the one to weight most heavily this round — reachability, not just correctness.**
`get_positions` also referenced `schwab.Client`, which does not exist in schwab-py 1.5.1. It
raised `AttributeError` on **every call** for months. Three review rounds read that function and
saw nothing wrong, because the code is correct-looking in isolation; CI was green because no
test ever called it; and `main.py` swallowed the exception into a log warning. Round 4 then
cited `/reconcile` as a live mitigation for a partial-fill gap — **it was citing dead code.**

So for every mechanism these documents rely on, ask three questions, not one:
- Is it correct?
- **Is it reached?** Who calls it, on what path, and does anything exercise that path?
- **If it fails, does anyone find out?** Or is it swallowed?

Both documents lean on functions whose callers do not exist yet. That is expected for a design,
but it means the *wiring* is the least-verified part and deserves the most scrutiny.

## What I want from you

Findings, each with:

- **Severity** — Critical / High / Medium / Low. Critical = could place, duplicate, or lose
  track of a real order; cause a guard to pass when it should block; or leave money committed
  with no record.
- **Evidence** — file and line, or the exact sentence, for both the claim and its refutation.
- **Why it fails** — a concrete sequence. "Under condition X at time T, the code does Y, so Z."
- **Fix** — specific enough to implement.

Rank by severity. If nothing is Critical, say so plainly rather than promoting a Medium.

## Focus areas

**Phase 0 has never been reviewed at all.** Weight it accordingly. In particular:

1. **Is Phase 0 actually inert?** It claims to add only tables, columns, constants and pure
   functions, and to be safe to land before the safety guards exist. Verify that — it is the
   entire justification for sequencing it first.
2. **`order_commitment()`** (Delta 2). It prices open orders at the limit and retains
   `filled_notional` through terminal statuses. Check the arithmetic against partial fills,
   over-fills (`filled_shares > requested_shares`), zero/NULL columns, and sells. Does the
   daily-ceiling sum double-count anything, or release capital it should not?
3. **The delta-plan structure itself.** Phase 0 adopts ledger Tasks 1/2/4 "as written except
   for these deltas" rather than restating them, to avoid the two-documents-disagree failure
   that produced round-4 finding 2. Does that actually work, or has it created a subtler
   version of the same problem? Are the deltas complete — does anything in Tasks 1/2/4 still
   contradict them?
4. **`resolve_order_manually`** (Delta 3) — the audited operator override. Are the allowed
   source states, transitions, and evidence requirements sufficient to prevent an operator from
   resolving a row into a state that loses a real order?
5. **The Phase 0 / Phase 1 seam.** Tasks 6 and 8 moved into Phase 1; Tasks 3/5/7 stayed in
   Workstream A. Did anything Phase 1 needs get left in Workstream A, or vice versa?

**In spec v4, these sections are new and unreviewed:**

6. **§2 / §12 kill switch** — now persisted, defaults `UNINITIALIZED`, and uses a
   `submission_gate()` spanning the final check through dispatch. Check the concurrency claim
   properly: the gate is a `threading.RLock` but the approval path is asyncio and the broker
   call goes through `asyncio.to_thread`. Does the lock actually span what the document says?
   Can `/halt` deadlock the event loop while holding it?
7. **§4 report-only `/resolve`** — worst-case reservation is `sum(order_commitment(c))` over
   candidates. Can that over-reserve so badly it wedges trading? Is "floored at the order's own
   commitment" right?
8. **§6 marketable limit sells** — priced *through* the bid by the slippage buffer. Is that the
   right instrument given the RSI+MACD trigger at `main.py:441`, and is the buy/sell asymmetry
   defensible?
9. **§7 `BEGIN IMMEDIATE`** — cap check plus reservation in one transaction, to close the
   cross-process gap. Does this interact safely with the existing `get_cursor` helper, WAL mode,
   and the `asyncio.Lock` held around it? Any lock-ordering or timeout hazard?
10. **§11 `REPLACED` chain-following** — the sweep follows `replacingOrderCollection[].orderId`
    instead of searching. Can the chain loop, fork, or dead-end? What if the successor is itself
    replaced before the next sweep?

## Decisions to argue, not confirm

- **Was inverting the dependency right?** Phase 0 first is a large restructuring driven by one
  round-4 finding. Is there a smaller correct answer that was missed?
- **After-hours session attribution** (spec Open Questions, Phase 0 Open Question 1). An order
  entered Friday night buckets to Friday by submission time but is actionable Monday. Which
  session's `MAX_DAILY_NOTIONAL_USD` should it consume? `market_time.market_session_date()`
  returns an Eastern *calendar* date and there is deliberately no exchange calendar. Both
  documents defer this. Is deferring it safe, or does it need resolving before implementation?
- **Is `expired` correctly terminal** given DAY orders and after-hours queueing?

## Environment facts, so you do not rediscover them

- **schwab-py 1.5.1**, now both installed *and* locked (`requirements.txt`); the local `.venv`
  matches the lock exactly — 75 pins, 0 drifted. Every API fact in these documents was verified
  against 1.5.1: `get_order`, `get_orders_for_account`, `cancel_order`, `equity_sell_limit` all
  exist; the status enum spells it `CANCELED` (one L) and includes literal `UNKNOWN` and
  `REPLACED` members.
- Local runs Python **3.12** in `.venv`, matching CI (3.11 + 3.12). System Python is 3.14 and
  cannot install the lock — that mismatch caused months of silent dependency drift.
- **584 tests pass**, ruff clean, CI green. They validate software behaviour, **not
  profitability**. No backtest exists and no forward sample exists.
- `DRY_RUN=true`; the DB has 0 recommendations, 0 trades, 0 positions.
- Strategy premise behind §6: the stop-loss finding was closed "won't fix, by design —
  long-term hold." Round 4 established that premise is **not** encoded in the sell path
  (`main.py:441` uses RSI + MACD). v4 changed the instrument to match the code rather than the
  premise. Say so if you think that was the wrong direction.
