---
phase: 06-sell-signals-sell-orders
verified: 2026-04-06T00:00:00Z
status: human_needed
score: 31/31 must-haves verified
re_verification:
  previous_status: gaps_found
  previous_score: 22/31
  gaps_closed:
    - "check_exit_signals returns True only when RSI exceeds SELL_RSI_THRESHOLD AND MACD is bearish (macd_line < signal_line)"
    - "ANALYST_DAILY_LIMIT config field exists with default 18"
    - "analyst_calls table exists with (date, provider) composite primary key and count column"
    - "get_analyst_call_count_today(db_path, provider) returns today's call count for that provider"
    - "increment_analyst_call_count(db_path, provider) upserts today's row for that provider"
    - "analyze_ticker and analyze_sell_ticker return provider_used in their result dict"
    - "build_sell_prompt produces a prompt with entry price, current price, P&L%, hold duration, RSI, macd_line, signal_line, and headlines"
    - "Daily analyst quota is checked before every analyst call (buy and sell), skipping tickers when limit reached"
    - "increment_analyst_call_count is called after every successful analyst API call"
  gaps_remaining: []
  regressions: []
human_verification:
  - test: "In a live or near-live environment, create a position for a ticker with RSI > 70 but with clearly bullish MACD (macd_line > signal_line), then trigger a scan."
    expected: "No sell recommendation is posted — the two-gate check (RSI AND macd_line < signal_line) blocks the sell signal when MACD is bullish."
    why_human: "Requires live yfinance data and a running Discord bot with a valid token and channel to observe the gate in action."
  - test: "With DRY_RUN=true, create a position manually in the database with RSI above threshold and MACD bearish, run a scan, and click Approve Sell on the Discord embed."
    expected: "Log shows '[DRY RUN] Approved: selling N share(s) of TICKER at $X', position status becomes 'closed', a trade row is created with side='sell'."
    why_human: "Requires a running Discord bot with a valid Discord token, real channel, and an interactive Approve button click."
---

# Phase 6: Sell Signals & Sell Orders Verification Report

**Phase Goal:** Complete the trading loop — the bot can recommend and execute sells with the same human-approval flow as buys. Implement automated exit signal detection (RSI + MACD), analyst sell analysis, Discord sell recommendations with approve/reject, and Schwab sell order placement.
**Verified:** 2026-04-06
**Status:** human_needed
**Re-verification:** Yes — after gap closure (Plans 06-04 and 06-05)

---

## Goal Achievement

### Observable Truths (from Plan 01 + Plan 02 must_haves)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | SELL_RSI_THRESHOLD config field exists with default 70 | VERIFIED | config.py line 44: `sell_rsi_threshold: float = float(os.getenv("SELL_RSI_THRESHOLD", "70.0"))` |
| 2 | ANALYST_DAILY_LIMIT config field exists with default 18 | VERIFIED | config.py line 45: `analyst_daily_limit: int = int(os.getenv("ANALYST_DAILY_LIMIT", "18"))` — spot-check confirmed `Config().analyst_daily_limit == 18` |
| 3 | analyst_calls table exists with (date, provider) composite PK | VERIFIED | models.py lines 65-71: `CREATE TABLE IF NOT EXISTS analyst_calls (date TEXT NOT NULL, provider TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (date, provider))` |
| 4 | get_analyst_call_count_today(db_path, provider) exists | VERIFIED | queries.py lines 260-273: function exists, queries analyst_calls for today's date and provider, returns 0 if no row |
| 5 | increment_analyst_call_count(db_path, provider) exists | VERIFIED | queries.py lines 276-291: function exists, uses `INSERT ... ON CONFLICT DO UPDATE SET count = count + 1` |
| 6 | analyze_ticker and analyze_sell_ticker return provider_used | VERIFIED | claude_analyst.py line 185: `provider_used = config.analyst_provider`; line 199 (fallback override); line 202: `result["provider_used"] = provider_used`. Same pattern in analyze_sell_ticker lines 281-298. |
| 7 | positions table has sell_blocked column | VERIFIED | models.py line 62: `sell_blocked BOOLEAN DEFAULT 0` in CREATE TABLE and ALTER TABLE migration |
| 8 | trades table has side column defaulting to 'buy' | VERIFIED | models.py line 37: `side TEXT NOT NULL DEFAULT 'buy'` in CREATE TABLE and ALTER TABLE migration |
| 9 | build_sell_prompt includes entry price, current price, P&L%, hold duration, RSI, macd_line, signal_line, and headlines | VERIFIED | claude_analyst.py lines 206-247: function accepts `macd_line` and `signal_line` kwargs; when both provided, prompt includes MACD Line, Signal Line, Histogram, and directional label; when absent shows N/A |
| 10 | analyze_ticker can process SELL/HOLD signals via same pipeline | VERIFIED | `_VALID_SIGNALS = {"BUY", "HOLD", "SKIP", "SELL"}` at line 52; parse_claude_response accepts SELL |
| 11 | check_exit_signals returns True only when RSI exceeds threshold AND MACD bearish | VERIFIED | exit_signals.py lines 5-21: two-gate logic — `rsi > config.sell_rsi_threshold and macd_line < signal_line`; None guard on all three values. Spot-checks confirmed: RSI+MACD-bearish=True, RSI+MACD-bullish=False, RSI-only=False |
| 12 | compute_macd returns (macd_line, signal_line, histogram) from price Series | VERIFIED | screener/technicals.py lines 46-65; returns None tuple on insufficient data |
| 13 | fetch_technical_data returns macd_line, signal_line, macd_histogram fields | VERIFIED | technicals.py lines 104-131; all three MACD keys in return dict |
| 14 | build_market_sell constructs a Schwab sell order spec | VERIFIED | orders.py line 22: `return equity_sell_market(ticker, shares).build()` |
| 15 | set_sell_blocked and reset_sell_blocked update positions table | VERIFIED | queries.py lines 235-254; correct SQL against status='open' |
| 16 | Daily scan includes a sell pass after the buy pass | VERIFIED | main.py line 165: `# --- Sell pass: evaluate open positions for exit signals ---` |
| 17 | Sell pass iterates open positions, skips sell_blocked, checks exit signals, calls analyst | VERIFIED | main.py lines 169-266; all three steps present |
| 18 | sell_blocked is reset when RSI drops below threshold | VERIFIED | main.py lines 175-183: RSI check inside sell_blocked branch calls reset_sell_blocked |
| 19 | SELL recommendation creates a red Discord embed with entry price, current price, P&L% | VERIFIED | embeds.py lines 42-62; red color, 5 fields including Entry Price, Current Price, P&L |
| 20 | Approving a sell creates a trade with side='sell', closes position, places sell order (or logs dry run) | VERIFIED | bot.py lines 112-134; create_trade(side="sell"), close_position, dry_run guard |
| 21 | Rejecting a sell sets sell_blocked=True on the position | VERIFIED | bot.py lines 136-143; set_sell_blocked called in reject handler |
| 22 | ANALYST_CALL_DELAY_S respected for sell analyst calls | VERIFIED | claude_analyst.py line 279: `time.sleep(config.analyst_call_delay_s)` in analyze_sell_ticker |
| 23 | Daily analyst quota checked before every analyst call | VERIFIED | main.py lines 95-111 (buy pass) and lines 207-223 (sell pass): both check primary + fallback counts against config.analyst_daily_limit, log warning and continue if both exhausted |
| 24 | increment_analyst_call_count called after every successful analyst API call | VERIFIED | main.py line 116: `queries.increment_analyst_call_count(config.db_path, analysis["provider_used"])` after analyze_ticker; line 232-234: same after analyze_sell_ticker |
| 25 | check_exit_signals tested for all RSI+MACD combinations plus None/missing cases | VERIFIED | tests/test_exit_signals.py: 16 tests covering RSI+MACD-bearish=True, RSI+MACD-bullish=False, RSI-low+MACD-bearish=False, and three None-guard permutations (06-04 added 6 new MACD matrix tests, updated 4 existing) |
| 26 | build_sell_prompt tested for correct field inclusion | VERIFIED | test_sell_prompt.py tests all fields present in actual implementation |
| 27 | parse_claude_response accepts SELL signal | VERIFIED | test_sell_prompt.py test_parse_sell_signal passes |
| 28 | build_sell_embed tested for red color and all fields | VERIFIED | test_sell_embed.py covers color, title, 5 fields, values |
| 29 | SellApproveRejectView approve tested: creates trade with side='sell', closes position | VERIFIED | test_sell_buttons.py 8 tests covering approve/reject behavior |
| 30 | SellApproveRejectView reject tested: sets sell_blocked, marks recommendation rejected | VERIFIED | test_sell_buttons.py |
| 31 | sell pass in run_scan tested: skips sell_blocked, resets when RSI drops, posts sell recs | VERIFIED | test_sell_scan.py 9 tests covering all sell pass behaviors |

**Score:** 31/31 truths verified

---

### Re-verification: Gaps Closed

All 9 gaps from the initial verification (2026-04-05) are now closed.

**Root Cause 1 (MACD gate) — Plans 06-04:**

- `check_exit_signals` now implements two-gate AND logic: `rsi > config.sell_rsi_threshold and macd_line < signal_line`, with a None guard that returns False when any value is absent (exit_signals.py lines 14-21).
- `build_sell_prompt` gained `macd_line: float | None = None` and `signal_line: float | None = None` keyword args. When both provided, prompt body shows MACD Line, Signal Line, Histogram, and directional label. When absent, shows "N/A (insufficient data)" (claude_analyst.py lines 214-240).
- `analyze_sell_ticker` gained matching `macd_line` and `signal_line` keyword args, passed through to `build_sell_prompt` (claude_analyst.py lines 261-262, 273-275).
- `main.py` sell pass passes `macd_line=tech_data.get("macd_line")` and `signal_line=tech_data.get("signal_line")` as kwargs to `analyze_sell_ticker` (main.py lines 229-230).
- `tests/test_exit_signals.py` updated: 4 existing "should trigger" tests now supply MACD bearish data; 6 new MACD 2x2 matrix tests added. Total: 16 tests.

**Root Cause 2 (analyst daily quota) — Plan 06-05:**

- `config.py` line 45: `analyst_daily_limit: int = int(os.getenv("ANALYST_DAILY_LIMIT", "18"))`.
- `database/models.py` lines 65-71: `analyst_calls` table with `PRIMARY KEY (date, provider)` in `initialize_db` executescript.
- `database/queries.py` lines 257-291: `get_analyst_call_count_today` and `increment_analyst_call_count` under `# --- Analyst quota tracking (D-11) ---`.
- `analyst/claude_analyst.py`: both `analyze_ticker` (lines 185, 199, 202) and `analyze_sell_ticker` (lines 281, 295, 298) set `provider_used`, override in fallback except branch, and attach to result dict.
- `main.py` buy pass (lines 95-118) and sell pass (lines 207-234): quota guard checks both providers, skips on exhaustion, increments after each successful call; cache hits bypass both.

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `config.py` | sell_rsi_threshold and analyst_daily_limit fields | VERIFIED | sell_rsi_threshold line 44, analyst_daily_limit line 45 |
| `database/models.py` | analyst_calls table, sell_blocked and side migrations | VERIFIED | analyst_calls table lines 65-71; sell_blocked migration lines 81-86; side migration lines 87-93 |
| `database/queries.py` | set_sell_blocked, reset_sell_blocked, create_trade with side, get_analyst_call_count_today, increment_analyst_call_count | VERIFIED | All five present; quota functions lines 260-291 |
| `screener/technicals.py` | compute_macd, extended fetch_technical_data | VERIFIED | compute_macd returns macd_line/signal_line/histogram |
| `screener/exit_signals.py` | check_exit_signals (RSI + MACD two-gate) | VERIFIED | Two-gate AND logic with None guard; 21 lines |
| `analyst/claude_analyst.py` | build_sell_prompt with MACD params, analyze_sell_ticker with MACD params, SELL in _VALID_SIGNALS, provider_used return | VERIFIED | All four requirements met |
| `schwab_client/orders.py` | build_market_sell, place_sell_order | VERIFIED | Both present and wired via equity_sell_market import |
| `discord_bot/embeds.py` | build_sell_embed | VERIFIED | Exists, red color, 5 fields |
| `discord_bot/bot.py` | SellApproveRejectView, send_sell_recommendation | VERIFIED | Both present and wired |
| `main.py` | sell pass in run_scan, quota guard (buy + sell) | VERIFIED | Sell pass lines 165-266; quota guards in both passes |
| `tests/test_exit_signals.py` | exit signal unit tests covering MACD 2x2 matrix | VERIFIED | 16 tests (10 RSI baseline + 6 MACD matrix) |
| `tests/test_sell_prompt.py` | sell prompt + parse tests | VERIFIED | 63 lines, 8 test functions |
| `tests/test_sell_embed.py` | sell embed tests | VERIFIED | 57 lines, 8 test functions |
| `tests/test_sell_buttons.py` | SellApproveRejectView async tests | VERIFIED | 172 lines, 9 test functions |
| `tests/test_sell_scan.py` | run_scan sell pass integration tests | VERIFIED | 233 lines, 9 test functions |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| screener/exit_signals.py | config.py | config.sell_rsi_threshold | VERIFIED | exit_signals.py line 21 |
| screener/exit_signals.py | screener/technicals.py | macd_line and signal_line from technical_data dict | VERIFIED | exit_signals.py lines 15-16: `macd_line = technical_data.get("macd_line"); signal_line = technical_data.get("signal_line")` |
| analyst/claude_analyst.py | analyst/claude_analyst.py | build_sell_prompt feeds into analyze_sell_ticker | VERIFIED | analyze_sell_ticker calls build_sell_prompt at line 273, passing macd_line and signal_line kwargs |
| schwab_client/orders.py | schwab.orders.equities | equity_sell_market import | VERIFIED | orders.py line 5 |
| main.py | screener/exit_signals.py | check_exit_signals call in sell pass | VERIFIED | main.py line 21 import; line 191 call |
| main.py | analyst/claude_analyst.py | analyze_sell_ticker call with macd_line/signal_line | VERIFIED | main.py lines 225-231; macd_line and signal_line passed as kwargs |
| main.py | database/queries.py | get_analyst_call_count_today + increment_analyst_call_count | VERIFIED | main.py lines 96-98, 101-105, 116-118 (buy pass); lines 208-212, 214-218, 232-234 (sell pass) |
| discord_bot/bot.py | schwab_client/orders.py | place_sell_order on approve | VERIFIED | bot.py line 13 import; line 116 call |
| discord_bot/bot.py | database/queries.py | close_position + create_trade(side='sell') on approve | VERIFIED | bot.py lines 118-127 |
| discord_bot/bot.py | database/queries.py | set_sell_blocked on reject | VERIFIED | bot.py line 139 |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| discord_bot/embeds.py build_sell_embed | entry_price, current_price, pnl_pct | main.py sell pass (pos["avg_cost_usd"], tech_data["price"]) | Yes — from DB + yfinance | FLOWING |
| discord_bot/bot.py SellApproveRejectView | current_price, shares | Passed from send_sell_recommendation call in main.py | Yes — real position data | FLOWING |
| main.py sell pass | open_positions | queries.get_open_positions(config.db_path) | Yes — reads positions table | FLOWING |
| main.py quota guard | primary_count, fallback_count | queries.get_analyst_call_count_today(config.db_path, provider) | Yes — reads analyst_calls table | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| check_exit_signals: RSI+MACD bearish fires | `check_exit_signals({'rsi':75.0,'macd_line':-0.5,'signal_line':0.2}, c)` | True | PASS |
| check_exit_signals: RSI+MACD bullish does NOT fire | `check_exit_signals({'rsi':75.0,'macd_line':0.5,'signal_line':0.2}, c)` | False | PASS |
| check_exit_signals: RSI-only (no MACD) returns False | `check_exit_signals({'rsi':75.0}, c)` | False | PASS |
| Config.analyst_daily_limit default | `Config().analyst_daily_limit` | 18 | PASS |
| All 222 tests pass | `pytest tests/ -q` | 222 passed | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SELL-01 | 06-01, 06-03 | Claude sell signal prompt built from position data + recent news, including MACD context | VERIFIED | build_sell_prompt includes MACD Line, Signal Line, Histogram, direction; tested in test_sell_prompt.py |
| SELL-02 | 06-01, 06-03 | Technical exit trigger: RSI > threshold AND MACD bearish on held position | VERIFIED | check_exit_signals: two-gate AND logic wired to tech_data; 16 tests |
| SELL-03 | 06-02, 06-03 | Daily scan includes sell signal evaluation pass over open positions (after buy scan) | VERIFIED | Sell pass in run_scan present and tested in test_sell_scan.py |
| SELL-04 | 06-02, 06-03 | Sell recommendation stored in recommendations table (signal='SELL', linked to position) | VERIFIED | create_recommendation called with signal="SELL" in main.py sell pass |
| SELL-05 | 06-02, 06-03 | Discord embed for sell recommendation (red color, shows entry price, current price, P&L) | VERIFIED | build_sell_embed: red color, Entry Price, Current Price, P&L fields present |
| SELL-06 | 06-02, 06-03 | Approve/Reject buttons for sell trigger market sell order via Schwab | VERIFIED | SellApproveRejectView with approve (place_sell_order) and reject handlers |
| SELL-07 | 06-01, 06-03 | build_market_sell added to schwab_client/orders.py | VERIFIED | orders.py line 22 |
| SELL-08 | 06-02, 06-03 | On sell approval, position status updated to 'closed', trade record created for the sell | VERIFIED | close_position + create_trade(side='sell') in SellApproveRejectView.approve |
| SELL-09 | 06-02, 06-03 | On sell rejection, position remains open, recommendation marked rejected | VERIFIED | update_recommendation_status("rejected") + set_sell_blocked in reject handler |

---

### Anti-Patterns Found

No blockers or warnings found. All previously flagged anti-patterns are resolved:

- `screener/exit_signals.py`: Docstring now documents the two-gate behavior correctly (RSI AND MACD bearish).
- `database/queries.py`: Analyst quota functions present and complete.
- `analyst/claude_analyst.py`: Both analyze functions return `provider_used` in result dict.
- `main.py`: Quota guard and increment wired in both buy and sell passes.

---

### Human Verification Required

#### 1. MACD Gate Behavioral Correctness (Live Environment)

**Test:** In a live or near-live environment, manually create a position for a ticker with RSI > 70 but with clearly bullish MACD (macd_line > signal_line), then trigger a scan.
**Expected:** No sell recommendation is posted — the two-gate check (RSI AND macd_line < signal_line) blocks the sell signal when MACD is bullish. With the old RSI-only implementation, a sell recommendation would have been posted.
**Why human:** Requires live yfinance data returning actual MACD values and a running Discord bot with a valid token and channel to observe the gate in action.

#### 2. Sell Order Execution End-to-End (Dry Run)

**Test:** With DRY_RUN=true, create a position manually in the database with RSI above threshold and MACD bearish on the next scan, run a scan, and click "Approve Sell" on the Discord embed.
**Expected:** Log shows "[DRY RUN] Approved: selling N share(s) of TICKER at $X", position status becomes 'closed', a trade row is created with side='sell'.
**Why human:** Requires a running Discord bot with a valid Discord token, real channel, and an interactive Approve button click.

---

### Summary

All 9 gaps identified in the initial verification (2026-04-05) are now closed. The two root causes were fully addressed:

**Root Cause 1 (MACD gate, Plan 06-04):** `check_exit_signals` now implements the specified two-gate AND logic (RSI AND MACD bearish) with a None fail-safe. `build_sell_prompt` and `analyze_sell_ticker` accept and surface MACD values. The call site in `main.py` passes MACD data from `tech_data`. Six new MACD 2x2 matrix tests validate all gate combinations.

**Root Cause 2 (Analyst quota system, Plan 06-05):** The complete D-11 quota tracking system is implemented end-to-end — `analyst_daily_limit` in Config, `analyst_calls` table in the DB, `get_analyst_call_count_today` and `increment_analyst_call_count` in queries.py, `provider_used` returned from both analyze functions, and quota guards with increments in both the buy and sell passes of `main.py`.

The full test suite (222 tests) passes. Two human verification items remain for live Discord environment testing.

---

_Verified: 2026-04-06_
_Verifier: Claude (gsd-verifier)_
_Re-verification: Yes — after gap closure plans 06-04 and 06-05_
