import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)


def _migrate_analyst_calls_to_per_model(conn) -> None:
    """Re-key analyst_calls on (date, provider, MODEL).

    Google meters the free tier per model, not per provider: on this project
    gemini-3.1-flash-lite allows 500 RPD while gemini-3.7-flash allows 20. With
    one counter per provider both tiers of the chain shared a single budget and
    `all_providers_exhausted` compared a number against itself.

    A rebuild rather than an ALTER, because SQLite cannot alter a PRIMARY KEY.
    Guarded on the column being absent, so it runs once and is a no-op after.

    Legacy rows keep their counts under model='' rather than being dropped or
    attributed to whatever model happens to be configured now. Those calls
    really were made -- discarding them hands back quota the provider has
    already spent -- but we do not know which model made them, and guessing
    would put them on a budget they never consumed.
    """
    columns = [row[1] for row in conn.execute("PRAGMA table_info(analyst_calls)")]
    if not columns or "model" in columns:
        return

    conn.execute(
        """CREATE TABLE analyst_calls_new (
               date TEXT NOT NULL,
               provider TEXT NOT NULL,
               model TEXT NOT NULL DEFAULT '',
               count INTEGER NOT NULL DEFAULT 0,
               PRIMARY KEY (date, provider, model)
           )"""
    )
    conn.execute(
        """INSERT INTO analyst_calls_new (date, provider, model, count)
               SELECT date, provider, '', count FROM analyst_calls"""
    )
    conn.execute("DROP TABLE analyst_calls")
    conn.execute("ALTER TABLE analyst_calls_new RENAME TO analyst_calls")
    conn.commit()
    logger.info("analyst_calls migrated to per-model quota tracking")


def _create_active_recommendation_index(conn) -> None:
    """One live recommendation per ticker, covering `approved` as well as `pending`.

    v1 covered `'pending'` only, so protection lapsed the instant the claim
    flipped a row to `approved` -- precisely the window between the claim and
    the order submission, which is when a duplicate becomes a second real order.

    Covering `approved` is only safe because the release valve now exists:
    `complete_recommendation`, driven by `sweep_terminal_recommendations`. Ship
    the index without it and the first buy of any ticker blocks that ticker
    forever. Never reorder those two.

    Creating a UNIQUE index RAISES if the table already violates it, and a
    database written before this index existed may hold two pending rows for one
    ticker. We log and continue rather than refusing to start: the guards, the
    ledger and the kill switch all work without this index, while an operator
    who cannot start the bot cannot `/halt` it either. Once the duplicates are
    cleared the index appears on the next start, with no code change.
    """
    try:
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_active_rec_per_ticker
                   ON recommendations(ticker)
                   WHERE status IN ('pending', 'approved')"""
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        duplicates = [
            f"{row[0]} x{row[1]}"
            for row in conn.execute(
                """SELECT ticker, COUNT(*) FROM recommendations
                    WHERE status IN ('pending', 'approved')
                    GROUP BY ticker HAVING COUNT(*) > 1"""
            )
        ]
        logger.error(
            "idx_active_rec_per_ticker NOT created: this database already has "
            "more than one active recommendation for %s. Duplicate-order "
            "protection is running on the guards alone until these rows are "
            "expired or completed.",
            ", ".join(duplicates) or "some ticker",
        )


def _backfill_intended_session_dates(conn) -> int:
    """Give every pre-existing order row an intended session. Returns rows filled.

    A NULL here is not a harmless gap. The daily notional ceiling equality-matches
    this column, so a NULL row is invisible to it and its committed capital stops
    counting against the cap -- the failure opens the guard rather than closing it,
    which is the exact shape that made a 401 read as "the account holds nothing".

    Rows whose submitted_at cannot be parsed are left NULL deliberately: inventing
    a session for them would be the same fabrication in the other direction. They
    are reported by the caller instead.
    """
    from market_time import intended_session_date

    rows = conn.execute(
        """SELECT id, submitted_at FROM orders
            WHERE intended_session_date IS NULL AND submitted_at IS NOT NULL"""
    ).fetchall()
    filled = 0
    for row in rows:
        order_id, submitted_at = row[0], row[1]
        try:
            instant = datetime.strptime(str(submitted_at), "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            continue
        conn.execute(
            "UPDATE orders SET intended_session_date = ? WHERE id = ?",
            (intended_session_date(instant).isoformat(), order_id),
        )
        filled += 1
    if filled:
        conn.commit()
    return filled


def _create_shadow_tables(conn) -> None:
    """Research tables: every candidate the pipeline saw, and its forward marks.

    Separate from `recommendations` on purpose. That table is operational -- the
    approval path, the dupe guard and `idx_active_rec_per_ticker` all read it,
    and it holds only candidates that survived every filter. Research needs the
    opposite: every candidate INCLUDING the rejected ones, because a conversion
    rate whose denominator omits the rejects is not a conversion rate.

    `human_action` lives here rather than in its own table because it is
    one-to-one with the observation and never re-stated.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS shadow_observations (
               id                    INTEGER PRIMARY KEY AUTOINCREMENT,
               session_date          TEXT NOT NULL,
               observed_at           TEXT NOT NULL,
               ticker                TEXT NOT NULL,
               scan_kind             TEXT NOT NULL,
               stage_reached         TEXT NOT NULL,
               outcome               TEXT NOT NULL,
               reject_reason         TEXT,
               fundamentals_json     TEXT,
               technicals_json       TEXT,
               headlines_json        TEXT,
               macro_json            TEXT,
               analyst_provider      TEXT,
               analyst_model         TEXT,
               analyst_signal        TEXT,
               analyst_confidence    TEXT,
               analyst_prompt_sha256 TEXT,
               analyst_raw_response  TEXT,
               cache_hit             INTEGER NOT NULL DEFAULT 0,
               recommendation_id     INTEGER,
               reference_price       REAL,
               human_action          TEXT,
               human_action_at       TEXT
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_shadow_obs_session
               ON shadow_observations(session_date, ticker)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS shadow_outcomes (
               id                   INTEGER PRIMARY KEY AUTOINCREMENT,
               observation_id       INTEGER NOT NULL REFERENCES shadow_observations(id),
               horizon              TEXT NOT NULL,
               as_of                TEXT NOT NULL,
               price                REAL,
               return_pct           REAL,
               benchmark_price      REAL,
               benchmark_return_pct REAL,
               UNIQUE(observation_id, horizon)
           )"""
    )
    conn.commit()


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
            model TEXT NOT NULL DEFAULT '',
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (date, provider, model)
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
            -- The session the broker will actually run this order in, as an ET
            -- calendar date. NOT derivable from submitted_at by a range query:
            -- an order entered 20:00 ET Friday is submitted Friday and executes
            -- Monday. The daily notional ceiling buckets on THIS, so that a
            -- Friday-night order and a Monday order cannot each claim a full
            -- allowance and then both fill against Monday's.
            intended_session_date TEXT,
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

        -- Durable outbox for operational alerts. The row is written BEFORE
        -- delivery is attempted, for the same reason the orders row is written
        -- before submission: a swallowed delivery error makes "Discord was
        -- down" indistinguishable from "nothing was wrong", and several safety
        -- states now depend on an alert actually ARRIVING.
        CREATE TABLE IF NOT EXISTS ops_alerts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            message      TEXT NOT NULL,
            -- NULL until a send has demonstrably succeeded. Undelivered rows
            -- are what the drain retries.
            delivered_at TEXT,
            attempts     INTEGER NOT NULL DEFAULT 0,
            last_error   TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- The drain reads only undelivered rows; a partial index keeps that
        -- scan proportional to the backlog, not to all alerts ever sent.
        CREATE INDEX IF NOT EXISTS idx_ops_alerts_undelivered
            ON ops_alerts(id) WHERE delivered_at IS NULL;

        -- The kill switch, as durable state rather than a process variable.
        -- Single row (CHECK id = 1): there is exactly one answer to "is trading
        -- on", and it has to be the same answer in every process, since a halt
        -- typed into one process must stop the others too. No row at all means
        -- UNINITIALIZED, which is NOT enabled — forgetting to seed it must fail
        -- closed.
        CREATE TABLE IF NOT EXISTS kill_switch (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            state      TEXT NOT NULL,
            actor      TEXT,
            reason     TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Append-only record of every transition, including the initial seed.
        -- How trading came to be on is as much a part of the incident record as
        -- who turned it off.
        CREATE TABLE IF NOT EXISTS kill_switch_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            previous_state TEXT NOT NULL,
            new_state      TEXT NOT NULL,
            actor          TEXT NOT NULL,
            reason         TEXT NOT NULL,
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );
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
            "ALTER TABLE orders ADD COLUMN intended_session_date TEXT"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        conn.execute(
            "ALTER TABLE recommendations ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    _migrate_analyst_calls_to_per_model(conn)
    _create_active_recommendation_index(conn)
    _backfill_intended_session_dates(conn)
    _create_shadow_tables(conn)
    # After the column is guaranteed to exist -- inside the schema block above
    # this would raise "no such column" on any pre-existing database.
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_orders_side_intended_session
               ON orders(side, intended_session_date)"""
    )
    conn.commit()

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
