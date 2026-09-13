from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

# Schwab refresh tokens live 7 days from creation; there is no way to extend
# one without a human completing a browser login.
REFRESH_TOKEN_LIFETIME_S = 7 * 24 * 3600
# One day of notice, delivered by the daily scan.
TOKEN_WARN_AGE_S = 6 * 24 * 3600

LOGIN_HINT = "run `.venv/Scripts/python.exe scripts/schwab_login.py` at the machine"


class SchwabLoginRequired(RuntimeError):
    """No usable Schwab login exists, and only a human can create one.

    Raised instead of starting a login, because the bot runs unattended: an
    interactive prompt there either crashes on EOF or blocks forever, and the
    Approve button would do it while holding `approval_gate()`.
    """


def get_token_path() -> str:
    """Return the absolute path where the Schwab OAuth token file is stored."""
    return str(Path(__file__).parent.parent / "schwab_token.json")


def token_age_seconds(token_path: str | None = None,
                      now: datetime | None = None) -> float | None:
    """Seconds since the token was created, or None if that cannot be known.

    None covers a missing file, unreadable JSON and a missing or non-numeric
    `creation_timestamp` alike: in every case nobody can say whether the login
    still works, and the caller treats that as "log in again".
    """
    path = token_path or get_token_path()
    try:
        created = json.loads(Path(path).read_text(encoding="utf-8"))["creation_timestamp"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        return None
    current = now.timestamp() if now is not None else time.time()
    return current - created


def get_client(config, now: datetime | None = None):
    """Return a schwab-py client loaded from the token file. NEVER logs in.

    This used `easy_client`, which discards a token older than 6.5 days and runs
    `client_from_login_flow(interactive=True)` -- an `input()` call. In an
    unattended bot that crashed or hung, and the Approve button reaches here
    even in dry run. A login is a human act now: `scripts/schwab_login.py`.

    Refuses on age rather than letting an HTTP call discover the expiry, so the
    error names the fix. The cutoff is the token's real 7-day lifetime, not
    easy_client's proactive 6.5: the last half day still works. A token revoked
    early still fails at request time; callers must treat that as a failed read.
    """
    import schwab.auth as auth

    path = get_token_path()
    age = token_age_seconds(path, now)
    if age is None:
        raise SchwabLoginRequired(f"no readable Schwab token at {path}; {LOGIN_HINT}")
    if age >= REFRESH_TOKEN_LIFETIME_S:
        raise SchwabLoginRequired(
            f"Schwab login expired {age / 86400:.1f} days after it was created; {LOGIN_HINT}"
        )
    return auth.client_from_token_file(path, config.schwab_app_key, config.schwab_app_secret)


def schwab_login_warning(token_path: str | None = None,
                         now: datetime | None = None) -> str | None:
    """The ops alert for a login that has expired or is about to, else None.

    Pure apart from reading the token file, so the scan can post it every run.
    """
    age = token_age_seconds(token_path, now)
    if age is None:
        return ("Schwab login: no readable token, so Approve is refused (no quote) "
                f"and nothing can be submitted — {LOGIN_HINT}.")
    if age >= REFRESH_TOKEN_LIFETIME_S:
        return ("Schwab login EXPIRED — Approve is refused (no quote) and nothing can "
                f"be submitted until you log in again: {LOGIN_HINT}.")
    if age >= TOKEN_WARN_AGE_S:
        hours = int((REFRESH_TOKEN_LIFETIME_S - age) // 3600)
        return (f"Schwab login expires in {hours}h. After that Approve is refused "
                f"until you log in again: {LOGIN_HINT}.")
    return None
