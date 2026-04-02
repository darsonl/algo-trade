import pytest
import os
from database.models import initialize_db
from database.queries import get_cached_analysis, set_cached_analysis


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test_cache.db")
    initialize_db(path)
    return path


def test_cache_miss_returns_none(db_path):
    result = get_cached_analysis(db_path, "AAPL", "nonexistent_hash")
    assert result is None


def test_cache_hit_returns_dict(db_path):
    set_cached_analysis(
        db_path,
        "AAPL",
        "abc123",
        "BUY",
        "Strong earnings growth.",
    )
    result = get_cached_analysis(db_path, "AAPL", "abc123")
    assert result == {"signal": "BUY", "reasoning": "Strong earnings growth."}


def test_set_cached_analysis_upserts(db_path):
    set_cached_analysis(db_path, "AAPL", "abc123", "BUY", "First reasoning.")
    set_cached_analysis(db_path, "AAPL", "abc123", "HOLD", "Updated reasoning.")
    result = get_cached_analysis(db_path, "AAPL", "abc123")
    assert result["signal"] == "HOLD"
    assert result["reasoning"] == "Updated reasoning."


def test_same_hash_different_tickers_are_independent(db_path):
    shared_hash = "shared_hash_001"
    set_cached_analysis(db_path, "AAPL", shared_hash, "BUY", "Apple looks strong.")
    set_cached_analysis(db_path, "MSFT", shared_hash, "HOLD", "Microsoft is neutral.")

    aapl_result = get_cached_analysis(db_path, "AAPL", shared_hash)
    msft_result = get_cached_analysis(db_path, "MSFT", shared_hash)

    assert aapl_result == {"signal": "BUY", "reasoning": "Apple looks strong."}
    assert msft_result == {"signal": "HOLD", "reasoning": "Microsoft is neutral."}


def test_get_cached_analysis_wrong_hash_returns_none(db_path):
    set_cached_analysis(db_path, "AAPL", "abc123", "BUY", "Strong earnings growth.")
    result = get_cached_analysis(db_path, "AAPL", "xyz999")
    assert result is None
