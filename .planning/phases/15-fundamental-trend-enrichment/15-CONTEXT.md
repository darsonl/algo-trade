# Phase 15: Fundamental Trend Enrichment - Context

**Gathered:** 2026-04-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Enrich the Claude BUY prompt with two valuation trajectory signals: P/E direction (expanding/contracting/stable/N/A derived from trailingPE vs forwardPE) and EPS quarterly trend (last 4 quarters from `quarterly_income_stmt`, chronological order). Scope is limited to `screener/fundamentals.py` (new `fetch_eps_data` function), `analyst/claude_analyst.py` (`build_prompt` signature + prompt text), `main.py` (new `asyncio.to_thread` call + `fundamental_trend` dict construction), and corresponding tests. No changes to the sell prompt, ETF prompt, Discord embeds, or DB schema.

</domain>

<decisions>
## Implementation Decisions

### P/E Direction Threshold
- **D-01:** Use a ±5% stable band. If `abs(forwardPE - trailingPE) / trailingPE < 0.05`, label is `"stable"`. Outside this band: `"expanding"` (forwardPE > trailingPE) or `"contracting"` (forwardPE < trailingPE). Tight band — small analyst estimate revisions don't flip the label.
- **D-02:** Label convention follows **valuation multiple direction** (not earnings momentum): `"expanding"` means the P/E ratio is growing (stock getting pricier relative to expected earnings); `"contracting"` means P/E ratio is shrinking (earnings expected to grow, stock getting cheaper per earnings dollar).
- **D-03:** When `forwardPE` is absent from yfinance info, label is `"N/A"` — no crash (per SC-3).

### EPS Trend Format
- **D-04:** Quarter labels + values format:
  ```
  - EPS trend (last 4Q): Q1-2025: $0.45, Q2-2025: $0.52, Q3-2025: $0.48, Q4-2025: $0.61
  ```
  Values in chronological order (oldest → newest), quarter label derived from the `pd.Timestamp` column keys in `quarterly_income_stmt`. Gives Claude both the trajectory and the period context.
- **D-05:** When `quarterly_income_stmt` returns `None`, is missing the "Diluted EPS" row, or returns fewer than 1 valid quarter, the EPS trend line is **omitted entirely** from the prompt (not shown as N/A). Per SC-4.

### Data Flow
- **D-06:** Add `fundamental_trend: dict | None = None` parameter to both `build_prompt()` and `analyze_ticker()`, mirroring the `macro_context` pattern. Clean separation: `info` = raw yfinance fields; `fundamental_trend` = computed enrichment (`pe_direction`, `eps_trend`).
  ```python
  # fundamental_trend shape:
  {
      "pe_direction": "contracting" | "expanding" | "stable" | "N/A",
      "eps_trend": [  # chronological, 1–4 entries; None if unavailable
          {"quarter": "Q1-2025", "eps": 0.45},
          ...
      ]
  }
  ```
- **D-07:** Add `fetch_eps_data(yf_ticker: yf.Ticker) -> list[dict] | None` in `screener/fundamentals.py`. Returns chronological list of `{"quarter": str, "eps": float}` dicts for last 4 quarters, or `None` on any error/missing data. Called from `main.py` via `asyncio.to_thread(fetch_eps_data, yf_ticker)` after the existing `fetch_fundamental_info` call.
- **D-08:** P/E direction is computed inline in `main.py` from `info` dict (`trailingPE` and `forwardPE`) — no separate fetch needed since both fields are already in `ticker.info`.

### Claude's Discretion
- Exact `build_prompt()` placement of the new `fundamental_trend` block relative to existing Fundamentals section (suggest inserting after the existing three fundamentals lines, before Market Context)
- Test strategy: new tests for `fetch_eps_data` in `test_screener_fundamentals.py`, or extend `test_analyst_claude.py` with `fundamental_trend` fixture variants

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Core files to modify
- `screener/fundamentals.py` — add `fetch_eps_data(yf_ticker)` function; existing `fetch_fundamental_info` unchanged
- `analyst/claude_analyst.py` — update `build_prompt()` and `analyze_ticker()` signatures; add `fundamental_trend` block to prompt text
- `main.py` — add `asyncio.to_thread(fetch_eps_data, yf_ticker)` call; compute `pe_direction` from `info`; pass `fundamental_trend` dict to `analyze_ticker`

### Test files to update
- `tests/test_screener_fundamentals.py` — add `fetch_eps_data` tests (None return, missing row, valid 4Q)
- `tests/test_analyst_claude.py` — add `fundamental_trend` fixture variants to `build_prompt` tests

### Reference implementations (read for patterns)
- `analyst/claude_analyst.py` lines 92–136 — existing `build_prompt()` with `macro_context` optional dict pattern to follow for `fundamental_trend`
- `analyst/claude_analyst.py` lines 259–300 — `analyze_ticker()` signature showing how `macro_context` is threaded through; `fundamental_trend` follows the same pattern
- `screener/fundamentals.py` lines 45–48 — `fetch_fundamental_info` as the pattern for the new `fetch_eps_data` function
- `main.py` lines 90–95 — `asyncio.to_thread(fetch_macro_context)` as the pattern for the new `asyncio.to_thread(fetch_eps_data, yf_ticker)` call

### Requirements
- `SIG-07` in `.planning/REQUIREMENTS.md` — P/E direction in Claude BUY prompt
- `SIG-08` in `.planning/REQUIREMENTS.md` — Quarterly EPS trend in Claude BUY prompt

### Success criteria (locked — do not renegotiate)
- SC-1: Claude BUY prompt contains P/E direction label (expanding / contracting / stable / N/A) from trailingPE vs forwardPE
- SC-2: Claude BUY prompt contains last 4 quarters EPS values in chronological order
- SC-3: When forwardPE is absent, P/E direction shows "N/A" (no crash)
- SC-4: When quarterly_income_stmt is None or missing "Diluted EPS" row, EPS trend line is omitted gracefully

### Prior research decisions (locked — from STATE.md v1.3 pre-phase)
- Use `ticker.quarterly_income_stmt` (NOT `ticker.quarterly_earnings`) — `quarterly_earnings` returns None silently in yfinance 1.2.0
- `quarterly_income_stmt` columns are newest-first `pd.Timestamp` — reverse before extracting last 4 quarters
- New yfinance attributes need `asyncio.to_thread` wrapping — not folded into `ticker.info`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `macro_context: dict | None = None` parameter pattern in `build_prompt()` and `analyze_ticker()` — exact same pattern for `fundamental_trend`
- `macro_context.get("key")` guard pattern in prompt builders — use same optional-field check for `fundamental_trend` keys
- `asyncio.to_thread(fetch_macro_context)` in `main.py` — same pattern for `asyncio.to_thread(fetch_eps_data, yf_ticker)`

### Established Patterns
- `screener/fundamentals.py` returns plain Python types (dict, bool) — `fetch_eps_data` returns `list[dict] | None` (consistent)
- All yfinance I/O in `async` functions uses `asyncio.to_thread` — mandatory here; `quarterly_income_stmt` is a blocking call
- Test fixtures for `build_prompt` tests mock `info` dict and `macro_context`; `fundamental_trend` adds a third optional fixture

### Integration Points
- `main.py` lines ~110–145: fundamental data fetch → filter → headlines fetch → `analyze_ticker`. The new `fetch_eps_data` call and `pe_direction` computation slot in between the existing `info` fetch and the `analyze_ticker` call.
- `analyze_ticker()` at line 277 calls `build_prompt(ticker, info, headlines, macro_context=macro_context)` — add `fundamental_trend=fundamental_trend` here.

</code_context>

<specifics>
## Specific Ideas

- Prompt layout preview (approved by user during discussion):
  ```
  Fundamentals:
  - Trailing P/E: 24.3
  - P/E Direction: contracting
  - EPS trend (last 4Q): Q1-2025: $0.45, Q2-2025: $0.52, Q3-2025: $0.48, Q4-2025: $0.61
  - Dividend Yield: 0.025
  - Earnings Growth: 0.12
  ```
- P/E direction and EPS trend both go in the `Fundamentals:` section of the prompt (not Market Context).
- The `fundamental_trend` dict is constructed in `main.py` after both `fetch_fundamental_info` and `fetch_eps_data` calls complete.

</specifics>

<deferred>
## Deferred Ideas

- P/E direction and EPS trend in SELL prompt — out of scope for Phase 15 (SIG-07 and SIG-08 are BUY-only per REQUIREMENTS.md traceability)
- EPS trend in ETF prompt — ETFs don't have quarterly EPS; out of scope by definition

</deferred>

---

*Phase: 15-fundamental-trend-enrichment*
*Context gathered: 2026-04-20*
