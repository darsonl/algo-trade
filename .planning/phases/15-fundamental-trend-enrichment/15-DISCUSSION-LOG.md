# Phase 15: Fundamental Trend Enrichment - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-20
**Phase:** 15-fundamental-trend-enrichment
**Areas discussed:** P/E direction threshold, EPS trend format, Data flow shape

---

## P/E Direction Threshold

| Option | Description | Selected |
|--------|-------------|----------|
| ±5% band | abs(forwardPE - trailingPE) / trailingPE < 0.05 → stable | ✓ |
| ±10% band | Wider tolerance; more tickers show "stable" | |
| No stable band | Any difference → directional; "stable" only on exact float equality | |

**User's choice:** ±5% band

---

## P/E Direction Convention

| Option | Description | Selected |
|--------|-------------|----------|
| Valuation multiple direction | forwardPE > trailingPE → expanding (stock pricier); forwardPE < trailingPE → contracting (earnings growing) | ✓ |
| Earnings momentum direction | Inverted: forwardPE < trailingPE → expanding earnings | |

**User's choice:** Valuation multiple direction

---

## EPS Trend Format

| Option | Description | Selected |
|--------|-------------|----------|
| Quarter labels + values | EPS trend (last 4Q): Q1-2025: $0.45, Q2-2025: $0.52, … | ✓ |
| Values only, oldest→newest | EPS trend (last 4Q): $0.45, $0.52, $0.48, $0.61 | |
| Values + trend label | EPS trend (last 4Q): $0.45, … — improving | |

**User's choice:** Quarter labels + values (with preview confirmation)

---

## Data Flow Shape

| Option | Description | Selected |
|--------|-------------|----------|
| New fundamental_trend dict param | Mirrors macro_context pattern; clean separation from raw info | ✓ |
| Inject into info dict | Fewer signature changes but mixes computed values into raw yfinance dict | |
| Claude's discretion | Let planner decide | |

**User's choice:** New fundamental_trend dict param

---

## EPS Data Fetch Location

| Option | Description | Selected |
|--------|-------------|----------|
| New fetch_eps_data() in fundamentals.py | Consistent with fetch_fundamental_info pattern; screener owns yfinance I/O | ✓ |
| Inline in main.py | Fewer files but main.py grows | |

**User's choice:** New fetch_eps_data() in screener/fundamentals.py

---

## Claude's Discretion

- Exact placement of the fundamental_trend block within build_prompt() (suggest after existing three fundamentals lines)
- Test strategy: new tests in test_screener_fundamentals.py vs extending test_analyst_claude.py

## Deferred Ideas

- P/E direction and EPS trend in SELL prompt — BUY-only per requirements
- EPS trend in ETF prompt — ETFs don't have quarterly EPS
