import json
import datetime
from pathlib import Path


_CACHE_PATH = Path(__file__).parent.parent / "sp500_cache.json"
_CACHE_TTL_HOURS = 24


def get_watchlist(path: str) -> list[str]:
    """Read tickers from a watchlist file, one per line. Skips comments and blanks."""
    with open(path, "r") as f:
        lines = f.readlines()
    tickers = []
    for line in lines:
        ticker = line.strip()
        if ticker and not ticker.startswith("#"):
            tickers.append(ticker.upper())
    return tickers


def get_universe(watchlist_path: str, extra_tickers: list[str] | None = None) -> list[str]:
    """Combine watchlist with any extra tickers (e.g. S&P 500), deduplicated."""
    watchlist = get_watchlist(watchlist_path)
    extra = [t.upper() for t in (extra_tickers or [])]
    seen: set[str] = set()
    result: list[str] = []
    for ticker in watchlist + extra:
        if ticker not in seen:
            seen.add(ticker)
            result.append(ticker)
    return result


def _load_sp500_cache() -> list[str] | None:
    """Return cached tickers if cache file exists, else None."""
    if not _CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return data.get("tickers")
    except Exception:
        return None


def _save_sp500_cache(tickers: list[str]) -> None:
    """Write tickers to cache file with current timestamp."""
    try:
        _CACHE_PATH.write_text(
            json.dumps({"fetched_at": datetime.datetime.now().isoformat(), "tickers": tickers}),
            encoding="utf-8",
        )
    except Exception:
        pass  # Cache write failure is non-fatal


def _cache_is_fresh() -> bool:
    """Return True if cache exists and was written within the last 24 hours."""
    if not _CACHE_PATH.exists():
        return False
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        fetched_at = datetime.datetime.fromisoformat(data["fetched_at"])
        return (datetime.datetime.now() - fetched_at).total_seconds() < _CACHE_TTL_HOURS * 3600
    except Exception:
        return False


def _fetch_sp500_from_wikipedia() -> list[str]:
    """Fetch S&P 500 tickers directly from Wikipedia (no cache logic)."""
    import pandas as pd
    table = pd.read_html(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", header=0
    )
    return table[0]["Symbol"].str.upper().tolist()


def get_sp500_tickers() -> list[str]:
    """
    Fetch S&P 500 ticker symbols from Wikipedia.

    Returns cached tickers if the cache is fresh (< 24h old).
    On any network or parse failure, returns cached tickers (even if stale)
    or an empty list if no cache exists.
    """
    if _cache_is_fresh():
        cached = _load_sp500_cache()
        if cached:
            return cached

    try:
        tickers = _fetch_sp500_from_wikipedia()
        _save_sp500_cache(tickers)
        return tickers
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "S&P 500 Wikipedia fetch failed: %s — falling back to cache", exc
        )
        cached = _load_sp500_cache()
        return cached if cached is not None else []
