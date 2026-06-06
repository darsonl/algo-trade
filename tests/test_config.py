"""
tests/test_config.py — Config.validate() suite + USE_LIMIT_BUY env mapping suite.

Two distinct test styles — do not conflate them:
  - validate() tests use EXPLICIT-KWARG Config objects (no module reload). A fully-valid
    Config is constructed via a helper and exactly one field is blanked per missing-field test.
    This is hermetic: does not depend on the machine's .env.
  - USE_LIMIT_BUY mapping tests use monkeypatch.setenv + importlib.reload(config), because
    the mapping `os.getenv("USE_LIMIT_BUY","false").lower()=="true"` is frozen in the
    dataclass field DEFAULT at import time.
"""

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
# USE_LIMIT_BUY env mapping (SC#4, D-05)
# ===========================================================================
#
# The mapping `os.getenv("USE_LIMIT_BUY", "false").lower() == "true"` is frozen
# in the dataclass field DEFAULT at module import time. Changing the env var
# after import has no effect unless the module is reloaded.
#
# The noop MUST be patched on the SOURCE module (dotenv.load_dotenv), NOT on
# config.load_dotenv. importlib.reload re-executes config.py's body, including
# line 4 `from dotenv import load_dotenv`. That rebind looks up dotenv.load_dotenv
# at reload time; if we've already patched config.load_dotenv, the rebind on
# line 4 restores the real function BEFORE line 6's call fires. Patching the
# SOURCE means the name line 4 imports IS already the noop.

@pytest.fixture(autouse=True)
def _restore_config_module():
    """Yield to let the test run, then reload config back to its natural state.

    This ensures USE_LIMIT_BUY mapping tests don't pollute module state for
    subsequent tests (including tests in other files that import config).
    """
    yield
    # Restore dotenv.load_dotenv to its real implementation (monkeypatch already
    # restored it by this point), then reload so field defaults reflect the
    # restored environment.
    importlib.reload(config_module)


def _reload_with_env(monkeypatch, value):
    """Reload config_module after setting USE_LIMIT_BUY to value (or unset when None).

    Patches the SOURCE dotenv.load_dotenv so the on-disk .env is never consulted
    during reload — prevents machine-specific .env from polluting the unset case.
    Returns the reloaded Config().use_limit_buy boolean.
    """
    # Patch SOURCE — survives reload's `from dotenv import load_dotenv` rebind.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    if value is None:
        monkeypatch.delenv("USE_LIMIT_BUY", raising=False)
    else:
        monkeypatch.setenv("USE_LIMIT_BUY", value)
    importlib.reload(config_module)
    return config_module.Config().use_limit_buy


def test_use_limit_buy_unset_defaults_false(monkeypatch):
    """Unset USE_LIMIT_BUY → False (the 2026-06-06 default; regression guard)."""
    assert _reload_with_env(monkeypatch, None) is False


def test_use_limit_buy_false_maps_false(monkeypatch):
    """USE_LIMIT_BUY=false → False."""
    assert _reload_with_env(monkeypatch, "false") is False


def test_use_limit_buy_true_maps_true(monkeypatch):
    """USE_LIMIT_BUY=true → True."""
    assert _reload_with_env(monkeypatch, "true") is True


def test_use_limit_buy_uppercase_true_maps_true(monkeypatch):
    """USE_LIMIT_BUY=TRUE (uppercase) → True (case-insensitivity guard)."""
    assert _reload_with_env(monkeypatch, "TRUE") is True
