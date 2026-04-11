# Requirements — v1.2 Signal Quality & Portfolio Analytics

## Milestone Goal

Finish ETF polish, enrich Claude signals with sector/macro context and confidence scoring, add portfolio stats, and surface scan errors to Discord.

---

## v1.2 Requirements

### ETF Polish

- [ ] **ETF-07**: User can configure a scheduled ETF scan that runs at a time offset from the stock scan (e.g. 9:30 AM) to avoid rate-limit contention
- [ ] **ETF-08**: Ops zero-recommendation alert for ETF scan is prefixed with `[ETF]` to distinguish it from stock scan silence
- [ ] **ETF-09**: ETFs with an expense ratio above a configurable threshold are flagged or skipped in the ETF embed

### Signal Enrichment

- [ ] **SIG-01**: Claude BUY/SELL prompt includes the ticker's sector (e.g. "Technology") so Claude can factor sector context into its reasoning
- [ ] **SIG-02**: Claude BUY/SELL/ETF prompt includes SPY 1-month trend direction and current VIX level so Claude can factor macro conditions into its reasoning
- [ ] **SIG-03**: Claude BUY/SELL prompt includes the ticker's current price position within its 52-week range (e.g. "near 52-week high") so Claude can assess momentum context
- [ ] **SIG-04**: Claude outputs a confidence level (high/medium/low) alongside the BUY/SELL/HOLD signal; confidence is displayed as a badge in the Discord embed (no behavioral change; badge omitted if confidence is absent)

### Portfolio Analytics

- [ ] **PORT-01**: User can see total aggregate unrealized P&L across all open positions in the `/positions` embed footer
- [ ] **PORT-02**: User can run `/stats` slash command to view win rate, average gain %, and average loss % across all closed trades

### Ops & Reliability

- [ ] **OPS-01**: Unhandled exceptions during stock and ETF scan runs are posted to the Discord ops channel (batched; maximum 3 error posts per scan run to prevent spam)

---

## Future Requirements

- ETF_SCAN_TIMES config field for multiple scheduled ETF windows — deferred until scheduled ETF scan is validated
- Earnings date warning in Claude prompt — deferred to v1.3
- Analyst consensus (avg target price) in Claude prompt — deferred to v1.3
- Per-sector position cap (exposure limiting) — deferred; SIG-01 covers prompt enrichment only in v1.2
- Trade history `/history` command (individual closed trades with entry/exit/hold duration) — deferred to v1.3
- `analyst_cache` TTL / expiry — deferred; cache staleness is a known limitation, document as such

---

## Out of Scope

- ETF 52-week range in SIG-03 — ETF info fetch pattern requires separate decision (fast_info vs full info vs history); stock path only in v1.2
- Separate Discord ops channel (`DISCORD_OPS_CHANNEL_ID`) — OPS-01 posts to existing trading channel; channel separation deferred
- DRY_RUN trade marker in `trades` table — `/stats` will include dry-run trades; documented as known limitation
- Confidence-based BUY filtering or yellow embed coloring — display-only badge; no behavioral change in v1.2
- Analyst cache invalidation on prompt enrichment — operator clears cache manually at deployment; not automated
- Parallel ticker scanning — explicitly out of scope (established in v1.0)

---

## Traceability

| REQ-ID  | Phase | Status |
|---------|-------|--------|
| ETF-07  | —     | Pending |
| ETF-08  | —     | Pending |
| ETF-09  | —     | Pending |
| SIG-01  | —     | Pending |
| SIG-02  | —     | Pending |
| SIG-03  | —     | Pending |
| SIG-04  | —     | Pending |
| PORT-01 | —     | Pending |
| PORT-02 | —     | Pending |
| OPS-01  | —     | Pending |

---

*Created: 2026-04-12 — v1.2 milestone*
