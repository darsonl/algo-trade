# Phase 18: Test Coverage Gaps - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-06
**Phase:** 18-test-coverage-gaps
**Areas discussed:** TEST-09 conflict, Config.validate() depth, Quota exhaustion scope, Fallback matrix breadth, USE_LIMIT_BUY test

---

## TEST-09 Conflict (parse-error fallback)

| Option | Description | Selected |
|--------|-------------|----------|
| Reword spec to new behavior | Update ROADMAP SC#2 + TEST-09 to assert parse error → fallback; tests codify current code | |
| Reword + note the reversal | Same, plus a dated note recording the reversal and why (Gemini free model returns unparseable output) | ✓ |
| Keep old spec, revert code | Treat parse-error fallback as a mistake, revert commit 1cb80f6 | |

**User's choice:** Reword + note the reversal
**Notes:** Roadmap SC#2/TEST-09 were written against the old "parse errors do not trigger fallback" design, which the user reversed in commit 1cb80f6. Spec corrected in commit 99f4a1f before writing CONTEXT.md. Advisor flagged that SC#1/SC#2 were a *pair* testing a distinction that no longer exists — reword drops the "asserts this distinction" framing.

---

## Config.validate() depth

| Option | Description | Selected |
|--------|-------------|----------|
| Each field + branch + happy path | Per-field ValueError, both ANALYST_PROVIDER branches, plus valid-config-passes test | ✓ |
| Each missing field only | Just assert each missing var raises | |
| You decide structure | Cover fields; planner picks parametrize vs separate | |

**User's choice:** Each field + branch + happy path
**Notes:** No test_config.py exists today — net-new coverage.

---

## Quota exhaustion scope (TEST-11)

| Option | Description | Selected |
|--------|-------------|----------|
| Both buy and sell paths | Assert neither analyze_ticker nor analyze_sell_ticker called when all providers exhausted | ✓ |
| Buy path only | Literal TEST-11 wording (analyze_ticker only) | |

**User's choice:** Both buy and sell paths
**Notes:** D-11 guard exists on both main.py:180 (buy) and main.py:325 (sell). Advisor flagged "both providers" in SC#5 was stale — code checks all three (primary + fallback + fallback2); test must exhaust all three slots to avoid a false green.

---

## Fallback matrix breadth

| Option | Description | Selected |
|--------|-------------|----------|
| Fill the full matrix | All 3 analyst functions × (API-failure, parse-error, fallback2, propagate-no-fallback) | ✓ |
| Close named gaps only | Just SC#1/#2 on analyze_ticker | |

**User's choice:** Fill the full matrix
**Notes:** Sell path (analyze_sell_ticker) currently has the least coverage — priority gap.

---

## USE_LIMIT_BUY test (SC#4)

| Option | Description | Selected |
|--------|-------------|----------|
| All three: unset, false, true | unset → False (new default), false → False, true → True | ✓ |
| Just false → False | Literal SC#4 only | |

**User's choice:** All three: unset, false, true
**Notes:** Locks the 2026-06-06 default reversal as a regression guard.

---

## Claude's Discretion

- Test structure (parametrized tables vs separate test functions)
- Test file organization (new tests/test_config.py vs extending existing)
- Mock/fixture patterns (reuse established unittest.mock + in-memory SQLite conventions)

## Deferred Ideas

None — discussion stayed within phase scope.
