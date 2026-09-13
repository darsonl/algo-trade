"""Suite-wide fixtures.

Each pins something outside the code that a test must not depend on: the
exchange calendar (the scan suite passed Monday to Friday and failed at
weekends) and the Schwab token file on this machine (the scan suite passed here
and failed in CI, which has none).
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


@pytest.fixture(autouse=True)
def _assume_a_fresh_schwab_login():
    """Every scan test runs as though the Schwab login is valid.

    Both scans post `schwab_login_warning` when the token is missing or near
    expiry. That reads a real file, `schwab_token.json`, so scan tests that
    count ops alerts passed on a machine with a fresh token and failed on one
    without -- CI has none, and PR #51 went red on exactly three of them.

    Same shape as the calendar pin: `main.schwab_login_warning` is patched, not
    `schwab_client.auth`, so `tests/test_schwab_login.py` still exercises the
    real function, and tests that ARE about the alert patch it locally and win.
    """
    with patch("main.schwab_login_warning", return_value=None):
        yield
