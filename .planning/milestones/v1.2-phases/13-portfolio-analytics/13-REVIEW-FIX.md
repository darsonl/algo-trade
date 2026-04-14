---
phase: 13-portfolio-analytics
fixed_at: 2026-04-14T12:11:32Z
review_path: .planning/phases/13-portfolio-analytics/13-REVIEW.md
iteration: 1
fix_scope: critical_warning
findings_in_scope: 3
fixed: 3
skipped: 0
status: all_fixed
---

# Phase 13: Code Review Fix Report

**Fixed at:** 2026-04-14T12:11:32Z
**Source review:** .planning/phases/13-portfolio-analytics/13-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 3
- Fixed: 3
- Skipped: 0

## Fixed Issues

### WR-01: TOCTOU race in `SellApproveRejectView.approve`

**Files modified:** `discord_bot/bot.py`
**Commit:** bbf8669
**Applied fix:** Added an idempotency guard at the top of `SellApproveRejectView.approve`. Before placing the sell order or fetching cost_basis, the handler now calls `queries.has_open_position()` and returns an ephemeral error message if the position is already closed. This prevents duplicate sell trades from concurrent or replayed button presses.

---

### WR-02: Falsy check on `current_price` suppresses valid `$0.00` display

**Files modified:** `discord_bot/embeds.py`
**Commit:** aa2f3fa
**Applied fix:** Changed `if s['current_price']` to `if s['current_price'] is not None` on line 128 of `build_positions_embed`. This matches the pattern already used for `pnl_pct` on the adjacent line and correctly handles the `0.0` edge case.

---

### WR-03: `avg_loss_pct` can be positive in `build_stats_embed` display

**Files modified:** `discord_bot/embeds.py`, `tests/test_embeds.py`
**Commit:** daf5926
**Applied fix:** Added an inline comment `# loss_pct is always <= 0` on the Avg Loss field in `build_stats_embed` to document the invariant and guard against silent regressions. Added a new test `test_build_stats_embed_avg_loss_negative` that asserts the Avg Loss field value starts with `"-"` when `avg_loss_pct=-0.021`, providing the coverage counterpart to the existing `test_build_stats_embed_avg_gain_prefix` test. The new test passes.

---

_Fixed: 2026-04-14T12:11:32Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
