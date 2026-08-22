"""The funnel report.

`build_funnel` is pure and tested without a database. The SQL and the rendering
are NOT -- and they are where the defects live (a window that applies to one
half of the report but not the other, a sample size that does not match the
mean beside it), so those are exercised against a real database file.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from database.models import initialize_db

_SPEC = importlib.util.spec_from_file_location(
    "shadow_report",
    Path(__file__).resolve().parent.parent / "scripts" / "shadow_report.py",
)
report = importlib.util.module_from_spec(_SPEC)
sys.modules["shadow_report"] = report
_SPEC.loader.exec_module(report)


# --- the pure aggregation ---

def test_funnel_counts_every_outcome():
    rows = [
        {"outcome": "rejected_fundamental"},
        {"outcome": "rejected_fundamental"},
        {"outcome": "rejected_signal"},
        {"outcome": "recommended"},
    ]
    assert report.build_funnel(rows) == {
        "rejected_fundamental": 2, "rejected_signal": 1, "recommended": 1}


def test_funnel_of_nothing_is_empty_not_an_error():
    assert report.build_funnel([]) == {}


def test_unknown_outcomes_are_still_counted():
    """A row written by an older version must not vanish from the totals --
    a denominator that silently drops rows is the defect this guards."""
    assert report.build_funnel([{"outcome": "from_the_future"}]) == {
        "from_the_future": 1}


# --- the queries, against a real database ---

def _db(tmp_path):
    path = str(tmp_path / "report.db")
    initialize_db(path)
    return path


def _observe(db_path, ticker, session_date, outcome="recommended", price=100.0):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO shadow_observations (session_date, observed_at, ticker,"
        " scan_kind, stage_reached, outcome, reference_price)"
        " VALUES (?,?,?,'stock','recommended',?,?)",
        (session_date, session_date + "T13:45:00Z", ticker, outcome, price),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _mark(db_path, oid, horizon, ret, bench):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO shadow_outcomes (observation_id, horizon, as_of, price,"
        " return_pct, benchmark_price, benchmark_return_pct)"
        " VALUES (?,?,'2026-08-27',110.0,?,500.0,?)",
        (oid, horizon, ret, bench),
    )
    conn.commit()


def test_since_applies_to_the_marks_as_well_as_the_funnel(tmp_path):
    """Both halves of one report must describe the same window.

    Aggregating shadow_outcomes without joining back to the observations would
    report marks for rows the funnel above excluded -- a report whose two
    sections silently disagree about what they are counting.
    """
    db = _db(tmp_path)
    old = _observe(db, "AAPL", "2026-07-01")
    new = _observe(db, "MSFT", "2026-08-20")
    _mark(db, old, "1w", 50.0, 1.0)
    _mark(db, new, "1w", 10.0, 1.0)

    marks = report.load_marks(db, "2026-08-01")
    assert len(marks) == 1
    assert marks[0]["n"] == 1
    assert marks[0]["ret"] == pytest.approx(10.0)
    assert len(report.load_observations(db, "2026-08-01")) == 1


def test_the_sample_size_matches_what_the_mean_was_computed_over(tmp_path):
    """A mark whose return could not be computed is a NULL. AVG skips it, so
    COUNT(*) beside that mean would overstate the sample."""
    db = _db(tmp_path)
    a = _observe(db, "AAPL", "2026-08-20")
    b = _observe(db, "MSFT", "2026-08-20")
    _mark(db, a, "1w", 10.0, 1.0)
    _mark(db, b, "1w", None, None)

    marks = report.load_marks(db)
    assert marks[0]["n"] == 1
    assert marks[0]["ret"] == pytest.approx(10.0)


def test_all_observations_are_loaded_when_no_window_is_given(tmp_path):
    db = _db(tmp_path)
    _observe(db, "AAPL", "2026-07-01")
    _observe(db, "MSFT", "2026-08-20")
    assert len(report.load_observations(db)) == 2


# --- the rendering ---

def test_an_empty_log_says_so_rather_than_printing_a_bare_zero(tmp_path):
    lines = report.render_report([], [])
    assert "nothing recorded yet" in "\n".join(lines)


def test_horizons_are_ordered_chronologically_not_alphabetically():
    """Sorted as strings these read 1m, 1w, 3m, 6m -- wrong, and wrong in a way
    nobody notices in a column of numbers."""
    marks = [{"horizon": h, "n": 1, "ret": 1.0, "bench": 0.0}
             for h in ("6m", "1m", "3m", "1w")]
    lines = report.render_report([{"outcome": "recommended"}], marks)
    shown = [line.split()[0] for line in lines if line.startswith("  ")
             and line.split()[0] in ("1w", "1m", "3m", "6m")]
    assert shown == ["1w", "1m", "3m", "6m"]


def test_the_mean_carries_its_caveat():
    """The number is printed for orientation. Printing it bare invites exactly
    the reading the spec forbids."""
    marks = [{"horizon": "1w", "n": 3, "ret": 2.0, "bench": 1.0}]
    text = "\n".join(report.render_report([{"outcome": "recommended"}], marks))
    assert "not evidence" in text


def test_the_spread_is_the_ticker_return_minus_the_benchmark():
    marks = [{"horizon": "1w", "n": 3, "ret": 2.5, "bench": 1.0}]
    text = "\n".join(report.render_report([{"outcome": "recommended"}], marks))
    assert "spread  +1.50" in text


def test_a_null_mean_renders_as_zero_rather_than_crashing():
    """AVG over an all-NULL group returns None."""
    marks = [{"horizon": "1w", "n": 0, "ret": None, "bench": None}]
    text = "\n".join(report.render_report([{"outcome": "recommended"}], marks))
    assert "n=   0" in text


# --- the CLI ---

def test_the_cli_reports_a_missing_database_instead_of_traceback(tmp_path, capsys):
    rc = report.main(["--db", str(tmp_path / "nope.db")])
    assert rc == 1
    assert "Has the bot ever run?" in capsys.readouterr().out


def test_a_database_predating_the_shadow_log_is_explained_not_tracebacked(
        tmp_path, capsys):
    """The live database was created before these tables existed, so this is
    the FIRST thing anyone running the report hits. Caught by actually running
    the tool against the real database rather than only against fixtures."""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE recommendations (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    assert report.shadow_tables_present(db) is False
    rc = report.main(["--db", db])
    out = capsys.readouterr().out
    assert rc == 1
    assert "no shadow log tables yet" in out
    assert "read-only" in out


def test_the_report_never_creates_the_tables_it_is_missing(tmp_path):
    """Read-only by design. A report that migrates the live database while the
    bot is running is not a report."""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE recommendations (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    report.main(["--db", db])
    assert report.shadow_tables_present(db) is False


def test_the_cli_prints_the_funnel(tmp_path, capsys):
    db = _db(tmp_path)
    _observe(db, "AAPL", "2026-08-20", outcome="rejected_fundamental")
    _observe(db, "MSFT", "2026-08-20", outcome="recommended")

    rc = report.main(["--db", db])
    out = capsys.readouterr().out
    assert rc == 0
    assert "rejected_fundamental" in out
    assert "recommended" in out
    assert "shadow observations (all time): 2" in out
