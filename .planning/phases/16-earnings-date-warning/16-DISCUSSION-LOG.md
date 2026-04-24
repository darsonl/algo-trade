# Phase 16: Earnings Date Warning - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-24
**Phase:** 16-earnings-date-warning
**Areas discussed:** Embed Field Design, 7-Day Warning Visual, Past Timestamp Handling, Prompt Placement

---

## Embed Field Design

| Option | Description | Selected |
|--------|-------------|----------|
| `Dec 15, 2025` | Human-readable month name, no days-remaining count | ✓ |
| `Dec 15, 2025 (in 6 days)` | Always shows days-remaining alongside the date | |
| `2025-12-15` | ISO date — compact but less scannable in Discord | |

**User's choice:** `Dec 15, 2025` format
**Notes:** Clean and readable at a glance. Days-remaining not needed in the value itself.

**Position in embed:**

| Option | Description | Selected |
|--------|-------------|----------|
| After P/E Ratio | 4th field, starts Row 2 alongside Confidence | |
| After Confidence | Last field in embed, Row 2+ | ✓ |
| Before Price | Leads the embed with time-sensitive info | |

**User's choice:** After Confidence (or after P/E Ratio when Confidence is absent)

---

## 7-Day Warning Visual

| Option | Description | Selected |
|--------|-------------|----------|
| ⚠️ prefix on field value | Value becomes `⚠️ Dec 18, 2025` — no extra embed fields | ✓ |
| Separate `⚠️ Earnings Soon` field | Additional field appears only within 7 days | |
| Field name changes | "Next Earnings" → "⚠️ Earnings Risk" within 7 days | |

**User's choice:** ⚠️ prefix on value
**Notes:** Contained and simple — no extra embed slots consumed, embed color stays BUY green.

---

## Past Timestamp Handling

| Option | Description | Selected |
|--------|-------------|----------|
| Suppress to "N/A" | Same treatment as absent data | ✓ |
| Show date as-is | `Oct 22, 2025` even if past — historical context | |
| Show with "(past)" suffix | `Oct 22, 2025 (past)` — honest but noisy | |

**User's choice:** Suppress to "N/A"
**Notes:** Consistent with how P/E Ratio and Dividend Yield handle missing/inapplicable data.

---

## Prompt Section Placement

| Option | Description | Selected |
|--------|-------------|----------|
| `Fundamentals:` block | Alongside P/E, Dividend Yield, EPS trend | |
| `Market Context:` block | Alongside SPY trend, VIX — timing risk framing | ✓ |
| Standalone line before conclusion | Directive note e.g. "⚠️ Earnings in 6 days" | |

**User's choice:** `Market Context:` section
**Notes:** Earnings proximity is a timing risk signal, not a valuation metric. Keeps Fundamentals clean.

---

## Claude's Discretion

- Whether `earnings_date` is added to the `fundamental_trend` dict or passed as a separate parameter
- Exact `datetime` conversion logic for Unix epoch → `"Dec 15, 2025"` string (UTC acceptable)
- Test strategy for embed and prompt tests

## Deferred Ideas

- Earnings BMO/AMC timing (before/after market open) — Future Requirements in REQUIREMENTS.md
- Earnings date in SELL prompt — not scoped
- Earnings date in ETF prompt — excluded by SC-4
