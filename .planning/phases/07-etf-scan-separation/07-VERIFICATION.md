---
phase: 07-etf-scan-separation
verified: 2026-04-07T14:00:00Z
status: passed
score: 9/9 must-haves verified
gaps: []
deferred: []
---

# Phase 7: ETF Scan Separation — Verification Report

**Phase Goal:** Give ETFs their own scan path — `/scan_etf` Discord command — so the stock screener logic stays clean and ETF analysis isn't hamstrung by stock-centric fundamental filters.
**Verified:** 2026-04-07
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `partition_watchlist` exists in `screener/universe.py` with `_ETF_ALLOWLIST` fallback | VERIFIED | Lines 15, 46-79 of `screener/universe.py` |
| 2 | `etf_watchlist.txt` exists and contains ETF tickers | VERIFIED | File contains 10 tickers: SPY, QQQ, VTI, IVV, VOO, VEA, BND, GLD, XLK, SCHD |
| 3 | `database/models.py` has `asset_type` column in DDL and ALTER migration | VERIFIED | Line 29 in CREATE TABLE, lines 96-101 in ALTER migration block |
| 4 | `analyst/claude_analyst.py` has `build_etf_prompt` and `analyze_etf_ticker` | VERIFIED | Lines 117-163 and 254-303 respectively |
| 5 | `discord_bot/embeds.py` has `build_etf_recommendation_embed` with `[ETF]` title tag | VERIFIED | Lines 42-80; title uses `f"{ticker} — {signal} [ETF]"` |
| 6 | `main.py` has `run_scan_etf` and calls `partition_watchlist` inside `run_scan` | VERIFIED | `run_scan_etf` lines 279-396; `partition_watchlist` called at line 69 in `run_scan` |
| 7 | `discord_bot/bot.py` has `/scan_etf` command, `send_etf_recommendation`, `_scan_etf_callback` | VERIFIED | `name="scan_etf"` lines 175-179; `send_etf_recommendation` lines 243-259; `_scan_etf_callback` lines 155, 196 |
| 8 | `screener/fundamentals.py` has NO ETF bypass (`_ETF_ALLOWLIST` or `if info.get("symbol"…)`) | VERIFIED | `fundamentals.py` contains no `_ETF_ALLOWLIST`, no symbol bypass; starts directly with `pe = info.get("trailingPE")` at line 20 |
| 9 | Full test suite passes — 247 tests green | VERIFIED | `pytest -q` output: 247 passed, 1 warning |

**Score:** 9/9 truths verified

---

### Required Artifacts

| Artifact | Provides | Status | Details |
|----------|----------|--------|---------|
| `screener/universe.py` | `partition_watchlist`, `_ETF_ALLOWLIST` | VERIFIED | Substantive implementation; wired into `main.py` at line 69 |
| `etf_watchlist.txt` | 10 ETF tickers for `/scan_etf` | VERIFIED | Populated with real tickers; read by `run_scan_etf` via `get_watchlist` |
| `database/models.py` | `asset_type` DDL column + ALTER migration | VERIFIED | In both CREATE TABLE DDL and idempotent ALTER block |
| `database/queries.py` | `create_recommendation(…, asset_type="stock")` | VERIFIED | `asset_type` param present; `"etf"` passed at `main.py` line 371 |
| `analyst/claude_analyst.py` | `build_etf_prompt`, `analyze_etf_ticker` | VERIFIED | Substantive — ETF-specific prompt (RSI/MACD/expense ratio; no P/E/yield/earnings growth); full fallback provider pattern |
| `discord_bot/embeds.py` | `build_etf_recommendation_embed` | VERIFIED | Substantive — 4 fields (Price, RSI, MA50 Trend, Expense Ratio), `[ETF]` tag, `_SIGNAL_COLORS` reuse |
| `main.py` | `run_scan_etf`, ETF bypass removal | VERIFIED | Full ETF pipeline; `partition_watchlist` filters ETFs from stock universe |
| `discord_bot/bot.py` | `/scan_etf` command, `send_etf_recommendation` | VERIFIED | Command registered in `setup_hook`; `send_etf_recommendation` uses `ApproveRejectView` with `price or 0.0` fallback |
| `watchlist.txt` | Stocks-only (no ETF tickers) | VERIFIED | File contains only comments; no ETF tickers present |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `run_scan` in `main.py` | `partition_watchlist` | `await asyncio.to_thread(partition_watchlist, universe)` | WIRED | Line 69 of `main.py` |
| `run_scan_etf` in `main.py` | `analyze_etf_ticker` | `await asyncio.to_thread(analyze_etf_ticker, …)` | WIRED | Lines 344-347 of `main.py` |
| `run_scan_etf` in `main.py` | `bot.send_etf_recommendation` | direct `await` call | WIRED | Lines 374-383 of `main.py` |
| `run_scan_etf` | `queries.create_recommendation` with `asset_type="etf"` | direct call | WIRED | Lines 363-372 of `main.py` |
| `bot.send_etf_recommendation` | `build_etf_recommendation_embed` | imported and called | WIRED | `bot.py` line 13 import; line 256 call |
| `main()` | `bot._scan_etf_callback` | lambda assignment | WIRED | `main.py` line 434 |
| `/scan_etf` command | `_scan_etf_callback()` | `asyncio.create_task` | WIRED | `bot.py` lines 195-196 |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `run_scan_etf` | `etf_tickers` | `get_watchlist("etf_watchlist.txt")` — reads real file | Yes — 10 real ETF tickers | FLOWING |
| `run_scan_etf` | `etfs` | `partition_watchlist(etf_tickers)` — yfinance quoteType queries | Yes — real yfinance calls with allowlist fallback | FLOWING |
| `run_scan_etf` | `tech_data` | `fetch_technical_data(yf_ticker)` — yfinance price history | Yes — real data | FLOWING |
| `run_scan_etf` | `expense_ratio` | `info.get("annualReportExpenseRatio")` — yfinance info | Yes — real data, None-safe | FLOWING |
| `run_scan_etf` | `analysis` | `analyze_etf_ticker` — real API call via `_call_api` | Yes — real LLM call, cache-backed | FLOWING |
| `run_scan` (stock pass) | `universe` (post-filter) | `partition_watchlist(universe)` — ETFs excluded upstream | Yes — stock-only after ETF removal | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Verification Method | Result | Status |
|----------|---------------------|--------|--------|
| `partition_watchlist` returns two lists | Code inspection: returns `(stocks, etfs)` tuple | Clear two-path logic with ETF/non-ETF routing | PASS |
| `build_etf_prompt` excludes stock fundamentals | Code inspection: prompt template contains RSI/MACD/Expense Ratio, not P/E/dividends/earnings | Confirmed at lines 144-162 of `claude_analyst.py` | PASS |
| `_ETF_ALLOWLIST` removed from `fundamentals.py` | Code inspection: file contains no such symbol | Confirmed — `fundamentals.py` is 41 lines, starts with `pe = info.get("trailingPE")` | PASS |
| All 247 tests pass | `python -m pytest -q` | 247 passed, 1 warning (unrelated websockets deprecation) | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Status | Evidence |
|-------------|-------------|--------|----------|
| ETF-01: partition_watchlist splits universe by quoteType | 07-01 | SATISFIED | `screener/universe.py` lines 46-79 |
| ETF-02: run_scan_etf skips fundamental filter | 07-03 | SATISFIED | `main.py` `run_scan_etf` has no `passes_fundamental_filter` call |
| ETF-03: ETF analyst prompt (no P/E context) | 07-02 | SATISFIED | `build_etf_prompt` omits P/E, dividend yield, earnings growth |
| ETF-04: ETF Discord embed with `[ETF]` tag | 07-02 | SATISFIED | `build_etf_recommendation_embed` title uses `[ETF]` |
| ETF-05: `/scan_etf` slash command | 07-03 | SATISFIED | Registered in `setup_hook`, wired to `run_scan_etf` via callback |
| ETF-06: ETF recommendations use same `recommendations` table | 07-01/03 | SATISFIED | `create_recommendation` called with `asset_type="etf"`; same schema |

---

### Anti-Patterns Found

None. No TODOs, placeholder returns, empty handlers, or hardcoded stubs were found in any phase-7 files.

---

### Human Verification Required

None. All goal-critical behaviors are verifiable from the codebase.

The following are observable only at runtime and are noted as informational (not blocking):

1. **`/scan_etf` Discord interaction** — visually confirm the ETF embed posts with `[ETF]` tag and Approve/Reject buttons render correctly in the actual Discord channel.
   - Expected: embed title `"SPY — BUY [ETF]"`, green color, 4 inline fields (Price, RSI, MA50 Trend, Expense Ratio), functional Approve/Reject buttons.
   - Why human: requires live Discord channel and bot connection; cannot verify programmatically without running the bot.

2. **ETF scan ops alert on zero recs** — confirm `send_ops_alert` posts to Discord when `run_scan_etf` finds no BUY signals.
   - Expected: `[OPS ALERT] ETF scan complete: 0 recommendations posted.` message appears in channel.
   - Why human: same reason — requires live bot.

---

### Gaps Summary

No gaps. All 9 observable truths are verified. The phase goal is fully achieved:

- ETFs have their own scan path (`/scan_etf` → `run_scan_etf`)
- Stock scan (`/scan` → `run_scan`) is clean — ETFs filtered upstream by `partition_watchlist`, `passes_fundamental_filter` has no ETF bypass
- ETF analyst prompt is ETF-specific (RSI, MACD, expense ratio; no P/E, no earnings growth)
- ETF Discord embeds use `[ETF]` tag with ETF-appropriate fields
- DB stores `asset_type="etf"` for ETF recommendations; existing schema unchanged
- 247 tests pass

---

_Verified: 2026-04-07_
_Verifier: Claude (gsd-verifier)_
