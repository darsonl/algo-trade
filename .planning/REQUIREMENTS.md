# Requirements — v1.3 Risk & Signal Quality

**Milestone:** v1.3
**Status:** Active
**Last updated:** 2026-04-18

---

## v1 Requirements

### Risk Management

- [ ] **RISK-01**: User can approve a buy recommendation and have a limit order placed at the signal price (not a market order)
- [ ] **RISK-02**: `USE_LIMIT_BUY` config flag controls limit order behavior — defaults to `true`, operator can opt out via `.env`
- [ ] **RISK-03**: `limit_price` and `order_type` stored in `trades` table for audit trail
- [ ] **RISK-04**: Approve embed shows scan-time price with timestamp ("as of HH:MM") so operator can assess staleness before clicking

### Signal Intelligence

- [ ] **SIG-05**: BUY embed displays next earnings date field (sourced from `info["earningsTimestamp"]` — no extra HTTP call)
- [ ] **SIG-06**: Next earnings date included in Claude BUY prompt so it can factor in proximity risk
- [ ] **SIG-07**: P/E direction (expanding / contracting / stable / N/A) included in Claude BUY prompt using `forwardPE` vs `trailingPE` as proxy
- [ ] **SIG-08**: Quarterly EPS trend (last 4 quarters from `ticker.quarterly_income_stmt`) included in Claude BUY prompt

### Operational Quality

- [ ] **OPS-02**: `/history` Discord slash command shows last 20 closed trades — ticker, entry price, exit price, P&L%, sell date

### Test Coverage

- [ ] **TEST-09**: Analyst fallback provider logic tested — primary API failure triggers fallback; parse errors do not trigger fallback
- [ ] **TEST-10**: Config `validate()` tested — missing required env vars raise `ValueError`; `USE_LIMIT_BUY=false` sets `use_limit_buy=False`
- [ ] **TEST-11**: `run_scan` quota exhaustion path tested — when both providers hit daily limit, `analyze_ticker` is not called

---

## Future Requirements

- Limit order cancel/expiry notification (detect unfilled GTC orders at next scan start)
- Stop-loss trigger (auto-post SELL when P&L < `-STOP_LOSS_PCT`)
- Take-profit trigger (auto-post SELL when P&L > `TAKE_PROFIT_PCT`)
- `/history` filtering by ticker or date range
- `/history` pagination for large trade histories
- Earnings BMO/AMC timing in embed (before/after market open)
- Real-time fill status polling via Schwab API

---

## Out of Scope

- Confidence-weighted position sizing — display-only badge remains behavioral change deferred
- GTC cancel button in Discord — DAY orders self-expire; GTC operator manages via Schwab app directly
- Historical P/E via price/EPS computation — `forwardPE` vs `trailingPE` proxy is sufficient
- `/history` exported as CSV/JSON — Discord embed sufficient for current scale
- Parallel ticker scanning — explicitly out of scope per PROJECT.md

---

## Traceability

*(filled by roadmapper)*

| REQ-ID | Phase | Description |
|--------|-------|-------------|
| RISK-01 | — | Limit buy order on Approve |
| RISK-02 | — | USE_LIMIT_BUY config flag |
| RISK-03 | — | limit_price/order_type in trades table |
| RISK-04 | — | Scan-time price staleness in embed |
| SIG-05 | — | Earnings date in BUY embed |
| SIG-06 | — | Earnings date in Claude prompt |
| SIG-07 | — | P/E direction in Claude prompt |
| SIG-08 | — | Quarterly EPS trend in Claude prompt |
| OPS-02 | — | /history slash command |
| TEST-09 | — | Analyst fallback tests |
| TEST-10 | — | Config validation tests |
| TEST-11 | — | run_scan quota exhaustion test |
