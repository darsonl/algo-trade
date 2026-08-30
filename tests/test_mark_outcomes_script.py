"""The forward-mark runner.

`mark_due_outcomes` normally runs at the FRONT of a market-timed scan, which is
why it is bounded (`MAX_MARKS_PER_RUN`, `MARKING_TIME_BUDGET_S`). Run by hand
those bounds have no reason to exist, so the thing this wrapper must not do is
finish quietly while a backlog is still due -- that looks exactly like success.

The planning half is pure and gets the detailed tests; the CLI runs end to end
against a real database file, because that is where a "dry run" that silently
writes would hide.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from database.models import initialize_db

_SPEC = importlib.util.spec_from_file_location(
    "mark_outcomes",
    Path(__file__).resolve().parent.parent / "scripts" / "mark_outcomes.py",
)
mark_outcomes = importlib.util.module_from_spec(_SPEC)
sys.modules["mark_outcomes"] = mark_outcomes
_SPEC.loader.exec_module(mark_outcomes)


def _db(tmp_path, name="s.db"):
    path = str(tmp_path / name)
    initialize_db(path)
    return path


def _observe(db_path, ticker, session_date, price=100.0):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO shadow_observations (session_date, observed_at, ticker,"
        " scan_kind, stage_reached, outcome, reference_price)"
        " VALUES (?,?,?,'stock','recommended','recommended',?)",
        (session_date, session_date + "T13:45:00Z", ticker, price),
    )
    conn.commit()
    conn.close()


def _outcome_count(db_path):
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM shadow_outcomes").fetchone()[0]
    conn.close()
    return n


# --- what is due, without touching the network ---

def test_due_by_horizon_counts_each_horizon_separately(tmp_path):
    """The report is per horizon because they mature at different times. One
    total would hide that 50 marks are the 1w column and nothing else is ripe
    yet -- which is exactly the state this tool shipped into."""
    db = _db(tmp_path)
    _observe(db, "AAPL", "2026-08-22")

    due = mark_outcomes.due_by_horizon(db, instant=mark_outcomes.utc("2026-08-30"))

    assert due["1w"] == 1
    assert due["1m"] == 0
    assert due["3m"] == 0
    assert due["6m"] == 0


def test_an_observation_is_not_due_before_its_horizon_matures(tmp_path):
    db = _db(tmp_path)
    _observe(db, "AAPL", "2026-08-22")

    due = mark_outcomes.due_by_horizon(db, instant=mark_outcomes.utc("2026-08-28"))

    assert due["1w"] == 0, "2026-08-22 + 7d is 08-29; it is not due on the 28th"


def test_a_zero_reference_price_is_not_due(tmp_path):
    """The eligibility invariant is `reference_price > 0`, not IS NOT NULL. A
    zero makes a row selectable while guaranteeing its return is None."""
    db = _db(tmp_path)
    _observe(db, "AAPL", "2026-08-22", price=0.0)

    due = mark_outcomes.due_by_horizon(db, instant=mark_outcomes.utc("2026-08-30"))

    assert due["1w"] == 0


# --- the CLI ---

def test_dry_run_reports_what_is_due_and_writes_nothing(tmp_path, capsys):
    """A dry run must not fetch either. Reporting what is due is a pure DB
    question, and spending network calls to answer it would make the safe
    option the expensive one."""
    db = _db(tmp_path)
    _observe(db, "AAPL", "2026-08-22")

    rc = mark_outcomes.main(["--db", db, "--dry-run", "--now", "2026-08-30"])

    assert rc == 0
    assert _outcome_count(db) == 0
    out = capsys.readouterr().out
    assert "1w" in out
    assert "dry run" in out.lower()


def test_a_missing_database_is_an_error_not_a_traceback(tmp_path, capsys):
    rc = mark_outcomes.main(["--db", str(tmp_path / "nope.db"), "--dry-run"])

    assert rc == 1
    assert "no database" in capsys.readouterr().out.lower()


def test_a_database_without_the_shadow_tables_explains_itself(tmp_path, capsys):
    """Running this against a database created before the shadow log shipped is
    an ordinary mistake, and `no such table` is not an answer to it. This exact
    failure has already been shipped once, in the report."""
    db = str(tmp_path / "old.db")
    sqlite3.connect(db).execute("CREATE TABLE unrelated (id INTEGER)")

    rc = mark_outcomes.main(["--db", db, "--dry-run"])

    assert rc == 1
    assert "shadow" in capsys.readouterr().out.lower()


def test_nothing_due_is_success_not_failure(tmp_path, capsys):
    db = _db(tmp_path)
    _observe(db, "AAPL", "2026-08-22")

    rc = mark_outcomes.main(["--db", db, "--now", "2026-08-28"])

    assert rc == 0
    assert "nothing" in capsys.readouterr().out.lower()


def test_marking_writes_the_rows_and_reports_the_count(tmp_path, capsys, monkeypatch):
    db = _db(tmp_path)
    _observe(db, "AAPL", "2026-08-22")
    _stub_window(monkeypatch)

    rc = mark_outcomes.main(["--db", db, "--now", "2026-08-30"])

    assert rc == 0
    assert _outcome_count(db) == 1
    assert "1" in capsys.readouterr().out


def test_a_truncated_run_reports_what_is_still_due(tmp_path, capsys, monkeypatch):
    """THE reason this wrapper exists. `MAX_MARKS_PER_RUN` is sized for a job
    running in front of a market-timed scan; run by hand, a bound that stops
    early and says nothing is indistinguishable from finishing."""
    db = _db(tmp_path)
    for i in range(5):
        _observe(db, f"T{i}", "2026-08-22")
    _stub_window(monkeypatch)

    rc = mark_outcomes.main(["--db", db, "--now", "2026-08-30", "--max", "2"])

    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert _outcome_count(db) == 2
    assert "still due" in out, "a truncated run must say so"
    assert "3" in out


def test_the_bound_is_restored_after_an_override(tmp_path, monkeypatch):
    """`--max` reaches into the outcomes module's globals, so it must put them
    back. Leaking the override would silently re-bound the SCAN's marking for
    the rest of the process."""
    from research import outcomes
    db = _db(tmp_path)
    _observe(db, "AAPL", "2026-08-22")
    _stub_window(monkeypatch)
    before = outcomes.MAX_MARKS_PER_RUN

    mark_outcomes.main(["--db", db, "--now", "2026-08-30", "--max", "1"])

    assert outcomes.MAX_MARKS_PER_RUN == before


def _stub_window(monkeypatch):
    """Replace the one network call with a quiet two-bar window."""
    import pandas as pd
    from research import outcomes

    def _frame(_ticker, _start, _end):
        index = pd.DatetimeIndex([pd.Timestamp(d, tz="America/New_York")
                                  for d in ("2026-08-22", "2026-08-29")])
        return pd.DataFrame(
            {"Close": [100.0, 110.0], "Dividends": [0.0, 0.0],
             "Stock Splits": [0.0, 0.0]},
            index=index,
        )

    monkeypatch.setattr(outcomes, "_fetch_window", _frame)
