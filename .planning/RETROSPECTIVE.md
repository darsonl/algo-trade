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

## Cross-Milestone Trends

| Metric | v1.0 |
|--------|------|
| Phases | 7 |
| Plans | 17 |
| Tests | 222 |
| LOC | 15,278 |
| Days | 9 |
| Commits | 81 |
| Req completion rate | 48/48 (40 not formally checked) |
