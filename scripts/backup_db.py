"""Take a safe, rotated snapshot of the database.

WHY NOT `cp`. The database runs in WAL mode (`get_connection` sets
`journal_mode=WAL`), so a committed transaction lives in the `-wal` sidecar
until something checkpoints it. Copying the `.db` file alone can therefore omit
committed rows -- producing a backup that opens cleanly, passes an integrity
check, and is quietly missing data. Copying all three files while a writer is
active can tear them against each other instead.

`sqlite3.Connection.backup()` is the supported answer: it reads through the WAL,
takes a transactionally consistent snapshot, and does not modify the source.

WHY ROTATION IS NOT OPTIONAL. Seven ad-hoc `.bak` files accumulated in the repo
root over three days of this project, and one of them slipped past `.gitignore`
and was committed. A backup helper without a cap just rebuilds that pile.

WHAT IS ACTUALLY AT RISK: the shadow log. `.info` is a snapshot with no history,
so a lost observation cannot be re-fetched at any price -- unlike positions or
orders, which the broker can still tell us about.

    # snapshot the configured database into ./backups, keeping the newest 5
    .venv/Scripts/python.exe scripts/backup_db.py
    # keep more, or fewer
    .venv/Scripts/python.exe scripts/backup_db.py --keep 20
    # somewhere else
    .venv/Scripts/python.exe scripts/backup_db.py --dir D:/safe --keep 3
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402

DEFAULT_DIR = "backups"
DEFAULT_KEEP = 5
SUFFIX = ".bak"


def backup(db_path: str, dest_dir: str, stamp: str | None = None) -> str:
    """Snapshot `db_path` into `dest_dir`. Returns the backup's path.

    Uses the sqlite backup API rather than a file copy, so data still sitting in
    the write-ahead log is included and the source is never modified.

    The timestamp is `%Y%m%d-%H%M%S` because `rotate` orders by NAME, and that
    format is the one that sorts the same way it runs. A locale-formatted date
    would rotate whichever file happened to sort oldest.
    """
    src = Path(db_path)
    out = Path(dest_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")

    # NEVER clobber an existing backup. `sqlite3.connect(dest)` on an existing
    # path just opens it, so two runs in the same second silently overwrote each
    # other -- found on the first real run. A backup tool that can destroy a
    # previous backup is worse than no backup tool.
    # The separator is `_` (0x5F), which sorts AFTER `.` (0x2E). With `-`
    # (0x2D) the suffixed name sorted BEFORE the plain one, so rotation -- which
    # orders by name -- would have deleted the newer backup first, inverting the
    # policy it exists to enforce.
    dest = out / f"{src.name}.{stamp}{SUFFIX}"
    serial = 1
    while dest.exists():
        dest = out / f"{src.name}.{stamp}_{serial:02d}{SUFFIX}"
        serial += 1

    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
            # The copy inherits journal_mode=WAL and then grows `-wal`/`-shm`
            # beside it. `rotate` matches only `*.bak`, so those would be
            # orphaned the moment their backup is rotated away. A snapshot has
            # no concurrent writers and no use for a WAL.
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
    finally:
        source.close()
    return str(dest)


def verify(path: str) -> bool:
    """Whether the file at `path` is a readable, structurally sound database.

    A backup nobody checked is a guess. Cheap here, and the one moment the
    answer still helps.
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def rotate(dest_dir: str, keep: int = DEFAULT_KEEP) -> list[str]:
    """Delete all but the newest `keep` backups. Returns what was removed.

    Matches only this tool's own `*.bak` files, never the directory listing --
    rotation that deleted by listing would eat whatever else lives there.

    `keep` is compared explicitly rather than tested for truthiness, so 0 means
    zero rather than "falsy, use the default" -- the same trap `screen_price`
    guards against with NaN and False.
    """
    out = Path(dest_dir)
    if not out.is_dir():
        return []
    backups = sorted(p for p in out.glob(f"*{SUFFIX}") if p.is_file())
    doomed = backups[:-keep] if keep > 0 else backups
    for path in doomed:
        path.unlink()
        # Sidecars belonging to backups written before WAL was switched off.
        # Left behind they leak two files per rotation, forever.
        for sidecar in ("-wal", "-shm"):
            stray = path.with_name(path.name + sidecar)
            if stray.exists():
                stray.unlink()
    return [str(p) for p in doomed]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Snapshot the database, safely.")
    ap.add_argument("--db", default=None,
                    help="database (default: the configured one)")
    ap.add_argument("--dir", default=DEFAULT_DIR,
                    help=f"where backups go (default: {DEFAULT_DIR}/)")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                    help=f"how many to retain (default: {DEFAULT_KEEP})")
    args = ap.parse_args(argv)

    # Refused rather than interpreted. `--keep -1` most plausibly means
    # "unlimited", while the rotation arithmetic reads it as "retain nothing"
    # and deletes every backup -- the exact opposite. Neither guess is safe.
    if args.keep < 0:
        print(f"--keep must be 0 or more (got {args.keep}).")
        print("Use a large number for 'effectively unlimited'; 0 keeps none.")
        return 1

    db_path = args.db or Config().db_path
    if not Path(db_path).exists():
        print(f"No database at {db_path}.")
        return 1

    dest = backup(db_path, args.dir)
    if not verify(dest):
        # Left on disk on purpose: a backup that failed its check is evidence,
        # and deleting it would hide that the snapshot path is broken.
        print(f"Backup written to {dest} but FAILED its integrity check.")
        print("Do not rely on it. The source database was not modified.")
        return 1
    print(f"Backed up to {dest}")

    # AFTER the write, never before: rotating first could delete the newest
    # backup to make room and then fail to produce its replacement.
    for path in rotate(args.dir, args.keep):
        print(f"  rotated out {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
