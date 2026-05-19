---
status: partial
phase: 17-limit-buy-orders
source: [17-VERIFICATION.md]
started: 2026-05-19T00:00:00Z
updated: 2026-05-19T00:00:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Live GTC limit order placement via Schwab paper trading
expected: When USE_LIMIT_BUY=true and DRY_RUN=false, clicking Discord Approve places a GTC limit order (not market) at the scan-time price via the Schwab paper-trading API. The order appears in Schwab with duration=GOOD_TILL_CANCEL and the correct limit price.
result: [pending]

### 2. Discord embed Price field visual rendering
expected: The BUY recommendation embed shows the Price field with a line break: "$52.34" on the first line and "as of 09:05" on the second line — renders correctly on Discord desktop, mobile, and web clients.
result: [pending]

### 3. USE_LIMIT_BUY=false market fallback in live environment
expected: When USE_LIMIT_BUY=false in .env and DRY_RUN=false, clicking Discord Approve places a market order (not limit), and the confirmation message does NOT contain "(limit, GTC)".
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
