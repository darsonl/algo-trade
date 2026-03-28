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
