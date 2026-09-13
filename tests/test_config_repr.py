"""Config's repr must not carry credentials.

pytest prints the repr of any object in a failing assertion, so a single failed
test involving a Config would put every credential on screen -- and into
whatever that output is pasted into. Production never formats a Config today;
this also keeps a future `logger.debug("%s", config)` from writing secrets into
log files that persist.
"""
import dataclasses
import re

import pytest

from config import Config

SECRET_ENV = {
    "SCHWAB_APP_KEY": "sentinel-schwab-app-key",
    "SCHWAB_APP_SECRET": "sentinel-schwab-app-secret",
    "SCHWAB_ACCOUNT_HASH": "sentinel-schwab-account-hash",
    "DISCORD_TOKEN": "sentinel-discord-token",
    "ANTHROPIC_API_KEY": "sentinel-anthropic-key",
    "ANALYST_API_KEY": "sentinel-analyst-key",
    "ANALYST_FALLBACK_API_KEY": "sentinel-fallback-key",
    "ANALYST_FALLBACK2_API_KEY": "sentinel-fallback2-key",
    "ALPHA_VANTAGE_API_KEY": "sentinel-alpha-vantage-key",
}


@pytest.fixture
def secret_config(monkeypatch):
    for name, value in SECRET_ENV.items():
        monkeypatch.setenv(name, value)
    return Config()


def test_no_secret_value_appears_in_repr(secret_config):
    text = repr(secret_config)
    leaked = [value for value in SECRET_ENV.values() if value in text]
    assert leaked == []


def test_the_secrets_are_still_loaded(secret_config):
    # Hidden from repr, not from the program.
    assert secret_config.schwab_app_secret == "sentinel-schwab-app-secret"
    assert secret_config.alpha_vantage_api_key == "sentinel-alpha-vantage-key"


def test_every_credential_shaped_field_is_hidden():
    """Structural, so a NEW credential field fails here the day it is added
    without `secret=True` -- the sentinel test only knows today's names."""
    credential = re.compile(r"(key|secret|token|hash|password)")
    shown = [f.name for f in dataclasses.fields(Config) if credential.search(f.name) and f.repr]
    assert shown == []


def test_repr_still_shows_the_settings_worth_debugging(secret_config):
    text = repr(secret_config)
    assert "execution_mode=" in text
    assert "max_forward_pe=" in text
    assert "discord_channel_id=" in text  # an identifier, not a credential
