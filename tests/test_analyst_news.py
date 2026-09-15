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

import datetime as _dt
import io
import json
import pathlib
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
    monkeypatch.setattr(news, "_unavailable_on", {})


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
        with pytest.raises(news.NewsProviderUnavailable):
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
    assert news._unavailable_on == {}


# ---------------------------------------------------------------------------
# Finnhub, and the provider chain: Finnhub -> Alpha Vantage -> yfinance.
#
# Shapes below are what the live API actually returned on 2026-09-15, not what
# the docs describe -- the docs say `datetime` is ISO 8601; it is a Unix epoch
# int. A bad token answers HTTP 401 with {"error":"Invalid API key"} and does
# NOT echo the token, unlike Alpha Vantage.
# ---------------------------------------------------------------------------

import urllib.error

_FINNHUB_KEY = "FINNHUBKEY000000000000000000000000000000"


def _finnhub_item(headline: str, ts: int = 1789477500) -> dict:
    return {
        "category": "company", "datetime": ts, "headline": headline,
        "id": 1234, "image": "https://example.com/i.png", "related": "AAPL",
        "source": "Yahoo", "summary": "...", "url": "https://example.com/a",
    }


def _http_error(code: int, body: bytes = b'{"error":"Invalid API key"}'):
    return urllib.error.HTTPError(
        url="https://finnhub.io/api/v1/company-news?token=SECRET",
        code=code, msg="err", hdrs=None, fp=io.BytesIO(body),
    )


def test_finnhub_returns_headlines_newest_first():
    body = [_finnhub_item(f"Headline {i}", ts=1789477500 - i) for i in range(8)]
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        assert news._fetch_from_finnhub("AAPL", _FINNHUB_KEY) == [
            f"Headline {i}" for i in range(5)
        ]


def test_finnhub_skips_items_without_a_headline():
    body = [_finnhub_item(""), {"category": "company"}, _finnhub_item("Real news")]
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        assert news._fetch_from_finnhub("AAPL", _FINNHUB_KEY) == ["Real news"]


def test_finnhub_requests_a_window_ending_today():
    seen = {}

    def _spy(url, *a, **kw):
        seen["url"] = url
        return _FakeResponse([_finnhub_item("x")])

    with patch("urllib.request.urlopen", side_effect=_spy):
        news._fetch_from_finnhub("AAPL", _FINNHUB_KEY)

    today = _dt.datetime.now(_dt.timezone.utc).date()
    assert f"to={today.isoformat()}" in seen["url"]
    assert f"from={(today - _dt.timedelta(days=news._FINNHUB_WINDOW_DAYS)).isoformat()}" in seen["url"]


def test_finnhub_rejects_a_bad_key_without_retrying():
    calls = []

    def _spy(*a, **kw):
        calls.append(1)
        raise _http_error(401)

    with patch("urllib.request.urlopen", side_effect=_spy):
        with pytest.raises(news.NewsProviderUnavailable):
            news._fetch_from_finnhub("AAPL", _FINNHUB_KEY)

    assert len(calls) == 1, "a rejected key cannot start working on retry"


def test_finnhub_rate_limit_is_retried(monkeypatch):
    # Finnhub's 429 is a PER-MINUTE cap, not a daily budget: unlike Alpha
    # Vantage's refusal, backing off and trying again can genuinely succeed.
    calls = []

    def _spy(*a, **kw):
        calls.append(1)
        raise _http_error(429, b'{"error":"API limit reached"}')

    monkeypatch.setattr(news._fetch_from_finnhub.retry, "sleep", lambda *_: None)
    with patch("urllib.request.urlopen", side_effect=_spy):
        with pytest.raises(urllib.error.HTTPError):
            news._fetch_from_finnhub("AAPL", _FINNHUB_KEY)

    assert len(calls) == 3


def test_a_finnhub_failure_never_logs_the_token(monkeypatch, caplog):
    # Finnhub carries the key in the query string, so any message quoting the
    # URL leaks it -- the Alpha Vantage lesson applied before it bites.
    def _spy(*a, **kw):
        raise RuntimeError(f"failed calling ...&token={_FINNHUB_KEY}")

    monkeypatch.setattr(news._fetch_from_finnhub.retry, "sleep", lambda *_: None)

    with patch("urllib.request.urlopen", side_effect=_spy), \
         patch.object(news, "_fetch_from_yfinance", return_value=["fallback"]):
        with caplog.at_level(logging.WARNING):
            news.fetch_news_headlines("AAPL", finnhub_api_key=_FINNHUB_KEY)

    assert _FINNHUB_KEY not in caplog.text


def test_the_chain_prefers_finnhub_over_alpha_vantage():
    with patch.object(news, "_fetch_from_finnhub", return_value=["from finnhub"]) as fh, \
         patch.object(news, "_fetch_from_alpha_vantage") as av, \
         patch.object(news, "_fetch_from_yfinance") as yf:
        result = news.fetch_news_headlines(
            "AAPL", alpha_vantage_api_key=_KEY, finnhub_api_key=_FINNHUB_KEY
        )

    assert result == ["from finnhub"]
    assert fh.called and not av.called and not yf.called


def test_alpha_vantage_covers_a_finnhub_failure():
    with patch.object(news, "_fetch_from_finnhub", side_effect=RuntimeError("boom")), \
         patch.object(news, "_fetch_from_alpha_vantage", return_value=["from av"]), \
         patch.object(news, "_fetch_from_yfinance") as yf:
        result = news.fetch_news_headlines(
            "AAPL", alpha_vantage_api_key=_KEY, finnhub_api_key=_FINNHUB_KEY
        )

    assert result == ["from av"]
    assert not yf.called


def test_yfinance_is_the_last_resort():
    with patch.object(news, "_fetch_from_finnhub", side_effect=RuntimeError("boom")), \
         patch.object(news, "_fetch_from_alpha_vantage", side_effect=RuntimeError("boom")), \
         patch.object(news, "_fetch_from_yfinance", return_value=["from yf"]):
        assert news.fetch_news_headlines(
            "AAPL", alpha_vantage_api_key=_KEY, finnhub_api_key=_FINNHUB_KEY
        ) == ["from yf"]


def test_an_empty_provider_result_falls_through_to_the_next():
    # A ticker Finnhub has no coverage for should still get a second chance,
    # rather than reaching the analyst with no headlines at all.
    with patch.object(news, "_fetch_from_finnhub", return_value=[]), \
         patch.object(news, "_fetch_from_alpha_vantage", return_value=["from av"]):
        assert news.fetch_news_headlines(
            "AAPL", alpha_vantage_api_key=_KEY, finnhub_api_key=_FINNHUB_KEY
        ) == ["from av"]


def test_a_provider_without_a_key_is_skipped():
    with patch.object(news, "_fetch_from_finnhub") as fh, \
         patch.object(news, "_fetch_from_alpha_vantage", return_value=["from av"]):
        news.fetch_news_headlines("AAPL", alpha_vantage_api_key=_KEY)
    assert not fh.called


def test_a_spent_alpha_vantage_budget_does_not_disable_finnhub():
    # The breaker is per provider. One shared flag would let Alpha Vantage's
    # 25/day cap silently switch off a provider with a 60/MINUTE limit.
    with patch.object(news, "_fetch_from_alpha_vantage",
                      side_effect=news.NewsProviderUnavailable("spent")), \
         patch.object(news, "_fetch_from_finnhub", return_value=[]), \
         patch.object(news, "_fetch_from_yfinance", return_value=["yf"]):
        news.fetch_news_headlines("A", alpha_vantage_api_key=_KEY, finnhub_api_key=_FINNHUB_KEY)

    assert "alpha_vantage" in news._unavailable_on
    assert "finnhub" not in news._unavailable_on


def test_a_finnhub_refusal_arms_only_the_finnhub_breaker():
    with patch.object(news, "_fetch_from_finnhub",
                      side_effect=news.NewsProviderUnavailable("bad key")), \
         patch.object(news, "_fetch_from_alpha_vantage", return_value=["av"]):
        news.fetch_news_headlines("A", alpha_vantage_api_key=_KEY, finnhub_api_key=_FINNHUB_KEY)

    assert "finnhub" in news._unavailable_on
    assert "alpha_vantage" not in news._unavailable_on


# ---------------------------------------------------------------------------
# Wiring. Structural, by AST, for the same reason test_schwab_login.py is: a
# provider that is configured but never passed through is invisible to every
# behavioural test -- FINNHUB_API_KEY sat in .env doing nothing until this.
# ---------------------------------------------------------------------------

import ast


def _news_call_keywords():
    """Every fetch_news_headlines call site in main.py, as keyword-name sets."""
    tree = ast.parse(pathlib.Path("main.py").read_text(encoding="utf-8"))
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "fetch_news_headlines":
            sites.append({kw.arg for kw in node.keywords})
        # asyncio.to_thread(fetch_news_headlines, ticker, key=...) -- the real
        # shape in this codebase, where the callable is the first argument.
        elif node.args and getattr(node.args[0], "id", None) == "fetch_news_headlines":
            sites.append({kw.arg for kw in node.keywords})
    return sites


def test_every_news_call_site_passes_both_provider_keys():
    sites = _news_call_keywords()
    assert sites, "no fetch_news_headlines call sites found in main.py"
    for kwargs in sites:
        assert "finnhub_api_key" in kwargs, f"call site missing finnhub key: {kwargs}"
        assert "alpha_vantage_api_key" in kwargs, f"call site missing AV key: {kwargs}"


def test_config_exposes_a_finnhub_key():
    from config import Config
    assert hasattr(Config(), "finnhub_api_key")
