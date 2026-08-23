"""The screen price: one price, one meaning, for every candidate.

`reference_price` used to be recorded only at the four post-technical exits,
taken from `closes.iloc[-1]`. That left 78% of the funnel unmarkable, and worse,
it priced the rejected and passing cohorts from DIFFERENT sources -- yfinance
history is auto-adjusted and fetched minutes later than `.info`, so the two are
not the same quantity and a cohort comparison across them measures the
difference in provenance as much as the difference in outcome.

Every post-`.info` outcome now takes its price from the SAME dict by the SAME
policy. Equal staleness across cohorts cancels in a comparison; unequal pricing
does not.
"""
import math

from screener.fundamentals import screen_price


def test_current_price_is_preferred():
    assert screen_price({"currentPrice": 195.9, "regularMarketPrice": 1.0}) == (
        195.9, "info.currentPrice")


def test_regular_market_price_is_the_fallback():
    assert screen_price({"regularMarketPrice": 195.9}) == (
        195.9, "info.regularMarketPrice")


def test_previous_close_is_NOT_used():
    """A previous close is a DIFFERENT SESSION.

    Silently substituting it moves the holding-period start back a day, so the
    return would cover a window the benchmark does not. Better to record no
    price -- an unpriced row is visibly missing; a wrong one is not.
    """
    assert screen_price({"previousClose": 195.9}) == (None, None)


def test_a_missing_price_is_not_an_error():
    assert screen_price({}) == (None, None)
    assert screen_price({"currentPrice": None}) == (None, None)


def test_non_positive_prices_are_refused():
    """Zero passes `IS NOT NULL`, so it would make the row eligible for marking
    and then yield NULL from compute_return -- four wasted fetches and four
    permanently unusable marks. Same rule as refusing to book a zero fill."""
    assert screen_price({"currentPrice": 0}) == (None, None)
    assert screen_price({"currentPrice": 0.0}) == (None, None)
    assert screen_price({"currentPrice": -5.0}) == (None, None)


def test_booleans_are_refused():
    """`isinstance(True, int)` is True in Python, so a naive numeric check
    accepts a bool and stores a price of 1.0."""
    assert screen_price({"currentPrice": True}) == (None, None)
    assert screen_price({"currentPrice": False}) == (None, None)


def test_non_finite_prices_are_refused():
    """NaN and infinity ARE floats. NaN in particular compares False to
    everything, so it would survive a `> 0` test written the obvious way."""
    assert screen_price({"currentPrice": float("nan")}) == (None, None)
    assert screen_price({"currentPrice": float("inf")}) == (None, None)
    assert screen_price({"currentPrice": float("-inf")}) == (None, None)


def test_a_numeric_string_is_refused():
    """yfinance returns JSON; a string here means the field changed shape.
    Coercing it would hide that."""
    assert screen_price({"currentPrice": "195.9"}) == (None, None)


def test_the_price_is_a_plain_float():
    """numpy scalars and Decimals reach the JSON serializer otherwise."""
    from decimal import Decimal
    price, source = screen_price({"currentPrice": Decimal("195.9")})
    assert price is None and source is None  # Decimal is not a float/int

    price, source = screen_price({"currentPrice": 196})
    assert isinstance(price, float) and price == 196.0


# --- totality: this runs OUTSIDE the recorder's safety net ---

def test_it_never_raises_for_any_input():
    """This is called as an ARGUMENT to `_record_shadow`, so it is evaluated
    before that wrapper's try block. A raise here would turn a genuine
    fundamental rejection into an `error` observation and could fire an ops
    alert -- instrumentation corrupting the data it exists to record.

    Totality is therefore a contract, not a nicety.
    """
    class Hostile:
        def __getitem__(self, k):
            raise RuntimeError("boom")

        def get(self, *a, **kw):
            raise RuntimeError("boom")

    for bad in (None, [], "not a dict", 42, object(), Hostile()):
        assert screen_price(bad) == (None, None)


def test_a_dict_whose_value_explodes_on_comparison_is_survived():
    class Explosive:
        def __gt__(self, other):
            raise ValueError("nope")

        def __float__(self):
            raise ValueError("nope")

    assert screen_price({"currentPrice": Explosive()}) == (None, None)


def test_nan_never_reaches_the_database_via_math_isnan_ordering():
    """Regression guard: `x > 0` is False for NaN, so ordering the checks the
    other way still refuses it -- but only if the finite check exists at all."""
    price, _ = screen_price({"currentPrice": float("nan")})
    assert price is None or not math.isnan(price)
