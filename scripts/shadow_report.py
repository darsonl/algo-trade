"""Read-only funnel report over the shadow log.

Answers the question the first live scan could not: "0 recommendations is
correct" and "0 recommendations is a silent failure" look identical without the
denominators. Tracing that outcome by hand took a session; this prints it.

    .venv/Scripts/python.exe scripts/shadow_report.py
    .venv/Scripts/python.exe scripts/shadow_report.py --since 2026-08-01
    .venv/Scripts/python.exe scripts/shadow_report.py --db /path/to/a/copy.db

Read-only by construction: every statement here is a SELECT, so it is safe to
point at the live database while the bot is running.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402
from research.outcomes import HORIZONS  # noqa: E402

# The earliest possible session date, as a string comparison floor. Session
# dates are TEXT in ISO form, so this sorts below every real value.
EPOCH = "0000-00-00"


def build_funnel(rows) -> dict:
    """Count outcomes. Unknown values are counted, never dropped.

    A row written by an older version of the recorder still happened, and a
    denominator that silently drops rows is not a denominator.
    """
    return dict(Counter(r["outcome"] for r in rows))


def load_observations(db_path: str, since: str = EPOCH) -> list:
    with _connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM shadow_observations WHERE session_date >= ?"
            " ORDER BY session_date, id",
            (since,),
        ).fetchall()


def load_marks(db_path: str, since: str = EPOCH) -> list:
    """Forward marks for observations inside the SAME window as the funnel.

    The join is what makes that true. Aggregating `shadow_outcomes` on its own
    would report marks for observations the funnel above excluded, so the two
    halves of one report would describe two different date ranges.

    `COUNT(return_pct)` rather than `COUNT(*)`: SQL's AVG skips NULLs, so
    counting all rows would print a sample size the mean was not computed over.
    """
    with _connect(db_path) as conn:
        return conn.execute(
            """SELECT s.horizon                    AS horizon,
                      COUNT(s.return_pct)          AS n,
                      AVG(s.return_pct)            AS ret,
                      AVG(s.benchmark_return_pct)  AS bench
                 FROM shadow_outcomes s
                 JOIN shadow_observations o ON o.id = s.observation_id
                WHERE o.session_date >= ?
                GROUP BY s.horizon""",
            (since,),
        ).fetchall()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


SHADOW_TABLES = ("shadow_observations", "shadow_outcomes")


def shadow_tables_present(db_path: str) -> bool:
    """Whether this database has the shadow log at all.

    A database created before the shadow log shipped has neither table, and
    every query below then dies with `no such table` -- a traceback, for the
    ordinary case of running the report before the bot has restarted once.

    Checked rather than created: this tool is read-only by design, and a report
    that silently migrates the live database while the bot is running is not a
    report. `initialize_db` owns the schema and runs at startup.
    """
    with _connect(db_path) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    return set(SHADOW_TABLES) <= names


def render_report(rows, marks, since: str = EPOCH) -> list[str]:
    """The whole report as lines. Pure, so it can be tested without printing."""
    funnel = build_funnel(rows)
    total = sum(funnel.values())
    window = "all time" if since == EPOCH else f"since {since}"
    out = [f"shadow observations ({window}): {total}", ""]
    if not total:
        out.append("  nothing recorded yet")
        return out

    for outcome, n in sorted(funnel.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"  {outcome:34} {n:5}  ({n / total:5.1%})")

    if marks:
        # Horizon order comes from HORIZONS rather than SQL, because sorting
        # these strings alphabetically reads 1m, 1w, 3m, 6m -- chronologically
        # wrong, and wrong in a way nobody notices in a column of numbers.
        order = {h: i for i, h in enumerate(HORIZONS)}
        out += ["", "forward marks (mean %, vs SPY over the same window):"]
        for m in sorted(marks, key=lambda r: order.get(r["horizon"], 99)):
            ret, bench = m["ret"] or 0.0, m["bench"] or 0.0
            out.append(
                f"  {m['horizon']:4} n={m['n']:4}  "
                f"ret {ret:+6.2f}  spy {bench:+6.2f}  spread {ret - bench:+6.2f}"
            )
        out += [
            "",
            "Mean is shown for orientation only. At these trade counts it is",
            "not evidence -- see the spec's metric restrictions before quoting it.",
        ]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Funnel report over the shadow log.")
    ap.add_argument("--since", default=EPOCH,
                    help="earliest session date to include (YYYY-MM-DD)")
    ap.add_argument("--db", default=None,
                    help="database to read (default: the configured one)")
    args = ap.parse_args(argv)

    db_path = args.db or Config().db_path
    if not Path(db_path).exists():
        print(f"No database at {db_path}. Has the bot ever run?")
        return 1

    if not shadow_tables_present(db_path):
        print(f"The database at {db_path} has no shadow log tables yet.")
        print("They are created by initialize_db at the next bot startup;")
        print("this tool is read-only and will not create them.")
        return 1

    for line in render_report(
        load_observations(db_path, args.since),
        load_marks(db_path, args.since),
        args.since,
    ):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
