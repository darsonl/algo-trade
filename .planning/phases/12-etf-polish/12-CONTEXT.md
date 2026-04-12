# Phase 12: ETF Polish — Context

**Gathered:** 2026-04-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Add an automatically scheduled ETF scan job that fires at a configurable time offset from the stock scan, and flag high-expense ETFs visually in their Discord embed.

Requirements in scope: ETF-07, ETF-09.
Out of scope: ETF_SCAN_TIMES multi-time support (deferred per REQUIREMENTS.md), expense ratio filtering/skipping, any behavioral changes to the scan pipeline, ETF 52-week range.

</domain>

<decisions>
## Implementation Decisions

### ETF-07: Scheduled ETF Scan

- **D-01:** ETF scan schedule is configured via two new env vars: `ETF_SCAN_HOUR` and `ETF_SCAN_MINUTE`. Mirrors the existing `SCAN_HOUR`/`SCAN_MINUTE` pattern exactly. Default: `ETF_SCAN_HOUR=9`, `ETF_SCAN_MINUTE=30` (30-minute offset from stock scan default of 09:00).
- **D-02:** Config gains a new field `etf_scan_times: list` built by a `_parse_etf_scan_times()` helper — mirrors `_parse_scan_times()`. Single-time list only (no `ETF_SCAN_TIMES` multi-time support yet).
- **D-03:** `configure_scheduler` is reused for the ETF job. Called a second time in `on_ready` with the ETF job function. ETF job IDs use `etf_scan_N` prefix (vs `scan_N` for stock) to avoid collisions.
- **D-04:** Startup log message for ETF scheduler: `"ETF scheduler started — daily ETF scan at %02d:%02d"` — mirrors the existing stock scan startup log pattern.

### ETF-09: Expense Ratio Flagging

- **D-05:** Flag behavior is **display-only** — no skip/filter. ETFs above threshold are still posted to Discord; the operator decides whether to approve or reject.
- **D-06:** The existing `Expense Ratio` embed field value is modified to include a warning when above threshold: `"⚠️ 0.0075 (High)"` instead of `"0.0075"`. No new field added. When at or below threshold, the existing plain format is used unchanged.
- **D-07:** The flag logic lives in `build_etf_recommendation_embed()` in `discord_bot/embeds.py`. A new `etf_max_expense_ratio: float | None` parameter is added to the embed builder. When `None`, no flagging occurs (backward-compatible). When provided, comparison is: `expense_ratio > etf_max_expense_ratio`.
- **D-08:** `config.etf_max_expense_ratio` is passed through from `run_scan_etf()` → `build_etf_recommendation_embed()`. No changes to analyst path.

### ETF-09: Threshold Configuration

- **D-09:** Env var: `ETF_MAX_EXPENSE_RATIO`. Config field: `etf_max_expense_ratio: float = float(os.getenv("ETF_MAX_EXPENSE_RATIO", "0.005"))`. Default: `0.005` (0.5%). Matches Config naming convention (`max_pe_ratio`, `min_dividend_yield`, etc.).

### Claude's Discretion

- Test coverage: unit tests for `_parse_etf_scan_times()`, `configure_scheduler` ETF job ID behavior, and `build_etf_recommendation_embed` expense ratio flag formatting.
- `.env.example` update: add `ETF_SCAN_HOUR`, `ETF_SCAN_MINUTE`, `ETF_MAX_EXPENSE_RATIO` with defaults and comments.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` §ETF-07 — scheduled ETF scan acceptance criteria
- `.planning/REQUIREMENTS.md` §ETF-09 — expense ratio threshold flagging acceptance criteria

### Key source files
- `main.py` — `configure_scheduler`, `on_ready` scheduler setup, `run_scan_etf`
- `config.py` — `Config` dataclass, `_parse_scan_times` helper pattern, existing scheduler fields
- `discord_bot/embeds.py` — `build_etf_recommendation_embed` (expense ratio field, parameter signature)
- `.env.example` — env var documentation

### Prior context
- `.planning/phases/09-ops-hardening/09-CONTEXT.md` — establishes the single-scan-job pattern (one configure_scheduler call); ETF-07 adds a second parallel call
- `.planning/phases/11-confidence-scoring/11-CONTEXT.md` — D-02 shows how embed fields are added conditionally; same pattern for expense ratio flag

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `configure_scheduler(scheduler, config, job_fn)` — already job-fn-agnostic; call with `etf_job_fn` and `config.etf_scan_times`
- `_parse_scan_times()` helper in `config.py` — clone for `_parse_etf_scan_times()` reading `ETF_SCAN_HOUR`/`ETF_SCAN_MINUTE`
- `build_etf_recommendation_embed` — already accepts `expense_ratio: float | None`; extend with `etf_max_expense_ratio` param

### Established Patterns
- All Config thresholds are `float = float(os.getenv("VAR", "default"))` — follow exactly
- Scheduler job IDs: `scan_0`, `scan_1` for stock — use `etf_scan_0` for ETF
- Embed field formatting: conditional `if x is not None` before `.add_field()` — flag logic fits same pattern

### Integration Points
- `main.py:on_ready` — add second `configure_scheduler` call after the stock scheduler call
- `main.py:run_scan_etf` — pass `config.etf_max_expense_ratio` to `build_etf_recommendation_embed`
- `discord_bot/embeds.py:build_etf_recommendation_embed` — add `etf_max_expense_ratio: float | None = None` parameter

</code_context>

<specifics>
## Specific Ideas

- Embed flag format: `"⚠️ 0.0075 (High)"` — the ⚠️ emoji makes it scannable at a glance in Discord
- ETF default scan time: 9:30 AM (30-minute offset from stock scan 9:00 AM) — avoids yfinance rate-limit contention with the stock scan, which aligns with Phase 7's original ETF separation motivation

</specifics>

<deferred>
## Deferred Ideas

- `ETF_SCAN_TIMES` (multi-time ETF scheduling) — explicitly deferred per REQUIREMENTS.md until scheduled ETF scan is validated
- Expense ratio filtering/skipping (not posting the embed at all) — user chose display-only flag; skip path deferred

</deferred>

---

*Phase: 12-etf-polish*
*Context gathered: 2026-04-12*
