"""Capital accounting for orders — the numbers both ceilings are computed from.

Pure functions over an order row dict, so every rule here is testable without
SQLite or a broker.

Rules encoded, and the review findings behind them:

- Open orders commit their LIMIT price, not the reference quote (round-4 #5).
  An order can fill at quote x (1 + buffer); reserving the quote lets a second
  order through at the ceiling boundary when both can fill above the cap.

- A terminal order keeps what actually filled (round-4 #6). A 10-share order
  that fills 4 then cancels must not release all 10 shares' worth of budget.

- When fill data has never been observed, a terminal order keeps its FULL
  commitment (round-5 #1). filled_* columns default to 0, and 0 is
  indistinguishable from "filled nothing" unless we track whether anyone looked.
  Releasing on an unobserved zero is the fail-open direction.

- rejected / submit_failed are the exception: the broker definitively refused,
  so no capital moved regardless of observation.

- Portfolio exposure uses remaining_buy_reservation, NOT order_commitment
  (round-5). Broker position market value already includes filled shares, so
  adding total commitment double-counts partial fills and can wedge trading.
"""
import pytest

from database.order_accounting import (
    COMMITTING_ORDER_STATUSES,
    BLOCKING_ORDER_STATUSES,
    TERMINAL_ORDER_STATUSES,
    order_commitment,
    remaining_buy_reservation,
)


def _order(**kw):
    row = {
        "status": "submitted",
        "side": "buy",
        "requested_shares": 10.0,
        "reference_price": 100.0,
        "limit_price": 100.5,
        "filled_shares": 0.0,
        "filled_notional": 0.0,
        "fills_observed": 0,
    }
    row.update(kw)
    return row


# --- open orders price at the limit (round-4 #5) ---

def test_open_order_commits_the_limit_price_not_the_reference():
    assert order_commitment(_order()) == pytest.approx(1005.0)


def test_open_order_falls_back_to_reference_when_no_limit():
    assert order_commitment(_order(limit_price=None)) == pytest.approx(1000.0)


def test_partially_filled_order_commits_fills_plus_remaining_at_limit():
    row = _order(status="partially_filled", filled_shares=4.0,
                 filled_notional=398.0, fills_observed=1)
    # 398 already spent + 6 shares still working at 100.5
    assert order_commitment(row) == pytest.approx(398.0 + 603.0)


def test_overfill_never_produces_negative_remaining():
    row = _order(status="partially_filled", filled_shares=12.0,
                 filled_notional=1200.0, fills_observed=1)
    assert order_commitment(row) == pytest.approx(1200.0)


# --- terminal orders keep what filled (round-4 #6) ---

def test_observed_cancel_after_partial_fill_keeps_only_the_filled_amount():
    row = _order(status="cancelled", filled_shares=4.0,
                 filled_notional=398.0, fills_observed=1)
    assert order_commitment(row) == pytest.approx(398.0)


def test_observed_cancel_with_no_fill_releases_everything():
    row = _order(status="cancelled", filled_shares=0.0,
                 filled_notional=0.0, fills_observed=1)
    assert order_commitment(row) == 0.0


def test_filled_order_commits_its_actual_fill_notional():
    row = _order(status="filled", filled_shares=10.0,
                 filled_notional=1002.0, fills_observed=1)
    assert order_commitment(row) == pytest.approx(1002.0)


# --- unobserved fill data fails CLOSED (round-5 #1) ---

def test_unobserved_cancel_retains_full_commitment():
    """0 filled shares that nobody has verified is not evidence of no fill."""
    row = _order(status="cancelled", fills_observed=0)
    assert order_commitment(row) == pytest.approx(1005.0)


def test_unobserved_filled_status_retains_full_commitment():
    row = _order(status="filled", fills_observed=0)
    assert order_commitment(row) == pytest.approx(1005.0)


@pytest.mark.parametrize("status", ["rejected", "submit_failed"])
def test_definitive_refusal_releases_even_unobserved(status):
    """The broker refused it outright, so no capital moved regardless."""
    assert order_commitment(_order(status=status, fills_observed=0)) == 0.0


def test_submit_unknown_commits_the_full_limit_amount():
    """The order MAY exist. Assuming otherwise is the fail-open direction."""
    assert order_commitment(_order(status="submit_unknown")) == pytest.approx(1005.0)


# --- portfolio reservation excludes what the broker already reports ---

def test_remaining_reservation_excludes_filled_shares():
    """Broker market value already counts filled shares; counting them here too
    double-charges the portfolio ceiling."""
    row = _order(status="partially_filled", filled_shares=4.0,
                 filled_notional=398.0, fills_observed=1)
    assert remaining_buy_reservation(row) == pytest.approx(603.0)


def test_remaining_reservation_is_zero_for_a_terminal_order():
    row = _order(status="filled", filled_shares=10.0,
                 filled_notional=1002.0, fills_observed=1)
    assert remaining_buy_reservation(row) == 0.0


def test_unobserved_terminal_order_still_reserves_the_remainder():
    row = _order(status="cancelled", fills_observed=0)
    assert remaining_buy_reservation(row) == pytest.approx(1005.0)


def test_remaining_reservation_ignores_sells():
    assert remaining_buy_reservation(_order(side="sell")) == 0.0


# --- NULL columns must not crash the ceiling computation ---

def test_null_fill_columns_are_treated_as_zero():
    row = _order(filled_shares=None, filled_notional=None)
    assert order_commitment(row) == pytest.approx(1005.0)


# --- the status sets ---

def test_submit_unknown_commits_capital_and_blocks_the_symbol():
    assert "submit_unknown" in COMMITTING_ORDER_STATUSES
    assert "submit_unknown" in BLOCKING_ORDER_STATUSES


def test_submit_unknown_is_never_terminal():
    """Nothing may sweep it away automatically; only an operator resolves it."""
    assert "submit_unknown" not in TERMINAL_ORDER_STATUSES


def test_every_status_is_either_committing_or_terminal():
    """No status may fall through both sets and become invisible to the ceilings."""
    known = set(COMMITTING_ORDER_STATUSES) | set(TERMINAL_ORDER_STATUSES)
    assert set(BLOCKING_ORDER_STATUSES) <= known
