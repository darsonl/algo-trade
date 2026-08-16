"""The approval preflight guard table (spec v4 §8, build step 7).

Twelve guards, evaluated in a fixed order. The order is load-bearing twice over:

  * Guard 1 is first so an unauthorized clicker learns nothing about the book.
    Rejection messages are a side channel.
  * Guard 5 precedes every guard that consumes broker data. If 5 ran after 9,
    guard 9 would already have evaluated against an empty list -- a broker
    outage would read as "the account holds nothing" and OPEN the ceiling.
    That is the exact defect that made get_positions parse a 401 as [].

`None` and `[]` are therefore different inputs everywhere in this module:
None means "the read failed", [] means "the read succeeded and there is
nothing". Absence of data is not data.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from risk.preflight import (
    BrokerSnapshot,
    Decision,
    TradeRequest,
    check_authorization,
    evaluate_trade,
)
from schwab_client.quotes import Quote

NOW = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)  # 11:00 ET Mon, mid-session
AFTER_HOURS = datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc)  # 21:00 ET Mon


def _config(**overrides):
    base = dict(
        allowed_discord_user_ids="1001,1002",
        discord_guild_id=500,
        discord_channel_id=900,
        max_position_size_usd=500.0,
        max_portfolio_usd=20000.0,
        max_daily_notional_usd=2000.0,
        approval_price_tolerance_pct=2.0,
        approval_slippage_buffer_pct=0.5,
        quote_max_age_s=30,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _quote(bid=99.0, ask=100.0, age_s=1.0, symbol="AAPL", now=NOW):
    return Quote(
        symbol=symbol, bid=bid, ask=ask, last=99.5,
        quote_time=now - timedelta(seconds=age_s),
    )


def _buy(**overrides):
    base = dict(
        side="buy", ticker="AAPL", scan_price=100.0, rec_id=1,
        user_id=1001, guild_id=500, channel_id=900,
        expires_at=NOW + timedelta(hours=1),
    )
    base.update(overrides)
    return TradeRequest(**base)


def _sell(**overrides):
    base = dict(
        side="sell", ticker="AAPL", scan_price=100.0, rec_id=1, shares=5.0,
        user_id=1001, guild_id=500, channel_id=900,
        expires_at=NOW + timedelta(hours=1),
    )
    base.update(overrides)
    return TradeRequest(**base)


def _broker(positions=(), working_orders=()):
    return BrokerSnapshot(
        positions=None if positions is None else list(positions),
        working_orders=None if working_orders is None else list(working_orders),
    )


def _evaluate(request, **overrides):
    kwargs = dict(
        quote=_quote(),
        broker=_broker(),
        local_orders=[],
        day_notional=0.0,
        trading_enabled=True,
        config=_config(),
        now=NOW,
    )
    kwargs.update(overrides)
    return evaluate_trade(request, **kwargs)


# --- the happy path, so every rejection below means something ---

def test_a_clean_buy_is_allowed():
    decision = _evaluate(_buy())
    assert decision.allowed
    assert decision.reason_code is None


def test_an_allowed_buy_reports_the_limit_price_and_share_count():
    """The caller must not recompute these -- guards 7-9 already priced at the
    limit, and a second computation could disagree with what was checked."""
    decision = _evaluate(_buy())
    assert decision.limit_price == pytest.approx(100.5)  # ask + 0.5%
    assert decision.shares == 4                          # floor(500 / 100.5)


# --- guard 1: unauthorized ---

def test_unknown_user_is_rejected():
    assert check_authorization(_buy(user_id=999), _config()).reason_code == "unauthorized"


def test_empty_allowlist_denies_everyone():
    """Deny all, never allow all."""
    got = check_authorization(_buy(), _config(allowed_discord_user_ids=""))
    assert got.reason_code == "unauthorized"


def test_wrong_guild_is_rejected():
    assert check_authorization(_buy(guild_id=4), _config()).reason_code == "unauthorized"


def test_wrong_channel_is_rejected():
    assert check_authorization(_buy(channel_id=4), _config()).reason_code == "unauthorized"


def test_authorized_user_returns_none():
    assert check_authorization(_buy(), _config()) is None


def test_authorization_is_checked_before_anything_else():
    """An unauthorized clicker must not be able to distinguish a halted bot, an
    expired recommendation, or a blown ceiling. All of them look identical."""
    decision = _evaluate(
        _buy(user_id=999, expires_at=NOW - timedelta(hours=1)),
        trading_enabled=False,
        quote=None,
        broker=_broker(positions=None, working_orders=None),
        day_notional=99999.0,
    )
    assert decision.reason_code == "unauthorized"


# --- guard 2: trading_disabled ---

def test_halted_trading_is_rejected():
    assert _evaluate(_buy(), trading_enabled=False).reason_code == "trading_disabled"


# --- guard 3: expired ---

def test_expired_recommendation_is_rejected():
    assert _evaluate(_buy(expires_at=NOW - timedelta(seconds=1))).reason_code == "expired"


def test_expiry_is_inclusive():
    """`now >= expires_at` -- exactly at the boundary is expired, not live."""
    assert _evaluate(_buy(expires_at=NOW)).reason_code == "expired"


# --- guard 4: quote_unavailable ---

def test_missing_quote_is_rejected():
    assert _evaluate(_buy(), quote=None).reason_code == "quote_unavailable"


def test_stale_quote_is_rejected_during_regular_hours():
    assert _evaluate(_buy(), quote=_quote(age_s=120)).reason_code == "quote_unavailable"


def test_stale_quote_is_accepted_outside_regular_hours():
    """A 30-second rule would reject every pre-open approval, which is exactly
    when this system is designed to be used. After hours the limit price, not
    quote freshness, is the binding control."""
    decision = _evaluate(
        _buy(expires_at=AFTER_HOURS + timedelta(hours=1)),
        quote=_quote(age_s=7200, now=AFTER_HOURS),
        now=AFTER_HOURS,
    )
    assert decision.allowed


# --- guard 5: broker_unavailable, and its ordering ---

def test_failed_position_read_is_rejected_not_treated_as_empty():
    decision = _evaluate(_buy(), broker=_broker(positions=None))
    assert decision.reason_code == "broker_unavailable"


def test_failed_working_order_read_is_rejected():
    decision = _evaluate(_buy(), broker=_broker(working_orders=None))
    assert decision.reason_code == "broker_unavailable"


def test_broker_failure_is_reported_before_the_exposure_guard_can_pass():
    """The ordering regression. With positions unreadable AND an order that
    would breach the ceiling, the answer must be broker_unavailable -- not a
    silent pass caused by evaluating exposure against an empty list."""
    decision = _evaluate(
        _buy(),
        broker=_broker(positions=None),
        config=_config(max_portfolio_usd=1.0),
    )
    assert decision.reason_code == "broker_unavailable"


def test_genuinely_empty_broker_data_is_not_a_failure():
    assert _evaluate(_buy(), broker=_broker(positions=[], working_orders=[])).allowed


# --- guard 6: price_drift ---

def test_price_drift_beyond_tolerance_is_rejected():
    decision = _evaluate(_buy(scan_price=100.0), quote=_quote(bid=109.0, ask=110.0))
    assert decision.reason_code == "price_drift"


def test_price_drift_within_tolerance_is_allowed():
    assert _evaluate(_buy(scan_price=100.0), quote=_quote(bid=100.5, ask=101.0)).allowed


def test_drift_downward_is_also_rejected():
    """A 10% gap down is not a bargain, it is news the analyst never saw."""
    decision = _evaluate(_buy(scan_price=100.0), quote=_quote(bid=89.0, ask=90.0))
    assert decision.reason_code == "price_drift"


def test_price_drift_does_not_apply_to_sells():
    decision = _evaluate(
        _sell(scan_price=100.0), quote=_quote(bid=80.0, ask=81.0),
        broker=_broker(positions=[{"symbol": "AAPL", "quantity": 10.0, "market_value": 800.0}]),
    )
    assert decision.allowed


# --- guard 7: size_zero, priced at the limit ---

def test_size_zero_is_rejected():
    decision = _evaluate(_buy(), config=_config(max_position_size_usd=50.0))
    assert decision.reason_code == "size_zero"


def test_sizing_uses_the_limit_price_not_the_scan_price():
    """At scan 100.00 a 100.40 budget buys one share; at the 100.50 limit it
    buys none. The ceiling must be computed against what the order can cost."""
    decision = _evaluate(_buy(scan_price=100.0), config=_config(max_position_size_usd=100.4))
    assert decision.reason_code == "size_zero"


# --- guard 8: daily_notional ---

def test_daily_notional_breach_is_rejected():
    decision = _evaluate(_buy(), day_notional=1900.0, config=_config(max_daily_notional_usd=2000.0))
    assert decision.reason_code == "daily_notional"


def test_daily_notional_exactly_at_the_ceiling_is_allowed():
    decision = _evaluate(
        _buy(), day_notional=1598.0, config=_config(max_daily_notional_usd=2000.0)
    )
    assert decision.allowed  # 1598 + 4 * 100.5 == 2000.0


# --- guard 9: portfolio_exposure ---

def test_portfolio_exposure_counts_broker_market_value():
    decision = _evaluate(
        _buy(),
        broker=_broker(positions=[{"symbol": "MSFT", "quantity": 10.0, "market_value": 19800.0}]),
        config=_config(max_portfolio_usd=20000.0),
    )
    assert decision.reason_code == "portfolio_exposure"


def test_portfolio_exposure_counts_broker_working_orders():
    """A buy placed by hand in the Schwab app is working and unfilled: it is in
    no position and in no local row, but it will cost money."""
    decision = _evaluate(
        _buy(),
        broker=_broker(working_orders=[
            {"broker_order_id": "B1", "symbol": "MSFT", "notional": 19800.0}
        ]),
        config=_config(max_portfolio_usd=20000.0),
    )
    assert decision.reason_code == "portfolio_exposure"


def test_the_same_order_seen_locally_and_at_the_broker_is_counted_once():
    """Merged by broker id. Double-counting rejects legitimate trades -- the
    recoverable direction, but still wrong."""
    local = [{
        "side": "buy", "ticker": "MSFT", "status": "submitted", "broker_order_id": "B1",
        "requested_shares": 100.0, "limit_price": 150.0, "reference_price": 150.0,
        "filled_shares": 0.0, "filled_notional": 0.0, "fills_observed": 0,
    }]
    broker_view = [{"broker_order_id": "B1", "symbol": "MSFT", "notional": 15000.0}]
    decision = _evaluate(
        _buy(), local_orders=local, broker=_broker(working_orders=broker_view),
        config=_config(max_portfolio_usd=15500.0),
    )
    assert decision.allowed, "15000 counted twice would breach 15500"


def test_a_local_order_not_yet_attached_to_a_broker_id_still_counts():
    """pending_submit has no broker id yet. It is not visible to the broker
    read, so dropping it would under-count exposure -- the open direction."""
    local = [{
        "side": "buy", "ticker": "MSFT", "status": "pending_submit", "broker_order_id": None,
        "requested_shares": 100.0, "limit_price": 150.0, "reference_price": 150.0,
        "filled_shares": 0.0, "filled_notional": 0.0, "fills_observed": 0,
    }]
    decision = _evaluate(
        _buy(), local_orders=local, config=_config(max_portfolio_usd=15100.0)
    )
    assert decision.reason_code == "portfolio_exposure"


# --- guard 10: duplicate_symbol ---

def test_existing_broker_position_blocks_a_second_buy():
    decision = _evaluate(
        _buy(ticker="AAPL"),
        broker=_broker(positions=[{"symbol": "AAPL", "quantity": 3.0, "market_value": 300.0}]),
    )
    assert decision.reason_code == "duplicate_symbol"


def test_existing_working_order_for_the_symbol_blocks_a_second_buy():
    decision = _evaluate(
        _buy(ticker="AAPL"),
        broker=_broker(working_orders=[
            {"broker_order_id": "B7", "symbol": "AAPL", "notional": 200.0}
        ]),
    )
    assert decision.reason_code == "duplicate_symbol"


def test_a_position_in_another_symbol_does_not_block():
    decision = _evaluate(
        _buy(ticker="AAPL"),
        broker=_broker(positions=[{"symbol": "MSFT", "quantity": 3.0, "market_value": 300.0}]),
    )
    assert decision.allowed


# --- guard 11: unresolved_order ---

def test_unresolved_order_blocks_a_buy():
    local = [{
        "side": "buy", "ticker": "AAPL", "status": "submit_unknown", "broker_order_id": None,
        "requested_shares": 1.0, "limit_price": 100.0, "reference_price": 100.0,
        "filled_shares": 0.0, "filled_notional": 0.0, "fills_observed": 0,
    }]
    assert _evaluate(_buy(), local_orders=local).reason_code == "unresolved_order"


def test_unresolved_order_blocks_a_sell_too():
    """Selling into an unknown order state can oversell."""
    local = [{
        "side": "buy", "ticker": "AAPL", "status": "submit_unknown", "broker_order_id": None,
        "requested_shares": 1.0, "limit_price": 100.0, "reference_price": 100.0,
        "filled_shares": 0.0, "filled_notional": 0.0, "fills_observed": 0,
    }]
    decision = _evaluate(
        _sell(), local_orders=local,
        broker=_broker(positions=[{"symbol": "AAPL", "quantity": 10.0, "market_value": 1000.0}]),
    )
    assert decision.reason_code == "unresolved_order"


def test_unresolved_order_in_another_symbol_does_not_block():
    local = [{
        "side": "buy", "ticker": "TSLA", "status": "submit_unknown", "broker_order_id": None,
        "requested_shares": 1.0, "limit_price": 100.0, "reference_price": 100.0,
        "filled_shares": 0.0, "filled_notional": 0.0, "fills_observed": 0,
    }]
    assert _evaluate(_buy(ticker="AAPL"), local_orders=local).allowed


# --- guard 12: sell_quantity ---

def test_selling_more_than_held_is_rejected():
    decision = _evaluate(
        _sell(shares=20.0),
        broker=_broker(positions=[{"symbol": "AAPL", "quantity": 10.0, "market_value": 1000.0}]),
    )
    assert decision.reason_code == "sell_quantity"


def test_selling_a_symbol_not_held_is_rejected():
    assert _evaluate(_sell(shares=1.0)).reason_code == "sell_quantity"


def test_selling_zero_shares_is_rejected():
    decision = _evaluate(
        _sell(shares=0.0),
        broker=_broker(positions=[{"symbol": "AAPL", "quantity": 10.0, "market_value": 1000.0}]),
    )
    assert decision.reason_code == "sell_quantity"


def test_selling_the_whole_holding_is_allowed():
    decision = _evaluate(
        _sell(shares=10.0),
        broker=_broker(positions=[{"symbol": "AAPL", "quantity": 10.0, "market_value": 1000.0}]),
    )
    assert decision.allowed


def test_a_sell_is_not_blocked_by_the_buy_only_guards():
    """Guards 6-10 are buy-only. A sell of a held symbol must not trip the
    duplicate-symbol or ceiling guards that its own position causes."""
    decision = _evaluate(
        _sell(shares=5.0),
        broker=_broker(positions=[{"symbol": "AAPL", "quantity": 10.0, "market_value": 99999.0}]),
        day_notional=99999.0,
        config=_config(max_portfolio_usd=1.0, max_daily_notional_usd=1.0),
    )
    assert decision.allowed


# --- the decision object itself ---

def test_a_rejection_carries_an_operator_readable_message():
    decision = _evaluate(_buy(), trading_enabled=False)
    assert not decision.allowed
    assert decision.message
    assert isinstance(decision, Decision)


# --- one Quote type, and the config the guards read ---

def test_quotes_module_reuses_the_preflight_quote_type():
    """Two structurally-identical Quote classes would pass every test here by
    duck typing and still be a latent bug: isinstance checks and any future
    field would silently diverge. quotes.py imports it from here, never the
    reverse -- preflight must not depend on schwab_client."""
    import risk.preflight as preflight
    import schwab_client.quotes as quotes

    assert quotes.Quote is preflight.Quote


def test_preflight_does_not_import_schwab_client():
    """Parsed imports, not raw text -- the module docstring names schwab_client
    precisely to explain the direction, and a substring check would flag it."""
    import ast
    import inspect

    import risk.preflight as preflight

    tree = ast.parse(inspect.getsource(preflight))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    offenders = {m for m in imported if m.split(".")[0] in {"schwab_client", "discord"}}
    assert not offenders, f"preflight must not import {offenders}"


def test_config_exposes_the_guard_thresholds():
    from config import Config

    config = Config()
    assert config.max_daily_notional_usd == 2000.0
    assert config.approval_price_tolerance_pct == 2.0
    assert config.discord_guild_id == 0
    assert config.allowed_discord_user_ids == ""      # deny all by default


def test_the_real_config_satisfies_the_guard_table():
    """The guards read config by attribute; a rename would only show up here."""
    from config import Config

    decision = evaluate_trade(
        _buy(user_id=1001, guild_id=0, channel_id=0),
        quote=_quote(), broker=_broker(), local_orders=[], day_notional=0.0,
        trading_enabled=True,
        config=Config(allowed_discord_user_ids="1001", discord_channel_id=0),
        now=NOW,
    )
    assert decision.allowed
