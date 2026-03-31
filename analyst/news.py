from tenacity import retry, stop_after_attempt, wait_exponential

_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)


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
def fetch_news_headlines(ticker: str, max_headlines: int = 5) -> list[str]:
    """Fetch recent news headlines for a ticker via yfinance."""
    import yfinance as yf
    news = yf.Ticker(ticker).news
    return extract_headlines(news or [], max_headlines=max_headlines)
