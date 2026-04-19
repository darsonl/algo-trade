# Phase 14: Trade History Command - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-19
**Phase:** 14-trade-history-command
**Areas discussed:** Embed layout, Pre-v1.2 rows

---

## Embed Layout

| Option | Description | Selected |
|--------|-------------|----------|
| Code block table | Single description field with monospaced table. Compact, scannable, fits 20 rows. | ✓ |
| Inline fields (like /positions) | One Discord field per trade, 3 across. 20 fields of 25 max. Taller, harder to scan. | |

**User's choice:** Code block table in `description`

---

### Date Format

| Option | Description | Selected |
|--------|-------------|----------|
| Short (Apr 1) | Saves horizontal space, keeps columns aligned | |
| ISO (2026-04-01) | Unambiguous and sortable | ✓ |

**User's choice:** ISO date format `YYYY-MM-DD`

---

## Pre-v1.2 Rows

| Option | Description | Selected |
|--------|-------------|----------|
| Exclude them | WHERE cost_basis IS NOT NULL. Consistent with /stats. Clean table, no partial rows. | ✓ |
| Include with N/A | All sell-side trades; NULL entry/P&L% shown as "N/A". More complete but inconsistent rows. | |

**User's choice:** Exclude pre-v1.2 rows (cost_basis IS NOT NULL filter)

---

## Claude's Discretion

- Embed title and color
- Footer content
- Column width padding
- Whether to extract a `get_closed_trades` helper vs. inline query

## Deferred Ideas

- `/history` filtering by ticker or date range
- `/history` pagination for large histories
- Including pre-v1.2 rows with N/A display
