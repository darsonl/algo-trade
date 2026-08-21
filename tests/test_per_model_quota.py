"""Analyst quota is metered PER MODEL, because Google meters it per model.

The dashboard for this project (free tier, 2026-08-21):

    gemini-3.1-flash-lite   15 RPM   500 RPD
    gemini-3.7-flash         5 RPM    20 RPD
    gemma-4-31b-it          30 RPM  14400 RPD

`analyst_calls` was keyed on (date, provider). Both tiers of the chain are
provider 'gemini', so they shared ONE counter — and `all_providers_exhausted`
computed `primary_count` and `fallback_count` as literally the same number.
A fallback with 25x the primary's daily budget was invisible to the app, and a
single conservative limit had to cover both.
"""
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import pytest

from database.models import get_cursor, initialize_db
from database.queries import (
    get_analyst_call_count_today,
    increment_analyst_call_count,
)

NOW = datetime(2026, 8, 21, 17, 0, tzinfo=timezone.utc)  # 13:00 ET, mid-session


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    initialize_db(path)
    return path


def test_two_models_on_one_provider_count_separately(db_path):
    """The whole point. 3.1-flash-lite and 3.7-flash are both 'gemini' but have
    500 and 20 RPD respectively."""
    increment_analyst_call_count(db_path, "gemini", "gemini-3.1-flash-lite", instant=NOW)
    increment_analyst_call_count(db_path, "gemini", "gemini-3.1-flash-lite", instant=NOW)
    increment_analyst_call_count(db_path, "gemini", "gemini-3.7-flash", instant=NOW)

    assert get_analyst_call_count_today(
        db_path, "gemini", "gemini-3.1-flash-lite", instant=NOW) == 2
    assert get_analyst_call_count_today(
        db_path, "gemini", "gemini-3.7-flash", instant=NOW) == 1


def test_an_unused_model_reads_zero(db_path):
    increment_analyst_call_count(db_path, "gemini", "gemini-3.7-flash", instant=NOW)

    assert get_analyst_call_count_today(
        db_path, "gemini", "gemini-3.1-flash-lite", instant=NOW) == 0


def test_the_same_model_name_on_two_providers_is_still_separate(db_path):
    """Two services, two quotas, even under one model name."""
    increment_analyst_call_count(db_path, "gemini", "gpt-4o-mini", instant=NOW)
    increment_analyst_call_count(db_path, "github", "gpt-4o-mini", instant=NOW)

    assert get_analyst_call_count_today(db_path, "gemini", "gpt-4o-mini", instant=NOW) == 1
    assert get_analyst_call_count_today(db_path, "github", "gpt-4o-mini", instant=NOW) == 1


def test_counting_is_still_bucketed_on_the_market_session(db_path):
    """Per-model must not quietly undo the session-date bucketing."""
    from datetime import timedelta
    increment_analyst_call_count(db_path, "gemini", "m", instant=NOW)

    next_session = NOW + timedelta(days=1)
    assert get_analyst_call_count_today(db_path, "gemini", "m", instant=next_session) == 0


# ─── Migration against a PRE-EXISTING database ───────────────────────────────


def test_a_legacy_table_is_rebuilt_with_the_model_column(db_path):
    """`CREATE TABLE IF NOT EXISTS` does nothing to an existing table, and a
    PRIMARY KEY cannot be ALTERed in SQLite — so this needs a real rebuild."""
    with get_cursor(db_path) as conn:
        conn.execute("DROP TABLE analyst_calls")
        conn.execute(
            """CREATE TABLE analyst_calls (
                   date TEXT NOT NULL,
                   provider TEXT NOT NULL,
                   count INTEGER NOT NULL DEFAULT 0,
                   PRIMARY KEY (date, provider)
               )"""
        )
        conn.execute(
            "INSERT INTO analyst_calls (date, provider, count) VALUES ('2026-08-20','gemini',7)"
        )

    initialize_db(db_path)

    with get_cursor(db_path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(analyst_calls)")]
    assert "model" in cols


def test_the_migration_preserves_legacy_counts(db_path):
    """Those calls really happened. Dropping them would hand back quota that
    the provider has already spent."""
    with get_cursor(db_path) as conn:
        conn.execute("DROP TABLE analyst_calls")
        conn.execute(
            """CREATE TABLE analyst_calls (
                   date TEXT NOT NULL, provider TEXT NOT NULL,
                   count INTEGER NOT NULL DEFAULT 0,
                   PRIMARY KEY (date, provider))"""
        )
        conn.execute(
            "INSERT INTO analyst_calls (date, provider, count) VALUES ('2026-08-20','gemini',7)"
        )

    initialize_db(db_path)

    with get_cursor(db_path) as conn:
        rows = [tuple(r) for r in conn.execute(
            "SELECT date, provider, model, count FROM analyst_calls"
        )]
    assert rows == [("2026-08-20", "gemini", "", 7)]


def test_the_migration_is_idempotent(db_path):
    increment_analyst_call_count(db_path, "gemini", "gemini-3.7-flash", instant=NOW)
    initialize_db(db_path)
    initialize_db(db_path)

    assert get_analyst_call_count_today(
        db_path, "gemini", "gemini-3.7-flash", instant=NOW) == 1


def test_the_rebuilt_table_actually_enforces_the_new_key(db_path):
    """A rebuild that forgot the PK lets duplicate rows accumulate, and the
    counter then silently under-reports — it reads one row, not the sum.

    This MUST force the migration path first. An earlier version inserted into
    the fixture's table, which `initialize_db` builds from the schema block
    (already correct), so it never touched the rebuilt table and a mutation
    dropping the PK from the rebuild survived it.
    """
    with get_cursor(db_path) as conn:
        conn.execute("DROP TABLE analyst_calls")
        conn.execute(
            """CREATE TABLE analyst_calls (
                   date TEXT NOT NULL, provider TEXT NOT NULL,
                   count INTEGER NOT NULL DEFAULT 0,
                   PRIMARY KEY (date, provider))"""
        )

    initialize_db(db_path)  # <- the rebuild under test

    with get_cursor(db_path) as conn:
        conn.execute(
            "INSERT INTO analyst_calls (date, provider, model, count) VALUES (?,?,?,?)",
            ("2026-08-21", "gemini", "m", 1),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO analyst_calls (date, provider, model, count) VALUES (?,?,?,?)",
                ("2026-08-21", "gemini", "m", 1),
            )
