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
    assert {"orders", "order_resolution_events"} <= tables
