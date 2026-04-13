import pytest
import sqlite3
import os
from database.models import initialize_db
from database.queries import (
    create_recommendation,
    get_recommendation,
    update_recommendation_status,
    create_trade,
    get_pending_recommendations,
    expire_stale_recommendations,
    ticker_recommended_today,
)

DB_PATH = "test_algo_trade.db"


@pytest.fixture(autouse=True)
def fresh_db():
    initialize_db(DB_PATH)
    yield
    os.remove(DB_PATH)


def test_initialize_db_creates_tables():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()
    assert "recommendations" in tables
    assert "trades" in tables


def test_create_and_get_recommendation():
    rec_id = create_recommendation(
        db_path=DB_PATH,
        ticker="AAPL",
        signal="BUY",
        reasoning="Strong fundamentals and positive sentiment.",
        price=175.50,
        dividend_yield=0.005,
        pe_ratio=28.0,
    )
    rec = get_recommendation(DB_PATH, rec_id)
    assert rec["ticker"] == "AAPL"
    assert rec["signal"] == "BUY"
    assert rec["status"] == "pending"


def test_update_recommendation_status():
    rec_id = create_recommendation(
        db_path=DB_PATH,
        ticker="JNJ",
        signal="BUY",
        reasoning="Solid dividend history.",
        price=155.0,
        dividend_yield=0.03,
        pe_ratio=15.0,
    )
    update_recommendation_status(DB_PATH, rec_id, "approved")
    rec = get_recommendation(DB_PATH, rec_id)
    assert rec["status"] == "approved"


def test_create_trade():
    rec_id = create_recommendation(
        db_path=DB_PATH,
        ticker="VYM",
        signal="BUY",
        reasoning="High yield ETF.",
        price=110.0,
        dividend_yield=0.03,
        pe_ratio=None,
    )
    trade_id = create_trade(
        db_path=DB_PATH,
        recommendation_id=rec_id,
        ticker="VYM",
        shares=4.0,
        price=110.0,
        order_id="schwab-order-123",
    )
    assert trade_id > 0


def test_get_pending_recommendations():
    create_recommendation(
        db_path=DB_PATH,
        ticker="MSFT",
        signal="BUY",
        reasoning="Strong growth.",
        price=400.0,
        dividend_yield=0.007,
        pe_ratio=32.0,
    )
    pending = get_pending_recommendations(DB_PATH)
    assert len(pending) == 1
    assert pending[0]["ticker"] == "MSFT"


def test_expire_stale_recommendations():
    rec_id = create_recommendation(
        db_path=DB_PATH,
        ticker="T",
        signal="BUY",
        reasoning="High yield.",
        price=17.0,
        dividend_yield=0.06,
        pe_ratio=8.0,
    )
    # Force expiry by updating expires_at to the past
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE recommendations SET expires_at = datetime('now', '-1 hour') WHERE id = ?",
        (rec_id,),
    )
    conn.commit()
    conn.close()
    expire_stale_recommendations(DB_PATH)
    rec = get_recommendation(DB_PATH, rec_id)
    assert rec["status"] == "expired"


# --- ticker_recommended_today (TEST-07) ---

def test_ticker_recommended_today_true_for_fresh_pending_rec():
    create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY",
        reasoning="Fresh.", price=150.0, dividend_yield=0.03, pe_ratio=20.0,
    )
    assert ticker_recommended_today(DB_PATH, "AAPL") is True


def test_ticker_recommended_today_false_for_different_ticker():
    create_recommendation(
        db_path=DB_PATH, ticker="MSFT", signal="BUY",
        reasoning="Fresh.", price=400.0, dividend_yield=0.007, pe_ratio=30.0,
    )
    assert ticker_recommended_today(DB_PATH, "AAPL") is False


def test_ticker_recommended_today_false_when_no_recs_exist():
    assert ticker_recommended_today(DB_PATH, "NVDA") is False


def test_ticker_recommended_today_false_for_expired_status():
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="T", signal="BUY",
        reasoning=".", price=17.0, dividend_yield=0.06, pe_ratio=8.0,
    )
    update_recommendation_status(DB_PATH, rec_id, "expired")
    assert ticker_recommended_today(DB_PATH, "T") is False


def test_ticker_recommended_today_false_for_rejected_status():
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="VZ", signal="BUY",
        reasoning=".", price=40.0, dividend_yield=0.06, pe_ratio=9.0,
    )
    update_recommendation_status(DB_PATH, rec_id, "rejected")
    assert ticker_recommended_today(DB_PATH, "VZ") is False


def test_ticker_recommended_today_true_for_approved_status():
    """Approved recs still count - prevents re-recommending a bought ticker today."""
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="JNJ", signal="BUY",
        reasoning=".", price=155.0, dividend_yield=0.03, pe_ratio=15.0,
    )
    update_recommendation_status(DB_PATH, rec_id, "approved")
    assert ticker_recommended_today(DB_PATH, "JNJ") is True


def test_ticker_recommended_today_false_for_yesterday_utc_date():
    """UTC boundary: a record created yesterday (UTC) is not 'today'."""
    import sqlite3
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY",
        reasoning="Yesterday.", price=150.0, dividend_yield=0.03, pe_ratio=20.0,
    )
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE recommendations SET created_at = datetime('now', '-1 day') WHERE id = ?",
        (rec_id,),
    )
    conn.commit()
    conn.close()
    assert ticker_recommended_today(DB_PATH, "AAPL") is False


def test_ticker_recommended_today_true_for_today_utc_date():
    """UTC boundary: a record explicitly created today (UTC) is found."""
    import sqlite3
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY",
        reasoning="Today.", price=150.0, dividend_yield=0.03, pe_ratio=20.0,
    )
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE recommendations SET created_at = datetime('now') WHERE id = ?",
        (rec_id,),
    )
    conn.commit()
    conn.close()
    assert ticker_recommended_today(DB_PATH, "AAPL") is True


# --- expire_stale_recommendations edge cases (TEST-08) ---

def test_expire_stale_does_not_expire_fresh_rec():
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY",
        reasoning="Fresh.", price=150.0, dividend_yield=0.03, pe_ratio=20.0,
    )
    expire_stale_recommendations(DB_PATH)
    rec = get_recommendation(DB_PATH, rec_id)
    assert rec["status"] == "pending"


def test_expire_stale_does_not_touch_approved_rec():
    """Approved records must never be expired even when expires_at is in the past."""
    import sqlite3
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="MSFT", signal="BUY",
        reasoning=".", price=400.0, dividend_yield=0.007, pe_ratio=30.0,
    )
    update_recommendation_status(DB_PATH, rec_id, "approved")
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE recommendations SET expires_at = datetime('now', '-1 hour') WHERE id = ?",
        (rec_id,),
    )
    conn.commit()
    conn.close()
    expire_stale_recommendations(DB_PATH)
    assert get_recommendation(DB_PATH, rec_id)["status"] == "approved"


def test_expire_stale_does_not_touch_rejected_rec():
    """Rejected records must not be overwritten by expire."""
    import sqlite3
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="VZ", signal="BUY",
        reasoning=".", price=40.0, dividend_yield=0.06, pe_ratio=9.0,
    )
    update_recommendation_status(DB_PATH, rec_id, "rejected")
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE recommendations SET expires_at = datetime('now', '-1 hour') WHERE id = ?",
        (rec_id,),
    )
    conn.commit()
    conn.close()
    expire_stale_recommendations(DB_PATH)
    assert get_recommendation(DB_PATH, rec_id)["status"] == "rejected"


def test_expire_stale_expires_past_boundary():
    """A record 1 second past its expires_at is expired."""
    import sqlite3
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="GE", signal="BUY",
        reasoning=".", price=100.0, dividend_yield=0.02, pe_ratio=12.0,
    )
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE recommendations SET expires_at = datetime('now', '-1 second') WHERE id = ?",
        (rec_id,),
    )
    conn.commit()
    conn.close()
    expire_stale_recommendations(DB_PATH)
    assert get_recommendation(DB_PATH, rec_id)["status"] == "expired"


def test_expire_stale_does_not_expire_at_exact_now():
    """
    Production SQL uses strict < so a record expiring exactly at 'now' stays pending.
    This documents the intended boundary semantics.
    """
    import sqlite3
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="IBM", signal="BUY",
        reasoning=".", price=130.0, dividend_yield=0.04, pe_ratio=14.0,
    )
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE recommendations SET expires_at = datetime('now') WHERE id = ?",
        (rec_id,),
    )
    conn.commit()
    conn.close()
    expire_stale_recommendations(DB_PATH)
    # datetime('now') is NOT < datetime('now'), so the record should remain pending
    assert get_recommendation(DB_PATH, rec_id)["status"] == "pending"


# --- asset_type column tests ---

def test_create_recommendation_with_asset_type_etf():
    """asset_type='etf' is stored and retrievable."""
    rec_id = create_recommendation(
        db_path=DB_PATH,
        ticker="SPY",
        signal="BUY",
        reasoning="Broad market ETF.",
        price=500.0,
        dividend_yield=0.013,
        pe_ratio=None,
        asset_type="etf",
    )
    rec = get_recommendation(DB_PATH, rec_id)
    assert rec["asset_type"] == "etf"


def test_create_recommendation_default_asset_type():
    """asset_type defaults to 'stock' when not specified."""
    rec_id = create_recommendation(
        db_path=DB_PATH,
        ticker="AAPL",
        signal="BUY",
        reasoning="Strong fundamentals.",
        price=175.0,
        dividend_yield=0.005,
        pe_ratio=28.0,
    )
    rec = get_recommendation(DB_PATH, rec_id)
    assert rec["asset_type"] == "stock"


# --- Confidence column tests (Phase 11) ---

def test_create_recommendation_with_confidence():
    """create_recommendation with confidence='high' stores the value in the DB."""
    rec_id = create_recommendation(
        db_path=DB_PATH,
        ticker="AAPL",
        signal="BUY",
        reasoning="Strong trend.",
        price=175.0,
        dividend_yield=0.005,
        pe_ratio=28.0,
        confidence="high",
    )
    rec = get_recommendation(DB_PATH, rec_id)
    assert rec["confidence"] == "high"


def test_create_recommendation_without_confidence():
    """create_recommendation without confidence stores NULL."""
    rec_id = create_recommendation(
        db_path=DB_PATH,
        ticker="MSFT",
        signal="BUY",
        reasoning="Good growth.",
        price=400.0,
        dividend_yield=0.007,
        pe_ratio=32.0,
    )
    rec = get_recommendation(DB_PATH, rec_id)
    assert rec["confidence"] is None


def test_cache_round_trip_with_confidence():
    """set_cached_analysis with confidence='medium' round-trips through get_cached_analysis."""
    from database.queries import set_cached_analysis, get_cached_analysis
    set_cached_analysis(DB_PATH, "GOOG", "hash123", "BUY", "Strong cloud growth.", confidence="medium")
    result = get_cached_analysis(DB_PATH, "GOOG", "hash123")
    assert result is not None
    assert result["signal"] == "BUY"
    assert result["reasoning"] == "Strong cloud growth."
    assert result["confidence"] == "medium"


def test_cache_round_trip_without_confidence():
    """set_cached_analysis without confidence stores NULL; get_cached_analysis returns confidence=None."""
    from database.queries import set_cached_analysis, get_cached_analysis
    set_cached_analysis(DB_PATH, "AMZN", "hash456", "HOLD", "Wait for earnings.")
    result = get_cached_analysis(DB_PATH, "AMZN", "hash456")
    assert result is not None
    assert result["confidence"] is None


def test_initialize_db_idempotent_confidence_migration():
    """Calling initialize_db twice does not raise (ALTER TABLE is idempotent via try/except)."""
    # First call is from the autouse fixture; call it again here
    initialize_db(DB_PATH)  # Should not raise


# ---------------------------------------------------------------------------
# Phase 13 Task 3: cost_basis migration + get_trade_stats tests
# ---------------------------------------------------------------------------

def test_initialize_db_adds_cost_basis_column():
    """After initialize_db, trades table has a cost_basis column."""
    conn = sqlite3.connect(DB_PATH)
    pragma_rows = conn.execute("PRAGMA table_info(trades)").fetchall()
    conn.close()
    col_names = [row[1] for row in pragma_rows]
    assert "cost_basis" in col_names


def test_create_trade_stores_cost_basis():
    """create_trade with cost_basis=100.0 stores that value in the row."""
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY",
        reasoning="Test.", price=100.0, dividend_yield=None, pe_ratio=None,
    )
    trade_id = create_trade(
        db_path=DB_PATH, recommendation_id=rec_id, ticker="AAPL",
        shares=5, price=110.0, order_id=None, side="sell", cost_basis=100.0,
    )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT cost_basis FROM trades WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    assert row["cost_basis"] == pytest.approx(100.0)


def test_create_trade_cost_basis_defaults_none():
    """create_trade without cost_basis kwarg stores NULL in DB."""
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="MSFT", signal="BUY",
        reasoning="Test.", price=200.0, dividend_yield=None, pe_ratio=None,
    )
    trade_id = create_trade(
        db_path=DB_PATH, recommendation_id=rec_id, ticker="MSFT",
        shares=2, price=210.0, order_id=None,
    )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT cost_basis FROM trades WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    assert row["cost_basis"] is None


def test_get_trade_stats_no_qualifying_rows():
    """Empty trades table returns None from get_trade_stats."""
    from database.queries import get_trade_stats
    assert get_trade_stats(DB_PATH) is None


def test_get_trade_stats_no_sells_with_cost_basis():
    """Only buy-side trades → get_trade_stats returns None."""
    from database.queries import get_trade_stats
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY",
        reasoning="Test.", price=100.0, dividend_yield=None, pe_ratio=None,
    )
    create_trade(
        db_path=DB_PATH, recommendation_id=rec_id, ticker="AAPL",
        shares=5, price=110.0, order_id=None, side="buy", cost_basis=100.0,
    )
    assert get_trade_stats(DB_PATH) is None


def test_get_trade_stats_all_wins():
    """2 sell trades with sell_price > cost_basis → wins=2, losses=0, win_rate=1.0."""
    from database.queries import get_trade_stats
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY",
        reasoning=".", price=100.0, dividend_yield=None, pe_ratio=None,
    )
    create_trade(db_path=DB_PATH, recommendation_id=rec_id, ticker="AAPL",
                 shares=5, price=110.0, order_id=None, side="sell", cost_basis=100.0)
    create_trade(db_path=DB_PATH, recommendation_id=rec_id, ticker="AAPL",
                 shares=3, price=120.0, order_id=None, side="sell", cost_basis=100.0)
    stats = get_trade_stats(DB_PATH)
    assert stats is not None
    assert stats["wins"] == 2
    assert stats["losses"] == 0
    assert stats["win_rate"] == pytest.approx(1.0)


def test_get_trade_stats_mixed():
    """2 wins + 1 loss → total=3, wins=2, losses=1, win_rate approx 0.667."""
    from database.queries import get_trade_stats
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY",
        reasoning=".", price=100.0, dividend_yield=None, pe_ratio=None,
    )
    create_trade(db_path=DB_PATH, recommendation_id=rec_id, ticker="AAPL",
                 shares=5, price=110.0, order_id=None, side="sell", cost_basis=100.0)
    create_trade(db_path=DB_PATH, recommendation_id=rec_id, ticker="AAPL",
                 shares=3, price=120.0, order_id=None, side="sell", cost_basis=100.0)
    create_trade(db_path=DB_PATH, recommendation_id=rec_id, ticker="AAPL",
                 shares=2, price=90.0, order_id=None, side="sell", cost_basis=100.0)
    stats = get_trade_stats(DB_PATH)
    assert stats["total"] == 3
    assert stats["wins"] == 2
    assert stats["losses"] == 1
    assert stats["win_rate"] == pytest.approx(2 / 3)


def test_get_trade_stats_breakeven_counts_as_win():
    """sell_price == cost_basis → wins=1, losses=0."""
    from database.queries import get_trade_stats
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY",
        reasoning=".", price=100.0, dividend_yield=None, pe_ratio=None,
    )
    create_trade(db_path=DB_PATH, recommendation_id=rec_id, ticker="AAPL",
                 shares=5, price=100.0, order_id=None, side="sell", cost_basis=100.0)
    stats = get_trade_stats(DB_PATH)
    assert stats["wins"] == 1
    assert stats["losses"] == 0


def test_get_trade_stats_null_cost_basis_excluded():
    """Sell trade with cost_basis=NULL is excluded; only the one with cost_basis set is counted."""
    from database.queries import get_trade_stats
    rec_id = create_recommendation(
        db_path=DB_PATH, ticker="AAPL", signal="BUY",
        reasoning=".", price=100.0, dividend_yield=None, pe_ratio=None,
    )
    # Sell with cost_basis (counted)
    create_trade(db_path=DB_PATH, recommendation_id=rec_id, ticker="AAPL",
                 shares=5, price=110.0, order_id=None, side="sell", cost_basis=100.0)
    # Sell without cost_basis (excluded)
    create_trade(db_path=DB_PATH, recommendation_id=rec_id, ticker="AAPL",
                 shares=3, price=90.0, order_id=None, side="sell")
    stats = get_trade_stats(DB_PATH)
    assert stats["total"] == 1
    assert stats["wins"] == 1
