import pytest
from analyst.news import extract_headlines


def make_news_item(title: str) -> dict:
    return {"title": title, "link": "https://example.com", "publisher": "Reuters"}


def test_extract_headlines_returns_titles():
    items = [make_news_item("Stock surges on earnings"), make_news_item("Fed holds rates")]
    assert extract_headlines(items) == ["Stock surges on earnings", "Fed holds rates"]


def test_extract_headlines_limits_count():
    items = [make_news_item(f"Headline {i}") for i in range(10)]
    result = extract_headlines(items, max_headlines=3)
    assert len(result) == 3


def test_extract_headlines_skips_items_without_title():
    items = [
        {"link": "https://example.com"},
        make_news_item("Valid headline"),
    ]
    result = extract_headlines(items)
    assert result == ["Valid headline"]


def test_extract_headlines_returns_empty_list_for_no_items():
    assert extract_headlines([]) == []


def test_extract_headlines_skips_empty_title():
    items = [make_news_item(""), make_news_item("Real news")]
    result = extract_headlines(items)
    assert result == ["Real news"]


def test_extract_headlines_default_limit_is_five():
    items = [make_news_item(f"Headline {i}") for i in range(10)]
    assert len(extract_headlines(items)) == 5


# --- yfinance >= 0.2.51 nested news schema: title lives at item["content"]["title"] ---

def make_nested_news_item(title: str) -> dict:
    return {"id": "abc-123", "content": {"title": title, "contentType": "STORY"}}


def test_extract_headlines_reads_nested_content_title():
    items = [make_nested_news_item("Apple unveils new chip"), make_nested_news_item("Fed cuts rates")]
    assert extract_headlines(items) == ["Apple unveils new chip", "Fed cuts rates"]


def test_extract_headlines_mixed_schemas():
    items = [make_nested_news_item("Nested headline"), make_news_item("Flat headline")]
    assert extract_headlines(items) == ["Nested headline", "Flat headline"]


def test_extract_headlines_nested_without_title_skipped():
    items = [{"id": "x", "content": {"contentType": "VIDEO"}}, make_nested_news_item("Real news")]
    assert extract_headlines(items) == ["Real news"]


def test_extract_headlines_content_not_dict_falls_back():
    # Defensive: content key present but not a dict should not crash
    items = [{"content": "weird string", "title": "Flat title wins"}]
    assert extract_headlines(items) == ["Flat title wins"]


# ---------------------------------------------------------------------------
# Alpha Vantage: credential redaction, quota handling, and the day breaker.
#
# This path had no tests at all, which is how a provider error message that
# quotes the caller's own API key came to be logged verbatim 43 times.
# ---------------------------------------------------------------------------

import json
import logging
from unittest.mock import patch

from analyst import news


# The real shape of Alpha Vantage's free-tier refusal: it echoes the key back.
_KEY = "TESTKEY0000ABCD1"
_QUOTA_BODY = {
    "Information": (
        f"We have detected your API key as {_KEY} and our standard API rate limit "
        "is 25 requests per day. Please subscribe to any of the premium plans at "
        "https://www.alphavantage.co/premium/ to instantly remove all daily rate limits."
    )
}


class _FakeResponse:
    """Minimal stand-in for the context manager urlopen returns."""

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _reset_breaker(monkeypatch):
    """Each test starts with the Alpha Vantage day-breaker disarmed."""
    monkeypatch.setattr(news, "_av_unavailable_on", None)


def test_quota_refusal_does_not_leak_the_api_key_into_the_log(monkeypatch, caplog):
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_QUOTA_BODY)), \
         patch.object(news, "_fetch_from_yfinance", return_value=["fallback headline"]):
        with caplog.at_level(logging.WARNING):
            result = news.fetch_news_headlines("AAPL", alpha_vantage_api_key=_KEY)

    assert result == ["fallback headline"]
    assert _KEY not in caplog.text


def test_redact_secret_replaces_every_occurrence():
    assert news.redact_secret("key=ABC and again ABC", "ABC") == "key=*** and again ***"


def test_redact_secret_leaves_text_alone_when_the_secret_is_empty():
    # "".replace("", "***") would corrupt every character boundary.
    assert news.redact_secret("nothing to hide", "") == "nothing to hide"


def test_a_quota_refusal_is_not_retried():
    calls = []

    def _spy(*args, **kwargs):
        calls.append(1)
        return _FakeResponse(_QUOTA_BODY)

    with patch("urllib.request.urlopen", side_effect=_spy):
        with pytest.raises(news.AlphaVantageUnavailable):
            news._fetch_from_alpha_vantage("AAPL", _KEY)

    assert len(calls) == 1, "a spent daily budget cannot succeed on retry"


def test_a_transient_failure_is_still_retried(monkeypatch):
    calls = []

    def _spy(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("connection reset")

    # tenacity hangs the Retrying instance off the wrapped function, and
    # replacing its sleep is the hook that lands -- the wait strategy itself
    # was bound at import time, so patching that is a no-op. Without this the
    # test spends 4 real seconds asleep.
    monkeypatch.setattr(news._fetch_from_alpha_vantage.retry, "sleep", lambda *_: None)

    with patch("urllib.request.urlopen", side_effect=_spy):
        with pytest.raises(TimeoutError):
            news._fetch_from_alpha_vantage("AAPL", _KEY)

    assert len(calls) == 3, "a network blip is worth retrying"


def test_alpha_vantage_is_skipped_for_the_rest_of_the_day_after_a_quota_refusal():
    calls = []

    def _spy(*args, **kwargs):
        calls.append(1)
        return _FakeResponse(_QUOTA_BODY)

    with patch("urllib.request.urlopen", side_effect=_spy), \
         patch.object(news, "_fetch_from_yfinance", return_value=["fallback"]):
        news.fetch_news_headlines("AAPL", alpha_vantage_api_key=_KEY)
        news.fetch_news_headlines("MSFT", alpha_vantage_api_key=_KEY)
        news.fetch_news_headlines("NVDA", alpha_vantage_api_key=_KEY)

    assert len(calls) == 1, "only the first ticker should pay for the refusal"


def test_the_day_breaker_re_arms_on_a_new_day(monkeypatch):
    calls = []

    def _spy(*args, **kwargs):
        calls.append(1)
        return _FakeResponse(_QUOTA_BODY)

    day = {"value": "2026-09-15"}
    monkeypatch.setattr(news, "_provider_quota_date", lambda: day["value"])

    with patch("urllib.request.urlopen", side_effect=_spy),          patch.object(news, "_fetch_from_yfinance", return_value=["fallback"]):
        news.fetch_news_headlines("AAPL", alpha_vantage_api_key=_KEY)   # pays for the refusal
        news.fetch_news_headlines("MSFT", alpha_vantage_api_key=_KEY)   # skipped
        day["value"] = "2026-09-16"
        news.fetch_news_headlines("NVDA", alpha_vantage_api_key=_KEY)   # fresh budget

    assert len(calls) == 2, "a new day gets a fresh budget"


def test_a_successful_fetch_returns_sentiment_tagged_headlines():
    body = {
        "feed": [
            {"title": "Chip demand surges", "overall_sentiment_label": "Bullish",
             "overall_sentiment_score": 0.7213},
        ]
    }
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        assert news._fetch_from_alpha_vantage("NVDA", _KEY) == [
            "Chip demand surges [Bullish, 0.72]"
        ]


def test_a_successful_fetch_does_not_arm_the_breaker():
    body = {"feed": [{"title": "All quiet"}]}
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        news.fetch_news_headlines("NVDA", alpha_vantage_api_key=_KEY)
    assert news._av_unavailable_on is None
