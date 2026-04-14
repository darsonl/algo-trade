---
phase: 13-portfolio-analytics
reviewed: 2026-04-14T00:00:00Z
depth: standard
files_reviewed: 9
files_reviewed_list:
  - screener/positions.py
  - discord_bot/embeds.py
  - discord_bot/bot.py
  - database/models.py
  - database/queries.py
  - tests/test_positions.py
  - tests/test_embeds.py
  - tests/test_sell_buttons.py
  - tests/test_database.py
findings:
  critical: 0
  warning: 3
  info: 3
  total: 6
status: issues_found
---

# Phase 13: Code Review Report

**Reviewed:** 2026-04-14T00:00:00Z
**Depth:** standard
**Files Reviewed:** 9
**Status:** issues_found

## Summary

Phase 13 ships portfolio analytics across three tracks: a `pnl_usd` field in `get_position_summary()`, a footer aggregating unrealized P&L on the `/positions` embed, a `/stats` slash command backed by `get_trade_stats()`, and the T-13-02 security fix that sources `cost_basis` from the DB rather than the Discord interaction payload.

The security-critical T-13-02 fix is correctly implemented: `cost_basis` is fetched from `get_open_positions()` inside `SellApproveRejectView.approve` before writing the trade row, and every test confirms the round-trip. No critical bugs or injection vulnerabilities were found.

Three warnings are raised, all concerning correctness edge cases: a race condition in the sell approve handler, a truthy check on `current_price` that silently suppresses display for `$0.00`, and a missing `avg_loss_pct` negative-sign guard in `build_stats_embed`. Three informational items are also noted.

---

## Warnings

### WR-01: TOCTOU race in `SellApproveRejectView.approve` — position may close between cost_basis fetch and trade write

**File:** `discord_bot/bot.py:120-136`

**Issue:** `cost_basis` is fetched from `get_open_positions()` and then `close_position()` is called in a later, separate statement. Between those two calls (lines 120–136) another concurrent button press could close the same position, causing `cost_basis` to remain `None` on the second execution even though a position existed moments before. More critically, `close_position()` is not guarded — if the first click succeeds and closes the position, a duplicate click will write a second `side='sell'` trade with `cost_basis=None` and attempt to close an already-closed position (a no-op in SQL, but a spurious trade row).

The view calls `self.stop()` at line 143 which disables the view for the in-memory object, but Discord reconstructed views (on bot restart with `timeout=None`) will not carry that state.

**Fix:** Add an idempotency guard using `has_open_position` before proceeding:

```python
# At the top of approve(), before placing the order:
if not queries.has_open_position(self.config.db_path, self.ticker):
    await interaction.response.send_message(
        f"Position for {self.ticker} is already closed.", ephemeral=True
    )
    return
```

This is a narrow window but matters for a financial system where duplicate sell trades are a real risk.

---

### WR-02: Falsy check on `current_price` suppresses valid `$0.00` display

**File:** `discord_bot/embeds.py:128`

**Issue:** The expression `if s['current_price']` is a truthy check. Any position whose live price resolves to exactly `0.0` (e.g., a halted or delisted stock that yfinance returns as `0.0` rather than raising) will display "N/A" in the embed instead of `$0.00`, silently hiding the data. The companion `pnl_pct` line at line 127 correctly uses `is not None`.

```python
price_str = f"${s['current_price']:.2f}" if s['current_price'] else "N/A"  # bug: 0.0 → "N/A"
```

**Fix:**

```python
price_str = f"${s['current_price']:.2f}" if s['current_price'] is not None else "N/A"
```

---

### WR-03: `avg_loss_pct` can be positive in `build_stats_embed` display

**File:** `discord_bot/embeds.py:179`

**Issue:** `get_trade_stats()` computes `avg_loss_pct` as a raw percentage, e.g. `-0.021` for a 2.1% loss. `build_stats_embed` formats it as `f"{loss_pct:.1%}"` which will render `-2.1%` only when the value is actually negative. However, the classification in `get_trade_stats` (`queries.py:105`) identifies a loss as `row["price"] < row["cost_basis"]`, which means `pct` will always be negative for losses — so the display is not wrong per se. The real concern is the absence of a sign assertion: if a caller ever passes a positive `avg_loss_pct` by accident (e.g., in a test or future refactor), the embed will render a misleading positive "Avg Loss" value with no `+` indicator and no assertion to catch it.

More concretely, `test_build_stats_embed_avg_gain_prefix` (test_embeds.py:152) verifies the `+` prefix on gain but there is no equivalent test asserting that the loss value is negative/starts with `-`. The omission is a test gap that could hide a regression.

**Fix:** Add a `"-"` prefix guard in the embed builder:

```python
embed.add_field(
    name="Avg Loss",
    value=f"{loss_pct:.1%}" if loss_pct is not None else "N/A",  # loss_pct is always <= 0
    inline=True,
)
```

And add a complementary test:

```python
def test_build_stats_embed_avg_loss_negative():
    embed = build_stats_embed(_make_stats(avg_loss_pct=-0.021))
    val = _field_value(embed, "Avg Loss")
    assert val.startswith("-"), f"Expected negative loss, got: {val}"
```

---

## Info

### IN-01: `get_position_summary` docstring does not mention `pnl_usd`

**File:** `screener/positions.py:10-11`

**Issue:** The module docstring on line 10 says "Returns list of dicts with keys: ticker, shares, avg_cost_usd, current_price, pnl_pct." The newly added `pnl_usd` key is absent from the documented return shape, which will mislead future readers and downstream callers who rely on the docstring as a contract.

**Fix:** Update the docstring to:

```python
"""Fetch current price for each open position via yfinance and compute P&L%.

Returns list of dicts with keys: ticker, shares, avg_cost_usd, current_price,
pnl_pct, pnl_usd.
Falls back to last_price from DB if yfinance fails. P&L% and P&L USD are None
if no price available.
"""
```

---

### IN-02: `_stats_command` returns `None` rather than an empty-state embed when no closed trades exist

**File:** `discord_bot/bot.py:229-232`

**Issue:** When `get_trade_stats()` returns `None` (no closed trades with `cost_basis` set), `_stats_command` sends the plain text message "No closed trades yet — nothing to analyze." This is a reasonable fallback, but it is a string response rather than an embed, creating an inconsistent UX compared to `/positions` which always responds with an embed. It is also not tested — `test_embeds.py` and `test_positions.py` do not include a test for `_stats_command` receiving a `None` stats result.

**Fix:** Either add a test covering the `None` branch, or build a thin "no data" embed to keep the UX consistent.

---

### IN-03: `test_build_positions_embed_with_data` does not include `pnl_usd` key

**File:** `tests/test_positions.py:244-252`

**Issue:** The fixture summaries at lines 244–247 include `pnl_pct` but not `pnl_usd`. The `build_positions_embed` function reads `s.get("pnl_usd")` at line 143 of `embeds.py`, which safely handles the missing key via `.get()`. However, the test inadvertently exercises the "no footer" branch for a non-empty list, hiding the footer rendering path. If `build_positions_embed` footer logic were broken, this test would still pass.

This is not a bug in production code (`.get()` is correct), but the test gives weaker coverage than intended.

**Fix:** Add `"pnl_usd"` to both summary dicts in `test_build_positions_embed_with_data` so the footer is also exercised in that test.

---

_Reviewed: 2026-04-14T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
