"""Suite-wide fixtures.

The only one here pins the exchange calendar, because without it the entire
scan suite passes Monday to Friday and fails at weekends and on market holidays.
"""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _assume_a_trading_session():
    """Every scan test runs as though today is a trading day.

    `run_scan`/`run_scan_etf` consult the real XNYS calendar and return early on
    a non-session. That is correct in production and intolerable in a test suite:
    it makes ~36 tests that have nothing to do with the calendar pass or fail
    according to the day they are run on. This was not hypothetical -- they all
    went red the moment the guard landed, on a Sunday.

    Pinned centrally rather than in each test for the same reason every
    time-dependent query in this project takes an `instant`: the clock is not a
    test input. Tests that ARE about the guard override it locally, and because
    this fixture is applied first, their patch wins.

    `main.is_trading_session` is patched, not `market_time.is_trading_session`,
    so `tests/test_market_time.py` still exercises the real calendar.
    """
    with patch("main.is_trading_session", return_value=True):
        yield
