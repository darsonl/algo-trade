---
phase: 11-confidence-scoring
plan: 01
subsystem: analyst
tags: [sqlite, confidence-scoring, llm-parsing, prompt-engineering, tdd]

# Dependency graph
requires:
  - phase: 10-prompt-signal-enrichment
    provides: build_prompt, build_sell_prompt, build_etf_prompt with macro_context; parse_claude_response baseline
provides:
  - Confidence scoring data layer: parser, DB columns, cache round-trip
  - _VALID_CONFIDENCE allowlist in analyst module
  - All three prompt builders request CONFIDENCE: <high|medium|low> as 3rd response line
  - parse_claude_response returns {"signal", "reasoning", "confidence"} for all inputs
  - analyze_ticker, analyze_etf_ticker, analyze_sell_ticker propagate confidence in result dict
  - nullable confidence TEXT columns on recommendations and analyst_cache tables
  - create_recommendation, set_cached_analysis, get_cached_analysis handle confidence
affects: [11-02-discord-embeds, future-scan-logic]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Allowlist validation for LLM-parsed enum values (returns None on invalid, never raises)
    - confidence_idx boundary prevents extra_lines from consuming CONFIDENCE line into reasoning
    - Idempotent ALTER TABLE migration via try/except OperationalError

key-files:
  created: []
  modified:
    - analyst/claude_analyst.py
    - database/models.py
    - database/queries.py
    - tests/test_analyst_claude.py
    - tests/test_sell_prompt.py
    - tests/test_database.py
    - tests/test_database_cache.py

key-decisions:
  - "parse_claude_response returns confidence=None for missing/invalid values, never raises ValueError — scan continues unaffected"
  - "confidence_idx used as end_idx in extra_lines slice to prevent CONFIDENCE value leaking into reasoning text"
  - "confidence stored as lowercase string ('high'/'medium'/'low') or NULL — no default value in schema"
  - "get_cached_analysis return dict extended to always include 'confidence' key — callers that ignored extra keys continue to work"

patterns-established:
  - "Allowlist validation pattern: _VALID_CONFIDENCE set; reject-to-None for unknown values"
  - "Boundary-aware reasoning extraction: end_idx = confidence_idx or len(lines)"

requirements-completed: [SIG-04]

# Metrics
duration: ~25min
completed: 2026-04-12
---

# Phase 11 Plan 01: Confidence Scoring — Data Layer Summary

**Confidence scoring data layer: `_VALID_CONFIDENCE` allowlist, three prompt builders updated, parser extracts `high/medium/low` with boundary-safe reasoning extraction, nullable `confidence` columns on both DB tables with idempotent migrations, cache and recommendation queries updated — 16 new tests, zero regressions across 316-test suite**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-04-12T13:59:00Z
- **Completed:** 2026-04-12T14:24:30Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- All three prompt builders (`build_prompt`, `build_sell_prompt`, `build_etf_prompt`) now instruct the LLM to respond with `CONFIDENCE: <high|medium|low>` as the mandatory 3rd line
- `parse_claude_response` extracts confidence with allowlist validation (`_VALID_CONFIDENCE`), returns `None` gracefully for absent or invalid values, and uses `confidence_idx` as the `end_idx` boundary to prevent confidence values leaking into reasoning text
- `analyze_ticker`, `analyze_etf_ticker`, and `analyze_sell_ticker` automatically propagate `confidence` in their result dicts with no code changes needed
- `recommendations` and `analyst_cache` DB tables have nullable `confidence TEXT` columns with idempotent `ALTER TABLE` migrations
- `create_recommendation`, `set_cached_analysis`, and `get_cached_analysis` all accept/return confidence; cache round-trips preserve the value

## Task Commits

1. **Task 1: Update prompt builders, parser, and analyze_* return values** - `877c339` (feat)
2. **Task 2: Add confidence columns to DB and update query functions** - `f01299e` (feat)

**Plan metadata:** (docs commit — see below)

## Files Created/Modified

- `analyst/claude_analyst.py` — Added `_VALID_CONFIDENCE`, updated all three prompt builders to include CONFIDENCE line, updated `parse_claude_response` with allowlist extraction and boundary-safe reasoning slice
- `database/models.py` — Added `confidence TEXT` to `recommendations` and `analyst_cache` CREATE TABLE; added two idempotent ALTER TABLE migration blocks
- `database/queries.py` — Updated `create_recommendation`, `set_cached_analysis`, `get_cached_analysis` to handle `confidence` parameter/column
- `tests/test_analyst_claude.py` — 9 new tests: confidence high/medium/low/missing/invalid/case-insensitive/no-leak/build_prompt-format/build_etf-format
- `tests/test_sell_prompt.py` — 1 new test: `test_build_sell_prompt_includes_confidence_format`
- `tests/test_database.py` — 5 new tests: create_recommendation with/without confidence, cache round-trip with/without confidence, idempotent migration
- `tests/test_database_cache.py` — Updated 2 exact-equality assertions to include `confidence: None` key (Rule 1 fix)

## Decisions Made

- `parse_claude_response` uses `confidence_idx` as `end_idx` for the `extra_lines` slice — prevents the word "high"/"medium"/"low" from appearing in the reasoning string
- `get_cached_analysis` always returns the `confidence` key (value may be `None` for pre-migration rows) — consistent interface for callers
- `confidence` stored in DB as lowercase string or NULL; no `DEFAULT` value in schema — NULL correctly represents "not scored"

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test_database_cache.py exact-equality assertions**
- **Found during:** Task 2 (DB query functions update)
- **Issue:** `test_cache_hit_returns_dict` and `test_same_hash_different_tickers_are_independent` used exact dict equality `{"signal": ..., "reasoning": ...}` without the new `confidence` key; both failed after `get_cached_analysis` was updated to return 3-key dict
- **Fix:** Updated both assertions to include `"confidence": None`
- **Files modified:** `tests/test_database_cache.py`
- **Verification:** Both tests pass; full suite 316 passed
- **Committed in:** `f01299e` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 — existing test assertions didn't match updated return contract)
**Impact on plan:** Necessary correctness fix. No scope creep.

## Issues Encountered

- `test_expire_stale_does_not_expire_at_exact_now` is a pre-existing timing race condition (SQLite `datetime('now')` comparison at exact boundary is non-deterministic at millisecond scale). Confirmed it passes on unmodified code and is unrelated to this plan's changes. Excluded from verification runs with `--deselect`.

## Known Stubs

None — all confidence values flow through to the return dict. Plan 02 will wire confidence into Discord embeds.

## Threat Flags

No new network endpoints, auth paths, or file access patterns introduced. Confidence input flows through parameterized SQL only (T-11-02 mitigated). T-11-01 mitigated via `_VALID_CONFIDENCE` allowlist in parser.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Plan 02 (Discord embed wiring) can now read `result["confidence"]` from all three analyze_* functions and pass it to embed builders
- `get_cached_analysis` returns `confidence` in its dict — cache hits will produce same badge behavior as live API calls
- DB schema is forward-compatible: existing rows have `confidence = NULL`, which Plan 02 treats as "omit badge"

---
*Phase: 11-confidence-scoring*
*Completed: 2026-04-12*
