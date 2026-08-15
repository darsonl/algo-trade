# Review prompt — Phase 1 safety spec v3 (round 4)

Run with:

```bash
codex exec --skip-git-repo-check < docs/superpowers/reviews/2026-08-15-spec-v3-review-prompt.md
```

A long prompt passed as a command-line *argument* silently produces empty output. Pipe the
file on stdin.

---

You are reviewing a design specification for an automated stock trading bot that places real
orders against a real Schwab brokerage account. Your job is to find defects **in the design**,
before it is implemented.

## Read these, in this order

1. `docs/superpowers/specs/2026-08-14-live-trading-safety-design.md` — the spec under review (v3)
2. `docs/superpowers/plans/2026-08-14-execution-ledger.md` — the successor plan; v3 modifies its
   status constants and its `get_day_notional` query
3. `CLAUDE.md` — project conventions, especially the market-session day-bucketing rule
4. The code the spec modifies: `schwab_client/orders.py`, `database/queries.py`,
   `database/models.py`, `discord_bot/bot.py`, `config.py`, `market_time.py`, `main.py`

**Verify claims against the repository. Do not take the spec's word for anything.** Several
defects in previous rounds were statements about the code that were simply not true, and one
was a statement the spec contradicted three sections later.

## Context you need

This bot posts AI-generated BUY recommendations to Discord; a human clicks Approve; the bot
places an order. It ran armed against a live account for two months with none of the guards in
this spec, though nothing ever executed (0 rows in every table). It is now disarmed
(`DRY_RUN=true`) pending this phase.

This spec has been through **three** external review rounds. Every round found real Critical
defects in a document that felt finished:

- Round 1 → v2: 2 Critical, 5 High
- Round 2 → v2 revisions
- Round 3 → v3: 3 Critical, 1 High
- Plus one Critical the author found by re-reading v2 against `master`
- Plus five defects the author found in the v3 draft itself (listed in the spec's "Corrections
  made to the v3 draft" table)

**The recurring defect shape**, seen in most of the above: *the prose asserts that some
component handles a case, and no such component exists.* "The sink re-reads it." "Reconciliation
resolves it." "Released by `completed`." Each read as a settled decision; each was false. v3
adopts a rule against this (see "Process rule adopted in v3"), but v3 was written by the same
author the rule is meant to police, so **treat every claim of the form "X handles this" as
unverified until you find X.**

A second shape worth watching for: *defensive parsing of an unvalidated response converts an
error into confident data.* `get_positions` had no `raise_for_status`, so an HTTP 401 body
flowed through `.get()` chains and emerged as `[]` — "the account holds nothing" — which made a
broker outage **open** the position-size guards instead of closing them.

## What I want from you

Findings, each with:

- **Severity**: Critical / High / Medium / Low. Critical = could place, duplicate, or lose track
  of a real order, or cause a guard to pass when it should block.
- **Evidence**: file and line, or the exact spec sentence, for both the claim and its refutation.
- **Why it fails**: a concrete sequence, not a category. "Under condition X at time T, the code
  does Y, so Z."
- **Fix**: specific enough to implement.

Rank by severity. If you find nothing Critical, say so plainly rather than promoting a Medium.

## Focus areas — the least-reviewed parts of v3

Sections 4 and 11 and everything in the "Corrections made to the v3 draft" table are **new in
this round and have never been externally reviewed.** Weight them accordingly.

Specifically:

1. **§4, `/resolve` matching.** It identifies an ambiguously-submitted order by matching broker
   orders on symbol + side + quantity + type + limit price within a −30s/+120s window. This is a
   personal account — the human can place orders manually at any time. Can a manual order still
   be mis-attributed? Can a *legitimately duplicated* bot order (two identical submissions)
   defeat the 2+-is-ambiguous rule? Is the twice-confirmed-zero rule sound, or does it have its
   own race?

2. **§11, terminal-status allowlist.** `{FILLED, CANCELED, REJECTED, EXPIRED}` frees the ticker;
   everything else keeps it blocked. `REPLACED` was removed from this set for a stated reason —
   check the reasoning. Are any of the four remaining statuses non-terminal in practice? Is any
   omitted status a permanent block with no exit?

3. **§11, partial fills.** Acknowledged gap: the sweep reads status and not `filledQuantity`, so
   a partially-filled-then-cancelled order leaves an unrecorded position. The stated mitigations
   are `/reconcile` reporting it as untracked, and guards 9/10 reading broker positions rather
   than DB positions. **Verify those two mitigations actually exist and actually cover it.**

4. **§2 and §12, the kill switch.** `risk/kill_switch.py` is module-level mutable state read by
   both a preflight guard and the order sink. Check the concurrency story: the approval path
   holds an `asyncio.Lock` and `/halt` is a separate coroutine. Can `/halt` land between the
   guard and the sink? Should it be able to? Does anything else in the process construct its own
   `Config` and diverge?

5. **§9 / §10, ordering and time.** Guard 5 (`broker_unavailable`) must precede every guard that
   consumes broker data — check the guard table order actually enforces that rather than merely
   describing it. Separately, confirm the daily-notional query uses market-session bounds and the
   expiry predicate uses bare UTC, and that neither has been "fixed" to match the other.

## Three decisions I want argued, not confirmed

These are judgment calls where I have picked a side and want the other side made as strongly as
possible. They are stated in the spec's Open Questions as Q1–Q3.

**Q1 — Should `/resolve` auto-resolve at all?** It currently writes state based on inference
about the one situation the design has already declared untrustworthy. The alternative is
report-only, with a human confirming every resolution. Cost of that: an unresolved row reserves
capital and blocks its symbol until someone acts, possibly for days. Who should own the
ambiguity?

**Q2 — Is a status-only sweep sufficient, or must `filledQuantity` come forward from Workstream
A into this phase?**

**Q3 — Scope.** The adopted design adds `recommendations.broker_order_id`, a broker read, and a
sweep, so that a unique index covering `('pending','approved')` has a release valve. The
alternative (end of §11) is a session-scoped index, `UNIQUE(ticker, session_date)`, which needs
none of that and argues that since **all orders are DAY duration** (§6), nothing being guarded
can outlive a session. Its correctness depends entirely on Schwab's after-hours DAY behavior —
if an order placed at 22:00 ET is queued for the next session's open rather than dying at the
boundary, the premise fails at exactly that edge. **This is the only open question whose answer
could make the phase smaller. It was identified after the v3 draft and is unvalidated.** Argue
it properly; do not just agree with the adopted design.

## Environment facts, so you don't rediscover them

- Installed `schwab-py` is **1.5.1** (the lockfile still pins 1.4.0 — a known open question).
  `get_order`, `get_orders_for_account`, `cancel_order`, and `equity_sell_limit` all exist in
  1.5.1. The status enum spells it `CANCELED` (one L) and includes a literal `UNKNOWN` member.
- The host is **Asia/Taipei (UTC+8)**. `SCAN_TIMES=21:45,03:30` are 09:45 ET and 15:30 ET of the
  **same** US session but two different local dates. This is why `market_time.py` exists and why
  `date(...,'localtime')` bucketing is forbidden by `CLAUDE.md`.
- 569 tests pass. They validate software behavior, not profitability. No backtest exists and no
  forward sample exists.
- Strategy context for §6: the stop-loss finding was closed "won't fix, by design — long-term
  hold," which is the premise for bounding sells with limit orders. If you think that premise is
  wrong, say so, because the sell design depends on it.
