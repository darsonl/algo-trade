"""Tests for sell embed formatting — SELL-05."""
import discord
from discord_bot.embeds import build_sell_embed, _SIGNAL_COLORS


def test_sell_in_signal_colors():
    assert "SELL" in _SIGNAL_COLORS
    assert _SIGNAL_COLORS["SELL"] == discord.Color.red()


def test_build_sell_embed_title():
    embed = build_sell_embed("AAPL", "Overbought", 150.0, 170.0, 0.133, 10, 72.5)
    assert embed.title == "AAPL — SELL"


def test_build_sell_embed_color_is_red():
    embed = build_sell_embed("AAPL", "Overbought", 150.0, 170.0, 0.133, 10, 72.5)
    assert embed.color == discord.Color.red()


def test_build_sell_embed_has_five_fields():
    embed = build_sell_embed("AAPL", "Overbought", 150.0, 170.0, 0.133, 10, 72.5)
    assert len(embed.fields) == 5
    field_names = [f.name for f in embed.fields]
    assert "Entry Price" in field_names
    assert "Current Price" in field_names
    assert "P&L" in field_names
    assert "Shares" in field_names
    assert "RSI" in field_names


def test_build_sell_embed_field_values():
    embed = build_sell_embed("MSFT", "Take profits", 300.0, 350.0, 0.1667, 5, 78.0)
    values = {f.name: f.value for f in embed.fields}
    assert values["Entry Price"] == "$300.00"
    assert values["Current Price"] == "$350.00"
    assert "+16.7%" in values["P&L"]
    assert "5" in values["Shares"]
    assert "78.0" in values["RSI"]


def test_build_sell_embed_negative_pnl():
    embed = build_sell_embed("TSLA", "Cut losses", 200.0, 180.0, -0.1, 3, 72.0)
    values = {f.name: f.value for f in embed.fields}
    assert "-10.0%" in values["P&L"]


def test_build_sell_embed_reasoning():
    embed = build_sell_embed("AAPL", "RSI indicates overbought", 150.0, 170.0, 0.133, 10, 72.5)
    assert embed.description == "RSI indicates overbought"


def test_build_market_sell():
    from schwab_client.orders import build_market_sell
    spec = build_market_sell("AAPL", 10)
    assert spec is not None
    assert isinstance(spec, dict)
