"""Safe, rotated database backups.

Two things this has to get right, and `cp` gets neither:

  WAL. The database runs in WAL mode, so a committed transaction lives in the
  `-wal` sidecar until it is checkpointed. Copying the `.db` file alone can
  therefore silently omit committed data -- a backup that looks fine, opens
  fine, and is missing rows. Every backup taken by hand in this project before
  this script used `cp`.

  ROTATION. An unbounded helper recreates the seven-file pile it was written to
  replace, and one of those got itself committed to git.

The shadow log is the thing actually worth protecting: `.info` is a snapshot
with no history, so a lost observation cannot be re-fetched at any price.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "backup_db",
    Path(__file__).resolve().parent.parent / "scripts" / "backup_db.py",
)
backup_db = importlib.util.module_from_spec(_SPEC)
sys.modules["backup_db"] = backup_db
_SPEC.loader.exec_module(backup_db)


def _wal_db(path, rows=3, checkpoint=False):
    """A WAL database with `rows` committed but deliberately NOT checkpointed.

    That is the state a running bot leaves the file in, and the state in which
    a file copy loses data.
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(rows)])
    conn.commit()
    if checkpoint:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return conn  # left OPEN, so the WAL is not checkpointed on close


def _count(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        conn.close()


# --- the WAL problem, which is the whole reason this is not `cp` ---

def test_a_backup_contains_data_still_sitting_in_the_wal(tmp_path):
    """THE test. A `cp` implementation fails this: the rows are committed but
    live in the -wal sidecar, so the .db file alone does not have them yet."""
    src = tmp_path / "live.db"
    conn = _wal_db(src, rows=5)
    try:
        dest = backup_db.backup(str(src), str(tmp_path / "out"))
        assert _count(dest) == 5, "committed-but-uncheckpointed rows were lost"
    finally:
        conn.close()


def test_the_backup_is_a_sound_database_not_just_a_file(tmp_path):
    src = tmp_path / "live.db"
    conn = _wal_db(src)
    try:
        dest = backup_db.backup(str(src), str(tmp_path / "out"))
    finally:
        conn.close()
    check = sqlite3.connect(dest)
    assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    check.close()


def test_the_source_is_left_untouched(tmp_path):
    """A backup that mutates the thing it is protecting is not a backup. The
    sqlite backup API is read-only on the source, and this pins that."""
    src = tmp_path / "live.db"
    conn = _wal_db(src, rows=4)
    try:
        backup_db.backup(str(src), str(tmp_path / "out"))
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 4
    finally:
        conn.close()


# --- rotation ---

def test_rotation_keeps_the_newest_and_deletes_the_rest(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    for stamp in ("20260101-000000", "20260102-000000", "20260103-000000"):
        (out / f"algo_trade.db.{stamp}.bak").write_text("x")

    removed = backup_db.rotate(str(out), keep=2)

    names = sorted(p.name for p in out.glob("*.bak"))
    assert len(names) == 2
    assert "algo_trade.db.20260101-000000.bak" not in names, "oldest must go"
    assert "algo_trade.db.20260103-000000.bak" in names, "newest must stay"
    assert len(removed) == 1


def test_rotation_removes_nothing_when_under_the_limit(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "algo_trade.db.20260101-000000.bak").write_text("x")

    assert backup_db.rotate(str(out), keep=5) == []
    assert len(list(out.glob("*.bak"))) == 1


def test_rotation_ignores_files_it_did_not_write(tmp_path):
    """Only this tool's own backups are candidates for deletion. A rotation that
    deleted by directory listing would eat whatever else lives there."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "algo_trade.db.20260101-000000.bak").write_text("x")
    (out / "important-notes.txt").write_text("keep me")

    backup_db.rotate(str(out), keep=0)

    assert (out / "important-notes.txt").exists()


def test_keep_zero_is_honoured_rather_than_treated_as_unset(tmp_path):
    """0 must mean zero, not "falsy, so use the default". The same `if not x`
    trap that `screen_price` guards against."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "algo_trade.db.20260101-000000.bak").write_text("x")

    backup_db.rotate(str(out), keep=0)

    assert list(out.glob("*.bak")) == []


# --- naming ---

def test_backup_names_sort_chronologically(tmp_path):
    """Rotation orders by NAME, so the timestamp has to sort the same way it
    runs. A locale-formatted date would rotate the wrong file."""
    src = tmp_path / "live.db"
    conn = _wal_db(src)
    try:
        a = backup_db.backup(str(src), str(tmp_path / "out"),
                             stamp="20260101-000000")
        b = backup_db.backup(str(src), str(tmp_path / "out"),
                             stamp="20260102-000000")
    finally:
        conn.close()
    assert Path(a).name < Path(b).name


def test_the_backup_keeps_the_source_database_name(tmp_path):
    src = tmp_path / "algo_trade.db"
    conn = _wal_db(src)
    try:
        dest = backup_db.backup(str(src), str(tmp_path / "out"),
                                stamp="20260101-000000")
    finally:
        conn.close()
    assert Path(dest).name == "algo_trade.db.20260101-000000.bak"


# --- the CLI ---

def test_cli_reports_the_backup_and_returns_zero(tmp_path, capsys):
    src = tmp_path / "live.db"
    conn = _wal_db(src)
    try:
        rc = backup_db.main(["--db", str(src), "--dir", str(tmp_path / "out")])
    finally:
        conn.close()
    assert rc == 0
    assert "backed up" in capsys.readouterr().out.lower()


def test_cli_on_a_missing_database_is_an_error_not_a_traceback(tmp_path, capsys):
    rc = backup_db.main(["--db", str(tmp_path / "nope.db")])
    assert rc == 1
    assert "no database" in capsys.readouterr().out.lower()


def test_cli_rotates_after_writing_not_before(tmp_path):
    """Rotating first could delete the newest backup to make room and then fail
    to write the replacement, leaving fewer backups than requested."""
    src = tmp_path / "live.db"
    out = tmp_path / "out"
    out.mkdir()
    (out / "algo_trade.db.20260101-000000.bak").write_text("x")
    conn = _wal_db(src)
    try:
        backup_db.main(["--db", str(src), "--dir", str(out), "--keep", "1"])
    finally:
        conn.close()

    remaining = list(out.glob("*.bak"))
    assert len(remaining) == 1
    assert remaining[0].name != "algo_trade.db.20260101-000000.bak", \
        "the surviving file must be the NEW backup, not the stale one"


# --- edges the mutation run exposed ---

def test_a_negative_keep_is_refused_rather_than_deleting_everything(tmp_path, capsys):
    """`--keep -1` most plausibly means "unlimited"; the rotation arithmetic
    would read it as "retain nothing" and delete every backup. Refuse it rather
    than pick either meaning."""
    src = tmp_path / "live.db"
    out = tmp_path / "out"
    out.mkdir()
    (out / "algo_trade.db.20260101-000000.bak").write_text("x")
    conn = _wal_db(src)
    try:
        rc = backup_db.main(["--db", str(src), "--dir", str(out), "--keep", "-1"])
    finally:
        conn.close()

    assert rc == 1
    assert (out / "algo_trade.db.20260101-000000.bak").exists(), "nothing deleted"
    assert "keep" in capsys.readouterr().out.lower()


def test_a_corrupt_backup_is_reported_and_not_silently_trusted(tmp_path, capsys,
                                                               monkeypatch):
    """The one moment checking still helps. A backup nobody verified is a guess,
    and this path is what turns a broken snapshot into a visible failure."""
    src = tmp_path / "live.db"
    conn = _wal_db(src)
    try:
        monkeypatch.setattr(backup_db, "verify", lambda _p: False)
        rc = backup_db.main(["--db", str(src), "--dir", str(tmp_path / "out")])
    finally:
        conn.close()

    assert rc == 1
    out = capsys.readouterr().out.lower()
    assert "integrity" in out
    assert "not modified" in out, "must say the SOURCE is unharmed"


def test_verify_rejects_a_file_that_is_not_a_database(tmp_path):
    junk = tmp_path / "junk.bak"
    junk.write_text("this is not a database")
    assert backup_db.verify(str(junk)) is False


def test_a_failed_verification_does_not_rotate(tmp_path, monkeypatch):
    """Rotation on a bad snapshot would delete a GOOD older backup to make room
    for one that just failed its check."""
    src = tmp_path / "live.db"
    out = tmp_path / "out"
    out.mkdir()
    (out / "algo_trade.db.20260101-000000.bak").write_text("x")
    conn = _wal_db(src)
    try:
        monkeypatch.setattr(backup_db, "verify", lambda _p: False)
        backup_db.main(["--db", str(src), "--dir", str(out), "--keep", "1"])
    finally:
        conn.close()

    assert (out / "algo_trade.db.20260101-000000.bak").exists(), \
        "the good older backup must survive a failed snapshot"


# --- found by running it for real, not by any fixture ---

def test_two_backups_in_the_same_second_do_not_clobber_each_other(tmp_path):
    """Found in production on the first run. `sqlite3.connect(dest)` on an
    existing path simply OPENS it, so a same-second second run overwrote the
    first backup and rotation then counted two files where one existed. A
    backup tool that can destroy a previous backup is worse than none."""
    src = tmp_path / "live.db"
    out = tmp_path / "out"
    conn = _wal_db(src)
    try:
        a = backup_db.backup(str(src), str(out), stamp="20260101-000000")
        b = backup_db.backup(str(src), str(out), stamp="20260101-000000")
    finally:
        conn.close()

    assert a != b, "the second backup must not reuse the first one's path"
    assert Path(a).exists() and Path(b).exists()
    assert len(list(out.glob("*.bak"))) == 2


def test_a_backup_carries_no_wal_sidecars(tmp_path):
    """The copy inherits journal_mode=WAL and grows `-wal`/`-shm` beside it.
    `rotate` matches only `*.bak`, so it deletes the backup and orphans the
    sidecars permanently."""
    src = tmp_path / "live.db"
    out = tmp_path / "out"
    conn = _wal_db(src)
    try:
        dest = backup_db.backup(str(src), str(out), stamp="20260101-000000")
        assert backup_db.verify(dest)
    finally:
        conn.close()

    strays = [p.name for p in out.iterdir() if not p.name.endswith(".bak")]
    assert strays == [], f"backup left sidecars behind: {strays}"


def test_rotation_removes_a_rotated_backups_sidecars_too(tmp_path):
    """Defensive, for backups written before the sidecars were suppressed.
    Otherwise every rotation leaks two files that nothing will ever clean up."""
    out = tmp_path / "out"
    out.mkdir()
    for stamp in ("20260101-000000", "20260102-000000"):
        (out / f"algo_trade.db.{stamp}.bak").write_text("x")
        (out / f"algo_trade.db.{stamp}.bak-wal").write_text("x")
        (out / f"algo_trade.db.{stamp}.bak-shm").write_text("x")

    backup_db.rotate(str(out), keep=1)

    assert not (out / "algo_trade.db.20260101-000000.bak-wal").exists()
    assert not (out / "algo_trade.db.20260101-000000.bak-shm").exists()
    assert (out / "algo_trade.db.20260102-000000.bak-wal").exists(), \
        "the surviving backup keeps its own sidecars"


def test_a_collision_suffix_sorts_after_the_plain_name(tmp_path):
    """Rotation orders by NAME, so a collision suffix that sorts EARLIER makes
    rotation delete the newer file first -- inverting the policy it exists to
    enforce. `-` is 0x2D and `.` is 0x2E, so a `-01` suffix sorted before the
    plain name; `_` (0x5F) sorts after."""
    src = tmp_path / "live.db"
    out = tmp_path / "out"
    conn = _wal_db(src)
    try:
        first = backup_db.backup(str(src), str(out), stamp="20260101-000000")
        second = backup_db.backup(str(src), str(out), stamp="20260101-000000")
    finally:
        conn.close()

    assert Path(first).name < Path(second).name, \
        "the later backup must sort later, or rotation deletes the wrong one"


def test_rotation_keeps_the_collision_suffixed_backup_as_the_newest(tmp_path):
    """The consequence, end to end: with keep=1 the survivor must be the second
    backup taken, not the first."""
    src = tmp_path / "live.db"
    out = tmp_path / "out"
    conn = _wal_db(src)
    try:
        first = backup_db.backup(str(src), str(out), stamp="20260101-000000")
        second = backup_db.backup(str(src), str(out), stamp="20260101-000000")
    finally:
        conn.close()

    backup_db.rotate(str(out), keep=1)

    assert not Path(first).exists()
    assert Path(second).exists(), "rotation deleted the NEWER backup"
