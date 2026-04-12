"""Tests for discord_bot/embeds.py — confidence badge presence/absence."""
import pytest
from discord_bot.embeds import (
    build_recommendation_embed,
    build_sell_embed,
    build_etf_recommendation_embed,
)


def _field_names(embed):
    """Return list of field names from a discord.Embed."""
    return [f.name for f in embed.fields]


def _field_value(embed, name):
    """Return the value of the first field matching name, or None."""
    for f in embed.fields:
        if f.name == name:
            return f.value
    return None


# ---------------------------------------------------------------------------
# build_recommendation_embed — confidence
# ---------------------------------------------------------------------------

def test_recommendation_embed_with_confidence():
    embed = build_recommendation_embed(
        "AAPL", "BUY", "Strong fundamentals.", 150.0, 0.005, 25.0, confidence="high"
    )
    assert "Confidence" in _field_names(embed)
    assert _field_value(embed, "Confidence") == "High"


def test_recommendation_embed_without_confidence():
    embed = build_recommendation_embed(
        "AAPL", "BUY", "Strong fundamentals.", 150.0, 0.005, 25.0
    )
    assert "Confidence" not in _field_names(embed)


def test_recommendation_embed_confidence_none():
    embed = build_recommendation_embed(
        "AAPL", "BUY", "Strong fundamentals.", 150.0, 0.005, 25.0, confidence=None
    )
    assert "Confidence" not in _field_names(embed)


def test_recommendation_embed_confidence_medium():
    embed = build_recommendation_embed(
        "MSFT", "BUY", "Solid.", 300.0, 0.01, 30.0, confidence="medium"
    )
    assert _field_value(embed, "Confidence") == "Medium"


def test_recommendation_embed_confidence_low():
    embed = build_recommendation_embed(
        "TSLA", "HOLD", "Mixed signals.", 200.0, None, None, confidence="low"
    )
    assert _field_value(embed, "Confidence") == "Low"


# ---------------------------------------------------------------------------
# build_sell_embed — confidence
# ---------------------------------------------------------------------------

def test_sell_embed_with_confidence():
    embed = build_sell_embed(
        "AAPL", "Sell reason.", 100.0, 90.0, -0.1, 10, 75.0, confidence="low"
    )
    assert "Confidence" in _field_names(embed)
    assert _field_value(embed, "Confidence") == "Low"


def test_sell_embed_without_confidence():
    embed = build_sell_embed(
        "AAPL", "Sell reason.", 100.0, 90.0, -0.1, 10, 75.0
    )
    assert "Confidence" not in _field_names(embed)


def test_sell_embed_confidence_none():
    embed = build_sell_embed(
        "AAPL", "Sell reason.", 100.0, 90.0, -0.1, 10, 75.0, confidence=None
    )
    assert "Confidence" not in _field_names(embed)


# ---------------------------------------------------------------------------
# build_etf_recommendation_embed — confidence
# ---------------------------------------------------------------------------

def test_etf_embed_with_confidence():
    embed = build_etf_recommendation_embed(
        "SPY", "BUY", "Strong trend.", 450.0, 55.0, 440.0, 0.0003, confidence="medium"
    )
    assert "Confidence" in _field_names(embed)
    assert _field_value(embed, "Confidence") == "Medium"


def test_etf_embed_without_confidence():
    embed = build_etf_recommendation_embed(
        "SPY", "BUY", "Strong trend.", 450.0, 55.0, 440.0, 0.0003
    )
    assert "Confidence" not in _field_names(embed)


def test_etf_embed_confidence_none():
    embed = build_etf_recommendation_embed(
        "QQQ", "BUY", "Tech momentum.", 380.0, 52.0, 370.0, 0.0020, confidence=None
    )
    assert "Confidence" not in _field_names(embed)
