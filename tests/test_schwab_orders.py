import pytest
from schwab_client.orders import build_market_buy, parse_positions


# --- build_market_buy ---

def test_build_market_buy_sets_symbol():
    spec = build_market_buy("AAPL", 5)
    leg = spec["orderLegCollection"][0]
    assert leg["instrument"]["symbol"] == "AAPL"


def test_build_market_buy_sets_quantity():
    spec = build_market_buy("AAPL", 5)
    leg = spec["orderLegCollection"][0]
    assert leg["quantity"] == 5


def test_build_market_buy_is_market_order():
    spec = build_market_buy("AAPL", 5)
    assert spec["orderType"] == "MARKET"


def test_build_market_buy_instruction_is_buy():
    spec = build_market_buy("AAPL", 5)
    leg = spec["orderLegCollection"][0]
    assert leg["instruction"] == "BUY"


def test_build_market_buy_asset_type_is_equity():
    spec = build_market_buy("JNJ", 3)
    leg = spec["orderLegCollection"][0]
    assert leg["instrument"]["assetType"] == "EQUITY"


# --- parse_positions ---

def _make_account_response(positions: list[dict]) -> dict:
    return {"securitiesAccount": {"positions": positions}}


def _make_position(symbol="AAPL", quantity=10.0, avg_price=150.0, market_value=1750.0):
    return {
        "instrument": {"symbol": symbol, "assetType": "EQUITY"},
        "longQuantity": quantity,
        "averagePrice": avg_price,
        "marketValue": market_value,
    }


def test_parse_positions_extracts_symbol():
    data = _make_account_response([_make_position("AAPL")])
    positions = parse_positions(data)
    assert positions[0]["symbol"] == "AAPL"


def test_parse_positions_extracts_quantity():
    data = _make_account_response([_make_position(quantity=10.0)])
    positions = parse_positions(data)
    assert positions[0]["quantity"] == 10.0


def test_parse_positions_extracts_avg_price():
    data = _make_account_response([_make_position(avg_price=175.50)])
    positions = parse_positions(data)
    assert positions[0]["avg_price"] == 175.50


def test_parse_positions_extracts_market_value():
    data = _make_account_response([_make_position(market_value=1800.0)])
    positions = parse_positions(data)
    assert positions[0]["market_value"] == 1800.0


def test_parse_positions_returns_empty_list_when_no_positions():
    data = _make_account_response([])
    assert parse_positions(data) == []


def test_parse_positions_handles_missing_positions_key():
    data = {"securitiesAccount": {}}
    assert parse_positions(data) == []


def test_parse_positions_handles_multiple_positions():
    data = _make_account_response([
        _make_position("AAPL", quantity=5.0),
        _make_position("JNJ", quantity=3.0),
    ])
    positions = parse_positions(data)
    assert len(positions) == 2
    symbols = {p["symbol"] for p in positions}
    assert symbols == {"AAPL", "JNJ"}
