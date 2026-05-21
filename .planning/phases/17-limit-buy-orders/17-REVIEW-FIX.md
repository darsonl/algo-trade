---
phase: 17-limit-buy-orders
fixed_at: 2026-05-21T00:00:00Z
fix_scope: critical_warning
findings_in_scope: 2
fixed: 2
skipped: 0
iteration: 1
status: all_fixed
---

# Phase 17: Code Review Fix Report

**Fixed:** 2026-05-21
**Scope:** Critical + Warning (2 findings)
**Status:** all_fixed

## Fixes Applied

### WR-01: GTC Limit Order Creates Position Before Fill — Phantom Position State

**File:** `discord_bot/bot.py`
**Fix applied:** Added prominent 4-line warning comment immediately above `queries.create_trade(...)` and `queries.upsert_position(...)` in `ApproveRejectView.approve`, documenting the acknowledge-vs-fill divergence risk, the downstream effects (blocked re-buys, phantom sell attempts), and that fill reconciliation is deferred to a future phase.

```python
# WARNING (RISK-05 / Phase 17): GTC limit orders are recorded as positions immediately
# on broker acknowledgement, not on fill. If the limit does not fill, has_open_position()
# will block re-buys and the sell pass may attempt to sell non-existent shares.
# Fill reconciliation is deferred to a future phase.
```

**Option chosen:** Option A minimal (comment) — deferred reconciliation documented in code. Full fill-status column approach deferred to a follow-on phase as the review recommended.

---

### WR-02: `self.scan_time` Stored in `ApproveRejectView` but Never Consumed

**File:** `discord_bot/bot.py`
**Fix applied:** `self.scan_time` is now surfaced in the approval confirmation message for all three branches (dry-run, limit-buy, market). The elapsed suffix `(scan at {scan_time})` is only appended when `scan_time` is non-None, preserving backward compatibility when no scan_time is passed.

```python
elapsed = f" (scan at {self.scan_time})" if self.scan_time else ""
if self.config.dry_run:
    msg = f"[DRY RUN] Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f}{elapsed}."
elif self.config.use_limit_buy:
    msg = f"Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f} (limit, GTC{elapsed})."
else:
    msg = f"Approved: buying {shares} share(s) of {self.ticker} at ${self.price:.2f}{elapsed}."
```

---

## Out of Scope (Info)

### IN-01: `place_limit_order` Docstring Plan Reference

Not fixed — Info severity is excluded from default fix scope (`critical_warning`). Re-run with `--all` to include Info findings.

---

_Fixed: 2026-05-21_
_Fixer: Claude (gsd-code-fixer)_
_Tests: 20 passed (test_discord_buttons.py)_
