import sqlite3
from database.models import get_connection


def create_recommendation(
    db_path: str,
    ticker: str,
    signal: str,
    reasoning: str,
    price: float,
    dividend_yield: float | None,
    pe_ratio: float | None,
    earnings_growth: float | None = None,
) -> int:
    conn = get_connection(db_path)
    cursor = conn.execute(
        """INSERT INTO recommendations
               (ticker, signal, reasoning, price, dividend_yield, pe_ratio, earnings_growth)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ticker, signal, reasoning, price, dividend_yield, pe_ratio, earnings_growth),
    )
    conn.commit()
    rec_id = cursor.lastrowid
    conn.close()
    return rec_id


def get_recommendation(db_path: str, rec_id: int) -> sqlite3.Row | None:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM recommendations WHERE id = ?", (rec_id,)
    ).fetchone()
    conn.close()
    return row


def update_recommendation_status(db_path: str, rec_id: int, status: str) -> None:
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE recommendations SET status = ? WHERE id = ?", (status, rec_id)
    )
    conn.commit()
    conn.close()


def set_discord_message_id(db_path: str, rec_id: int, message_id: str) -> None:
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE recommendations SET discord_message_id = ? WHERE id = ?",
        (message_id, rec_id),
    )
    conn.commit()
    conn.close()


def create_trade(
    db_path: str,
    recommendation_id: int,
    ticker: str,
    shares: float,
    price: float,
    order_id: str | None,
) -> int:
    conn = get_connection(db_path)
    cursor = conn.execute(
        """INSERT INTO trades (recommendation_id, ticker, shares, price, order_id)
           VALUES (?, ?, ?, ?, ?)""",
        (recommendation_id, ticker, shares, price, order_id),
    )
    conn.commit()
    trade_id = cursor.lastrowid
    conn.close()
    return trade_id


def get_pending_recommendations(db_path: str) -> list[sqlite3.Row]:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM recommendations WHERE status = 'pending' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return rows


def ticker_recommended_today(db_path: str, ticker: str) -> bool:
    conn = get_connection(db_path)
    row = conn.execute(
        """SELECT id FROM recommendations
           WHERE ticker = ? AND date(created_at) = date('now')
           AND status NOT IN ('expired', 'rejected')""",
        (ticker,),
    ).fetchone()
    conn.close()
    return row is not None


def expire_stale_recommendations(db_path: str) -> None:
    conn = get_connection(db_path)
    conn.execute(
        """UPDATE recommendations
           SET status = 'expired'
           WHERE status = 'pending' AND expires_at < datetime('now')"""
    )
    conn.commit()
    conn.close()
