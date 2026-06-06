---
phase: 18-test-coverage-gaps
plan: "02"
subsystem: tests
tags: [testing, config, validate, use_limit_buy, env-mapping]
dependency_graph:
  requires: []
  provides: [tests/test_config.py]
  affects: [config.Config.validate, config.use_limit_buy]
tech_stack:
  added: []
  patterns: [importlib.reload, monkeypatch.setattr(dotenv), explicit-kwarg Config mutation]
key_files:
  created:
    - tests/test_config.py
  modified: []
decisions:
  - "validate() tested via explicit-kwarg Config mutation (no reload) — hermetic regardless of machine .env"
  - "USE_LIMIT_BUY mapping tested via importlib.reload with dotenv source-patched noop — survives reload rebind of load_dotenv"
  - "autouse fixture reloads config module after each mapping test — prevents state leak to other test files"
  - "Mutation check performed inline (not committed) — confirmed test is non-vacuous"
metrics:
  duration_minutes: 3
  completed_date: "2026-06-06"
  tasks_completed: 2
  files_created: 1
  files_modified: 0
requirements: [TEST-10]
---

# Phase 18 Plan 02: Config Validation and USE_LIMIT_BUY Mapping Tests Summary

**One-liner:** 16-test hermetic suite covering Config.validate() both ANALYST_PROVIDER branches and USE_LIMIT_BUY env-default mapping via importlib.reload with dotenv source-patched noop.

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Config.validate() suite (both ANALYST_PROVIDER branches + happy path) | aabfa5e | tests/test_config.py (created) |
| 2 | USE_LIMIT_BUY env-mapping suite (unset/false/true via reload) | aabfa5e | tests/test_config.py (appended) |

Note: Tasks 1 and 2 both write to the same new file; committed atomically together.

## What Was Built

### Task 1 — Config.validate() suite (12 tests)

Helper `_valid_config(provider="claude")` constructs a fully-valid Config using explicit attribute mutation — no module reload, no dependence on machine .env. Each missing-field test blanks exactly one field so the assertion fires for the targeted field, not an earlier check.

Tests:
1. `test_validate_passes_with_fully_valid_config` — happy path (claude)
2. `test_validate_passes_with_valid_gemini_config` — happy path (gemini)
3. `test_validate_raises_when_schwab_app_key_missing` — match="SCHWAB_APP_KEY"
4. `test_validate_raises_when_schwab_app_secret_missing` — match="SCHWAB_APP_SECRET"
5. `test_validate_raises_when_discord_token_missing` — match="DISCORD_TOKEN"
6. `test_validate_raises_when_discord_channel_id_missing` — discord_channel_id=0; match="DISCORD_CHANNEL_ID"
7. `test_validate_raises_when_schwab_account_hash_missing` — match="SCHWAB_ACCOUNT_HASH"
8. `test_validate_raises_when_claude_keys_both_missing` — both keys blank; match="ANTHROPIC_API_KEY"
9. `test_validate_claude_passes_with_anthropic_key_only` — anthropic_api_key alone satisfies claude branch
10. `test_validate_claude_passes_with_analyst_key_only` — analyst_api_key alone satisfies claude branch
11. `test_validate_raises_when_other_provider_api_key_missing` — gemini, analyst_api_key blank; match="ANALYST_API_KEY"
12. `test_validate_other_provider_passes_with_api_key` — gemini, analyst_api_key set; passes

### Task 2 — USE_LIMIT_BUY env-mapping suite (4 tests)

Helper `_reload_with_env(monkeypatch, value)` patches `dotenv.load_dotenv` (the SOURCE module, not `config.load_dotenv`) to a noop before calling `importlib.reload(config_module)`. This is required because `importlib.reload` re-executes config.py's body including line 4 `from dotenv import load_dotenv`, which would rebind `config.load_dotenv` back to the real function before line 6's call fires — destroying any noop patched on `config.load_dotenv`. Patching the SOURCE means the name line 4 imports IS already the noop.

An `autouse` fixture reloads `config_module` after each test, restoring field defaults from the (monkeypatch-restored) environment — preventing state leak to test_main.py and other files.

Tests:
1. `test_use_limit_buy_unset_defaults_false` — USE_LIMIT_BUY unset → False (regression guard for 2026-06-06 default)
2. `test_use_limit_buy_false_maps_false` — "false" → False
3. `test_use_limit_buy_true_maps_true` — "true" → True
4. `test_use_limit_buy_uppercase_true_maps_true` — "TRUE" → True (case-insensitivity guard)

## Mutation Check (Non-Vacuity Proof)

**Purpose:** Prove `test_use_limit_buy_unset_defaults_false` is non-vacuous — it actually reads the field default, not a stale env value.

**Procedure:**
1. Temporarily edited `config.py` line 38 default from `"false"` to `"true"` (not committed).
2. Ran `pytest tests/test_config.py -q -k test_use_limit_buy_unset_defaults_false`.
3. Result: **RED** — `assert True is False` (E assert True is False — where True = _reload_with_env(..., None)).
4. Reverted `config.py` line 38 back to `"false"`.
5. Confirmed `git diff --quiet -- config.py` exits 0 (CONFIG_CLEAN).
6. Ran full suite — **GREEN** — 16 passed.

The mutation turned RED because the source-patch approach makes the unset case read the (mutated) field default rather than anything from the on-disk .env. This confirms the test correctly exercises the env-default mapping, not attribute assignment.

## Verification Results

| Check | Result |
|-------|--------|
| `pytest tests/test_config.py -q -k validate` | 12 passed |
| `pytest tests/test_config.py -q -k use_limit_buy` | 4 passed |
| `pytest tests/test_config.py -q` (full suite) | 16 passed |
| Hermetic check: `USE_LIMIT_BUY=true SCHWAB_APP_KEY=x pytest -q -k validate` | 12 passed |
| `pytest tests/test_main.py -q` (isolation check) | 20 passed |
| `git diff --quiet -- config.py` | exit 0 (CLEAN) |
| Mutation check (RED then revert→GREEN) | Documented above |

## Deviations from Plan

None — plan executed exactly as written.

Task 1 and Task 2 were committed in a single atomic commit (aabfa5e) because both write to the same new file. The plan allowed this — the TDD RED phase is trivial for tests on already-working production code; the only true RED demonstrated was the Task-2 mutation check.

## Known Stubs

None.

## Threat Flags

None — test-only file, no new runtime code paths.

## Self-Check: PASSED

- `tests/test_config.py` exists: FOUND
- Commit aabfa5e exists: FOUND
- `git diff --quiet -- config.py` exits 0: CONFIRMED
- 16 tests pass: CONFIRMED
