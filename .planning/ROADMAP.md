# Roadmap: Algo Trade Bot

## Milestones

- ✅ **v1.0 MVP** — Phases 1-6 (shipped 2026-04-06)
- ✅ **v1.1 ETF + Async** — Phases 7-8 (shipped 2026-04-11)
- 🔄 **v1.2 Signal Quality & Portfolio Analytics** — Phases 9-13 (in progress)

---

## Phases

<details>
<summary>✅ v1.0 MVP (Phases 1-6) — SHIPPED 2026-04-06</summary>

- [x] Phase 1: Refactoring & Code Quality (1/1 plans) — completed 2026-03-31
- [x] Phase 2: Reliability & Error Handling (1/1 plans) — completed 2026-04-01
- [x] Phase 2.5: Analyst Token Minimization (4/4 plans) — completed 2026-04-02
- [x] Phase 3: Documentation (1/1 plans) — completed 2026-04-02
- [x] Phase 4: Test Coverage Expansion (2/2 plans) — completed 2026-04-03
- [x] Phase 5: Position Monitoring (3/3 plans) — completed 2026-04-03
- [x] Phase 6: Sell Signals & Sell Orders (5/5 plans) — completed 2026-04-06

See `.planning/milestones/v1.0-ROADMAP.md` for full phase details.

</details>

<details>
<summary>✅ v1.1 ETF + Async (Phases 7-8) — SHIPPED 2026-04-11</summary>

- [x] Phase 7: ETF Scan Separation (3/3 plans) — completed 2026-04-07
- [x] Phase 8: Asyncio Event Loop Fix (1/1 plan) — completed 2026-04-09

See `.planning/milestones/v1.1-ROADMAP.md` for full phase details.

</details>

### v1.2 Signal Quality & Portfolio Analytics

- [ ] **Phase 9: Ops Hardening** — Scan errors surface to Discord; ETF alert is distinguishable from stock alert
- [x] **Phase 10: Prompt Signal Enrichment** — Claude receives sector, macro, and price-range context in every analysis (completed 2026-04-12)
- [x] **Phase 11: Confidence Scoring** — Every Claude signal carries a confidence badge visible in Discord (completed 2026-04-12)
- [x] **Phase 12: ETF Polish** — ETF scan runs on its own schedule; high-cost ETFs are flagged in the embed (completed 2026-04-13)
- [ ] **Phase 13: Portfolio Analytics** — User can see aggregate P&L and closed-trade performance stats

---

## Phase Details

### Phase 9: Ops Hardening
**Goal:** Unhandled scan exceptions are posted to Discord so failures are never silently swallowed, and ETF scan zero-recommendation alerts are distinguishable from stock scan alerts.
**Depends on:** Phase 8
**Requirements:** OPS-01, ETF-08
**Success criteria:**
1. When an unhandled exception occurs during a stock or ETF scan, a message appears in the Discord ops channel within the same scan run — the bot does not log silently and continue.
2. A single scan run with multiple exceptions posts at most 3 error messages to Discord, preventing alert spam.
3. When the ETF scan completes with zero recommendations, the Discord ops alert message begins with `[ETF]`; the stock scan zero-recommendation alert does not carry this prefix.
**Plans:** 1 plan
Plans:
- [ ] 09-01-PLAN.md — Error posting + ETF prefix + tests

### Phase 10: Prompt Signal Enrichment
**Goal:** Claude's BUY, SELL, and ETF prompts include the ticker's sector, SPY trend direction, current VIX level, and 52-week price range position so Claude can factor sector and macro context into every recommendation.
**Depends on:** Phase 9
**Requirements:** SIG-01, SIG-02, SIG-03
**Success criteria:**
1. A BUY recommendation embed for any stock shows reasoning that references the ticker's sector (e.g. "Technology") — confirming sector context reached Claude.
2. A BUY or SELL recommendation shows reasoning that references SPY trend direction or VIX level — confirming macro context reached Claude.
3. A BUY or SELL recommendation shows reasoning that references whether the stock is near its 52-week high, low, or mid-range — confirming price-range context reached Claude.
4. An ETF recommendation includes SPY trend and VIX context in Claude reasoning (ETF path receives macro enrichment; sector and 52-week range are stock-path only).
**Plans:** 2/2 plans complete
Plans:
- [x] 10-01-PLAN.md — Macro fetcher + prompt builder enrichment
- [x] 10-02-PLAN.md — Wire macro through scan pipeline

### Phase 11: Confidence Scoring
**Goal:** Every Claude BUY/SELL/HOLD signal includes a confidence level (high/medium/low) that is displayed as a badge in the Discord embed; a missing confidence level does not break the embed or the scan.
**Depends on:** Phase 10
**Requirements:** SIG-04
**Success criteria:**
1. A Discord recommendation embed displays a confidence badge (e.g. "Confidence: High") when Claude returns a confidence level alongside the signal.
2. When Claude omits a confidence level, the embed posts without a badge and no error is raised — the badge is optional, not required.
3. The confidence level is persisted to the database so it is available for future queries without re-calling Claude.
**Plans:** 2/2 plans complete
Plans:
- [x] 11-01-PLAN.md — Prompt format + parse_claude_response + DB migration + tests
- [x] 11-02-PLAN.md — Embed badge + analyst return value + wiring

### Phase 12: ETF Polish
**Goal:** The ETF scan fires automatically at a configured time offset from the stock scan, and ETFs with an expense ratio above a configurable threshold are flagged in their Discord embed.
**Depends on:** Phase 8 (ETF scan must be working; independent of Phases 10-11)
**Requirements:** ETF-07, ETF-09
**Success criteria:**
1. At bot startup, APScheduler registers an ETF scan job that fires at a different clock time than the stock scan job (e.g. 9:30 AM vs 9:00 AM) — confirmed in scheduler startup logs without any manual `/scan_etf` invocation.
2. An ETF with an expense ratio above the configured threshold is visibly marked in its Discord embed (e.g. a field noting "High expense ratio") so the operator can factor cost before approving.
3. Adjusting the ETF scan schedule does not affect the stock scan schedule — both jobs fire independently at their respective configured times.
**Plans:** 2/2 plans complete
Plans:
- [x] 12-01-PLAN.md — ETF scheduler config + second configure_scheduler call + tests
- [x] 12-02-PLAN.md — Expense ratio flag in ETF embed + config threshold + wiring
**UI hint**: yes

### Phase 13: Portfolio Analytics
**Goal:** The `/positions` command shows total aggregate unrealized P&L across all open positions, and a new `/stats` slash command reports win rate, average gain percentage, and average loss percentage on all closed trades.
**Depends on:** Phase 8 (positions and trades tables must exist; independent of Phases 9-12)
**Requirements:** PORT-01, PORT-02
**Success criteria:**
1. The `/positions` embed footer displays a total unrealized P&L figure (e.g. "+$142.30 total unrealized") that aggregates all open positions — not just the per-position rows already shown.
2. Running `/stats` in Discord returns a message showing win rate percentage, average gain percentage on winning trades, and average loss percentage on losing trades.
3. `/stats` returns a clear message (e.g. "No closed trades yet") when the trades table has no closed records — it does not error or post an empty embed.
4. The `/stats` figures are computed from DB aggregation only — no external API call is made.
**Plans:** 1 plan
Plans:
- [ ] 13-01-PLAN.md — pnl_usd extension + positions footer + /stats command + DB migration + tests
**UI hint**: yes

---

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Refactoring & Code Quality | v1.0 | 1/1 | Complete | 2026-03-31 |
| 2. Reliability & Error Handling | v1.0 | 1/1 | Complete | 2026-04-01 |
| 2.5. Analyst Token Minimization | v1.0 | 4/4 | Complete | 2026-04-02 |
| 3. Documentation | v1.0 | 1/1 | Complete | 2026-04-02 |
| 4. Test Coverage Expansion | v1.0 | 2/2 | Complete | 2026-04-03 |
| 5. Position Monitoring | v1.0 | 3/3 | Complete | 2026-04-03 |
| 6. Sell Signals & Sell Orders | v1.0 | 5/5 | Complete | 2026-04-06 |
| 7. ETF Scan Separation | v1.1 | 3/3 | Complete | 2026-04-07 |
| 8. Asyncio Event Loop Fix | v1.1 | 1/1 | Complete | 2026-04-09 |
| 9. Ops Hardening | v1.2 | 0/1 | In progress | - |
| 10. Prompt Signal Enrichment | v1.2 | 2/2 | Complete   | 2026-04-12 |
| 11. Confidence Scoring | v1.2 | 2/2 | Complete   | 2026-04-12 |
| 12. ETF Polish | v1.2 | 2/2 | Complete   | 2026-04-13 |
| 13. Portfolio Analytics | v1.2 | 0/1 | Not started | - |

---
*Roadmap defined: 2026-03-30*
*Last updated: 2026-04-13 — Phase 13 plan list updated*
