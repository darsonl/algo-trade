# Phase 17: Limit Buy Orders - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-17
**Phase:** 17-limit-buy-orders
**Areas discussed:** Staleness Timestamp UI, Approval Message Wording, Timestamp Visibility Toggle, DRY_RUN + Limit Order Interaction

---

## Staleness Timestamp UI

| Option | Description | Selected |
|--------|-------------|----------|
| Inline with Price field | `$52.34\nas of 09:05` — compact, no extra embed slot | ✓ |
| Separate 'Scanned At' field | Price + new inline "Scanned At" field — clean separation but uses extra slot | |
| Embed footer | `Scan price as of 09:05` in footer — secondary, operators may miss it | |

**User's choice:** Inline with Price field
**Notes:** Operator sees price + age in one glance. Compact — reuses existing Price field.

---

## Approval Message Wording

| Option | Description | Selected |
|--------|-------------|----------|
| Explicit limit order wording | "Approved: limit order for 9 shares of AAPL at $52.34 (GTC)." | |
| Same wording, add parenthetical | "Approved: buying 9 shares of AAPL at $52.34 (limit, GTC)." | ✓ |
| Unchanged wording | Keep current "Approved: buying N shares of TICKER at $PRICE." | |

**User's choice:** Same wording, add parenthetical
**Notes:** Minimal change to existing message shape. Market fallback keeps current wording (no parenthetical).

---

## Timestamp Visibility Toggle

| Option | Description | Selected |
|--------|-------------|----------|
| Always show it | Operator always knows staleness regardless of order type. Simpler code. | ✓ |
| Only when USE_LIMIT_BUY=true | Timestamp most relevant to limit execution. Cleaner embed in market mode. | |

**User's choice:** Always show it
**Notes:** Consistent operator experience. Simpler embed builder — no config-conditional.

---

## DRY_RUN + Limit Order Interaction

| Option | Description | Selected |
|--------|-------------|----------|
| Reflect limit intent in dry run | Confirmation says "(limit, GTC)"; DB gets order_type='limit', limit_price set | |
| Dry run always says market | No real order fires → record as market; avoids labeling unexecuted intent as "limit" | ✓ |

**User's choice:** Dry run always says market
**Notes:** Simpler — DRY_RUN already means "nothing happened". Keeps dry-run history unambiguous.

---

## Claude's Discretion

- Whether `place_order` is extended with optional `limit_price` param, or a separate `place_limit_order` function is added — planner decides based on schwab-py API
- Exact schwab-py function name for limit buy (`equity_buy_limit`) — verify against library before implementing
- Test strategy details (file names, test count, mock approach)

## Deferred Ideas

- GTC unfill detection at next scan — Future Requirements in REQUIREMENTS.md
- Discord cancel button for GTC orders — Out of Scope in REQUIREMENTS.md
- Limit order for sell path — not requested
