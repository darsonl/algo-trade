"""The recorder writes, and above all it never raises.

A research instrument that can abort a scan is a liability, not an instrument.
Same rule as the ops-alert outbox: neither send nor drain may raise, because
both run inside scans that must not be aborted by their own reporting.
"""
import sqlite3
import sys

from config import Config
from database import queries
from database.models import initialize_db
from research import shadow_log


def _config(tmp_path):
    c = Config()
    c.db_path = str(tmp_path / "s.db")
    initialize_db(c.db_path)
    return c


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM shadow_observations").fetchall()


def test_observe_writes_one_row(tmp_path):
    cfg = _config(tmp_path)
    rid = shadow_log.observe(
        cfg, "AAPL", "stock", "fundamental", "rejected_fundamental",
        fundamentals={"trailingPE": 99.0},
    )
    rows = _rows(cfg.db_path)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["outcome"] == "rejected_fundamental"
    assert rid == rows[0]["id"]


def test_observe_stamps_the_market_session_date_not_the_local_date(tmp_path):
    """20:04 ET on the 20th is the 20th's session even though it is the 21st in
    Taipei, where this host lives. Bare local dates split one US session across
    two buckets -- the bug market_time.py exists to prevent."""
    from datetime import datetime, timezone as _tz
    cfg = _config(tmp_path)
    instant = datetime(2026, 8, 21, 0, 4, tzinfo=_tz.utc)  # 20:04 ET on the 20th
    shadow_log.observe(cfg, "AAPL", "stock", "universe",
                       "skipped_open_position", instant=instant)
    assert _rows(cfg.db_path)[0]["session_date"] == "2026-08-20"


def test_a_write_failure_does_not_raise(tmp_path):
    """The load-bearing property. A scan must survive its own recorder."""
    cfg = _config(tmp_path)
    cfg.db_path = str(tmp_path / "does" / "not" / "exist.db")
    result = shadow_log.observe(cfg, "AAPL", "stock", "universe",
                                "skipped_open_position")
    assert result is None  # reported as "not recorded", not as an exception


def test_an_invalid_stage_does_not_raise_either(tmp_path):
    """build_observation raises on a bad stage by design; observe must absorb
    it. A typo in a call site must not take the scan down with it."""
    cfg = _config(tmp_path)
    assert shadow_log.observe(cfg, "AAPL", "stock", "nonsense", "recommended") is None
    assert _rows(cfg.db_path) == []


def test_human_action_is_attached_to_the_observation(tmp_path):
    cfg = _config(tmp_path)
    shadow_log.observe(cfg, "AAPL", "stock", "recommended", "recommended",
                       recommendation_id=42)
    queries.set_shadow_human_action(cfg.db_path, 42, "approved",
                                    "2026-08-20T14:00:00Z")
    row = _rows(cfg.db_path)[0]
    assert row["human_action"] == "approved"
    assert row["human_action_at"] == "2026-08-20T14:00:00Z"


def test_human_action_for_an_unknown_recommendation_is_a_noop(tmp_path):
    cfg = _config(tmp_path)
    queries.set_shadow_human_action(cfg.db_path, 999, "approved",
                                    "2026-08-20T14:00:00Z")
    assert _rows(cfg.db_path) == []


# --- the contract holds for EVERY line, including the local imports ---
#
# `record` and `observe` import lazily to keep this module importable without
# the database package. Those imports used to sit ABOVE their try blocks, so a
# raising import escaped and broke the never-raises contract from the one line
# not covered by it. Unreachable in production (main.py imports both at module
# scope, so they are cached and a cached import cannot raise) -- which is
# exactly why it needed a test: the guarantee was being inherited from the
# caller rather than held by the function.
#
# Setting a module to None in sys.modules is what makes a cached import raise:
# CPython treats a None entry as a poisoned module and raises ImportError.

def test_observe_never_raises_when_its_own_import_fails(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    monkeypatch.setitem(sys.modules, "market_time", None)
    assert shadow_log.observe(cfg, "AAPL", "stock", "universe", "error") is None


def test_record_never_raises_when_its_own_import_fails(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    obs = shadow_log.build_observation(
        "AAPL", "stock", "universe", "error",
        session_date="2026-08-20", observed_at="2026-08-20T13:45:00Z")
    monkeypatch.setitem(sys.modules, "database", None)
    assert shadow_log.record(cfg, obs) is None
