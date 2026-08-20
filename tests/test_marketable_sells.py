"""Sells become marketable limits priced through the bid (round-5 #9, slice 2).

Sells were market orders. Spec §6 replaces them with a limit priced THROUGH the
bid, so the order still behaves like a market order for fill purposes while
carrying a worst-case price the guards validated.

The instrument is matched to the trigger that actually exists (RSI + MACD, a
momentum exit): a missed sell holds the position through the decline that fired
the signal. Buys keep the passive `quote * (1 + buffer)` limit — that asymmetry
is deliberate and documented so nobody "fixes" it into symmetry.

What is left here is the pure spec construction. `place_marketable_sell_order`
was deleted as an orphan (step 6's tail) once the sell approval path priced and
submitted the order itself, and the tests that drove the broker through it went
with it. Nothing it proved is unproven — every claim has a home, listed at the
bottom of this file.
"""
from schwab_client.orders import build_marketable_sell


# ─── Order construction ──────────────────────────────────────────────────────


def test_marketable_sell_is_a_limit_order():
    spec = build_marketable_sell("AAPL", 10, "99.50")

    leg = spec["orderLegCollection"][0]
    assert spec["orderType"] == "LIMIT"
    assert leg["instruction"] == "SELL"
    assert leg["quantity"] == 10


def test_marketable_sell_carries_the_limit_price():
    spec = build_marketable_sell("AAPL", 10, "99.50")
    assert spec["price"] == "99.50"


def test_marketable_sell_is_a_day_order():
    """DAY, not GTC: a marketable limit that fails to fill today has missed the
    move it was reacting to. Leaving it resting for weeks would sell into an
    unrelated future market."""
    spec = build_marketable_sell("AAPL", 10, "99.50")
    assert spec["duration"] == "DAY"


# ─── Where the retired coverage went ─────────────────────────────────────────
#
# Two approval-path tests moved to tests/test_sell_approval_ledger.py when the
# sell path was rewired onto the guard table. The rest went when
# `place_marketable_sell_order` itself was deleted. Each behaviour it asserted
# is checked somewhere the PRODUCTION path actually runs:
#
#   priced through the bid      -> test_quotes.py::test_sell_limit_is_priced_below_the_bid
#                                  test_sell_approval_ledger.py::
#                                    test_the_order_is_priced_from_the_quote_the_guards_saw
#                                  (which also pins DAY duration and the exact
#                                   price, neither of which assert_called_once saw)
#   bid, never the ask          -> test_quotes.py::test_sell_limit_is_priced_below_the_bid
#                                  + test_sell_limit_with_zero_buffer_is_the_bid_itself
#   unusable quote blocks it    -> test_quotes.py::test_empty_payload_raises,
#                                    test_missing_symbol_key_raises
#                                  test_sell_approval_ledger.py::
#                                    test_no_usable_quote_refuses_the_sell
#   stale quote blocks it       -> test_quotes.py::test_fetch_rejects_a_stale_quote
#   zero bid blocks it          -> test_quotes.py::
#                                    test_non_positive_or_non_numeric_bid_raises,
#                                    test_sell_limit_refuses_a_non_positive_bid
#   a halt blocks it            -> test_sell_approval_ledger.py::
#                                    test_a_halted_switch_refuses_the_sell
#                                  test_kill_switch_wiring.py sink tests
#   returns the broker order id -> test_submission_outcomes.py (classify_submission)
#
# The one claim that did NOT survive is `test_quote_is_not_fetched_when_trading_is_halted`
# — the deleted function checked the switch BEFORE the quote round-trip, while
# the approval path fetches the quote first and reads the switch after. That is
# a cost ordering, not a safety property: the guards and the submission gate
# still refuse the sell. It is not re-asserted here because it is no longer true.
