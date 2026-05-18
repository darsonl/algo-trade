---
phase: 17-limit-buy-orders
plan: "01"
subsystem: database, orders, config
tags: [schwab-py, sqlite, limit-order, gtc, config-flag]

requires:
  - phase: 16-earnings-date-warning
    provides: "trades table with cost_basis column (additive migration pattern)"

provides:
  - "Config.use_limit_buy bool field (USE_LIMIT_BUY env var, default true)"
  - "build_limit_buy function — GTC limit order spec builder with GOOD_TILL_CANCEL duration"
  - "place_limit_order function — mirrors place_order, formats float price internally"
  - "trades table: limit_price REAL and order_type TEXT columns (CREATE TABLE + ALTER TABLE)"
  - "create_trade signature extended with limit_price and order_type kwargs (safe defaults)"
  - "5 unit tests covering build_limit_buy correctness (symbol, quantity, price-string, LIMIT, GTC)"

affects: [17-02, discord-bot, bot-approve-handler, schwab-orders]

tech-stack:
  added: []
  patterns:
    - "bool-from-env: os.getenv('FLAG', 'default').lower() == 'true' (not bool(os.getenv(...)))"
    - "build_*_buy separate functions (not extending place_order with optional param)"
    - "additive SQLite migration: CREATE TABLE IF NOT EXISTS + ALTER TABLE try/except blocks"
    - "TDD: RED commit (failing import) then GREEN commit (implementation)"

key-files:
  created: []
  modified:
    - config.py
    - schwab_client/orders.py
    - database/models.py
    - database/queries.py
    - tests/test_schwab_orders.py

key-decisions:
  - "build_limit_buy calls .set_duration(Duration.GOOD_TILL_CANCEL) before .build() — equity_buy_limit defaults to DAY which silently expires late-afternoon approvals (D-04, SC-5)"
  - "place_limit_order accepts float and formats internally as f\"{:.2f}\" — raw float triggers DeprecationWarning in schwab-py 1.5.1, future TypeError (D-03)"
  - "create_trade new params use safe defaults (limit_price=None, order_type='market') — sell path call site (SellApproveRejectView) requires zero changes (D-13)"
  - "use_limit_buy uses .lower() == 'true' pattern — bool('false') is True in Python, matching dry_run/paper_trading precedent (D-01, D-02)"

patterns-established:
  - "Pattern: GTC limit buy — always call .set_duration(Duration.GOOD_TILL_CANCEL) before .build() — never use equity_buy_limit default"
  - "Pattern: formatted price string — use f\"{price:.2f}\" not str(price) or round() for schwab-py price params"

requirements-completed: [RISK-01, RISK-02, RISK-03]

duration: 25min
completed: 2026-05-19
---

# Phase 17 Plan 01: Limit Buy Orders Foundation Summary

**GTC limit buy order infrastructure: Config flag, build_limit_buy with GOOD_TILL_CANCEL enforcement, trades schema migration, and 5 unit tests confirming schwab-py DAY-default pitfall is mitigated**

## Performance

- **Duration:** 25 min
- **Started:** 2026-05-19T00:00:00Z
- **Completed:** 2026-05-19T00:25:00Z
- **Tasks:** 3 (Task 2 had 2 commits: RED + GREEN)
- **Files modified:** 5

## Accomplishments

- Added `Config.use_limit_buy` bool field using `.lower() == "true"` pattern (defaults True, respects `USE_LIMIT_BUY=false`)
- Implemented `build_limit_buy` with mandatory `Duration.GOOD_TILL_CANCEL` before `.build()` — guards against schwab-py DAY default (critical pitfall confirmed by live import)
- Implemented `place_limit_order` mirroring `place_order` structure, reusing `_call_place_order` unchanged, formatting float price internally
- Extended `trades` table with `limit_price REAL` and `order_type TEXT` in both CREATE TABLE and ALTER TABLE migration blocks
- Extended `create_trade` signature with `limit_price=None` and `order_type='market'` defaults — sell path unchanged
- 5 TDD unit tests green: symbol, quantity, price-as-string, LIMIT type, GOOD_TILL_CANCEL duration

## Task Commits

Each task was committed atomically:

1. **Task 1: Add use_limit_buy field to Config dataclass** - `c093962` (feat)
2. **Task 2 RED: Add failing tests for build_limit_buy** - `3da2cb4` (test)
3. **Task 2 GREEN: Add build_limit_buy and place_limit_order** - `83b3de7` (feat)
4. **Task 3: Extend trades schema + create_trade signature** - `21b1733` (feat)

_Note: Task 2 used TDD with separate RED (failing import) and GREEN (implementation) commits per plan spec._

## Files Created/Modified

- `config.py` — Added `use_limit_buy: bool` field after `dry_run` (line 38)
- `schwab_client/orders.py` — Added `equity_buy_limit` + `Duration` imports; added `build_limit_buy` and `place_limit_order` functions
- `database/models.py` — Added `limit_price REAL` and `order_type TEXT` to trades CREATE TABLE; added 2 ALTER TABLE migration blocks after cost_basis block
- `database/queries.py` — Extended `create_trade` signature with `limit_price` and `order_type` params; updated INSERT SQL to 9 columns
- `tests/test_schwab_orders.py` — Updated import; added 5 `test_build_limit_buy_*` tests

## Decisions Made

- **GTC enforcement**: `build_limit_buy` calls `.set_duration(Duration.GOOD_TILL_CANCEL)` before `.build()`. Live verification confirmed `equity_buy_limit` defaults to `DAY` — omitting this would silently expire late-afternoon orders (T-17-01 mitigated).
- **Separate function**: Added `build_limit_buy` alongside `build_market_buy` (not extending `place_order` with optional `limit_price`). Follows `place_order`/`place_sell_order` symmetry precedent.
- **Float-to-string internally**: `place_limit_order` accepts `float` and formats as `f"{limit_price:.2f}"` — D-03 compliance without burdening call sites.
- **CREATE TABLE only new columns**: `cost_basis` is not in the existing CREATE TABLE — only `limit_price` and `order_type` were added to CREATE TABLE. `cost_basis` migration is already handled by its own ALTER TABLE block. This matches the `must_haves.artifacts` contract exactly.

## Deviations from Plan

None — plan executed exactly as written. The CREATE TABLE ambiguity (cost_basis already absent from CREATE TABLE) was resolved per advisor guidance: only `limit_price REAL` and `order_type TEXT` were added, matching the `must_haves.artifacts` contract verbatim.

## Issues Encountered

- Windows file lock during verification script (`os.unlink` on open SQLite file) — resolved by closing connection before cleanup. Verification logic passed correctly.

## Known Stubs

None — this plan implements infrastructure only; no UI rendering or data display. Plan 02 will wire the Discord approval routing.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: T-17-01 mitigated | schwab_client/orders.py | `build_limit_buy` explicitly sets GOOD_TILL_CANCEL before .build() — unit test asserts duration |
| threat_flag: T-17-04 mitigated | config.py | `.lower() == "true"` pattern prevents bool("false") trap for USE_LIMIT_BUY |

## Next Phase Readiness

- Plan 02 can now import `build_limit_buy`, `place_limit_order`, and `Config.use_limit_buy` — all interfaces are in place
- `create_trade` accepts `limit_price` and `order_type` kwargs — approval handler can pass them without schema changes
- No blockers for Plan 02 (Discord bot routing + confirmation message + scan_time embed timestamp)

---
*Phase: 17-limit-buy-orders*
*Completed: 2026-05-19*
