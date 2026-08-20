"""EXECUTION_MODE replaces DRY_RUN + PAPER_TRADING (spec v4 step 1).

`paper_trading` was read in exactly one place — a startup warning — and gated
nothing, while *reading* like a safety layer. An operator setting
PAPER_TRADING=true and believing themselves protected was the failure this
removes. One env surface now says what the bot will do with an order.

`dry_run` survives as a DERIVED, ASSIGNABLE field rather than a read-only
property: 55 test sites set it to True specifically to stay off live Schwab,
and CLAUDE.md documents that as the required convention. Making it read-only
would silently strip protection from any site that was missed.
"""
import os
from unittest.mock import MagicMock

import pytest

from config import Config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Config reads env at CONSTRUCTION, so each test gets a clean slate."""
    for name in ("EXECUTION_MODE", "DRY_RUN", "PAPER_TRADING"):
        monkeypatch.delenv(name, raising=False)


def _valid(config):
    """Fill in the unrelated credential requirements so validate() reaches ours."""
    config.schwab_app_key = "k"
    config.schwab_app_secret = "s"
    config.discord_token = "t"
    config.analyst_api_key = "a"
    config.discord_channel_id = 1
    config.schwab_account_hash = "h"
    return config


# ─── The mode drives dry_run ─────────────────────────────────────────────────


def test_the_default_mode_is_dry_run():
    assert Config().execution_mode == "dry_run"


def test_the_default_mode_derives_dry_run_true():
    assert Config().dry_run is True


def test_live_derives_dry_run_false():
    os.environ["EXECUTION_MODE"] = "live"
    try:
        assert Config().dry_run is False
    finally:
        del os.environ["EXECUTION_MODE"]


def test_any_non_live_mode_derives_dry_run_true():
    """Fail closed on anything that is not exactly `live` — including a typo,
    which must not be read as permission to trade."""
    os.environ["EXECUTION_MODE"] = "liv"
    try:
        assert Config().dry_run is True
    finally:
        del os.environ["EXECUTION_MODE"]


def test_dry_run_stays_assignable():
    """55 test sites set it to stay off live Schwab. A read-only property would
    silently strip protection from any site that was missed."""
    config = Config()
    config.dry_run = False
    assert config.dry_run is False


def test_paper_trading_is_gone():
    """It gated nothing while reading like a safety layer."""
    assert not hasattr(Config(), "paper_trading")


# ─── Migration is loud ───────────────────────────────────────────────────────


def test_a_legacy_dry_run_variable_fails_validation():
    """Silently deriving the new value from the legacy one would reintroduce
    exactly the unopted-into safety this removes."""
    os.environ["DRY_RUN"] = "true"
    try:
        with pytest.raises(ValueError, match="EXECUTION_MODE"):
            _valid(Config()).validate()
    finally:
        del os.environ["DRY_RUN"]


def test_a_legacy_paper_trading_variable_fails_validation():
    os.environ["PAPER_TRADING"] = "true"
    try:
        with pytest.raises(ValueError, match="EXECUTION_MODE"):
            _valid(Config()).validate()
    finally:
        del os.environ["PAPER_TRADING"]


def test_the_migration_error_names_the_mode_the_old_settings_map_to():
    """An operator must not have to guess. DRY_RUN=false maps to live."""
    os.environ["DRY_RUN"] = "false"
    try:
        with pytest.raises(ValueError, match="EXECUTION_MODE=live"):
            _valid(Config()).validate()
    finally:
        del os.environ["DRY_RUN"]


def test_a_legacy_dry_run_true_maps_to_dry_run():
    os.environ["DRY_RUN"] = "true"
    try:
        with pytest.raises(ValueError, match="EXECUTION_MODE=dry_run"):
            _valid(Config()).validate()
    finally:
        del os.environ["DRY_RUN"]


def test_simulated_is_not_implemented():
    os.environ["EXECUTION_MODE"] = "simulated"
    try:
        with pytest.raises(NotImplementedError):
            _valid(Config()).validate()
    finally:
        del os.environ["EXECUTION_MODE"]


def test_an_unrecognised_mode_fails_startup():
    """Not silently treated as dry_run: a typo in the one variable that decides
    whether real money moves must stop the process, not be guessed at."""
    os.environ["EXECUTION_MODE"] = "liv"
    try:
        with pytest.raises(ValueError, match="liv"):
            _valid(Config()).validate()
    finally:
        del os.environ["EXECUTION_MODE"]


def test_a_valid_dry_run_config_passes_validation():
    _valid(Config()).validate()  # must not raise


# ─── The sink refuses unless BOTH mode signals agree ─────────────────────────
#
# Two signals exist for compatibility: execution_mode is the env surface,
# dry_run is what 55 tests set to stay off live Schwab. Requiring AGREEMENT
# means a disagreement fails CLOSED — the illegal execution_mode='dry_run' +
# dry_run=False state is blocked by the first clause, and a test that sets only
# dry_run=True is protected by the second.


@pytest.fixture
def live_db(tmp_path):
    from database.models import initialize_db
    from risk import kill_switch
    path = str(tmp_path / "t.db")
    initialize_db(path)
    kill_switch.init(path, env_default=True)
    return path


def _sink_config(db_path, mode="live", dry_run=None):
    config = Config()
    config.db_path = db_path
    config.schwab_account_hash = "hash"
    config.execution_mode = mode
    config.dry_run = (mode != "live") if dry_run is None else dry_run
    return config


SPEC = {"orderType": "LIMIT", "price": "100.00"}


def test_the_sink_submits_when_both_signals_say_live(live_db):
    from schwab_client import orders
    client = MagicMock()

    orders._call_place_order(client, _sink_config(live_db), SPEC)

    client.place_order.assert_called_once_with("hash", SPEC)


def test_the_sink_refuses_when_the_mode_is_not_live(live_db):
    """Structurally incapable of ordering in dry run, rather than relying on
    every caller to remember to check."""
    from schwab_client import orders
    client = MagicMock()

    with pytest.raises(RuntimeError):
        orders._call_place_order(client, _sink_config(live_db, mode="dry_run"), SPEC)

    client.place_order.assert_not_called()


def test_the_sink_refuses_when_dry_run_disagrees_with_a_live_mode(live_db):
    """The state a test creates by setting dry_run=True on a live config. It
    must fail closed, not resolve in favour of the env surface."""
    from schwab_client import orders
    client = MagicMock()

    with pytest.raises(RuntimeError):
        orders._call_place_order(
            client, _sink_config(live_db, mode="live", dry_run=True), SPEC
        )

    client.place_order.assert_not_called()


def test_the_sink_refuses_the_illegal_dry_mode_with_dry_run_false(live_db):
    """The other direction of disagreement, which no legitimate path produces."""
    from schwab_client import orders
    client = MagicMock()

    with pytest.raises(RuntimeError):
        orders._call_place_order(
            client, _sink_config(live_db, mode="dry_run", dry_run=False), SPEC
        )

    client.place_order.assert_not_called()


def test_the_mode_check_does_not_swallow_the_kill_switch(live_db):
    """A halt must still be reported as a halt, not as a mode problem — the
    operator messages send you to different places."""
    from risk import kill_switch
    from risk.kill_switch import TradingHalted
    from schwab_client import orders

    kill_switch.halt(live_db, actor="operator", reason="incident")

    with pytest.raises(TradingHalted):
        orders._call_place_order(MagicMock(), _sink_config(live_db), SPEC)


# ─── The startup banner ──────────────────────────────────────────────────────


def test_live_mode_announces_itself():
    """Starting with real money at stake must be visible in the log AND in
    Discord. The old banner required DRY_RUN=false AND PAPER_TRADING=false —
    two variables, one of which gated nothing."""
    from main import live_execution_banner

    live = Config()
    live.execution_mode = "live"
    live.dry_run = False
    assert "live" in live_execution_banner(live).lower()


def test_a_dry_run_start_says_nothing():
    from main import live_execution_banner

    assert live_execution_banner(Config()) is None


def test_a_disagreeing_config_is_not_announced_as_live():
    """dry_run=True on a live mode cannot place an order — the sink refuses it —
    so announcing 'LIVE TRADING ACTIVE' would be a false alarm."""
    from main import live_execution_banner

    config = Config()
    config.execution_mode = "live"
    config.dry_run = True
    assert live_execution_banner(config) is None
