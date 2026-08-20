"""The one-second limbo at expires_at (spec v4 §10).

`claim_recommendation_tx` treats a recommendation as live only while
`expires_at > now`, so `now == expires_at` is already too late to approve.
`expire_stale_recommendations` used `expires_at < now`, so that same instant was
also too early to expire. For exactly one second a row was neither claimable nor
expirable: a button that refuses, on a recommendation that still reads
`pending`, with nothing in the log to say why.

The two predicates must be complements. They are pinned together here, against
ONE pinned instant, because a test that lets the clock move proves nothing about
the boundary — the second ticks and `<` becomes true on its own.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from database.models import get_cursor, initialize_db
from database.queries import (
    claim_recommendation_tx,
    create_recommendation,
    expire_stale_recommendations,
    get_recommendation,
)

NOW = datetime(2026, 8, 21, 14, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_path():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    initialize_db(path)
    return path


def _rec_expiring_at(db_path, when, ticker="AAPL"):
    rec_id = create_recommendation(
        db_path, ticker=ticker, signal="BUY", reasoning="r", price=100.0,
        dividend_yield=0.0, pe_ratio=15.0,
    )
    with get_cursor(db_path) as conn:
        conn.execute(
            "UPDATE recommendations SET expires_at = ? WHERE id = ?",
            (when.strftime("%Y-%m-%d %H:%M:%S"), rec_id),
        )
    return rec_id


def test_a_recommendation_expiring_exactly_now_is_expired(db_path):
    """The boundary itself. `<` left this row pending forever-ish."""
    rec_id = _rec_expiring_at(db_path, NOW)

    expire_stale_recommendations(db_path, instant=NOW)

    assert get_recommendation(db_path, rec_id)["status"] == "expired"


def test_the_same_instant_is_already_unclaimable(db_path):
    """The other half of the complement, so the two cannot drift apart."""
    rec_id = _rec_expiring_at(db_path, NOW)

    with get_cursor(db_path) as conn:
        claimed = claim_recommendation_tx(conn, rec_id, "approved", instant=NOW)

    assert claimed is False


def test_no_instant_is_both_claimable_and_expirable(db_path):
    """Belt and braces: the two predicates must partition the timeline, with no
    row falling through both."""
    for offset in (-2, -1, 0, 1, 2):
        instant = NOW + timedelta(seconds=offset)
        rec_id = _rec_expiring_at(db_path, NOW, ticker=f"T{offset}")

        with get_cursor(db_path) as conn:
            claimable = claim_recommendation_tx(conn, rec_id, "approved", instant=instant)
        if claimable:
            with get_cursor(db_path) as conn:
                conn.execute(
                    "UPDATE recommendations SET status = 'pending' WHERE id = ?", (rec_id,)
                )

        expire_stale_recommendations(db_path, instant=instant)
        expired = get_recommendation(db_path, rec_id)["status"] == "expired"

        assert claimable != expired, (
            f"at offset {offset}s the recommendation was "
            f"{'both claimable and expirable' if claimable and expired else 'neither'}"
        )


def test_a_recommendation_expiring_later_survives(db_path):
    rec_id = _rec_expiring_at(db_path, NOW + timedelta(hours=1))

    expire_stale_recommendations(db_path, instant=NOW)

    assert get_recommendation(db_path, rec_id)["status"] == "pending"


def test_expiry_still_defaults_to_the_current_time(db_path):
    """The instant parameter is for tests; production passes nothing."""
    rec_id = _rec_expiring_at(db_path, datetime.now(timezone.utc) - timedelta(hours=2))

    expire_stale_recommendations(db_path)

    assert get_recommendation(db_path, rec_id)["status"] == "expired"


def test_only_pending_rows_are_expired(db_path):
    """An approved recommendation with an unresolved order must not be swept
    into `expired` — guard 11 and the sweep own that row now."""
    rec_id = _rec_expiring_at(db_path, NOW - timedelta(hours=1))
    with get_cursor(db_path) as conn:
        conn.execute(
            "UPDATE recommendations SET status = 'approved' WHERE id = ?", (rec_id,)
        )

    expire_stale_recommendations(db_path, instant=NOW)

    assert get_recommendation(db_path, rec_id)["status"] == "approved"
