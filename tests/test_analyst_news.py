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
