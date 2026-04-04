---
phase: 06-sell-signals-sell-orders
plan: 04
subsystem: sell-signals
tags: [exit-signals, macd, two-gate, analyst, tests]
dependency_graph:
  requires: []
  provides: [two-gate-exit-signal, macd-prompt-context, macd-matrix-tests]
  affects: [screener/exit_signals.py, analyst/claude_analyst.py, main.py, tests/test_exit_signals.py]
tech_stack:
  added: []
  patterns: [two-gate-logic, fail-safe-none-guard, keyword-only-optional-params]
key_files:
  created: []
  modified:
    - screener/exit_signals.py
    - analyst/claude_analyst.py
    - main.py
    - tests/test_exit_signals.py
decisions:
  - "MACD params added as keyword args after fallback_client in analyze_sell_ticker to preserve backward compatibility with positional callers"
  - "Existing tests that expected True with RSI-only data updated to include MACD bearish data — reflects correct two-gate contract"
  - "None guard covers all three values (rsi, macd_line, signal_line) as fail-safe: insufficient data means no exit signal"
metrics:
  duration_minutes: 12
  completed_date: "2026-04-05"
  tasks_completed: 2
  files_modified: 4
---

# Phase 06 Plan 04: MACD Gate Gap Closure Summary

Two-gate exit signal with MACD bearish confirmation: RSI AND macd_line < signal_line, with MACD context surfaced in the sell analyst prompt.

## What Was Done

**Task 1: Two-gate exit signal in check_exit_signals**

Replaced the RSI-only gate with a two-gate AND condition per D-01/D-10. The function now requires:
- Gate 1: `rsi > config.sell_rsi_threshold`
- Gate 2: `macd_line < signal_line` (MACD bearish confirmation)

A fail-safe None guard covers all three values — if any of rsi, macd_line, or signal_line is absent or None, the function returns False (no signal). This prevents false sells when technical data is incomplete.

4 existing tests that only supplied RSI data were updated to include MACD bearish context (`macd_line=-0.5, signal_line=0.2`) for the "should trigger" cases. 6 new MACD 2x2 matrix tests were added covering: RSI-high+MACD-bearish=True, RSI-high+MACD-bullish=False, RSI-low+MACD-bearish=False, and three None-guard permutations.

Total test count: 16 (10 original updated + 6 new), all passing.

**Task 2: MACD params in build_sell_prompt and analyze_sell_ticker**

`build_sell_prompt` gained `macd_line: float | None = None` and `signal_line: float | None = None` keyword args. When both values are provided, the prompt body includes MACD Line, Signal Line, Histogram, and a directional label (Bearish/Bullish). When either is None, the prompt shows "N/A (insufficient data)".

`analyze_sell_ticker` gained the same two keyword args positioned after `fallback_client` to maintain backward compatibility with existing positional callers. The values are passed through to `build_sell_prompt`.

`main.py` sell pass updated to pass `macd_line=tech_data.get("macd_line")` and `signal_line=tech_data.get("signal_line")` as kwargs to `analyze_sell_ticker`.

Note: `analyst/claude_analyst.py` and `main.py` changes were already present in the repository from the 06-05 commit (`5868923`) which ran in parallel. The exit_signals.py and test_exit_signals.py changes are the 06-04-specific commits.

## Commits

| Commit | Description |
|--------|-------------|
| f94a657 | feat(06-04): add MACD bearish gate to check_exit_signals (two-gate logic) |

Note: analyst/claude_analyst.py and main.py MACD params landed in 5868923 (06-05 parallel work, same plan window).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated 4 existing RSI-only tests to supply MACD data for True assertions**

- **Found during:** Task 1 verification
- **Issue:** 4 existing tests (`test_rsi_above_threshold_returns_true`, `test_custom_threshold_high_rsi_above`, `test_custom_threshold_low`, `test_rsi_just_above_threshold`) only supplied RSI without MACD data, which now returns False under the two-gate logic. Tests asserted True.
- **Fix:** Added `macd_line=-0.5, signal_line=0.2` (bearish) to each test's data dict to correctly exercise the two-gate path.
- **Files modified:** tests/test_exit_signals.py
- **Commit:** f94a657

## Known Stubs

None — all gates are wired to real technical data from fetch_technical_data, which already returns macd_line and signal_line.

## Self-Check: PASSED

- screener/exit_signals.py: FOUND
- tests/test_exit_signals.py: FOUND (16 tests passing)
- analyst/claude_analyst.py: FOUND (macd_line/signal_line params present)
- main.py: FOUND (macd_line/signal_line passed as kwargs)
- All 222 tests pass
