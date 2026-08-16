from __future__ import annotations

import sqlite3
from datetime import datetime

from database.models import get_cursor
from database.order_accounting import (
    BLOCKING_ORDER_STATUSES,
    UNRESOLVED_ORDER_STATUSES,
    order_commitment,
    remaining_buy_reservation,
)
from market_time import (
    as_utc,
    intended_session_date,
    market_session_bounds_utc,
    market_session_date,
)
from schwab_client.order_payload import extract_fills, extract_replacement


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


def claim_recommendation_tx(
    conn, rec_id: int, new_status: str, instant: datetime | None = None
) -> bool:
    """Claim a recommendation INSIDE a caller's transaction, expiry included.

    Takes a `conn` rather than a db_path for the same reason the order CRUD
    does: the cap check, this claim, and the reservation must be one
    transaction, and a db_path-taking function opens a second connection that
    then blocks on the caller's own write lock.

    Expiry is in the SQL predicate rather than checked beforehand, so the
    liveness test and the claim are one atomic step — a recommendation cannot
    expire between being examined and being taken. `expires_at > now` because
    `now >= expires_at` is expired, matching guard 3.
    """
    stamp = as_utc(instant).strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        """UPDATE recommendations SET status = ?
            WHERE id = ? AND status = 'pending'
              AND (expires_at IS NULL OR expires_at > ?)""",
        (new_status, rec_id, stamp),
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


# ---------------------------------------------------------------------------
# Order ledger
#
# These functions take a CONNECTION, not a db_path — unlike everything above.
# The daily-notional read and the insert that reserves against it must be one
# transaction (round-5 finding 2). A db_path-taking function opens its own
# second connection, which then blocks on the caller's write lock and raises
# "database is locked" after busy_timeout. Callers use
# `with immediate_transaction(db_path) as conn:` for the guard->reserve
# sequence, or `with get_cursor(db_path) as conn:` for plain reads.
# ---------------------------------------------------------------------------

def create_order(
    conn,
    recommendation_id: int | None,
    ticker: str,
    side: str,
    order_type: str,
    requested_shares: float,
    reference_price: float,
    limit_price: float | None = None,
    instant: datetime | None = None,
) -> int:
    """Insert a 'pending_submit' order and return its id.

    Called BEFORE the broker request. If the process dies between this insert
    and the broker response, the row survives and the order is recoverable; the
    reverse order can leave a real position with no ledger entry at all.

    Stamps intended_session_date at insert time, because it is a fact about the
    submission instant and nothing later can recover it: once the row is written,
    "which session was this heading for" is no longer derivable from a status.
    submitted_at is written from the same instant rather than left to SQLite's
    default, so the two can never describe different moments.
    """
    stamped = as_utc(instant)
    cur = conn.execute(
        """INSERT INTO orders (recommendation_id, ticker, side, order_type,
                               requested_shares, reference_price, limit_price,
                               submitted_at, intended_session_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (recommendation_id, ticker, side, order_type,
         requested_shares, reference_price, limit_price,
         stamped.strftime("%Y-%m-%d %H:%M:%S"),
         intended_session_date(stamped).isoformat()),
    )
    return cur.lastrowid


def attach_broker_order_id(conn, order_id: int, broker_order_id: str) -> None:
    """Record the broker's id and move the order to 'submitted'."""
    conn.execute(
        """UPDATE orders
              SET broker_order_id = ?, status = 'submitted',
                  updated_at = datetime('now')
            WHERE id = ?""",
        (broker_order_id, order_id),
    )


def _mark(conn, order_id: int, status: str, reason: str) -> None:
    conn.execute(
        "UPDATE orders SET status = ?, failure_reason = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (status, reason, order_id),
    )


def mark_order_submit_unknown(conn, order_id: int, reason: str) -> None:
    """The submission outcome was ambiguous — the order MAY exist at the broker.

    Never terminal. Only an operator resolves it; nothing sweeps it away.
    """
    _mark(conn, order_id, "submit_unknown", reason)


def mark_order_submit_failed(conn, order_id: int, reason: str) -> None:
    """The broker definitively refused. This is the one zero-fill we can trust."""
    _mark(conn, order_id, "submit_failed", reason)


def observe_fills(conn, order_id: int, filled_shares: float,
                  filled_notional: float, status: str) -> None:
    """Record fill data read from the broker, and flip `fills_observed`.

    The flag is the point: until someone has actually looked, a terminal order
    keeps its full commitment, because a default zero is indistinguishable from
    a verified zero.
    """
    conn.execute(
        """UPDATE orders
              SET filled_shares = ?, filled_notional = ?, fills_observed = 1,
                  status = ?, updated_at = datetime('now')
            WHERE id = ?""",
        (filled_shares, filled_notional, status, order_id),
    )


def get_order(conn, order_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def get_orders_by_status(conn, statuses: tuple[str, ...]) -> list[dict]:
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"SELECT * FROM orders WHERE status IN ({placeholders}) ORDER BY id",
        tuple(statuses),
    ).fetchall()
    return [dict(r) for r in rows]


def get_day_notional(conn, instant: datetime | None = None) -> float:
    """The committed buy notional of the session `instant` would trade in.

    "Today" is the US market session, never the host calendar date: on a UTC+8
    host the 21:45 and 03:30 scans are one session but two local dates, so a
    localtime bucket resets the ceiling mid-session.

    Buckets on intended_session_date, NOT on a submitted_at range (round-5 #7).
    A submitted_at range answers "entered during Friday", but an order entered
    20:00 ET Friday executes Monday. Bucketing on entry let Friday night and
    Monday each draw a full allowance against Monday's single real ceiling.

    Every buy row in the session is summed through order_commitment rather than
    filtered by status, because status alone cannot answer how much capital an
    order holds — a partially filled then cancelled order is terminal and still
    committed.
    """
    session = intended_session_date(instant).isoformat()
    rows = conn.execute(
        """SELECT * FROM orders
            WHERE side = 'buy' AND intended_session_date = ?""",
        (session,),
    ).fetchall()
    return sum(order_commitment(dict(r)) for r in rows)


def get_open_buy_reservation(conn) -> float:
    """Buy exposure not yet visible as a broker position.

    Deliberately NOT order_commitment: broker position market value already
    includes filled shares, so reserving those again double-charges the
    portfolio ceiling.
    """
    rows = conn.execute(
        "SELECT * FROM orders WHERE side = 'buy' AND status IN ({})".format(
            ",".join("?" for _ in BLOCKING_ORDER_STATUSES)
        ),
        BLOCKING_ORDER_STATUSES,
    ).fetchall()
    return sum(remaining_buy_reservation(dict(r)) for r in rows)


def sweep_stale_pending_submits(conn, older_than_seconds: int = 300) -> list[int]:
    """Make orders stranded by a crash resolvable. Returns the ids swept.

    A row committed before submission whose process then died stays
    'pending_submit' with no broker id — unreachable, because the status sweep
    needs an id and manual resolution only accepts unresolved rows. Moving it to
    'submit_unknown' does NOT resubmit anything; it says "this may exist, a human
    must check", which is the truthful state.

    Recent rows are left alone: they are probably just mid-flight, and sweeping
    them would invent an unknown that never happened.
    """
    rows = conn.execute(
        """SELECT id FROM orders
            WHERE status = 'pending_submit'
              AND broker_order_id IS NULL
              AND submitted_at <= datetime('now', ?)""",
        (f"-{int(older_than_seconds)} seconds",),
    ).fetchall()
    ids = [r["id"] for r in rows]
    for order_id in ids:
        _mark(conn, order_id, "submit_unknown",
              "stranded in pending_submit; process likely died before the broker replied")
    return ids


# --- Operator resolution of orders the system cannot resolve itself ---

# `adopt`            the order exists; attach its broker id so it becomes pollable.
# `confirmed_absent` verified no such order exists; release the capital.
# `keep_blocked`     cannot tell yet; change nothing, stay reserved.
RESOLUTIONS = ("adopt", "confirmed_absent", "keep_blocked")


def resolve_order_manually(
    conn,
    order_id: int,
    resolution: str,
    actor: str,
    evidence: str,
    broker_order_id: str | None = None,
) -> int:
    """Apply an operator's decision about an unresolved order. Returns the event id.

    `/resolve` is report-only: matching fields establish an order's shape, not
    its provenance, and Schwab exposes no client-supplied correlation id. So a
    human owns this call, and every use of it is audited.

    Only orders in UNRESOLVED_ORDER_STATUSES are eligible. A pending_submit row
    stranded by a crash reaches that state through sweep_stale_pending_submits;
    resolving an already-resolved order would let an operator overwrite state the
    broker actually reported.
    """
    if resolution not in RESOLUTIONS:
        raise ValueError(f"unknown resolution {resolution!r}; expected one of {RESOLUTIONS}")
    if not (actor or "").strip():
        raise ValueError("actor is required: an unattributed override is not an audit trail")
    if not (evidence or "").strip():
        raise ValueError("evidence is required: record what was checked and where")

    order = get_order(conn, order_id)
    if order is None:
        raise ValueError(f"no order with id {order_id}")

    previous_status = order["status"]
    if previous_status not in UNRESOLVED_ORDER_STATUSES:
        raise ValueError(
            f"order {order_id} is {previous_status!r}, not unresolved; "
            "manual resolution would overwrite state the broker reported"
        )

    if resolution == "adopt":
        if not (broker_order_id or "").strip():
            raise ValueError("adopt requires the broker order id to attach")
        # Deliberately does NOT touch fills_observed: adopting says where the
        # order is, not what it filled, so the full commitment stands until
        # someone actually reads fill data.
        attach_broker_order_id(conn, order_id, broker_order_id)
        # The ambiguity is settled: we know WHICH order is ours, so its own
        # economics govern and the worst-case candidate reservation lapses.
        _clear_reservation_override(conn, order_id)
        new_status = "submitted"
    elif resolution == "confirmed_absent":
        # The one zero-fill a human can vouch for. submit_failed is the existing
        # "definitively never landed" status, so capital releases through the
        # same rule as a broker refusal rather than a second special case.
        _mark(conn, order_id, "submit_failed",
              f"operator confirmed absent at broker: {evidence}")
        _clear_reservation_override(conn, order_id)
        new_status = "submit_failed"
    else:  # keep_blocked
        new_status = previous_status

    cur = conn.execute(
        """INSERT INTO order_resolution_events
               (order_id, resolution, actor, evidence, broker_order_id,
                previous_status, new_status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (order_id, resolution, actor.strip(), evidence.strip(), broker_order_id,
         previous_status, new_status),
    )
    return cur.lastrowid


def get_resolution_events(conn, order_id: int) -> list[dict]:
    """Every operator decision about this order, oldest first."""
    rows = conn.execute(
        "SELECT * FROM order_resolution_events WHERE order_id = ? ORDER BY id",
        (order_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Replacement chains ---

def _chain_broker_ids(conn, order_id: int, max_depth: int) -> list[str] | None:
    """Broker ids already in this order's chain, walking back to its root.

    Returns None if the chain is longer than max_depth. Both the visited set and
    the depth bound exist because a chain is broker-controlled data: a loop or a
    pathological length must not be walked forever.
    """
    seen: list[str] = []
    current = get_order(conn, order_id)
    depth = 0
    while current is not None:
        if depth > max_depth:
            return None
        if current["broker_order_id"]:
            seen.append(current["broker_order_id"])
        predecessor_id = current["predecessor_order_id"]
        current = get_order(conn, predecessor_id) if predecessor_id else None
        depth += 1
    return seen


def adopt_replacement(conn, order_id: int, payload: dict, max_depth: int = 10) -> int | None:
    """Follow a REPLACED order to its successor. Returns the successor row id.

    Returns None when the payload reports no replacement.

    On success the predecessor is closed at its ACTUAL fills and the successor is
    inserted with its OWN quantity and limit. Carrying the predecessor's numbers
    forward is the defect this exists to prevent: 5 shares at $100 replaced by 10
    at $150 would keep $500 reserved while $1,500 is actionable.

    Anything ambiguous — several successors, a missing id, absent economics, a
    changed symbol or side, a loop, an over-long chain — moves the predecessor to
    submit_unknown, which keeps its FULL commitment and asks for a human. We
    cannot say what is live at the broker, so we assume the worst.
    """
    order = get_order(conn, order_id)
    if order is None:
        raise ValueError(f"no order with id {order_id}")

    replacement, reason = extract_replacement(payload)
    if replacement is None and reason is None:
        return None

    def _unresolved(why: str) -> None:
        _mark(conn, order_id, "submit_unknown", f"replacement not adoptable: {why}")

    if replacement is None:
        _unresolved(reason)
        return None

    if replacement.symbol != order["ticker"] or replacement.side != order["side"]:
        _unresolved(
            f"successor is {replacement.side} {replacement.symbol}, "
            f"predecessor was {order['side']} {order['ticker']}"
        )
        return None

    chain = _chain_broker_ids(conn, order_id, max_depth)
    if chain is None:
        _unresolved(f"replacement chain deeper than {max_depth}")
        return None
    if replacement.successor_id in chain:
        _unresolved(f"successor {replacement.successor_id} already appears in this chain")
        return None

    # Predecessor first: its fills really happened and must survive the transition.
    filled_shares, filled_notional = extract_fills(payload)
    observe_fills(conn, order_id, filled_shares, filled_notional, "cancelled")

    successor_id = create_order(
        conn,
        recommendation_id=order["recommendation_id"],
        ticker=replacement.symbol,
        side=replacement.side,
        order_type="limit" if replacement.limit_price is not None else "market",
        requested_shares=replacement.quantity,
        reference_price=replacement.limit_price or order["reference_price"],
        limit_price=replacement.limit_price,
    )
    conn.execute(
        """UPDATE orders
              SET status = 'submitted', broker_order_id = ?, predecessor_order_id = ?,
                  updated_at = datetime('now')
            WHERE id = ?""",
        (replacement.successor_id, order_id, successor_id),
    )
    return successor_id


# --- Worst-case reservation for ambiguous submissions ---

def record_candidate_observation(conn, order_id: int, candidates: list[dict]) -> float:
    """Persist the broker orders that might be this one, and reserve for all of them.

    Returns the resulting reservation.

    `/resolve` is report-only, so nothing here changes the order's status — but
    the RESERVATION has to be durable. Candidates leave the working-order
    endpoint once they fill or cancel, and a later observation finding none of
    them is not evidence that none were ours. So the override only ever rises
    while the order is unresolved; only a human resolution clears it.

    Floored at the order's own commitment: our order may be precisely the one
    the endpoint did not return.
    """
    order = get_order(conn, order_id)
    if order is None:
        raise ValueError(f"no order with id {order_id}")
    if order["status"] not in UNRESOLVED_ORDER_STATUSES:
        raise ValueError(
            f"order {order_id} is {order['status']!r}, not unresolved; "
            "candidate reservation applies only to an ambiguous submission"
        )

    total = 0.0
    for candidate in candidates:
        quantity = float(candidate.get("quantity") or 0.0)
        price = candidate.get("limit_price")
        notional = quantity * float(price if price is not None else order["reference_price"])
        total += notional
        conn.execute(
            """INSERT INTO order_candidates
                   (order_id, broker_order_id, symbol, side, quantity, limit_price, notional)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (order_id, candidate.get("broker_order_id"), candidate.get("symbol"),
             candidate.get("side"), quantity, price, notional),
        )

    own = order_commitment({**dict(order), "reserved_notional_override": None})
    reservation = max(total, own, float(order["reserved_notional_override"] or 0.0))
    conn.execute(
        "UPDATE orders SET reserved_notional_override = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (reservation, order_id),
    )
    return reservation


def get_candidate_observations(conn, order_id: int) -> list[dict]:
    """Every candidate ever observed for this order, oldest first."""
    rows = conn.execute(
        "SELECT * FROM order_candidates WHERE order_id = ? ORDER BY id", (order_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def _clear_reservation_override(conn, order_id: int) -> None:
    conn.execute(
        "UPDATE orders SET reserved_notional_override = NULL WHERE id = ?", (order_id,)
    )


# ---------------------------------------------------------------------------
# Ops-alert outbox. Like the order CRUD above, these take a CONNECTION rather
# than a db_path, so an alert can be enqueued inside the same transaction as
# the state change that warranted it.
# ---------------------------------------------------------------------------

def enqueue_ops_alert(conn, message: str) -> int:
    """Persist an ops alert as undelivered and return its id.

    Called BEFORE the delivery attempt. The ordering is the entire safety
    property: a send that fails after this insert leaves a durable row behind,
    whereas the reverse order loses the alert with nothing to show for it.
    """
    cur = conn.execute("INSERT INTO ops_alerts (message) VALUES (?)", (message,))
    return cur.lastrowid


def mark_ops_alert_delivered(conn, alert_id: int) -> None:
    """Record that delivery demonstrably succeeded."""
    conn.execute(
        "UPDATE ops_alerts SET delivered_at = datetime('now') WHERE id = ?",
        (alert_id,),
    )


def record_ops_alert_failure(conn, alert_id: int, error: str) -> None:
    """Count a failed delivery attempt WITHOUT consuming the alert.

    `delivered_at` stays NULL on purpose, so the drain picks the alert up
    again. attempts/last_error exist to make a persistently failing alert
    visible to an operator rather than silently retried forever.
    """
    conn.execute(
        "UPDATE ops_alerts SET attempts = attempts + 1, last_error = ? WHERE id = ?",
        (error, alert_id),
    )


def get_ops_alert(conn, alert_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM ops_alerts WHERE id = ?", (alert_id,)
    ).fetchone()
    return dict(row) if row else None


def get_undelivered_ops_alerts(conn, limit: int = 20) -> list[dict]:
    """Alerts still awaiting delivery, oldest first.

    Oldest-first because ops alerts read as a narrative — replaying a backlog
    out of order misleads whoever is trying to reconstruct what happened. The
    limit bounds a single drain pass so a long outage cannot dump an unbounded
    backlog into one burst of Discord calls.
    """
    rows = conn.execute(
        "SELECT * FROM ops_alerts WHERE delivered_at IS NULL ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
