"""Ambiguous submissions: search, report, and nag. Never resolve.

A `submit_unknown` order MAY exist at the broker. Guard 11 blocks every new
order for its ticker while it is open, and only a human can clear it -- so this
module has exactly two jobs: show an operator what the broker actually holds,
and keep saying so until someone acts.

It lives outside `main.py` because `discord_bot.bot` needs the report for
`/resolve`, and `main` already imports `TradingBot`. It lives in `risk/`
alongside `preflight` and `kill_switch` because that is what it is: the
recovery path for a position we cannot account for.

Nothing here transitions an order. Matching fields establish an order's SHAPE,
not its PROVENANCE, and Schwab exposes no client-supplied correlation id -- two
identical buys may both be ours. Only `queries.resolve_order_manually`, driven
by a human and audited, moves a row.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from database import queries
from database.models import get_cursor
from database.order_accounting import UNRESOLVED_ORDER_STATUSES, order_commitment
from schwab_client.order_payload import _as_datetime
from schwab_client.orders import find_recent_orders

logger = logging.getLogger(__name__)


def _parse_utc(value) -> datetime | None:
    """A stored timestamp as an AWARE UTC datetime.

    Awareness matters: the candidate window compares this against `now`, and
    mixing a naive bound with an aware one raises rather than filtering.
    """
    parsed = _as_datetime(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _describe_candidate(order: dict, candidate: dict) -> str:
    """One candidate line, with the ways it differs from what we submitted.

    The differences are the point. An operator deciding whether to `adopt` is
    being asked to judge provenance from shape, so the report must show where
    the shape disagrees rather than presenting a match as a fact.
    """
    diffs = []
    qty = float(candidate.get("quantity") or 0.0)
    if abs(qty - float(order["requested_shares"])) > 1e-9:
        diffs.append(f"shares {qty:g} vs our {float(order['requested_shares']):g}")

    limit = candidate.get("limit_price")
    if limit is None:
        diffs.append("no limit price (priced at our reference for reserving)")
    elif order["limit_price"] is not None and abs(float(limit) - float(order["limit_price"])) > 1e-9:
        diffs.append(f"limit ${float(limit):.2f} vs our ${float(order['limit_price']):.2f}")

    detail = f"; {', '.join(diffs)}" if diffs else "; matches on every field we can compare"
    return (
        f"    - broker order {candidate['broker_order_id']} "
        f"[{candidate.get('status') or 'no status'}] "
        f"entered {candidate.get('entered_at') or 'unknown time'}{detail}"
    )


def report_unknown_submissions(config, client=None, now: datetime | None = None) -> str:
    """Search the broker for orders that might be our ambiguous submissions.

    Reads, ranks, and REPORTS. It never writes order status -- not even for a
    single exact match. Matching fields establish an order's shape, not its
    provenance, and Schwab exposes no client-supplied correlation id, so two
    identical buys may both be ours. A human owns that judgement and records it
    through `/resolve`, which is audited.

    It does write the RESERVATION, via record_candidate_observation. That number
    has to be durable: candidates leave the broker's endpoint once they fill or
    cancel, and a later observation finding none of them is not evidence that
    none were ours (round-5 #5). The override only ever rises while the order is
    unresolved; only a human resolution clears it.

    A broker read that raises is reported and skipped, never fatal, and leaves
    its row untouched. One unreadable order must not hide the rest, and it must
    never look like "no candidate exists" -- that is what makes a
    `confirmed_absent` resolution, which releases capital, look justified.
    """
    now = now or datetime.now(timezone.utc)
    lookback = timedelta(minutes=int(getattr(config, "resolve_lookback_min", 30) or 30))

    with get_cursor(config.db_path) as conn:
        unresolved = queries.get_orders_by_status(conn, UNRESOLVED_ORDER_STATUSES)

    if not unresolved:
        return "No unresolved submissions. Every order in the ledger is accounted for."

    sections = [f"{len(unresolved)} unresolved submission(s) — REPORT ONLY, nothing below is resolved:"]
    for order in unresolved:
        submitted = _parse_utc(order["submitted_at"]) or now
        own = order_commitment({**order, "reserved_notional_override": None})
        header = (
            f"\n  Order #{order['id']} — {order['ticker']} {order['side']} "
            f"{float(order['requested_shares']):g} @ ${float(order['limit_price'] or order['reference_price']):.2f} "
            f"(${own:,.2f}), submitted {order['submitted_at']}"
        )

        try:
            candidates = find_recent_orders(
                config, symbol=order["ticker"], side=order["side"],
                since=submitted - lookback, until=now, client=client,
            )
        except Exception as exc:
            # Reported, never raised, and the row is left exactly as it was.
            logger.warning("Candidate search failed for order %s: %s", order["id"], exc)
            sections.append(
                f"{header}\n    ! broker read FAILED ({exc}). Row left unresolved; "
                "capital stays reserved. Do not resolve on a failed read."
            )
            continue

        with get_cursor(config.db_path) as conn:
            reservation = queries.record_candidate_observation(conn, order["id"], candidates)

        if candidates:
            lines = "\n".join(_describe_candidate(order, c) for c in candidates)
            body = (
                f"\n    {len(candidates)} candidate(s) found — matching SHAPE, not proof of provenance:\n"
                f"{lines}\n    reserved worst-case: ${reservation:,.2f}"
            )
        else:
            body = (
                "\n    No candidate found in the window. This is NOT proof the order "
                f"does not exist — it may have aged out. Reserved: ${reservation:,.2f}"
            )

        sections.append(
            f"{header}{body}\n"
            f"    Resolve with: /resolve order_id:{order['id']} "
            f"resolution:<adopt|confirmed_absent|keep_blocked> evidence:<what you checked>"
        )

    return "\n".join(sections)


async def alert_stuck_orders(bot, config,
                             now: datetime | None = None) -> int:
    """Nag about unresolved submissions older than `stuck_approval_alert_h`.

    Returns how many alerts were sent.

    This repeats on EVERY scan, deliberately. Guard 11 blocks new buys and sells
    of the order's ticker for as long as the row is unresolved, and only a human
    running `/resolve` can clear it. Alerting once would let a missed message
    turn into a symbol that is blocked indefinitely with nothing ever mentioning
    it again — which is the exact failure this whole path exists to remove.

    Never raises. It runs at scan start, and a failing nag must not abort the
    scan it precedes.
    """
    now = now or datetime.now(timezone.utc)
    threshold = timedelta(hours=int(getattr(config, "stuck_approval_alert_h", 24) or 24))

    try:
        with get_cursor(config.db_path) as conn:
            unresolved = queries.get_orders_by_status(conn, UNRESOLVED_ORDER_STATUSES)
    except Exception as exc:
        logger.error("Stuck-order check could not read the ledger: %s", exc)
        return 0

    sent = 0
    for order in unresolved:
        submitted = _parse_utc(order["submitted_at"])
        if submitted is None or now - submitted < threshold:
            continue

        age_h = (now - submitted).total_seconds() / 3600.0
        reserved = order_commitment(order)
        try:
            await bot.send_ops_alert(
                f"⚠️ Order #{order['id']} ({order['ticker']} {order['side']}) has been "
                f"unresolved for {age_h:.0f}h. ${reserved:,.2f} stays reserved and "
                f"{order['ticker']} is blocked from new orders until this is cleared.\n"
                f"Run `/resolve` to see candidate broker orders, then "
                f"`/resolve order_id:{order['id']} "
                f"resolution:<adopt|confirmed_absent|keep_blocked> evidence:<what you checked>`"
            )
            sent += 1
        except Exception as exc:
            logger.error("Could not alert on stuck order %s: %s", order["id"], exc)

    return sent
