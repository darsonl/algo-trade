from __future__ import annotations
import logging

import schwab
from schwab.orders.equities import equity_buy_market, equity_sell_market
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)


def build_market_buy(ticker: str, shares: int) -> dict:
    """Return the JSON spec for a market buy order (no network call)."""
    return equity_buy_market(ticker, shares).build()


def build_market_sell(ticker: str, shares: int) -> dict:
    """Return the JSON spec for a market sell order (no network call)."""
    return equity_sell_market(ticker, shares).build()


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
        instrument = pos.get("instrument", {})
        symbol = instrument.get("symbol")
        if not symbol:
            continue  # Skip non-equity / cash positions
        result.append({
            "symbol": symbol,
            "quantity": pos.get("longQuantity", 0.0),
            "avg_price": pos.get("averagePrice", 0.0),
            "market_value": pos.get("marketValue", 0.0),
        })
    return result


@_retry
def _call_place_order(client, account_hash: str, spec) -> object:
    return client.place_order(account_hash, spec)


def place_order(ticker: str, shares: int, config, client=None) -> str:
    """
    Place a market buy order via the Schwab API.
    Returns the order ID string on success, or raises RuntimeError on failure.
    """
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)

    spec = build_market_buy(ticker, shares)
    try:
        resp = _call_place_order(client, config.schwab_account_hash, spec)
        order_id = resp.headers.get("Location", "").split("/")[-1]
        logger.info("Placed order %s: %s x%d", order_id, ticker, shares)
        return order_id or None
    except Exception as exc:
        logger.error("Order placement failed for %s: %s", ticker, exc)
        raise RuntimeError(f"Order placement failed for {ticker}: {exc}") from exc


def place_sell_order(ticker: str, shares: int, config, client=None) -> str:
    """Place a market sell order via the Schwab API.

    Returns the order ID string on success, or raises RuntimeError on failure.
    Mirrors place_order but uses build_market_sell instead of build_market_buy.
    """
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)

    spec = build_market_sell(ticker, shares)
    try:
        resp = _call_place_order(client, config.schwab_account_hash, spec)
        order_id = resp.headers.get("Location", "").split("/")[-1]
        logger.info("Placed sell order %s: %s x%d", order_id, ticker, shares)
        return order_id or None
    except Exception as exc:
        logger.error("Sell order placement failed for %s: %s", ticker, exc)
        raise RuntimeError(f"Sell order placement failed for {ticker}: {exc}") from exc


def get_positions(config, client=None) -> list[dict]:
    """Return current account positions as a list of dicts."""
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)

    resp = client.get_account(
        config.schwab_account_hash,
        fields=[schwab.Client.Account.Fields.POSITIONS],
    )
    return parse_positions(resp.json())
