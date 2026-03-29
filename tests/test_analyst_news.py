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
