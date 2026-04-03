import sqlite3


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection to db_path with WAL mode and Row factory enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


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
            discord_message_id TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            shares REAL NOT NULL,
            price REAL NOT NULL,
            order_id TEXT,
            executed_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (recommendation_id) REFERENCES recommendations(id)
        );

        CREATE TABLE IF NOT EXISTS analyst_cache (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker        TEXT NOT NULL,
            headline_hash TEXT NOT NULL,
            signal        TEXT NOT NULL,
            reasoning     TEXT NOT NULL,
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
            last_updated TEXT
        );
    """)
    conn.commit()
    try:
        conn.execute(
            "ALTER TABLE recommendations ADD COLUMN earnings_growth REAL"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.close()
