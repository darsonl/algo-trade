---
phase: 07-etf-scan-separation
plan: 03
subsystem: main, discord_bot, screener
tags: [etf, run_scan_etf, discord, slash-command, fundamentals-cleanup, async]
dependency_graph:
  requires: [07-01, 07-02]
  provides: [run_scan_etf, /scan_etf command, clean fundamentals filter]
  affects: [main.py, discord_bot/bot.py, screener/fundamentals.py, tests/test_main.py]
tech_stack:
  added: []
  patterns: [asyncio.to_thread, analyst-quota-guard, cache-check-pattern, partition_watchlist-as-etf-filter]
key_files:
  created: []
  modified:
    - main.py
    - discord_bot/bot.py
    - screener/fundamentals.py
    - tests/test_main.py
decisions:
  - "run_scan_etf skips fundamental filter entirely — ETF analysis uses only technicals, headlines, and expense_ratio"
  - "partition_watchlist wrapped in asyncio.to_thread both in run_scan_etf (ETF universe) and run_scan (stock universe exclusion) — ASYNC-03 compliance"
  - "send_etf_recommendation reuses ApproveRejectView with price or 0.0 fallback — same approval flow as stocks"
  - "_ETF_ALLOWLIST removed from fundamentals.py; stock scan now uses partition_watchlist to exclude ETFs upstream before fundamental filter is called"
  - "run_scan cache test updated to also patch partition_watchlist since run_scan now calls it"
metrics:
  duration: "~25 minutes"
  completed: "2026-04-07T12:45:13Z"
  tasks_completed: 2
  files_modified: 4
---

# Phase 7 Plan 3: ETF Pipeline Assembly Summary

**One-liner:** `run_scan_etf` coroutine wiring ETF watchlist through `analyze_etf_ticker` to Discord ETF embeds via `/scan_etf` command, plus `_ETF_ALLOWLIST` bypass removed from `passes_fundamental_filter` and stock scan universe filtered upstream via `partition_watchlist`.

## What Was Built

### Task 1: run_scan_etf, /scan_etf command, send_etf_recommendation

**main.py changes:**

- Added `partition_watchlist` to imports from `screener.universe`
- Added `analyze_etf_ticker` to imports from `analyst.claude_analyst`
- Updated `run_scan` to call `await asyncio.to_thread(partition_watchlist, universe)` after `get_universe`, filtering ETFs from the stock scan universe before any fundamental filter or analyst call
- Added `async def run_scan_etf(bot: TradingBot, config: Config) -> None:` coroutine that:
  - Reads `etf_watchlist.txt` via `get_watchlist`
  - Wraps `partition_watchlist` in `asyncio.to_thread` (ASYNC-03)
  - Skips tickers already recommended today or with open positions
  - Fetches technicals and expense ratio (no fundamental filter)
  - Checks analyst cache before making API calls
  - Applies same quota guard pattern as `run_scan` buy pass
  - Posts BUY-signal ETFs via `bot.send_etf_recommendation` with `asset_type="etf"`
  - Sends ops alert if 0 recommendations posted
- Added `bot._scan_etf_callback = lambda: run_scan_etf(bot, config)` in `main()`

**discord_bot/bot.py changes:**

- Added `build_etf_recommendation_embed` to embeds import
- Added `_scan_etf_callback = None` attribute to `TradingBot.__init__`
- Registered `/scan_etf` command in `setup_hook` via `self.tree.add_command`
- Added `_scan_etf_command` method (sends confirmation message, creates task via `_scan_etf_callback`)
- Added `send_etf_recommendation` method: fetches channel, builds ETF embed, attaches `ApproveRejectView`, returns message id

### Task 2: Fundamentals cleanup + run_scan_etf tests

**screener/fundamentals.py cleanup (D-06):**

- Removed `_ETF_ALLOWLIST` set entirely
- Removed ETF bypass (`if info.get("symbol", "").upper() in _ETF_ALLOWLIST: return True`)
- `passes_fundamental_filter` now starts directly with `pe = info.get("trailingPE")`
- All 8 existing fundamentals tests still pass — none relied on the ETF bypass

**tests/test_main.py — run_scan_etf tests (4 new):**

- `test_run_scan_etf_posts_buy_recommendation`: BUY signal results in `send_etf_recommendation` called; `create_recommendation` called with `asset_type="etf"`
- `test_run_scan_etf_skips_non_buy`: HOLD signal — `send_etf_recommendation` not called
- `test_run_scan_etf_skips_already_recommended`: `ticker_recommended_today=True` — `analyze_etf_ticker` not called
- `test_run_scan_etf_zero_recs_sends_ops_alert`: HOLD result — `send_ops_alert` called with message containing "ETF scan complete: 0"

Also updated `test_run_scan_cache_miss_calls_analyze_ticker_and_caches` to patch `partition_watchlist` (required since `run_scan` now calls it after `get_universe`).

## Verification

```
pytest tests/test_screener_fundamentals.py tests/test_main.py -v
# 24 passed

pytest  (full suite)
# 247 passed, 1 warning
```

```
grep -r "_ETF_ALLOWLIST" screener/fundamentals.py   # no matches
grep "scan_etf" discord_bot/bot.py                  # command registration + callback
grep "run_scan_etf" main.py                         # function definition + callback assignment
grep "asyncio.to_thread.*partition_watchlist" main.py  # 2 matches (run_scan + run_scan_etf)
grep "asset_type" main.py                            # "etf" passed to create_recommendation
```

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 4719967 | feat(07-03): add run_scan_etf, /scan_etf command, send_etf_recommendation |
| 2 | b285eca | feat(07-03): remove ETF bypass from fundamentals.py and add run_scan_etf tests |

## Deviations from Plan

**1. [Rule 1 - Bug] Updated existing run_scan cache test to patch partition_watchlist**

- **Found during:** Task 2 test run
- **Issue:** `test_run_scan_cache_miss_calls_analyze_ticker_and_caches` was failing because `run_scan` now calls `partition_watchlist` (added in Task 1), but the test did not mock it, causing a real yfinance call attempt
- **Fix:** Added `patch("main.partition_watchlist", return_value=(["AAPL"], []))` to the test
- **Files modified:** tests/test_main.py
- **Commit:** b285eca (included in Task 2 commit)

## Known Stubs

None — all functions are fully implemented. `run_scan_etf` makes real API calls, reads real files, and posts real Discord embeds. No placeholder data.

## Threat Flags

None — no new network endpoints, auth paths, or trust boundaries beyond those documented in the plan's threat model (T-07-09 through T-07-12). The `/scan_etf` command uses the same authorization model as `/scan` (any server member can trigger, single-operator design). The analyst quota guard (T-07-10) and `asyncio.to_thread` wrapping (ASYNC-03) are both implemented.

## Self-Check: PASSED

- [x] `main.py` contains `async def run_scan_etf(bot: TradingBot, config: Config) -> None:`
- [x] `main.py` contains `await asyncio.to_thread(partition_watchlist,` (2 occurrences — ASYNC-03)
- [x] `main.py` contains `analyze_etf_ticker` in import statement
- [x] `main.py` contains `asset_type="etf"` in `create_recommendation` call
- [x] `main.py` `run_scan` contains `stocks_only, _etfs = await asyncio.to_thread(partition_watchlist,`
- [x] `discord_bot/bot.py` contains `name="scan_etf"`
- [x] `discord_bot/bot.py` contains `async def _scan_etf_command(`
- [x] `discord_bot/bot.py` contains `async def send_etf_recommendation(`
- [x] `discord_bot/bot.py` contains `build_etf_recommendation_embed`
- [x] `discord_bot/bot.py` contains `_scan_etf_callback`
- [x] `main.py` contains `bot._scan_etf_callback = lambda: run_scan_etf(bot, config)`
- [x] `screener/fundamentals.py` does NOT contain `_ETF_ALLOWLIST`
- [x] `screener/fundamentals.py` does NOT contain `if info.get("symbol"`
- [x] `screener/fundamentals.py` `passes_fundamental_filter` starts with `pe = info.get("trailingPE")`
- [x] `tests/test_main.py` contains `test_run_scan_etf_posts_buy_recommendation`
- [x] `tests/test_main.py` contains `test_run_scan_etf_skips_non_buy`
- [x] `tests/test_main.py` contains `test_run_scan_etf_skips_already_recommended`
- [x] `tests/test_main.py` contains `test_run_scan_etf_zero_recs_sends_ops_alert`
- [x] `tests/test_main.py` contains `asset_type="etf"` in assertion
- [x] `pytest tests/test_screener_fundamentals.py tests/test_main.py` exits 0 (24 passed)
- [x] Full suite `pytest` exits 0 (247 passed)
- [x] Commits 4719967 and b285eca exist
