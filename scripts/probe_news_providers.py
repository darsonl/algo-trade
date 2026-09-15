"""Measure what the news providers actually return, through the app's own code.

Run this before changing the provider chain, and after any provider release
note. It exists for the same reason `probe_analyst_models.py` does: the things
on this path have been chosen from documentation and the documentation has been
wrong. Measured against the live API on 2026-09-15:

* Finnhub's docs describe `datetime` as ISO 8601. It is a **Unix epoch int**.
* Finnhub's `category` is always "company" and `related` always contains the
  symbol, so neither is usable as a relevance filter, whatever the docs imply.
* Alpha Vantage's rate-limit refusal arrives as **HTTP 200** with an
  `Information` body, and quotes the caller's own API key back at them -- which
  is how 43 log lines came to contain a live credential.

The second trap worth remembering: a provider's *error* shape matters as much
as its success shape. This probe deliberately calls each provider with a junk
token and reports whether the token comes back in the body, because that is the
question no success-path test can answer.

    .venv/Scripts/python.exe scripts/probe_news_providers.py
    .venv/Scripts/python.exe scripts/probe_news_providers.py AAPL MSFT KO
    .venv/Scripts/python.exe scripts/probe_news_providers.py --no-error-probe

Read-only: it fetches news and writes nothing. It DOES spend provider quota --
Alpha Vantage's free tier is 25 calls/day total, so probing it three times
costs an eighth of a scan's budget.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config                                    # noqa: E402
from analyst import news                                     # noqa: E402

_JUNK_TOKEN = "PROBE-INVALID-TOKEN-0000"
_DEFAULT_TICKERS = ["AAPL", "KO", "COR"]   # wide, mid and thin news coverage


def _probe_success(label, fetch, ticker, api_key):
    if not api_key:
        print(f"  {label:<14} SKIPPED (no key configured)")
        return
    started = time.monotonic()
    try:
        headlines = fetch(ticker, api_key, 5)
    except Exception as exc:                                  # noqa: BLE001
        redacted = news.redact_secret(str(exc), api_key)
        leaked = api_key in str(exc)
        print(f"  {label:<14} ERROR {type(exc).__name__}: {redacted[:90]}")
        print(f"  {'':<14}   >>> KEY IN EXCEPTION TEXT: {leaked}")
        return
    elapsed = time.monotonic() - started
    tagged = sum(1 for h in headlines if h.rstrip().endswith("]"))
    print(f"  {label:<14} {len(headlines)} headlines in {elapsed:4.1f}s"
          f"  (sentiment-tagged: {tagged})")
    for headline in headlines[:3]:
        print(f"  {'':<14}   - {headline[:88]}")


def _probe_error_shape(label, fetch, ticker):
    """Call with a junk token: does the refusal quote it back at us?"""
    try:
        fetch(ticker, _JUNK_TOKEN, 5)
    except Exception as exc:                                  # noqa: BLE001
        text = str(exc)
        print(f"  {label:<14} {type(exc).__name__}: {text[:80]}")
        print(f"  {'':<14}   >>> JUNK TOKEN ECHOED: {_JUNK_TOKEN in text}")
    else:
        print(f"  {label:<14} accepted a junk token — check the endpoint")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="*", default=None)
    parser.add_argument("--no-error-probe", action="store_true",
                        help="skip the junk-token calls (they are cheap but do hit the API)")
    args = parser.parse_args()

    config = Config()
    tickers = args.tickers or _DEFAULT_TICKERS
    providers = (
        ("finnhub", news._fetch_from_finnhub, config.finnhub_api_key),
        ("alpha_vantage", news._fetch_from_alpha_vantage, config.alpha_vantage_api_key),
    )

    print("keys configured:", ", ".join(
        f"{name}={'yes' if key else 'NO'}" for name, _, key in providers))

    for ticker in tickers:
        print(f"\n=== {ticker} ===")
        for name, fetch, key in providers:
            _probe_success(name, fetch, ticker, key)
        print(f"  {'yfinance':<14} (fallback, always available)")

    if not args.no_error_probe:
        print("\n=== error shapes (junk token) ===")
        for name, fetch, _ in providers:
            _probe_error_shape(name, fetch, tickers[0])

    print("\nChain in use: " + " -> ".join(
        [name for name, _, key in providers if key] + ["yfinance"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
