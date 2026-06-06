---
phase: 18-test-coverage-gaps
reviewed: 2026-06-06T00:00:00Z
depth: standard
files_reviewed: 4
files_reviewed_list:
  - tests/test_analyst_claude.py
  - tests/test_config.py
  - tests/test_main.py
  - tests/test_sell_scan.py
findings:
  critical: 0
  warning: 0
  info: 2
  total: 2
status: issues_found
---

# Phase 18: Code Review Report

**Reviewed:** 2026-06-06
**Depth:** standard
**Files Reviewed:** 4
**Status:** issues_found

## Summary

All four test files are structurally sound. The phase-specific focus checklist (quota-exhaustion vacuousness, `USE_LIMIT_BUY` namespace patching, fallback-matrix provider assertions, mock realism) passes on every count:

- **Quota exhaustion (test_main.py:171, test_sell_scan.py:211):** All three provider slots are set non-empty before `run_scan` is called. The mock returns the daily limit (18) for all providers, which satisfies the `>= analyst_daily_limit` three-way AND guard. Both tests are made non-vacuous by their positive counterparts (`test_run_scan_cache_miss_calls_analyze_ticker_and_caches` and `test_sell_pass_posts_sell_recommendation`), which prove the analyst function IS reached when count=0.
- **USE_LIMIT_BUY (test_config.py:166):** `_reload_with_env` patches `dotenv.load_dotenv` — the source module reference imported on line 15 — not `config.load_dotenv`. This is correct: `importlib.reload` re-executes `config.py`'s `from dotenv import load_dotenv` at reload time, which would restore the real function if only `config.load_dotenv` were patched. Patching the source survives the rebind. The `unset → False` case is deterministic.
- **Fallback matrix (test_analyst_claude.py):** No test asserts that parse errors skip the fallback chain. All parse-error tests converge down the chain (primary → fallback → fallback2). Provider assertions match `_make_fallback_config()` (gemini primary, deepseek fallback, openai fallback2): deepseek at lines 844, 904, 929, 952; openai at lines 866, 975, 998, 1082.
- **Mock realism:** `capture_call(client, model, prompt)` signature matches `_call_api(client, model, prompt)`. Analysis dicts carry `provider_used` and `confidence` keys, matching what `run_scan` reads via `analysis.get("confidence")` and `analysis["provider_used"]`.

Two minor test-hygiene items are noted below.

## Info

### IN-01: `fetch_macro_context` and `fetch_eps_data` not patched in test_main.py async tests

**File:** `tests/test_main.py:89`
**Issue:** None of the six `run_scan` / `run_scan_etf` tests in `test_main.py` patch `main.fetch_macro_context` or `main.fetch_eps_data`. In `run_scan`, both are called inside `asyncio.to_thread(...)` wrapped in `try/except`; failures are silently swallowed and `macro_context` falls back to a null dict. This means the tests do not make vacuous assertions — they still exercise the correct code paths — but the tests are susceptible to network latency or DNS failures in CI environments that block outbound connections, making the suite intermittently slow or flaky. The sell-scan tests (`test_sell_scan.py`) correctly patch `fetch_macro_context` at the module level on every test; `test_main.py` should do the same for consistency. `fetch_eps_data` is called inside the per-ticker `try/except` in `run_scan` and its absence does not affect assertions, but it represents unnecessary live I/O in a test.

**Fix:** Add patches for `main.fetch_macro_context` and `main.fetch_eps_data` to the `run_scan` async test harnesses in `test_main.py`:
```python
with patch("main.fetch_macro_context", return_value={"spy_trend_1m": None, "spy_trend_1y": None, "vix_level": None}):
    with patch("main.fetch_eps_data", return_value=None):
        # ... existing patches ...
        await run_scan(bot, config)
```

### IN-02: Weak disjunctive assertion in `test_sell_pass_posts_sell_recommendation`

**File:** `tests/test_sell_scan.py:73`
**Issue:** After asserting `send_sell_recommendation` was called once, the test uses a disjunctive assertion to confirm the ticker: `assert ticker_in_kwargs == "AAPL" or ticker_in_args == "AAPL"`. In `main.py`, `send_sell_recommendation` is called with `ticker=ticker` as a keyword argument (line 385-394), so `ticker_in_kwargs` will always be `"AAPL"` when the call is correct. The `or ticker_in_args == "AAPL"` branch is dead — it would only catch a positional calling convention that the source does not use. This makes the assertion weaker than the source warrants. Similarly, `test_run_scan_etf_posts_buy_recommendation` (test_main.py:279) uses `first_call.kwargs.get("ticker") == "SPY" or first_call.kwargs.get("signal") == "BUY"`, which passes trivially on a correct BUY signal even if ticker is wrong.

**Fix:** Tighten both assertions to the keyword form only, matching the actual calling convention:
```python
# test_sell_scan.py:73 — replace disjunction with direct kwargs check
call_kwargs = mock_bot.send_sell_recommendation.call_args
assert call_kwargs.kwargs.get("ticker") == "AAPL"

# test_main.py:279 — replace disjunction with direct kwargs check
first_call = bot.send_etf_recommendation.call_args_list[0]
assert first_call.kwargs.get("ticker") == "SPY"
assert first_call.kwargs.get("signal") == "BUY"
```

---

_Reviewed: 2026-06-06_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
