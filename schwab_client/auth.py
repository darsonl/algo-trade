from __future__ import annotations
from pathlib import Path


def get_token_path() -> str:
    """Return the absolute path where the Schwab OAuth token file is stored."""
    return str(Path(__file__).parent.parent / "schwab_token.json")


def get_client(config):
    """
    Return an authenticated schwab-py client.

    On first run this opens a browser for the OAuth2 login flow and writes the
    token to schwab_token.json.  Subsequent runs load the token from that file
    and refresh it automatically.
    """
    import schwab.auth as auth
    return auth.easy_client(
        api_key=config.schwab_app_key,
        app_secret=config.schwab_app_secret,
        callback_url=config.schwab_callback_url,
        token_path=get_token_path(),
    )
