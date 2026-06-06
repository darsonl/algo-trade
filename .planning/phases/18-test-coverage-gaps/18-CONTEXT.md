# Phase 18: Test Coverage Gaps - Context

**Gathered:** 2026-06-06
**Status:** Ready for planning

<domain>
## Phase Boundary

Add automated tests for three untested execution paths: analyst fallback logic, `Config.validate()`, and quota exhaustion. Requirements TEST-09, TEST-10, TEST-11.

This phase writes tests only — it does not change runtime behavior. The one exception is documentation: the phase spec itself (ROADMAP SC#2/#5, REQUIREMENTS TEST-09/11) was corrected in commit `99f4a1f` before this discussion to match behavior that drifted after the roadmap was written.

Scope excludes: new features, refactoring production code, integration tests beyond the three named paths.

</domain>

<decisions>
## Implementation Decisions

### Parse-error fallback (TEST-09)
- **D-01:** Tests assert that parse errors (`ValueError` from `parse_claude_response`) **trigger** the fallback chain — the same `primary → fallback → fallback2` path as API errors. This reverses the original spec wording. SC#1 and SC#2 no longer test a fork (API-fail vs parse-fail behaving differently); they test two failure types that **converge** on the same chain. Do NOT write any test asserting parse errors skip fallback. Ground truth: commit `1cb80f6` + the CLAUDE.md fallback design note. *(Existing tests `test_analyze_ticker_uses_fallback_on_primary_parse_error`, `test_analyze_ticker_uses_fallback2_on_fallback_parse_error`, `test_analyze_ticker_parse_error_propagates_when_no_fallback` already cover this for `analyze_ticker` — see D-04 for extending to etf/sell.)*

### Config.validate() depth (TEST-10)
- **D-02:** Test each required env var missing → raises `ValueError` (one assertion per field). Cover **both** `ANALYST_PROVIDER` branches: `claude` requires `ANTHROPIC_API_KEY` or `ANALYST_API_KEY` (`config.py:89-91`); any other provider requires `ANALYST_API_KEY` (`config.py:92-94`). Also add a positive test: a fully-valid config passes `validate()` without raising. No `test_config.py` exists today — this is net-new coverage.

### Quota exhaustion (TEST-11)
- **D-03:** Test covers **both** scan paths, not just the buy path. When all three providers (primary + fallback + fallback2) are at/over `analyst_daily_limit`, assert neither `analyze_ticker` (buy, `main.py:180`) nor `analyze_sell_ticker` (sell, `main.py:325`) is called. The test must exhaust all **three** provider slots — exhausting only two would pass while fallback2 is still under quota (false green).

### Fallback matrix breadth
- **D-04:** Fill the full fallback matrix across all three analyst functions — `analyze_ticker`, `analyze_etf_ticker`, `analyze_sell_ticker` — each covering: primary API-failure → fallback, parse-error → fallback, fallback → fallback2 chain, and propagate-when-no-fallback-configured. The sell path (`analyze_sell_ticker`) currently has the least coverage and is the priority gap. Symmetry across the three functions is the goal — no blind spots.

### USE_LIMIT_BUY config mapping (SC#4)
- **D-05:** Test all three cases: `USE_LIMIT_BUY` unset → `use_limit_buy = False` (the default as of 2026-06-06), `=false` → `False`, `=true` → `True`. This locks the reversed default as a regression guard, not just the literal `false → False` from the original SC#4.

### Claude's Discretion
- Test structure (parametrized tables vs separate test functions) — planner/executor decides.
- Test file organization — whether to create a new `tests/test_config.py` for the validate() suite or extend an existing file (likely a new file given none exists).
- Mock/fixture patterns — reuse the established `unittest.mock` + in-memory SQLite conventions already in the suite.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase spec (corrected 2026-06-06)
- `.planning/REQUIREMENTS.md` — TEST-09, TEST-10, TEST-11 (reworded to current behavior)
- `.planning/ROADMAP.md` §"Phase 18: Test Coverage Gaps" — success criteria 1–5 plus the inline spec-reversal note

### Behavior ground truth (parse-error fallback)
- `CLAUDE.md` §"Analyst fallback provider" (Key Design Decisions) — authoritative statement that BOTH API errors and parse errors trigger the fallback chain
- Commit `1cb80f6` — the code change that introduced parse-error fallback + Gemini list-format quota parsing, and the existing parse-error tests for `analyze_ticker`

### Code under test
- `config.py:81-99` — `Config.validate()`, the field checks and the two `ANALYST_PROVIDER` branches (TEST-10)
- `config.py:38` — `use_limit_buy` env mapping, default now `"false"` (SC#4)
- `main.py:180-205` — D-11 quota guard on the buy path (TEST-11)
- `main.py:325-346` — D-11 quota guard on the sell path (TEST-11)
- `analyst/claude_analyst.py` — `analyze_ticker` / `analyze_etf_ticker` / `analyze_sell_ticker` fallback pipelines (TEST-09, D-04)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tests/test_analyst_claude.py` — already contains the parse-error fallback tests for `analyze_ticker` and the `_make_fallback_config()` helper (sets primary=gemini, fallback=deepseek, fallback2=openai). Extend this for the etf/sell matrix (D-04).
- `tests/test_analyze_ticker.py` and `test_analyst_claude.py:259` (`test_analyze_etf_ticker_uses_fallback_on_primary_failure`) — existing API-failure fallback patterns to mirror.
- `tests/test_main.py` — `run_scan` test harness with the standard nested `patch("main.queries.increment_analyst_call_count")` + in-memory config fixture (`analyst_daily_limit = 18`). The model for the TEST-11 quota-exhaustion tests.
- `tests/test_discord_buttons.py:15` — `_make_config(use_limit_buy=...)` helper pattern for config-flag tests (SC#4).

### Established Patterns
- pytest + `unittest.mock` (`patch`, `MagicMock`); `side_effect` for multi-call fallback sequencing.
- In-memory SQLite (`db_path=":memory:"`) for DB-touching tests.
- `pytest.mark.asyncio` (or equivalent) for `run_scan` async tests.
- Quota source to mock: `queries.get_analyst_call_count_today(date, provider)` — return values ≥ `analyst_daily_limit` to simulate exhaustion.

### Integration Points
- No `tests/test_config.py` exists — the `Config.validate()` suite (TEST-10) is a new file.
- TEST-11 hooks into `run_scan` / sell-pass via the same mock stack already used in `test_main.py`.

</code_context>

<specifics>
## Specific Ideas

- "Go thorough" was the consistent choice across all four areas — full fallback matrix, both quota paths, all three USE_LIMIT_BUY cases, and the complete validate() suite including the happy path. Planner should not trim these to the literal minimum success-criteria wording.
- The quota test must exhaust all THREE provider slots (advisor-flagged false-green risk if only two are exhausted).

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 18-test-coverage-gaps*
*Context gathered: 2026-06-06*
