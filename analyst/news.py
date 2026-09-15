import datetime as _dt
import logging
import urllib.request
import urllib.parse
import urllib.error
import json
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)

_AV_NEWS_URL = "https://www.alphavantage.co/query"
_REDACTED = "***"


class NewsProviderUnavailable(RuntimeError):
    """Alpha Vantage refused the call for a reason that will not change today.

    Covers a spent daily budget and a rejected key alike: the free tier answers
    both with an ``Information`` body and HTTP 200, not an error status, and
    neither can succeed on a retry. Kept distinct from a transient failure so
    the retry decorator lets it straight through and the day breaker trips on
    it -- a spent budget is a known quantity, not flakiness.
    """


def redact_secret(text: str, secret: str) -> str:
    """Blank every occurrence of ``secret`` in ``text``.

    Alpha Vantage's rate-limit message quotes the caller's own API key back at
    them, so the provider's message cannot be logged as received. Redacting the
    value we already hold is stronger than matching their wording, which they
    are free to change without telling us.

    An empty secret returns the text unchanged: ``"".replace("", ...)`` injects
    the placeholder at every character boundary.
    """
    if not secret:
        return text
    return text.replace(secret, _REDACTED)


def _provider_quota_date() -> str:
    """The day Alpha Vantage's daily budget is bucketed by.

    UTC, deliberately NOT ``market_session_date()``: this is a vendor's billing
    day, not an exchange's, and the two are different questions. A wrong guess
    at the boundary costs exactly one call, which re-trips the breaker.
    """
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


# Provider name -> the day it was found unavailable. Reset implicitly by the
# date rolling over. Per provider, never shared: Alpha Vantage's 25/day cap
# must not switch off Finnhub, whose limit is 60/MINUTE.
_unavailable_on: dict[str, str] = {}

_FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/company-news"
# Coverage is wildly uneven -- measured 2026-09-15 over 7 days: SNDK 138 items,
# MA 55, COR 15. A short window would leave thin names with no headlines at all.
_FINNHUB_WINDOW_DAYS = 7

_av_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    retry=retry_if_not_exception_type(NewsProviderUnavailable),
    reraise=True,
)


_finnhub_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    retry=retry_if_not_exception_type(NewsProviderUnavailable),
    reraise=True,
)


def extract_headlines(news_items: list[dict], max_headlines: int = 5) -> list[str]:
    """Extract headline strings from yfinance news dicts, skipping items without a title.

    Handles both news schemas: yfinance >= 0.2.51 nests the title under
    item["content"]["title"]; older versions put it at item["title"].
    """
    headlines = []
    for item in news_items:
        content = item.get("content")
        title = content.get("title", "") if isinstance(content, dict) else ""
        if not title:
            title = item.get("title", "")
        title = title.strip()
        if title:
            headlines.append(title)
        if len(headlines) == max_headlines:
            break
    return headlines


@_av_retry
def _fetch_from_alpha_vantage(ticker: str, api_key: str, max_headlines: int = 5) -> list[str]:
    """Fetch news from Alpha Vantage NEWS_SENTIMENT endpoint.

    Each headline is enriched with the sentiment label and score so the analyst
    receives pre-scored context, e.g. "Fed cuts rates [Bullish, 0.72]".
    """
    params = urllib.parse.urlencode({
        "function": "NEWS_SENTIMENT",
        "tickers": ticker,
        "limit": max_headlines,
        "apikey": api_key,
    })
    url = f"{_AV_NEWS_URL}?{params}"

    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode())

    if "Information" in data:
        # Rate limit or invalid key. Redacted at the raise, not at the log
        # site, so no later handler can reach the unredacted text.
        raise NewsProviderUnavailable(
            f"Alpha Vantage API error: {redact_secret(data['Information'], api_key)}"
        )

    articles = data.get("feed", [])
    headlines = []
    for article in articles[:max_headlines]:
        title = article.get("title", "").strip()
        if not title:
            continue
        label = article.get("overall_sentiment_label", "")
        score = article.get("overall_sentiment_score")
        if label and score is not None:
            headlines.append(f"{title} [{label}, {float(score):.2f}]")
        else:
            headlines.append(title)
    return headlines


@_retry
def _fetch_from_yfinance(ticker: str, max_headlines: int = 5) -> list[str]:
    """Fetch recent news headlines for a ticker via yfinance (fallback)."""
    import yfinance as yf
    news = yf.Ticker(ticker).news
    return extract_headlines(news or [], max_headlines=max_headlines)


@_finnhub_retry
def _fetch_from_finnhub(ticker: str, api_key: str, max_headlines: int = 5) -> list[str]:
    """Fetch company news from Finnhub, newest first.

    Measured against the live API on 2026-09-15, because the docs are wrong in
    one place that matters: `datetime` is a Unix epoch int, not ISO 8601. Items
    carry category/datetime/headline/id/image/related/source/summary/url, and
    `category` is always "company" while `related` always contains the symbol,
    so neither is usable as a relevance filter.

    The feed is ordered by RECENCY, not relevance: a thinly covered name gets
    sector stories tagged to its ticker ahead of its own news. That is a real
    quality cost against Alpha Vantage's ranked NEWS_SENTIMENT, and the price
    of covering every candidate rather than the first 25 of the day.
    """
    to_date = _dt.datetime.now(_dt.timezone.utc).date()
    from_date = to_date - _dt.timedelta(days=_FINNHUB_WINDOW_DAYS)
    params = urllib.parse.urlencode({
        "symbol": ticker,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "token": api_key,
    })

    try:
        with urllib.request.urlopen(f"{_FINNHUB_NEWS_URL}?{params}", timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 429 is Finnhub's per-minute cap and IS worth retrying. Every other
        # 4xx -- a rejected key (401), a plan that excludes the endpoint (403),
        # a malformed request (400) -- is settled for the day.
        if 400 <= exc.code < 500 and exc.code != 429:
            raise NewsProviderUnavailable(
                f"Finnhub refused the request (HTTP {exc.code})"
            ) from None
        raise

    if isinstance(data, dict):
        # Finnhub answers errors with a JSON object; success is always a list.
        raise NewsProviderUnavailable(
            f"Finnhub returned an error object: {redact_secret(str(data), api_key)}"
        )

    headlines = []
    for article in data:
        headline = (article.get("headline") or "").strip()
        if headline:
            headlines.append(headline)
        if len(headlines) == max_headlines:
            break
    return headlines




def fetch_news_headlines(
    ticker: str,
    max_headlines: int = 5,
    alpha_vantage_api_key: str = "",
    finnhub_api_key: str = "",
) -> list[str]:
    """Fetch recent news headlines for a ticker, best provider first.

    Chain: Finnhub -> Alpha Vantage -> yfinance. Finnhub leads because its free
    tier is capped per MINUTE (60) rather than per day, so it covers a whole
    scan; Alpha Vantage's 25/day covered only the first 25 tickers in scan
    order, which made headline richness correlate with S&P rank by
    construction. Uniform input across the universe is worth more than richer
    input for a rank-selected quarter of it.

    A provider that returns nothing falls through to the next rather than
    ending the chain: a ticker one source has no coverage for should still get
    a second chance instead of reaching the analyst with no headlines at all.
    """
    providers = (
        ("finnhub", finnhub_api_key, _fetch_from_finnhub),
        ("alpha_vantage", alpha_vantage_api_key, _fetch_from_alpha_vantage),
    )

    for name, api_key, fetch in providers:
        if not api_key or _unavailable_on.get(name) == _provider_quota_date():
            continue
        try:
            headlines = fetch(ticker, api_key, max_headlines)
            if headlines:
                return headlines
        except NewsProviderUnavailable as exc:
            # Settled for the day: stop asking. The 26th call of a 25-call day
            # cannot succeed and neither can the 27th.
            _unavailable_on[name] = _provider_quota_date()
            logger.warning(
                "%s is unavailable for the rest of %s (%s) — falling through from %s.",
                name, _unavailable_on[name], redact_secret(str(exc), api_key), ticker,
            )
        except Exception as exc:
            logger.warning(
                "%s news fetch failed for %s (%s), trying the next provider",
                name, ticker, redact_secret(str(exc), api_key),
            )
    return _fetch_from_yfinance(ticker, max_headlines)
