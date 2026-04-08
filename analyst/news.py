import logging
import urllib.request
import urllib.parse
import json
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)

_AV_NEWS_URL = "https://www.alphavantage.co/query"


def extract_headlines(news_items: list[dict], max_headlines: int = 5) -> list[str]:
    """Extract headline strings from yfinance news dicts, skipping items without a title."""
    headlines = []
    for item in news_items:
        title = item.get("title", "").strip()
        if title:
            headlines.append(title)
        if len(headlines) == max_headlines:
            break
    return headlines


@_retry
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
        # Rate limit or invalid key message from Alpha Vantage
        raise RuntimeError(f"Alpha Vantage API error: {data['Information']}")

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
    include sentiment labels). Falls back to yfinance when no key is set.
    """
    if alpha_vantage_api_key:
        try:
            return _fetch_from_alpha_vantage(ticker, alpha_vantage_api_key, max_headlines)
        except Exception as exc:
            logger.warning(
                "Alpha Vantage news fetch failed for %s (%s), falling back to yfinance", ticker, exc
            )
    return _fetch_from_yfinance(ticker, max_headlines)
