"""Broker reads must fail closed.

Round-3 finding C3 / spec v4 build step 3. `get_positions` called `resp.json()`
with no `raise_for_status`, so an HTTP error body — which is a perfectly valid
dict with no `securitiesAccount` key — flowed through `parse_positions`'s
`.get()` chains and came out as `[]`, i.e. "the account holds nothing."

That made a broker outage OPEN the guards that read exposure and holdings
instead of closing them. These tests pin the fail-closed behaviour.
"""
from types import SimpleNamespace

import httpx
import pytest

from schwab_client.orders import get_positions, parse_positions

_URL = "https://api.schwabapi.com/trader/v1/accounts/ABC123"


def _config():
    return SimpleNamespace(schwab_account_hash="ABC123")


def _response(status_code: int, payload) -> httpx.Response:
    """A real httpx.Response, so raise_for_status behaves exactly as in prod."""
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", _URL),
    )


class _Client:
    """Minimal stand-in for schwab.client.Client at the network boundary."""

    def __init__(self, response):
        self._response = response

    def get_account(self, account_hash, fields=None):
        return self._response


# --- parse_positions rejects a payload that is not an account ---

def test_parse_positions_raises_when_securities_account_absent():
    """An error body has no securitiesAccount key. It must not read as 'no positions'."""
    with pytest.raises(ValueError):
        parse_positions({"errors": [{"message": "Unauthorized"}]})


def test_parse_positions_raises_on_empty_payload():
    with pytest.raises(ValueError):
        parse_positions({})


def test_parse_positions_still_accepts_an_account_holding_nothing():
    """A real account with no open positions is empty, not an error."""
    assert parse_positions({"securitiesAccount": {}}) == []


# --- get_positions validates transport before parsing payload ---

@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_get_positions_raises_on_http_error(status):
    """A non-2xx must raise, never parse. This is the defect that opened the gate."""
    client = _Client(_response(status, {"errors": [{"message": "nope"}]}))
    with pytest.raises(httpx.HTTPStatusError):
        get_positions(_config(), client=client)


def test_get_positions_error_body_never_reads_as_empty_holdings():
    """The specific failure: a 401 must not return [] as if the account were empty."""
    client = _Client(_response(401, {"errors": [{"message": "Unauthorized"}]}))
    with pytest.raises(Exception) as exc_info:
        get_positions(_config(), client=client)
    assert not isinstance(exc_info.value, AssertionError)


def test_get_positions_returns_positions_on_success():
    payload = {
        "securitiesAccount": {
            "positions": [
                {
                    "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
                    "longQuantity": 10.0,
                    "averagePrice": 150.0,
                    "marketValue": 1750.0,
                }
            ]
        }
    }
    client = _Client(_response(200, payload))
    positions = get_positions(_config(), client=client)
    assert [p["symbol"] for p in positions] == ["AAPL"]


def test_get_positions_returns_empty_for_account_with_no_positions():
    client = _Client(_response(200, {"securitiesAccount": {}}))
    assert get_positions(_config(), client=client) == []
