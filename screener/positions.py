"""Position summary helper -- fetches live prices and computes P&L for open positions."""

import yfinance as yf
from database.queries import get_open_positions


def get_position_summary(db_path: str) -> list[dict]:
    """Fetch current price for each open position via yfinance and compute P&L%.

    Returns list of dicts with keys: ticker, shares, avg_cost_usd, current_price, pnl_pct.
    Falls back to last_price from DB if yfinance fails. P&L% is None if no price available.
    """
    positions = get_open_positions(db_path)
    results = []
    for pos in positions:
        ticker = pos["ticker"]
        try:
            current_price = yf.Ticker(ticker).fast_info.last_price
        except Exception:
            current_price = pos["last_price"]  # may be None
        pnl_pct = None
        pnl_usd = None
        if current_price and pos["avg_cost_usd"]:
            pnl_pct = (current_price - pos["avg_cost_usd"]) / pos["avg_cost_usd"]
            pnl_usd = (current_price - pos["avg_cost_usd"]) * pos["shares"]
        results.append({
            "ticker": ticker,
            "shares": pos["shares"],
            "avg_cost_usd": pos["avg_cost_usd"],
            "current_price": current_price,
            "pnl_pct": pnl_pct,
            "pnl_usd": pnl_usd,
        })
    return results
