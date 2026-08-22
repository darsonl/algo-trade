"""Forward marks: what the price actually did after each observation."""
import sqlite3
from datetime import datetime, timezone

import pytest

from config import Config
from database import queries
from database.models import initialize_db
from research import outcomes


def _config(tmp_path):
    c = Config()
    c.db_path = str(tmp_path / "s.db")
    initialize_db(c.db_path)
    return c


def _observe(cfg, ticker, session_date, price):
    conn = sqlite3.connect(cfg.db_path)
    conn.execute(
        "INSERT INTO shadow_observations (session_date, observed_at, ticker,"
        " scan_kind, stage_reached, outcome, reference_price)"
        " VALUES (?,?,?,'stock','recommended','recommended',?)",
        (session_date, session_date + "T13:45:00Z", ticker, price),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _fake_closes(mapping, calls=None):
    """A `_close_on_or_before` stub reading from a {(ticker, as_of): close} map."""
    def _close(ticker, as_of):
        if calls is not None:
            calls.append((ticker, as_of))
        return mapping.get((ticker, as_of))
    return _close


def _outcome_rows(cfg):
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM shadow_outcomes").fetchall()


# --- the pure part ---

def test_compute_return_is_a_percentage():
    assert outcomes.compute_return(100.0, 110.0) == pytest.approx(10.0)
    assert outcomes.compute_return(100.0, 90.0) == pytest.approx(-10.0)


def test_compute_return_on_zero_entry_returns_none():
    """A zero or missing entry price cannot yield a return. Returning 0.0 would
    silently enter a fake flat trade into the sample."""
    assert outcomes.compute_return(0.0, 110.0) is None
    assert outcomes.compute_return(None, 110.0) is None


# --- what is due, and what is not ---

def test_only_matured_horizons_are_due(tmp_path):
    """A 1-week mark is not due after two days. Marking early would record a
    two-day return in the one-week column.

    NOTE the argument is a CUTOFF (`now - horizon_days`), not `now`. Evaluated
    on 2026-08-22 the 1w cutoff is 2026-08-15, and a 2026-08-20 session has not
    matured; evaluated on 2026-08-28 the cutoff is 2026-08-21, and it has.
    """
    cfg = _config(tmp_path)
    _observe(cfg, "AAPL", "2026-08-20", 100.0)
    # as if now = 2026-08-22  ->  cutoff = 2026-08-15
    assert queries.pending_shadow_marks(cfg.db_path, "1w", "2026-08-15") == []
    # as if now = 2026-08-28  ->  cutoff = 2026-08-21
    assert len(queries.pending_shadow_marks(cfg.db_path, "1w", "2026-08-21")) == 1


def test_an_already_marked_horizon_is_not_due_again(tmp_path):
    cfg = _config(tmp_path)
    oid = _observe(cfg, "AAPL", "2026-08-20", 100.0)
    queries.record_shadow_outcome(cfg.db_path, oid, "1w", "2026-08-27",
                                  110.0, 10.0, 500.0, 1.0)
    # cutoff 2026-08-21 WOULD match on date; the recorded mark is what excludes it
    assert queries.pending_shadow_marks(cfg.db_path, "1w", "2026-08-21") == []


def test_an_observation_with_no_reference_price_is_never_due(tmp_path):
    """Only candidates that reached the technical stage carry a reference price,
    so only those can be marked. A NULL entry would make every return NULL."""
    cfg = _config(tmp_path)
    _observe(cfg, "AAPL", "2026-08-20", None)
    assert queries.pending_shadow_marks(cfg.db_path, "1w", "2026-08-21") == []


def test_recording_the_same_horizon_twice_does_not_duplicate(tmp_path):
    cfg = _config(tmp_path)
    oid = _observe(cfg, "AAPL", "2026-08-20", 100.0)
    queries.record_shadow_outcome(cfg.db_path, oid, "1w", "2026-08-27",
                                  110.0, 10.0, 500.0, 1.0)
    queries.record_shadow_outcome(cfg.db_path, oid, "1w", "2026-08-27",
                                  111.0, 11.0, 500.0, 1.0)
    conn = sqlite3.connect(cfg.db_path)
    assert conn.execute("SELECT COUNT(*) FROM shadow_outcomes").fetchone()[0] == 1


# --- marking ---

@pytest.mark.asyncio
async def test_a_mark_carries_both_the_ticker_and_the_benchmark_over_one_window(
        tmp_path, monkeypatch):
    """Absolute return alone mostly measures the market, so every mark carries
    SPY over the IDENTICAL window -- entry at the observation's session date,
    exit at the horizon date."""
    cfg = _config(tmp_path)
    _observe(cfg, "AAPL", "2026-08-20", 100.0)
    monkeypatch.setattr(outcomes, "_close_on_or_before", _fake_closes({
        ("AAPL", "2026-08-27"): 110.0,
        ("SPY", "2026-08-20"): 500.0,
        ("SPY", "2026-08-27"): 505.0,
    }))

    n = await outcomes.mark_due_outcomes(
        cfg, instant=datetime(2026, 8, 28, tzinfo=timezone.utc))

    assert n == 1
    rows = _outcome_rows(cfg)
    assert len(rows) == 1
    assert rows[0]["horizon"] == "1w"
    assert rows[0]["as_of"] == "2026-08-27"
    assert rows[0]["price"] == pytest.approx(110.0)
    assert rows[0]["return_pct"] == pytest.approx(10.0)
    assert rows[0]["benchmark_price"] == pytest.approx(505.0)
    assert rows[0]["benchmark_return_pct"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_the_benchmark_is_fetched_once_per_date_not_once_per_ticker(
        tmp_path, monkeypatch):
    """Every observation on a session date shares one SPY entry and one SPY
    exit. Fetching them inside the per-row loop multiplies the network cost of
    a scan by the size of the universe for no additional information."""
    cfg = _config(tmp_path)
    for ticker in ("AAPL", "MSFT", "XOM"):
        _observe(cfg, ticker, "2026-08-20", 100.0)
    calls = []
    monkeypatch.setattr(outcomes, "_close_on_or_before", _fake_closes({
        ("AAPL", "2026-08-27"): 110.0,
        ("MSFT", "2026-08-27"): 120.0,
        ("XOM", "2026-08-27"): 90.0,
        ("SPY", "2026-08-20"): 500.0,
        ("SPY", "2026-08-27"): 505.0,
    }, calls=calls))

    await outcomes.mark_due_outcomes(
        cfg, instant=datetime(2026, 8, 28, tzinfo=timezone.utc))

    assert calls.count(("SPY", "2026-08-20")) == 1
    assert calls.count(("SPY", "2026-08-27")) == 1
    assert len(_outcome_rows(cfg)) == 3


@pytest.mark.asyncio
async def test_an_unavailable_price_leaves_the_horizon_pending(tmp_path, monkeypatch):
    """A price we could not read is NOT a mark.

    Recording it anyway writes a NULL row, and `pending_shadow_marks` excludes
    anything with a row -- so a single transient yfinance outage would convert
    itself into permanently missing data with no way to retry. Same rule as the
    order sweep: a terminal status is not permission to book a zero fill.
    """
    cfg = _config(tmp_path)
    _observe(cfg, "AAPL", "2026-08-20", 100.0)
    monkeypatch.setattr(outcomes, "_close_on_or_before", _fake_closes({
        ("SPY", "2026-08-20"): 500.0,
        ("SPY", "2026-08-27"): 505.0,
    }))  # AAPL absent -> None

    n = await outcomes.mark_due_outcomes(
        cfg, instant=datetime(2026, 8, 28, tzinfo=timezone.utc))

    assert n == 0
    assert _outcome_rows(cfg) == []
    # and it is still due, so a later scan can pick it up
    assert len(queries.pending_shadow_marks(cfg.db_path, "1w", "2026-08-21")) == 1


@pytest.mark.asyncio
async def test_one_failing_ticker_does_not_stop_the_others(tmp_path, monkeypatch):
    """Per-observation failure, like the terminal-order sweep. A delisted symbol
    must not cost every other symbol its mark."""
    cfg = _config(tmp_path)
    _observe(cfg, "DEAD", "2026-08-20", 100.0)
    _observe(cfg, "AAPL", "2026-08-20", 100.0)

    prices = {
        ("AAPL", "2026-08-27"): 110.0,
        ("SPY", "2026-08-20"): 500.0,
        ("SPY", "2026-08-27"): 505.0,
    }

    def _close(ticker, as_of):
        if ticker == "DEAD":
            raise RuntimeError("delisted")
        return prices.get((ticker, as_of))

    monkeypatch.setattr(outcomes, "_close_on_or_before", _close)

    n = await outcomes.mark_due_outcomes(
        cfg, instant=datetime(2026, 8, 28, tzinfo=timezone.utc))

    assert n == 1
    assert [r["price"] for r in _outcome_rows(cfg)] == [pytest.approx(110.0)]


@pytest.mark.asyncio
async def test_a_price_fetch_failure_does_not_raise(tmp_path, monkeypatch):
    """Same contract as the recorder: this runs inside a scan."""
    cfg = _config(tmp_path)
    _observe(cfg, "AAPL", "2026-08-20", 100.0)

    def _boom(*a, **kw):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(outcomes, "_close_on_or_before", _boom)
    n = await outcomes.mark_due_outcomes(
        cfg, instant=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert n == 0  # nothing marked, nothing raised


@pytest.mark.asyncio
async def test_a_broken_database_does_not_raise(tmp_path):
    """The query itself can fail. `mark_due_outcomes` still returns a count."""
    cfg = _config(tmp_path)
    cfg.db_path = str(tmp_path / "nonexistent" / "s.db")
    assert await outcomes.mark_due_outcomes(cfg) == 0
