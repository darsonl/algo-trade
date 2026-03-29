import discord
import pytest
from discord_bot.embeds import build_recommendation_embed


def make_embed(signal="BUY", ticker="AAPL", price=175.50, div_yield=0.006, pe_ratio=24.5):
    return build_recommendation_embed(
        ticker=ticker,
        signal=signal,
        reasoning="Strong fundamentals and positive momentum.",
        price=price,
        dividend_yield=div_yield,
        pe_ratio=pe_ratio,
    )


# --- Title ---

def test_embed_title_contains_ticker():
    embed = make_embed(ticker="JNJ")
    assert "JNJ" in embed.title


def test_embed_title_contains_signal():
    embed = make_embed(signal="BUY")
    assert "BUY" in embed.title


# --- Color encodes signal ---

def test_embed_color_green_for_buy():
    embed = make_embed(signal="BUY")
    assert embed.color == discord.Color.green()


def test_embed_color_yellow_for_hold():
    embed = make_embed(signal="HOLD")
    assert embed.color == discord.Color.yellow()


def test_embed_color_red_for_skip():
    embed = make_embed(signal="SKIP")
    assert embed.color == discord.Color.red()


# --- Reasoning in description ---

def test_embed_description_contains_reasoning():
    embed = make_embed()
    assert "Strong fundamentals and positive momentum." in embed.description


# --- Fields ---

def _field_values(embed: discord.Embed) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields}


def test_embed_has_price_field():
    embed = make_embed(price=175.50)
    fields = _field_values(embed)
    assert any("Price" in name for name in fields)
    assert "175.50" in list(fields.values())[0] or any("175.50" in v for v in fields.values())


def test_embed_has_dividend_yield_field():
    embed = make_embed(div_yield=0.006)
    fields = _field_values(embed)
    assert any("Dividend" in name or "Yield" in name for name in fields)


def test_embed_has_pe_ratio_field():
    embed = make_embed(pe_ratio=24.5)
    fields = _field_values(embed)
    assert any("P/E" in name or "PE" in name for name in fields)


def test_embed_shows_na_for_missing_dividend_yield():
    embed = build_recommendation_embed(
        ticker="MSFT", signal="BUY", reasoning="Good.",
        price=400.0, dividend_yield=None, pe_ratio=30.0,
    )
    values = " ".join(f.value for f in embed.fields)
    assert "N/A" in values


def test_embed_shows_na_for_missing_pe_ratio():
    embed = build_recommendation_embed(
        ticker="MSFT", signal="BUY", reasoning="Good.",
        price=400.0, dividend_yield=0.007, pe_ratio=None,
    )
    values = " ".join(f.value for f in embed.fields)
    assert "N/A" in values


def test_embed_raises_on_invalid_signal():
    with pytest.raises(ValueError, match="signal"):
        build_recommendation_embed(
            ticker="AAPL", signal="MAYBE", reasoning="Dunno.",
            price=100.0, dividend_yield=None, pe_ratio=None,
        )
