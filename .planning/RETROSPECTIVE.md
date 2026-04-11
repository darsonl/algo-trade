# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

---

## Milestone: v1.0 — MVP

**Shipped:** 2026-04-06
**Phases:** 7 (1, 2, 2.5, 3, 4, 5, 6) | **Plans:** 17 | **Timeline:** 9 days

### What Was Built

- Refactored codebase with clean Config injection, shared yf.Ticker, single Anthropic client per scan
- Structured logging + tenacity retry on all external APIs + S&P 500 JSON cache
- Gemini free-tier defense: analyst cache by SHA-256 news hash, top-10 S&P universe, 12s throttle, 429 retryDelay, Gemma 4 fallback
- Position tracking with weighted avg cost, exposure guard, open-position skip guard, `/positions` command
- Full sell pipeline: RSI+MACD two-gate exit, Claude SELL analysis, red Discord embeds, Schwab sell order
- Per-provider analyst daily quota guard (analyst_calls DB table)
- 222-test suite from near-zero meaningful coverage

### What Worked

- **Two-gate exit signal design**: requiring RSI overbought AND MACD bearish cross reduced noise significantly before implementation was even tested — good upfront reasoning
- **Phase 2.5 insertion**: inserting the Analyst Token Minimization phase mid-milestone when Gemini rate limits became a real problem was the right call; GSD's decimal phase numbering made this clean
- **Pure function discipline**: maintaining pure/stateless functions throughout made the 222-test suite achievable without complex mocking hierarchies
- **Fallback provider pattern**: using the same Gemini API key with Gemma 4 31B as fallback was zero-config for the operator and gave meaningful rate limit headroom
- **Incremental DB migrations**: additive-only schema changes meant never breaking production state across phases

### What Was Inefficient

- **Requirements never checked off**: 40 of 48 v1 requirements were completed but never formally ticked in REQUIREMENTS.md — the traceability table was stale from day one; requirement tracking should happen immediately when each phase lands, not saved for milestone close
- **Missing SUMMARY.md for early phases**: Phases 1, 2, 2.5, and 4-01 completed without SUMMARY.md files, making the gsd-tools milestone CLI unable to extract stats or accomplishments automatically; GSD discipline should be applied from the first plan
- **Pre-flight ordering error**: updated ROADMAP.md to the new format before running `milestone complete`, so the archived v1.0-ROADMAP.md captured the collapsed format rather than the original detailed phase breakdown; run the CLI *before* reorganizing
- **Production DB bootstrapping gap**: after Phase 2.5 and Phase 5 added new tables, the production DB wasn't automatically migrated — required manual `initialize_db` call. A startup migration check would prevent this class of production incident

### Patterns Established

- **Analyst cache by SHA-256 news hash**: idempotent rescan — same headlines → zero API calls; this pattern should be carried forward whenever LLM calls are gated on dynamic content
- **asyncio.to_thread for yfinance**: any synchronous call inside an async Discord handler must be wrapped; Backlog 999.1 / Phase 8 will formalize this as a comprehensive sweep
- **Decimal phase insertion**: Phase 2.5 demonstrated that mid-milestone phase insertion works cleanly with GSD's numbering — use it without hesitation when scope expands
- **Two-gate exit signals**: conjunction of RSI + MACD bearish is the baseline for any future exit logic; single-indicator exits should require strong justification

### Key Lessons

1. **Check off requirements as each phase lands** — don't leave traceability for milestone close; it's 2 minutes of work per phase and prevents 40-item debt at archival
2. **Write SUMMARY.md for every plan** — even simple plans; it costs almost nothing and makes gsd-tools automation useful
3. **Run `milestone complete` CLI before reorganizing ROADMAP.md** — the CLI reads ROADMAP.md to extract phase stats; updating it first corrupts the archive
4. **Startup DB migration check** — new tables added by phases should be verified present at bot startup via a lightweight schema check, not discovered in production

### Cost Observations

- Sessions: ~15 across 9 days
- Notable: Phase 2.5 was the highest-value insertion — prevented live API exhaustion before it became a blocker; the quota guard added in Phase 6 Plan 05 was the final layer of the same defense

---

## Milestone: v1.1 — ETF + Async

**Shipped:** 2026-04-11
**Phases:** 2 (7–8) | **Plans:** 4 | **Timeline:** 5 days (2026-04-07 → 2026-04-11) | **Commits:** 32

### What Was Built

- `partition_watchlist()` splits watchlist by yfinance quoteType with allowlist fallback; `etf_watchlist.txt` separates ETF universe; `asset_type` column added to recommendations table
- `build_etf_prompt` focused on RSI/MACD/expense ratio (no P/E or earnings growth); `analyze_etf_ticker` with same return shape + quota tracking as stock path
- `build_etf_recommendation_embed` with `[ETF]` title tag; `/scan_etf` Discord slash command + `run_scan_etf` coroutine; `_ETF_ALLOWLIST` removed from `fundamentals.py`
- All 9 blocking yfinance calls wrapped in `asyncio.to_thread` across buy, sell, and ETF passes — zero bare synchronous yfinance calls remain on the event loop
- 252 tests green (+30 from v1.0)

### What Worked

- **Separation over bypass**: creating a full ETF scan path (`run_scan_etf`) rather than hacking the stock fundamental filter with an ETF allowlist kept both paths clean and independently testable
- **Phase 8 placed last intentionally**: deferring the async sweep until after all scan paths (buy, sell, ETF) were implemented meant one comprehensive audit caught everything; wrapping calls incrementally per phase would have left gaps
- **SUMMARY.md discipline maintained**: all 4 plans have SUMMARY.md files with complete self-checks; gsd-tools automation worked cleanly (though accomplishment extraction failed due to format mismatch — see What Was Inefficient)
- **TDD red-green pattern**: Phase 7 Plans 01 and 02 followed strict RED commit → GREEN commit cycle; produced 30 new tests with zero rework

### What Was Inefficient

- **gsd-tools accomplishment extraction failed**: the CLI looked for a specific YAML `one_liner` field in SUMMARY.md frontmatter, but the summaries use a prose "One-liner:" heading; the MILESTONES.md entry had to be hand-corrected; either standardize the SUMMARY.md format or update the CLI extraction logic
- **plan_count reporting wrong (2 vs 4)**: gsd-tools `roadmap analyze` reported 2 plans (one per phase in ROADMAP.md `plan_count`) rather than counting actual plan files; the ROADMAP.md phase blocks listed `3 plans` and `1 plan` but the CLI parsed a different field; verify CLI output against disk when stats matter
- **STATE.md body not auto-updated**: the `milestone complete` CLI updated the YAML header correctly but left the body (Current Position, Phase Status table, Key Context) stale; body cleanup required a manual write pass
- **Requirements not checked off before archival**: same pattern as v1.0 — all 10 v1.1 requirements were shipped but unchecked in REQUIREMENTS.md; the archive copy needed manual checkbox updates

### Patterns Established

- **ETF scan as a first-class path**: `run_scan_etf` is structurally identical to `run_scan` minus the fundamental filter; any future asset class (options, bonds) should get its own scan coroutine, not a bypass flag
- **asyncio.to_thread at the call site**: wrapping at the call site (not inside the function) keeps functions pure/synchronous and testable; the caller owns the async boundary — carry this forward
- **P8-audit comments**: adding `# P8-audit: already wrapped` comments at previously-wrapped sites made the sweep unambiguous; use audit-comment patterns when doing comprehensive refactor sweeps

### Key Lessons

1. **Check off requirements immediately when each phase lands** — second time this happened; add it as a mandatory step in the phase completion checklist
2. **Verify gsd-tools stats against disk before archiving** — CLI plan counts and accomplishment extraction can silently fail; spot-check the output before trusting it
3. **STATE.md body needs manual cleanup at milestone close** — the CLI only updates the YAML header; budget time for a body rewrite pass
4. **ETF expense ratio is often None from yfinance** — `build_etf_prompt` and `build_etf_recommendation_embed` both handle None gracefully with "N/A"; document this yfinance quirk for future ETF features

### Cost Observations

- Sessions: ~6 across 5 days
- Notable: Phase 8 was the fastest phase (10 minutes, 1 file, 0 test changes) — the sweep approach and pre-existing asyncio.to_thread pattern made it mechanical; comprehensive upfront design in Phase 7 paid off

---

## Cross-Milestone Trends

| Metric | v1.0 | v1.1 |
|--------|------|------|
| Phases | 7 | 2 |
| Plans | 17 | 4 |
| Tests | 222 | 252 |
| Days | 9 | 5 |
| Commits | 81 | 32 |
| Req completion rate | 48/48 (40 not formally checked) | 10/10 (0 formally checked before archival) |
| Requirements-checked-off discipline | ❌ | ❌ — persistent pattern, needs process fix |
