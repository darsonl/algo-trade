# Phase 7: ETF Scan Separation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions captured in CONTEXT.md — this log preserves the Q&A.

**Date:** 2026-04-07
**Phase:** 07-etf-scan-separation
**Mode:** discuss

## Gray Areas Identified

Based on codebase analysis (watchlist.txt, fundamentals.py, claude_analyst.py, REQUIREMENTS.md):
- ETF analyst prompt design
- watchlist.txt split / etf_watchlist.txt creation
- Expense ratio handling when yfinance returns None

## Discussion Log

### ETF Analyst Prompt

| Question | Options Presented | Answer |
|----------|------------------|--------|
| Include news headlines? | Include news + technicals / Technical signals only / News only when available | Include news + technicals |
| Signal format? | Same BUY/HOLD/SKIP / Add ETF-specific TREND signal / BUY/HOLD only | Same BUY/HOLD/SKIP |

### watchlist.txt Split

| Question | Options Presented | Answer |
|----------|------------------|--------|
| What happens to watchlist.txt after etf_watchlist.txt is created? | Migrate ETFs out, watchlist.txt becomes stocks-only / Keep as-is, etf_watchlist.txt separate / Add stocks during Phase 7 | Migrate ETFs to etf_watchlist.txt, watchlist.txt becomes stocks-only |

### Expense Ratio

| Question | Options Presented | Answer |
|----------|------------------|--------|
| What to show when annualReportExpenseRatio is None? | Show N/A + DEBUG log / Omit field / Hard-code fallback dict | Show N/A, log a debug warning |

## Corrections Made

No corrections — all recommended options were accepted.
