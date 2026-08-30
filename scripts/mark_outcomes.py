"""Take the forward marks that have matured, outside a scan.

`mark_due_outcomes` normally runs at the FRONT of `run_scan`, before the
universe is built. That placement is why it is bounded: `MAX_MARKS_PER_RUN` and
`MARKING_TIME_BUDGET_S` exist so a backlog cannot delay a market-timed scan.

Run by hand neither reason applies, and the bound becomes a hazard rather than a
safeguard -- a run that stops at the cap and says nothing looks exactly like a
run that finished. So this tool reports what was still due when it stopped, and
`--max` / `--budget` let an operator drain a backlog the scan would leave.

WHY A SCRIPT AND NOT A SLASH COMMAND: taking marks requires no Discord, no
Schwab, and no bot. The whole point is to collect research data on a machine
that is not running the bot -- which, given scans land at 09:45 and 15:30 ET,
is the usual case here.

UNLIKE `backfill_screen_price.py`, THIS DEFAULTS TO DOING THE WORK. The backfill
rewrites existing rows, so it defaults to preview and demands `--apply`. Marking
only ever appends, is idempotent (`INSERT OR IGNORE`, and `pending_shadow_marks`
excludes anything already marked), and is what the scheduler does unattended on
every scan. Requiring a flag for the ordinary case would be ceremony, not safety.

    # take every matured mark
    .venv/Scripts/python.exe scripts/mark_outcomes.py
    # just say what is due -- no network, no writes
    .venv/Scripts/python.exe scripts/mark_outcomes.py --dry-run
    # drain a backlog the scan's bounds would leave
    .venv/Scripts/python.exe scripts/mark_outcomes.py --max 2000 --budget 3600
    # against a copy
    .venv/Scripts/python.exe scripts/mark_outcomes.py --db copy.db
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402
from database import queries  # noqa: E402
from research import outcomes  # noqa: E402

SHADOW_TABLES = ("shadow_observations", "shadow_outcomes")


def utc(day: str) -> datetime:
    """Midnight UTC on `day` (YYYY-MM-DD). For pinning the clock in tests."""
    return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def shadow_tables_present(db_path: str) -> bool:
    """Whether this database has the shadow log at all.

    A database created before the shadow log shipped has neither table, and the
    queries below then die with `no such table` -- a traceback for the ordinary
    case of running this before the bot has restarted once. Checked rather than
    created: `initialize_db` owns the schema and runs at startup.
    """
    conn = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    return set(SHADOW_TABLES) <= names


def due_by_horizon(db_path: str, instant=None) -> dict[str, int]:
    """How many marks each horizon is waiting on, without fetching anything.

    Per horizon rather than one total, because they mature at different times
    and a single number would hide which column is actually ripe.

    This asks the SAME query the marking job uses, so "due" here means exactly
    what it means there -- including `reference_price > 0`, which is the
    eligibility invariant and lives in the query rather than in the caller.
    """
    now = instant or datetime.now(timezone.utc)
    out = {}
    for horizon, days in outcomes.HORIZONS.items():
        cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        out[horizon] = len(queries.pending_shadow_marks(db_path, horizon, cutoff))
    return out


def render_due(due: dict[str, int]) -> list[str]:
    """The due table as lines. Pure, so it can be tested without printing."""
    lines = [f"marks due: {sum(due.values())}"]
    lines += [f"  {h:3} {n:6}" for h, n in due.items()]
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Take matured forward marks for shadow observations.")
    ap.add_argument("--db", default=None,
                    help="database (default: the configured one)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what is due; fetch nothing, write nothing")
    ap.add_argument("--max", type=int, default=None,
                    help=f"marks per run (default {outcomes.MAX_MARKS_PER_RUN}, "
                         "sized for running in front of a scan)")
    ap.add_argument("--budget", type=float, default=None,
                    help=f"seconds (default {outcomes.MARKING_TIME_BUDGET_S})")
    ap.add_argument("--now", default=None,
                    help="pin the clock, YYYY-MM-DD (testing)")
    args = ap.parse_args(argv)

    db_path = args.db or Config().db_path
    if not Path(db_path).exists():
        print(f"No database at {db_path}.")
        return 1
    if not shadow_tables_present(db_path):
        print(f"The database at {db_path} has no shadow log tables.")
        print("Start the bot once so initialize_db can create them, then re-run.")
        return 1

    instant = utc(args.now) if args.now else None
    due = due_by_horizon(db_path, instant)
    for line in render_due(due):
        print(line)

    if not sum(due.values()):
        print("\nNothing to do.")
        return 0
    if args.dry_run:
        print("\nDry run: nothing fetched, nothing written.")
        return 0

    config = Config()
    config.db_path = db_path

    # The overrides are module globals because that is how `mark_due_outcomes`
    # reads them. Restored in `finally` so an override cannot leak into a scan's
    # marking later in the same process.
    saved = (outcomes.MAX_MARKS_PER_RUN, outcomes.MARKING_TIME_BUDGET_S)
    if args.max is not None:
        outcomes.MAX_MARKS_PER_RUN = args.max
    if args.budget is not None:
        outcomes.MARKING_TIME_BUDGET_S = args.budget
    try:
        marked = asyncio.run(outcomes.mark_due_outcomes(config, instant=instant))
    finally:
        outcomes.MAX_MARKS_PER_RUN, outcomes.MARKING_TIME_BUDGET_S = saved

    print(f"\nMarked {marked}.")

    # Re-asked, not inferred from the count. A mark can also be skipped because
    # its price could not be read, and those rows stay due too -- reporting only
    # the cap would call that a clean finish.
    left = sum(due_by_horizon(db_path, instant).values())
    if left:
        print(f"{left} still due -- re-run to continue.")
        print("(bounded by --max/--budget, or a price that could not be read)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
