"""The `approved`-covering partial unique index (spec v4 step 11).

Ships LAST, and the ordering is the whole point. v1 covered `'pending'` only, so
protection lapsed the instant the claim flipped a row to `approved` — precisely
the window between the claim and the order submission. Covering `approved`
closes that window but needs a release valve, and v2 shipped the index while
naming a `completed` transition owned by a poller that was never built. Under
that index the first buy of any ticker would have blocked that ticker forever.

`complete_recommendation` + `sweep_terminal_recommendations` are that valve, and
they landed before this file did.
"""
import os
import sqlite3
import tempfile

import pytest

from database.models import get_cursor, initialize_db
from database.queries import (
    complete_recommendation,
    create_recommendation,
    update_recommendation_status,
)


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    initialize_db(path)
    return path


def _rec(db_path, ticker="AAPL"):
    return create_recommendation(
        db_path, ticker=ticker, signal="BUY", reasoning="r", price=100.0,
        dividend_yield=0.0, pe_ratio=15.0,
    )


def test_the_index_exists(db_path):
    with get_cursor(db_path) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )]
    assert "idx_active_rec_per_ticker" in names


def test_a_second_pending_recommendation_for_one_ticker_is_refused(db_path):
    _rec(db_path, "AAPL")

    with pytest.raises(sqlite3.IntegrityError):
        _rec(db_path, "AAPL")


def test_an_approved_row_still_blocks_a_new_recommendation(db_path):
    """The window v1 left open: the claim flips the row to `approved` and the
    order has not been submitted yet. A second recommendation here becomes a
    second real order for the same ticker."""
    rec_id = _rec(db_path, "AAPL")
    update_recommendation_status(db_path, rec_id, "approved")

    with pytest.raises(sqlite3.IntegrityError):
        _rec(db_path, "AAPL")


def test_completing_the_recommendation_frees_the_ticker(db_path):
    """The release valve, end to end. Without this the first buy of any ticker
    blocks that ticker forever."""
    rec_id = _rec(db_path, "AAPL")
    update_recommendation_status(db_path, rec_id, "approved")
    complete_recommendation(db_path, rec_id, "FILLED")

    assert _rec(db_path, "AAPL")  # no raise


@pytest.mark.parametrize("status", ["rejected", "expired", "completed"])
def test_an_inactive_row_does_not_block_the_ticker(db_path, status):
    rec_id = _rec(db_path, "AAPL")
    update_recommendation_status(db_path, rec_id, status)

    assert _rec(db_path, "AAPL")  # no raise


def test_different_tickers_are_independent(db_path):
    _rec(db_path, "AAPL")
    assert _rec(db_path, "MSFT")


# ─── The migration must survive a database that already violates it ──────────


def test_a_preexisting_database_with_duplicates_still_starts(db_path):
    """`CREATE UNIQUE INDEX` on a table that already has duplicates RAISES.

    A live database written before this index existed may hold two pending rows
    for one ticker. Taking the bot down on startup is the worse failure: the
    guards and the ledger still work without this index, and an operator who
    cannot start the bot cannot /halt it either.
    """
    # Build the duplicate state the way a pre-index database would hold it.
    with get_cursor(db_path) as conn:
        conn.execute("DROP INDEX idx_active_rec_per_ticker")
    _rec(db_path, "AAPL")
    _rec(db_path, "AAPL")

    initialize_db(db_path)  # must not raise

    with get_cursor(db_path) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )]
    assert "idx_active_rec_per_ticker" not in names, (
        "the index must be absent, not silently half-applied"
    )


def test_the_index_is_created_once_the_duplicates_are_gone(db_path):
    """The operator's fix takes effect on the next start, with no code change."""
    with get_cursor(db_path) as conn:
        conn.execute("DROP INDEX idx_active_rec_per_ticker")
    _rec(db_path, "AAPL")
    dupe = _rec(db_path, "AAPL")
    initialize_db(db_path)

    update_recommendation_status(db_path, dupe, "expired")
    initialize_db(db_path)

    with get_cursor(db_path) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )]
    assert "idx_active_rec_per_ticker" in names


# ─── The scan must not walk into the index ───────────────────────────────────


def test_a_stale_approved_recommendation_is_not_caught_by_the_session_dupe_guard(db_path):
    """Why an extra guard is needed at all.

    `ticker_recommended_today` is SESSION-scoped. An `approved` row left behind
    by an ambiguous submission yesterday is invisible to it — but it is very
    much visible to the index, so the scan would walk into an IntegrityError.
    """
    from datetime import datetime, timedelta, timezone
    from database.queries import ticker_recommended_today

    rec_id = _rec(db_path, "AAPL")
    update_recommendation_status(db_path, rec_id, "approved")
    with get_cursor(db_path) as conn:
        conn.execute(
            "UPDATE recommendations SET created_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(days=5))
             .strftime("%Y-%m-%d %H:%M:%S"), rec_id),
        )

    assert ticker_recommended_today(db_path, "AAPL") is False


def test_has_active_recommendation_sees_it(db_path):
    from database.queries import has_active_recommendation

    rec_id = _rec(db_path, "AAPL")
    assert has_active_recommendation(db_path, "AAPL") is True

    update_recommendation_status(db_path, rec_id, "approved")
    assert has_active_recommendation(db_path, "AAPL") is True

    complete_recommendation(db_path, rec_id, "FILLED")
    assert has_active_recommendation(db_path, "AAPL") is False


def test_has_active_recommendation_is_per_ticker(db_path):
    from database.queries import has_active_recommendation

    _rec(db_path, "AAPL")
    assert has_active_recommendation(db_path, "MSFT") is False
