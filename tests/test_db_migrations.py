"""initialize_db must upgrade an EXISTING database, not only build a fresh one.

`CREATE TABLE IF NOT EXISTS` silently does nothing when the table is already
there, so a column added to the schema block later never reaches a database
created before it. Every such column needs the ALTER TABLE migration idiom.

This was caught the boring way: creating a database at one commit, running
initialize_db from the next, and looking. Reading the schema block would not
have shown it — the column is right there in the CREATE statement.
"""
import os
import sqlite3
from contextlib import closing

import pytest

from database.models import initialize_db

DB_PATH = "test_db_migrations.db"

# The orders table exactly as the previous slice shipped it, before
# predecessor_order_id existed.
_LEGACY_ORDERS = """
CREATE TABLE orders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER,
    ticker            TEXT NOT NULL,
    side              TEXT NOT NULL,
    order_type        TEXT NOT NULL,
    requested_shares  REAL NOT NULL,
    reference_price   REAL NOT NULL,
    limit_price       REAL,
    status            TEXT NOT NULL DEFAULT 'pending_submit',
    broker_order_id   TEXT,
    filled_shares     REAL NOT NULL DEFAULT 0,
    filled_notional   REAL NOT NULL DEFAULT 0,
    fills_observed    INTEGER NOT NULL DEFAULT 0,
    failure_reason    TEXT,
    submitted_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture(autouse=True)
def cleanup():
    yield
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


def _columns(table: str) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    conn.close()
    return cols


def _legacy_db_with_one_order():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_LEGACY_ORDERS)
    conn.execute(
        """INSERT INTO orders (ticker, side, order_type, requested_shares, reference_price)
           VALUES ('AAPL', 'buy', 'limit', 5.0, 100.0)"""
    )
    conn.commit()
    conn.close()


def test_predecessor_column_is_added_to_an_existing_orders_table():
    _legacy_db_with_one_order()
    assert "predecessor_order_id" not in _columns("orders")

    initialize_db(DB_PATH)

    assert "predecessor_order_id" in _columns("orders")


def test_the_upgrade_preserves_existing_orders():
    _legacy_db_with_one_order()
    initialize_db(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT ticker, requested_shares FROM orders").fetchall()
    conn.close()
    assert rows == [("AAPL", 5.0)]


def test_initialize_is_idempotent():
    """Running it twice must not raise on the already-applied migration."""
    _legacy_db_with_one_order()
    initialize_db(DB_PATH)
    initialize_db(DB_PATH)
    assert "predecessor_order_id" in _columns("orders")


def test_a_fresh_database_gets_every_ledger_table():
    initialize_db(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {
        "orders", "order_resolution_events", "ops_alerts",
        "kill_switch", "kill_switch_events",
    } <= tables


def _tables() -> set[str]:
    conn = sqlite3.connect(DB_PATH)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    return names


def test_kill_switch_tables_are_added_to_an_existing_database():
    """An existing deployment must gain the switch, not silently lack it.

    Absent tables read as UNINITIALIZED, which is not-enabled, so the failure
    mode here is refusing to trade rather than trading unguarded — but an
    operator still needs /halt to work after an upgrade.
    """
    _legacy_db_with_one_order()
    assert "kill_switch" not in _tables()

    initialize_db(DB_PATH)

    assert {"kill_switch", "kill_switch_events"} <= _tables()


def test_a_persisted_halt_survives_a_later_initialize_db():
    """Startup runs initialize_db. It must never reset an operator's halt."""
    initialize_db(DB_PATH)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO kill_switch (id, state, actor, reason) "
            "VALUES (1, 'HALTED', 'operator', 'incident')"
        )
        conn.commit()

    initialize_db(DB_PATH)

    with closing(sqlite3.connect(DB_PATH)) as conn:
        state = conn.execute("SELECT state FROM kill_switch WHERE id = 1").fetchone()[0]
    assert state == "HALTED"


def test_ops_alerts_table_is_added_to_an_existing_database():
    """A whole new table reaches an old DB where a new COLUMN would not."""
    _legacy_db_with_one_order()
    assert "ops_alerts" not in _tables()

    initialize_db(DB_PATH)

    assert "ops_alerts" in _tables()


def test_ops_alerts_upgrade_preserves_existing_rows():
    _legacy_db_with_one_order()
    initialize_db(DB_PATH)

    # closing(): a statement that raises between connect and close leaks the
    # connection, and on Windows the locked file then survives `cleanup` and
    # breaks every later run in the session. Same reason get_cursor exists.
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("INSERT INTO ops_alerts (message) VALUES ('survivor')")
        conn.commit()

    initialize_db(DB_PATH)  # a later startup must not clobber the outbox

    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute("SELECT message, delivered_at FROM ops_alerts").fetchall()
    assert rows == [("survivor", None)]
