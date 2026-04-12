---
phase: 11-confidence-scoring
plan: 02
subsystem: discord-embeds
tags: [discord, confidence-scoring, embeds, tdd]

# Dependency graph
requires:
  - phase: 11-confidence-scoring
    plan: 01
    provides: analyze_ticker/analyze_sell_ticker/analyze_etf_ticker return confidence in dict; create_recommendation/set_cached_analysis/get_cached_analysis handle confidence
provides:
  - All three embed builders accept optional confidence param and display Confidence badge when present
  - All three bot send methods accept and forward confidence
  - main.py wires confidence end-to-end: LLM response -> cache write -> create_recommendation -> embed display
affects: [discord-embeds, discord-bot, scan-pipelines]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Optional confidence badge pattern: guarded by `if confidence is not None` — no N/A placeholder
    - confidence.capitalize() — lowercase DB value rendered as title-case in Discord
    - analysis.get("confidence") safe access — None propagates safely when key absent (older paths)

key-files:
  created:
    - tests/test_embeds.py
  modified:
    - discord_bot/embeds.py
    - discord_bot/bot.py
    - main.py
    - tests/test_main.py
    - tests/test_discord_bot.py

key-decisions:
  - "confidence badge uses embed.add_field with inline=True — consistent with other metadata fields (Price, RSI, etc.)"
  - "confidence.capitalize() converts stored lowercase ('high') to display title-case ('High') at the embed layer"
  - "analysis.get('confidence') is the canonical safe access pattern — never analysis['confidence'] to avoid KeyError on older code paths"

requirements-completed: [SIG-04]

# Metrics
duration: ~10min
completed: 2026-04-12
---

# Phase 11 Plan 02: Confidence Scoring — Discord Embed Wiring Summary

**Confidence badge wired end-to-end: all three embed builders display optional Confidence field, bot send methods forward confidence, main.py passes `analysis.get("confidence")` to cache writes, DB creation, and Discord sends — 14 new tests (11 embed + 3 wiring), 331 total, zero regressions**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-04-12T14:20:00Z
- **Completed:** 2026-04-12T14:31:39Z
- **Tasks:** 2
- **Files modified:** 5 (+ 1 created)

## Accomplishments

- `build_recommendation_embed`, `build_sell_embed`, `build_etf_recommendation_embed` each accept `confidence: str | None = None`; when not None they add an inline "Confidence" field with `confidence.capitalize()` as value
- `send_recommendation`, `send_sell_recommendation`, `send_etf_recommendation` each accept and forward `confidence` to the embed builder
- `run_scan` buy pass passes `confidence=analysis.get("confidence")` to `set_cached_analysis`, `create_recommendation`, and `bot.send_recommendation`
- `run_scan` sell pass passes `confidence=analysis.get("confidence")` to `create_recommendation` and `bot.send_sell_recommendation`
- `run_scan_etf` passes `confidence=analysis.get("confidence")` to `set_cached_analysis`, `create_recommendation`, and `bot.send_etf_recommendation`
- Cache hits propagate confidence identically to live API calls (cache stores and returns confidence; `get_cached_analysis` already returns it per Plan 01)

## Task Commits

1. **Task 1: Add confidence parameter to embed builders and bot send methods** — `9f191ea` (feat)
2. **Task 2: Wire confidence through main.py scan pipelines and add wiring tests** — `a7376f1` (feat)

## Files Created/Modified

- `discord_bot/embeds.py` — Added `confidence: str | None = None` to all three embed builders; added guarded `embed.add_field(name="Confidence", value=confidence.capitalize(), inline=True)` after last existing field in each builder
- `discord_bot/bot.py` — Added `confidence: str | None = None` to `send_recommendation`, `send_sell_recommendation`, `send_etf_recommendation`; forwarded to embed builder calls
- `main.py` — Added `confidence=analysis.get("confidence")` to 8 call sites across run_scan buy pass (3), run_scan sell pass (2), run_scan_etf (3)
- `tests/test_embeds.py` — Created with 11 tests covering confidence present/absent/None for all three embed types
- `tests/test_main.py` — Added 3 tests: confidence flows to create_recommendation+send_recommendation (stock), None confidence safe propagation, ETF confidence flows end-to-end
- `tests/test_discord_bot.py` — Updated exact call assertion to include `confidence=None` (Rule 1 auto-fix)

## Decisions Made

- Confidence badge uses `embed.add_field` with `inline=True` — matches the display style of Price, RSI, and other metadata fields already in the embed
- `confidence.capitalize()` converts lowercase DB values to title-case at render time — DB stores "high", Discord shows "High"
- `analysis.get("confidence")` is the safe access pattern throughout main.py — gracefully handles analyze_* results that lack the key

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_discord_bot.py exact call assertion to include confidence=None**
- **Found during:** Task 2 full suite run
- **Issue:** `test_send_etf_recommendation_posts_embed_and_returns_message_id` used `mock_build_embed.assert_called_once_with("SPY", "BUY", ..., 0.0009)` without `confidence=None`; failed after `send_etf_recommendation` now passes `confidence=confidence` (which defaults to None) to the embed builder
- **Fix:** Updated assertion to `assert_called_once_with(..., 0.0009, confidence=None)`
- **Files modified:** `tests/test_discord_bot.py`
- **Commit:** `a7376f1` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 — existing test assertion didn't match updated call contract)
**Impact on plan:** Necessary correctness fix. No scope creep.

## Known Stubs

None — confidence flows through all three scan paths to embed display. No placeholder values.

## Threat Flags

No new network endpoints, auth paths, or file access patterns introduced. Confidence input flows via `analysis.get("confidence")` with None-safe propagation; passes through parameterized SQL (T-11-05 mitigated per plan).

## Self-Check: PASSED

- `discord_bot/embeds.py` exists and contains `confidence: str | None = None` (3 times)
- `discord_bot/bot.py` exists and contains `confidence: str | None = None` (3 times)
- `main.py` contains `confidence=analysis.get("confidence")` (8 call sites)
- `tests/test_embeds.py` exists with 11 tests
- Commits `9f191ea` and `a7376f1` present in git log
- Full suite: 331 passed, 0 failed
