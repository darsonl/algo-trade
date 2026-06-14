import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from analyst.claude_analyst import build_prompt, parse_claude_response, build_etf_prompt, analyze_etf_ticker, analyze_ticker, analyze_sell_ticker
from analyst.claude_analyst import _parse_retry_delay, _should_retry, _call_api


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
    # dividendYield arrives in percent (yfinance >= 0.2.55) and is rendered as a % string
    info = {"trailingPE": 24.5, "dividendYield": 0.6, "earningsGrowth": 0.08}
    prompt = build_prompt("AAPL", info, [])
    assert "24.5" in prompt
    assert "0.60%" in prompt
    assert "0.08" in prompt


def test_build_prompt_missing_dividend_yield_shows_na():
    prompt = build_prompt("AAPL", {"trailingPE": 24.5}, [])
    assert "Dividend Yield: N/A" in prompt


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
    macro = {"spy_trend_1m": "Bullish (+3.2%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.4 (Low volatility)"}
    prompt = build_prompt("AAPL", info, ["Apple beats estimates"], macro_context=macro)
    assert "Market Context:" in prompt
    assert "Sector: Technology" in prompt
    assert "SPY trend (1m): Bullish (+3.2%)" in prompt
    assert "SPY trend (1y): Bearish (-8.5%)" in prompt
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
    assert "SPY trend (1m):" not in prompt
    assert "SPY trend (1y):" not in prompt
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
    macro = {"spy_trend_1m": "Bullish (+3.2%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.4 (Low volatility)"}
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
    macro = {"spy_trend_1m": "Bearish (-1.4%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "25.0 (Elevated volatility)"}
    prompt = build_etf_prompt("SPY", ["ETF news"], rsi=65.0, macro_context=macro)
    assert "Market Context:" in prompt
    assert "SPY trend (1m): Bearish (-1.4%)" in prompt
    assert "SPY trend (1y): Bearish (-8.5%)" in prompt
    assert "VIX: 25.0 (Elevated volatility)" in prompt


def test_build_etf_prompt_with_macro_context_excludes_sector_and_52w():
    """build_etf_prompt Market Context block does NOT include Sector or 52-week range (per D-10)."""
    macro = {"spy_trend_1m": "Bullish (+3.2%)", "spy_trend_1y": "Bearish (-8.5%)", "vix_level": "18.4 (Low volatility)"}
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


# ---------------------------------------------------------------------------
# fundamental_trend enrichment tests (SIG-07, SIG-08)
# ---------------------------------------------------------------------------

def test_build_prompt_with_fundamental_trend_includes_pe_direction_and_eps():
    """Test A: fundamental_trend with full EPS data renders P/E Direction and all EPS entries."""
    fundamental_trend = {
        "pe_direction": "contracting",
        "eps_trend": [
            {"quarter": "Q1-2025", "eps": 0.45},
            {"quarter": "Q2-2025", "eps": 0.52},
            {"quarter": "Q3-2025", "eps": 0.48},
            {"quarter": "Q4-2025", "eps": 0.61},
        ],
    }
    prompt = build_prompt("AAPL", {}, [], fundamental_trend=fundamental_trend)
    assert "P/E Direction: contracting" in prompt
    assert "EPS trend (last 4Q):" in prompt
    assert "Q1-2025: $0.45" in prompt
    assert "Q2-2025: $0.52" in prompt
    assert "Q3-2025: $0.48" in prompt
    assert "Q4-2025: $0.61" in prompt


def test_build_prompt_fundamental_trend_pe_direction_na():
    """Test B: fundamental_trend with pe_direction N/A and no eps_trend."""
    fundamental_trend = {"pe_direction": "N/A", "eps_trend": None}
    prompt = build_prompt("AAPL", {}, [], fundamental_trend=fundamental_trend)
    assert "P/E Direction: N/A" in prompt
    assert "EPS trend" not in prompt


def test_build_prompt_fundamental_trend_eps_omitted_when_none():
    """Test C: eps_trend=None means EPS line is omitted, P/E Direction still present."""
    fundamental_trend = {"pe_direction": "stable", "eps_trend": None}
    prompt = build_prompt("AAPL", {}, [], fundamental_trend=fundamental_trend)
    assert "P/E Direction: stable" in prompt
    assert "EPS trend" not in prompt


def test_build_prompt_fundamental_trend_eps_omitted_when_empty_list():
    """Test D: eps_trend=[] (empty list) means EPS line is omitted per D-05."""
    fundamental_trend = {"pe_direction": "expanding", "eps_trend": []}
    prompt = build_prompt("AAPL", {}, [], fundamental_trend=fundamental_trend)
    assert "P/E Direction: expanding" in prompt
    assert "EPS trend" not in prompt


def test_build_prompt_eps_chronological_order_preserved():
    """Test E: Q1 entry appears before Q4 entry in the rendered prompt."""
    fundamental_trend = {
        "pe_direction": "contracting",
        "eps_trend": [
            {"quarter": "Q1-2025", "eps": 0.40},
            {"quarter": "Q2-2025", "eps": 0.45},
            {"quarter": "Q3-2025", "eps": 0.50},
            {"quarter": "Q4-2025", "eps": 0.55},
        ],
    }
    prompt = build_prompt("AAPL", {}, [], fundamental_trend=fundamental_trend)
    assert prompt.index("Q1-2025") < prompt.index("Q4-2025")


def test_build_prompt_backward_compat_no_fundamental_trend():
    """Test F: build_prompt called without fundamental_trend kwarg — no crash, no P/E Direction or EPS trend."""
    prompt = build_prompt("AAPL", {}, ["headline"])
    assert "P/E Direction" not in prompt
    assert "EPS trend" not in prompt
    assert "AAPL" in prompt
    assert "SIGNAL:" in prompt


def test_analyze_ticker_forwards_fundamental_trend_to_build_prompt():
    """Test G: analyze_ticker receives fundamental_trend kwarg and forwards it to build_prompt."""
    from config import Config

    config = Config(
        analyst_provider="gemini",
        analyst_api_key="test-key",
        analyst_model="gemini-2.5-flash",
        analyst_call_delay_s=0,
        analyst_fallback_provider="",
        analyst_fallback_api_key="",
        analyst_fallback_model="",
    )
    fundamental_trend = {"pe_direction": "contracting", "eps_trend": None}
    captured_prompts = []

    def capture_call(client, model, prompt):
        captured_prompts.append(prompt)
        return "SIGNAL: BUY\nREASONING: Strong trend confirmed.\nCONFIDENCE: high"

    mock_client = MagicMock()
    with patch("analyst.claude_analyst._call_api", side_effect=capture_call):
        result = analyze_ticker(
            ticker="AAPL",
            info={},
            headlines=[],
            config=config,
            client=mock_client,
            fundamental_trend=fundamental_trend,
        )

    assert len(captured_prompts) == 1
    assert "P/E Direction: contracting" in captured_prompts[0]


# ---------------------------------------------------------------------------
# earnings_date prompt injection tests (SIG-06)
# ---------------------------------------------------------------------------

def test_build_prompt_with_earnings_date_includes_market_context_line():
    """Test A: build_prompt with earnings_date='Dec 15, 2025' contains '- Next Earnings: Dec 15, 2025'."""
    prompt = build_prompt("AAPL", {}, [], earnings_date="Dec 15, 2025")
    assert "- Next Earnings: Dec 15, 2025" in prompt


def test_build_prompt_with_earnings_date_none_omits_line():
    """Test B: build_prompt without earnings_date kwarg does NOT contain 'Next Earnings'. No crash."""
    prompt = build_prompt("AAPL", {}, [])
    assert "Next Earnings" not in prompt


def test_build_prompt_with_earnings_date_proximity_note():
    """Test C: build_prompt with proximity note in earnings_date renders full string."""
    prompt = build_prompt("AAPL", {}, [], earnings_date="Dec 18, 2025 (in 6 days — proximity risk)")
    assert "- Next Earnings: Dec 18, 2025 (in 6 days — proximity risk)" in prompt


def test_build_prompt_earnings_date_in_market_context_block():
    """Test D: 'Next Earnings' appears AFTER 'Market Context:' in the prompt."""
    prompt = build_prompt("AAPL", {}, [], earnings_date="Dec 15, 2025")
    assert prompt.index("Market Context:") < prompt.index("Next Earnings")


def test_analyze_ticker_forwards_earnings_date_to_build_prompt():
    """Test E: analyze_ticker with earnings_date kwarg forwards it to build_prompt (captured via _call_api spy)."""
    from config import Config

    config = Config(
        analyst_provider="gemini",
        analyst_api_key="test-key",
        analyst_model="gemini-2.5-flash",
        analyst_call_delay_s=0,
        analyst_fallback_provider="",
        analyst_fallback_api_key="",
        analyst_fallback_model="",
    )
    captured_prompts = []

    def capture_call(client, model, prompt):
        captured_prompts.append(prompt)
        return "SIGNAL: BUY\nREASONING: Earnings proximity noted.\nCONFIDENCE: medium"

    mock_client = MagicMock()
    with patch("analyst.claude_analyst._call_api", side_effect=capture_call):
        analyze_ticker(
            ticker="AAPL",
            info={},
            headlines=[],
            config=config,
            client=mock_client,
            earnings_date="Dec 15, 2025",
        )

    assert len(captured_prompts) == 1
    assert "- Next Earnings: Dec 15, 2025" in captured_prompts[0]


# --- DeepSeek provider tests ---

def test_create_fallback2_client_deepseek_uses_openai_sdk_with_correct_base_url():
    """create_fallback2_client with provider='deepseek' returns an openai.OpenAI client
    pointed at https://api.deepseek.com."""
    from config import Config
    from analyst.claude_analyst import create_fallback2_client
    import openai

    config = Config(
        analyst_provider="gemini",
        analyst_api_key="test-key",
        analyst_fallback2_provider="deepseek",
        analyst_fallback2_api_key="sk-test-deepseek",
        analyst_fallback2_model="",
    )
    client = create_fallback2_client(config)
    assert isinstance(client, openai.OpenAI)
    assert "deepseek.com" in str(client.base_url)


def test_create_fallback2_client_returns_none_when_unconfigured():
    """create_fallback2_client returns None when provider or api_key are blank."""
    from config import Config
    from analyst.claude_analyst import create_fallback2_client

    config = Config(analyst_provider="gemini", analyst_api_key="key",
                    analyst_fallback2_provider="", analyst_fallback2_api_key="")
    assert create_fallback2_client(config) is None


def test_analyze_etf_ticker_uses_fallback2_when_both_primary_and_fallback_fail():
    """When primary and fallback both raise, analyze_etf_ticker retries with fallback2_client
    and sets provider_used to the second fallback provider name."""
    from analyst.claude_analyst import analyze_etf_ticker
    from config import Config

    config = Config(
        analyst_provider="gemini",
        analyst_api_key="test-key",
        analyst_model="gemini-2.5-flash",
        analyst_call_delay_s=0,
        analyst_fallback_provider="openai",
        analyst_fallback_api_key="openai-key",
        analyst_fallback_model="gpt-4o-mini",
        analyst_fallback2_provider="deepseek",
        analyst_fallback2_api_key="sk-test",
        analyst_fallback2_model="deepseek-chat",
    )

    tech_data = {
        "rsi": 55.0, "macd_line": 0.1, "signal_line": 0.05,
        "macd_histogram": 0.05, "price": 500.0, "ma50": 490.0,
    }
    primary_client = MagicMock()
    fallback_client = MagicMock()
    fallback2_client = MagicMock()

    call_count = {"n": 0}

    def api_side_effect(client, model, prompt):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise RuntimeError("provider unavailable")
        return "SIGNAL: BUY\nREASONING: DeepSeek confirmed bullish momentum."

    with patch("analyst.claude_analyst._call_api", side_effect=api_side_effect):
        result = analyze_etf_ticker(
            ticker="SPY",
            headlines=["Markets rally"],
            tech_data=tech_data,
            expense_ratio=0.0009,
            config=config,
            client=primary_client,
            fallback_client=fallback_client,
            fallback2_client=fallback2_client,
        )

    assert result["signal"] == "BUY"
    assert result["provider_used"] == "deepseek"
    assert call_count["n"] == 3, "Expected three _call_api calls (primary + fallback + fallback2)"


def test_analyze_etf_ticker_propagates_when_fallback_fails_and_no_fallback2():
    """When fallback fails and fallback2_client is None, the exception propagates."""
    from analyst.claude_analyst import analyze_etf_ticker
    from config import Config
    import pytest

    config = Config(
        analyst_provider="gemini",
        analyst_api_key="test-key",
        analyst_model="gemini-2.5-flash",
        analyst_call_delay_s=0,
        analyst_fallback_provider="openai",
        analyst_fallback_api_key="openai-key",
        analyst_fallback_model="gpt-4o-mini",
    )

    tech_data = {
        "rsi": 55.0, "macd_line": 0.1, "signal_line": 0.05,
        "macd_histogram": 0.05, "price": 500.0, "ma50": 490.0,
    }

    def always_fail(client, model, prompt):
        raise RuntimeError("all providers down")

    with patch("analyst.claude_analyst._call_api", side_effect=always_fail):
        with pytest.raises(RuntimeError, match="all providers down"):
            analyze_etf_ticker(
                ticker="SPY",
                headlines=[],
                tech_data=tech_data,
                expense_ratio=0.0009,
                config=config,
                client=MagicMock(),
                fallback_client=MagicMock(),
                fallback2_client=None,

            )


# --- _parse_retry_delay and _should_retry ---

def _make_exc(body):
    """Build a minimal exception with a .response whose .json() returns body."""
    exc = Exception("fake")
    exc.response = MagicMock()
    exc.response.json.return_value = body
    return exc


_GEMINI_429_LIST = [{"error": {"details": [
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "33s"},
]}}]

_ANTHROPIC_429_DICT = {"error": {"details": [
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "20s"},
]}}

_GEMINI_DAY_QUOTA = [{"error": {"details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"},
        {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "quotaValue": "5"},
    ]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "18s"},
]}}]

_GEMINI_MINUTE_ONLY = [{"error": {"details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "quotaValue": "5"},
    ]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "33s"},
]}}]


def test_parse_retry_delay_gemini_list_format():
    assert _parse_retry_delay(_make_exc(_GEMINI_429_LIST)) == 33.0


def test_parse_retry_delay_anthropic_dict_format():
    assert _parse_retry_delay(_make_exc(_ANTHROPIC_429_DICT)) == 20.0


def test_should_retry_per_day_quota_returns_false():
    assert _should_retry(_make_exc(_GEMINI_DAY_QUOTA)) is False


def test_should_retry_per_minute_quota_returns_true():
    assert _should_retry(_make_exc(_GEMINI_MINUTE_ONLY)) is True


# --- parse-error fallback tests ---

def _make_fallback_config():
    from config import Config
    return Config(
        analyst_provider="gemini",
        analyst_api_key="test-key",
        analyst_model="gemini-2.5-flash",
        analyst_call_delay_s=0,
        analyst_fallback_provider="deepseek",
        analyst_fallback_api_key="sk-test",
        analyst_fallback_model="deepseek-chat",
        analyst_fallback2_provider="openai",
        analyst_fallback2_api_key="openai-key",
        analyst_fallback2_model="gpt-4o-mini",
    )


def test_analyze_ticker_uses_fallback_on_primary_parse_error():
    """Primary returns a garbled response (template echo); fallback returns a valid signal."""
    config = _make_fallback_config()
    call_count = {"n": 0}

    def api_side_effect(client, model, prompt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "SIGNAL: <BUY|HOLD|SKIP>\nREASONING: template echo"
        return "SIGNAL: BUY\nREASONING: Fallback confirmed strong trend.\nCONFIDENCE: high"

    with patch("analyst.claude_analyst._call_api", side_effect=api_side_effect):
        result = analyze_ticker(
            ticker="BLK", info={}, headlines=[], config=config,
            client=MagicMock(), fallback_client=MagicMock(),
        )

    assert result["signal"] == "BUY"
    assert result["provider_used"] == "deepseek"
    assert call_count["n"] == 2


def test_analyze_ticker_uses_fallback2_on_fallback_parse_error():
    """Primary and fallback both return garbled responses; fallback2 returns a valid signal."""
    config = _make_fallback_config()
    call_count = {"n": 0}

    def api_side_effect(client, model, prompt):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return "SIGNAL: <BUY|HOLD|SKIP>\nREASONING: template echo"
        return "SIGNAL: SKIP\nREASONING: Fallback2 confirmed.\nCONFIDENCE: low"

    with patch("analyst.claude_analyst._call_api", side_effect=api_side_effect):
        result = analyze_ticker(
            ticker="BLK", info={}, headlines=[], config=config,
            client=MagicMock(), fallback_client=MagicMock(), fallback2_client=MagicMock(),
        )

    assert result["signal"] == "SKIP"
    assert result["provider_used"] == "openai"
    assert call_count["n"] == 3


def test_analyze_ticker_parse_error_propagates_when_no_fallback():
    """Primary parse error propagates if fallback_client is None."""
    config = _make_fallback_config()

    with patch("analyst.claude_analyst._call_api", return_value="SIGNAL: <BUY|HOLD|SKIP>\nREASONING: bad"):
        with pytest.raises(ValueError, match="signal"):
            analyze_ticker(
                ticker="BLK", info={}, headlines=[], config=config,
                client=MagicMock(), fallback_client=None,
            )


def test_analyze_etf_ticker_uses_fallback_on_primary_parse_error():
    """ETF: primary returns garbled response; fallback returns valid signal."""
    from analyst.claude_analyst import analyze_etf_ticker
    config = _make_fallback_config()
    tech_data = {"rsi": 60.0, "macd_line": 0.1, "signal_line": 0.05,
                 "macd_histogram": 0.05, "price": 450.0, "ma50": 440.0}
    call_count = {"n": 0}

    def api_side_effect(client, model, prompt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "SIGNAL: <BUY|HOLD|SKIP>\nREASONING: template echo"
        return "SIGNAL: HOLD\nREASONING: Fallback confirmed neutral trend.\nCONFIDENCE: medium"

    with patch("analyst.claude_analyst._call_api", side_effect=api_side_effect):
        result = analyze_etf_ticker(
            ticker="SPY", headlines=[], tech_data=tech_data,
            expense_ratio=0.0009, config=config,
            client=MagicMock(), fallback_client=MagicMock(),
        )

    assert result["signal"] == "HOLD"
    assert result["provider_used"] == "deepseek"
    assert call_count["n"] == 2


# --- analyze_sell_ticker fallback matrix (D-04 priority gap) ---

def test_analyze_sell_ticker_uses_fallback_on_primary_failure():
    """Primary API raises; fallback returns a valid SELL signal."""
    config = _make_fallback_config()
    call_count = {"n": 0}

    def api_side_effect(client, model, prompt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("quota exhausted")
        return "SIGNAL: SELL\nREASONING: Fallback confirmed overbought.\nCONFIDENCE: high"

    with patch("analyst.claude_analyst._call_api", side_effect=api_side_effect):
        result = analyze_sell_ticker(
            ticker="AAPL", entry_price=150.0, current_price=170.0, pnl_pct=0.13,
            hold_days=10, rsi=75.0, headlines=["Headline"], config=config,
            client=MagicMock(), fallback_client=MagicMock(),
        )

    assert result["signal"] == "SELL"
    assert result["provider_used"] == "deepseek"
    assert call_count["n"] == 2


def test_analyze_sell_ticker_uses_fallback_on_primary_parse_error():
    """Primary returns template-echo (parse error); fallback returns a valid HOLD signal."""
    config = _make_fallback_config()
    call_count = {"n": 0}

    def api_side_effect(client, model, prompt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "SIGNAL: <SELL|HOLD>\nREASONING: template echo"
        return "SIGNAL: HOLD\nREASONING: Fallback says hold.\nCONFIDENCE: medium"

    with patch("analyst.claude_analyst._call_api", side_effect=api_side_effect):
        result = analyze_sell_ticker(
            ticker="AAPL", entry_price=150.0, current_price=170.0, pnl_pct=0.13,
            hold_days=10, rsi=75.0, headlines=["Headline"], config=config,
            client=MagicMock(), fallback_client=MagicMock(),
        )

    assert result["signal"] == "HOLD"
    assert result["provider_used"] == "deepseek"
    assert call_count["n"] == 2


def test_analyze_sell_ticker_uses_fallback2_when_both_primary_and_fallback_fail():
    """Primary and fallback both raise API errors; fallback2 returns a valid SELL signal."""
    config = _make_fallback_config()
    call_count = {"n": 0}

    def api_side_effect(client, model, prompt):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise RuntimeError("provider down")
        return "SIGNAL: SELL\nREASONING: fb2 confirmed overbought.\nCONFIDENCE: high"

    with patch("analyst.claude_analyst._call_api", side_effect=api_side_effect):
        result = analyze_sell_ticker(
            ticker="AAPL", entry_price=150.0, current_price=170.0, pnl_pct=0.13,
            hold_days=10, rsi=75.0, headlines=["Headline"], config=config,
            client=MagicMock(), fallback_client=MagicMock(), fallback2_client=MagicMock(),
        )

    assert result["signal"] == "SELL"
    assert result["provider_used"] == "openai"
    assert call_count["n"] == 3


def test_analyze_sell_ticker_uses_fallback2_on_fallback_parse_error():
    """Primary and fallback both return template-echo (parse errors); fallback2 succeeds."""
    config = _make_fallback_config()
    call_count = {"n": 0}

    def api_side_effect(client, model, prompt):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return "SIGNAL: <SELL|HOLD>\nREASONING: template echo"
        return "SIGNAL: SELL\nREASONING: fb2 confirmed.\nCONFIDENCE: low"

    with patch("analyst.claude_analyst._call_api", side_effect=api_side_effect):
        result = analyze_sell_ticker(
            ticker="AAPL", entry_price=150.0, current_price=170.0, pnl_pct=0.13,
            hold_days=10, rsi=75.0, headlines=["Headline"], config=config,
            client=MagicMock(), fallback_client=MagicMock(), fallback2_client=MagicMock(),
        )

    assert result["signal"] == "SELL"
    assert result["provider_used"] == "openai"
    assert call_count["n"] == 3


def test_analyze_sell_ticker_propagates_when_no_fallback():
    """Primary API failure propagates when fallback_client is None."""
    config = _make_fallback_config()

    with patch("analyst.claude_analyst._call_api", side_effect=RuntimeError("all down")):
        with pytest.raises(RuntimeError, match="all down"):
            analyze_sell_ticker(
                ticker="AAPL", entry_price=150.0, current_price=170.0, pnl_pct=0.13,
                hold_days=10, rsi=75.0, headlines=["Headline"], config=config,
                client=MagicMock(), fallback_client=None,
            )


# --- analyze_ticker and analyze_etf_ticker missing matrix cells ---

def test_analyze_ticker_uses_fallback_on_primary_failure():
    """Primary API raises; fallback returns a valid BUY signal."""
    config = _make_fallback_config()
    call_count = {"n": 0}

    def api_side_effect(client, model, prompt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("quota exhausted")
        return "SIGNAL: BUY\nREASONING: Fallback confirmed.\nCONFIDENCE: high"

    with patch("analyst.claude_analyst._call_api", side_effect=api_side_effect):
        result = analyze_ticker(
            ticker="BLK", info={}, headlines=[], config=config,
            client=MagicMock(), fallback_client=MagicMock(),
        )

    assert result["signal"] == "BUY"
    assert result["provider_used"] == "deepseek"
    assert call_count["n"] == 2


def test_analyze_ticker_uses_fallback2_on_fallback_api_failure():
    """Primary and fallback both raise API errors; fallback2 returns a valid SKIP signal."""
    config = _make_fallback_config()
    call_count = {"n": 0}

    def api_side_effect(client, model, prompt):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise RuntimeError("down")
        return "SIGNAL: SKIP\nREASONING: fb2.\nCONFIDENCE: low"

    with patch("analyst.claude_analyst._call_api", side_effect=api_side_effect):
        result = analyze_ticker(
            ticker="BLK", info={}, headlines=[], config=config,
            client=MagicMock(), fallback_client=MagicMock(), fallback2_client=MagicMock(),
        )

    assert result["signal"] == "SKIP"
    assert result["provider_used"] == "openai"
    assert call_count["n"] == 3


def test_analyze_etf_ticker_uses_fallback2_on_fallback_parse_error():
    """ETF: primary and fallback both return template-echo (parse errors); fallback2 succeeds."""
    config = _make_fallback_config()
    tech_data = {"rsi": 60.0, "macd_line": 0.1, "signal_line": 0.05,
                 "macd_histogram": 0.05, "price": 450.0, "ma50": 440.0}
    call_count = {"n": 0}

    def api_side_effect(client, model, prompt):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return "SIGNAL: <BUY|HOLD|SKIP>\nREASONING: template echo"
        return "SIGNAL: BUY\nREASONING: fb2 confirmed.\nCONFIDENCE: medium"

    with patch("analyst.claude_analyst._call_api", side_effect=api_side_effect):
        result = analyze_etf_ticker(
            ticker="SPY", headlines=[], tech_data=tech_data,
            expense_ratio=0.0009, config=config,
            client=MagicMock(), fallback_client=MagicMock(), fallback2_client=MagicMock(),
        )

    assert result["signal"] == "BUY"
    assert result["provider_used"] == "openai"
    assert call_count["n"] == 3


# --- _call_api response extraction (live SDK-call path) ---
# The fallback/orchestration tests above patch _call_api wholesale, so the actual
# SDK call and response unwrapping never execute there. These exercise that path
# directly with PLAIN stubs — not MagicMock, which answers hasattr(client, "messages")
# True and would force every client down the anthropic branch — pinning the openai
# (1.x/2.x identical) and anthropic response shapes.

class _StubOpenAIClient:
    """openai-shaped client: .chat.completions.create(**kw), no `messages` attr.

    _call_api takes the openai branch when the client lacks `messages`, then reads
    response.choices[0].message.content.
    """
    def __init__(self, content: str):
        self._content = content
        self.captured: dict = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.captured = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


class _StubAnthropicClient:
    """anthropic-shaped client: .messages.create(**kw) → content[0].text."""
    def __init__(self, text: str):
        self._text = text
        self.captured: dict = {}
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.captured = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=self._text)])


def test_call_api_openai_branch_extracts_message_content():
    """_call_api routes a non-anthropic client through chat.completions.create and
    returns choices[0].message.content (the openai 1.x/2.x response shape)."""
    client = _StubOpenAIClient("SIGNAL: BUY\nREASONING: stubbed openai path.")

    out = _call_api(client, "gpt-4o-mini", "the prompt")

    assert out == "SIGNAL: BUY\nREASONING: stubbed openai path."
    # max_tokens (NOT max_completion_tokens) is what the OpenAI-compatible proxy
    # endpoints (Gemini/DeepSeek/GitHub) expect — guard that it stays this way.
    assert client.captured["model"] == "gpt-4o-mini"
    assert client.captured["max_tokens"] == 256
    assert client.captured["messages"] == [{"role": "user", "content": "the prompt"}]


def test_call_api_anthropic_branch_extracts_content_text():
    """_call_api routes a client exposing .messages through messages.create and
    returns content[0].text (the anthropic response shape)."""
    client = _StubAnthropicClient("SIGNAL: HOLD\nREASONING: stubbed anthropic path.")

    out = _call_api(client, "claude-opus-4-8", "the prompt")

    assert out == "SIGNAL: HOLD\nREASONING: stubbed anthropic path."
    assert client.captured["model"] == "claude-opus-4-8"
    assert client.captured["max_tokens"] == 256
    assert client.captured["messages"] == [{"role": "user", "content": "the prompt"}]
