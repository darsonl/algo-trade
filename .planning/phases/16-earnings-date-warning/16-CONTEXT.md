# Phase 16: Earnings Date Warning - Context

**Gathered:** 2026-04-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Add earnings date awareness to the BUY embed (SIG-05) and Claude BUY prompt (SIG-06). The data source is `info["earningsTimestamp"]` — already present in the `fetch_fundamental_info` result dict, so zero extra network cost. When within 7 days, the embed field is visually flagged. ETF scan path skips earnings entirely. No changes to the sell prompt, ETF prompt, DB schema, or non-BUY embed paths.

</domain>

<decisions>
## Implementation Decisions

### Embed Field — Format
- **D-01:** Display format is `Dec 15, 2025` — human-readable month name, no days-remaining count in the value.
- **D-02:** Past timestamps (epoch < today) are suppressed to `"N/A"` — same treatment as absent data, consistent with how P/E Ratio and Dividend Yield handle missing values.
- **D-03:** Absent `earningsTimestamp` (key missing or `None`) also renders as `"N/A"`.

### Embed Field — Position
- **D-04:** "Next Earnings" field is added **after Confidence** in `build_recommendation_embed`. When Confidence is absent, it follows P/E Ratio directly. It is the last field in the BUY embed.
- **D-05:** Field is inline (`inline=True`) — consistent with all other fundamental fields.

### Embed Field — 7-Day Warning
- **D-06:** When the earnings date is within 7 calendar days (strictly: `0 <= days_until_earnings < 7`), the field value is prefixed with `⚠️` — e.g., `⚠️ Dec 18, 2025`. Field name stays "Next Earnings". No extra embed fields consumed, no color change (embed color is locked to BUY green).
- **D-07:** The 7-day threshold is the boundary specified in SC-2. At exactly 7 days out, no warning (warning triggers at < 7 days).

### Claude Prompt — Placement
- **D-08:** The earnings date line goes in the `Market Context:` section of the BUY prompt — it is a timing risk signal, not a valuation metric. Suggested line:
  ```
  - Next Earnings: Dec 15, 2025
  ```
  When within 7 days, include proximity note:
  ```
  - Next Earnings: Dec 18, 2025 (in 6 days — proximity risk)
  ```
  When absent or past: omit the line entirely (do not emit "N/A" to Claude — omission is cleaner than a non-actionable signal).

### Data Threading
- **D-09:** `earnings_date` is extracted inline in `main.py` from the existing `info` dict — same pattern as `pe_direction` (D-08 of Phase 15). No new `asyncio.to_thread` call needed.
- **D-10:** Add `earnings_date: str | None` parameter to `build_recommendation_embed()` and `build_prompt()` / `analyze_ticker()` — follows the keyword-arg optional pattern established by `confidence`, `macro_context`, and `fundamental_trend`. Default `None` preserves all existing call sites.
- **D-11:** ETF path (`build_etf_recommendation_embed`, `build_etf_prompt`, `analyze_etf_ticker`) receives no `earnings_date` argument — callers in `run_scan_etf` do not extract or pass it.

### Claude's Discretion
- Whether `earnings_date` is added to the `fundamental_trend` dict or passed as a separate parameter — either is consistent with prior patterns; planner picks the cleaner fit.
- Exact `datetime` conversion logic for Unix epoch → `"Dec 15, 2025"` string (UTC date is acceptable; no timezone localization required).
- Test strategy: new tests in `tests/test_discord_embeds.py` for the warning/N/A cases, and `tests/test_analyst_claude.py` for `earnings_date` prompt injection.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Core files to modify
- `discord_bot/embeds.py` — `build_recommendation_embed()`: add `earnings_date: str | None = None` kwarg, add "Next Earnings" field after Confidence with ⚠️ prefix logic
- `analyst/claude_analyst.py` — `build_prompt()` and `analyze_ticker()`: add `earnings_date: str | None = None` kwarg; inject into `Market Context:` block
- `main.py` — extract `earningsTimestamp` from `info` dict, convert to display string, pass to `analyze_ticker` and embed builder

### Test files to update
- `tests/test_discord_embeds.py` — earnings date field: present/absent, past/future, within-7-days warning
- `tests/test_analyst_claude.py` — `build_prompt` with `earnings_date` present, absent, and within-7-days variants

### Reference implementations (read for patterns)
- `discord_bot/embeds.py` lines 40–42 — `confidence` optional field pattern: `if confidence is not None: embed.add_field(...)` — same pattern for "Next Earnings"
- `discord_bot/embeds.py` lines 83–88 — `etf_max_expense_ratio` optional field + flag logic — analogous to earnings ⚠️ flag
- `analyst/claude_analyst.py` lines 92–136 — `build_prompt()` with `macro_context` optional dict — `earnings_date` follows same optional kwarg approach
- `main.py` lines 114–142 — Phase 15 `pe_direction` inline computation from `info` dict — `earningsTimestamp` extraction follows same pattern
- `main.py` line 273 — `build_recommendation_embed(ticker, signal, reasoning, price, dividend_yield, pe_ratio, confidence=confidence)` — call site to extend with `earnings_date=earnings_date`

### Requirements
- `SIG-05` in `.planning/REQUIREMENTS.md` — BUY embed displays next earnings date field
- `SIG-06` in `.planning/REQUIREMENTS.md` — Next earnings date in Claude BUY prompt

### Success criteria (locked — do not renegotiate)
- SC-1: BUY embed displays a "Next Earnings" field showing the date (or "N/A" when absent or past)
- SC-2: When earnings are within 7 days, the embed field is prefixed with ⚠️
- SC-3: Claude BUY prompt includes the next earnings date in Market Context so it can factor in proximity risk
- SC-4: ETF scan path skips the earnings date field entirely — no empty or erroring field on ETF embeds

### Prior research decisions (locked — from STATE.md v1.3 pre-phase)
- Earnings date sourced from `info["earningsTimestamp"]` (already fetched by `fetch_fundamental_info`) — zero extra HTTP call

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `if confidence is not None: embed.add_field(...)` pattern in `build_recommendation_embed` — exact template for the "Next Earnings" optional field
- `macro_context: dict | None = None` keyword arg pattern in `build_prompt` / `analyze_ticker` — same optional threading for `earnings_date: str | None = None`
- `pe_direction` inline computation in `main.py` (lines 117–126) — same pattern for extracting and converting `earningsTimestamp` from `info`

### Established Patterns
- All optional embed fields use `inline=True` — "Next Earnings" follows suit
- Optional prompt fields are injected conditionally (`if macro_context and macro_context.get("key"):`) — `earnings_date` follows same guard
- ETF embed and prompt builders receive no fundamental arguments — earnings continues this exclusion

### Integration Points
- `main.py` line 273: `build_recommendation_embed(...)` — add `earnings_date=earnings_date` kwarg
- `main.py` ~line 150: `analyze_ticker(...)` call — add `earnings_date=earnings_date` kwarg
- `analyst/claude_analyst.py` `build_prompt` Market Context block — insert earnings date line after SPY/VIX lines

</code_context>

<specifics>
## Specific Ideas

- Embed field value examples:
  - Normal: `Dec 15, 2025`
  - Warning: `⚠️ Dec 18, 2025`
  - Absent or past: `N/A`
- Claude prompt line examples:
  - Normal: `- Next Earnings: Dec 15, 2025`
  - Within 7 days: `- Next Earnings: Dec 18, 2025 (in 6 days — proximity risk)`
  - Absent/past: line omitted entirely from prompt (not emitted as N/A)
- Field name: `"Next Earnings"` (consistent with BUY embed field naming style)

</specifics>

<deferred>
## Deferred Ideas

- Earnings BMO/AMC timing (before/after market open) in embed — noted in REQUIREMENTS.md Future Requirements; out of scope for Phase 16
- Earnings date in SELL prompt — sell path has no earnings proximity use case; not scoped
- Earnings date in ETF prompt — ETFs don't report quarterly earnings the same way; excluded by SC-4

</deferred>

---

*Phase: 16-earnings-date-warning*
*Context gathered: 2026-04-24*
