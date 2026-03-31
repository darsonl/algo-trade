# Phase 1: Refactoring & Code Quality - Context

**Gathered:** 2026-03-31
**Status:** Ready for planning

<domain>
## Phase Boundary

Clean up the existing codebase so every subsequent phase builds on a solid foundation.
10 discrete refactoring changes (REF-01 to REF-10). No new user-facing features.
All existing tests must remain green after every change.

</domain>

<decisions>
## Implementation Decisions

### Function Signatures (REF-03, REF-02)
- **D-01:** `fetch_fundamental_info` and `fetch_technical_data` signatures change to accept `yf_ticker: yf.Ticker` as a **required** parameter. `run_scan` creates one `yf.Ticker(ticker)` object and passes it to both calls.
- **D-02:** Anthropic client (REF-02) uses the existing optional-param pattern — `analyze_ticker(ticker, client=None)` already accepts it. `run_scan` creates `anthropic.Anthropic()` once and passes it. No signature change needed.

### Config Singleton Removal (REF-01)
- **D-03:** Remove `config = Config()` from `config.py` (line 51 area). Only `main.py` imports the singleton — all other modules import only the `Config` class, so impact is isolated to one file.
- **D-04:** `main.py` instantiates `config = Config()` inside `main()` and immediately calls `config.validate()` as the first action — preserving fast-fail behavior.
- **D-05:** Tests are unaffected — they already create their own `Config()` instances with env overrides. No test imports the module-level singleton.

### DB Column Migration (REF-08)
- **D-06:** Use `ALTER TABLE recommendations ADD COLUMN earnings_growth REAL` in `init_db()`, wrapped in `try/except sqlite3.OperationalError` to handle both fresh and existing databases. No data loss.
- **D-07:** Existing rows will have `earnings_growth = NULL`. This is acceptable — NULL means "not recorded", not "zero growth". The column is for audit purposes only, not for filtering.

### Deferred Import Migration (REF-06)
- **D-08:** Move `import yfinance as yf`, `import anthropic`, and all other function-body imports to module top level. No comment needed near the imports — standard `mock.patch` behavior applies.
- **D-09:** Existing tests are unaffected — they test pure filter functions only, none of which call yfinance or anthropic. Future tests (Phase 4) will patch via `@mock.patch('screener.fundamentals.yf')` etc.

### manual_scan Event Replacement (REF-07)
- **D-10:** Replace `bot.dispatch('manual_scan')` + `on_manual_scan` handler with a direct `await` call to the scan function. No custom Discord.py event dispatch pattern remains.

### Claude's Discretion
- WAL mode PRAGMA details (REF-10): Implementation of `PRAGMA journal_mode=WAL` in `get_connection` — no additional pragmas (e.g., `busy_timeout`) required unless implementation discovers a need.
- Named constant naming (REF-04, REF-05): How to name `min_volume_ratio` and the min-data-points constant — follow existing Config field naming convention (`snake_case` matching env var counterpart).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs — requirements fully captured in decisions above.

### Project Files
- `.planning/REQUIREMENTS.md` — REF-01 to REF-10 definitions (authoritative source for all 10 changes)
- `.planning/ROADMAP.md` — Phase 1 deliverables and verification criteria
- `.planning/codebase/CONCERNS.md` — Full audit of each problem being fixed (technical debt section, refactoring opportunities section)
- `.planning/codebase/CONVENTIONS.md` — Established patterns to preserve (snake_case, pure functions, type hints, `@dataclass`)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `Config` dataclass (`config.py`): Well-structured — the only change is removing the module-level singleton instance.
- `passes_fundamental_filter`, `passes_technical_filter`: Pure functions untouched by this phase.
- `init_db()` in `database/models.py`: The right place to add the ALTER TABLE migration.

### Established Patterns
- All function signatures use type hints — new `yf_ticker: yf.Ticker` param must be typed.
- Pure functions already receive `config: Config` as a parameter — the same pattern applies to `yf_ticker`.
- Tests use `Config()` with field overrides via pytest fixtures — this pattern continues unchanged.

### Integration Points
- `run_scan()` in `main.py` is the call site for both fetch functions and `analyze_ticker` — all three refactoring changes (REF-02, REF-03) converge here.
- `init_db()` in `database/models.py` is called at startup — safe place for the migration guard.

</code_context>

<specifics>
## Specific Ideas

- No specific UI or API references — this is a backend-only refactoring phase.
- The scan should remain sequential (no parallelization) — that's v2 scope (PERF-01).

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 01-refactoring-code-quality*
*Context gathered: 2026-03-31*
