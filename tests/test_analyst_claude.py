import pytest
from unittest.mock import patch, MagicMock
from analyst.claude_analyst import build_prompt, parse_claude_response, build_etf_prompt, analyze_etf_ticker


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


# --- build_etf_prompt ---

def test_build_etf_prompt_includes_ticker_and_technical_data():
    """Test 1: build_etf_prompt includes ticker, RSI, MACD, and expense ratio."""
    prompt = build_etf_prompt(
        "SPY",
        ["ETF tracking S&P 500", "Markets rally on strong jobs data"],
        rsi=65.0,
        macd_line=0.5432,
        signal_line=0.4321,
        macd_histogram=0.1111,
        expense_ratio=0.0009,
        price=450.0,
        ma50=440.0,
    )
    assert "SPY" in prompt
    assert "RSI" in prompt
    assert "MACD" in prompt
    assert "Expense Ratio" in prompt
    assert "65.0" in prompt
    assert "0.5432" in prompt
    assert "0.0009" in prompt


def test_build_etf_prompt_excludes_stock_fundamentals():
    """Test 2: build_etf_prompt does NOT contain stock-only fundamental fields."""
    prompt = build_etf_prompt(
        "QQQ",
        [],
        rsi=55.0,
        expense_ratio=0.0020,
    )
    assert "P/E" not in prompt
    assert "Dividend Yield" not in prompt
    assert "Earnings Growth" not in prompt


def test_build_etf_prompt_expense_ratio_none_shows_na():
    """Test 3: build_etf_prompt with expense_ratio=None outputs 'N/A' for expense ratio."""
    prompt = build_etf_prompt(
        "BND",
        [],
        expense_ratio=None,
    )
    assert "N/A" in prompt


def test_build_etf_prompt_macd_none_shows_na():
    """Test 4: build_etf_prompt with macd_line=None outputs 'N/A' for MACD section."""
    prompt = build_etf_prompt(
        "VTI",
        [],
        macd_line=None,
    )
    assert "N/A" in prompt


def test_build_etf_prompt_empty_headlines_shows_no_headlines_message():
    """Test 5: build_etf_prompt with empty headlines list outputs no headlines message."""
    prompt = build_etf_prompt(
        "IVV",
        [],
    )
    assert "No recent headlines available" in prompt


def test_build_etf_prompt_includes_signal_format_instruction():
    """Test 6: build_etf_prompt output ends with the standard SIGNAL:/REASONING: format."""
    prompt = build_etf_prompt(
        "VOO",
        ["Broad market ETF rally"],
        rsi=60.0,
        expense_ratio=0.0003,
    )
    assert "SIGNAL: <BUY|HOLD|SKIP>" in prompt
    assert "REASONING:" in prompt


def _make_config():
    """Create a minimal Config for testing."""
    from config import Config
    return Config(
        analyst_provider="gemini",
        analyst_api_key="test-key",
        analyst_model="gemini-2.5-flash",
        analyst_call_delay_s=0,
        analyst_fallback_provider="",
        analyst_fallback_api_key="",
        analyst_fallback_model="",
    )


def test_analyze_etf_ticker_returns_correct_shape():
    """Test 7: analyze_etf_ticker with mocked _call_api returns correct shape."""
    config = _make_config()
    tech_data = {
        "rsi": 65.0,
        "macd_line": 0.5,
        "signal_line": 0.4,
        "macd_histogram": 0.1,
        "price": 450.0,
        "ma50": 440.0,
    }
    mock_client = MagicMock()

    with patch("analyst.claude_analyst._call_api") as mock_call:
        mock_call.return_value = "SIGNAL: BUY\nREASONING: Strong trend and low expense ratio."
        result = analyze_etf_ticker(
            ticker="SPY",
            headlines=["Markets up"],
            tech_data=tech_data,
            expense_ratio=0.0009,
            config=config,
            client=mock_client,
        )

    assert result["signal"] == "BUY"
    assert "reasoning" in result
    assert result["provider_used"] == "gemini"


def test_analyze_etf_ticker_calls_build_etf_prompt_not_build_prompt():
    """Test 8: analyze_etf_ticker calls build_etf_prompt, not build_prompt.
    Verify by checking the prompt passed to _call_api does not contain 'Trailing P/E'."""
    config = _make_config()
    tech_data = {
        "rsi": 70.0,
        "macd_line": 0.3,
        "signal_line": 0.2,
        "macd_histogram": 0.1,
        "price": 300.0,
        "ma50": 295.0,
    }
    mock_client = MagicMock()
    captured_prompts = []

    def capture_call(client, model, prompt):
        captured_prompts.append(prompt)
        return "SIGNAL: HOLD\nREASONING: Neutral momentum, wait for confirmation."

    with patch("analyst.claude_analyst._call_api", side_effect=capture_call):
        analyze_etf_ticker(
            ticker="QQQ",
            headlines=[],
            tech_data=tech_data,
            expense_ratio=0.0020,
            config=config,
            client=mock_client,
        )

    assert len(captured_prompts) == 1
    assert "Trailing P/E" not in captured_prompts[0]
    assert "RSI" in captured_prompts[0]
