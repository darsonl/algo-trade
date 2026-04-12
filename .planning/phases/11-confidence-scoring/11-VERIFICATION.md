---
phase: 11-confidence-scoring
verified: 2026-04-12T15:00:00Z
status: passed
score: 9/9 must-haves verified
re_verification: false
---

# Phase 11: Confidence Scoring Verification Report

**Phase Goal:** Add confidence scoring to the analyst pipeline — LLM responses include a `high|medium|low` confidence field that is parsed, stored in DB, and displayed as a badge in Discord embeds across all three scan paths (buy, sell, ETF).
**Verified:** 2026-04-12T15:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `_VALID_CONFIDENCE = {"high", "medium", "low"}` exists in analyst module | VERIFIED | `analyst/claude_analyst.py` line 54 |
| 2 | All three prompt builders request `CONFIDENCE: <high\|medium\|low>` | VERIFIED | Lines 134, 195, 413 of `claude_analyst.py` — final format line in each prompt |
| 3 | `parse_claude_response` returns dict with `"confidence"` key | VERIFIED | Lines 228-234: allowlist-validated extraction, returns `None` gracefully for absent/invalid values |
| 4 | DB `recommendations` and `analyst_cache` tables have nullable `confidence TEXT` columns with idempotent ALTER TABLE migrations | VERIFIED | `database/models.py` lines 30, 51 (CREATE TABLE), lines 105-113 (two ALTER TABLE blocks) |
| 5 | `create_recommendation` has `confidence: str \| None = None` param and includes it in INSERT | VERIFIED | `database/queries.py` lines 15, 22-23 |
| 6 | All three embed builders accept `confidence` param and add inline Confidence field when not None | VERIFIED | `discord_bot/embeds.py` lines 18, 40-41 (stock); 53, 84-85 (ETF); 97, 110-111 (sell) |
| 7 | All three bot send methods accept and forward `confidence` to embed builders | VERIFIED | `discord_bot/bot.py` lines 217, 221 (send_recommendation); 236, 240 (send_sell_recommendation); 255, 259 (send_etf_recommendation) |
| 8 | `main.py` wires `analysis.get("confidence")` at all 8 call sites across buy/sell/ETF scan paths | VERIFIED | Lines 142, 163, 174 (buy path: cache write + create_rec + send); 287, 299 (sell path: create_rec + send); 401, 419, 431 (ETF path: cache write + create_rec + send) |
| 9 | Full test suite passes with 330+ tests | VERIFIED | 331 passed, 0 failed — 29 confidence-specific tests confirmed |

**Score:** 9/9 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `analyst/claude_analyst.py` | `_VALID_CONFIDENCE` allowlist, updated parser, updated prompt builders | VERIFIED | All three prompt builders end with `CONFIDENCE: <high\|medium\|low>` line; parser uses boundary-safe `confidence_idx` as `end_idx` |
| `database/models.py` | `confidence TEXT` in CREATE TABLE for both tables + idempotent ALTER TABLE migrations | VERIFIED | Both tables include `confidence TEXT` in CREATE TABLE; two try/except ALTER TABLE blocks at lines 105-113 |
| `database/queries.py` | `create_recommendation`, `set_cached_analysis`, `get_cached_analysis` handle confidence | VERIFIED | All three functions updated; `get_cached_analysis` always returns `confidence` key (value may be None for pre-migration rows) |
| `discord_bot/embeds.py` | All three embed builders: `confidence: str \| None = None` param + guarded `add_field` | VERIFIED | `build_recommendation_embed`, `build_etf_recommendation_embed`, `build_sell_embed` all confirmed |
| `discord_bot/bot.py` | All three send methods: accept and forward `confidence` | VERIFIED | `send_recommendation`, `send_sell_recommendation`, `send_etf_recommendation` all confirmed |
| `main.py` | 8 call sites use `analysis.get("confidence")` | VERIFIED | Exact count: 3 buy path + 2 sell path + 3 ETF path = 8 |
| `tests/test_analyst_claude.py` | Confidence parsing tests (high/medium/low/missing/invalid/case-insensitive/no-leak) | VERIFIED | 9 confidence tests pass |
| `tests/test_embeds.py` | Embed confidence badge tests for all three embed types | VERIFIED | 11 tests pass (confidence present/absent/None for all three builders) |
| `tests/test_database.py` | DB confidence round-trip + idempotent migration tests | VERIFIED | 5 tests pass |
| `tests/test_main.py` | Wiring tests: confidence flows to create_recommendation + send methods | VERIFIED | 3 wiring tests pass |
| `tests/test_sell_prompt.py` | `test_build_sell_prompt_includes_confidence_format` | VERIFIED | 1 test passes |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `parse_claude_response` | `analyze_ticker` / `analyze_etf_ticker` / `analyze_sell_ticker` return dict | `result = parse_claude_response(text)` | WIRED | All three analyze_* functions call `parse_claude_response`; confidence propagates automatically via dict passthrough |
| `analyze_ticker` result | `set_cached_analysis` | `analysis.get("confidence")` | WIRED | `main.py` line 142 |
| `analyze_ticker` result | `create_recommendation` | `analysis.get("confidence")` | WIRED | `main.py` line 163 |
| `analyze_ticker` result | `bot.send_recommendation` | `analysis.get("confidence")` | WIRED | `main.py` line 174 |
| `analyze_sell_ticker` result | `create_recommendation` | `analysis.get("confidence")` | WIRED | `main.py` line 287 |
| `analyze_sell_ticker` result | `bot.send_sell_recommendation` | `analysis.get("confidence")` | WIRED | `main.py` line 299 |
| `analyze_etf_ticker` result | `set_cached_analysis` | `analysis.get("confidence")` | WIRED | `main.py` line 401 |
| `analyze_etf_ticker` result | `create_recommendation` | `analysis.get("confidence")` | WIRED | `main.py` line 419 |
| `analyze_etf_ticker` result | `bot.send_etf_recommendation` | `analysis.get("confidence")` | WIRED | `main.py` line 431 |
| `bot.send_recommendation` | `build_recommendation_embed` | `confidence=confidence` kwarg | WIRED | `bot.py` line 221 |
| `bot.send_sell_recommendation` | `build_sell_embed` | `confidence=confidence` kwarg | WIRED | `bot.py` line 240 |
| `bot.send_etf_recommendation` | `build_etf_recommendation_embed` | `confidence=confidence` kwarg | WIRED | `bot.py` line 259 |
| `get_cached_analysis` return dict | cache-hit code path in `main.py` | `analysis.get("confidence")` safe access | WIRED | Cache hits produce same confidence badge behavior as live API calls |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `build_recommendation_embed` | `confidence` | `analysis` dict from `parse_claude_response` or `get_cached_analysis` | Yes — parsed from LLM response text or fetched from DB | FLOWING |
| `build_sell_embed` | `confidence` | `analyze_sell_ticker` result dict | Yes — same pipeline | FLOWING |
| `build_etf_recommendation_embed` | `confidence` | `analyze_etf_ticker` result dict | Yes — same pipeline | FLOWING |

All three embed builders guard with `if confidence is not None` — no empty badge rendered when LLM omits the field. `confidence.capitalize()` converts lowercase DB value to title-case at render time.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `parse_claude_response` returns `confidence` key | `pytest -k "confidence" -q` | 29 passed | PASS |
| All three prompt builders include CONFIDENCE line | grep in `claude_analyst.py` | Found at lines 134, 195, 413 | PASS |
| Full suite: 331 tests, zero failures | `pytest -x -q` | 331 passed, 1 warning in ~20s | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SIG-04 | 11-01-PLAN.md, 11-02-PLAN.md | Confidence scoring: LLM returns high/medium/low, stored in DB, displayed in embeds | SATISFIED | All acceptance criteria met end-to-end |

### Anti-Patterns Found

No blockers or stubs detected.

- Boundary-safe reasoning extraction uses `confidence_idx` as `end_idx` — prevents the word "high"/"medium"/"low" from leaking into reasoning text.
- `if confidence is not None` guard in all three embed builders — no "N/A" or empty badge displayed when LLM omits the field.
- `analysis.get("confidence")` (not `analysis["confidence"]`) used consistently in `main.py` — safe for any code path that returns a result dict without the key.
- SQL uses parameterized queries for `confidence` column — no injection risk.

### Human Verification Required

None. All acceptance criteria are verifiable programmatically. The confidence badge rendering in Discord (visual appearance in the actual Discord client) could be spot-checked by a human, but the embed construction logic is fully covered by the 11 embed tests.

### Gaps Summary

No gaps. All 9 observable truths verified. Phase goal achieved.

The confidence scoring feature is wired end-to-end:
1. Prompts instruct the LLM to output `CONFIDENCE: <high|medium|low>` as the third response line.
2. `parse_claude_response` extracts and validates the value via `_VALID_CONFIDENCE` allowlist, returns `None` gracefully for absent/invalid values.
3. Both DB tables (`recommendations` and `analyst_cache`) have nullable `confidence TEXT` columns with idempotent migrations for existing databases.
4. All three scan paths (buy, sell, ETF) in `main.py` wire `analysis.get("confidence")` to the cache write, `create_recommendation`, and Discord send calls.
5. All three Discord embed builders display an inline Confidence badge with `confidence.capitalize()` when the value is not None.
6. 331 tests pass with 29 new confidence-specific tests covering parsing, DB round-trips, embed rendering, and end-to-end wiring.

---

_Verified: 2026-04-12T15:00:00Z_
_Verifier: Claude (gsd-verifier)_
