# Phase 12: ETF Polish — Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-12
**Phase:** 12-etf-polish
**Areas discussed:** ETF scan scheduling, Expense ratio flag behavior, Expense ratio threshold defaults

---

## ETF Scan Scheduling

| Option | Description | Selected |
|--------|-------------|----------|
| ETF_SCAN_HOUR + ETF_SCAN_MINUTE | Mirrors existing SCAN_HOUR/SCAN_MINUTE pattern — simple, consistent | ✓ |
| ETF_SCAN_TIMES | Multi-time list format — more powerful but out of scope | |

| Option | Description | Selected |
|--------|-------------|----------|
| Reuse configure_scheduler for ETF job | Call twice with different job_fn — pure, testable | ✓ |
| New configure_etf_scheduler function | Separate function for clarity | |

---

## Expense Ratio Flag Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Flag in embed only | Post recommendation; mark Expense Ratio field with ⚠️ | ✓ |
| Skip ETF entirely | Filter out before analyst call | |
| Flag + add reasoning note | Embed flag + note in reasoning | |

| Option | Description | Selected |
|--------|-------------|----------|
| Modify existing Expense Ratio field value | Change "0.0075" → "⚠️ 0.0075 (High)" | ✓ |
| Add separate 'High Expense Ratio' warning field | Keep existing field, add new inline field | |

---

## Expense Ratio Threshold Defaults

| Option | Description | Selected |
|--------|-------------|----------|
| 0.005 (0.5%) | Standard cutoff — flags expensive ETFs, leaves SPY/QQQ/BND unmarked | ✓ |
| 0.01 (1.0%) | More permissive — only flags actively managed/leveraged ETFs | |

| Option | Description | Selected |
|--------|-------------|----------|
| ETF_MAX_EXPENSE_RATIO | Matches Config naming convention (max_pe_ratio, etc.) | ✓ |
| MAX_ETF_EXPENSE_RATIO | Alternative ordering | |
