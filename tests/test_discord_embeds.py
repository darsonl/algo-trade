import discord
import pytest
from discord_bot.embeds import build_recommendation_embed, build_etf_recommendation_embed


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


# --- build_etf_recommendation_embed ---

def make_etf_embed(
    ticker="SPY",
    signal="BUY",
    reasoning="Strong uptrend with low expense ratio.",
    price=450.0,
    rsi=65.0,
    ma50=440.0,
    expense_ratio=0.0009,
):
    return build_etf_recommendation_embed(
        ticker=ticker,
        signal=signal,
        reasoning=reasoning,
        price=price,
        rsi=rsi,
        ma50=ma50,
        expense_ratio=expense_ratio,
    )


def test_etf_embed_buy_signal_is_green():
    """Test 1: build_etf_recommendation_embed with signal='BUY' returns embed with green color."""
    embed = make_etf_embed(signal="BUY")
    assert embed.color == discord.Color.green()


def test_etf_embed_has_required_fields():
    """Test 2: build_etf_recommendation_embed includes fields: Price, RSI, MA50 Trend, Expense Ratio."""
    embed = make_etf_embed()
    field_names = [f.name for f in embed.fields]
    assert "Price" in field_names
    assert "RSI" in field_names
    assert "MA50 Trend" in field_names
    assert "Expense Ratio" in field_names


def test_etf_embed_expense_ratio_none_shows_na():
    """Test 3: build_etf_recommendation_embed with expense_ratio=None shows 'N/A' for Expense Ratio field."""
    embed = build_etf_recommendation_embed(
        ticker="BND",
        signal="HOLD",
        reasoning="Neutral bond ETF.",
        price=75.0,
        rsi=50.0,
        ma50=74.0,
        expense_ratio=None,
    )
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Expense Ratio"] == "N/A"


def test_etf_embed_rsi_none_shows_na():
    """Test 4: build_etf_recommendation_embed with rsi=None shows 'N/A' for RSI field."""
    embed = build_etf_recommendation_embed(
        ticker="VTI",
        signal="BUY",
        reasoning="Broad market ETF.",
        price=220.0,
        rsi=None,
        ma50=215.0,
        expense_ratio=0.0003,
    )
    fields = {f.name: f.value for f in embed.fields}
    assert fields["RSI"] == "N/A"


def test_etf_embed_title_format():
    """Test 5: build_etf_recommendation_embed title format is '{ticker} — {signal} [ETF]'."""
    embed = make_etf_embed(ticker="QQQ", signal="SKIP")
    assert embed.title == "QQQ — SKIP [ETF]"


def test_etf_embed_hold_signal_is_yellow():
    """Test 6: build_etf_recommendation_embed with signal='HOLD' returns embed with yellow color."""
    embed = make_etf_embed(signal="HOLD")
    assert embed.color == discord.Color.yellow()


# --- earnings_date field (SIG-05) ---

def test_embed_earnings_date_shows_future_date():
    """Test A: build_recommendation_embed with earnings_date='Dec 15, 2025' shows that value in 'Next Earnings' field."""
    embed = build_recommendation_embed(
        ticker="AAPL",
        signal="BUY",
        reasoning="Strong outlook.",
        price=175.0,
        dividend_yield=0.005,
        pe_ratio=24.0,
        earnings_date="Dec 15, 2025",
    )
    fields = _field_values(embed)
    assert fields["Next Earnings"] == "Dec 15, 2025"


def test_embed_earnings_date_shows_na_when_none():
    """Test B: build_recommendation_embed with no earnings_date kwarg has NO 'Next Earnings' field (backward compat)."""
    embed = build_recommendation_embed(
        ticker="AAPL",
        signal="BUY",
        reasoning="Strong outlook.",
        price=175.0,
        dividend_yield=0.005,
        pe_ratio=24.0,
    )
    fields = _field_values(embed)
    assert "Next Earnings" not in fields


def test_embed_earnings_date_shows_na_string():
    """Test C: build_recommendation_embed with earnings_date='N/A' shows 'N/A' in 'Next Earnings' field."""
    embed = build_recommendation_embed(
        ticker="AAPL",
        signal="BUY",
        reasoning="Strong outlook.",
        price=175.0,
        dividend_yield=0.005,
        pe_ratio=24.0,
        earnings_date="N/A",
    )
    fields = _field_values(embed)
    assert fields["Next Earnings"] == "N/A"


def test_embed_earnings_date_warning_prefix():
    """Test D: build_recommendation_embed with earnings_date='⚠️ Dec 18, 2025' shows that value in 'Next Earnings' field."""
    embed = build_recommendation_embed(
        ticker="AAPL",
        signal="BUY",
        reasoning="Strong outlook.",
        price=175.0,
        dividend_yield=0.005,
        pe_ratio=24.0,
        earnings_date="⚠️ Dec 18, 2025",
    )
    fields = _field_values(embed)
    assert fields["Next Earnings"] == "⚠️ Dec 18, 2025"


def test_embed_earnings_date_field_is_inline():
    """Test E: 'Next Earnings' field has inline=True (per D-05)."""
    embed = build_recommendation_embed(
        ticker="AAPL",
        signal="BUY",
        reasoning="Strong outlook.",
        price=175.0,
        dividend_yield=0.005,
        pe_ratio=24.0,
        earnings_date="Dec 15, 2025",
    )
    next_earnings_field = next(f for f in embed.fields if f.name == "Next Earnings")
    assert next_earnings_field.inline is True


def test_embed_earnings_date_is_last_field_after_confidence():
    """Test F: 'Next Earnings' is the last field, 'Confidence' is second-to-last (per D-04)."""
    embed = build_recommendation_embed(
        ticker="AAPL",
        signal="BUY",
        reasoning="Strong outlook.",
        price=175.0,
        dividend_yield=0.005,
        pe_ratio=24.0,
        confidence="high",
        earnings_date="Dec 15, 2025",
    )
    assert embed.fields[-1].name == "Next Earnings"
    assert embed.fields[-2].name == "Confidence"
