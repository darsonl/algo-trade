# Phase 9: Ops Hardening - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-12
**Phase:** 09-ops-hardening
**Areas discussed:** Error capture scope, Batching format, Post timing

---

## Error capture scope

| Option | Description | Selected |
|--------|-------------|----------|
| Yes — post all caught per-ticker errors | Each caught exception in the per-ticker loop gets posted (up to the cap). Matches the spirit of OPS-01. | ✓ |
| No — only scan-crashing exceptions | Only exceptions that escape all catch blocks get posted. Per-ticker errors stay silent. | |

**User's choice:** Post all caught per-ticker errors
**Notes:** —

---

| Option | Description | Selected |
|--------|-------------|----------|
| Ticker + error type only | Short: `[ERROR] AAPL: ConnectionError` | ✓ |
| Ticker + full error message | Medium: `[ERROR] AAPL: HTTPError 429 Too Many Requests` | |
| You decide | Claude chooses the format | |

**User's choice:** Ticker + error type only
**Notes:** —

---

## Batching format

| Option | Description | Selected |
|--------|-------------|----------|
| 3 separate Discord messages | Each error gets its own message. Cap at 3 total. | ✓ |
| 1 consolidated message | One Discord message at end of scan listing up to 3 errors. | |

**User's choice:** Separate Discord messages, but: if total errors exceed 3, a trailing message should state how many more were not shown.
**Notes:** User questioned why cap exists at 3. After explanation (yfinance outage scenario → 20+ messages), agreed to keep cap at 3 but add overflow message.

---

## Post timing

| Option | Description | Selected |
|--------|-------------|----------|
| Immediately as they happen | Post each error to Discord right when caught. Real-time visibility. | ✓ |
| Collected and posted at end of scan | Accumulate all, post summary after all tickers processed. | |

**User's choice:** Immediately as they happen

---

| Option | Description | Selected |
|--------|-------------|----------|
| Post 3 immediately + trailing overflow message | First 3 post immediately; after scan, if total > 3 post `[X more errors not shown]` | ✓ |
| Post 3 immediately, silent drop after | No overflow message. Errors 4+ silently dropped. | |

**User's choice:** Post 3 immediately + trailing overflow message
**Notes:** Resolves the conflict between immediate posting and wanting total error count visible.

---

## Claude's Discretion

None — all decisions were explicit.

## Deferred Ideas

None.
