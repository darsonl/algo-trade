"""The screen-price backfill.

Recovers a price for rows recorded before the policy existed, from the `.info`
dict each row already stores. The planning half is pure and gets the detailed
tests; the CLI is exercised end to end against a real database file, because
that is where a "preview" that silently writes would hide.
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from database.models import initialize_db

_SPEC = importlib.util.spec_from_file_location(
    "backfill_screen_price",
    Path(__file__).resolve().parent.parent / "scripts" / "backfill_screen_price.py",
)
backfill = importlib.util.module_from_spec(_SPEC)
sys.modules["backfill_screen_price"] = backfill
_SPEC.loader.exec_module(backfill)


def _row(**kw):
    base = {"id": 1, "ticker": "AAPL", "outcome": "rejected_fundamental",
            "reference_price": None, "reference_price_source": None,
            "fundamentals_json": json.dumps({"currentPrice": 195.9})}
    base.update(kw)
    return base


# --- planning (pure) ---

def test_a_recoverable_row_is_planned_with_backfill_provenance():
    updates, tally = backfill.plan_backfill([_row()])
    assert updates == [(1, 195.9, "backfill:info.currentPrice")]
    assert tally["to_update"] == 1


def test_the_source_is_marked_as_backfilled():
    """A backfilled price is recovered from a snapshot, not observed live. The
    prefix keeps the two cohorts separable if the difference ever matters."""
    updates, _ = backfill.plan_backfill([_row()])
    assert updates[0][2].startswith("backfill:")


def test_malformed_json_is_counted_and_skipped_not_fatal():
    """One bad row must not cost every other row its price."""
    updates, tally = backfill.plan_backfill(
        [_row(id=1, fundamentals_json="{not json"), _row(id=2)])
    assert [u[0] for u in updates] == [2]
    assert tally["malformed_json"] == 1


def test_a_json_scalar_is_treated_as_malformed():
    """json.loads("7") succeeds and returns an int. .get would then raise."""
    updates, tally = backfill.plan_backfill([_row(fundamentals_json="7")])
    assert updates == []
    assert tally["malformed_json"] == 1


def test_a_row_with_no_fundamentals_is_counted_and_left_alone():
    updates, tally = backfill.plan_backfill([_row(fundamentals_json=None)])
    assert updates == []
    assert tally["no_fundamentals_json"] == 1


def test_an_unusable_price_leaves_the_row_untouched():
    """Never zero a row. Absent stays absent -- an unpriced row is visibly
    missing, a wrongly-priced one is not."""
    updates, tally = backfill.plan_backfill(
        [_row(fundamentals_json=json.dumps({"currentPrice": 0}))])
    assert updates == []
    assert tally["no_usable_price"] == 1


def test_previous_close_alone_is_not_recovered():
    """Same policy as the live path, or the cohorts are not comparable."""
    updates, tally = backfill.plan_backfill(
        [_row(fundamentals_json=json.dumps({"previousClose": 195.9}))])
    assert updates == []
    assert tally["no_usable_price"] == 1


def test_running_twice_plans_nothing_the_second_time():
    updates, _ = backfill.plan_backfill([_row()])
    done = _row(reference_price=updates[0][1], reference_price_source=updates[0][2])
    updates2, tally = backfill.plan_backfill([done])
    assert updates2 == []
    assert tally["already_done"] == 1


def test_a_technical_stage_price_is_REPLACED_by_the_screen_price():
    """The whole point. A row already carrying closes.iloc[-1] must be rewritten
    to the .info price, or the pass cohort keeps a different provenance from the
    reject cohort and the comparison stays invalid."""
    updates, _ = backfill.plan_backfill(
        [_row(outcome="recommended", reference_price=999.0,
              reference_price_source=None)])
    assert updates == [(1, 195.9, "backfill:info.currentPrice")]


# --- the CLI, against a real database ---

def _db(tmp_path, rows):
    path = str(tmp_path / "b.db")
    initialize_db(path)
    conn = sqlite3.connect(path)
    for outcome, price, fundamentals in rows:
        conn.execute(
            "INSERT INTO shadow_observations (session_date, observed_at, ticker,"
            " scan_kind, stage_reached, outcome, reference_price, fundamentals_json)"
            " VALUES ('2026-08-20','2026-08-20T13:45:00Z','AAPL','stock',"
            "'fundamental',?,?,?)",
            (outcome, price, fundamentals),
        )
    conn.commit()
    conn.close()
    return path


def _stored(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return [(r["outcome"], r["reference_price"], r["reference_price_source"])
            for r in conn.execute(
                "SELECT outcome, reference_price, reference_price_source"
                " FROM shadow_observations ORDER BY id")]


def test_preview_is_the_default_and_writes_nothing(tmp_path, capsys):
    path = _db(tmp_path, [("rejected_fundamental", None,
                           json.dumps({"currentPrice": 195.9}))])
    rc = backfill.main(["--db", path])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PREVIEW ONLY" in out
    assert _stored(path) == [("rejected_fundamental", None, None)]


def test_apply_writes_the_recovered_price(tmp_path, capsys):
    path = _db(tmp_path, [("rejected_fundamental", None,
                           json.dumps({"currentPrice": 195.9}))])
    rc = backfill.main(["--db", path, "--apply"])
    assert rc == 0
    assert "Updated 1 row(s)." in capsys.readouterr().out
    assert _stored(path) == [
        ("rejected_fundamental", 195.9, "backfill:info.currentPrice")]


def test_universe_skips_are_not_touched(tmp_path):
    """They fire before any data is fetched, so there is nothing to recover and
    nothing to compare them against."""
    path = _db(tmp_path, [("skipped_open_position", None,
                           json.dumps({"currentPrice": 195.9}))])
    backfill.main(["--db", path, "--apply"])
    assert _stored(path) == [("skipped_open_position", None, None)]


def test_apply_is_idempotent(tmp_path):
    path = _db(tmp_path, [("rejected_fundamental", None,
                           json.dumps({"currentPrice": 195.9}))])
    backfill.main(["--db", path, "--apply"])
    first = _stored(path)
    backfill.main(["--db", path, "--apply"])
    assert _stored(path) == first


def test_a_missing_database_is_reported_not_tracebacked(tmp_path, capsys):
    rc = backfill.main(["--db", str(tmp_path / "nope.db")])
    assert rc == 1
    assert "No database at" in capsys.readouterr().out


def test_the_backfilled_row_becomes_markable(tmp_path):
    """The end-to-end point of the exercise: after the backfill,
    pending_shadow_marks selects a row that it could never have selected."""
    from database import queries
    path = _db(tmp_path, [("rejected_fundamental", None,
                           json.dumps({"currentPrice": 195.9}))])
    assert queries.pending_shadow_marks(path, "1w", "2026-08-21") == []
    backfill.main(["--db", path, "--apply"])
    assert len(queries.pending_shadow_marks(path, "1w", "2026-08-21")) == 1


def test_an_old_schema_is_explained_not_tracebacked(tmp_path, capsys):
    """Hit on the very first real run: the live database had not been opened by
    initialize_db since the column was added, so every query died with
    `no such column`. Adding the column is initialize_db's job, not this
    tool's -- a data tool that silently alters schema is unreasonable-about."""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE shadow_observations ("
                 " id INTEGER PRIMARY KEY, outcome TEXT,"
                 " reference_price REAL, fundamentals_json TEXT)")
    conn.commit()
    conn.close()

    assert backfill.has_provenance_column(path) is False
    rc = backfill.main(["--db", path])
    out = capsys.readouterr().out
    assert rc == 1
    assert "predates the screen-price change" in out


def test_the_tool_never_alters_the_schema_itself(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE shadow_observations ("
                 " id INTEGER PRIMARY KEY, outcome TEXT,"
                 " reference_price REAL, fundamentals_json TEXT)")
    conn.commit()
    conn.close()

    backfill.main(["--db", path, "--apply"])
    assert backfill.has_provenance_column(path) is False
