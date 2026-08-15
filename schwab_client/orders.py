from __future__ import annotations
import logging

from schwab.client import Client as SchwabClient
from schwab.orders.equities import equity_buy_limit, equity_buy_market, equity_sell_market
from schwab.orders.common import Duration
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)


def _checked(resp):
    """Validate the transport before anything parses the payload.

    A JSON error body is structurally a valid dict, so parsing first and
    inspecting later lets a 401/429/500 masquerade as data. Every broker READ
    goes through here.
    """
    resp.raise_for_status()
    return resp.json()


def build_market_buy(ticker: str, shares: int) -> dict:
    """Return the JSON spec for a market buy order (no network call)."""
    return equity_buy_market(ticker, shares).build()


def build_limit_buy(ticker: str, shares: int, limit_price_str: str) -> dict:
    """Return the JSON spec for a GTC limit buy order (no network call).

    CRITICAL: .set_duration(Duration.GOOD_TILL_CANCEL) is required.
    Without it, equity_buy_limit defaults to DAY — late approvals silently expire.
    """
    spec = equity_buy_limit(ticker, shares, limit_price_str)
    spec.set_duration(Duration.GOOD_TILL_CANCEL)
    return spec.build()


def build_market_sell(ticker: str, shares: int) -> dict:
    """Return the JSON spec for a market sell order (no network call)."""
    return equity_sell_market(ticker, shares).build()


def parse_positions(account_response: dict) -> list[dict]:
    """
    Extract a clean list of positions from a Schwab get_account response dict.

    Returns dicts with keys: symbol, quantity, avg_price, market_value, asset_type.
    asset_type is the Schwab instrument assetType (e.g. 'EQUITY', 'CASH_EQUIVALENT'),
    or '' when absent — reconciliation uses it to ignore cash/sweep instruments.
    """
    account = account_response.get("securitiesAccount")
    if not isinstance(account, dict):
        # An HTTP error body is a valid dict too. Without this check its missing
        # keys flow through the .get() chain below and come out as [] — "the
        # account holds nothing" — which makes a broker outage look like a clean
        # empty account and OPENS the exposure and holdings guards.
        raise ValueError(
            "Schwab account response has no 'securitiesAccount' object; "
            f"got keys {sorted(account_response)!r}"
        )

    raw_positions = account.get("positions", [])
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
            "asset_type": instrument.get("assetType", ""),
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


def place_limit_order(ticker: str, shares: int, limit_price: float, config, client=None) -> str:
    """Place a GTC limit buy order via the Schwab API.

    Takes limit_price as float and formats internally to f"{limit_price:.2f}" (D-03).
    Returns the order ID string on success, or raises RuntimeError on failure.
    Mirrors place_order structure exactly.
    """
    if client is None:
        from schwab_client.auth import get_client
        client = get_client(config)
    limit_price_str = f"{limit_price:.2f}"
    spec = build_limit_buy(ticker, shares, limit_price_str)
    try:
        resp = _call_place_order(client, config.schwab_account_hash, spec)
        order_id = resp.headers.get("Location", "").split("/")[-1]
        logger.info("Placed limit order %s: %s x%d @ %s", order_id, ticker, shares, limit_price_str)
        return order_id or None
    except Exception as exc:
        logger.error("Limit order placement failed for %s: %s", ticker, exc)
        raise RuntimeError(f"Limit order placement failed for {ticker}: {exc}") from exc


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
        fields=[SchwabClient.Account.Fields.POSITIONS],
    )
    return parse_positions(_checked(resp))
