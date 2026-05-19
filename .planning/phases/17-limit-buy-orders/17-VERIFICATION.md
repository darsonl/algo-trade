---
phase: 17-limit-buy-orders
verified: 2026-05-19T00:00:00Z
status: human_needed
score: 5/5 must-haves verified
re_verification: false
human_verification:
  - test: "Enable USE_LIMIT_BUY=true (default) and PAPER_TRADING=true, trigger a scan in paper-trading mode, click Approve on a BUY embed"
    expected: "Schwab paper-trading endpoint receives a limit order with orderType=LIMIT and duration=GOOD_TILL_CANCEL at the scan-time price formatted as a 2-decimal string; confirmation message reads 'Approved: buying N share(s) of TICKER at $X.XX (limit, GTC).'"
    why_human: "place_limit_order calls the real Schwab API through schwab-py; unit tests mock the HTTP layer and cannot confirm the JSON spec is accepted by the broker endpoint or that GTC duration persists through schwab-py's build() serialization in a live call"
  - test: "Post a BUY recommendation embed in Discord (USE_LIMIT_BUY=true, scan running) and observe the Price field"
    expected: "Price field renders as two lines: '$X.XX' on the first line and 'as of HH:MM' on the second line within the same embed field"
    why_human: "Discord renders embed field values with newlines differently depending on client version and mobile vs desktop; unit tests assert the string contains the newline but cannot verify the visual rendering in Discord's UI"
  - test: "Set USE_LIMIT_BUY=false in .env, restart bot, trigger scan, click Approve on a BUY embed"
    expected: "Confirmation message reads 'Approved: buying N share(s) of TICKER at $X.XX.' (no '(limit, GTC)' parenthetical); Schwab receives a market order, not a limit order"
    why_human: "Fallback to market order path needs to be confirmed against the live Schwab API; tests mock place_order but cannot verify the market-order JSON spec is sent instead of a limit spec at the broker"
---

# Phase 17: Limit Buy Orders Verification Report

**Phase Goal:** Every approved BUY places a limit order at the signal price instead of a market order, reducing execution slippage risk
**Verified:** 2026-05-19
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Approving a BUY with USE_LIMIT_BUY=true (dry_run=false) calls place_limit_order, not place_order (SC-1, RISK-01) | VERIFIED | `bot.py:77-80` — `if self.config.use_limit_buy: order_id = place_limit_order(...)` |
| 2 | Setting USE_LIMIT_BUY=false falls back to place_order (SC-2, RISK-02) | VERIFIED | `bot.py:81-82` — `else: order_id = place_order(...)` branch |
| 3 | trades table records limit_price and order_type for every executed buy (SC-3, RISK-03) | VERIFIED | `models.py:42-43` CREATE TABLE columns; `models.py:122,127` ALTER TABLE migrations; `queries.py:62-85` create_trade with both params; `bot.py:84-93` call with limit_price_val/order_type_val |
| 4 | BUY embed Price field shows scan-time price with "as of HH:MM" timestamp (SC-4, RISK-04) | VERIFIED | `embeds.py:20,32-33` — scan_time kwarg + conditional `\nas of {scan_time}` append; `main.py:101` scan_time captured before loop; `main.py:256` passed to send_recommendation |
| 5 | Limit orders use GTC duration, not DAY (SC-5) | VERIFIED | `orders.py:30` — `spec.set_duration(Duration.GOOD_TILL_CANCEL)` before `.build()`; 5 unit tests confirm spec["duration"] == "GOOD_TILL_CANCEL" |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `config.py` | use_limit_buy bool field | VERIFIED | Line 38: `use_limit_buy: bool = os.getenv("USE_LIMIT_BUY", "true").lower() == "true"` — correct pattern, not `bool(os.getenv(...))` |
| `schwab_client/orders.py` | build_limit_buy + place_limit_order + Duration import | VERIFIED | Lines 5-6 imports; `build_limit_buy` at line 23; `place_limit_order` at line 90; `Duration.GOOD_TILL_CANCEL` at line 30 |
| `database/models.py` | limit_price REAL and order_type TEXT in trades CREATE TABLE + ALTER TABLE blocks | VERIFIED | CREATE TABLE columns at lines 42-43; ALTER TABLE blocks at lines 121-130 |
| `database/queries.py` | create_trade with limit_price and order_type params | VERIFIED | Lines 62-85 — both params with safe defaults (limit_price=None, order_type="market") |
| `tests/test_schwab_orders.py` | 5 tests for build_limit_buy | VERIFIED | 5 `test_build_limit_buy_*` functions confirmed by `grep -c` |
| `discord_bot/embeds.py` | build_recommendation_embed with scan_time kwarg | VERIFIED | Lines 20, 32-33 — param + if-branch; build_etf_recommendation_embed untouched |
| `discord_bot/bot.py` | ApproveRejectView routing + send_recommendation scan_time | VERIFIED | Lines 39, 45 (scan_time param + self.scan_time); lines 73-93 (routing block); lines 284, 288-289 (send_recommendation) |
| `main.py` | scan_time captured before ticker loop; passed to send_recommendation | VERIFIED | Line 101 capture (before `for ticker in universe:` at line 103); line 256 pass |
| `tests/test_discord_buttons.py` | 6 new tests for limit/market/dry-run routing | VERIFIED | 20 references to use_limit_buy; new test count confirmed by suite (444 passed) |
| `tests/test_discord_embeds.py` | 3 new tests for scan_time Price field variants | VERIFIED | Suite passes; 3 scan_time tests from SUMMARY confirmed |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `main.py run_scan` | `send_recommendation` | `scan_time=scan_time` kwarg | VERIFIED | `main.py:256` — `scan_time=scan_time` in send_recommendation call |
| `send_recommendation` | `build_recommendation_embed` | `scan_time=scan_time` kwarg forwarding | VERIFIED | `bot.py:288` — `scan_time=scan_time` passed to embed builder |
| `send_recommendation` | `ApproveRejectView.__init__` | `scan_time=scan_time` kwarg | VERIFIED | `bot.py:289` — `ApproveRejectView(rec_id, ticker, price, self.config, scan_time=scan_time)` |
| `ApproveRejectView.approve` | `place_limit_order` | `config.use_limit_buy` routing branch | VERIFIED | `bot.py:77-80` — `if self.config.use_limit_buy: order_id = place_limit_order(...)` |
| `ApproveRejectView.approve` | `create_trade` | `limit_price=limit_price_val, order_type=order_type_val` kwargs | VERIFIED | `bot.py:84-93` — both kwargs passed; limit_price_val/order_type_val initialized outside dry_run block (D-06) |
| `config.py USE_LIMIT_BUY` | `os.getenv` | `.lower() == "true"` pattern | VERIFIED | Line 38 — exact pattern confirmed; guards against bool("false") == True pitfall |
| `build_limit_buy` | `Duration.GOOD_TILL_CANCEL` | `spec.set_duration(...)` | VERIFIED | `orders.py:30` — called before `.build()` |
| `database/queries.py create_trade` | `trades table` | `INSERT INTO trades (..., limit_price, order_type)` | VERIFIED | `queries.py:77-80` — 9-column INSERT with both new columns |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `discord_bot/embeds.py build_recommendation_embed` | `scan_time` | `main.py:101 datetime.now().strftime("%H:%M")` before ticker loop | Yes — live datetime at scan start | FLOWING |
| `discord_bot/bot.py ApproveRejectView.approve` | `limit_price_val` | `self.price` (yfinance scan-time price) when use_limit_buy=True | Yes — real yfinance price from tech_data | FLOWING |
| `database/queries.py create_trade` | `limit_price, order_type` | `limit_price_val, order_type_val` from approve handler | Yes — values flow from routing decision | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full test suite — all 444 tests pass | `pytest -q --tb=short` | `444 passed, 1 warning in 32.85s` | PASS |
| use_limit_buy defaults True from env | Import check via plan-documented pattern | `config.py:38` — `.lower() == "true"` with default `"true"` | PASS |
| Duration.GOOD_TILL_CANCEL in build_limit_buy | grep confirmed | `orders.py:30` — exactly 1 occurrence in build_limit_buy body | PASS |
| scan_time captured before ticker loop | Line-number comparison | `main.py:101` (scan_time) < `main.py:103` (`for ticker in universe:`) | PASS |
| ETF scope guard — no scan_time in ETF path | grep on embeds.py | `scan_time` appears only 3 lines: param (20), if-check (32), append (33) — all in build_recommendation_embed, not build_etf_recommendation_embed | PASS |
| "(limit, GTC)" in bot.py — exactly 1 line | grep confirmed | `bot.py:101` — only in the `elif self.config.use_limit_buy:` branch | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| RISK-01 | 17-01-PLAN.md, 17-02-PLAN.md | Limit buy order on Approve | SATISFIED | place_limit_order wired in ApproveRejectView.approve; build_limit_buy produces correct LIMIT+GTC spec; 5 unit tests green |
| RISK-02 | 17-01-PLAN.md, 17-02-PLAN.md | USE_LIMIT_BUY config flag | SATISFIED | config.py:38 — use_limit_buy field with correct .lower() pattern; market fallback in bot.py:81-82 |
| RISK-03 | 17-01-PLAN.md, 17-02-PLAN.md | limit_price/order_type in trades table | SATISFIED | models.py CREATE TABLE + ALTER TABLE; queries.py create_trade 9-column INSERT; bot.py passes both kwargs |
| RISK-04 | 17-02-PLAN.md | Scan-time price staleness in embed | SATISFIED | embeds.py scan_time kwarg + "\nas of HH:MM" append; main.py captures once before loop; 3 embed tests green |

No orphaned requirements — REQUIREMENTS.md traceability maps only RISK-01 through RISK-04 to Phase 17, and all 4 are claimed in plan frontmatter and verified above.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| None found | — | No TODOs, placeholders, empty returns, or hardcoded stub data in phase-modified files | — | — |

Specifically checked: no `TODO/FIXME/HACK` in modified files; no `return null/return []` in routing paths; no `console.log`-only handlers; limit_price_val and order_type_val are not hardcoded empty — they are conditionally set to real values on the live limit path.

### Human Verification Required

#### 1. Live GTC Limit Order Placement (Paper Trading)

**Test:** Enable USE_LIMIT_BUY=true (default) and PAPER_TRADING=true in .env. Start the bot, trigger a scan, wait for a BUY recommendation to appear in Discord, click Approve.
**Expected:** Schwab paper-trading endpoint receives a limit order with orderType=LIMIT and duration=GOOD_TILL_CANCEL at the scan-time price formatted as a 2-decimal string (e.g. "52.34"). Confirmation message reads "Approved: buying N share(s) of TICKER at $X.XX (limit, GTC)."
**Why human:** Unit tests mock the HTTP layer at `_call_place_order`. The schwab-py spec serialization and broker acceptance of the GTC duration field cannot be confirmed without a real API round-trip. A malformed spec would be rejected at the broker level, not caught by mocks.

#### 2. Discord Embed Price Field Visual Rendering

**Test:** In the same Discord channel where the BUY recommendation appears, observe the Price field value in the embed.
**Expected:** The Price field shows the dollar amount on the first line and "as of HH:MM" on the second line within the same embed field (not as two separate fields, not as a single concatenated string without visual break).
**Why human:** Discord embed field values render newlines differently across desktop, mobile, and web clients. The unit tests assert the Python string contains `\n` but cannot verify the final visual layout in Discord's UI renderer.

#### 3. USE_LIMIT_BUY=false Market Fallback (Live Path)

**Test:** Set USE_LIMIT_BUY=false in .env, restart the bot, trigger a scan, click Approve on a BUY embed.
**Expected:** Confirmation message reads "Approved: buying N share(s) of TICKER at $X.XX." (no "(limit, GTC)" suffix). Schwab receives a market order spec, not a limit order.
**Why human:** Tests verify the routing branch at the mock level. The market-order JSON spec must be confirmed as correctly submitted (not accidentally a limit spec) through a real Schwab paper-trading call.

### Gaps Summary

No gaps. All 5 roadmap success criteria are verified at the code level (exist, substantive, wired, data flowing). The 444-test suite passes with zero regressions. Three items are flagged for human verification because they involve the live Schwab API and Discord rendering — these are inherent to any external-service integration and are not implementation defects.

---

_Verified: 2026-05-19_
_Verifier: Claude (gsd-verifier)_
