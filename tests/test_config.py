"""
tests/test_config.py — Config.validate() suite + USE_LIMIT_BUY env mapping suite.

Two distinct test styles — do not conflate them:
  - validate() tests use EXPLICIT-KWARG Config objects (no module reload). A fully-valid
    Config is constructed via a helper and exactly one field is blanked per missing-field test.
    This is hermetic: does not depend on the machine's .env — but validate() now also
    refuses the LEGACY DRY_RUN / PAPER_TRADING variables, which it reads straight from
    os.environ, so the autouse fixture below clears them. Without it these tests would
    pass or fail depending on what happens to be in the developer's .env, which is
    exactly the dependency this docstring claims they do not have. The legacy-variable
    behaviour itself is covered in tests/test_execution_mode.py.
  - USE_LIMIT_BUY mapping tests use monkeypatch.setenv + importlib.reload(config), because
    the mapping `os.getenv("USE_LIMIT_BUY","false").lower()=="true"` is frozen in the
    dataclass field DEFAULT at import time.
"""

import pytest as _pytest


@_pytest.fixture(autouse=True)
def _no_legacy_execution_vars(monkeypatch):
    for name in ("DRY_RUN", "PAPER_TRADING"):
        monkeypatch.delenv(name, raising=False)


import pytest
import importlib
import dotenv
import config as config_module
from config import Config


# ===========================================================================
# Config.validate() suite (TEST-10, D-02)
# ===========================================================================

def _valid_config(provider="claude"):
    """Return a fully-valid Config using explicit attribute mutation.

    Uses dummy credentials so the result does not depend on the machine's .env.
    For the claude provider: anthropic_api_key satisfies the branch; analyst_api_key
    may be blank.
    """
    c = Config()
    c.schwab_app_key = "ak"
    c.schwab_app_secret = "as"
    c.discord_token = "dt"
    c.discord_channel_id = 12345
    c.schwab_account_hash = "ah"
    c.analyst_provider = provider
    c.analyst_api_key = "" if provider == "claude" else "gkey"
    c.anthropic_api_key = "anthropic-key" if provider == "claude" else ""
    return c


def test_validate_passes_with_fully_valid_config():
    """Happy path: a fully-valid claude-provider config raises no exception."""
    _valid_config().validate()  # must not raise


def test_validate_passes_with_valid_gemini_config():
    """Happy path: a fully-valid gemini-provider config raises no exception."""
    _valid_config(provider="gemini").validate()  # must not raise


def test_validate_raises_when_schwab_app_key_missing():
    c = _valid_config()
    c.schwab_app_key = ""
    with pytest.raises(ValueError, match="SCHWAB_APP_KEY"):
        c.validate()


def test_validate_raises_when_schwab_app_secret_missing():
    c = _valid_config()
    c.schwab_app_secret = ""
    with pytest.raises(ValueError, match="SCHWAB_APP_SECRET"):
        c.validate()


def test_validate_raises_when_discord_token_missing():
    c = _valid_config()
    c.discord_token = ""
    with pytest.raises(ValueError, match="DISCORD_TOKEN"):
        c.validate()


def test_validate_raises_when_discord_channel_id_missing():
    """discord_channel_id is an int; 0 is falsy and triggers the raise."""
    c = _valid_config()
    c.discord_channel_id = 0
    with pytest.raises(ValueError, match="DISCORD_CHANNEL_ID"):
        c.validate()


def test_validate_raises_when_schwab_account_hash_missing():
    c = _valid_config()
    c.schwab_account_hash = ""
    with pytest.raises(ValueError, match="SCHWAB_ACCOUNT_HASH"):
        c.validate()


def test_validate_raises_when_claude_keys_both_missing():
    """Claude branch: both analyst_api_key and anthropic_api_key blank → raises."""
    c = _valid_config(provider="claude")
    c.analyst_api_key = ""
    c.anthropic_api_key = ""
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        c.validate()


def test_validate_claude_passes_with_anthropic_key_only():
    """Claude branch: anthropic_api_key alone satisfies the branch."""
    c = _valid_config(provider="claude")
    c.analyst_api_key = ""
    c.anthropic_api_key = "anthropic-key"
    c.validate()  # must not raise


def test_validate_claude_passes_with_analyst_key_only():
    """Claude branch: analyst_api_key alone satisfies the branch."""
    c = _valid_config(provider="claude")
    c.analyst_api_key = "some-key"
    c.anthropic_api_key = ""
    c.validate()  # must not raise


def test_validate_raises_when_other_provider_api_key_missing():
    """Other-provider branch (gemini): analyst_api_key blank → raises."""
    c = _valid_config(provider="gemini")
    c.analyst_api_key = ""
    with pytest.raises(ValueError, match="ANALYST_API_KEY"):
        c.validate()


def test_validate_other_provider_passes_with_api_key():
    """Other-provider branch (gemini): analyst_api_key set → passes."""
    c = _valid_config(provider="gemini")
    c.analyst_api_key = "gkey"
    c.validate()  # must not raise


# ===========================================================================
# Guard-table env mapping
# ===========================================================================
#
# USE_LIMIT_BUY used to be exercised here through importlib.reload, because its
# mapping was frozen in a dataclass field default at import time. Both are gone:
# the toggle was removed (every buy is a limit order, so there is nothing to
# toggle), and Config now reads the environment at CONSTRUCTION via
# field(default_factory=...), so a plain Config() sees the current env.


def test_max_daily_notional_defaults_to_2000(monkeypatch):
    monkeypatch.delenv("MAX_DAILY_NOTIONAL_USD", raising=False)
    assert config_module.Config().max_daily_notional_usd == 2000.0


def test_max_daily_notional_reads_the_environment_at_construction(monkeypatch):
    monkeypatch.setenv("MAX_DAILY_NOTIONAL_USD", "750.5")
    assert config_module.Config().max_daily_notional_usd == 750.5


def test_price_tolerance_defaults_to_2_percent(monkeypatch):
    monkeypatch.delenv("APPROVAL_PRICE_TOLERANCE_PCT", raising=False)
    assert config_module.Config().approval_price_tolerance_pct == 2.0


def test_the_approver_allowlist_defaults_to_empty(monkeypatch):
    """Empty means DENY ALL. It must never come to mean allow-all."""
    monkeypatch.delenv("ALLOWED_DISCORD_USER_IDS", raising=False)
    assert config_module.Config().allowed_discord_user_ids == ""


def test_the_approver_allowlist_is_read_at_construction(monkeypatch):
    monkeypatch.setenv("ALLOWED_DISCORD_USER_IDS", "1,2,3")
    assert config_module.Config().allowed_discord_user_ids == "1,2,3"


def test_the_approver_allowlist_is_separate_from_the_ops_allowlist(monkeypatch):
    """Halting is a safety action anyone trusted should be able to take.
    Spending money is not, so the two lists are deliberately different knobs."""
    monkeypatch.setenv("OPS_USER_IDS", "111")
    monkeypatch.setenv("ALLOWED_DISCORD_USER_IDS", "222")
    c = config_module.Config()
    assert c.ops_user_ids == "111"
    assert c.allowed_discord_user_ids == "222"
