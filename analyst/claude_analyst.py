from __future__ import annotations
import time
import anthropic
import openai
from config import Config
from tenacity import retry, stop_after_attempt


def _wait_for_retry(retry_state) -> float:
    """Wait based on retryDelay in 429 response body, fallback to exponential."""
    exc = retry_state.outcome.exception()
    if exc and hasattr(exc, "response"):
        try:
            delay_str = exc.response.json()["error"]["details"][-1]["retryDelay"]
            return float(delay_str.rstrip("s")) + 1
        except Exception:
            pass
    return min(2 ** retry_state.attempt_number, 60)


_retry = retry(
    wait=_wait_for_retry,
    stop=stop_after_attempt(3),
    reraise=True,
)

_VALID_SIGNALS = {"BUY", "HOLD", "SKIP"}

_OPENAI_BASE_URLS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "github": "https://models.inference.ai.azure.com",
}

_DEFAULT_MODELS: dict[str, str] = {
    "claude": "claude-opus-4-6",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "github": "gpt-4o-mini",
}


def create_analyst_client(config: Config):
    """Return an SDK client for the configured analyst provider."""
    provider = config.analyst_provider
    api_key = config.analyst_api_key

    if provider == "claude":
        return anthropic.Anthropic(api_key=api_key or config.anthropic_api_key)

    base_url = _OPENAI_BASE_URLS.get(provider)  # None → default OpenAI endpoint
    return openai.OpenAI(api_key=api_key, base_url=base_url)


def build_prompt(ticker: str, info: dict, headlines: list[str]) -> str:
    """Build the analysis prompt for a single ticker."""
    pe = info.get("trailingPE", "N/A")
    div_yield = info.get("dividendYield", "N/A")
    earnings_growth = info.get("earningsGrowth", "N/A")

    headlines_block = (
        "\n".join(f"- {h}" for h in headlines) if headlines else "- No recent headlines available."
    )

    return f"""You are a stock analyst. Analyze the following stock and return exactly two lines.

Ticker: {ticker}

Fundamentals:
- Trailing P/E: {pe}
- Dividend Yield: {div_yield}
- Earnings Growth: {earnings_growth}

Recent news headlines:
{headlines_block}

Respond with exactly this format (no extra text):
SIGNAL: <BUY|HOLD|SKIP>
REASONING: <2-3 sentences explaining your decision>"""


def parse_claude_response(text: str) -> dict:
    """
    Parse the analyst response into {signal, reasoning}.
    Raises ValueError if the format is invalid or signal is not BUY/HOLD/SKIP.
    """
    lines = text.strip().splitlines()

    signal_line = next((l for l in lines if l.strip().startswith("SIGNAL:")), None)
    if signal_line is None:
        raise ValueError("Response missing SIGNAL: line")

    reasoning_start = next(
        (i for i, l in enumerate(lines) if l.strip().startswith("REASONING:")), None
    )
    if reasoning_start is None:
        raise ValueError("Response missing REASONING: line")

    signal = signal_line.split(":", 1)[1].strip()
    if signal not in _VALID_SIGNALS:
        raise ValueError(f"Invalid signal '{signal}': must be one of {_VALID_SIGNALS}")

    first_reasoning_line = lines[reasoning_start].split(":", 1)[1].strip()
    extra_lines = [l.strip() for l in lines[reasoning_start + 1:] if l.strip()]
    reasoning = " ".join([first_reasoning_line] + extra_lines).strip()

    return {"signal": signal, "reasoning": reasoning}


@_retry
def _call_api(client, model: str, prompt: str) -> str:
    """Make the LLM API call. Only this function is retried, not prompt building or response parsing."""
    if hasattr(client, "messages"):
        response = client.messages.create(
            model=model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    response = client.chat.completions.create(
        model=model,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def analyze_ticker(
    ticker: str,
    info: dict,
    headlines: list[str],
    config: Config,
    client=None,
) -> dict:
    """
    Call the configured analyst provider to get a BUY/HOLD/SKIP signal.
    Returns {"signal": str, "reasoning": str}.
    """
    if client is None:
        client = create_analyst_client(config)

    model = config.analyst_model or _DEFAULT_MODELS.get(config.analyst_provider, "")
    prompt = build_prompt(ticker, info, headlines)

    if config.analyst_call_delay_s > 0:
        time.sleep(config.analyst_call_delay_s)

    text = _call_api(client, model, prompt)

    return parse_claude_response(text)
