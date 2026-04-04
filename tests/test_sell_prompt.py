"""Tests for sell prompt building and SELL signal parsing — SELL-01."""
from analyst.claude_analyst import build_sell_prompt, parse_claude_response, _VALID_SIGNALS


def test_sell_in_valid_signals():
    assert "SELL" in _VALID_SIGNALS


def test_build_sell_prompt_includes_position_data():
    prompt = build_sell_prompt(
        ticker="AAPL",
        entry_price=150.0,
        current_price=170.0,
        pnl_pct=0.1333,
        hold_days=30,
        rsi=72.5,
        headlines=["Apple beats earnings", "iPhone sales surge"],
    )
    assert "AAPL" in prompt
    assert "$150.00" in prompt   # entry price
    assert "$170.00" in prompt   # current price
    assert "+13.3%" in prompt    # P&L
    assert "30 days" in prompt   # hold duration
    assert "72.5" in prompt      # RSI
    assert "Apple beats earnings" in prompt
    assert "iPhone sales surge" in prompt
    assert "SELL" in prompt      # sell signal option present
    assert "HOLD" in prompt      # hold signal option present


def test_build_sell_prompt_no_headlines():
    prompt = build_sell_prompt("AAPL", 100.0, 90.0, -0.1, 10, 75.0, [])
    assert "No recent headlines available" in prompt


def test_build_sell_prompt_negative_pnl():
    prompt = build_sell_prompt("TSLA", 200.0, 180.0, -0.1, 5, 71.0, [])
    assert "-10.0%" in prompt


def test_build_sell_prompt_returns_string():
    prompt = build_sell_prompt("MSFT", 300.0, 350.0, 0.1667, 15, 78.0, ["Headline"])
    assert isinstance(prompt, str)
    assert len(prompt) > 50


def test_build_sell_prompt_rsi_value_formatted():
    prompt = build_sell_prompt("AAPL", 150.0, 170.0, 0.133, 30, 72.5, [])
    assert "72.5" in prompt


def test_parse_sell_signal():
    text = "SIGNAL: SELL\nREASONING: Stock is overbought with weakening fundamentals."
    result = parse_claude_response(text)
    assert result["signal"] == "SELL"
    assert "overbought" in result["reasoning"]


def test_parse_hold_signal_from_sell_analysis():
    text = "SIGNAL: HOLD\nREASONING: Despite high RSI, momentum remains strong."
    result = parse_claude_response(text)
    assert result["signal"] == "HOLD"
    assert "momentum" in result["reasoning"]
