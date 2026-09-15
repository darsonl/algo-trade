import datetime as _dt
import logging
import urllib.request
import urllib.parse
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


class AlphaVantageUnavailable(RuntimeError):
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


# Day the budget was found spent. Reset implicitly by the date rolling over.
_av_unavailable_on: str | None = None

_av_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    retry=retry_if_not_exception_type(AlphaVantageUnavailable),
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
        raise AlphaVantageUnavailable(
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


def fetch_news_headlines(
    ticker: str,
    max_headlines: int = 5,
    alpha_vantage_api_key: str = "",
) -> list[str]:
    """Fetch recent news headlines for a ticker.

    Uses Alpha Vantage NEWS_SENTIMENT when an API key is provided (headlines
    include sentiment labels). Falls back to yfinance when no key is set, when
    the call fails, and for the rest of the day once the budget is spent.
    """
    global _av_unavailable_on

    if alpha_vantage_api_key and _av_unavailable_on != _provider_quota_date():
        try:
            return _fetch_from_alpha_vantage(ticker, alpha_vantage_api_key, max_headlines)
        except AlphaVantageUnavailable as exc:
            # The 26th call of a 25-call day cannot succeed, and neither can the
            # 27th. Stop asking until the date rolls over: retrying a known
            # refusal buys nothing but latency and another log line.
            _av_unavailable_on = _provider_quota_date()
            logger.warning(
                "Alpha Vantage is unavailable for the rest of %s (%s) — "
                "using yfinance headlines from %s onward.",
                _av_unavailable_on, exc, ticker,
            )
        except Exception as exc:
            logger.warning(
                "Alpha Vantage news fetch failed for %s (%s), falling back to yfinance",
                ticker, redact_secret(str(exc), alpha_vantage_api_key),
            )
    return _fetch_from_yfinance(ticker, max_headlines)
