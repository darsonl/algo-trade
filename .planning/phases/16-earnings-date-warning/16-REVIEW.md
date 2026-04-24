---
phase: 16-earnings-date-warning
reviewed: 2026-04-24T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - discord_bot/embeds.py
  - discord_bot/bot.py
  - analyst/claude_analyst.py
  - main.py
  - tests/test_discord_embeds.py
  - tests/test_analyst_claude.py
findings:
  critical: 0
  warning: 2
  info: 2
  total: 4
status: issues_found
---

# Phase 16: Code Review Report

**Reviewed:** 2026-04-24T00:00:00Z
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Phase 16 adds earnings-date warning support: an `earningsTimestamp` from yfinance is decoded in `main.py`, formatted with a `⚠️` prefix when within 7 days, and surfaced both in the Discord embed (`earnings_date_embed`) and the analyst prompt (`earnings_date_prompt`). The implementation is clean and the new parameter is correctly threaded through `build_recommendation_embed`, `send_recommendation`, and `build_prompt`/`analyze_ticker`. Two correctness warnings and two info-level items were found.

## Warnings

### WR-01: `earnings_date_embed = "N/A"` is passed as a non-None string, so the embed always renders a "Next Earnings" field even when no date is available

**File:** `main.py:147` and `main.py:154`

**Issue:** When `earningsTimestamp` is absent (`_ts is None`) or the date is in the past, `earnings_date_embed` is set to the string `"N/A"`. This non-`None` value is passed to `send_recommendation` and then to `build_recommendation_embed`, which checks `if earnings_date is not None` — so the "Next Earnings" field is always rendered, even for tickers with no upcoming earnings. The intended design (from the test `test_embed_earnings_date_shows_na_when_none`) is that omitting the kwarg produces **no** "Next Earnings" field.

**Fix:** Set `earnings_date_embed = None` (not `"N/A"`) when there is no upcoming earnings date, so the field is suppressed entirely. Only the cases where an actual future date exists should produce a non-`None` value.

```python
# main.py — lines 146-165
_ts = info.get("earningsTimestamp")
if _ts is None:
    earnings_date_embed = None        # was "N/A" — suppresses the embed field
    earnings_date_prompt = None
else:
    _earnings_dt = datetime.fromtimestamp(_ts, tz=timezone.utc).date()
    _today = date.today()
    if _earnings_dt < _today:
        earnings_date_embed = None    # was "N/A" — suppresses the embed field
        earnings_date_prompt = None
    else:
        _days_until = (_earnings_dt - _today).days
        _date_str = _earnings_dt.strftime("%b %d, %Y")
        if 0 <= _days_until < 7:
            earnings_date_embed = f"⚠️ {_date_str}"
            earnings_date_prompt = f"{_date_str} (in {_days_until} days — proximity risk)"
        else:
            earnings_date_embed = _date_str
            earnings_date_prompt = _date_str
```

---

### WR-02: Test `test_embed_earnings_date_shows_na_string` passes the literal string `"N/A"` as `earnings_date` and asserts the field appears — this masks WR-01 and locks in the wrong behavior

**File:** `tests/test_discord_embeds.py:215-227`

**Issue:** The test at line 215 passes `earnings_date="N/A"` and asserts `fields["Next Earnings"] == "N/A"`. This test passes today but it validates the broken behavior described in WR-01. If WR-01 is fixed (embed field suppressed when `earnings_date_embed` is `None`), `main.py` will never pass `"N/A"` to the embed, so this test scenario becomes unreachable in production. Keeping it creates a false guarantee and will make reviewers think "N/A" is a legitimate value to forward to the embed.

**Fix:** Remove `test_embed_earnings_date_shows_na_string` (or rename it to document that it tests a direct API edge case that never occurs via `main.py`) and add a test that verifies `main.py`-level behavior: when `earningsTimestamp` is absent, no "Next Earnings" field appears. Alternatively, if the embed must support `"N/A"` as a passthrough value, document the caller contract explicitly and leave it as-is — but then WR-01 must be resolved differently.

---

## Info

### IN-01: `_days_until` proximity window boundary — earnings exactly today (`_days_until == 0`) emits "in 0 days" in the prompt

**File:** `main.py:162`

**Issue:** When `_earnings_dt == _today`, `_days_until` is `0` and the prompt string becomes `"Dec 18, 2025 (in 0 days — proximity risk)"`. This is technically correct (earnings are today, which is the highest-risk scenario) but the phrasing is awkward. The embed receives `"⚠️ Dec 18, 2025"` which is fine, but the prompt wording could confuse the LLM.

**Fix:** Special-case `_days_until == 0` for clearer language:

```python
if _days_until == 0:
    earnings_date_prompt = f"{_date_str} (today — proximity risk)"
elif 0 < _days_until < 7:
    earnings_date_prompt = f"{_date_str} (in {_days_until} days — proximity risk)"
```

---

### IN-02: `test_embed_earnings_date_shows_na_when_none` has a misleading docstring

**File:** `tests/test_discord_embeds.py:201-202`

**Issue:** The test is named `test_embed_earnings_date_shows_na_when_none` but its docstring says "has NO 'Next Earnings' field". The test actually asserts `"Next Earnings" not in fields`, which is the correct behavior — the name says "shows N/A" but the implementation checks for absence. The mismatch will confuse future readers about the contract.

**Fix:** Rename the test to `test_embed_earnings_date_field_absent_when_none` to match what it actually asserts.

---

_Reviewed: 2026-04-24T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
