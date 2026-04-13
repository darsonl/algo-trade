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
    asset_type: str = "stock",
    confidence: str | None = None,
) -> int:
    """Insert a new recommendation row and return its auto-assigned id."""
    conn = get_connection(db_path)
    cursor = conn.execute(
        """INSERT INTO recommendations
               (ticker, signal, reasoning, price, dividend_yield, pe_ratio, earnings_growth, asset_type, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ticker, signal, reasoning, price, dividend_yield, pe_ratio, earnings_growth, asset_type, confidence),
    )
    conn.commit()
    rec_id = cursor.lastrowid
    conn.close()
    return rec_id


def get_recommendation(db_path: str, rec_id: int) -> sqlite3.Row | None:
    """Return the recommendations row for rec_id, or None if not found."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM recommendations WHERE id = ?", (rec_id,)
    ).fetchone()
    conn.close()
    return row


def update_recommendation_status(db_path: str, rec_id: int, status: str) -> None:
    """Set the status column of recommendation rec_id to status."""
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE recommendations SET status = ? WHERE id = ?", (status, rec_id)
    )
    conn.commit()
    conn.close()


def set_discord_message_id(db_path: str, rec_id: int, message_id: str) -> None:
    """Store the Discord message id against recommendation rec_id."""
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
    side: str = "buy",
    cost_basis: float | None = None,
) -> int:
    """Record an executed trade linked to recommendation_id and return the trade id."""
    conn = get_connection(db_path)
    cursor = conn.execute(
        """INSERT INTO trades (recommendation_id, ticker, shares, price, order_id, side, cost_basis)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (recommendation_id, ticker, shares, price, order_id, side, cost_basis),
    )
    conn.commit()
    trade_id = cursor.lastrowid
    conn.close()
    return trade_id


def get_trade_stats(db_path: str) -> dict | None:
    """Compute win rate, avg gain %, and avg loss % from closed sell trades with cost_basis set.

    Returns dict {total, wins, losses, win_rate, avg_gain_pct, avg_loss_pct}
    or None when no qualifying rows exist (side='sell' AND cost_basis IS NOT NULL).
    Pre-migration rows with cost_basis IS NULL are silently excluded per D-10.
    """
    conn = get_connection(db_path)
    rows = conn.execute(
        """SELECT price, cost_basis FROM trades
           WHERE side = 'sell' AND cost_basis IS NOT NULL""",
    ).fetchall()
    conn.close()
    if not rows:
        return None
    total = len(rows)
    gains = []
    losses = []
    for row in rows:
        pct = (row["price"] - row["cost_basis"]) / row["cost_basis"]
        if row["price"] >= row["cost_basis"]:  # break-even counts as win
            gains.append(pct)
        else:
            losses.append(pct)
    wins = len(gains)
    loss_count = len(losses)
    win_rate = wins / total
    avg_gain_pct = sum(gains) / wins if wins else None
    avg_loss_pct = sum(losses) / loss_count if loss_count else None
    return {
        "total": total,
        "wins": wins,
        "losses": loss_count,
        "win_rate": win_rate,
        "avg_gain_pct": avg_gain_pct,
        "avg_loss_pct": avg_loss_pct,
    }


def get_pending_recommendations(db_path: str) -> list[sqlite3.Row]:
    """Return all pending recommendations ordered newest first."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM recommendations WHERE status = 'pending' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return rows


def ticker_recommended_today(db_path: str, ticker: str) -> bool:
    """Return True if ticker has a non-expired, non-rejected recommendation created today (UTC date)."""
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
    """Set status='expired' on all pending recommendations whose expires_at is in the past."""
    conn = get_connection(db_path)
    conn.execute(
        """UPDATE recommendations
           SET status = 'expired'
           WHERE status = 'pending' AND expires_at < datetime('now')"""
    )
    conn.commit()
    conn.close()


def get_cached_analysis(db_path: str, ticker: str, headline_hash: str) -> dict | None:
    """Return {signal, reasoning, confidence} if a cached result exists for (ticker, headline_hash), else None."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT signal, reasoning, confidence FROM analyst_cache WHERE ticker = ? AND headline_hash = ?",
        (ticker, headline_hash),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {"signal": row["signal"], "reasoning": row["reasoning"], "confidence": row["confidence"]}


def set_cached_analysis(
    db_path: str, ticker: str, headline_hash: str, signal: str, reasoning: str,
    confidence: str | None = None,
) -> None:
    """Upsert an analyst result keyed by (ticker, headline_hash)."""
    conn = get_connection(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO analyst_cache (ticker, headline_hash, signal, reasoning, confidence)
           VALUES (?, ?, ?, ?, ?)""",
        (ticker, headline_hash, signal, reasoning, confidence),
    )
    conn.commit()
    conn.close()


# --- Position CRUD ---


def create_position(db_path: str, ticker: str, shares: float, avg_cost_usd: float) -> int:
    """Insert a new open position for ticker, or re-open a closed one via ON CONFLICT upsert.

    If a row for ticker already exists (from a prior closed position), the conflict clause
    resets shares, avg_cost_usd, entry_date, and status back to 'open'. Returns the row id.
    """
    conn = get_connection(db_path)
    cursor = conn.execute(
        """INSERT INTO positions (ticker, shares, avg_cost_usd, entry_date, status)
           VALUES (?, ?, ?, date('now'), 'open')
           ON CONFLICT(ticker) DO UPDATE SET
               shares=excluded.shares,
               avg_cost_usd=excluded.avg_cost_usd,
               entry_date=date('now'),
               status='open',
               last_price=NULL,
               last_updated=NULL""",
        (ticker, shares, avg_cost_usd),
    )
    conn.commit()
    pos_id = cursor.lastrowid
    conn.close()
    return pos_id


def update_position(db_path: str, ticker: str, new_shares: float, buy_price: float) -> None:
    """Add new_shares to the existing open position for ticker using a weighted average cost.

    The weighted average formula is:
        (existing_shares * existing_avg + new_shares * buy_price) / (existing_shares + new_shares)
    Only updates rows where status='open'.
    """
    conn = get_connection(db_path)
    conn.execute(
        """UPDATE positions
           SET shares = shares + ?,
               avg_cost_usd = (shares * avg_cost_usd + ? * ?) / (shares + ?)
           WHERE ticker = ? AND status = 'open'""",
        (new_shares, new_shares, buy_price, new_shares, ticker),
    )
    conn.commit()
    conn.close()


def get_open_positions(db_path: str) -> list[sqlite3.Row]:
    """Return all rows from positions where status='open', ordered by entry_date ascending."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM positions WHERE status = 'open' ORDER BY entry_date ASC"
    ).fetchall()
    conn.close()
    return rows


def has_open_position(db_path: str, ticker: str) -> bool:
    """Return True if an open position for ticker exists, False otherwise."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT id FROM positions WHERE ticker = ? AND status = 'open'",
        (ticker,),
    ).fetchone()
    conn.close()
    return row is not None


def close_position(db_path: str, ticker: str) -> None:
    """Set status='closed' on the open position for ticker."""
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE positions SET status = 'closed' WHERE ticker = ? AND status = 'open'",
        (ticker,),
    )
    conn.commit()
    conn.close()


def upsert_position(db_path: str, ticker: str, shares: float, price: float) -> None:
    """Create a new position or add shares to an existing open position.

    Dispatches to create_position when no open position exists for ticker,
    or update_position (weighted avg cost) when one does.
    """
    if has_open_position(db_path, ticker):
        update_position(db_path, ticker, new_shares=shares, buy_price=price)
    else:
        create_position(db_path, ticker, shares=shares, avg_cost_usd=price)


def set_sell_blocked(db_path: str, ticker: str) -> None:
    """Set sell_blocked=True on the open position for ticker (per D-04)."""
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE positions SET sell_blocked = 1 WHERE ticker = ? AND status = 'open'",
        (ticker,),
    )
    conn.commit()
    conn.close()


def reset_sell_blocked(db_path: str, ticker: str) -> None:
    """Reset sell_blocked=False on the open position for ticker (per D-04)."""
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE positions SET sell_blocked = 0 WHERE ticker = ? AND status = 'open'",
        (ticker,),
    )
    conn.commit()
    conn.close()


# --- Analyst quota tracking (D-11) ---


def get_analyst_call_count_today(db_path: str, provider: str) -> int:
    """Return the number of analyst API calls made today for provider.

    Returns 0 if no row exists for today's date and the given provider.
    """
    from datetime import date
    today = date.today().isoformat()
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT count FROM analyst_calls WHERE date = ? AND provider = ?",
        (today, provider),
    ).fetchone()
    conn.close()
    return row["count"] if row else 0


def increment_analyst_call_count(db_path: str, provider: str) -> None:
    """Upsert today's call count for provider, incrementing by 1.

    Uses INSERT ... ON CONFLICT DO UPDATE to atomically increment the counter
    or create a new row with count=1 if none exists for today and provider.
    """
    from datetime import date
    today = date.today().isoformat()
    conn = get_connection(db_path)
    conn.execute(
        """INSERT INTO analyst_calls (date, provider, count) VALUES (?, ?, 1)
           ON CONFLICT(date, provider) DO UPDATE SET count = count + 1""",
        (today, provider),
    )
    conn.commit()
    conn.close()
