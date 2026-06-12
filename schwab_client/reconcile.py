"""Position reconciliation — compare DB open positions against Schwab account positions.

Closes RISK-05: positions are recorded in the DB on broker acknowledgement (not
fill), and a DB write can fail after a live order succeeds. Both leave the DB
and the broker disagreeing about what is actually held. These helpers detect
that drift; they are pure functions and never mutate anything — reporting and
manual correction are deliberate (human-in-the-loop, same as trade approval).
"""
from __future__ import annotations

# Schwab assetTypes the bot could plausibly have bought (stocks and ETFs; ETFs
# appear as EQUITY or COLLECTIVE_INVESTMENT depending on API version). Broker
# holdings outside this set (cash sweeps, money market funds, bonds) are never
# bot-managed, so they are excluded from the "untracked" check to avoid a
# permanent false alarm in every report.
_EQUITY_LIKE_ASSET_TYPES = {"EQUITY", "ETF", "COLLECTIVE_INVESTMENT", ""}

_SHARE_TOLERANCE = 1e-6


def diff_positions(db_positions: list[dict], broker_positions: list[dict]) -> dict:
    """Compare DB open positions to broker positions and classify discrepancies.

    db_positions: rows with at least 'ticker' and 'shares'.
    broker_positions: parse_positions() output ('symbol', 'quantity', 'asset_type').

    Returns {"phantom": [...], "untracked": [...], "mismatched": [...]}:
    - phantom: in the DB but absent (or zero shares) at the broker — an
      unfilled limit order, a failed live order, or a manual sale.
    - untracked: equity-like broker holding with no DB row — a manual buy or
      a DB write that failed after the order succeeded.
    - mismatched: held on both sides but share counts differ — a partial fill
      or a manual trade.
    """
    broker_by_symbol = {p["symbol"].upper(): p for p in broker_positions}
    db_by_ticker = {p["ticker"].upper(): p for p in db_positions}

    phantom: list[dict] = []
    mismatched: list[dict] = []
    for ticker, pos in db_by_ticker.items():
        broker = broker_by_symbol.get(ticker)
        if broker is None or broker["quantity"] <= 0:
            phantom.append({"ticker": ticker, "db_shares": pos["shares"]})
        elif abs(broker["quantity"] - pos["shares"]) > _SHARE_TOLERANCE:
            mismatched.append({
                "ticker": ticker,
                "db_shares": pos["shares"],
                "broker_shares": broker["quantity"],
            })

    untracked = [
        {"ticker": symbol, "broker_shares": p["quantity"]}
        for symbol, p in broker_by_symbol.items()
        if symbol not in db_by_ticker
        and p["quantity"] > 0
        and p.get("asset_type", "") in _EQUITY_LIKE_ASSET_TYPES
    ]

    return {"phantom": phantom, "untracked": untracked, "mismatched": mismatched}


def format_reconciliation_report(diff: dict) -> str | None:
    """Render a diff_positions() result as a human-readable alert, or None when clean."""
    if not (diff["phantom"] or diff["untracked"] or diff["mismatched"]):
        return None

    lines = ["Position reconciliation found discrepancies vs Schwab:"]
    for p in diff["phantom"]:
        lines.append(
            f"- PHANTOM {p['ticker']}: DB shows {p['db_shares']:g} share(s), broker has none "
            "(unfilled limit order or manual sale?)"
        )
    for m in diff["mismatched"]:
        lines.append(
            f"- MISMATCH {m['ticker']}: DB shows {m['db_shares']:g} share(s), broker has "
            f"{m['broker_shares']:g} (partial fill or manual trade?)"
        )
    for u in diff["untracked"]:
        lines.append(
            f"- UNTRACKED {u['ticker']}: broker holds {u['broker_shares']:g} share(s) "
            "not tracked by the bot"
        )
    lines.append("No automatic changes made — review and correct positions manually.")
    return "\n".join(lines)
