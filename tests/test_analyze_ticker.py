import pytest
from unittest.mock import MagicMock, patch
from analyst.claude_analyst import analyze_ticker
from config import Config


def _make_config(delay=0.0, model="test-model", provider="claude"):
    c = Config()
    c.analyst_call_delay_s = delay
    c.analyst_model = model
    c.analyst_provider = provider
    c.analyst_api_key = "fake-key"
    c.anthropic_api_key = "fake-key"
    return c


def _make_anthropic_client(response_text: str) -> MagicMock:
    """Return a MagicMock mimicking the Anthropic client (.messages.create path)."""
    client = MagicMock()
    client.messages.create.return_value.content = [MagicMock(text=response_text)]
    return client


def test_analyze_ticker_returns_buy_signal():
    client = _make_anthropic_client("SIGNAL: BUY\nREASONING: Strong earnings growth.")
    result = analyze_ticker("AAPL", {}, [], _make_config(), client=client)
    assert result["signal"] == "BUY"
    assert "Strong earnings growth" in result["reasoning"]


def test_analyze_ticker_returns_hold_signal():
    client = _make_anthropic_client("SIGNAL: HOLD\nREASONING: Neutral outlook.")
    result = analyze_ticker("MSFT", {}, [], _make_config(), client=client)
    assert result["signal"] == "HOLD"


def test_analyze_ticker_returns_skip_signal():
    client = _make_anthropic_client("SIGNAL: SKIP\nREASONING: Poor sentiment.")
    result = analyze_ticker("T", {}, [], _make_config(), client=client)
    assert result["signal"] == "SKIP"


def test_analyze_ticker_propagates_parse_error():
    """Malformed API response raises ValueError from parse_claude_response."""
    client = _make_anthropic_client("This is not the right format at all.")
    with pytest.raises(ValueError):
        analyze_ticker("AAPL", {}, [], _make_config(), client=client)


def test_analyze_ticker_sleeps_for_configured_delay():
    client = _make_anthropic_client("SIGNAL: BUY\nREASONING: Solid fundamentals.")
    with patch("analyst.claude_analyst.time.sleep") as mock_sleep:
        analyze_ticker("AAPL", {}, [], _make_config(delay=7.0), client=client)
    mock_sleep.assert_called_once_with(7.0)


def test_analyze_ticker_no_sleep_when_delay_is_zero():
    client = _make_anthropic_client("SIGNAL: BUY\nREASONING: Solid fundamentals.")
    with patch("analyst.claude_analyst.time.sleep") as mock_sleep:
        analyze_ticker("AAPL", {}, [], _make_config(delay=0.0), client=client)
    mock_sleep.assert_not_called()


def test_analyze_ticker_creates_client_when_none_provided():
    """analyze_ticker must call create_analyst_client when client=None."""
    mock_client = _make_anthropic_client("SIGNAL: BUY\nREASONING: ok.")
    with patch("analyst.claude_analyst.create_analyst_client", return_value=mock_client) as mock_create:
        result = analyze_ticker("AAPL", {}, [], _make_config(), client=None)
    mock_create.assert_called_once()
    assert result["signal"] == "BUY"


def test_analyze_ticker_uses_configured_model():
    """Model from config is passed through to the API call."""
    client = _make_anthropic_client("SIGNAL: HOLD\nREASONING: Neutral.")
    analyze_ticker("AAPL", {}, [], _make_config(model="claude-custom-model"), client=client)
    call_kwargs = client.messages.create.call_args[1]
    assert call_kwargs["model"] == "claude-custom-model"
