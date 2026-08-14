# Execution Ledger Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record positions and P&L from confirmed broker executions instead of order acknowledgements, so the trade ledger reflects what actually happened — for buys *and* sells, exactly once, at real fill prices.

**Architecture:** An `orders` table sits between `recommendations` and `trades`. The order row is created **before** broker submission so a broker-accepted order can never exist outside the ledger. The Approve button stops there. A poller reads broker status, and every fill is applied by a **single transactional function** that writes the trade, mutates the position, and advances the order's fill counters together — with optimistic concurrency so two pollers cannot double-count.

**Tech Stack:** Python 3.11, SQLite (WAL), schwab-py, discord.py, APScheduler, pytest, pytest-asyncio

**Spec:** `docs/superpowers/plans/2026-08-14-codex-backlog-roadmap.md` (Workstream A); source findings in `docs/superpowers/codex_recommendations.md` §4 and §10

**Revision note (v2):** v1 was reviewed externally and had four Critical and five High defects,
all fixed here. See "What v2 changes" below.

## Global Constraints

- Python 3.11; SQLite via `database/models.py:get_cursor` — never open raw connections
- All broker I/O inside async functions wrapped in `await asyncio.to_thread(...)`
- Tests set `config.dry_run = True` or patch the broker call; the suite never reaches live Schwab
- **Day-bucketing uses the US market session** via `market_time.market_session_bounds_utc()`,
  as a range predicate (`col >= ? AND col < ?`). **Neither `'localtime'` nor bare UTC** — see
  CLAUDE.md and commit `36761da`. A v2 draft of this plan used `'localtime'`; on this UTC+8
  host that splits one US session across two local dates and resets any daily ceiling
  mid-session. Instant comparisons (expiry, `expires_at`) are a different thing and stay bare
  UTC `datetime('now')`.
- New tables via `CREATE TABLE IF NOT EXISTS`; new columns via `try: ALTER TABLE / except sqlite3.OperationalError: pass`
- Test files use module-level `DB_PATH` with an `autouse` `fresh_db` fixture, matching `tests/test_positions.py`
- **Prerequisite:** Phase 1 (`docs/superpowers/specs/2026-08-14-live-trading-safety-design.md`) must land first. Task 6 modifies the approval handler Phase 1 rewrites.
- **Prerequisite:** resolve the schwab-py pin. `requirements.txt:172` pins `1.4.0`; every API fact here was verified against `1.5.1`, which is what is installed. Bump the pin and regenerate the lock, or re-verify `get_order` and the status enum against 1.4.0.
- Commit after every task

---

## What v2 changes

| v1 defect | Severity | Fix |
|---|---|---|
| Sell fills never reduced the position | Critical | `record_fill` handles both sides; sells decrement and close |
| Fill application across three transactions, not idempotent | Critical | One transactional `record_fill` with optimistic concurrency on `filled_shares` |
| Redefining `trades` as fills broke Phase 1's daily-notional budget | Critical | Daily notional and exposure now read from `orders` at `reference_price` |
| Broker placement before `create_order` could orphan a real order | Critical | Order row created first, in `pending_submit`, then the broker id is attached |
| Incremental fills priced at the *cumulative* average | High | Track `filled_notional`; incremental price = Δnotional / Δshares |
| Missing fill price permanently discarded the fill | High | Unpriced fills defer — counters are not advanced, so the next poll retries |
| `asyncio.create_task` under a `BackgroundScheduler` worker thread | High | `asyncio.run_coroutine_threadsafe(..., bot.loop)`, matching existing jobs |
| `REPLACED` orders abandoned their successor | High | Successor id parsed and a linked order row created |
| Poller sell trades had no `cost_basis`, so `/stats` showed nothing | High | `record_fill` reads position avg cost inside the same transaction |
| Only `fetch_order_status` was inside the per-order `try` | Medium | Whole per-order body wrapped |
| Dry-run created no positions at all, breaking forward validation | Medium | Dry-run synthesises a full fill at `reference_price` |
| No execution time, no fees | Medium | `executed_at` from leg data; `fees` column on `trades` |

---

### Task 1: `orders` table, `trades` columns, and CRUD

**Files:**
- Modify: `database/models.py` (new table + two `trades` columns)
- Modify: `database/queries.py`
- Test: `tests/test_orders_table.py`

**Interfaces:**
- Produces:
  - `create_order(db_path, recommendation_id, ticker, side, order_type, requested_shares, reference_price, limit_price=None) -> int` (status `pending_submit`, no broker id)
  - `attach_broker_order_id(db_path, order_id, broker_order_id) -> None` (→ `submitted`)
  - `mark_order_submit_failed(db_path, order_id, reason) -> None`
  - `get_orders_by_status(db_path, statuses) -> list[dict]`
  - `get_order(db_path, order_id) -> dict | None`
  - `get_day_notional(db_path) -> float`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orders_table.py
import os
import sqlite3
import pytest
from database.models import initialize_db
from database.queries import (
    create_order, attach_broker_order_id, mark_order_submit_failed,
    get_orders_by_status, get_order, get_day_notional, create_recommendation,
)

DB_PATH = "test_orders_table.db"


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def _rec(ticker="AAPL"):
    return create_recommendation(
        db_path=DB_PATH, ticker=ticker, signal="BUY", reasoning="t", price=100.0
    )


def _order(ticker="AAPL", shares=5, ref=100.0, side="buy"):
    return create_order(DB_PATH, _rec(ticker), ticker, side, "market", shares, ref)


def test_orders_table_exists():
    conn = sqlite3.connect(DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "orders" in tables


def test_new_order_starts_pending_submit_with_no_broker_id():
    """The row must exist BEFORE the broker call, so a real order is never orphaned."""
    oid = _order()
    row = get_order(DB_PATH, oid)
    assert row["status"] == "pending_submit"
    assert row["broker_order_id"] is None
    assert row["filled_shares"] == 0
    assert row["filled_notional"] == 0


def test_attach_broker_id_moves_to_submitted():
    oid = _order()
    attach_broker_order_id(DB_PATH, oid, "BRK1")
    row = get_order(DB_PATH, oid)
    assert row["status"] == "submitted"
    assert row["broker_order_id"] == "BRK1"


def test_submit_failure_is_terminal():
    oid = _order()
    mark_order_submit_failed(DB_PATH, oid, "connection refused")
    assert get_order(DB_PATH, oid)["status"] == "submit_failed"


def test_get_orders_by_status_filters():
    o1 = _order("AAPL")
    o2 = _order("MSFT")
    attach_broker_order_id(DB_PATH, o2, "BRK2")
    assert [o["ticker"] for o in get_orders_by_status(DB_PATH, ("pending_submit",))] == ["AAPL"]
    assert [o["ticker"] for o in get_orders_by_status(DB_PATH, ("submitted",))] == ["MSFT"]


def test_broker_order_id_is_unique():
    o1, o2 = _order("AAPL"), _order("MSFT")
    attach_broker_order_id(DB_PATH, o1, "BRK1")
    with pytest.raises(sqlite3.IntegrityError):
        attach_broker_order_id(DB_PATH, o2, "BRK1")


# --- daily notional comes from ORDERS, not trades (Phase 1 budget) ---

def test_day_notional_counts_submitted_orders_not_fills():
    """An approved-but-unfilled order must still consume the daily budget."""
    o = _order(shares=5, ref=100.0)
    attach_broker_order_id(DB_PATH, o, "BRK1")
    assert get_day_notional(DB_PATH) == 500.0


def test_day_notional_counts_pending_submit_orders():
    _order(shares=5, ref=100.0)
    assert get_day_notional(DB_PATH) == 500.0


def test_day_notional_excludes_rejected_and_failed():
    o = _order(shares=5, ref=100.0)
    mark_order_submit_failed(DB_PATH, o, "boom")
    assert get_day_notional(DB_PATH) == 0.0


def test_day_notional_excludes_sells():
    _order(shares=5, ref=100.0, side="sell")
    assert get_day_notional(DB_PATH) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orders_table.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_order'`

- [ ] **Step 3: Add the schema**

In `database/models.py`, inside `executescript`:

```sql
        CREATE TABLE IF NOT EXISTS orders (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER NOT NULL,
            broker_order_id   TEXT UNIQUE,
            ticker            TEXT NOT NULL,
            side              TEXT NOT NULL,
            order_type        TEXT NOT NULL,
            requested_shares  REAL NOT NULL,
            limit_price       REAL,
            reference_price   REAL NOT NULL,
            status            TEXT NOT NULL DEFAULT 'pending_submit',
            filled_shares     REAL NOT NULL DEFAULT 0,
            filled_notional   REAL NOT NULL DEFAULT 0,
            failure_reason    TEXT,
            replaced_by       TEXT,
            submitted_at      TEXT NOT NULL DEFAULT (datetime('now')),
            last_polled_at    TEXT,
            closed_at         TEXT,
            FOREIGN KEY (recommendation_id) REFERENCES recommendations(id)
        );
```

And with the other `ALTER TABLE` migrations:

```python
    for _col in ("fees REAL DEFAULT 0", "order_row_id INTEGER"):
        try:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {_col}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists
```

- [ ] **Step 4: Add the CRUD functions**

Append to `database/queries.py`:

```python
# --- Order ledger (Workstream A) ---
from datetime import datetime

from market_time import market_session_bounds_utc

OPEN_ORDER_STATUSES = ("pending_submit", "submitted", "working", "partially_filled")
UNRESOLVED_ORDER_STATUSES = ("submit_unknown",)
TERMINAL_ORDER_STATUSES = ("filled", "cancelled", "rejected", "submit_failed")

# Statuses that still commit capital, for daily-notional and exposure reservation.
# submit_unknown is included deliberately: the broker call was ambiguous, so the
# order MAY exist and MAY have committed real capital. Assuming otherwise is the
# fail-open direction — it lets a possibly-live $500 order count $0 against both
# ceilings. See spec section 4.
COMMITTING_ORDER_STATUSES = OPEN_ORDER_STATUSES + UNRESOLVED_ORDER_STATUSES + ("filled",)

# Statuses that block a second buy of the same symbol (preflight guards 10/11).
BLOCKING_ORDER_STATUSES = OPEN_ORDER_STATUSES + UNRESOLVED_ORDER_STATUSES

# submit_unknown is intentionally absent from TERMINAL_ORDER_STATUSES: nothing may
# sweep it away automatically. Only main.py::resolve_unknown_submissions clears it,
# by querying Client.get_orders_for_account — /reconcile reads positions and cannot
# distinguish "unfilled working order" from "no order at all".


def create_order(
    db_path: str, recommendation_id: int, ticker: str, side: str, order_type: str,
    requested_shares: float, reference_price: float, limit_price: float | None = None,
) -> int:
    """Insert an order in 'pending_submit' and return its row id.

    Called BEFORE the broker request. If the process dies between this insert
    and the broker response, the row survives in 'pending_submit' and
    reconciliation can ask the broker whether the order actually landed. The
    reverse order — submit, then insert — can leave a real position with no
    ledger entry at all.
    """
    with get_cursor(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO orders
                   (recommendation_id, ticker, side, order_type,
                    requested_shares, reference_price, limit_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (recommendation_id, ticker, side, order_type,
             requested_shares, reference_price, limit_price),
        )
        return cursor.lastrowid


def attach_broker_order_id(db_path: str, order_id: int, broker_order_id: str) -> None:
    """Record the broker's order id and move the row to 'submitted'."""
    with get_cursor(db_path) as conn:
        conn.execute(
            """UPDATE orders SET broker_order_id = ?, status = 'submitted'
                WHERE id = ?""",
            (broker_order_id, order_id),
        )


def mark_order_submit_failed(db_path: str, order_id: int, reason: str) -> None:
    """Mark an order as never submitted. Terminal; releases its budget."""
    with get_cursor(db_path) as conn:
        conn.execute(
            """UPDATE orders
                  SET status = 'submit_failed', failure_reason = ?,
                      closed_at = datetime('now')
                WHERE id = ?""",
            (reason, order_id),
        )


def get_orders_by_status(db_path: str, statuses: tuple[str, ...]) -> list[dict]:
    """Return orders whose status is in statuses, oldest first."""
    placeholders = ",".join("?" for _ in statuses)
    with get_cursor(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM orders WHERE status IN ({placeholders}) ORDER BY id",
            statuses,
        ).fetchall()
    return [dict(r) for r in rows]


def get_order(db_path: str, order_id: int) -> dict | None:
    """Return one order row by primary key."""
    with get_cursor(db_path) as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def get_day_notional(db_path: str, instant: datetime | None = None) -> float:
    """This SESSION's committed buy notional, valued at each order's reference price.

    Reads ORDERS, not trades. Phase 1's daily ceiling must be consumed at
    approval time: if it counted fills, several buys could all be approved
    before any fill was visible and jointly blow through the cap. Rejected,
    cancelled, and never-submitted orders release their budget.

    "Today" is the US MARKET SESSION date, not the host calendar date. An
    earlier draft of this plan used date(submitted_at,'localtime') — that is
    the convention commit 36761da removed and CLAUDE.md now forbids. This host
    is Asia/Taipei (UTC+8), where SCAN_TIMES=21:45,03:30 are 09:45 ET and
    15:30 ET of ONE session but TWO local dates. A localtime bucket therefore
    resets the ceiling mid-session and admits double MAX_DAILY_NOTIONAL_USD —
    the same defect that was doubling ANALYST_DAILY_LIMIT to 36.

    The range predicate leaves submitted_at unwrapped and so index-usable.
    `instant` is injectable so tests can pin time without freezegun.
    """
    start, end = market_session_bounds_utc(instant)
    placeholders = ",".join("?" for _ in COMMITTING_ORDER_STATUSES)
    with get_cursor(db_path) as conn:
        row = conn.execute(
            f"""SELECT COALESCE(SUM(requested_shares * reference_price), 0.0) AS total
                  FROM orders
                 WHERE side = 'buy'
                   AND status IN ({placeholders})
                   AND submitted_at >= ? AND submitted_at < ?""",
            (*COMMITTING_ORDER_STATUSES, start, end),
        ).fetchone()
    return float(row["total"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_orders_table.py -v`
Expected: 10 passed

- [ ] **Step 6: Commit**

```bash
git add database/ tests/test_orders_table.py
git commit -m "feat: orders table with pre-submission row creation and order-based day notional"
```

---

### Task 2: Broker status mapping and response parsing

**Files:**
- Create: `execution/__init__.py` (empty), `execution/status.py`
- Test: `tests/test_order_status_mapping.py`

**Interfaces:**
- Produces:
  - `map_broker_status(broker_status, filled_qty, requested_qty) -> str`
  - `parse_order_response(payload: dict) -> dict` with keys `broker_status, filled_qty, requested_qty, filled_notional, avg_fill_price, last_execution_time, fees, replaced_by, internal_status`
  - `TERMINAL: frozenset[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_order_status_mapping.py
import pytest
from execution.status import map_broker_status, parse_order_response, TERMINAL


@pytest.mark.parametrize("s", [
    "WORKING", "QUEUED", "ACCEPTED", "NEW", "PENDING_ACTIVATION",
    "PENDING_ACKNOWLEDGEMENT", "AWAITING_CONDITION", "AWAITING_MANUAL_REVIEW",
    "AWAITING_PARENT_ORDER", "AWAITING_RELEASE_TIME", "AWAITING_STOP_CONDITION",
    "AWAITING_UR_OUT", "PENDING_CANCEL", "PENDING_RECALL", "PENDING_REPLACE",
])
def test_non_terminal_statuses_map_to_working(s):
    assert map_broker_status(s, 0, 10) == "working"


@pytest.mark.parametrize("s,expected", [
    ("FILLED", "filled"),
    ("CANCELED", "cancelled"),
    ("CANCELLED", "cancelled"),
    ("EXPIRED", "cancelled"),
    ("REPLACED", "replaced"),
    ("REJECTED", "rejected"),
])
def test_terminal_statuses(s, expected):
    assert map_broker_status(s, 0, 10) == expected


def test_partial_fill_on_open_order():
    assert map_broker_status("WORKING", 4, 10) == "partially_filled"


def test_cancel_after_partial_fill_is_still_cancelled():
    assert map_broker_status("CANCELED", 4, 10) == "cancelled"


def test_unknown_status_maps_to_working_never_to_a_fill():
    assert map_broker_status("SOME_NEW_SCHWAB_STATUS", 0, 10) == "working"
    assert map_broker_status("UNKNOWN", 0, 10) == "working"


def test_unknown_status_with_fills_reports_partial_not_filled():
    """Fill quantity may be trusted; an unrecognised status may not."""
    assert map_broker_status("UNKNOWN", 4, 10) == "partially_filled"


def test_status_is_case_insensitive():
    assert map_broker_status("filled", 10, 10) == "filled"


def test_terminal_set():
    assert TERMINAL == frozenset({"filled", "cancelled", "rejected", "replaced"})


# --- parse_order_response ---

def test_parses_share_weighted_notional_and_price():
    payload = {
        "status": "FILLED", "quantity": 10, "filledQuantity": 10,
        "orderActivityCollection": [{"executionLegs": [
            {"quantity": 6, "price": 100.00, "time": "2026-08-14T14:30:00+0000"},
            {"quantity": 4, "price": 101.00, "time": "2026-08-14T14:31:00+0000"},
        ]}],
    }
    r = parse_order_response(payload)
    assert r["internal_status"] == "filled"
    assert r["filled_notional"] == 1004.0          # 6*100 + 4*101
    assert r["avg_fill_price"] == 100.40
    assert r["last_execution_time"] == "2026-08-14T14:31:00+0000"


def test_unfilled_order_has_no_price_or_notional():
    r = parse_order_response({"status": "WORKING", "quantity": 10, "filledQuantity": 0})
    assert r["filled_notional"] == 0.0
    assert r["avg_fill_price"] is None


def test_filled_quantity_without_execution_legs_yields_no_notional():
    """Schwab can report a fill before leg detail arrives; the caller must defer."""
    r = parse_order_response({"status": "FILLED", "quantity": 10, "filledQuantity": 10})
    assert r["filled_qty"] == 10
    assert r["filled_notional"] is None
    assert r["avg_fill_price"] is None


def test_replacement_successor_is_extracted():
    payload = {
        "status": "REPLACED", "quantity": 10, "filledQuantity": 0,
        "replacingOrderCollection": [{"orderId": 999123}],
    }
    r = parse_order_response(payload)
    assert r["internal_status"] == "replaced"
    assert r["replaced_by"] == "999123"


def test_fees_are_summed():
    payload = {
        "status": "FILLED", "quantity": 1, "filledQuantity": 1,
        "orderActivityCollection": [{"executionLegs": [{"quantity": 1, "price": 10.0}]}],
        "orderFeeCollection": [{"feeValue": 0.65}, {"feeValue": 0.02}],
    }
    assert parse_order_response(payload)["fees"] == pytest.approx(0.67)


def test_missing_fields_do_not_raise():
    r = parse_order_response({})
    assert r["internal_status"] == "working"
    assert r["filled_qty"] == 0
    assert r["fees"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_order_status_mapping.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'execution'`

- [ ] **Step 3: Write the implementation**

```python
# execution/status.py
"""Map Schwab broker order statuses onto the internal order lifecycle.

Pure module: imports nothing from schwab, discord, or the database, so its
tests need no fixtures. schwab-py exposes ~21 status values via
Client.Order.Status; they collapse into a small internal set.
"""
from __future__ import annotations

TERMINAL = frozenset({"filled", "cancelled", "rejected", "replaced"})

_CANCELLED = {"CANCELED", "CANCELLED", "EXPIRED"}
_REJECTED = {"REJECTED"}
_FILLED = {"FILLED"}
_REPLACED = {"REPLACED"}


def map_broker_status(broker_status: str, filled_qty: float, requested_qty: float) -> str:
    """Return the internal lifecycle state for a broker status plus fill quantities.

    Terminal broker states win over fill quantity: an order cancelled after a
    partial fill is 'cancelled', because the remainder will never arrive. An
    unrecognised status maps to 'working' when nothing has filled — a new Schwab
    status must never conjure a completed order. Fill *quantity*, unlike status,
    is trusted, so an unknown status with partial fills reports partial.
    """
    status = (broker_status or "").strip().upper()

    if status in _REJECTED:
        return "rejected"
    if status in _CANCELLED:
        return "cancelled"
    if status in _REPLACED:
        return "replaced"
    if status in _FILLED:
        return "filled"
    if filled_qty > 0:
        return "partially_filled" if filled_qty < requested_qty else "filled"
    return "working"


def parse_order_response(payload: dict) -> dict:
    """Extract lifecycle state and fill data from a Schwab get_order response.

    filled_notional is share-weighted across execution legs — a 6-share leg at
    $100 and a 4-share leg at $101 total $1,004, not 10 x $100.50. It is None
    (not 0.0) when the broker reports filled quantity without leg detail, so the
    caller can DEFER rather than record an unpriced fill.
    """
    broker_status = payload.get("status", "")
    requested_qty = float(payload.get("quantity", 0) or 0)
    filled_qty = float(payload.get("filledQuantity", 0) or 0)

    total_shares = 0.0
    total_notional = 0.0
    last_time = None
    for activity in payload.get("orderActivityCollection", []) or []:
        for leg in activity.get("executionLegs", []) or []:
            qty = float(leg.get("quantity", 0) or 0)
            price = float(leg.get("price", 0) or 0)
            total_shares += qty
            total_notional += qty * price
            if leg.get("time"):
                last_time = leg["time"]

    if total_shares > 0:
        filled_notional = round(total_notional, 6)
        avg_fill_price = round(total_notional / total_shares, 4)
    elif filled_qty > 0:
        filled_notional = None      # reported a fill but gave no leg detail
        avg_fill_price = None
    else:
        filled_notional = 0.0
        avg_fill_price = None

    fees = sum(
        float(f.get("feeValue", 0) or 0)
        for f in payload.get("orderFeeCollection", []) or []
    )

    replaced_by = None
    for successor in payload.get("replacingOrderCollection", []) or []:
        if successor.get("orderId"):
            replaced_by = str(successor["orderId"])
            break

    return {
        "broker_status": broker_status,
        "filled_qty": filled_qty,
        "requested_qty": requested_qty,
        "filled_notional": filled_notional,
        "avg_fill_price": avg_fill_price,
        "last_execution_time": last_time,
        "fees": fees,
        "replaced_by": replaced_by,
        "internal_status": map_broker_status(broker_status, filled_qty, requested_qty),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_order_status_mapping.py -v`
Expected: 21 parametrized + 11 standalone = **32 passed**

- [ ] **Step 5: Commit**

```bash
git add execution/ tests/test_order_status_mapping.py
git commit -m "feat: map Schwab order statuses and parse fill data"
```

---

### Task 3: Transactional fill application

**Files:**
- Modify: `database/queries.py`
- Test: `tests/test_record_fill.py`

**Interfaces:**
- Produces: `record_fill(db_path, order_id, expected_filled_shares, new_filled_shares, new_filled_notional, status, fees=0.0, executed_at=None) -> dict | None`

**This is the heart of the workstream.** One function, one transaction, both sides.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_record_fill.py
import os
import pytest
from database.models import initialize_db
from database.queries import (
    create_recommendation, create_order, attach_broker_order_id, record_fill,
    get_order, get_open_positions, get_all_trades, create_position,
)

DB_PATH = "test_record_fill.db"


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def _buy_order(shares=10.0, ref=100.0, ticker="AAPL"):
    rec = create_recommendation(
        db_path=DB_PATH, ticker=ticker, signal="BUY", reasoning="t", price=ref
    )
    oid = create_order(DB_PATH, rec, ticker, "buy", "limit", shares, ref, limit_price=ref)
    attach_broker_order_id(DB_PATH, oid, f"BRK-{oid}")
    return oid


def _sell_order(shares=10.0, ref=110.0, ticker="AAPL"):
    rec = create_recommendation(
        db_path=DB_PATH, ticker=ticker, signal="SELL", reasoning="t", price=ref
    )
    oid = create_order(DB_PATH, rec, ticker, "sell", "market", shares, ref)
    attach_broker_order_id(DB_PATH, oid, f"BRK-{oid}")
    return oid


# --- buys ---

def test_buy_fill_creates_position_at_actual_fill_price():
    oid = _buy_order()
    record_fill(DB_PATH, oid, 0.0, 10.0, 1037.5, "filled")
    pos = get_open_positions(DB_PATH)
    assert len(pos) == 1 and pos[0]["shares"] == 10
    assert pos[0]["avg_cost_usd"] == 103.75      # NOT the 100.00 reference price


def test_incremental_fill_prices_only_the_new_shares():
    """4 @ $100 then 3 @ $110 must record the second tranche at $110, not $104.29."""
    oid = _buy_order()
    record_fill(DB_PATH, oid, 0.0, 4.0, 400.0, "partially_filled")
    record_fill(DB_PATH, oid, 4.0, 7.0, 730.0, "partially_filled")
    trades = get_all_trades(DB_PATH)
    assert [t["shares"] for t in trades] == [4.0, 3.0]
    assert trades[1]["price"] == pytest.approx(110.0)


# --- sells (v1 had no sell path at all) ---

def test_sell_fill_reduces_the_position():
    create_position(DB_PATH, "AAPL", shares=10, avg_cost_usd=100.0)
    oid = _sell_order(shares=4.0)
    record_fill(DB_PATH, oid, 0.0, 4.0, 440.0, "filled")
    pos = get_open_positions(DB_PATH)
    assert pos[0]["shares"] == 6


def test_full_sell_closes_the_position():
    create_position(DB_PATH, "AAPL", shares=10, avg_cost_usd=100.0)
    oid = _sell_order(shares=10.0)
    record_fill(DB_PATH, oid, 0.0, 10.0, 1100.0, "filled")
    assert get_open_positions(DB_PATH) == []


def test_sell_trade_carries_cost_basis_so_stats_can_see_it():
    """v1 regression: poller sells had NULL cost_basis and vanished from /stats."""
    create_position(DB_PATH, "AAPL", shares=10, avg_cost_usd=100.0)
    oid = _sell_order(shares=10.0)
    record_fill(DB_PATH, oid, 0.0, 10.0, 1100.0, "filled")
    assert get_all_trades(DB_PATH)[0]["cost_basis"] == 100.0


def test_sell_without_a_position_records_the_trade_but_creates_nothing():
    oid = _sell_order(shares=10.0)
    record_fill(DB_PATH, oid, 0.0, 10.0, 1100.0, "filled")
    assert get_open_positions(DB_PATH) == []
    assert len(get_all_trades(DB_PATH)) == 1


# --- idempotency and atomicity ---

def test_stale_expected_value_is_rejected():
    """Optimistic concurrency: a second poller with an outdated view loses."""
    oid = _buy_order()
    assert record_fill(DB_PATH, oid, 0.0, 10.0, 1000.0, "filled") is not None
    assert record_fill(DB_PATH, oid, 0.0, 10.0, 1000.0, "filled") is None
    assert get_open_positions(DB_PATH)[0]["shares"] == 10       # not 20
    assert len(get_all_trades(DB_PATH)) == 1


def test_order_counters_advance_with_the_trade():
    oid = _buy_order()
    record_fill(DB_PATH, oid, 0.0, 4.0, 400.0, "partially_filled")
    row = get_order(DB_PATH, oid)
    assert row["filled_shares"] == 4.0
    assert row["filled_notional"] == 400.0
    assert row["status"] == "partially_filled"


def test_terminal_status_stamps_closed_at():
    oid = _buy_order()
    record_fill(DB_PATH, oid, 0.0, 10.0, 1000.0, "filled")
    assert get_order(DB_PATH, oid)["closed_at"] is not None


def test_fees_and_execution_time_are_recorded():
    oid = _buy_order()
    record_fill(DB_PATH, oid, 0.0, 10.0, 1000.0, "filled",
                fees=0.65, executed_at="2026-08-14T14:31:00+0000")
    t = get_all_trades(DB_PATH)[0]
    assert t["fees"] == 0.65
    assert t["executed_at"] == "2026-08-14T14:31:00+0000"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_record_fill.py -v`
Expected: FAIL — `ImportError: cannot import name 'record_fill'`

- [ ] **Step 3: Write the implementation**

Append to `database/queries.py`:

```python
def record_fill(
    db_path: str,
    order_id: int,
    expected_filled_shares: float,
    new_filled_shares: float,
    new_filled_notional: float,
    status: str,
    fees: float = 0.0,
    executed_at: str | None = None,
) -> dict | None:
    """Apply a confirmed incremental fill: trade + position + order counters, atomically.

    Returns the applied delta, or None when another writer already advanced this
    order past expected_filled_shares (optimistic concurrency).

    Everything happens in ONE transaction. v1 used three separate committed
    statements, so a crash between them left the order's counters behind the
    trades already written, and the next poll re-recorded the same fill.

    The incremental price is derived from the NOTIONAL delta, not the cumulative
    average: 4 shares at $100 followed by 3 at $110 must record the second
    tranche at $110. Using the running average ($104.29) misstates both the
    trade and the resulting position cost.
    """
    delta_shares = float(new_filled_shares) - float(expected_filled_shares)
    with get_cursor(db_path) as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if order is None:
            return None

        prior_notional = float(order["filled_notional"] or 0.0)
        delta_notional = float(new_filled_notional) - prior_notional

        # Optimistic guard: only advance if nobody else moved the counter.
        cursor = conn.execute(
            """UPDATE orders
                  SET filled_shares = ?, filled_notional = ?, status = ?,
                      last_polled_at = datetime('now'),
                      closed_at = CASE
                          WHEN ? IN ('filled','cancelled','rejected','replaced')
                               AND closed_at IS NULL
                          THEN datetime('now') ELSE closed_at END
                WHERE id = ? AND filled_shares = ?""",
            (new_filled_shares, new_filled_notional, status, status,
             order_id, expected_filled_shares),
        )
        if cursor.rowcount != 1:
            return None       # another poller won; leave everything untouched

        if delta_shares <= 0:
            return {"new_shares": 0.0, "fill_price": None, "status": status}

        fill_price = delta_notional / delta_shares
        ticker = order["ticker"]
        side = order["side"]

        cost_basis = None
        if side == "sell":
            pos = conn.execute(
                "SELECT * FROM positions WHERE ticker = ? AND status = 'open'",
                (ticker,),
            ).fetchone()
            if pos is not None:
                cost_basis = float(pos["avg_cost_usd"])

        conn.execute(
            """INSERT INTO trades
                   (recommendation_id, ticker, shares, price, order_id, side,
                    cost_basis, order_row_id, fees, executed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))""",
            (order["recommendation_id"], ticker, delta_shares, fill_price,
             order["broker_order_id"], side, cost_basis, order_id, fees, executed_at),
        )

        if side == "buy":
            existing = conn.execute(
                "SELECT * FROM positions WHERE ticker = ? AND status = 'open'",
                (ticker,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO positions (ticker, shares, avg_cost_usd)
                       VALUES (?, ?, ?)""",
                    (ticker, delta_shares, fill_price),
                )
            else:
                total_shares = float(existing["shares"]) + delta_shares
                total_cost = (
                    float(existing["shares"]) * float(existing["avg_cost_usd"])
                    + delta_shares * fill_price
                )
                conn.execute(
                    """UPDATE positions
                          SET shares = ?, avg_cost_usd = ?, last_updated = datetime('now')
                        WHERE id = ?""",
                    (total_shares, total_cost / total_shares, existing["id"]),
                )
        else:
            # Sells decrement and close. v1 had no sell branch at all, so a
            # confirmed sale left the DB showing shares the broker no longer had.
            existing = conn.execute(
                "SELECT * FROM positions WHERE ticker = ? AND status = 'open'",
                (ticker,),
            ).fetchone()
            if existing is not None:
                remaining = float(existing["shares"]) - delta_shares
                if remaining > 1e-9:
                    conn.execute(
                        """UPDATE positions SET shares = ?, last_updated = datetime('now')
                            WHERE id = ?""",
                        (remaining, existing["id"]),
                    )
                else:
                    conn.execute(
                        """UPDATE positions
                              SET shares = 0, status = 'closed',
                                  last_updated = datetime('now')
                            WHERE id = ?""",
                        (existing["id"],),
                    )

        return {"new_shares": delta_shares, "fill_price": fill_price, "status": status}


def get_all_trades(db_path: str) -> list[dict]:
    """Return every trade row, oldest first."""
    with get_cursor(db_path) as conn:
        rows = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_record_fill.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add database/queries.py tests/test_record_fill.py
git commit -m "feat: transactional, idempotent fill application for buys and sells"
```

---

### Task 4: Fetch order status from Schwab

**Files:**
- Modify: `schwab_client/orders.py`
- Test: `tests/test_fetch_order_status.py`

**Interfaces:**
- Produces: `fetch_order_status(broker_order_id, config, client=None) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fetch_order_status.py
import pytest
from unittest.mock import MagicMock
from schwab_client.orders import fetch_order_status


class _Cfg:
    schwab_account_hash = "HASH123"


def _resp(status_code=200, payload=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = payload or {"status": "FILLED"}
    return r


def test_calls_get_order_with_account_hash():
    client = MagicMock()
    client.get_order.return_value = _resp()
    assert fetch_order_status("BRK1", _Cfg(), client=client) == {"status": "FILLED"}
    client.get_order.assert_called_once_with("BRK1", "HASH123")


def test_http_error_raises_rather_than_parsing_the_body():
    """A 401 body must not be parsed into an empty 'working' order."""
    client = MagicMock()
    client.get_order.return_value = _resp(status_code=401, payload={"error": "unauthorized"})
    with pytest.raises(RuntimeError, match="BRK1"):
        fetch_order_status("BRK1", _Cfg(), client=client)


def test_exception_raises_runtime_error():
    client = MagicMock()
    client.get_order.side_effect = Exception("boom")
    with pytest.raises(RuntimeError, match="Order status fetch failed for BRK1"):
        fetch_order_status("BRK1", _Cfg(), client=client)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fetch_order_status.py -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_order_status'`

- [ ] **Step 3: Write the implementation**

Append to `schwab_client/orders.py`:

```python
@_retry
def _call_get_order(client, broker_order_id: str, account_hash: str):
    return client.get_order(broker_order_id, account_hash)


def fetch_order_status(broker_order_id: str, config, client=None) -> dict:
    """Return the raw Schwab order payload for broker_order_id.

    Status is checked explicitly: an error body parsed as JSON would otherwise
    look like an order with no status and no fills, i.e. a healthy 'working'
    order, and a broker outage would silently read as "nothing has filled".

    Retrying a read is safe, unlike retrying an order submission.
    """
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)
    try:
        resp = _call_get_order(client, broker_order_id, config.schwab_account_hash)
        status_code = getattr(resp, "status_code", 200)
        if status_code >= 400:
            raise RuntimeError(f"HTTP {status_code}")
        return resp.json()
    except Exception as exc:
        logger.error("Order status fetch failed for %s: %s", broker_order_id, exc)
        raise RuntimeError(f"Order status fetch failed for {broker_order_id}: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch_order_status.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add schwab_client/orders.py tests/test_fetch_order_status.py
git commit -m "feat: fetch broker order status with explicit HTTP status checking"
```

---

### Task 5: The poller

**Files:**
- Create: `execution/ledger.py`
- Test: `tests/test_poll_orders.py`

**Interfaces:**
- Produces: `async def poll_open_orders(config, client=None) -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_poll_orders.py
import os
import pytest
from unittest.mock import patch
from database.models import initialize_db
from database.queries import (
    create_recommendation, create_order, attach_broker_order_id, get_order,
    get_open_positions, get_all_trades, get_orders_by_status,
)
from execution.ledger import poll_open_orders

DB_PATH = "test_poll_orders.db"


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


class _Cfg:
    db_path = DB_PATH
    schwab_account_hash = "HASH"
    dry_run = False


def _seed(shares=10.0, ref=100.0, side="buy"):
    rec = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY", reasoning="t", price=ref
    )
    oid = create_order(DB_PATH, rec, "AAPL", side, "limit", shares, ref, limit_price=ref)
    attach_broker_order_id(DB_PATH, oid, "BRK1")
    return oid


def _payload(status="FILLED", qty=10, filled=10, legs=((10, 103.75),)):
    p = {"status": status, "quantity": qty, "filledQuantity": filled}
    if legs:
        p["orderActivityCollection"] = [
            {"executionLegs": [{"quantity": q, "price": pr} for q, pr in legs]}
        ]
    return p


@pytest.mark.asyncio
async def test_filled_order_creates_position_at_fill_price():
    _seed()
    with patch("execution.ledger.fetch_order_status", return_value=_payload()):
        await poll_open_orders(_Cfg())
    pos = get_open_positions(DB_PATH)
    assert pos[0]["avg_cost_usd"] == 103.75
    assert get_order(DB_PATH, 1)["status"] == "filled"


@pytest.mark.asyncio
async def test_unfilled_order_creates_no_position():
    """The core defect: a GTC limit that never fills must not create a position."""
    _seed()
    with patch("execution.ledger.fetch_order_status",
               return_value=_payload("WORKING", filled=0, legs=None)):
        await poll_open_orders(_Cfg())
    assert get_open_positions(DB_PATH) == []
    assert get_order(DB_PATH, 1)["status"] == "working"


@pytest.mark.asyncio
async def test_repolling_does_not_double_count():
    _seed()
    with patch("execution.ledger.fetch_order_status", return_value=_payload()):
        await poll_open_orders(_Cfg())
        await poll_open_orders(_Cfg())
    assert get_open_positions(DB_PATH)[0]["shares"] == 10
    assert len(get_all_trades(DB_PATH)) == 1


@pytest.mark.asyncio
async def test_fill_without_leg_data_defers_rather_than_losing_it():
    """Counters must NOT advance while the price is unknown, or the fill is lost."""
    _seed()
    with patch("execution.ledger.fetch_order_status",
               return_value=_payload("FILLED", filled=10, legs=None)):
        await poll_open_orders(_Cfg())
    assert get_order(DB_PATH, 1)["filled_shares"] == 0
    assert get_all_trades(DB_PATH) == []

    with patch("execution.ledger.fetch_order_status", return_value=_payload()):
        await poll_open_orders(_Cfg())
    assert get_open_positions(DB_PATH)[0]["shares"] == 10


@pytest.mark.asyncio
async def test_broker_failure_leaves_order_untouched():
    _seed()
    with patch("execution.ledger.fetch_order_status", side_effect=RuntimeError("api down")):
        await poll_open_orders(_Cfg())
    assert get_order(DB_PATH, 1)["status"] == "submitted"


@pytest.mark.asyncio
async def test_one_bad_order_does_not_stall_the_rest():
    """v1 wrapped only the fetch, so a parse error skipped every later order."""
    _seed()
    rec2 = create_recommendation(
        db_path=DB_PATH, ticker="MSFT", signal="BUY", reasoning="t", price=50.0
    )
    o2 = create_order(DB_PATH, rec2, "MSFT", "buy", "limit", 2, 50.0, limit_price=50.0)
    attach_broker_order_id(DB_PATH, o2, "BRK2")

    def _side_effect(broker_id, *a, **kw):
        if broker_id == "BRK1":
            return {"status": "FILLED", "quantity": "not-a-number"}
        return _payload(qty=2, filled=2, legs=((2, 50.0),))

    with patch("execution.ledger.fetch_order_status", side_effect=_side_effect):
        await poll_open_orders(_Cfg())

    assert [p["ticker"] for p in get_open_positions(DB_PATH)] == ["MSFT"]


@pytest.mark.asyncio
async def test_replacement_creates_a_successor_order():
    _seed()
    payload = {"status": "REPLACED", "quantity": 10, "filledQuantity": 0,
               "replacingOrderCollection": [{"orderId": 999123}]}
    with patch("execution.ledger.fetch_order_status", return_value=payload):
        await poll_open_orders(_Cfg())

    assert get_order(DB_PATH, 1)["replaced_by"] == "999123"
    successors = [o for o in get_orders_by_status(DB_PATH, ("submitted",))
                  if o["broker_order_id"] == "999123"]
    assert len(successors) == 1


@pytest.mark.asyncio
async def test_dry_run_synthesises_a_fill_at_reference_price():
    """Dry-run must still populate positions so forward validation works."""
    class _DryCfg(_Cfg):
        dry_run = True
    rec = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY", reasoning="t", price=100.0
    )
    create_order(DB_PATH, rec, "AAPL", "buy", "market", 5, 100.0)   # no broker id
    await poll_open_orders(_DryCfg())
    pos = get_open_positions(DB_PATH)
    assert pos[0]["shares"] == 5 and pos[0]["avg_cost_usd"] == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_poll_orders.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'execution.ledger'`

- [ ] **Step 3: Write the implementation**

```python
# execution/ledger.py
"""Poll broker orders and apply confirmed fills to the ledger.

This module is the ONLY place positions are created from broker activity.
All fill accounting is delegated to queries.record_fill, which applies the
trade, the position change, and the order counters in one transaction.
"""
from __future__ import annotations

import asyncio
import logging

from database import queries
from execution.status import parse_order_response
from schwab_client.orders import fetch_order_status

logger = logging.getLogger(__name__)


async def _poll_one(order: dict, config, client) -> dict | None:
    """Poll and apply a single order. Returns a summary, or None when skipped."""
    broker_id = order.get("broker_order_id")
    payload = await asyncio.to_thread(fetch_order_status, broker_id, config, client)
    parsed = parse_order_response(payload)

    # A reported fill with no execution-leg detail has no price. Advancing the
    # counters here would make the next poll see a zero delta and lose the fill
    # permanently, so defer instead and retry on the next tick.
    if parsed["filled_qty"] > float(order["filled_shares"] or 0) and parsed["filled_notional"] is None:
        logger.warning(
            "Order %s reports %s filled shares with no execution legs — deferring",
            broker_id, parsed["filled_qty"],
        )
        return {"ticker": order["ticker"], "status": "deferred", "filled": parsed["filled_qty"]}

    if parsed["replaced_by"]:
        await asyncio.to_thread(
            queries.link_replacement_order, config.db_path, order["id"], parsed["replaced_by"]
        )

    await asyncio.to_thread(
        queries.record_fill,
        config.db_path,
        order["id"],
        float(order["filled_shares"] or 0),
        parsed["filled_qty"],
        parsed["filled_notional"] or 0.0,
        parsed["internal_status"],
        parsed["fees"],
        parsed["last_execution_time"],
    )
    return {
        "ticker": order["ticker"],
        "status": parsed["internal_status"],
        "filled": parsed["filled_qty"],
    }


async def _simulate_dry_run_fills(config) -> list[dict]:
    """Fill every dry-run order at its reference price, immediately.

    Dry-run orders have no broker counterpart, so nothing would ever fill them.
    Without this, dry-run produces no positions at all and forward validation
    (Codex Phase 5) cannot exercise the sell pass, /positions, or /stats.
    """
    orders = await asyncio.to_thread(
        queries.get_orders_by_status, config.db_path, ("pending_submit",)
    )
    results = []
    for order in orders:
        shares = float(order["requested_shares"])
        price = float(order["reference_price"])
        await asyncio.to_thread(
            queries.record_fill, config.db_path, order["id"], 0.0,
            shares, shares * price, "filled", 0.0, None,
        )
        results.append({"ticker": order["ticker"], "status": "filled (simulated)",
                        "filled": shares})
    return results


async def poll_open_orders(config, client=None) -> list[dict]:
    """Poll every non-terminal order and apply confirmed fills."""
    if getattr(config, "dry_run", False):
        return await _simulate_dry_run_fills(config)

    open_orders = await asyncio.to_thread(
        queries.get_orders_by_status, config.db_path, queries.OPEN_ORDER_STATUSES
    )
    results = []
    for order in open_orders:
        if not order.get("broker_order_id"):
            # Created but never confirmed submitted. Reconciliation owns this
            # case — the order may or may not exist at the broker.
            logger.warning(
                "Order %s (%s) has no broker id — needs reconciliation",
                order["id"], order["ticker"],
            )
            continue
        try:
            summary = await _poll_one(order, config, client)
        except Exception as exc:
            # Per-order isolation covers the WHOLE body, not just the fetch:
            # a malformed payload must not stall every later order.
            logger.warning("Order poll failed for %s: %s", order.get("broker_order_id"), exc)
            continue
        if summary:
            results.append(summary)
    return results
```

- [ ] **Step 4: Add `link_replacement_order`**

Append to `database/queries.py`:

```python
def link_replacement_order(db_path: str, order_id: int, successor_broker_id: str) -> None:
    """Record a replacement successor and create a tracked order row for it.

    Schwab replacement cancels the old order and creates a new one. Without a
    row for the successor, the poller stops watching and misses its fill.
    """
    with get_cursor(db_path) as conn:
        original = conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if original is None:
            return
        conn.execute(
            "UPDATE orders SET replaced_by = ? WHERE id = ?",
            (successor_broker_id, order_id),
        )
        exists = conn.execute(
            "SELECT 1 FROM orders WHERE broker_order_id = ?", (successor_broker_id,)
        ).fetchone()
        if exists:
            return
        remaining = float(original["requested_shares"]) - float(original["filled_shares"] or 0)
        conn.execute(
            """INSERT INTO orders
                   (recommendation_id, broker_order_id, ticker, side, order_type,
                    requested_shares, reference_price, limit_price, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'submitted')""",
            (original["recommendation_id"], successor_broker_id, original["ticker"],
             original["side"], original["order_type"], max(remaining, 0.0),
             original["reference_price"], original["limit_price"]),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_poll_orders.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add execution/ledger.py database/queries.py tests/test_poll_orders.py
git commit -m "feat: poll broker orders and build positions from confirmed fills"
```

---

### Task 6: Approve button creates an order before submitting

**Files:**
- Modify: `discord_bot/bot.py` (both approval views)
- Test: `tests/test_approve_creates_order.py`

**Prerequisite:** Phase 1 must have landed; this edits the handler Phase 1 rewrites.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_approve_creates_order.py
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from database.models import initialize_db
from database.queries import (
    create_recommendation, get_open_positions, get_all_trades,
    get_orders_by_status, get_order,
)
from discord_bot.bot import ApproveRejectView

DB_PATH = "test_approve_creates_order.db"


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def _config():
    cfg = MagicMock()
    cfg.db_path = DB_PATH
    cfg.dry_run = False
    cfg.use_limit_buy = False
    cfg.max_position_size_usd = 500.0
    cfg.max_portfolio_usd = 20000.0
    return cfg


def _interaction():
    i = MagicMock()
    i.response.defer = AsyncMock()
    i.response.send_message = AsyncMock()
    i.followup.send = AsyncMock()
    return i


@pytest.mark.asyncio
async def test_approve_writes_order_and_no_position():
    rec = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY", reasoning="t", price=100.0
    )
    view = ApproveRejectView(rec, "AAPL", 100.0, _config())
    with patch("discord_bot.bot.place_order", return_value="BRK1"):
        await view.approve(_interaction(), MagicMock())

    orders = get_orders_by_status(DB_PATH, ("submitted",))
    assert len(orders) == 1 and orders[0]["broker_order_id"] == "BRK1"
    assert get_open_positions(DB_PATH) == []
    assert get_all_trades(DB_PATH) == []


@pytest.mark.asyncio
async def test_order_row_exists_even_when_placement_raises():
    """A broker call that may have succeeded must leave a row to reconcile."""
    rec = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY", reasoning="t", price=100.0
    )
    view = ApproveRejectView(rec, "AAPL", 100.0, _config())
    with patch("discord_bot.bot.place_order", side_effect=RuntimeError("timeout")):
        await view.approve(_interaction(), MagicMock())

    failed = get_orders_by_status(DB_PATH, ("submit_failed",))
    assert len(failed) == 1
    assert failed[0]["failure_reason"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_approve_creates_order.py -v`
Expected: FAIL — a position and trade are created (current behavior)

- [ ] **Step 3: Rewire the handler**

In `discord_bot/bot.py`, replace the `create_trade` / `upsert_position` block (and its
`WARNING (RISK-05 / Phase 17)` comment) with an order-first sequence:

```python
        # Create the order row BEFORE the broker call. If the process dies
        # mid-submission the row survives and reconciliation can determine
        # whether the order landed. Submitting first can leave a real position
        # with no ledger entry at all. (Codex finding 4.)
        order_row_id = await asyncio.to_thread(
            queries.create_order,
            db_path=self.config.db_path,
            recommendation_id=self.rec_id,
            ticker=self.ticker,
            side="buy",
            order_type=order_type_val,
            requested_shares=shares,
            reference_price=effective_price,
            limit_price=limit_price_val,
        )

        try:
            if not self.config.dry_run:
                if self.config.use_limit_buy:
                    order_id = await asyncio.to_thread(
                        place_limit_order, self.ticker, shares, effective_price, self.config
                    )
                else:
                    order_id = await asyncio.to_thread(
                        place_order, self.ticker, shares, self.config
                    )
                await asyncio.to_thread(
                    queries.attach_broker_order_id, self.config.db_path,
                    order_row_id, order_id,
                )
        except Exception as exc:
            await asyncio.to_thread(
                queries.mark_order_submit_failed, self.config.db_path,
                order_row_id, str(exc),
            )
            await asyncio.to_thread(
                queries.update_recommendation_status, self.config.db_path,
                self.rec_id, "pending",
            )
            logger.error("Buy order failed for %s: %s", self.ticker, exc)
            await interaction.followup.send(
                f"Order placement failed for {self.ticker}: {exc} — recommendation "
                "re-opened. Verify in Schwab before retrying."
            )
            return
```

Apply the same structure to the sell handler with `side="sell"`. Trades and positions are no
longer written here at all.

- [ ] **Step 4: Run tests and the full suite**

Run: `pytest tests/test_approve_creates_order.py -v && pytest -q`
Expected: 2 passed. `tests/test_discord_buttons.py` and `tests/test_sell_buttons.py` assert
`create_trade` / `upsert_position` were called — update them to expect `create_order`, since
the behavior change is intentional.

- [ ] **Step 5: Commit**

```bash
git add discord_bot/bot.py tests/
git commit -m "fix: record orders on approval, not positions (Codex finding 4)"
```

---

### Task 7: Schedule the poller

**Files:** `main.py`, `config.py`; Test: `tests/test_poller_schedule.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_poller_schedule.py
from unittest.mock import MagicMock, patch
import pytest
import main


def test_config_exposes_poll_interval(monkeypatch):
    from config import Config
    monkeypatch.setenv("ORDER_POLL_INTERVAL_S", "120")
    assert Config().order_poll_interval_s == 120


@pytest.mark.asyncio
async def test_run_order_poll_calls_poller_in_dry_run_too():
    """Dry-run polling synthesises fills; it must not be skipped."""
    cfg = MagicMock()
    cfg.dry_run = True
    with patch("main.poll_open_orders", return_value=[]) as mock_poll:
        await main.run_order_poll(MagicMock(), cfg)
    mock_poll.assert_called_once()


@pytest.mark.asyncio
async def test_poller_errors_are_swallowed():
    cfg = MagicMock()
    cfg.dry_run = False
    with patch("main.poll_open_orders", side_effect=RuntimeError("down")):
        await main.run_order_poll(MagicMock(), cfg)   # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_poller_schedule.py -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute 'run_order_poll'`

- [ ] **Step 3: Add the config field**

```python
    order_poll_interval_s: int = _env_int("ORDER_POLL_INTERVAL_S", "300")
```

- [ ] **Step 4: Add the runner and schedule it**

In `main.py`:

```python
from apscheduler.triggers.interval import IntervalTrigger
from execution.ledger import poll_open_orders


async def run_order_poll(bot, config) -> None:
    """Poll open broker orders and apply fills. Runs in dry-run too, where it
    synthesises fills at the reference price so forward validation still works."""
    try:
        results = await poll_open_orders(config)
        if results:
            logger.info("Order poll updated %d order(s)", len(results))
    except Exception as exc:
        logger.error("Order poll failed: %s", exc)
```

In `on_ready`, alongside the existing jobs — note this uses
`asyncio.run_coroutine_threadsafe`, **not** `asyncio.create_task`. The scheduler is a
`BackgroundScheduler` (`main.py:684`) whose jobs run in a worker thread with no event loop,
so `create_task` would raise "no running event loop" on every tick:

```python
        scheduler.add_job(
            lambda: asyncio.run_coroutine_threadsafe(
                run_order_poll(bot, config), bot.loop
            ).result(),
            trigger=IntervalTrigger(seconds=config.order_poll_interval_s),
            id="order_poll",
            replace_existing=True,
        )
```

Also `await run_order_poll(bot, config)` at the start of `run_scan`'s sell pass, so positions
are current before exit signals are evaluated.

- [ ] **Step 5: Run tests and the full suite**

Run: `pytest tests/test_poller_schedule.py -v && pytest -q`
Expected: 3 passed, suite green

- [ ] **Step 6: Commit**

```bash
git add main.py config.py tests/test_poller_schedule.py
git commit -m "feat: schedule the order poller on the bot event loop"
```

---

### Task 8: Exposure from broker positions and reserved orders

**Files:** `risk/preflight.py` (Phase 1), `discord_bot/bot.py`; Test: `tests/test_exposure_from_broker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exposure_from_broker.py
from risk.preflight import compute_current_exposure


def test_uses_broker_market_value_not_cost_basis():
    broker = [{"symbol": "AAPL", "quantity": 10, "avg_price": 100.0, "market_value": 1500.0}]
    assert compute_current_exposure(broker, []) == 1500.0


def test_unfilled_limit_order_reserves_exposure():
    working = [{"ticker": "MSFT", "requested_shares": 5, "filled_shares": 0,
                "limit_price": 200.0, "reference_price": 195.0, "side": "buy"}]
    assert compute_current_exposure([], working) == 1000.0


def test_market_order_reserves_at_reference_price():
    """v1 excluded market orders entirely, assuming they 'fill promptly'."""
    working = [{"ticker": "MSFT", "requested_shares": 5, "filled_shares": 0,
                "limit_price": None, "reference_price": 195.0, "side": "buy"}]
    assert compute_current_exposure([], working) == 975.0


def test_partial_fill_reserves_only_the_remainder():
    working = [{"ticker": "MSFT", "requested_shares": 5, "filled_shares": 2,
                "limit_price": 200.0, "reference_price": 195.0, "side": "buy"}]
    assert compute_current_exposure([], working) == 600.0


def test_sell_orders_do_not_add_exposure():
    working = [{"ticker": "MSFT", "requested_shares": 5, "filled_shares": 0,
                "limit_price": 200.0, "reference_price": 195.0, "side": "sell"}]
    assert compute_current_exposure([], working) == 0.0


def test_empty_inputs_are_zero():
    assert compute_current_exposure([], []) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_exposure_from_broker.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_current_exposure'`

- [ ] **Step 3: Write the implementation**

Append to `risk/preflight.py`:

```python
def compute_current_exposure(
    broker_positions: list[dict], working_orders: list[dict]
) -> float:
    """Return committed capital: broker market value plus unfilled buy reservations.

    Uses the broker's marketValue rather than the DB's avg_cost_usd (Codex
    finding 10) — cost basis understates exposure in a rising market.

    EVERY unfilled buy reserves capital, priced at its limit where it has one and
    its approval-time reference price otherwise. v1 skipped market orders on the
    assumption they fill promptly; between approval and fill they are exactly the
    orders the ceiling cannot see.
    """
    exposure = sum(float(p.get("market_value", 0) or 0) for p in broker_positions)

    for order in working_orders:
        if order.get("side") != "buy":
            continue
        price = order.get("limit_price") or order.get("reference_price")
        if price is None:
            continue
        unfilled = float(order.get("requested_shares", 0) or 0) - float(
            order.get("filled_shares", 0) or 0
        )
        if unfilled > 0:
            exposure += unfilled * float(price)

    return exposure
```

- [ ] **Step 4: Wire it into the approval path**

In `discord_bot/bot.py`, replace the DB-based `existing_total` computation with:

```python
        broker_positions = await asyncio.to_thread(get_positions, self.config)
        working_orders = await asyncio.to_thread(
            queries.get_orders_by_status, self.config.db_path,
            queries.OPEN_ORDER_STATUSES,
        )
```

and pass both to `evaluate_trade`, whose guard 8 calls `compute_current_exposure`. Guard 7
(daily notional) takes `queries.get_day_notional(db_path)`.

- [ ] **Step 5: Run tests and the full suite**

Run: `pytest tests/test_exposure_from_broker.py -v && pytest -q`
Expected: 6 passed, suite green

- [ ] **Step 6: Commit**

```bash
git add risk/preflight.py discord_bot/bot.py tests/test_exposure_from_broker.py
git commit -m "fix: exposure from broker market value plus reserved working orders"
```

---

### Task 9: Documentation and verification

**Files:** `CLAUDE.md`, `README.md`, `pytest.ini`

- [ ] **Step 1: Create pytest.ini** (clears the pytest-asyncio warning Codex noted)

```ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
```

- [ ] **Step 2: Update CLAUDE.md**

Module table:

```markdown
| `execution/status.py` | Pure Schwab-status → lifecycle mapping and order-response parsing |
| `execution/ledger.py` | `poll_open_orders` — polls the broker and applies fills |
```

Key Design Decisions:

```markdown
- **Fills, not acknowledgements**: the Approve button creates an `orders` row *before*
  submitting, then attaches the broker id. Trades and positions are written only by
  `queries.record_fill`, called from `execution.ledger.poll_open_orders` on confirmed fill
  quantities at real fill prices. An unfilled GTC limit creates no position. This replaces
  the RISK-05 behavior where an acknowledgement was recorded as a completed trade at the
  scan price.
- **One transaction per fill**: `record_fill` writes the trade, mutates the position, and
  advances the order counters together, guarded by optimistic concurrency on
  `filled_shares`. Two concurrent pollers cannot double-count, and a crash mid-apply rolls
  back cleanly. Incremental price comes from the *notional* delta, never the cumulative
  average.
- **Risk budget reads orders, not trades**: `get_day_notional` and `compute_current_exposure`
  value committed capital from the `orders` table at each order's `reference_price`. Reading
  fills instead would let several market buys be approved before any fill was visible and
  jointly exceed the ceiling.
- **Dry-run synthesises fills**: `poll_open_orders` fills dry-run orders at their reference
  price so `/positions`, `/stats`, and the sell pass still work for forward validation.
```

Add `orders` to the Database Schema section, and note `trades.fees` / `trades.order_row_id`.

- [ ] **Step 3: Update README** — positions appear only after fill confirmation;
`ORDER_POLL_INTERVAL_S` controls cadence.

- [ ] **Step 4: Run the full suite and linter**

Run: `pytest -q && ruff check .`

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md pytest.ini
git commit -m "docs: document the execution ledger and add pytest config"
```

---

## Self-Review

**Spec coverage:**
- Finding 4 (acks treated as fills) → Tasks 1–7 ✓
- Finding 4 (partial fills) → Tasks 2, 3, 5 ✓
- Finding 4 (real fill prices) → Tasks 2, 3 ✓
- Finding 4 (P&L wrong) → Task 3, sell `cost_basis` ✓
- Finding 4 (sell pass sells non-existent shares) → Task 3 sell branch + Task 7 poll-before-sell ✓
- Finding 4 (fill price, quantity, execution time, fees) → Tasks 2, 3 ✓
- Finding 10 (exposure from broker values) → Task 8 ✓
- Finding 10 (working orders in reserved exposure) → Task 8 ✓
- pytest-asyncio warning → Task 9 ✓

**Deliberately not covered:**
- "Make reconciliation capable of blocking unsafe follow-up actions" — `run_reconciliation`
  stays report-only. Once positions derive from fills most current discrepancies should
  vanish; making it blocking should wait until we see what it reports post-ledger. It does
  gain one new job: orders stuck in `pending_submit` with no broker id.
- Slippage measurement — `reference_price` and the real fill price are both stored, so it is
  derivable, but no reporting is built here.

**Type consistency:** `parse_order_response` returns nine keys (Task 2), consumed by name in
Task 5 ✓. `record_fill`'s eight parameters (Task 3) match its call in Task 5 ✓.
`create_order` (Task 1) matches Task 6's call including `reference_price` ✓.
`OPEN_ORDER_STATUSES` is defined in Task 1 and referenced in Tasks 5 and 8 ✓.
`compute_current_exposure(broker_positions, working_orders)` (Task 8) consumes the order-row
shape produced by `get_orders_by_status` including `side` and `reference_price` ✓.

**Known residual risk:** `record_fill`'s optimistic guard protects against double-counting
but not against a torn read between `get_orders_by_status` and `record_fill` — the poller may
compute a delta from a stale row and lose the race, which is correct behavior (it returns
None and retries next tick). The `pending_submit` orphan case is handed to reconciliation
rather than resolved automatically, because adopting a broker order by symbol-and-time match
is a heuristic that could bind the wrong order.
