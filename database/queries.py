from __future__ import annotations

import sqlite3
from datetime import datetime

from database.models import get_cursor
from market_time import market_session_bounds_utc, market_session_date


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
    with get_cursor(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO recommendations
                   (ticker, signal, reasoning, price, dividend_yield, pe_ratio, earnings_growth, asset_type, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, signal, reasoning, price, dividend_yield, pe_ratio, earnings_growth, asset_type, confidence),
        )
        return cursor.lastrowid


def get_recommendation(db_path: str, rec_id: int) -> sqlite3.Row | None:
    """Return the recommendations row for rec_id, or None if not found."""
    with get_cursor(db_path) as conn:
        return conn.execute(
            "SELECT * FROM recommendations WHERE id = ?", (rec_id,)
        ).fetchone()


def claim_recommendation(db_path: str, rec_id: int, new_status: str) -> bool:
    """Atomically transition a recommendation from 'pending' to new_status.

    Returns True iff this call performed the transition (the row was still
    pending). The Discord buttons use this as an idempotency gate: a double
    click or a click on a stale restored view loses the race and gets False,
    so an order can never be placed twice for the same recommendation.
    """
    with get_cursor(db_path) as conn:
        cursor = conn.execute(
            "UPDATE recommendations SET status = ? WHERE id = ? AND status = 'pending'",
            (new_status, rec_id),
        )
        return cursor.rowcount == 1


def update_recommendation_status(db_path: str, rec_id: int, status: str) -> None:
    """Set the status column of recommendation rec_id to status."""
    with get_cursor(db_path) as conn:
        conn.execute(
            "UPDATE recommendations SET status = ? WHERE id = ?", (status, rec_id)
        )


def set_discord_message_id(db_path: str, rec_id: int, message_id: str) -> None:
    """Store the Discord message id against recommendation rec_id."""
    with get_cursor(db_path) as conn:
        conn.execute(
            "UPDATE recommendations SET discord_message_id = ? WHERE id = ?",
            (message_id, rec_id),
        )


def create_trade(
    db_path: str,
    recommendation_id: int,
    ticker: str,
    shares: float,
    price: float,
    order_id: str | None,
    side: str = "buy",
    cost_basis: float | None = None,
    limit_price: float | None = None,    # NEW — RISK-03
    order_type: str = "market",          # NEW — RISK-03
) -> int:
    """Record an executed trade linked to recommendation_id and return the trade id."""
    with get_cursor(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO trades
                   (recommendation_id, ticker, shares, price, order_id, side, cost_basis, limit_price, order_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (recommendation_id, ticker, shares, price, order_id, side, cost_basis, limit_price, order_type),
        )
        return cursor.lastrowid


def get_trade_stats(db_path: str) -> dict | None:
    """Compute win rate, avg gain %, and avg loss % from closed sell trades with cost_basis set.

    Returns dict {total, wins, losses, win_rate, avg_gain_pct, avg_loss_pct}
    or None when no qualifying rows exist (side='sell' AND cost_basis IS NOT NULL).
    Pre-migration rows with cost_basis IS NULL are silently excluded per D-10.
    """
    with get_cursor(db_path) as conn:
        rows = conn.execute(
            """SELECT price, cost_basis FROM trades
               WHERE side = 'sell' AND cost_basis IS NOT NULL""",
        ).fetchall()
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


def get_closed_trades(db_path: str, limit: int = 20) -> list[dict]:
    """Return up to `limit` closed sell-trades newest-first.

    Filter per D-05/D-06: WHERE side = 'sell' AND cost_basis IS NOT NULL — no JOIN.
    Pre-migration rows with NULL cost_basis are excluded consistent with get_trade_stats.

    Returns a list of plain dicts with keys: ticker, price (exit price), cost_basis (entry price),
    executed_at (ISO timestamp string). Empty list when no qualifying rows exist.
    """
    with get_cursor(db_path) as conn:
        rows = conn.execute(
            """SELECT ticker, price, cost_basis, executed_at FROM trades
               WHERE side = 'sell' AND cost_basis IS NOT NULL
               ORDER BY executed_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [
        {
            "ticker": row["ticker"],
            "price": row["price"],
            "cost_basis": row["cost_basis"],
            "executed_at": row["executed_at"],
        }
        for row in rows
    ]


def get_pending_recommendations(db_path: str) -> list[sqlite3.Row]:
    """Return all pending recommendations ordered newest first."""
    with get_cursor(db_path) as conn:
        return conn.execute(
            "SELECT * FROM recommendations WHERE status = 'pending' ORDER BY created_at DESC"
        ).fetchall()


def ticker_recommended_today(
    db_path: str, ticker: str, instant: datetime | None = None
) -> bool:
    """Return True if ticker has a non-expired, non-rejected recommendation this session.

    "Today" is the US market SESSION date, not a calendar date on this machine.
    Both earlier attempts were wrong here: bare date('now') compares UTC days,
    which roll over mid-afternoon US time; date(..., 'localtime') compares the
    host's days, which on a UTC+8 host splits one US session in two — the
    configured 21:45 and 03:30 scans are 09:45 ET and 15:30 ET of the SAME
    session but land on different local dates, so this guard never matched
    between them and the same ticker could be recommended twice.

    The range predicate also leaves created_at unwrapped, so it can use an index.
    """
    start, end = market_session_bounds_utc(instant)
    with get_cursor(db_path) as conn:
        row = conn.execute(
            """SELECT id FROM recommendations
               WHERE ticker = ? AND created_at >= ? AND created_at < ?
               AND status NOT IN ('expired', 'rejected')""",
            (ticker, start, end),
        ).fetchone()
    return row is not None


def expire_stale_recommendations(db_path: str) -> None:
    """Set status='expired' on all pending recommendations whose expires_at is in the past."""
    with get_cursor(db_path) as conn:
        conn.execute(
            """UPDATE recommendations
               SET status = 'expired'
               WHERE status = 'pending' AND expires_at < datetime('now')"""
        )


def get_cached_analysis(db_path: str, ticker: str, headline_hash: str) -> dict | None:
    """Return {signal, reasoning, confidence} if a cached result exists for (ticker, headline_hash), else None."""
    with get_cursor(db_path) as conn:
        row = conn.execute(
            "SELECT signal, reasoning, confidence FROM analyst_cache WHERE ticker = ? AND headline_hash = ?",
            (ticker, headline_hash),
        ).fetchone()
    if row is None:
        return None
    return {"signal": row["signal"], "reasoning": row["reasoning"], "confidence": row["confidence"]}


def set_cached_analysis(
    db_path: str, ticker: str, headline_hash: str, signal: str, reasoning: str,
    confidence: str | None = None,
) -> None:
    """Upsert an analyst result keyed by (ticker, headline_hash)."""
    with get_cursor(db_path) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO analyst_cache (ticker, headline_hash, signal, reasoning, confidence)
               VALUES (?, ?, ?, ?, ?)""",
            (ticker, headline_hash, signal, reasoning, confidence),
        )


# --- Position CRUD ---


def create_position(
    db_path: str, ticker: str, shares: float, avg_cost_usd: float,
    instant: datetime | None = None,
) -> int:
    """Insert a new open position for ticker, or re-open a closed one via ON CONFLICT upsert.

    If a row for ticker already exists (from a prior closed position), the conflict clause
    resets shares, avg_cost_usd, entry_date, and status back to 'open'. Returns the row id.

    entry_date is the US market session date, supplied explicitly rather than
    left to SQL. It feeds hold_days in the sell prompt, so a half-day skew from
    host-local bucketing would misreport holding periods.
    """
    session = market_session_date(instant).isoformat()
    with get_cursor(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO positions (ticker, shares, avg_cost_usd, entry_date, status)
               VALUES (?, ?, ?, ?, 'open')
               ON CONFLICT(ticker) DO UPDATE SET
                   shares=excluded.shares,
                   avg_cost_usd=excluded.avg_cost_usd,
                   entry_date=excluded.entry_date,
                   status='open',
                   last_price=NULL,
                   last_updated=NULL""",
            (ticker, shares, avg_cost_usd, session),
        )
        return cursor.lastrowid


def update_position(db_path: str, ticker: str, new_shares: float, buy_price: float) -> None:
    """Add new_shares to the existing open position for ticker using a weighted average cost.

    The weighted average formula is:
        (existing_shares * existing_avg + new_shares * buy_price) / (existing_shares + new_shares)
    Only updates rows where status='open'.
    """
    with get_cursor(db_path) as conn:
        conn.execute(
            """UPDATE positions
               SET shares = shares + ?,
                   avg_cost_usd = (shares * avg_cost_usd + ? * ?) / (shares + ?)
               WHERE ticker = ? AND status = 'open'""",
            (new_shares, new_shares, buy_price, new_shares, ticker),
        )


def get_open_positions(db_path: str) -> list[sqlite3.Row]:
    """Return all rows from positions where status='open', ordered by entry_date ascending."""
    with get_cursor(db_path) as conn:
        return conn.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY entry_date ASC"
        ).fetchall()


def has_open_position(db_path: str, ticker: str) -> bool:
    """Return True if an open position for ticker exists, False otherwise."""
    with get_cursor(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM positions WHERE ticker = ? AND status = 'open'",
            (ticker,),
        ).fetchone()
    return row is not None


def close_position(db_path: str, ticker: str) -> None:
    """Set status='closed' on the open position for ticker."""
    with get_cursor(db_path) as conn:
        conn.execute(
            "UPDATE positions SET status = 'closed' WHERE ticker = ? AND status = 'open'",
            (ticker,),
        )


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
    with get_cursor(db_path) as conn:
        conn.execute(
            "UPDATE positions SET sell_blocked = 1 WHERE ticker = ? AND status = 'open'",
            (ticker,),
        )


def reset_sell_blocked(db_path: str, ticker: str) -> None:
    """Reset sell_blocked=False on the open position for ticker (per D-04)."""
    with get_cursor(db_path) as conn:
        conn.execute(
            "UPDATE positions SET sell_blocked = 0 WHERE ticker = ? AND status = 'open'",
            (ticker,),
        )


# --- Analyst quota tracking (D-11) ---


def get_analyst_call_count_today(
    db_path: str, provider: str, instant: datetime | None = None
) -> int:
    """Return the number of analyst API calls made this session for provider.

    Returns 0 if no row exists for the current session date and the given provider.

    Bucketed on the US market session, not date.today(): on a UTC+8 host the
    local day rolls over mid-session, which let ANALYST_DAILY_LIMIT reset partway
    through a single US trading day and silently double the effective quota.
    """
    today = market_session_date(instant).isoformat()
    with get_cursor(db_path) as conn:
        row = conn.execute(
            "SELECT count FROM analyst_calls WHERE date = ? AND provider = ?",
            (today, provider),
        ).fetchone()
    return row["count"] if row else 0


def increment_analyst_call_count(
    db_path: str, provider: str, instant: datetime | None = None
) -> None:
    """Upsert this session's call count for provider, incrementing by 1.

    Uses INSERT ... ON CONFLICT DO UPDATE to atomically increment the counter
    or create a new row with count=1 if none exists for this session and provider.
    Session-bucketed for the same reason as get_analyst_call_count_today.
    """
    today = market_session_date(instant).isoformat()
    with get_cursor(db_path) as conn:
        conn.execute(
            """INSERT INTO analyst_calls (date, provider, count) VALUES (?, ?, 1)
               ON CONFLICT(date, provider) DO UPDATE SET count = count + 1""",
            (today, provider),
        )
