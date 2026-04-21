# Roadmap: Algo Trade Bot

## Milestones

- ✅ **v1.0 MVP** — Phases 1-6 (shipped 2026-04-06)
- ✅ **v1.1 ETF + Async** — Phases 7-8 (shipped 2026-04-11)
- ✅ **v1.2 Signal Quality & Portfolio Analytics** — Phases 9-13 (shipped 2026-04-14)
- 🔄 **v1.3 Risk & Signal Quality** — Phases 14-18 (active)

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

<details>
<summary>✅ v1.2 Signal Quality & Portfolio Analytics (Phases 9-13) — SHIPPED 2026-04-14</summary>

- [x] Phase 9: Ops Hardening (1/1 plan) — completed 2026-04-13
- [x] Phase 10: Prompt Signal Enrichment (2/2 plans) — completed 2026-04-12
- [x] Phase 11: Confidence Scoring (2/2 plans) — completed 2026-04-12
- [x] Phase 12: ETF Polish (2/2 plans) — completed 2026-04-13
- [x] Phase 13: Portfolio Analytics (1/1 plan) — completed 2026-04-14

See `.planning/milestones/v1.2-ROADMAP.md` for full phase details.

</details>

### v1.3 Risk & Signal Quality (Phases 14-18)

- [x] **Phase 14: Trade History Command** — `/history` slash command showing last 20 closed trades (completed 2026-04-19)
- [x] **Phase 15: Fundamental Trend Enrichment** — P/E direction and EPS quarterly trend in Claude BUY prompt (completed 2026-04-21)
- [ ] **Phase 16: Earnings Date Warning** — Next earnings date in BUY embed and Claude prompt
- [ ] **Phase 17: Limit Buy Orders** — Limit order execution on Approve with config flag and audit trail
- [ ] **Phase 18: Test Coverage Gaps** — Analyst fallback, config validation, and quota exhaustion tests

---

## Phase Details

### Phase 14: Trade History Command
**Goal**: Operators can review the full closed-trade history from Discord without leaving the app
**Depends on**: Phase 13 (trades table with cost_basis column present since v1.2)
**Requirements**: OPS-02
**Success Criteria** (what must be TRUE):
  1. Operator runs `/history` and sees an embed listing up to the last 20 closed trades
  2. Each row shows ticker, entry price, exit price, P&L%, and sell date
  3. When no closed trades exist, the command responds with "No closed trades yet." (not an empty embed)
  4. The query uses a sell-side-only SELECT — no JOIN that would duplicate rows for multi-trade tickers
**Plans**: TBD

### Phase 14.1: SPY 1-year trend signal (INSERTED)

**Goal:** Add a 1-year SPY trend signal alongside the existing 1-month signal, giving Claude two temporal perspectives (momentum + structural trend) across all BUY/SELL/ETF prompts
**Requirements**: N/A (signal quality improvement, no formal requirement ID)
**Depends on:** Phase 14
**Plans:** 1/1 plans complete

Plans:
- [x] 14.1-01-PLAN.md — Update macro.py producer, claude_analyst.py consumers, main.py fallbacks, and all tests

### Phase 15: Fundamental Trend Enrichment
**Goal**: Claude BUY prompt includes P/E direction and EPS trend so signal quality reflects valuation trajectory
**Depends on**: Phase 14
**Requirements**: SIG-07, SIG-08
**Success Criteria** (what must be TRUE):
  1. Claude BUY prompt contains a P/E direction label (expanding / contracting / stable / N/A) derived from trailingPE vs forwardPE
  2. Claude BUY prompt contains the last 4 quarters of EPS values in chronological order
  3. When forwardPE is absent from yfinance info, the P/E direction field shows "N/A" rather than crashing
  4. When quarterly_income_stmt returns None or is missing the "Diluted EPS" row, the EPS trend field is omitted gracefully
**Plans**: 1 plan
  - [x] 15-01-PLAN.md — Add fetch_eps_data + thread fundamental_trend through build_prompt/analyze_ticker + wire into main.py buy-scan

### Phase 16: Earnings Date Warning
**Goal**: Operators and Claude both see upcoming earnings proximity before approving a BUY
**Depends on**: Phase 15 (fetch_fundamental_info already extended)
**Requirements**: SIG-05, SIG-06
**Success Criteria** (what must be TRUE):
  1. BUY embed displays a "Next Earnings" field showing the date (or "N/A" when absent)
  2. When earnings are within 7 days, the embed field is visually flagged as a warning
  3. Claude BUY prompt includes the next earnings date so it can factor in proximity risk
  4. ETF scan path skips the earnings date field entirely — no empty or erroring field on ETF embeds
**Plans**: TBD
**UI hint**: yes

### Phase 17: Limit Buy Orders
**Goal**: Every approved BUY places a limit order at the signal price instead of a market order, reducing execution slippage risk
**Depends on**: Phase 16
**Requirements**: RISK-01, RISK-02, RISK-03, RISK-04
**Success Criteria** (what must be TRUE):
  1. Approving a BUY recommendation calls `equity_buy_limit` with price as a formatted string (not a raw float) when USE_LIMIT_BUY=true
  2. Setting USE_LIMIT_BUY=false in .env falls back to the existing market order path
  3. The trades table records limit_price and order_type for every executed buy, enabling audit trail review
  4. The BUY embed Approve button area shows the scan-time price with a timestamp ("as of HH:MM") so the operator can assess staleness
  5. Limit orders default to GTC duration — a late-afternoon approval does not result in a silently unfilled DAY order
**Plans**: TBD

### Phase 18: Test Coverage Gaps
**Goal**: Critical untested execution paths (analyst fallback, config validation, quota exhaustion) are covered by automated tests
**Depends on**: Phase 17 (all prior phases stable before writing integration tests)
**Requirements**: TEST-09, TEST-10, TEST-11
**Success Criteria** (what must be TRUE):
  1. A primary API failure triggers the fallback provider and a corresponding test asserts the fallback was called
  2. A parse error does NOT trigger the fallback provider and a test asserts this distinction
  3. Config.validate() raises ValueError for each missing required env var and a test covers this for each field
  4. Setting USE_LIMIT_BUY=false in the test environment sets use_limit_buy=False on the Config object
  5. When both providers have exhausted daily quota, run_scan does not call analyze_ticker and a test asserts the skip path
**Plans**: TBD

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
| 9. Ops Hardening | v1.2 | 1/1 | Complete | 2026-04-13 |
| 10. Prompt Signal Enrichment | v1.2 | 2/2 | Complete | 2026-04-12 |
| 11. Confidence Scoring | v1.2 | 2/2 | Complete | 2026-04-12 |
| 12. ETF Polish | v1.2 | 2/2 | Complete | 2026-04-13 |
| 13. Portfolio Analytics | v1.2 | 1/1 | Complete | 2026-04-14 |
| 14. Trade History Command | v1.3 | 1/1 | Complete    | 2026-04-19 |
| 14.1. SPY 1-year trend signal | v1.3 | 1/1 | Complete    | 2026-04-19 |
| 15. Fundamental Trend Enrichment | v1.3 | 1/1 | Complete    | 2026-04-21 |
| 16. Earnings Date Warning | v1.3 | 0/? | Not started | - |
| 17. Limit Buy Orders | v1.3 | 0/? | Not started | - |
| 18. Test Coverage Gaps | v1.3 | 0/? | Not started | - |

---
*Roadmap defined: 2026-03-30*
*Last updated: 2026-04-21 — Phase 15 planned (1 plan)*
