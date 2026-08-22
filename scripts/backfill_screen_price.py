"""Backfill `reference_price` for shadow rows recorded before the screen-price policy.

Rows written by the first version of the shadow log carried a price only at the
four post-technical exits, and that price came from `closes.iloc[-1]` -- a
different endpoint from `.info`, fetched minutes later, and auto-adjusted. Rows
that exited at the fundamental gate or on quota exhaustion carried no price at
all and could never be marked.

Every such row still stores the `.info` dict it was judged on, so the correct
price can be recovered without a network call. This applies the SAME
`screen_price` policy the live path uses, so backfilled and live rows are
comparable, and records `reference_price_source` with a `backfill:` prefix so
they stay distinguishable.

WHY THIS IS A SCRIPT AND NOT A STARTUP MIGRATION: `initialize_db` runs before
the bot can start, and CLAUDE.md is explicit that an operator who cannot start
the bot cannot `/halt` it either. Parsing research JSON on every row is exactly
the kind of work that can fail on malformed data, and research instrumentation
must not hold that power over startup.

    # look, change nothing (default)
    .venv/Scripts/python.exe scripts/backfill_screen_price.py
    # apply
    .venv/Scripts/python.exe scripts/backfill_screen_price.py --apply
    # against a copy
    .venv/Scripts/python.exe scripts/backfill_screen_price.py --db copy.db --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402
from screener.fundamentals import screen_price  # noqa: E402

# Rows that got far enough to have an `.info` dict. Universe-level skips fire
# before any data is fetched, so they have nothing to recover and are excluded
# for the same reason the live path does not price them.
POST_INFO_OUTCOMES = (
    "rejected_fundamental",
    "skipped_quota_exhausted",
    "rejected_signal",
    "rejected_technical",
    "recommended",
)


def plan_backfill(rows) -> tuple[list[tuple], Counter]:
    """Decide what each row should become. Pure: no database, no clock.

    Returns `(updates, tally)` where updates are `(id, price, source)`.
    A row is only rewritten when a price can actually be recovered -- an
    unreadable row is counted and left exactly as it is, never zeroed.
    """
    updates, tally = [], Counter()
    for row in rows:
        raw = row["fundamentals_json"]
        if not raw:
            tally["no_fundamentals_json"] += 1
            continue
        try:
            info = json.loads(raw)
        except Exception:
            tally["malformed_json"] += 1
            continue
        if not isinstance(info, dict):
            tally["malformed_json"] += 1
            continue
        price, source = screen_price(info)
        if price is None:
            tally["no_usable_price"] += 1
            continue
        if (row["reference_price"] == price
                and row["reference_price_source"] == f"backfill:{source}"):
            tally["already_done"] += 1
            continue
        updates.append((row["id"], price, f"backfill:{source}"))
        tally["to_update"] += 1
    return updates, tally


def has_provenance_column(db_path: str) -> bool:
    """Whether the schema is new enough for this tool.

    A database that has not been opened by `initialize_db` since the
    screen-price change has no `reference_price_source`, and every query below
    dies with `no such column`. Checked rather than created: adding the column
    is `initialize_db`'s job and happens at startup, and a data tool that
    silently alters schema is a tool nobody can reason about.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(shadow_observations)")}
    finally:
        conn.close()
    return "reference_price_source" in cols


def _load(db_path: str) -> list:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(POST_INFO_OUTCOMES))
    return conn.execute(
        f"""SELECT id, ticker, outcome, reference_price, reference_price_source,
                   fundamentals_json
              FROM shadow_observations
             WHERE outcome IN ({placeholders})
             ORDER BY id""",
        POST_INFO_OUTCOMES,
    ).fetchall()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backfill shadow reference prices.")
    ap.add_argument("--db", default=None, help="database (default: the configured one)")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default: preview only)")
    args = ap.parse_args(argv)

    db_path = args.db or Config().db_path
    if not Path(db_path).exists():
        print(f"No database at {db_path}.")
        return 1

    if not has_provenance_column(db_path):
        print(f"The database at {db_path} predates the screen-price change")
        print("(no `reference_price_source` column). Start the bot once so")
        print("initialize_db can add it, then re-run this tool.")
        return 1

    rows = _load(db_path)
    updates, tally = plan_backfill(rows)

    print(f"post-info shadow rows examined: {len(rows)}")
    for key in ("to_update", "already_done", "no_usable_price",
                "no_fundamentals_json", "malformed_json"):
        print(f"  {key:24} {tally[key]:5}")

    if not updates:
        print("\nNothing to do.")
        return 0

    if not args.apply:
        print(f"\nPREVIEW ONLY. Re-run with --apply to write {len(updates)} row(s).")
        print("Back up the database first; this rewrites research data in place.")
        return 0

    conn = sqlite3.connect(db_path)
    with conn:
        conn.executemany(
            "UPDATE shadow_observations"
            "   SET reference_price = ?, reference_price_source = ?"
            " WHERE id = ?",
            [(price, source, rid) for rid, price, source in updates],
        )
    conn.close()
    print(f"\nUpdated {len(updates)} row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
