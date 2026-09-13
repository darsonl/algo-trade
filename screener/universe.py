import json
import datetime
import logging
import math
import time
from pathlib import Path
from tenacity import retry, stop_after_attempt, wait_exponential
import yfinance as yf

_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)

_ETF_ALLOWLIST = {"SPY", "QQQ", "VTI", "IVV", "VOO", "VEA", "BND", "GLD", "XLK", "SCHD"}

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


def partition_watchlist(
    tickers: list[str], info_sink: dict | None = None
) -> tuple[list[str], list[str]]:
    """
    Classify tickers as stocks or ETFs using yfinance quoteType.

    For each ticker, queries yf.Ticker(ticker).info['quoteType']. If quoteType
    equals 'ETF', the ticker is classified as an ETF. Otherwise it is treated as
    a stock. When yfinance raises any exception, falls back to _ETF_ALLOWLIST:
    tickers in the allowlist go to etfs, all others go to stocks.

    Performance: `.info` is the heaviest yfinance call, and the scan loop needs it
    again (fundamentals for stocks, expense ratio for ETFs). Pass a dict as
    `info_sink` to capture the fetched info per ticker ({ticker: info}); the caller
    can then reuse it instead of fetching `.info` a second time. Tickers whose lookup
    failed (allowlist fallback) are absent from the sink, so the caller should treat a
    miss as "fetch it yourself". The return value is unchanged so existing
    callers and tests are unaffected.

    Do NOT use @_retry — per-ticker failures are handled by the allowlist fallback.
    The function will be wrapped in asyncio.to_thread at its call site (Plan 03).

    Returns:
        (stocks, etfs) — two lists of uppercase ticker strings.
    """
    log = logging.getLogger(__name__)
    stocks: list[str] = []
    etfs: list[str] = []
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            if info_sink is not None:
                info_sink[ticker] = info
            quote_type = info.get("quoteType", "")
            if quote_type == "ETF":
                etfs.append(ticker)
            else:
                stocks.append(ticker)
        except Exception:
            log.debug(
                "yfinance quoteType lookup failed for %s, using allowlist fallback", ticker
            )
            if ticker.upper() in _ETF_ALLOWLIST:
                etfs.append(ticker)
            else:
                stocks.append(ticker)
    return stocks, etfs


def _load_sp500_cache() -> list[str] | None:
    """Return cached tickers if cache file exists, else None."""
    if not _CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return data.get("tickers")
    except Exception:
        return None


def _load_sp500_cik() -> dict[str, str] | None:
    """Return the cached ticker -> CIK map, or None (absent in caches written before it existed)."""
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8")).get("cik") or None
    except Exception:
        return None


def _save_sp500_cache(tickers: list[str], cik: dict[str, str] | None = None) -> None:
    """Write tickers (and the ticker -> CIK map) to cache file with current timestamp."""
    try:
        _CACHE_PATH.write_text(
            json.dumps({"fetched_at": datetime.datetime.now().isoformat(), "tickers": tickers, "cik": cik or {}}),
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


def _parse_sp500_table(table) -> tuple[list[str], dict[str, str]]:
    """(tickers, ticker -> SEC CIK) from the Wikipedia constituents table.

    The CIK identifies the COMPANY, so dual-class listings share one. It is
    normalised to a plain digit string: pandas may read it as an int or keep
    leading zeros. A missing column yields no map rather than an error -- the
    ranking then skips de-duplication, which is the recoverable direction.
    """
    import pandas as pd
    tickers = table["Symbol"].astype(str).str.upper().tolist()
    cik: dict[str, str] = {}
    if "CIK" in table.columns:
        for ticker, raw in zip(tickers, table["CIK"]):
            if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                continue
            digits = str(raw).strip().split(".")[0].lstrip("0")
            if digits.isdigit():
                cik[ticker] = digits
    return tickers, cik


@_retry
def _fetch_sp500_from_wikipedia() -> tuple[list[str], dict[str, str]]:
    """Fetch S&P 500 tickers and their CIKs directly from Wikipedia (no cache logic)."""
    import pandas as pd
    import urllib.request
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as response:
        table = pd.read_html(response, header=0)
    return _parse_sp500_table(table[0])


def get_sp500_company_ids() -> dict[str, str]:
    """ticker -> SEC CIK for S&P 500 constituents; {} when unavailable.

    Served from a fresh cache when it has the map. A cache written before the
    map existed has only tickers, so it costs one Wikipedia fetch to fill in.
    Never raises: the caller treats {} as "cannot tell share classes apart".
    """
    if _cache_is_fresh():
        cached = _load_sp500_cik()
        if cached:
            return cached
    try:
        tickers, cik = _fetch_sp500_from_wikipedia()
        if tickers:
            _save_sp500_cache(tickers, cik)
        return cik
    except Exception as exc:
        logging.getLogger(__name__).warning("S&P 500 CIK fetch failed: %s", exc)
        return _load_sp500_cik() or {}


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
        tickers, cik = _fetch_sp500_from_wikipedia()
        _save_sp500_cache(tickers, cik)
        return tickers
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "S&P 500 Wikipedia fetch failed: %s — falling back to cache", exc
        )
        cached = _load_sp500_cache()
        return cached if cached is not None else []


_top_sp500_cache: dict = {}

# Disk cache for the EPS+ROE ranking. The ranking costs ~500 yfinance .info
# calls (10-20 minutes); the in-memory cache alone dies with the process, so
# every restart used to repay that cost on the next scan. Stores the FULL
# ranked list (not the top-N slice) so a TOP_SP500_COUNT change never
# invalidates it.
_TOP_CACHE_PATH = Path(__file__).parent.parent / "sp500_top_cache.json"
# Marks a ranking with one ticker per company. A cache without it -- written
# before de-duplication existed, or built while CIKs were unavailable -- holds
# duplicate share classes and is rebuilt rather than served for a day.
_TOP_CACHE_DEDUPE = "cik-v1"


def _load_top_cache() -> dict | None:
    """Return {"ranked": [...], "fetched_at": datetime} from disk if fresh (<24h) and de-duplicated, else None."""
    try:
        data = json.loads(_TOP_CACHE_PATH.read_text(encoding="utf-8"))
        if data.get("dedupe") != _TOP_CACHE_DEDUPE:
            return None
        fetched_at = datetime.datetime.fromisoformat(data["fetched_at"])
        if (datetime.datetime.now() - fetched_at).total_seconds() >= _CACHE_TTL_HOURS * 3600:
            return None
        ranked = data["ranked"]
        if not ranked:
            return None
        return {"ranked": ranked, "fetched_at": fetched_at}
    except Exception:
        return None


def _save_top_cache(ranked: list[str], deduped: bool) -> None:
    """Write the full ranked ticker list to disk. Failure is non-fatal."""
    try:
        _TOP_CACHE_PATH.write_text(
            json.dumps({
                "fetched_at": datetime.datetime.now().isoformat(),
                "ranked": ranked,
                "dedupe": _TOP_CACHE_DEDUPE if deduped else None,
            }),
            encoding="utf-8",
        )
    except Exception:
        pass


def one_per_company(
    ranked: list[str], company_ids: dict[str, str], volumes: dict[str, float | None]
) -> list[str]:
    """Keep one ticker per company (same CIK), preserving rank order.

    Within a company the most-traded class survives -- tighter spreads when an
    order is placed, and the gap is wide and stable (GOOGL 1.5x GOOG, FOXA 4x
    FOX, NWSA 3x NWS). A class with no volume loses to one with a volume; with
    none on either side the higher-ranked class stays. A ticker without a CIK is
    never merged, since "no id" is not a shared id.
    """
    groups: dict[str, list[str]] = {}
    for ticker in ranked:
        cik = company_ids.get(ticker)
        if cik:
            groups.setdefault(cik, []).append(ticker)

    def preference(ticker: str) -> tuple:
        volume = volumes.get(ticker)
        return (volume is not None, volume or 0.0, -ranked.index(ticker))

    keep = {cik: max(members, key=preference) for cik, members in groups.items()}
    return [t for t in ranked if company_ids.get(t) is None or keep[company_ids[t]] == t]


def _rank_desc(items: list[tuple[float, str]]) -> dict[str, int]:
    """Rank tickers by value descending (1 = highest value).

    Ties share the same rank (standard competition ranking: a ticker's rank is
    1 + the number of tickers with a strictly greater value).
    """
    ranks: dict[str, int] = {}
    for value, ticker in items:
        ranks[ticker] = 1 + sum(1 for other, _ in items if other > value)
    return ranks


def get_top_sp500_by_fundamentals(config) -> list[str]:
    """
    Return top config.top_sp500_count S&P 500 tickers ranked by combined EPS + ROE score.

    Two-tier 24h cache: in-memory first, then sp500_top_cache.json on disk (so a
    process restart doesn't repay the ~500 yfinance info calls). Both tiers hold
    the full ranking and slice per top_sp500_count on read. Falls back to a raw
    get_sp500_tickers() slice on any fetch error.
    """
    import yfinance as yf
    global _top_sp500_cache
    if _top_sp500_cache.get("fetched_at"):
        age = (datetime.datetime.now() - _top_sp500_cache["fetched_at"]).total_seconds()
        if age < _CACHE_TTL_HOURS * 3600:
            return _top_sp500_cache["ranked"][: config.top_sp500_count]

    disk_cache = _load_top_cache()
    if disk_cache is not None:
        # Keep the disk timestamp so the memory tier expires when the disk data does.
        _top_sp500_cache = disk_cache
        return disk_cache["ranked"][: config.top_sp500_count]

    tickers = get_sp500_tickers()
    scored: list[tuple[float, float, str]] = []  # (eps, roe, ticker)
    volumes: dict[str, float | None] = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            eps = info.get("trailingEps") or 0.0
            roe = info.get("returnOnEquity") or 0.0
            scored.append((eps, roe, t))
            volume = info.get("averageVolume")
            volumes[t] = (float(volume) if isinstance(volume, (int, float)) and not isinstance(volume, bool)
                          and math.isfinite(volume) else None)  # NaN would break max()
        except Exception:
            continue
        time.sleep(0.15)

    if not scored:
        # All per-ticker fetches failed; fall back to unranked slice without caching
        logging.getLogger(__name__).warning(
            "get_top_sp500_by_fundamentals: all per-ticker EPS/ROE fetches failed, "
            "falling back to unranked S&P 500 slice"
        )
        return tickers[: config.top_sp500_count]

    # Rank-sum: rank EPS and ROE independently (1 = best), then combine by summing the
    # two ranks so a stock strong on BOTH metrics outranks one that is great on a single
    # metric. Scale-free — avoids EPS dollar magnitude swamping the ROE fraction, the
    # flaw in the previous `eps + roe` score.
    eps_rank = _rank_desc([(eps, t) for eps, _roe, t in scored])
    roe_rank = _rank_desc([(roe, t) for _eps, roe, t in scored])
    ranked_full = [t for _eps, _roe, t in sorted(scored, key=lambda r: eps_rank[r[2]] + roe_rank[r[2]])]

    # Dual-class shares carry identical EPS and ROE, so they rank side by side
    # and would spend two slots on one company. De-duplicating HERE, before the
    # slice, is what lets the next company take the freed slot.
    company_ids = get_sp500_company_ids()
    deduped = bool(company_ids)
    if deduped:
        ranked_full = one_per_company(ranked_full, company_ids, volumes)
    else:
        logging.getLogger(__name__).warning(
            "get_top_sp500_by_fundamentals: no CIKs available, share classes of one "
            "company (e.g. GOOG/GOOGL) are NOT de-duplicated in this ranking"
        )

    _top_sp500_cache = {"ranked": ranked_full, "fetched_at": datetime.datetime.now()}
    _save_top_cache(ranked_full, deduped=deduped)
    return ranked_full[: config.top_sp500_count]
