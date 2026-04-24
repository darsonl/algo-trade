---
phase: 16
plan: 01
subsystem: analyst-embed-pipeline
tags: [earnings, embed, prompt-enrichment, SIG-05, SIG-06]
dependency_graph:
  requires: []
  provides: [earnings_date_embed_field, earnings_date_prompt_injection]
  affects: [discord_bot/embeds.py, discord_bot/bot.py, analyst/claude_analyst.py, main.py]
tech_stack:
  added: []
  patterns: [optional-kwarg-threading, inline-computation-from-info-dict, pre-formatted-string-caller-pattern]
key_files:
  created: []
  modified:
    - discord_bot/embeds.py
    - discord_bot/bot.py
    - analyst/claude_analyst.py
    - main.py
    - tests/test_discord_embeds.py
    - tests/test_analyst_claude.py
decisions:
  - embed-builder-receives-pre-formatted-string: build_recommendation_embed renders earnings_date value as-is; all formatting logic (N/A, date string, warning prefix, proximity note) lives in main.py buy-scan loop only
  - past-timestamps-suppressed-to-na-in-embed-but-omitted-from-prompt: embed always shows a field value; Claude prompt omits line entirely when date absent or past (cleaner signal)
  - etf-and-sell-paths-excluded: ETF builders and sell prompt not modified; enforced by scope guard and verified by grep
metrics:
  duration_seconds: 298
  completed_date: "2026-04-24"
  tasks_completed: 3
  files_modified: 6
  tests_added: 11
---

# Phase 16 Plan 01: Earnings Date Warning Summary

**One-liner:** Earnings date from `info["earningsTimestamp"]` surfaced in BUY embed ("Next Earnings" inline field) and Claude BUY prompt (Market Context line) with 7-day proximity warning prefix, zero extra HTTP cost.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Add failing tests for earnings_date embed field (SIG-05) | c97d6d6 | tests/test_discord_embeds.py |
| 1 (GREEN) | Add earnings_date field to build_recommendation_embed and send_recommendation | 6962a92 | discord_bot/embeds.py, discord_bot/bot.py |
| 2 (RED) | Add failing tests for earnings_date prompt injection (SIG-06) | ac3ba8a | tests/test_analyst_claude.py |
| 2 (GREEN) | Thread earnings_date through build_prompt and analyze_ticker | 187ab3f | analyst/claude_analyst.py |
| 3 | Wire earningsTimestamp extraction and earnings_date into buy-scan flow | d9f7c15 | main.py |

## Implementation Summary

### Task 1 — SIG-05: Embed field

- Added `earnings_date: str | None = None` kwarg as last parameter to `build_recommendation_embed` in `discord_bot/embeds.py`
- When `earnings_date is not None`, adds `embed.add_field(name="Next Earnings", value=earnings_date, inline=True)` as the last field after Confidence
- Added same kwarg to `send_recommendation` in `discord_bot/bot.py` and forwarded to `build_recommendation_embed`
- Embed builder is a pure renderer — no formatting logic; caller provides pre-formatted string

### Task 2 — SIG-06: Claude prompt injection

- Added `earnings_date: str | None = None` kwarg to both `build_prompt` and `analyze_ticker` in `analyst/claude_analyst.py`
- In `build_prompt`, appends `f"- Next Earnings: {earnings_date}"` as last line in `market_lines` (after 52-week range) when non-None
- `analyze_ticker` forwards `earnings_date=earnings_date` to `build_prompt`

### Task 3 — main.py wiring

- Updated `from datetime import date` to `from datetime import date, datetime, timezone`
- Inserted `earningsTimestamp` extraction block after `fundamental_trend` assembly, before headlines fetch
- Logic: None/missing → `earnings_date_embed="N/A"`, `earnings_date_prompt=None`; past timestamp → same; future within 7 days → `earnings_date_embed="⚠️ {date_str}"`, `earnings_date_prompt="{date_str} (in N days — proximity risk)"`; future ≥ 7 days → both set to plain date string
- Wired `earnings_date=earnings_date_prompt` into `analyze_ticker` call
- Wired `earnings_date=earnings_date_embed` into `bot.send_recommendation` call
- ETF scan path (`run_scan_etf`, `analyze_etf_ticker`, `build_etf_recommendation_embed`) untouched

## Tests Added

**tests/test_discord_embeds.py** (+6 tests, 18 → 24 total):
- `test_embed_earnings_date_shows_future_date` — future date string rendered as field value
- `test_embed_earnings_date_shows_na_when_none` — no kwarg → no "Next Earnings" field (backward compat)
- `test_embed_earnings_date_shows_na_string` — explicit "N/A" string rendered correctly
- `test_embed_earnings_date_warning_prefix` — warning emoji prefix passed through as-is
- `test_embed_earnings_date_field_is_inline` — field has `inline=True`
- `test_embed_earnings_date_is_last_field_after_confidence` — field ordering: Confidence then Next Earnings

**tests/test_analyst_claude.py** (+5 tests, 49 → 54 total):
- `test_build_prompt_with_earnings_date_includes_market_context_line` — "- Next Earnings: Dec 15, 2025" in prompt
- `test_build_prompt_with_earnings_date_none_omits_line` — no kwarg → no "Next Earnings" line
- `test_build_prompt_with_earnings_date_proximity_note` — full proximity note string in prompt
- `test_build_prompt_earnings_date_in_market_context_block` — "Next Earnings" appears after "Market Context:"
- `test_analyze_ticker_forwards_earnings_date_to_build_prompt` — spy confirms earnings_date reaches prompt

## Decision Traceability

| Decision | Implementation |
|----------|----------------|
| D-01: Display format `Dec 15, 2025` | `_date_str = _earnings_dt.strftime("%b %d, %Y")` in main.py |
| D-02: Past timestamps → "N/A" | `if _earnings_dt < _today: earnings_date_embed = "N/A"` |
| D-03: Absent timestamp → "N/A" | `if _ts is None: earnings_date_embed = "N/A"` |
| D-04: "Next Earnings" last field | Added after `if confidence is not None` block in embeds.py |
| D-05: Field is inline | `embed.add_field(..., inline=True)` |
| D-06: ⚠️ prefix within 7 days | `earnings_date_embed = f"⚠️ {_date_str}"` when `0 <= _days_until < 7` |
| D-07: No warning at exactly 7 days | Condition is strictly `< 7` |
| D-08: Prompt line in Market Context, omit when absent/past | `market_lines.append(f"- Next Earnings: {earnings_date}")` guarded by `if earnings_date is not None`; `earnings_date_prompt=None` for absent/past cases |
| D-09: Inline extraction, zero extra HTTP | `_ts = info.get("earningsTimestamp")` — same info dict already fetched |
| D-10: Optional kwarg pattern | `earnings_date: str | None = None` default preserves all existing callers |
| D-11: ETF exclusion | ETF builders and `run_scan_etf` not modified; grep confirms 0 ETF/sell references |

## Deviations from Plan

None — plan executed exactly as written.

## Threat Surface Scan

No new network endpoints, auth paths, or file access patterns introduced. `earningsTimestamp` crosses the yfinance → bot boundary (T-16-01 through T-16-05 in plan threat model). All mitigations verified:

- T-16-01: `info.get("earningsTimestamp")` None-guard + per-ticker try/except in main.py covers non-integer/missing
- T-16-02: Explicit past-date guard renders "N/A" for expired timestamps
- T-16-04: `_date_str` derived from `strftime` only; no user input reaches prompt

## Self-Check: PASSED

All modified files exist. All 5 task commits verified in git log:
- c97d6d6 test(16-01): RED tests for earnings_date embed field
- 6962a92 feat(16-01): earnings_date field in build_recommendation_embed + send_recommendation
- ac3ba8a test(16-01): RED tests for earnings_date prompt injection
- 187ab3f feat(16-01): thread earnings_date through build_prompt and analyze_ticker
- d9f7c15 feat(16-01): wire earningsTimestamp extraction into buy-scan flow

Full test suite: 418 passed, 0 failed.
