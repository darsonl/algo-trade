"""The shadow tables, created against a PRE-EXISTING database.

Migrations in this repo are always tested against a database that already
exists and lacks the new objects, because that is the only case that occurs in
production. A test that creates a fresh file proves nothing about upgrade.
"""
import sqlite3

from database.models import initialize_db


def _preexisting_db(path):
    """A database that predates the shadow tables but is otherwise REAL.

    `status` is included deliberately. It has been in `CREATE TABLE
    recommendations` since this project's first commit, and
    `_create_active_recommendation_index` builds a PARTIAL index whose
    `WHERE status IN ('pending','approved')` clause is parsed against the table
    at creation time. A fixture without it describes a database that has never
    existed -- and forces production to carry a migration for a state that
    cannot occur, which is the wrong direction of fix.
    """
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE recommendations ("
        "    id INTEGER PRIMARY KEY,"
        "    ticker TEXT,"
        "    status TEXT NOT NULL DEFAULT 'pending'"
        ")"
    )
    conn.commit()
    conn.close()


def test_shadow_tables_are_created_on_an_existing_database(tmp_path):
    db = str(tmp_path / "pre.db")
    _preexisting_db(db)

    initialize_db(db)

    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "shadow_observations" in names
    assert "shadow_outcomes" in names


def test_shadow_observation_columns(tmp_path):
    db = str(tmp_path / "a.db")
    initialize_db(db)
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(shadow_observations)")}
    assert {
        "id", "session_date", "observed_at", "ticker", "scan_kind",
        "stage_reached", "outcome", "reject_reason",
        "fundamentals_json", "technicals_json", "headlines_json", "macro_json",
        "analyst_provider", "analyst_model", "analyst_signal",
        "analyst_confidence", "analyst_prompt_sha256", "analyst_raw_response",
        "cache_hit", "recommendation_id", "reference_price",
        "human_action", "human_action_at",
    } <= cols


def test_one_outcome_row_per_observation_and_horizon(tmp_path):
    """Re-marking the same horizon must not create a second row: the marking job
    reruns on every scan and would otherwise multiply rows without bound."""
    db = str(tmp_path / "b.db")
    initialize_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO shadow_observations (session_date, observed_at, ticker,"
        " scan_kind, stage_reached, outcome) VALUES"
        " ('2026-08-20','2026-08-20T13:45:00Z','AAPL','stock','analyst','recommended')"
    )
    conn.execute(
        "INSERT INTO shadow_outcomes (observation_id, horizon, as_of, price)"
        " VALUES (1,'1w','2026-08-27',1.0)"
    )
    try:
        conn.execute(
            "INSERT INTO shadow_outcomes (observation_id, horizon, as_of, price)"
            " VALUES (1,'1w','2026-08-27',2.0)"
        )
        raise AssertionError("duplicate (observation_id, horizon) was accepted")
    except sqlite3.IntegrityError:
        pass


def test_initialize_db_is_idempotent_for_shadow_tables(tmp_path):
    db = str(tmp_path / "c.db")
    initialize_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO shadow_observations (session_date, observed_at, ticker,"
        " scan_kind, stage_reached, outcome) VALUES"
        " ('2026-08-20','2026-08-20T13:45:00Z','MSFT','stock','universe','skipped_open_position')"
    )
    conn.commit()
    conn.close()

    initialize_db(db)  # second startup

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM shadow_observations").fetchone()[0] == 1
