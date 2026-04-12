# Phase 9: Ops Hardening - Context

**Gathered:** 2026-04-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Surface per-ticker scan exceptions to Discord so they are never silently swallowed, and make the ETF zero-recommendation alert distinguishable from the stock alert by adding a `[ETF]` prefix.

Requirements in scope: OPS-01, ETF-08.
Nothing else is in scope — no new alert channels, no behavioral changes to the scan loop.

</domain>

<decisions>
## Implementation Decisions

### Error Capture Scope
- **D-01:** ALL caught per-ticker exceptions (inside the `except Exception` blocks in `run_scan` and `run_scan_etf`) should be posted to Discord as ops alerts — not only exceptions that would crash the entire scan. The intent is operator visibility into any ticker that failed, even when the scan continues.
- **D-02:** Discord error message format: ticker + error type only. Example: `[ERROR] AAPL: ConnectionError`. Short, readable — error type is sufficient to diagnose the issue class.

### Batching & Cap
- **D-03:** Cap at 3 separate Discord error posts per scan run (one per error as they happen). Each is a separate `send_ops_alert()` call — no consolidated message.
- **D-04:** If total errors exceed 3, post one trailing overflow message **after all tickers are processed**: `[N more errors not shown — check logs]`. This is posted even though per-error messages are immediate — it's the only way to communicate total error count without waiting.

### Post Timing
- **D-05:** Post errors **immediately as they are caught** (first 3). A separate `error_count` accumulator tracks all errors including errors 4+. At end of scan, if `error_count > 3`, post the overflow message.

### ETF-08 (Pre-decided — trivial change)
- **D-06:** Add `[ETF]` prefix to the existing ETF zero-recommendation alert. Change: `"ETF scan complete: 0 recommendations posted."` → `"[ETF] ETF scan complete: 0 recommendations posted."`. The stock scan zero-rec alert must NOT have any prefix.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` §ETF-08, §OPS-01 — acceptance criteria for both requirements in this phase

### Existing code to read before touching
- `main.py` — `run_scan()` (lines ~55–175) and `run_scan_etf()` (lines ~287–410): both contain the per-ticker `except Exception` clauses to be modified and the existing `send_ops_alert()` calls for zero-rec alerts
- `discord_bot/bot.py` — `send_ops_alert()` implementation: must understand what it accepts before modifying callers

### Testing
- `.planning/codebase/TESTING.md` — existing test conventions; Phase 9 changes must maintain 252+ tests green

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `bot.send_ops_alert(message: str)` — already exists, already used for zero-rec alerts and DB abort alerts. Phase 9 just calls it more often (per caught error).
- `run_scan` per-ticker `except Exception as exc` at ~line 167: this is the exact insertion point for OPS-01 in the stock scan.
- `run_scan_etf` per-ticker `except Exception as exc` at ~line 402: ETF scan insertion point.

### Established Patterns
- Zero-rec alert for stock: `send_ops_alert("Scan complete: 0 recommendations posted.")` at end of scan.
- Zero-rec alert for ETF: `send_ops_alert("ETF scan complete: 0 recommendations posted.")` — this is the ETF-08 target.
- DB abort alert (ETF scan): `send_ops_alert(f"ETF scan aborted — DB schema error: {exc}")` — example of error-surfacing already in place.

### Integration Points
- The `error_count` accumulator and `errors_posted` counter need to be local variables initialized at the top of `run_scan` and `run_scan_etf`, incremented inside the per-ticker except block.
- Overflow message posted after the ticker loop ends (before the zero-rec check and sell pass).

</code_context>

<specifics>
## Specific Ideas

- Error message format confirmed: `[ERROR] {ticker}: {type(exc).__name__}` — error type only (e.g., `ConnectionError`, `KeyError`), not the full message string.
- Overflow message wording: `[X more errors not shown — check logs]` where X = total_errors - 3.
- ETF-08 is a one-line string change; no structural changes needed.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 09-ops-hardening*
*Context gathered: 2026-04-12*
