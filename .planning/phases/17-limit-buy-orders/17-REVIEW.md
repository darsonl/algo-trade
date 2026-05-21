---
phase: 17-limit-buy-orders
reviewed: 2026-05-21T00:00:00Z
depth: standard
files_reviewed: 10
files_reviewed_list:
  - config.py
  - schwab_client/orders.py
  - database/models.py
  - database/queries.py
  - tests/test_schwab_orders.py
  - discord_bot/embeds.py
  - discord_bot/bot.py
  - main.py
  - tests/test_discord_embeds.py
  - tests/test_discord_buttons.py
findings:
  critical: 0
  warning: 2
  info: 1
  total: 3
status: issues_found
---

# Phase 17: Code Review Report

**Reviewed:** 2026-05-21
**Depth:** standard
**Files Reviewed:** 10
**Status:** issues_found

## Summary

Phase 17 introduces `USE_LIMIT_BUY` config flag, `build_limit_buy`/`place_limit_order` in `schwab_client/orders.py`, `limit_price`/`order_type` columns in the `trades` table, `scan_time` kwarg threading through the embed and Discord bot layers, and GTC duration enforcement. The implementation is structurally sound: config parsing uses the correct `.lower() == "true"` pattern, the GTC duration is set before `.build()`, and the dry-run path correctly records market defaults regardless of `use_limit_buy`. Tests cover the six critical routing branches.

Two warnings require attention before live trading: a phantom-position state divergence when a GTC limit goes unfilled, and dead state stored in `ApproveRejectView` that is allocated on every button click but never consumed.

---

## Warnings

### WR-01: GTC Limit Order Creates Position Before Fill — Phantom Position State

**File:** `discord_bot/bot.py:76-95`

**Issue:** When `use_limit_buy=True` and `dry_run=False`, `place_limit_order` returns an order ID the moment the broker *acknowledges* the working order — not when shares are actually purchased. The code immediately calls `queries.create_trade(...)` and `queries.upsert_position(...)` on lines 84-95 before any fill confirmation. Because GTC orders can sit open indefinitely (days to weeks), the position tracker will diverge from reality for the entire duration:

1. `has_open_position` returns `True` for the ticker, blocking re-buy recommendations on subsequent scans even though no shares are held.
2. The exposure guard at lines 58-71 counts the phantom position's cost toward `max_portfolio_usd`, artificially consuming portfolio budget.
3. The sell-pass loop in `main.py:284+` will attempt to generate sell recommendations and eventually place a market sell order for shares the account does not own.
4. `get_trade_stats` computes P&L against a fictional fill price if the order is later modified or cancelled at the broker.

The market-order path in `place_order` has the same structure but the fill window is seconds (not days), so divergence is negligible in practice. GTC fundamentally changes the risk profile.

**Fix:** Two options depending on roadmap appetite:

Option A (minimal, deferred reconciliation) — add a `fill_status TEXT DEFAULT 'pending'` column to `trades` and a `fill_pending BOOLEAN DEFAULT 0` column to `positions`. Record the trade and position as pending on limit-order placement; a separate reconciliation job (or the sell-pass loop) queries the Schwab order status endpoint and marks them filled/cancelled. Do not count `fill_pending=True` positions in the exposure guard or sell-pass eligibility.

Option B (simplest immediate fix) — skip `upsert_position` when `order_type == 'limit'` and instead create the position only after a confirmed fill event (requires a Schwab order-status webhook or polling loop). This is the architecturally correct answer but requires a follow-on phase.

For Phase 17 scope, at minimum document this behavior prominently in a code comment at line 84 and in `17-CONTEXT.md` so operators are not surprised when the sell pass tries to exit a phantom position:

```python
# WARNING (RISK-05 / Phase 17): GTC limit orders are recorded as positions immediately
# on broker acknowledgement, not on fill. If the limit does not fill, has_open_position()
# will block re-buys and the sell pass may attempt to sell non-existent shares.
# Fill reconciliation is deferred to a future phase.
queries.create_trade(...)
queries.upsert_position(...)
```

---

### WR-02: `self.scan_time` Stored in `ApproveRejectView` but Never Consumed

**File:** `discord_bot/bot.py:39-45`

**Issue:** `ApproveRejectView.__init__` accepts `scan_time` as a parameter (line 39) and stores it as `self.scan_time` (line 45). Neither the `approve` handler (lines 47-105) nor the `reject` handler (lines 107-111) reads `self.scan_time`. The value is allocated on every button instantiation but produces no observable behavior.

This is dead state. If `scan_time` was intended to be surfaced in the approval confirmation message (e.g., "Approved at 09:05 — limit GTC placed at scan-time price"), the intent was not realized. If it was threaded into `ApproveRejectView` purely as a pass-through to the embed (which already received `scan_time` in the `build_recommendation_embed` call on line 288 before `ApproveRejectView` is constructed on line 289), then storing it on the view is unnecessary.

**Fix:** Either use `self.scan_time` in the approval confirmation message to provide staleness context to the operator:

```python
# In approve handler, replace the current msg construction:
elapsed = ""
if self.scan_time:
    elapsed = f" (scan at {self.scan_time})"
if self.config.dry_run:
    msg = f"[DRY RUN] Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f}{elapsed}."
elif self.config.use_limit_buy:
    msg = f"Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f} (limit, GTC{elapsed})."
else:
    msg = f"Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f}{elapsed}."
```

Or, if purely pass-through to the embed was the intent and `self.scan_time` has no further purpose, remove the attribute to avoid misleading future readers:

```python
def __init__(self, rec_id: int, ticker: str, price: float, config: Config, scan_time: str | None = None):
    super().__init__(timeout=None)
    self.rec_id = rec_id
    self.ticker = ticker
    self.price = price
    self.config = config
    # scan_time intentionally not stored — passed to embed before view construction
```

---

## Info

### IN-01: `place_limit_order` Docstring Mentions Internal Format Detail Inconsistently

**File:** `schwab_client/orders.py:90-94`

**Issue:** The docstring for `place_limit_order` at line 92 reads: "Takes limit_price as float and formats internally to `f'{limit_price:.2f}'` (D-03)." The inline citation `(D-03)` is a plan-internal reference that has no meaning outside the phase planning artifacts. It will be opaque to anyone reading the code in six months or in a fresh context.

**Fix:** Replace the parenthetical plan reference with a plain English rationale:

```python
def place_limit_order(ticker: str, shares: int, limit_price: float, config, client=None) -> str:
    """Place a GTC limit buy order via the Schwab API.

    Takes limit_price as float and formats to two decimal places internally
    (e.g. 52.3 -> "52.30") to satisfy the Schwab API's string price requirement.
    Returns the order ID string on success, or raises RuntimeError on failure.
    Mirrors place_order structure exactly.
    """
```

---

_Reviewed: 2026-05-21_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
