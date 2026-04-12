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


# --- G-4: analyze_etf_ticker fallback provider ---

def test_analyze_etf_ticker_uses_fallback_on_primary_failure():
    """When the primary _call_api raises, analyze_etf_ticker retries with fallback_client
    and sets provider_used to the fallback provider name."""
    config = _make_config()
    config.analyst_fallback_provider = "openai"
    config.analyst_fallback_model = "gpt-4o-mini"

    tech_data = {
        "rsi": 60.0,
        "macd_line": 0.2,
        "signal_line": 0.1,
        "macd_histogram": 0.1,
        "price": 480.0,
        "ma50": 470.0,
    }
    primary_client = MagicMock()
    fallback_client = MagicMock()

    call_count = {"n": 0}

    def api_side_effect(client, model, prompt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("quota exhausted")
        return "SIGNAL: BUY\nREASONING: Fallback confirmed strong trend."

    with patch("analyst.claude_analyst._call_api", side_effect=api_side_effect):
        result = analyze_etf_ticker(
            ticker="SPY",
            headlines=["Markets rally"],
            tech_data=tech_data,
            expense_ratio=0.0009,
            config=config,
            client=primary_client,
            fallback_client=fallback_client,
        )

    assert result["signal"] == "BUY"
    assert result["provider_used"] == "openai"
    assert call_count["n"] == 2, "Expected exactly two _call_api calls (primary fail + fallback)"


# --- build_prompt macro_context enrichment ---

def test_build_prompt_with_macro_context_includes_market_context_block():
    """build_prompt with macro_context dict includes Market Context block with all 4 lines."""
    info = {
        "trailingPE": 28.5,
        "dividendYield": 0.005,
        "earningsGrowth": 0.12,
        "sector": "Technology",
        "fiftyTwoWeekHigh": 200.0,
        "fiftyTwoWeekLow": 100.0,
        "regularMarketPrice": 180.0,
    }
    macro = {"spy_trend": "Bullish (+3.2%)", "vix_level": "18.4 (Low volatility)"}
    prompt = build_prompt("AAPL", info, ["Apple beats estimates"], macro_context=macro)
    assert "Market Context:" in prompt
    assert "Sector: Technology" in prompt
    assert "SPY trend: Bullish (+3.2%)" in prompt
    assert "VIX: 18.4 (Low volatility)" in prompt
    assert "52-week range:" in prompt


def test_build_prompt_without_macro_context_omits_spy_vix():
    """build_prompt with macro_context=None does NOT contain SPY trend or VIX lines,
    but DOES contain Sector and 52-week range (per D-11)."""
    info = {
        "sector": "Technology",
        "fiftyTwoWeekHigh": 200.0,
        "fiftyTwoWeekLow": 100.0,
        "regularMarketPrice": 180.0,
    }
    prompt = build_prompt("AAPL", info, [], macro_context=None)
    assert "SPY trend:" not in prompt
    assert "VIX:" not in prompt
    assert "Sector: Technology" in prompt
    assert "52-week range:" in prompt


def test_build_prompt_missing_sector_shows_na():
    """build_prompt with info missing 'sector' key shows 'Sector: N/A'."""
    info = {}
    prompt = build_prompt("AAPL", info, [], macro_context=None)
    assert "Sector: N/A" in prompt


def test_build_prompt_missing_52w_shows_na():
    """build_prompt with info missing fiftyTwoWeekHigh shows '52-week range: N/A'."""
    info = {}
    prompt = build_prompt("AAPL", info, [], macro_context=None)
    assert "52-week range: N/A" in prompt


def test_build_prompt_market_context_position():
    """Market Context block appears AFTER Fundamentals: and BEFORE Recent news headlines:."""
    info = {
        "trailingPE": 24.5,
        "sector": "Technology",
        "regularMarketPrice": 150.0,
        "fiftyTwoWeekHigh": 200.0,
        "fiftyTwoWeekLow": 100.0,
    }
    macro = {"spy_trend": "Bullish (+3.2%)", "vix_level": "18.4 (Low volatility)"}
    prompt = build_prompt("AAPL", info, ["Headline"], macro_context=macro)
    fundamentals_pos = prompt.index("Fundamentals:")
    market_pos = prompt.index("Market Context:")
    news_pos = prompt.index("Recent news headlines:")
    assert fundamentals_pos < market_pos < news_pos


def test_build_prompt_backward_compat_no_macro_context_arg():
    """build_prompt called without macro_context argument still works (backward compat)."""
    prompt = build_prompt("AAPL", {}, ["headline"])
    assert "AAPL" in prompt
    assert "SIGNAL:" in prompt


# --- build_etf_prompt macro_context enrichment ---

def test_build_etf_prompt_with_macro_context_includes_spy_and_vix():
    """build_etf_prompt with macro_context includes SPY trend and VIX in Market Context block."""
    macro = {"spy_trend": "Bearish (-1.4%)", "vix_level": "25.0 (Elevated volatility)"}
    prompt = build_etf_prompt("SPY", ["ETF news"], rsi=65.0, macro_context=macro)
    assert "Market Context:" in prompt
    assert "SPY trend: Bearish (-1.4%)" in prompt
    assert "VIX: 25.0 (Elevated volatility)" in prompt


def test_build_etf_prompt_with_macro_context_excludes_sector_and_52w():
    """build_etf_prompt Market Context block does NOT include Sector or 52-week range (per D-10)."""
    macro = {"spy_trend": "Bullish (+3.2%)", "vix_level": "18.4 (Low volatility)"}
    prompt = build_etf_prompt("QQQ", [], macro_context=macro)
    assert "Sector:" not in prompt
    assert "52-week range:" not in prompt


def test_build_etf_prompt_without_macro_context_omits_market_context():
    """build_etf_prompt with macro_context=None does NOT include Market Context block."""
    prompt = build_etf_prompt("QQQ", [], macro_context=None)
    assert "Market Context:" not in prompt


def test_build_etf_prompt_backward_compat_no_macro_context_arg():
    """build_etf_prompt called without macro_context argument still works (backward compat)."""
    prompt = build_etf_prompt("SPY", [], rsi=60.0)
    assert "SPY" in prompt
    assert "SIGNAL:" in prompt


# --- Confidence scoring tests (Phase 11) ---

def test_parse_confidence_high():
    text = "SIGNAL: BUY\nREASONING: Strong fundamentals.\nCONFIDENCE: high"
    result = parse_claude_response(text)
    assert result["confidence"] == "high"


def test_parse_confidence_medium():
    text = "SIGNAL: BUY\nREASONING: Mixed signals.\nCONFIDENCE: medium"
    result = parse_claude_response(text)
    assert result["confidence"] == "medium"


def test_parse_confidence_low():
    text = "SIGNAL: HOLD\nREASONING: Uncertain outlook.\nCONFIDENCE: low"
    result = parse_claude_response(text)
    assert result["confidence"] == "low"


def test_parse_confidence_missing():
    text = "SIGNAL: BUY\nREASONING: Good stock."
    result = parse_claude_response(text)
    assert result["confidence"] is None


def test_parse_confidence_invalid_value():
    text = "SIGNAL: BUY\nREASONING: Good stock.\nCONFIDENCE: maybe"
    result = parse_claude_response(text)
    assert result["confidence"] is None


def test_parse_confidence_case_insensitive():
    text = "SIGNAL: BUY\nREASONING: Good stock.\nCONFIDENCE:  High "
    result = parse_claude_response(text)
    assert result["confidence"] == "high"


def test_parse_confidence_does_not_leak_into_reasoning():
    text = "SIGNAL: BUY\nREASONING: Good stock.\nCONFIDENCE: high"
    result = parse_claude_response(text)
    assert result["reasoning"] == "Good stock."
    assert "high" not in result["reasoning"]


def test_build_prompt_includes_confidence_format():
    prompt = build_prompt("AAPL", {}, [])
    assert "CONFIDENCE: <high|medium|low>" in prompt


def test_build_etf_prompt_includes_confidence_format():
    prompt = build_etf_prompt("SPY", [], rsi=60.0)
    assert "CONFIDENCE: <high|medium|low>" in prompt
