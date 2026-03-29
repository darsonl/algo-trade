import pytest
from analyst.claude_analyst import build_prompt, parse_claude_response


# --- build_prompt ---

def test_build_prompt_contains_ticker():
    prompt = build_prompt("AAPL", {}, [])
    assert "AAPL" in prompt


def test_build_prompt_contains_headlines():
    headlines = ["Apple beats Q3 estimates", "iPhone sales up 12%"]
    prompt = build_prompt("AAPL", {}, headlines)
    assert "Apple beats Q3 estimates" in prompt
    assert "iPhone sales up 12%" in prompt


def test_build_prompt_contains_fundamental_data():
    info = {"trailingPE": 24.5, "dividendYield": 0.006, "earningsGrowth": 0.08}
    prompt = build_prompt("AAPL", info, [])
    assert "24.5" in prompt
    assert "0.006" in prompt
    assert "0.08" in prompt


def test_build_prompt_instructs_signal_format():
    prompt = build_prompt("AAPL", {}, [])
    # Must tell Claude to respond with exactly this structure
    assert "SIGNAL:" in prompt
    assert "REASONING:" in prompt


def test_build_prompt_valid_signals_mentioned():
    prompt = build_prompt("AAPL", {}, [])
    assert "BUY" in prompt
    assert "HOLD" in prompt
    assert "SKIP" in prompt


def test_build_prompt_handles_empty_headlines():
    # Should not raise
    prompt = build_prompt("JNJ", {}, [])
    assert "JNJ" in prompt


# --- parse_claude_response ---

def test_parse_buy_signal():
    text = "SIGNAL: BUY\nREASONING: Strong earnings growth and positive sentiment."
    result = parse_claude_response(text)
    assert result["signal"] == "BUY"
    assert "Strong earnings growth" in result["reasoning"]


def test_parse_hold_signal():
    text = "SIGNAL: HOLD\nREASONING: Fundamentals are neutral, wait for clearer trend."
    result = parse_claude_response(text)
    assert result["signal"] == "HOLD"


def test_parse_skip_signal():
    text = "SIGNAL: SKIP\nREASONING: Negative news sentiment outweighs the technicals."
    result = parse_claude_response(text)
    assert result["signal"] == "SKIP"


def test_parse_response_strips_whitespace():
    text = "  SIGNAL:  BUY  \n  REASONING:  Good value.  "
    result = parse_claude_response(text)
    assert result["signal"] == "BUY"
    assert result["reasoning"] == "Good value."


def test_parse_response_raises_on_invalid_signal():
    text = "SIGNAL: MAYBE\nREASONING: Not sure."
    with pytest.raises(ValueError, match="signal"):
        parse_claude_response(text)


def test_parse_response_raises_on_missing_signal_line():
    text = "REASONING: Looks interesting."
    with pytest.raises(ValueError, match="SIGNAL"):
        parse_claude_response(text)


def test_parse_response_raises_on_missing_reasoning_line():
    text = "SIGNAL: BUY"
    with pytest.raises(ValueError, match="REASONING"):
        parse_claude_response(text)


def test_parse_reasoning_can_be_multiline():
    text = "SIGNAL: BUY\nREASONING: Line one.\nLine two.\nLine three."
    result = parse_claude_response(text)
    assert "Line one." in result["reasoning"]
    assert "Line two." in result["reasoning"]
