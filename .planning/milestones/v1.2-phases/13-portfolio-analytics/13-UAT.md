---
status: complete
phase: 13-portfolio-analytics
source: [13-01-SUMMARY.md]
started: 2026-04-14T00:30:00Z
updated: 2026-04-14T00:45:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: Kill any running bot process. Start the bot fresh with `python main.py`. It should boot without errors. In particular: the DB migration adding `cost_basis REAL` to the trades table should run silently (or not appear at all if the column already exists). No traceback, no OperationalError.
result: pass

### 2. /positions footer — all positions priced
expected: Run `/positions` when at least one open position has a live price. The embed should show a footer line in the format "Total unrealized: +$X.XX (+Y.Y%)" (or with a minus sign for a loss). No "(partial)" suffix.
result: pass

### 3. /positions footer — partial prices
expected: Run `/positions` when you have at least one priced position AND one position where yfinance returns no price (or if that's hard to simulate, note that this is tested in the test suite). If testable: the footer should show "Total unrealized: ... (partial)".
result: skipped
reason: hard to reproduce live — covered by unit tests

### 4. /positions footer — no prices available
expected: Run `/positions` when all positions are unpriced (or the positions list is empty). The embed should have NO footer line — just the fields or "No open positions." description.
result: skipped
reason: hard to reproduce live — covered by unit tests

### 5. /stats — with qualifying closed trades
expected: After at least one sell has been approved (so a trade row exists with side='sell' and cost_basis set), run `/stats`. The bot should reply with an embed titled "Trade Statistics" containing three inline fields: Win Rate, Avg Gain, Avg Loss, plus a description row showing closed trade counts.
result: skipped
reason: no closed trades in DB yet — covered by unit tests

### 6. /stats — no closed trades
expected: Run `/stats` with no sell trades in the DB (or no sell trades with cost_basis set). The bot should reply with plain text: "No closed trades yet — nothing to analyze." — no embed.
result: pass

## Summary

total: 6
passed: 3
issues: 0
skipped: 3
pending: 0

## Gaps

[none yet]
