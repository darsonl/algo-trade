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


# ---------------------------------------------------------------------------
# Phase 14 Task 2: build_history_embed tests
# ---------------------------------------------------------------------------

def _make_trade(ticker="AAPL", cost_basis=100.0, price=110.0, executed_at="2026-04-01T10:00:00"):
    """Helper to build a minimal closed-trade dict mirroring get_closed_trades output."""
    return {
        "ticker": ticker,
        "cost_basis": cost_basis,
        "price": price,
        "executed_at": executed_at,
    }


def test_build_history_embed_title():
    """embed.title == 'Trade History'."""
    from discord_bot.embeds import build_history_embed
    embed = build_history_embed([_make_trade()])
    assert embed.title == "Trade History"


def test_build_history_embed_color():
    """embed.color == discord.Color.blurple()."""
    import discord
    from discord_bot.embeds import build_history_embed
    embed = build_history_embed([_make_trade()])
    assert embed.color == discord.Color.blurple()


def test_build_history_embed_description_is_code_block():
    """embed.description starts and ends with triple-backtick fence."""
    from discord_bot.embeds import build_history_embed
    embed = build_history_embed([_make_trade()])
    assert embed.description.startswith("```")
    assert embed.description.endswith("```")


def test_build_history_embed_header_row():
    """Description contains the header with expected column names."""
    from discord_bot.embeds import build_history_embed
    embed = build_history_embed([_make_trade()])
    assert "Ticker" in embed.description
    assert "Entry" in embed.description
    assert "Exit" in embed.description
    assert "P&L%" in embed.description
    assert "Date" in embed.description


def test_build_history_embed_row_count():
    """Given 3 trades, the description contains exactly 3 data rows."""
    from discord_bot.embeds import build_history_embed
    trades = [
        _make_trade(ticker="AAPL", executed_at="2026-04-01T10:00:00"),
        _make_trade(ticker="MSFT", executed_at="2026-04-02T10:00:00"),
        _make_trade(ticker="GOOG", executed_at="2026-04-03T10:00:00"),
    ]
    embed = build_history_embed(trades)
    # Strip the code fence and header to count data rows
    lines = embed.description.strip("`").strip().splitlines()
    # lines[0] is header, rest are data rows
    data_rows = [l for l in lines[1:] if l.strip()]
    assert len(data_rows) == 3


def test_build_history_embed_gain_sign():
    """A trade with price=110, cost_basis=100 renders '+10.0%'."""
    from discord_bot.embeds import build_history_embed
    embed = build_history_embed([_make_trade(cost_basis=100.0, price=110.0)])
    assert "+10.0%" in embed.description


def test_build_history_embed_loss_sign():
    """A trade with price=90, cost_basis=100 renders '-10.0%'."""
    from discord_bot.embeds import build_history_embed
    embed = build_history_embed([_make_trade(cost_basis=100.0, price=90.0)])
    assert "-10.0%" in embed.description


def test_build_history_embed_date_format():
    """executed_at='2026-04-01T14:30:00' renders as '2026-04-01' in the row."""
    from discord_bot.embeds import build_history_embed
    embed = build_history_embed([_make_trade(executed_at="2026-04-01T14:30:00")])
    assert "2026-04-01" in embed.description
    assert "14:30" not in embed.description


def test_build_history_embed_ticker_and_prices():
    """A trade with ticker='AAPL', cost_basis=150.25, price=165.50 produces a row with all three."""
    from discord_bot.embeds import build_history_embed
    embed = build_history_embed([_make_trade(ticker="AAPL", cost_basis=150.25, price=165.50)])
    assert "AAPL" in embed.description
    assert "150.25" in embed.description
    assert "165.50" in embed.description
