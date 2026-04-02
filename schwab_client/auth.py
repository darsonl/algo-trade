from __future__ import annotations
from pathlib import Path


def get_token_path() -> str:
    """Return the absolute path where the Schwab OAuth token file is stored."""
    return str(Path(__file__).parent.parent / "schwab_token.json")


def get_client(config):
    """
    Return an authenticated schwab-py client using config.schwab_app_key and config.schwab_app_secret.

    On first run, schwab-py starts a local callback server on port 8182 and opens a browser for the
    Schwab OAuth2 login flow. config.schwab_callback_url must exactly match the redirect URI registered
    at developer.schwab.com (default https://127.0.0.1) — a mismatch causes a redirect_mismatch error.
    After login the token is written to schwab_token.json; subsequent calls load and auto-refresh it.
    """
    import schwab.auth as auth
    return auth.easy_client(
        api_key=config.schwab_app_key,
        app_secret=config.schwab_app_secret,
        callback_url=config.schwab_callback_url,
        token_path=get_token_path(),
    )
