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


# ---------------------------------------------------------------------------
# Phase 13 Task 2: build_stats_embed tests
# ---------------------------------------------------------------------------

def _make_stats(total=12, wins=7, losses=5, win_rate=None, avg_gain_pct=0.042, avg_loss_pct=-0.021):
    if win_rate is None:
        win_rate = wins / total
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_gain_pct": avg_gain_pct,
        "avg_loss_pct": avg_loss_pct,
    }


def test_build_stats_embed_fields():
    """build_stats_embed produces Win Rate, Avg Gain, Avg Loss fields and description with 'Closed Trades: 12'."""
    from discord_bot.embeds import build_stats_embed
    stats = _make_stats()
    embed = build_stats_embed(stats)
    assert "Win Rate" in _field_names(embed)
    assert "Avg Gain" in _field_names(embed)
    assert "Avg Loss" in _field_names(embed)
    assert "Closed Trades: 12" in embed.description


def test_build_stats_embed_title_and_color():
    """build_stats_embed title is 'Trade Statistics'."""
    import discord
    from discord_bot.embeds import build_stats_embed
    embed = build_stats_embed(_make_stats())
    assert embed.title == "Trade Statistics"
    assert embed.color == discord.Color.blurple()


def test_build_stats_embed_avg_gain_prefix():
    """Avg Gain field value starts with '+'."""
    from discord_bot.embeds import build_stats_embed
    embed = build_stats_embed(_make_stats(avg_gain_pct=0.042))
    assert _field_value(embed, "Avg Gain").startswith("+")


def test_build_stats_embed_avg_gain_none():
    """avg_gain_pct=None → Avg Gain field value is 'N/A'."""
    from discord_bot.embeds import build_stats_embed
    embed = build_stats_embed(_make_stats(wins=0, losses=3, avg_gain_pct=None))
    assert _field_value(embed, "Avg Gain") == "N/A"


def test_build_stats_embed_avg_loss_negative():
    """avg_loss_pct is negative — Avg Loss field value starts with '-'."""
    from discord_bot.embeds import build_stats_embed
    embed = build_stats_embed(_make_stats(avg_loss_pct=-0.021))
    val = _field_value(embed, "Avg Loss")
    assert val.startswith("-"), f"Expected negative loss, got: {val}"
