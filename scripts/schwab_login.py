"""Log in to Schwab and write a fresh token. The ONLY place a login happens.

The bot never logs in by itself (`schwab_client.auth.get_client` raises
`SchwabLoginRequired` instead), because it runs unattended and a login needs a
human in a browser. A refresh token lasts 7 days; each scan warns in the ops
channel during the last day.

    .venv/Scripts/python.exe scripts/schwab_login.py
    .venv/Scripts/python.exe scripts/schwab_login.py --show-url

Your browser opens the Schwab login page. After you approve, it will warn about
a self-signed certificate on https://127.0.0.1:<port> -- that is schwab-py's
local callback server; check the address and continue.

schwab-py prints the authorization URL, and that URL contains the app key. It
is suppressed by default so it does not land in terminal scrollback, a pasted
log, or an AI assistant's transcript. `--show-url` prints it for when the
browser does not open by itself.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from config import Config  # noqa: E402
from schwab_client.auth import (  # noqa: E402
    REFRESH_TOKEN_LIFETIME_S,
    get_token_path,
    token_age_seconds,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Log in to Schwab and refresh the token.")
    ap.add_argument("--show-url", action="store_true",
                    help="print the authorization URL (it contains the app key)")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="seconds to wait for the browser login (default 600)")
    args = ap.parse_args(argv)

    import schwab.auth as auth

    config = Config()
    print("Opening the Schwab login in your browser; waiting up to "
          f"{args.timeout:.0f}s for you to finish...")
    sink = contextlib.nullcontext() if args.show_url else contextlib.redirect_stdout(io.StringIO())
    try:
        with sink:
            auth.client_from_login_flow(
                config.schwab_app_key, config.schwab_app_secret,
                config.schwab_callback_url, get_token_path(),
                interactive=False, callback_timeout=args.timeout,
            )
    except Exception as exc:
        print(f"Login failed: {type(exc).__name__}: {exc}")
        return 1

    age = token_age_seconds()
    if age is None:
        print("Login returned, but the token file is unreadable. Try again.")
        return 1
    expires = datetime.now(timezone.utc) + timedelta(seconds=REFRESH_TOKEN_LIFETIME_S - age)
    print(f"Logged in. Token expires {expires:%Y-%m-%d %H:%M} UTC "
          f"({expires.astimezone():%Y-%m-%d %H:%M} local).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
