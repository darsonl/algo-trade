from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


def build_market_buy(ticker: str, shares: int) -> dict:
    """Return the JSON spec for a market buy order (no network call)."""
    from schwab.orders.equities import equity_buy_market
    return equity_buy_market(ticker, shares).build()


def parse_positions(account_response: dict) -> list[dict]:
    """
    Extract a clean list of positions from a Schwab get_account response dict.

    Returns dicts with keys: symbol, quantity, avg_price, market_value.
    """
    raw_positions = (
        account_response
        .get("securitiesAccount", {})
        .get("positions", [])
    )
    result = []
    for pos in raw_positions:
        result.append({
            "symbol": pos["instrument"]["symbol"],
            "quantity": pos["longQuantity"],
            "avg_price": pos["averagePrice"],
            "market_value": pos["marketValue"],
        })
    return result


def place_order(ticker: str, shares: int, config, client=None) -> str | None:
    """
    Place a market buy order via the Schwab API.
    Returns the order ID string, or None if placement fails.
    """
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)

    spec = build_market_buy(ticker, shares)
    try:
        resp = client.place_order(config.schwab_account_hash, spec)
        order_id = resp.headers.get("Location", "").split("/")[-1]
        logger.info("Placed order %s: %s x%d", order_id, ticker, shares)
        return order_id or None
    except Exception as exc:
        logger.error("Order placement failed for %s: %s", ticker, exc)
        return None


def get_positions(config, client=None) -> list[dict]:
    """Return current account positions as a list of dicts."""
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)

    import schwab
    resp = client.get_account(
        config.schwab_account_hash,
        fields=[schwab.Client.Account.Fields.POSITIONS],
    )
    return parse_positions(resp.json())
