# Execution Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record positions and P&L from confirmed broker executions instead of order acknowledgements, so the trade ledger reflects what actually happened.

**Architecture:** A new `orders` table sits between `recommendations` and `trades`. The Discord Approve button creates an *order* row and stops there — it no longer writes trades or positions. A poller reads broker order status via `schwab-py`'s `Client.get_order`, maps the 20-value Schwab status enum onto six internal states, and only on confirmed fill quantity does it write a trade row and update the position at the real fill price. All mapping and diff logic lives in pure functions; the poller is the only component that does I/O.

**Tech Stack:** Python 3.11, SQLite (WAL), schwab-py 1.5.1, discord.py, APScheduler, pytest, pytest-asyncio

**Spec:** `docs/superpowers/plans/2026-08-14-codex-backlog-roadmap.md` (Workstream A); source findings in `codex_recommendations.md` §4 and §10

## Global Constraints

- Python 3.11; SQLite via the existing `database/models.py:get_cursor` context manager — never open raw connections in new code
- All yfinance and Schwab I/O inside async functions must be wrapped in `await asyncio.to_thread(...)` — zero synchronous broker calls on the Discord event loop
- Tests must set `config.dry_run = True` (or patch the broker call) so the suite never reaches the live Schwab API
- Day-bucketing uses SQLite `'localtime'`; expiry/timestamp comparisons use bare UTC `datetime('now')`. Do not unify these.
- New tables use `CREATE TABLE IF NOT EXISTS` inside `initialize_db`; new columns on existing tables use the established `try: ALTER TABLE / except sqlite3.OperationalError: pass` migration pattern
- Test files use a module-level `DB_PATH = "test_<name>.db"` with an `autouse` `fresh_db` fixture calling `initialize_db` then `os.remove` — match `tests/test_positions.py`
- Commit after every task

---

### Task 1: `orders` table and CRUD

**Files:**
- Modify: `database/models.py:37-101` (add table to the `executescript` block)
- Modify: `database/queries.py` (append new functions)
- Test: `tests/test_orders_table.py`

**Interfaces:**
- Consumes: `database.models.get_cursor`, `database.models.initialize_db`
- Produces:
  - `create_order(db_path, recommendation_id, ticker, side, order_type, requested_shares, broker_order_id, limit_price=None) -> int`
  - `get_orders_by_status(db_path, statuses: tuple[str, ...]) -> list[dict]`
  - `update_order_fill(db_path, order_id, status, filled_shares, avg_fill_price) -> None`
  - `get_order_by_broker_id(db_path, broker_order_id) -> dict | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orders_table.py
import os
import sqlite3
import pytest
from database.models import initialize_db
from database.queries import (
    create_order,
    get_orders_by_status,
    update_order_fill,
    get_order_by_broker_id,
    create_recommendation,
)

DB_PATH = "test_orders_table.db"


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def _rec() -> int:
    return create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY",
        reasoning="test", price=100.0,
    )


def test_orders_table_exists():
    conn = sqlite3.connect(DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "orders" in tables


def test_create_order_defaults_to_submitted_with_zero_fill():
    oid = create_order(
        DB_PATH, recommendation_id=_rec(), ticker="AAPL", side="buy",
        order_type="limit", requested_shares=5, broker_order_id="BRK1",
        limit_price=100.0,
    )
    assert oid > 0
    row = get_order_by_broker_id(DB_PATH, "BRK1")
    assert row["status"] == "submitted"
    assert row["filled_shares"] == 0
    assert row["avg_fill_price"] is None


def test_get_orders_by_status_filters():
    r = _rec()
    create_order(DB_PATH, r, "AAPL", "buy", "market", 5, "BRK1")
    o2 = create_order(DB_PATH, r, "MSFT", "buy", "market", 3, "BRK2")
    update_order_fill(DB_PATH, o2, status="filled", filled_shares=3, avg_fill_price=99.5)
    open_orders = get_orders_by_status(DB_PATH, ("submitted", "working"))
    assert [o["ticker"] for o in open_orders] == ["AAPL"]


def test_update_order_fill_records_price_and_quantity():
    oid = create_order(DB_PATH, _rec(), "AAPL", "buy", "market", 5, "BRK1")
    update_order_fill(DB_PATH, oid, status="partially_filled", filled_shares=2, avg_fill_price=101.25)
    row = get_order_by_broker_id(DB_PATH, "BRK1")
    assert row["status"] == "partially_filled"
    assert row["filled_shares"] == 2
    assert row["avg_fill_price"] == 101.25


def test_broker_order_id_is_unique():
    r = _rec()
    create_order(DB_PATH, r, "AAPL", "buy", "market", 5, "BRK1")
    with pytest.raises(sqlite3.IntegrityError):
        create_order(DB_PATH, r, "AAPL", "buy", "market", 5, "BRK1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orders_table.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_order'`

- [ ] **Step 3: Add the table**

In `database/models.py`, inside the existing `conn.executescript("""...""")` block, after the `analyst_calls` table:

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
            status            TEXT NOT NULL DEFAULT 'submitted',
            filled_shares     REAL NOT NULL DEFAULT 0,
            avg_fill_price    REAL,
            submitted_at      TEXT NOT NULL DEFAULT (datetime('now')),
            last_polled_at    TEXT,
            closed_at         TEXT,
            FOREIGN KEY (recommendation_id) REFERENCES recommendations(id)
        );
```

- [ ] **Step 4: Add the CRUD functions**

Append to `database/queries.py`:

```python
# --- Order ledger (Workstream A) ---

_TERMINAL_ORDER_STATUSES = ("filled", "cancelled", "rejected")


def create_order(
    db_path: str,
    recommendation_id: int,
    ticker: str,
    side: str,
    order_type: str,
    requested_shares: float,
    broker_order_id: str | None,
    limit_price: float | None = None,
) -> int:
    """Insert a submitted order and return its row id."""
    with get_cursor(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO orders
                   (recommendation_id, broker_order_id, ticker, side,
                    order_type, requested_shares, limit_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (recommendation_id, broker_order_id, ticker, side,
             order_type, requested_shares, limit_price),
        )
        return cursor.lastrowid


def get_orders_by_status(db_path: str, statuses: tuple[str, ...]) -> list[dict]:
    """Return orders whose status is in statuses, oldest first."""
    placeholders = ",".join("?" for _ in statuses)
    with get_cursor(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM orders WHERE status IN ({placeholders}) ORDER BY id",
            statuses,
        ).fetchall()
    return [dict(r) for r in rows]


def update_order_fill(
    db_path: str,
    order_id: int,
    status: str,
    filled_shares: float,
    avg_fill_price: float | None,
) -> None:
    """Update an order's lifecycle state and fill data.

    closed_at is stamped only on a terminal status so a re-poll of an already
    closed order does not keep moving the timestamp.
    """
    with get_cursor(db_path) as conn:
        conn.execute(
            """UPDATE orders
                  SET status = ?, filled_shares = ?, avg_fill_price = ?,
                      last_polled_at = datetime('now'),
                      closed_at = CASE
                          WHEN ? IN ('filled', 'cancelled', 'rejected')
                               AND closed_at IS NULL
                          THEN datetime('now') ELSE closed_at END
                WHERE id = ?""",
            (status, filled_shares, avg_fill_price, status, order_id),
        )


def get_order_by_broker_id(db_path: str, broker_order_id: str) -> dict | None:
    """Return the order row for a broker order id, or None."""
    with get_cursor(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE broker_order_id = ?", (broker_order_id,)
        ).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_orders_table.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add database/models.py database/queries.py tests/test_orders_table.py
git commit -m "feat: add orders table and CRUD for the execution ledger"
```

---

### Task 2: Map Schwab order status to internal states

**Files:**
- Create: `execution/__init__.py` (empty)
- Create: `execution/status.py`
- Test: `tests/test_order_status_mapping.py`

**Interfaces:**
- Consumes: nothing (pure module, no imports from `schwab` or `discord`)
- Produces:
  - `map_broker_status(broker_status: str, filled_qty: float, requested_qty: float) -> str` returning one of `submitted | working | partially_filled | filled | cancelled | rejected`
  - `TERMINAL: frozenset[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_order_status_mapping.py
import pytest
from execution.status import map_broker_status, TERMINAL


@pytest.mark.parametrize("broker_status", [
    "WORKING", "QUEUED", "ACCEPTED", "NEW", "PENDING_ACTIVATION",
    "PENDING_ACKNOWLEDGEMENT", "AWAITING_CONDITION", "AWAITING_MANUAL_REVIEW",
])
def test_open_statuses_map_to_working(broker_status):
    assert map_broker_status(broker_status, 0, 10) == "working"


def test_filled_maps_to_filled():
    assert map_broker_status("FILLED", 10, 10) == "filled"


def test_partial_fill_on_open_order():
    assert map_broker_status("WORKING", 4, 10) == "partially_filled"


def test_cancelled_with_partial_fill_is_still_cancelled():
    """A cancelled order that partly filled is terminal — the remainder will never fill."""
    assert map_broker_status("CANCELED", 4, 10) == "cancelled"


def test_expired_maps_to_cancelled():
    assert map_broker_status("EXPIRED", 0, 10) == "cancelled"


def test_rejected_maps_to_rejected():
    assert map_broker_status("REJECTED", 0, 10) == "rejected"


def test_unknown_status_maps_to_working_not_filled():
    """Fail safe: an unrecognised status must never be treated as a fill."""
    assert map_broker_status("SOME_NEW_SCHWAB_STATUS", 0, 10) == "working"


def test_status_is_case_insensitive():
    assert map_broker_status("filled", 10, 10) == "filled"


def test_terminal_set():
    assert TERMINAL == frozenset({"filled", "cancelled", "rejected"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_order_status_mapping.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'execution'`

- [ ] **Step 3: Write the implementation**

```python
# execution/status.py
"""Map Schwab broker order statuses onto the internal order lifecycle.

Pure module: imports nothing from schwab, discord, or the database so its
tests need no fixtures. schwab-py exposes 20 status values via
schwab.client.Client.Order.Status; they collapse into six internal states.
"""
from __future__ import annotations

TERMINAL = frozenset({"filled", "cancelled", "rejected"})

_CANCELLED = {"CANCELED", "CANCELLED", "EXPIRED", "REPLACED"}
_REJECTED = {"REJECTED"}
_FILLED = {"FILLED"}


def map_broker_status(broker_status: str, filled_qty: float, requested_qty: float) -> str:
    """Return the internal lifecycle state for a broker status plus fill quantities.

    Terminal broker states win over fill quantity: an order that was cancelled
    after a partial fill is 'cancelled', because the unfilled remainder will
    never arrive. An unrecognised status maps to 'working', never to a fill —
    a new Schwab status must not be able to conjure a position.
    """
    status = (broker_status or "").strip().upper()

    if status in _REJECTED:
        return "rejected"
    if status in _CANCELLED:
        return "cancelled"
    if status in _FILLED:
        return "filled"
    if filled_qty > 0 and filled_qty < requested_qty:
        return "partially_filled"
    if filled_qty > 0 and filled_qty >= requested_qty:
        return "filled"
    return "working"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_order_status_mapping.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add execution/__init__.py execution/status.py tests/test_order_status_mapping.py
git commit -m "feat: map Schwab order statuses to internal lifecycle states"
```

---

### Task 3: Parse the Schwab order response

**Files:**
- Modify: `execution/status.py` (append)
- Test: `tests/test_order_response_parsing.py`

**Interfaces:**
- Consumes: `execution.status.map_broker_status`
- Produces: `parse_order_response(payload: dict) -> dict` returning
  `{"broker_status": str, "filled_qty": float, "requested_qty": float, "avg_fill_price": float | None, "internal_status": str}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_order_response_parsing.py
from execution.status import parse_order_response


def test_parses_filled_order_with_execution_legs():
    payload = {
        "status": "FILLED",
        "quantity": 10,
        "filledQuantity": 10,
        "orderActivityCollection": [
            {"executionLegs": [
                {"quantity": 6, "price": 100.00},
                {"quantity": 4, "price": 101.00},
            ]}
        ],
    }
    result = parse_order_response(payload)
    assert result["internal_status"] == "filled"
    assert result["filled_qty"] == 10
    # Share-weighted: (6*100 + 4*101) / 10 = 100.40
    assert result["avg_fill_price"] == 100.40


def test_partial_fill_reports_partial_status():
    payload = {
        "status": "WORKING",
        "quantity": 10,
        "filledQuantity": 3,
        "orderActivityCollection": [
            {"executionLegs": [{"quantity": 3, "price": 99.0}]}
        ],
    }
    result = parse_order_response(payload)
    assert result["internal_status"] == "partially_filled"
    assert result["filled_qty"] == 3
    assert result["avg_fill_price"] == 99.0


def test_unfilled_order_has_no_price():
    payload = {"status": "WORKING", "quantity": 10, "filledQuantity": 0}
    result = parse_order_response(payload)
    assert result["internal_status"] == "working"
    assert result["filled_qty"] == 0
    assert result["avg_fill_price"] is None


def test_missing_fields_do_not_raise():
    result = parse_order_response({})
    assert result["internal_status"] == "working"
    assert result["filled_qty"] == 0
    assert result["avg_fill_price"] is None


def test_zero_quantity_legs_do_not_divide_by_zero():
    payload = {
        "status": "WORKING", "quantity": 10, "filledQuantity": 0,
        "orderActivityCollection": [{"executionLegs": [{"quantity": 0, "price": 0}]}],
    }
    assert parse_order_response(payload)["avg_fill_price"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_order_response_parsing.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_order_response'`

- [ ] **Step 3: Write the implementation**

Append to `execution/status.py`:

```python
def parse_order_response(payload: dict) -> dict:
    """Extract lifecycle state and fill data from a Schwab get_order response.

    avg_fill_price is share-weighted across execution legs, not a simple mean —
    a 6-share leg at $100 and a 4-share leg at $101 average to $100.40, not
    $100.50. Returns None when nothing has filled, so callers can distinguish
    "no fill" from "filled at zero".
    """
    broker_status = payload.get("status", "")
    requested_qty = float(payload.get("quantity", 0) or 0)
    filled_qty = float(payload.get("filledQuantity", 0) or 0)

    total_shares = 0.0
    total_notional = 0.0
    for activity in payload.get("orderActivityCollection", []) or []:
        for leg in activity.get("executionLegs", []) or []:
            qty = float(leg.get("quantity", 0) or 0)
            price = float(leg.get("price", 0) or 0)
            total_shares += qty
            total_notional += qty * price

    avg_fill_price = round(total_notional / total_shares, 4) if total_shares > 0 else None

    return {
        "broker_status": broker_status,
        "filled_qty": filled_qty,
        "requested_qty": requested_qty,
        "avg_fill_price": avg_fill_price,
        "internal_status": map_broker_status(broker_status, filled_qty, requested_qty),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_order_response_parsing.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add execution/status.py tests/test_order_response_parsing.py
git commit -m "feat: parse Schwab order responses into fill data"
```

---

### Task 4: Compute ledger actions from an order update

**Files:**
- Create: `execution/ledger.py`
- Test: `tests/test_ledger_actions.py`

**Interfaces:**
- Consumes: `execution.status.TERMINAL`
- Produces: `compute_fill_delta(order_row: dict, parsed: dict) -> dict | None` returning
  `{"new_shares": float, "fill_price": float, "status": str}` or `None` when nothing new filled

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger_actions.py
from execution.ledger import compute_fill_delta


def _order(filled=0.0, requested=10.0, status="working"):
    return {
        "id": 1, "ticker": "AAPL", "side": "buy",
        "requested_shares": requested, "filled_shares": filled, "status": status,
    }


def test_first_fill_reports_full_quantity():
    parsed = {"internal_status": "filled", "filled_qty": 10.0, "avg_fill_price": 100.0}
    delta = compute_fill_delta(_order(filled=0.0), parsed)
    assert delta == {"new_shares": 10.0, "fill_price": 100.0, "status": "filled"}


def test_incremental_fill_reports_only_the_new_shares():
    """An order already recorded at 4 shares that is now 7 filled adds 3, not 7."""
    parsed = {"internal_status": "partially_filled", "filled_qty": 7.0, "avg_fill_price": 100.0}
    delta = compute_fill_delta(_order(filled=4.0), parsed)
    assert delta["new_shares"] == 3.0


def test_no_change_returns_none():
    parsed = {"internal_status": "working", "filled_qty": 4.0, "avg_fill_price": 100.0}
    assert compute_fill_delta(_order(filled=4.0), parsed) is None


def test_unfilled_cancel_returns_none():
    parsed = {"internal_status": "cancelled", "filled_qty": 0.0, "avg_fill_price": None}
    assert compute_fill_delta(_order(filled=0.0), parsed) is None


def test_cancel_after_partial_fill_records_the_partial():
    parsed = {"internal_status": "cancelled", "filled_qty": 4.0, "avg_fill_price": 99.0}
    delta = compute_fill_delta(_order(filled=0.0), parsed)
    assert delta == {"new_shares": 4.0, "fill_price": 99.0, "status": "cancelled"}


def test_missing_fill_price_returns_none_rather_than_guessing():
    """Never invent a price — a fill with no price is not recordable."""
    parsed = {"internal_status": "filled", "filled_qty": 10.0, "avg_fill_price": None}
    assert compute_fill_delta(_order(filled=0.0), parsed) is None


def test_broker_reporting_fewer_shares_than_recorded_returns_none():
    """Defensive: never emit a negative delta."""
    parsed = {"internal_status": "working", "filled_qty": 2.0, "avg_fill_price": 100.0}
    assert compute_fill_delta(_order(filled=5.0), parsed) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ledger_actions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'execution.ledger'`

- [ ] **Step 3: Write the implementation**

```python
# execution/ledger.py
"""Pure fill-accounting for the execution ledger.

Decides what a broker order update means for the trade and position records.
Does no I/O: callers pass the stored order row and the parsed broker response,
and receive the incremental change to apply (or None).
"""
from __future__ import annotations


def compute_fill_delta(order_row: dict, parsed: dict) -> dict | None:
    """Return the incremental fill to record, or None when there is nothing new.

    Returns only the *new* shares since the last poll, so a partially filled
    order that fills further does not double-count. Returns None rather than
    guessing when the broker reports a fill with no price — an invented price
    is exactly the defect this workstream exists to remove.
    """
    already = float(order_row.get("filled_shares", 0) or 0)
    now_filled = float(parsed.get("filled_qty", 0) or 0)
    new_shares = now_filled - already

    if new_shares <= 0:
        return None

    fill_price = parsed.get("avg_fill_price")
    if fill_price is None:
        return None

    return {
        "new_shares": new_shares,
        "fill_price": float(fill_price),
        "status": parsed["internal_status"],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ledger_actions.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add execution/ledger.py tests/test_ledger_actions.py
git commit -m "feat: compute incremental fill deltas for the ledger"
```

---

### Task 5: Fetch order status from Schwab

**Files:**
- Modify: `schwab_client/orders.py` (append after `get_positions`)
- Test: `tests/test_fetch_order_status.py`

**Interfaces:**
- Consumes: `schwab_client.auth.get_client`
- Produces: `fetch_order_status(broker_order_id: str, config, client=None) -> dict` — the raw Schwab payload

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fetch_order_status.py
import pytest
from unittest.mock import MagicMock
from schwab_client.orders import fetch_order_status


class _Cfg:
    schwab_account_hash = "HASH123"


def test_calls_get_order_with_account_hash():
    client = MagicMock()
    client.get_order.return_value.json.return_value = {"status": "FILLED"}
    result = fetch_order_status("BRK1", _Cfg(), client=client)
    client.get_order.assert_called_once_with("BRK1", "HASH123")
    assert result == {"status": "FILLED"}


def test_raises_runtime_error_on_failure():
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

    Mirrors place_order's structure: lazy client import, shared retry policy,
    RuntimeError on failure. Parsing lives in execution/status.py so this
    function stays a thin I/O shim.
    """
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)
    try:
        resp = _call_get_order(client, broker_order_id, config.schwab_account_hash)
        return resp.json()
    except Exception as exc:
        logger.error("Order status fetch failed for %s: %s", broker_order_id, exc)
        raise RuntimeError(f"Order status fetch failed for {broker_order_id}: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch_order_status.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add schwab_client/orders.py tests/test_fetch_order_status.py
git commit -m "feat: fetch broker order status via schwab-py get_order"
```

---

### Task 6: The poller — apply broker updates to the ledger

**Files:**
- Modify: `execution/ledger.py` (append)
- Test: `tests/test_poll_orders.py`

**Interfaces:**
- Consumes: `database.queries.get_orders_by_status`, `update_order_fill`, `create_trade`, `upsert_position`; `schwab_client.orders.fetch_order_status`; `execution.status.parse_order_response`; `execution.ledger.compute_fill_delta`
- Produces: `async def poll_open_orders(config, client=None) -> list[dict]` returning a summary per updated order

- [ ] **Step 1: Write the failing test**

```python
# tests/test_poll_orders.py
import os
import pytest
from unittest.mock import patch
from database.models import initialize_db
from database.queries import (
    create_recommendation, create_order, get_order_by_broker_id,
    get_open_positions, get_all_trades,
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
    dry_run = True


def _seed_order(shares=10.0):
    rec = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY", reasoning="t", price=100.0
    )
    return create_order(DB_PATH, rec, "AAPL", "buy", "limit", shares, "BRK1", 100.0)


@pytest.mark.asyncio
async def test_filled_order_creates_position_at_actual_fill_price():
    _seed_order()
    payload = {
        "status": "FILLED", "quantity": 10, "filledQuantity": 10,
        "orderActivityCollection": [{"executionLegs": [{"quantity": 10, "price": 103.75}]}],
    }
    with patch("execution.ledger.fetch_order_status", return_value=payload):
        await poll_open_orders(_Cfg())

    positions = get_open_positions(DB_PATH)
    assert len(positions) == 1
    # The recommendation price was 100.00; the position must use the FILL price.
    assert positions[0]["avg_cost_usd"] == 103.75
    assert get_order_by_broker_id(DB_PATH, "BRK1")["status"] == "filled"


@pytest.mark.asyncio
async def test_unfilled_order_creates_no_position():
    """The core defect: a GTC limit that never fills must not create a position."""
    _seed_order()
    payload = {"status": "WORKING", "quantity": 10, "filledQuantity": 0}
    with patch("execution.ledger.fetch_order_status", return_value=payload):
        await poll_open_orders(_Cfg())

    assert get_open_positions(DB_PATH) == []
    assert get_order_by_broker_id(DB_PATH, "BRK1")["status"] == "working"


@pytest.mark.asyncio
async def test_partial_fill_records_only_filled_shares():
    _seed_order()
    payload = {
        "status": "WORKING", "quantity": 10, "filledQuantity": 4,
        "orderActivityCollection": [{"executionLegs": [{"quantity": 4, "price": 99.0}]}],
    }
    with patch("execution.ledger.fetch_order_status", return_value=payload):
        await poll_open_orders(_Cfg())

    positions = get_open_positions(DB_PATH)
    assert positions[0]["shares"] == 4


@pytest.mark.asyncio
async def test_repolling_an_unchanged_order_does_not_double_count():
    _seed_order()
    payload = {
        "status": "FILLED", "quantity": 10, "filledQuantity": 10,
        "orderActivityCollection": [{"executionLegs": [{"quantity": 10, "price": 100.0}]}],
    }
    with patch("execution.ledger.fetch_order_status", return_value=payload):
        await poll_open_orders(_Cfg())
        await poll_open_orders(_Cfg())

    positions = get_open_positions(DB_PATH)
    assert positions[0]["shares"] == 10  # not 20


@pytest.mark.asyncio
async def test_broker_failure_leaves_order_untouched():
    _seed_order()
    with patch("execution.ledger.fetch_order_status", side_effect=RuntimeError("api down")):
        await poll_open_orders(_Cfg())

    assert get_order_by_broker_id(DB_PATH, "BRK1")["status"] == "submitted"
    assert get_open_positions(DB_PATH) == []


@pytest.mark.asyncio
async def test_rejected_order_creates_no_position_and_is_terminal():
    _seed_order()
    payload = {"status": "REJECTED", "quantity": 10, "filledQuantity": 0}
    with patch("execution.ledger.fetch_order_status", return_value=payload):
        await poll_open_orders(_Cfg())

    assert get_open_positions(DB_PATH) == []
    assert get_order_by_broker_id(DB_PATH, "BRK1")["status"] == "rejected"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_poll_orders.py -v`
Expected: FAIL — `ImportError: cannot import name 'poll_open_orders'`

- [ ] **Step 3: Add `get_all_trades` to queries**

Append to `database/queries.py`:

```python
def get_all_trades(db_path: str) -> list[dict]:
    """Return every trade row, oldest first. Used by tests and reporting."""
    with get_cursor(db_path) as conn:
        rows = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Write the poller**

Append to `execution/ledger.py`:

```python
import asyncio
import logging

from database import queries
from execution.status import parse_order_response
from schwab_client.orders import fetch_order_status

logger = logging.getLogger(__name__)

_OPEN_STATUSES = ("submitted", "working", "partially_filled")


async def poll_open_orders(config, client=None) -> list[dict]:
    """Poll every non-terminal order and apply confirmed fills to the ledger.

    This is the ONLY place positions are created from broker activity. A broker
    error on one order is logged and skipped so a single bad symbol cannot stall
    the whole poll — the order keeps its current status and is retried next tick.
    """
    open_orders = await asyncio.to_thread(
        queries.get_orders_by_status, config.db_path, _OPEN_STATUSES
    )
    results = []

    for order in open_orders:
        broker_id = order.get("broker_order_id")
        if not broker_id:
            continue  # dry-run order with no broker counterpart

        try:
            payload = await asyncio.to_thread(
                fetch_order_status, broker_id, config, client
            )
        except Exception as exc:
            logger.warning("Order poll failed for %s: %s", broker_id, exc)
            continue

        parsed = parse_order_response(payload)
        delta = compute_fill_delta(order, parsed)

        if delta is not None:
            await asyncio.to_thread(
                queries.create_trade,
                db_path=config.db_path,
                recommendation_id=order["recommendation_id"],
                ticker=order["ticker"],
                shares=delta["new_shares"],
                price=delta["fill_price"],
                order_id=broker_id,
                side=order["side"],
            )
            if order["side"] == "buy":
                await asyncio.to_thread(
                    queries.upsert_position, config.db_path,
                    order["ticker"], delta["new_shares"], delta["fill_price"],
                )

        await asyncio.to_thread(
            queries.update_order_fill,
            config.db_path,
            order["id"],
            parsed["internal_status"],
            parsed["filled_qty"],
            parsed["avg_fill_price"],
        )
        results.append({
            "ticker": order["ticker"],
            "status": parsed["internal_status"],
            "filled": parsed["filled_qty"],
        })

    return results
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_poll_orders.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add execution/ledger.py database/queries.py tests/test_poll_orders.py
git commit -m "feat: poll broker orders and build positions from confirmed fills"
```

---

### Task 7: Approve button creates an order, not a position

**Files:**
- Modify: `discord_bot/bot.py:124-142` (buy) and the sell handler around `:240`
- Test: `tests/test_approve_creates_order.py`

**Interfaces:**
- Consumes: `database.queries.create_order`
- Produces: no new exports; `ApproveRejectView.approve` now writes only an order row

- [ ] **Step 1: Write the failing test**

```python
# tests/test_approve_creates_order.py
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from database.models import initialize_db
from database.queries import (
    create_recommendation, get_open_positions, get_all_trades, get_orders_by_status,
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
    """Approval acknowledges an order; it must not create a position or trade."""
    rec = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY", reasoning="t", price=100.0
    )
    view = ApproveRejectView(rec, "AAPL", 100.0, _config())
    with patch("discord_bot.bot.place_order", return_value="BRK1"):
        await view.approve(_interaction(), MagicMock())

    orders = get_orders_by_status(DB_PATH, ("submitted",))
    assert len(orders) == 1
    assert orders[0]["broker_order_id"] == "BRK1"
    assert get_open_positions(DB_PATH) == []
    assert get_all_trades(DB_PATH) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_approve_creates_order.py -v`
Expected: FAIL — a position and trade are created (the current behavior)

- [ ] **Step 3: Replace the trade/position writes**

In `discord_bot/bot.py`, delete the `create_trade` and `upsert_position` calls at lines 128-142 along with the `WARNING (RISK-05 / Phase 17)` comment block, and replace with:

```python
        # Record the ORDER only. Trades and positions are created by
        # execution.ledger.poll_open_orders from confirmed broker fills —
        # never from an acknowledgement. (Codex finding 4.)
        await asyncio.to_thread(
            queries.create_order,
            db_path=self.config.db_path,
            recommendation_id=self.rec_id,
            ticker=self.ticker,
            side="buy",
            order_type=order_type_val,
            requested_shares=shares,
            broker_order_id=order_id,
            limit_price=limit_price_val,
        )
```

Add `create_order` to the `from database import queries` usage (the module is already imported).

Apply the same change to the sell handler: `side="sell"`, `order_type="market"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_approve_creates_order.py -v`
Expected: 1 passed

- [ ] **Step 5: Run the full suite and fix fallout**

Run: `pytest -q`
Expected: failures in `tests/test_discord_buttons.py` and `tests/test_sell_buttons.py` that assert `create_trade` / `upsert_position` were called. Update those assertions to expect `create_order` instead — the behavior change is intentional and those tests encode the old contract.

- [ ] **Step 6: Commit**

```bash
git add discord_bot/bot.py tests/
git commit -m "fix: record orders on approval, not positions (Codex finding 4)"
```

---

### Task 8: Schedule the poller

**Files:**
- Modify: `main.py` (add job in the scheduler setup, and call at scan start)
- Modify: `config.py` (add `order_poll_interval_s`)
- Test: `tests/test_poller_schedule.py`

**Interfaces:**
- Consumes: `execution.ledger.poll_open_orders`, `main.configure_scheduler`
- Produces: `main.run_order_poll(bot, config)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_poller_schedule.py
from unittest.mock import MagicMock, patch
import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import main


def test_config_exposes_poll_interval(monkeypatch):
    from config import Config
    monkeypatch.setenv("ORDER_POLL_INTERVAL_S", "120")
    assert Config().order_poll_interval_s == 120


@pytest.mark.asyncio
async def test_run_order_poll_skips_in_dry_run():
    cfg = MagicMock()
    cfg.dry_run = True
    with patch("main.poll_open_orders") as mock_poll:
        await main.run_order_poll(MagicMock(), cfg)
    mock_poll.assert_not_called()


@pytest.mark.asyncio
async def test_run_order_poll_calls_poller_when_live():
    cfg = MagicMock()
    cfg.dry_run = False
    with patch("main.poll_open_orders", return_value=[]) as mock_poll:
        await main.run_order_poll(MagicMock(), cfg)
    mock_poll.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_poller_schedule.py -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute 'run_order_poll'`

- [ ] **Step 3: Add the config field**

In `config.py`, after `analyst_call_delay_s`:

```python
    order_poll_interval_s: int = _env_int("ORDER_POLL_INTERVAL_S", "300")
```

- [ ] **Step 4: Add the runner and schedule it**

In `main.py`, import and add:

```python
from execution.ledger import poll_open_orders


async def run_order_poll(bot, config) -> None:
    """Poll open broker orders and apply fills. No-op in dry run.

    Dry-run orders have no broker counterpart, so polling them would call the
    live Schwab API from a mode that is supposed to be incapable of it.
    """
    if config.dry_run:
        return
    try:
        results = await poll_open_orders(config)
        if results:
            logger.info("Order poll updated %d order(s)", len(results))
    except Exception as exc:
        logger.error("Order poll failed: %s", exc)
```

In the scheduler setup, alongside the existing cron jobs:

```python
    scheduler.add_job(
        lambda: asyncio.create_task(run_order_poll(bot, config)),
        trigger=IntervalTrigger(seconds=config.order_poll_interval_s),
        id="order_poll",
        replace_existing=True,
    )
```

Add `from apscheduler.triggers.interval import IntervalTrigger` to the imports.

Also call `await run_order_poll(bot, config)` at the top of `run_scan`, before the sell pass, so positions are current before exit signals are evaluated.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_poller_schedule.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add main.py config.py tests/test_poller_schedule.py
git commit -m "feat: schedule the order poller and run it before the sell pass"
```

---

### Task 9: Exposure from broker positions

**Files:**
- Modify: `risk/preflight.py` (from the Phase 1 spec — guard 8)
- Modify: `discord_bot/bot.py` (supply broker positions to the guard)
- Test: `tests/test_exposure_from_broker.py`

**Interfaces:**
- Consumes: `schwab_client.orders.get_positions`, `database.queries.get_orders_by_status`
- Produces: `compute_current_exposure(broker_positions: list[dict], working_orders: list[dict]) -> float` in `risk/preflight.py`

**Note:** This task depends on Phase 1 (`risk/preflight.py`) being complete. If Phase 1 has not landed, stop here and complete it first.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exposure_from_broker.py
from risk.preflight import compute_current_exposure


def test_uses_broker_market_value_not_cost_basis():
    """Codex finding 10: exposure must reflect current market value."""
    broker = [{"symbol": "AAPL", "quantity": 10, "avg_price": 100.0, "market_value": 1500.0}]
    assert compute_current_exposure(broker, []) == 1500.0


def test_working_orders_reserve_exposure():
    """An unfilled limit order still commits capital and must count."""
    broker = [{"symbol": "AAPL", "quantity": 10, "avg_price": 100.0, "market_value": 1500.0}]
    working = [{"ticker": "MSFT", "requested_shares": 5, "filled_shares": 0, "limit_price": 200.0}]
    assert compute_current_exposure(broker, working) == 2500.0


def test_partially_filled_order_reserves_only_the_remainder():
    broker = []
    working = [{"ticker": "MSFT", "requested_shares": 5, "filled_shares": 2, "limit_price": 200.0}]
    assert compute_current_exposure(broker, working) == 600.0


def test_market_order_without_limit_price_is_ignored_in_reservation():
    """A market order has no committed price; it fills fast and shows up as a position."""
    assert compute_current_exposure([], [
        {"ticker": "MSFT", "requested_shares": 5, "filled_shares": 0, "limit_price": None}
    ]) == 0.0


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
    """Return committed capital: broker market value plus unfilled order reservations.

    Uses the broker's marketValue rather than the DB's avg_cost_usd, which is
    what Codex finding 10 requires — cost basis understates exposure in a rising
    market. Unfilled limit orders reserve capital they have not yet spent, so
    they count toward the ceiling; market orders have no committed price and are
    excluded (they fill promptly and appear as positions).
    """
    exposure = sum(float(p.get("market_value", 0) or 0) for p in broker_positions)

    for order in working_orders:
        limit_price = order.get("limit_price")
        if limit_price is None:
            continue
        unfilled = float(order.get("requested_shares", 0) or 0) - float(
            order.get("filled_shares", 0) or 0
        )
        if unfilled > 0:
            exposure += unfilled * float(limit_price)

    return exposure
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_exposure_from_broker.py -v`
Expected: 5 passed

- [ ] **Step 5: Wire it into the approval path**

In `discord_bot/bot.py`, replace the DB-based `existing_total` computation with broker data gathered before `evaluate_trade`:

```python
        broker_positions = await asyncio.to_thread(get_positions, self.config)
        working_orders = await asyncio.to_thread(
            queries.get_orders_by_status, self.config.db_path,
            ("submitted", "working", "partially_filled"),
        )
```

and pass both into `evaluate_trade`, whose guard 8 calls `compute_current_exposure`.

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add risk/preflight.py discord_bot/bot.py tests/test_exposure_from_broker.py
git commit -m "fix: compute exposure from broker market value and working orders"
```

---

### Task 10: Derive `/stats` from executions

**Files:**
- Modify: `database/queries.py:92` (`get_trade_stats`)
- Test: `tests/test_stats_from_fills.py`

**Interfaces:**
- Consumes: the `trades` table, now populated only from confirmed fills
- Produces: unchanged signature; `get_trade_stats` gains an explicit exclusion of orders that never filled

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stats_from_fills.py
import os
import pytest
from database.models import initialize_db
from database.queries import create_recommendation, create_trade, get_trade_stats

DB_PATH = "test_stats_from_fills.db"


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def test_stats_use_recorded_fill_prices():
    rec = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY", reasoning="t", price=100.0
    )
    # Bought at a fill of 103.75 (not the 100.00 recommendation price), sold at 110.
    create_trade(DB_PATH, rec, "AAPL", shares=10, price=103.75, order_id="B1", side="buy")
    create_trade(DB_PATH, rec, "AAPL", shares=10, price=110.0, order_id="S1",
                 side="sell", cost_basis=103.75)
    stats = get_trade_stats(DB_PATH)
    assert stats["total"] == 1
    assert stats["wins"] == 1
    # (110 - 103.75) / 103.75 = 6.024%
    assert round(stats["avg_gain_pct"], 2) == 6.02


def test_zero_share_trades_are_excluded():
    """A cancelled order that recorded no fill must not appear in stats."""
    rec = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY", reasoning="t", price=100.0
    )
    create_trade(DB_PATH, rec, "AAPL", shares=0, price=0.0, order_id="S1",
                 side="sell", cost_basis=100.0)
    assert get_trade_stats(DB_PATH) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_stats_from_fills.py -v`
Expected: FAIL on `test_zero_share_trades_are_excluded` — zero-share rows are currently counted

- [ ] **Step 3: Add the exclusion**

In `database/queries.py:get_trade_stats`, add `AND shares > 0` to the WHERE clause alongside the existing `side='sell' AND cost_basis IS NOT NULL` filter.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_stats_from_fills.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add database/queries.py tests/test_stats_from_fills.py
git commit -m "fix: exclude zero-fill trades from performance statistics"
```

---

### Task 11: Documentation and full-suite verification

**Files:**
- Modify: `CLAUDE.md` (Module Responsibilities, Key Design Decisions, Database Schema)
- Modify: `README.md`
- Create: `pytest.ini`

- [ ] **Step 1: Create pytest.ini**

Clears the pytest-asyncio warning Codex noted:

```ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
```

- [ ] **Step 2: Update CLAUDE.md**

Add to the module table:

```markdown
| `execution/status.py` | Pure Schwab-status → internal-lifecycle mapping and order-response parsing |
| `execution/ledger.py` | `compute_fill_delta` (pure) + `poll_open_orders` — the ONLY writer of positions from broker activity |
```

Add to Key Design Decisions:

```markdown
- **Fills, not acknowledgements**: the Approve button writes an `orders` row only.
  `execution.ledger.poll_open_orders` polls broker status and creates trades and positions
  exclusively from confirmed fill quantities at actual fill prices. An unfilled GTC limit
  therefore creates no position. This replaces the RISK-05 behavior where an order
  acknowledgement was recorded as a completed trade at the scan price.
```

Add `orders` to the Database Schema section.

- [ ] **Step 3: Update the README** trading-flow description to state that positions appear only after fill confirmation, and that `ORDER_POLL_INTERVAL_S` controls the poll cadence.

- [ ] **Step 4: Run the full suite and linter**

Run: `pytest -q && ruff check .`
Expected: all tests pass, Ruff clean

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md pytest.ini
git commit -m "docs: document the execution ledger and add pytest config"
```

---

## Self-Review

**Spec coverage:**
- Finding 4 (acks treated as fills) → Tasks 1–8 ✓
- Finding 4 (partial fills) → Tasks 3, 4, 6 ✓
- Finding 4 (real fill prices) → Tasks 3, 6, 10 ✓
- Finding 4 (P&L wrong) → Task 10 ✓
- Finding 4 (sell pass sells non-existent shares) → Task 8, poll before sell pass ✓
- Finding 10 (exposure from broker values) → Task 9 ✓
- Finding 10 (working orders in reserved exposure) → Task 9 ✓
- pytest-asyncio warning → Task 11 ✓

**Not covered here, by design:** Codex's "make reconciliation capable of blocking unsafe
follow-up actions". `run_reconciliation` stays report-only. Once positions derive from
fills, most discrepancies it currently reports should disappear; making it *blocking*
should wait until we see what it reports post-ledger. Recorded in the roadmap.

**Type consistency:** `compute_fill_delta` returns `{"new_shares", "fill_price", "status"}`
in Task 4 and is consumed with those exact keys in Task 6 ✓. `parse_order_response` returns
`{"broker_status", "filled_qty", "requested_qty", "avg_fill_price", "internal_status"}` in
Task 3, consumed with those keys in Tasks 4 and 6 ✓. `create_order`'s parameters in Task 1
match the call in Task 7 ✓.

**Known gap:** `create_trade` currently requires `recommendation_id`; sell fills from the
poller pass the buy recommendation's id. That is the existing schema's shape and is left
alone deliberately — changing the FK semantics is a larger migration than this workstream
warrants.
