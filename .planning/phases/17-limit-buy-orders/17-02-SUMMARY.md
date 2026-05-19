---
phase: 17-limit-buy-orders
plan: "02"
subsystem: discord-bot, embeds, main-orchestration
tags: [limit-order, gtc, discord-embed, scan-time, approval-routing, tdd]

requires:
  - phase: 17-limit-buy-orders
    plan: "01"
    provides: "place_limit_order, Config.use_limit_buy, create_trade with limit_price/order_type kwargs"

provides:
  - "build_recommendation_embed with scan_time kwarg — Price field shows '$X.XX\\nas of HH:MM' when provided"
  - "ApproveRejectView.__init__ with scan_time kwarg (stored as self.scan_time)"
  - "ApproveRejectView.approve routes to place_limit_order or place_order based on config.use_limit_buy"
  - "limit_price_val/order_type_val initialized outside dry_run block — dry-run always records market defaults"
  - "create_trade called with limit_price and order_type kwargs on all approve paths"
  - "Confirmation message contains '(limit, GTC)' only on live limit path"
  - "send_recommendation has scan_time kwarg forwarded to embed and view"
  - "main.py: scan_time captured once before ticker loop; passed to send_recommendation"
  - "9 new tests: 3 embed (RISK-04) + 6 bot routing (RISK-01, RISK-02, RISK-03)"

affects: [discord-bot, main-orchestration, approval-flow]

tech-stack:
  added: []
  patterns:
    - "scan_time: str | None = None kwarg pattern — all optional, backward-compatible"
    - "limit_price_val/order_type_val initialized outside if-block — dry-run always writes market defaults (D-06)"
    - "Three-branch confirmation message: dry_run / use_limit_buy / market"
    - "TDD: RED commit (failing tests) then GREEN commit (implementation) for Tasks 1 and 2"

key-files:
  created: []
  modified:
    - discord_bot/embeds.py
    - discord_bot/bot.py
    - main.py
    - tests/test_discord_embeds.py
    - tests/test_discord_buttons.py

key-decisions:
  - "D-06 enforcement: limit_price_val and order_type_val initialized to (None, 'market') OUTSIDE the if not dry_run block — guarantees dry-run always writes order_type='market' regardless of use_limit_buy"
  - "ETF scope guard: build_etf_recommendation_embed and send_etf_recommendation deliberately NOT modified — ETF approvals route through same ApproveRejectView so use_limit_buy=True applies to ETF buys too (intentional, strictly safer)"
  - "Existing live tests updated to use_limit_buy=False: test_approve_live_calls_place_order and test_approve_live_stores_order_id_in_trade were fixed to pass use_limit_buy=False since Config.use_limit_buy defaults True (Rule 3 auto-fix)"
  - "place_limit_order assertion uses 4-arg form: (ticker, shares, price, config) — matches Wave 1 signature"

patterns-established:
  - "Pattern: scan_time captured once before the ticker loop in run_scan — never per-ticker (D-09, Pitfall 4)"
  - "Pattern: optional scan_time kwarg with None default — all callers backward-compatible when kwarg omitted"

requirements-completed: [RISK-01, RISK-02, RISK-03, RISK-04]

duration: 30min
completed: 2026-05-19
---

# Phase 17 Plan 02: Limit Buy Discord Wiring Summary

**Discord approval routing for GTC limit orders: scan_time Price field staleness timestamp, use_limit_buy routing in ApproveRejectView.approve, limit_price/order_type create_trade audit trail, and 9 new tests covering all RISK requirements**

## Performance

- **Duration:** 30 min
- **Started:** 2026-05-19T00:00:00Z
- **Completed:** 2026-05-19T00:30:00Z
- **Tasks:** 3 (Tasks 1 and 2 each had RED + GREEN commits)
- **Files modified:** 5

## Accomplishments

- Added `scan_time: str | None = None` kwarg to `build_recommendation_embed` — Price field renders `"$X.XX\nas of HH:MM"` when provided, `"$X.XX"` when omitted (backward compat)
- `build_etf_recommendation_embed` deliberately left untouched — scope guard verified by test and grep
- Added `scan_time` kwarg to `ApproveRejectView.__init__` (stored as `self.scan_time`) and `send_recommendation`
- Replaced single-path `place_order` call with limit/market routing branch in `ApproveRejectView.approve`:
  - `use_limit_buy=True, dry_run=False` → `place_limit_order(ticker, shares, price, config)`
  - `use_limit_buy=False, dry_run=False` → `place_order(ticker, shares, config)` (unchanged)
  - `dry_run=True` → no order placed (unchanged)
- `limit_price_val` and `order_type_val` initialized to `(None, "market")` OUTSIDE the `if not dry_run` block — D-06 fully enforced
- `create_trade` now always receives `limit_price` and `order_type` kwargs on the approve path
- Three-branch confirmation message: `[DRY RUN]` / `(limit, GTC)` / plain market wording
- `main.py`: `scan_time = datetime.now().strftime("%H:%M")` captured before `for ticker in universe:` loop (line 101 < 103); passed as `scan_time=scan_time` to `send_recommendation`
- 9 new tests added: 3 in `test_discord_embeds.py` + 6 in `test_discord_buttons.py`
- Full suite: 436 tests passing (0 regressions)

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: add failing tests for scan_time Price field** - `ff2bad6` (test)
2. **Task 1 GREEN: add scan_time to build_recommendation_embed Price field** - `df0a5c7` (feat)
3. **Task 2 RED: add failing tests for limit/market routing in ApproveRejectView** - `488543a` (test)
4. **Task 2 GREEN: wire limit/market routing in ApproveRejectView.approve and send_recommendation** - `e73b960` (feat)
5. **Task 3: capture scan_time before ticker loop and wire to send_recommendation** - `429a840` (feat)

## Files Created/Modified

- `discord_bot/embeds.py` — Added `scan_time: str | None = None` param to `build_recommendation_embed`; Price field now uses `price_value` variable with conditional `\nas of {scan_time}` append
- `discord_bot/bot.py` — Added `place_limit_order` import; `ApproveRejectView.__init__` gets `scan_time` param; approve handler replaced with limit/market routing + D-06 initialization pattern + three-branch confirmation; `send_recommendation` gets `scan_time` kwarg forwarded to embed and view
- `main.py` — `scan_time = datetime.now().strftime("%H:%M")` added before ticker loop; `scan_time=scan_time` added to `send_recommendation` call
- `tests/test_discord_embeds.py` — 3 new tests: `test_embed_price_field_includes_scan_time_when_provided`, `test_embed_price_field_plain_when_no_scan_time`, `test_etf_embed_has_no_scan_time_parameter`
- `tests/test_discord_buttons.py` — Updated `_make_config` and `_make_view` helpers with `use_limit_buy` param; fixed 2 existing live tests to pass `use_limit_buy=False`; 6 new tests for limit/market routing, create_trade kwargs, and confirmation messages

## Decisions Made

- **D-06 enforcement**: `limit_price_val = None` and `order_type_val = "market"` are initialized before `if not self.config.dry_run:` block. This guarantees dry-run always writes `order_type='market'` and `limit_price=None` regardless of `use_limit_buy` setting. T-17-08 (Tampering: dry-run labeled as limit) fully mitigated.
- **ETF routing behavior**: ETF approvals also route through `ApproveRejectView.approve`, so `use_limit_buy=True` will place GTC limit orders for ETF buys too. This is intentional and strictly safer than market orders — documented in plan objective and threat model (T-17-09 accepted).
- **Existing live test updates**: `test_approve_live_calls_place_order` and `test_approve_live_stores_order_id_in_trade` updated to pass `use_limit_buy=False` — both previously relied on the old single-path `place_order` call; with `_make_config` defaulting `use_limit_buy=True`, they would have routed to `place_limit_order` instead (Rule 3 auto-fix).
- **4-arg `place_limit_order` assertion**: Test asserts `mock_place_limit_order.assert_called_once_with("AAPL", 5, 100.0, view.config)` matching the Wave 1 signature `(ticker, shares, limit_price, config)`.

## D-05 through D-11 Implementation Traceability

| Decision | Implementation | Verified |
|----------|---------------|---------|
| D-05: use_limit_buy flag drives routing | `if self.config.use_limit_buy:` branch in approve | grep shows 2 references in bot.py |
| D-06: dry-run records market defaults | `limit_price_val=None, order_type_val="market"` outside if-block | test_approve_dry_run_create_trade_records_market_regardless_of_use_limit_buy |
| D-07: Price field shows "as of HH:MM" | `price_value += f"\nas of {scan_time}"` | test_embed_price_field_includes_scan_time_when_provided |
| D-08: Price field plain when no scan_time | `if scan_time is not None:` guard | test_embed_price_field_plain_when_no_scan_time |
| D-09: scan_time captured once pre-loop | `scan_time = datetime.now().strftime("%H:%M")` at line 101 | grep confirms line 101 < line 103 |
| D-10: "(limit, GTC)" in live limit message | `f"...${self.price:.2f} (limit, GTC)."` branch | test_approve_limit_live_confirmation_message_contains_limit_gtc |
| D-11: no "(limit, GTC)" for dry-run/market | Three-branch message routing | test_approve_market_live_confirmation_message_does_not_contain_limit_gtc |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Updated existing live tests to use use_limit_buy=False**
- **Found during:** Task 2 implementation (before running tests)
- **Issue:** `test_approve_live_calls_place_order` and `test_approve_live_stores_order_id_in_trade` used `_make_view(dry_run=False)` which after helper update defaults `use_limit_buy=True`, routing to `place_limit_order` instead of `place_order` — both assertions would have failed
- **Fix:** Added `use_limit_buy=False` to both calls, with comments explaining the intent
- **Files modified:** `tests/test_discord_buttons.py`
- **Commit:** `488543a` (included in the RED commit before implementation)

## Known Stubs

None — all Plan 02 behaviors are fully wired. Price field shows real scan_time from `datetime.now()`. Routing uses live `config.use_limit_buy`. No placeholder data.

## Threat Flags

None — all threat surface introduced in this plan was pre-catalogued in the plan's threat model (T-17-06 through T-17-10). No new surface discovered during implementation.

## Key Link Verification

All `must_haves.key_links` verified:

| Link | Pattern | Status |
|------|---------|--------|
| main.py → send_recommendation via scan_time | `scan_time=scan_time` at line 256 | VERIFIED |
| send_recommendation → build_recommendation_embed via scan_time | `build_recommendation_embed.*scan_time` at line 288 | VERIFIED |
| send_recommendation → ApproveRejectView.__init__ via scan_time | `ApproveRejectView.*scan_time` at line 289 | VERIFIED |
| ApproveRejectView.approve → place_limit_order via use_limit_buy | `use_limit_buy.*place_limit_order` at line 77-78 | VERIFIED |
| ApproveRejectView.approve → create_trade with limit_price/order_type | `create_trade.*limit_price.*order_type` kwargs at lines 87-96 | VERIFIED |

---
*Phase: 17-limit-buy-orders*
*Completed: 2026-05-19*
