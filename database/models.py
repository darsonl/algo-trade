import sqlite3
from contextlib import contextmanager


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection to db_path with WAL mode and Row factory enabled.

    Sets busy_timeout so a connection waits (up to 5s) for a competing writer to
    release its lock instead of immediately raising "database is locked" — defensive
    hardening for the case where a DB write runs off the event-loop thread.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def get_cursor(db_path: str):
    """Yield a connection that commits on clean exit and ALWAYS closes.

    Replaces the repeated open / commit / close dance in queries.py and fixes the
    connection leak in that pattern: if a statement raised between open and close, the
    old code skipped conn.close(). Here close() runs in `finally`, and commit() runs
    only on a clean exit (an exception closes without committing and re-propagates).
    Reads are fine too — commit() on a read-only connection is a harmless no-op.
    """
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def immediate_transaction(db_path: str):
    """Yield a connection inside a BEGIN IMMEDIATE write transaction.

    Takes the write lock UP FRONT, before any read. `get_cursor` cannot do this
    job: it runs in autocommit until the first write, so two processes can each
    read the same daily total and each then reserve against it — the cap check
    and the reservation are not atomic. BEGIN IMMEDIATE makes the second writer
    block (or time out) at the START of the sequence instead.

    Rolls back on any exception, so a guard that rejects AFTER inserting the
    order row leaves no reservation behind.
    """
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_db(db_path: str) -> None:
    """Create the recommendations, trades, and analyst_cache tables if absent, and run the earnings_growth migration on existing DBs."""
    conn = get_connection(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            signal TEXT NOT NULL,
            reasoning TEXT NOT NULL,
            price REAL NOT NULL,
            dividend_yield REAL,
            pe_ratio REAL,
            earnings_growth REAL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL DEFAULT (datetime('now', '+24 hours')),
            discord_message_id TEXT,
            asset_type TEXT NOT NULL DEFAULT 'stock',
            confidence TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            shares REAL NOT NULL,
            price REAL NOT NULL,
            order_id TEXT,
            side TEXT NOT NULL DEFAULT 'buy',
            executed_at TEXT NOT NULL DEFAULT (datetime('now')),
            limit_price REAL,
            order_type TEXT,
            FOREIGN KEY (recommendation_id) REFERENCES recommendations(id)
        );

        CREATE TABLE IF NOT EXISTS analyst_cache (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker        TEXT NOT NULL,
            headline_hash TEXT NOT NULL,
            signal        TEXT NOT NULL,
            reasoning     TEXT NOT NULL,
            confidence    TEXT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(ticker, headline_hash)
        );

        CREATE TABLE IF NOT EXISTS positions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker       TEXT NOT NULL UNIQUE,
            shares       REAL NOT NULL,
            avg_cost_usd REAL NOT NULL,
            entry_date   TEXT NOT NULL DEFAULT (date('now')),
            status       TEXT NOT NULL DEFAULT 'open',
            last_price   REAL,
            last_updated TEXT,
            sell_blocked BOOLEAN DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS analyst_calls (
            date TEXT NOT NULL,
            provider TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (date, provider)
        );

        -- Durable state for anything that may have reached the broker. The row is
        -- created BEFORE submission, so a broker-accepted order can never exist
        -- outside the ledger; the cost is a crash window handled by
        -- sweep_stale_pending_submits.
        CREATE TABLE IF NOT EXISTS orders (
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
            -- 0 means nobody has read fill data for this order yet. An unverified
            -- zero is NOT evidence of no fill, so terminal statuses stay fully
            -- committed until this flips. See order_accounting.order_commitment.
            fills_observed    INTEGER NOT NULL DEFAULT 0,
            failure_reason    TEXT,
            -- Set when this row is the successor Schwab created to replace another.
            -- Editing an order does not mutate it: the original is killed and a new
            -- one appears under a new id, so the chain has to be walkable.
            predecessor_order_id INTEGER,
            -- Worst-case reservation while a submission outcome is ambiguous: the
            -- summed commitment of every broker order that might be this one.
            -- Durable and monotonic, because candidates leave the working-order
            -- endpoint and a later empty observation is not evidence of absence.
            reserved_notional_override REAL,
            submitted_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (recommendation_id) REFERENCES recommendations(id),
            FOREIGN KEY (predecessor_order_id) REFERENCES orders(id)
        );

        -- Range predicate over (side, submitted_at) is how the session-bucketed
        -- daily notional query reads; keeping submitted_at unwrapped keeps it usable.
        CREATE INDEX IF NOT EXISTS idx_orders_side_submitted
            ON orders(side, submitted_at);

        -- One real broker order backs at most one ledger row. Guards manual
        -- `adopt` against attaching the same order twice.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_broker_id
            ON orders(broker_order_id) WHERE broker_order_id IS NOT NULL;

        -- Append-only audit of operator overrides. /resolve is report-only, so a
        -- human decides what an ambiguous submission actually was; this records
        -- who decided, on what evidence, and what it changed. Never updated or
        -- deleted — a later decision appends, so the earlier one survives.
        CREATE TABLE IF NOT EXISTS order_resolution_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id        INTEGER NOT NULL,
            resolution      TEXT NOT NULL,
            actor           TEXT NOT NULL,
            evidence        TEXT NOT NULL,
            broker_order_id TEXT,
            previous_status TEXT NOT NULL,
            new_status      TEXT NOT NULL,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );

        CREATE INDEX IF NOT EXISTS idx_resolution_events_order
            ON order_resolution_events(order_id, id);

        -- Broker orders that MIGHT be an ambiguous submission. Append-only: each
        -- observation is kept so the reservation can be justified after the fact,
        -- once the candidates themselves have left the broker's endpoint.
        CREATE TABLE IF NOT EXISTS order_candidates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id        INTEGER NOT NULL,
            broker_order_id TEXT,
            symbol          TEXT,
            side            TEXT,
            quantity        REAL,
            limit_price     REAL,
            notional        REAL NOT NULL,
            observed_at     TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );

        CREATE INDEX IF NOT EXISTS idx_order_candidates_order
            ON order_candidates(order_id, id);
    """)
    conn.commit()
    # CREATE TABLE IF NOT EXISTS does nothing when the table already exists, so a
    # column added to the schema block above never reaches a database created
    # before it. Every such column needs this idiom.
    try:
        conn.execute(
            "ALTER TABLE orders ADD COLUMN predecessor_order_id INTEGER"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        conn.execute(
            "ALTER TABLE orders ADD COLUMN reserved_notional_override REAL"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        conn.execute(
            "ALTER TABLE recommendations ADD COLUMN earnings_growth REAL"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN sell_blocked BOOLEAN DEFAULT 0"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE trades ADD COLUMN side TEXT DEFAULT 'buy'"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE recommendations ADD COLUMN asset_type TEXT DEFAULT 'stock'"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE recommendations ADD COLUMN confidence TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE analyst_cache ADD COLUMN confidence TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE trades ADD COLUMN cost_basis REAL")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE trades ADD COLUMN limit_price REAL")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE trades ADD COLUMN order_type TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.close()
