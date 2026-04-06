# Project Research Summary

**Project:** Algo Trade Bot — v1.1 ETF + Async
**Domain:** Discord-based automated trading bot with human-approval workflow
**Researched:** 2026-04-06
**Confidence:** HIGH

## Executive Summary

Algo Trade v1.1 adds two focused capabilities to a fully operational v1.0 system: a separate ETF scan pipeline and asyncio event loop hardening. The core insight from all four research areas is that the codebase already contains most of what v1.1 needs — the work is primarily structural separation, not net-new capability. The existing `_ETF_ALLOWLIST` in `fundamentals.py` is a stopgap that routes ETF tickers silently through a stock-oriented analyst prompt, producing low-quality recommendations. The fix is a proper `partition_watchlist` classification step and a dedicated `run_scan_etf` coroutine that skips the fundamental filter and uses an ETF-appropriate prompt. No new dependencies are required.

The recommended implementation order is Phase 7 (ETF scan separation) before Phase 8 (asyncio hardening). Phase 7 produces a visible working feature — operators can issue `/scan_etf` and see ETF-specific Discord embeds — while Phase 8 is a mechanical reliability pass that wraps 7 remaining blocking yfinance calls in `asyncio.to_thread`. Writing Phase 7's new code async-clean from day one means Phase 8 only touches existing `run_scan` call sites. The entire v1.1 scope is low-to-medium complexity and can reuse the existing test infrastructure, embed patterns, analyst client pipeline, and Discord bot command structure with minimal modification.

The primary risks are not architectural but operational: blocking `partition_watchlist` calls on the Discord event loop (causes gateway disconnects), double-wrapping already-wrapped functions during the Phase 8 audit (causes RuntimeError), and `earnings_growth=None` reaching embed formatting code (causes silent TypeError). All three have clear preventions documented in PITFALLS.md and must be handled before the respective phases ship.

---

## Key Findings

### Recommended Stack

No new packages are needed for v1.1. The existing stack — discord.py 2.4.0, yfinance 0.2.51, anthropic 0.40.0, schwab-py 1.4.0, APScheduler 3.10.4, pandas 2.2.3, SQLite — is fully adequate. The only stack-level decision is ETF detection method: `yfinance Ticker.info["quoteType"]` (returns `"ETF"` for all major ETFs, `"EQUITY"` for stocks) is the correct approach, replacing the current `_ETF_ALLOWLIST` hardcode. This field is present in the same `.info` dict already fetched for fundamentals, meaning classification adds zero extra network calls when called from `partition_watchlist`.

**Core technologies:**
- `yfinance 0.2.51` (pinned): market data + ETF classification via `quoteType` — do not upgrade; minor versions change `.info` schema
- `asyncio.to_thread` (stdlib 3.9+): blocking call offload — already used 3 times in `main.py`, pattern established
- `discord.py 2.4.0`: slash command registration + event loop ownership — ETF command mirrors existing `/scan` wiring exactly
- `anthropic 0.40.0` / fallback: LLM analyst — existing `_call_api` + fallback pipeline reused unchanged for ETF path

### Expected Features

**Must have (table stakes):**
- `partition_watchlist(tickers) -> (stocks, etfs)` — deterministic classification using `quoteType`; pure sync function, testable without mocks
- `build_etf_prompt(ticker, tech_data, headlines)` — ETF-specific prompt that injects trend/RSI/MACD/expense ratio instead of P/E/earnings growth
- `run_scan_etf(bot, config)` — standalone coroutine mirroring `run_scan` structure; skips fundamental filter, uses ETF prompt
- `/scan_etf` slash command — independent Discord command, never shares `_scan_callback` slot with `/scan`
- `asset_type TEXT NOT NULL DEFAULT 'stock'` DB column — additive migration so ETF and stock recs are queryable by type
- `build_etf_recommendation_embed` — embed variant showing Price, RSI, Trend, Expense Ratio rather than P/E and earnings growth
- `etf_watchlist.txt` — static ETF universe file the operator controls; no external ETF screener API needed

**Should have (differentiators):**
- Expense ratio surfaced in both the analyst prompt and Discord embed — ETFs with >0.50% expense ratio warrant operator attention
- Trend label ("Above MA50" / "Below MA50") in embed — more scannable than raw numbers
- Separate ops alert prefix `[ETF]` to distinguish ETF scan silence from stock scan silence
- Scheduled ETF scan at a time offset (e.g. 9:05 AM) to avoid yfinance rate-limit contention with the stock scan

**Defer (v2+):**
- `ETF_SCAN_TIMES` config field — operator can run `/scan_etf` manually until scheduled ETF scan is validated
- `asyncio.gather` for parallel ticker scanning — explicitly out of scope per PROJECT.md
- Dynamic ETF universe from an external API — static file is sufficient for single-operator use

### Architecture Approach

The architecture pattern for v1.1 is parallel pipelines with shared infrastructure, not branching on a mode flag. `run_scan` (stocks) and `run_scan_etf` (ETFs) are independent coroutines that share `fetch_technical_data`, `fetch_news_headlines`, `should_recommend`, quota guard logic, and the Discord posting path — but diverge at fundamental filter (ETFs skip it entirely) and analyst prompt (`build_etf_prompt` vs `build_prompt`). A single `run_full_scan` wrapper coroutine scheduled by APScheduler calls both in sequence, avoiding two competing scheduled jobs that would contend for yfinance quota.

**Major components introduced in v1.1:**
1. `screener/universe.py::partition_watchlist` — classifies universe into stocks and ETFs; I/O-bound, must be called via `asyncio.to_thread` at `main.py` call site
2. `analyst/claude_analyst.py::build_etf_prompt` + `analyze_etf_ticker` — ETF-specific prompt and analyst entry point; reuses `_call_api`, `parse_claude_response`, fallback pipeline unchanged
3. `main.py::run_scan_etf` — new coroutine parallel to `run_scan`; medium complexity because it must correctly copy quota guard and async wrapping patterns
4. `discord_bot/bot.py::/scan_etf` — new slash command with its own inline handler; does not touch `_scan_callback`
5. `asyncio.to_thread` wrappers on 7 remaining blocking calls in `run_scan` — Phase 8 mechanical pass; no logic changes

### Critical Pitfalls

1. **Blocking `partition_watchlist` on the event loop** — The function makes N yfinance `.info` calls; calling it without `asyncio.to_thread` blocks Discord's heartbeat, causing gateway disconnects. Wrap in `asyncio.to_thread` from the moment it is introduced in Phase 7, not deferred to Phase 8.

2. **Double-wrapping `get_top_sp500_by_fundamentals` during Phase 8 audit** — This call is already wrapped at `main.py` line 61. A second wrap raises `RuntimeError: no running event loop`. Phase 8 must start by listing all already-wrapped call sites and marking them in code with a comment.

3. **`earnings_growth=None` crashing embed formatting** — ETFs have no earnings growth. The embed formatter will be the first code path to receive a stock recommendation with `earnings_growth=None` in normal operation (the fundamental filter previously rejected such stocks before they reached this point). Audit `build_recommendation_embed` for None guards before Phase 7 ships.

4. **`/scan_etf` command overwriting `_scan_callback`** — The ETF slash command must register its own inline handler and call `run_scan_etf` directly. Reassigning the shared `_scan_callback` slot would silently redirect the `/scan` command to run ETF logic.

5. **Test mocks patching source module instead of `main.<fn>`** — All functions called via `asyncio.to_thread` in `main.py` must be patched at `main.<function_name>`, not at the source module. The existing `_full_patch` context manager in `test_run_scan.py` must include `partition_watchlist` from the moment Phase 7 adds it.

---

## Implications for Roadmap

Based on research, the v1.1 scope maps cleanly to two phases. Phase 7 delivers the ETF feature; Phase 8 delivers the async reliability hardening. Architecture and pitfalls research both confirm this ordering — Phase 7's new code should be written async-clean from day one, and Phase 8 then completes the sweep of existing blocking calls.

### Phase 7: ETF Scan Separation

**Rationale:** The ETF feature is the visible deliverable of v1.1. Building it first means operators can validate ETF recommendations while Phase 8 is in progress. All Phase 7 code is new (no migration of existing logic), so it can be written async-safe from the start rather than retrofitted.

**Delivers:** Working `/scan_etf` command, ETF-specific Discord embeds, clean separation of stock and ETF recommendation pipelines, removal of `_ETF_ALLOWLIST` stopgap.

**Addresses:** All table-stakes features — `partition_watchlist`, `build_etf_prompt`, `run_scan_etf`, `/scan_etf`, `asset_type` DB column, `build_etf_recommendation_embed`, `etf_watchlist.txt`.

**Avoids:**
- Call `partition_watchlist` inside `asyncio.to_thread` immediately (Pitfall: blocking event loop)
- Audit `build_recommendation_embed` for `None` guards before shipping (Pitfall: `earnings_growth=None` crash)
- Register `/scan_etf` with its own handler, never reuse `_scan_callback` (Pitfall: command slot collision)
- Add `asset_type` column migration in this phase, not deferred (Moderate pitfall: ambiguous historical queries)
- Add `partition_watchlist` mock to `_full_patch` in test suite immediately (Pitfall: mock target drift)

**Build order within Phase 7:**
1. `partition_watchlist` (pure function, test first)
2. `build_etf_prompt` + `analyze_etf_ticker` (pure function, test first)
3. DB migration: `asset_type` column
4. `build_etf_recommendation_embed`
5. `run_scan_etf` coroutine
6. `/scan_etf` slash command + `run_full_scan` wrapper
7. Remove `_ETF_ALLOWLIST` (only after partition is verified end-to-end)

### Phase 8: Asyncio Event Loop Hardening

**Rationale:** Phase 8 is a mechanical reliability pass with no user-visible feature change. Doing it after Phase 7 means the ETF path (new code) is already async-clean and Phase 8 only touches existing `run_scan` call sites — a tightly bounded change that is easy to validate.

**Delivers:** All remaining blocking yfinance calls in `run_scan` (buy pass and sell pass) wrapped in `asyncio.to_thread`, eliminating the risk of Discord heartbeat timeouts during stock scan execution.

**Addresses:** 7 blocking call sites — `yf.Ticker(ticker)` (×3), `fetch_fundamental_info`, `fetch_news_headlines` (×2), `fetch_technical_data` (×2).

**Avoids:**
- Begin by listing all already-wrapped call sites (lines 61, 112, 225) and adding `# already wrapped` comments before touching anything (Pitfall: double-wrap RuntimeError)
- Confirm all patched functions in `test_run_scan.py` use `main.<fn>` not source module path (Pitfall: mock target binding)
- No logic changes — this phase is wrap-only; if any logic change is tempted, defer it

**Research flags:** Phase 8 follows well-documented stdlib patterns (`asyncio.to_thread`) that are already validated in the codebase. No additional research needed.

### Phase Ordering Rationale

- Phase 7 before Phase 8 because Phase 7 produces a working operator-facing feature; Phase 8 is infrastructure hardening with no observable output
- Phase 7's new code written async-clean from the start means Phase 8 has a smaller, well-bounded scope
- Sequential coroutine execution (`run_full_scan` calls stock scan then ETF scan) avoids yfinance rate-limit contention from two concurrent APScheduler jobs
- `_ETF_ALLOWLIST` removal deferred to the end of Phase 7 (after `partition_watchlist` is verified) — removing it earlier would silently drop ETF tickers from the scan

### Research Flags

Phases that follow standard, well-documented patterns — no additional research phase needed:
- **Phase 7:** All implementation patterns are grounded in direct code inspection. `build_etf_prompt` mirrors `build_prompt`; `run_scan_etf` mirrors `run_scan`; `/scan_etf` mirrors `/scan`. The only external dependency (`quoteType` field) has a clear fallback. Confidence is HIGH.
- **Phase 8:** Pure stdlib `asyncio.to_thread` wrapping following an already-established pattern used 3 times in the codebase. No research needed.

One area warranting validation before finalizing ETF prompt fields:
- **ETF yfinance field availability** — The fields `totalAssets`, `annualReportExpenseRatio`, `ytdReturn`, `threeYearAverageReturn`, `category` are MEDIUM confidence (yfinance is an unofficial scraper). Print `yf.Ticker("SPY").info` and `yf.Ticker("QQQ").info` before finalizing `build_etf_prompt` to confirm which fields are actually present. Use `.get()` with `"N/A"` fallbacks for all of them.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All technologies are pinned and confirmed from `requirements.txt`. No new dependencies needed. `asyncio.to_thread` availability confirmed by 3 existing uses in codebase. |
| Features | HIGH | All findings derived from direct code inspection of `fundamentals.py`, `claude_analyst.py`, `main.py`, `bot.py`. The `_ETF_ALLOWLIST` fragility is confirmed at `screener/fundamentals.py` line 12. |
| Architecture | HIGH | Component responsibilities and data flow verified against actual code. Anti-patterns identified from real structural properties of `run_scan` and `_scan_callback`. |
| Pitfalls | HIGH | All 6 critical pitfalls are traceable to specific line numbers in the codebase. Mock patch target pitfall is confirmed by Python import semantics, not speculation. |

**Overall confidence: HIGH**

### Gaps to Address

- **ETF `.info` field availability (MEDIUM):** `annualReportExpenseRatio`, `ytdReturn`, `category`, and related ETF fields in yfinance are from training knowledge of the 0.2.x API. Validate against actual `SPY`/`QQQ`/`VTI` `.info` dicts before finalizing `build_etf_prompt`. Add `.get(field, "N/A")` fallbacks for every ETF-specific field — do not assume presence.

- **`quoteType` reliability under rate limiting (MEDIUM):** When yfinance returns a minimal/partial response (Yahoo Finance rate limit or scraper change), `quoteType` may be absent. The mitigation — `info.get("quoteType", "").upper() == "ETF"` combined with a static fallback allowlist — is documented in PITFALLS.md. Implement both layers.

- **ETF scan ops alert threshold:** The 0-recommendations ops alert will fire frequently for small ETF universes (2-3 tickers, all recommended today). Decide before Phase 7 ships whether to suppress the ETF scan ops alert, use a separate threshold, or add a "suppressed by dedup" counter to the alert message.

---

## Sources

### Primary (HIGH confidence)
- Direct codebase inspection — `main.py`, `screener/fundamentals.py`, `screener/universe.py`, `screener/technicals.py`, `analyst/claude_analyst.py`, `discord_bot/bot.py`, `discord_bot/embeds.py`, `database/models.py`, `database/queries.py`, `config.py`
- Direct test inspection — `tests/test_run_scan.py`, `tests/test_discord_buttons.py`, `tests/test_screener_fetchers.py`
- `.planning/PROJECT.md` — milestone scope, Out of Scope constraints, backlog items
- Python 3.9+ stdlib — `asyncio.to_thread` semantics, function reference binding at import time

### Secondary (MEDIUM confidence)
- yfinance 0.2.x training knowledge — `quoteType` field values (`"ETF"` / `"EQUITY"`), ETF `.info` fields (`totalAssets`, `annualReportExpenseRatio`, `ytdReturn`, `category`)
- APScheduler 3.10.4 + `run_coroutine_threadsafe` interaction pattern — confirmed by existing usage in `main.py` line 337

### Tertiary (LOW confidence — validate before use)
- Specific ETF `.info` field names (`annualReportExpenseRatio`, `threeYearAverageReturn`, `navPrice`) — present in training data but Yahoo Finance scraper responses vary; confirm with live `yf.Ticker("SPY").info` print before wiring into prompt

---
*Research completed: 2026-04-06*
*Ready for roadmap: yes*
