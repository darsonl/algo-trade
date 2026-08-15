"""
Verify the kill-switch operator allowlist before starting the bot.

Checks that OPS_USER_IDS is set and parses, and reports the current kill-switch
state so you know whether /resume is still needed. An empty allowlist authorizes
NOBODY, and a malformed entry is skipped rather than treated as a wildcard — so
a typo silently locks you out of /halt rather than opening it up. That fails in
the safe direction, but it fails quietly, which is what this script is for.

Exits non-zero if nobody is authorized.

Usage:
    .venv/Scripts/python.exe scripts/check_ops_ids.py
"""
import sys
import pathlib

# Running `python scripts/check_ops_ids.py` puts scripts/ on sys.path, not the
# repo root — add the repo root so the `config` / `discord_bot` imports resolve.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import logging  # noqa: E402
import os  # noqa: E402

# This script calls is_authorized() once per id plus a probe, so library
# warnings would repeat several times and say what the report below already
# says more clearly. Silence them and let the report speak.
logging.disable(logging.WARNING)

from config import Config  # noqa: E402
from discord_bot.bot import is_authorized  # noqa: E402
from risk import kill_switch  # noqa: E402

UNLISTED_PROBE = 999  # any id that must never be authorized


def main() -> int:
    cfg = Config()
    raw = cfg.ops_user_ids
    print(f"OPS_USER_IDS raw value: {raw!r}")

    ids, malformed = [], []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            malformed.append(part)

    if malformed:
        print(f"  IGNORED (not numeric): {malformed}  <-- these authorize nobody")

    if not ids:
        print("\n  NOT SET - nobody can run /halt or /resume.")
        print("  Add OPS_USER_IDS=<your discord user id> to .env")
        print("  (Discord: Settings > Advanced > Developer Mode, then")
        print("   right-click your name > Copy User ID)")
        return 1

    # Check through the real predicate rather than reimplementing it, so this
    # cannot drift from what the bot actually enforces.
    for uid in ids:
        if not is_authorized(cfg, uid):
            print(f"\n  FAILED: {uid} parsed but is_authorized() rejected it.")
            return 1
    if is_authorized(cfg, UNLISTED_PROBE):
        print(f"\n  FAILED: unlisted user {UNLISTED_PROBE} was authorized.")
        return 1

    print(f"\n  Authorized: {ids}")
    print(f"  Verified via is_authorized(); unlisted user {UNLISTED_PROBE} correctly refused.")

    if not os.path.exists(cfg.db_path):
        print(f"\n  Database {cfg.db_path} does not exist yet.")
        print("  It is created on first startup, and the kill switch will seed from")
        print(f"  TRADING_ENABLED={cfg.trading_enabled} -> "
              f"{'ENABLED' if cfg.trading_enabled else 'HALTED'}.")
        return 0

    state = kill_switch.get_state(cfg.db_path)
    print(f"\n  Kill switch state: {state}")
    if state == kill_switch.UNINITIALIZED:
        print("  Not seeded yet; the next startup seeds it from "
              f"TRADING_ENABLED={cfg.trading_enabled}.")
    elif state != kill_switch.ENABLED:
        print("  Trading is OFF. Run /resume in Discord to arm it.")
    else:
        print("  Trading is ARMED at the kill switch.")
        if cfg.dry_run:
            print("  DRY_RUN=true still blocks every order (separate control).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
