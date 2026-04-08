---
phase: 07
slug: etf-scan-separation
status: verified
threats_open: 0
asvs_level: 1
created: 2026-04-08
---

# Phase 07 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Discord user → /scan_etf | Any Discord server member can trigger the ETF scan | Command intent (no payload) |
| etf_watchlist.txt → run_scan_etf | User-editable file provides ETF ticker list | Ticker symbols (low sensitivity) |
| yfinance annualReportExpenseRatio → prompt/embed | External data displayed in Discord embed and injected into analyst prompt | Numeric ratio (display only, no routing) |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-07-09 | Elevation of Privilege | `/scan_etf` command | accept | Same authorization model as `/scan` — any server member can trigger; single-operator bot design (no new privilege escalation). See Accepted Risks Log. | closed |
| T-07-10 | Denial of Service | `run_scan_etf` | mitigate | (1) Analyst quota guard in `run_scan_etf` prevents unlimited API calls (`analyst_daily_limit` check, main.py:338); (2) `asyncio.to_thread(partition_watchlist, ...)` prevents event-loop blocking (ASYNC-03, main.py:289); (3) per-ticker `try/except Exception` prevents one failure aborting the scan (main.py:393). | closed |
| T-07-11 | Tampering | `screener/fundamentals.py` cleanup | mitigate | `_ETF_ALLOWLIST` constant and ETF bypass (`if info.get("symbol")...return True`) removed from `passes_fundamental_filter`; ETFs are now routed to `run_scan_etf` upstream via `partition_watchlist` before reaching the stock fundamental filter. Verified: `grep _ETF_ALLOWLIST screener/fundamentals.py` returns no matches. | closed |
| T-07-12 | Spoofing | `expense_ratio` from yfinance | accept | `expense_ratio` is used only for display in the Discord embed and analyst prompt; `None` → "N/A" rendering; no financial routing decision depends on this value. See Accepted Risks Log. | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-07-01 | T-07-09 | `/scan_etf` uses same open-trigger authorization as `/scan`. This is an intentional single-operator design: only the bot owner operates the Discord server. Restricting trigger access would add complexity without meaningful security benefit. | Trading Bot Dev | 2026-04-08 |
| AR-07-02 | T-07-12 | `expense_ratio` sourced from yfinance is untrusted but used only for informational display. A spoofed or stale value cannot cause incorrect order placement; it only affects the analyst prompt and embed field. | Trading Bot Dev | 2026-04-08 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-04-08 | 4 | 4 | 0 | gsd-secure-phase (claude-sonnet-4-6) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-04-08
