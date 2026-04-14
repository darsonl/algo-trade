# Phase 10: Prompt Signal Enrichment - Context

**Gathered:** 2026-04-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Add sector, SPY trend direction, VIX level, and 52-week price range position to Claude's BUY, SELL, and ETF prompts. Claude should be able to factor sector rotation, macro conditions, and momentum context into every recommendation.

Requirements in scope: SIG-01, SIG-02, SIG-03.
Out of scope: confidence scoring (SIG-04, Phase 11), ETF 52-week range (out-of-scope note in REQUIREMENTS.md), analyst cache invalidation, any behavioral filtering based on enriched signals.

</domain>

<decisions>
## Implementation Decisions

### Macro Data Fetch (SPY + VIX)

- **D-01:** Macro context lives in a new `screener/macro.py` module with a `fetch_macro_context()` function. Keeps macro logic separate and testable, consistent with the existing screener module pattern (fundamentals.py, technicals.py, exit_signals.py).
- **D-02:** Macro context is fetched **once at scan start**, before the ticker loop — in both `run_scan()` and `run_scan_etf()`. All tickers in one scan see the same macro snapshot. This matches how `get_top_sp500_by_fundamentals()` is fetched at scan start.
- **D-03:** `fetch_macro_context()` returns a dict: `{"spy_trend": str | None, "vix_level": str | None}` — fully formatted strings ready to inject into prompts.
- **D-04:** SPY trend expressed as direction label with percentage: `"Bullish (+3.2%)"` or `"Bearish (-1.4%)"`. Computed by comparing SPY 1-month return vs 0. Requires only a single SPY fast_info or history fetch.
- **D-05:** VIX level expressed as value + label: `"18.4 (Low volatility)"`. Thresholds: `< 20` = Low volatility, `20–30` = Elevated volatility, `> 30` = High volatility.

### Missing Data Policy

- **D-06:** If SPY/VIX fetch fails (any exception), log a warning and pass `None` macro context to prompt builders. Prompt builders omit the Market Context block entirely when macro is `None`. Scan continues without macro enrichment — no Discord alert, no scan abort.
- **D-07:** If sector is `None` or empty for a ticker, show `"N/A"` in the prompt: `"Sector: N/A"`. Keeps prompt format consistent across all tickers.
- **D-08:** If 52-week high/low is missing from yfinance info, show `"N/A"`: `"52-week range: N/A"`.

### Prompt Structure

- **D-09:** New context goes in a separate **`Market Context:`** block, inserted between the Fundamentals block and the Recent news headlines block in the BUY prompt. Same block pattern for the SELL prompt (after position data, before headlines).
- **D-10:** ETF prompt gets the same `Market Context:` block but with only SPY trend and VIX — no sector, no 52-week range (per SIG-02 scope and REQUIREMENTS.md out-of-scope note).
- **D-11:** Market Context block is **omitted entirely** (not shown with "N/A" placeholders) when macro context is unavailable (D-06). Sector and 52-week range lines are always present in stock prompts (using "N/A" per D-07/D-08 when data is missing).

### 52-Week Range Expression

- **D-12:** Position computed as: `pct = (current_price - low_52w) / (high_52w - low_52w)`. Labels: `>= 80%` → `"Near high"`, `<= 20%` → `"Near low"`, otherwise `"Mid-range"`. Displayed as: `"52-week range: Near high (87%)"`.
- **D-13:** 52-week high/low comes from `yf_ticker.info` fields `fiftyTwoWeekHigh` and `fiftyTwoWeekLow` — already fetched in `fetch_fundamental_info()`, no extra I/O needed.

### Signal Plumbing

- **D-14:** Sector is already available in `info["sector"]` from the existing `fetch_fundamental_info()` call. No extra yfinance fetch needed for sector.
- **D-15:** Macro context dict is passed as a new `macro_context` parameter to `build_prompt()`, `build_sell_prompt()`, and `build_etf_prompt()`. These remain pure functions (no I/O inside them).
- **D-16:** Sector and 52-week range are extracted from the `info` dict inside `build_prompt()` and `build_sell_prompt()`. The caller (`main.py`) passes the existing `info` dict — no new parameters needed for sector or 52-week range.

### Final Prompt Shape (BUY example)

```
Ticker: AAPL

Fundamentals:
- Trailing P/E: 28.5
- Dividend Yield: 0.5%
- Earnings Growth: 12%

Market Context:
- Sector: Technology
- SPY trend: Bullish (+3.2%)
- VIX: 18.4 (Low volatility)
- 52-week range: Near high (87%)

Recent news headlines:
- Apple beats Q1 estimates
...
```

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` §SIG-01, §SIG-02, §SIG-03 — acceptance criteria; note the ETF 52-week range out-of-scope callout in the "Out of Scope" section

### Existing code to read before touching
- `analyst/claude_analyst.py` — `build_prompt()`, `build_sell_prompt()`, `build_etf_prompt()`, `analyze_ticker()`, `analyze_sell_ticker()`, `analyze_etf_ticker()`: all prompt builders and their callers must be updated
- `main.py` — `run_scan()` (~lines 55–185) and `run_scan_etf()` (~lines 287–410): scan orchestration where macro fetch must be inserted at scan start, and macro context threaded through to analyst calls
- `screener/fundamentals.py` — `fetch_fundamental_info()`: confirms sector + 52-week data are in `yf_ticker.info` at no extra cost

### Existing modules as structural reference
- `screener/technicals.py` — Pattern reference for how a screener module is structured (pure compute functions + yfinance fetch function)
- `screener/exit_signals.py` — Pattern reference for a pure-function screener module with no yfinance I/O

### Testing
- `.planning/codebase/TESTING.md` — existing test conventions; Phase 10 must maintain 260+ tests green
- `CLAUDE.md` §Test Suite — test file naming and pytest conventions

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `fetch_fundamental_info(yf_ticker)` — already returns `info` dict containing `sector`, `fiftyTwoWeekHigh`, `fiftyTwoWeekLow`. Zero additional I/O for sector or 52-week range.
- `asyncio.to_thread(...)` wrapper pattern — all yfinance I/O must use this; the new `fetch_macro_context()` call in `run_scan` must follow this pattern.
- `send_ops_alert()` in `bot.py` — NOT needed here; macro fetch failures are logged only (D-06).

### Established Patterns
- **Pure prompt builders**: `build_prompt()`, `build_sell_prompt()`, `build_etf_prompt()` have no I/O. Keep them pure — pass fetched data in as parameters.
- **Module per concern**: fundamentals.py / technicals.py / exit_signals.py each own one responsibility. `screener/macro.py` follows the same convention.
- **asyncio.to_thread for yfinance**: Every yfinance call inside an async function is wrapped. `fetch_macro_context()` is sync; callers wrap it.

### Integration Points
- `run_scan()` and `run_scan_etf()` in `main.py`: each needs one `await asyncio.to_thread(fetch_macro_context)` call before the ticker loop, storing result as `macro_context`. Then `macro_context` is threaded into `analyze_ticker()` / `analyze_sell_ticker()` / `analyze_etf_ticker()`, which pass it into the prompt builders.
- `analyze_ticker()`, `analyze_sell_ticker()`, `analyze_etf_ticker()` signatures each gain a `macro_context: dict | None = None` parameter to forward to their prompt builder.

</code_context>

<specifics>
## Specific Ideas

- The BUY prompt preview shown during discussion is canonical for the Market Context block layout:
  ```
  Market Context:
  - Sector: Technology
  - SPY trend: Bullish (+3.2%)
  - VIX: 18.4 (Low volatility)
  - 52-week range: Near high (87%)
  ```
- SPY trend uses `yf.Ticker("SPY")` fast_info or 1-month history to compute the return.
- VIX uses `yf.Ticker("^VIX")` latest close.
- When macro is unavailable (D-06), the Market Context block is silently omitted — the prompt goes straight from Fundamentals to Recent news headlines.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 10-prompt-signal-enrichment*
*Context gathered: 2026-04-12*
