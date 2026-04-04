from __future__ import annotations
import logging
import time
import anthropic
import openai
from config import Config
from tenacity import retry, retry_if_exception, stop_after_attempt

logger = logging.getLogger(__name__)


def _parse_retry_delay(exc) -> float | None:
    """Parse retryDelay seconds from a 429 response body, or return None."""
    if not (exc and hasattr(exc, "response")):
        return None
    try:
        details = exc.response.json()["error"]["details"]
        detail = next((d for d in details if "retryDelay" in d), None)
        if detail is None:
            return None
        raw = detail["retryDelay"]
        if isinstance(raw, str) and raw.endswith("s"):
            return float(raw[:-1])
        return float(raw)
    except Exception:
        return None


def _should_retry(exc) -> bool:
    """Return False when daily quota is exhausted (retryDelay > 60s) — don't retry."""
    delay = _parse_retry_delay(exc)
    if delay is not None and delay > 60:
        return False
    return True


def _wait_for_retry(retry_state) -> float:
    """Wait based on retryDelay in 429 response body, fallback to exponential."""
    delay = _parse_retry_delay(retry_state.outcome.exception())
    if delay is not None:
        return delay + 1
    return min(2 ** retry_state.attempt_number, 60)


_retry = retry(
    wait=_wait_for_retry,
    retry=retry_if_exception(_should_retry),
    stop=stop_after_attempt(3),
    reraise=True,
)

_VALID_SIGNALS = {"BUY", "HOLD", "SKIP", "SELL"}

_OPENAI_BASE_URLS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "github": "https://models.inference.ai.azure.com",
}

_DEFAULT_MODELS: dict[str, str] = {
    "claude": "claude-opus-4-6",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.5-flash",
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


def create_fallback_client(config: Config):
    """Return an SDK client for the fallback provider, or None if not configured."""
    provider = config.analyst_fallback_provider
    if not provider or not config.analyst_fallback_api_key:
        return None
    if provider == "claude":
        return anthropic.Anthropic(api_key=config.analyst_fallback_api_key)
    base_url = _OPENAI_BASE_URLS.get(provider)
    return openai.OpenAI(api_key=config.analyst_fallback_api_key, base_url=base_url)


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
    fallback_client=None,
) -> dict:
    """
    Call the configured analyst provider to get a BUY/HOLD/SKIP signal.
    Returns {"signal": str, "reasoning": str}.
    If the primary API call fails and fallback_client is provided, retries with the fallback.
    """
    if client is None:
        client = create_analyst_client(config)

    model = config.analyst_model or _DEFAULT_MODELS.get(config.analyst_provider, "")
    prompt = build_prompt(ticker, info, headlines)

    if config.analyst_call_delay_s > 0:
        time.sleep(config.analyst_call_delay_s)

    try:
        text = _call_api(client, model, prompt)
    except Exception as api_exc:
        if fallback_client is None:
            raise
        logger.warning(
            "Primary analyst failed for %s (%s), using fallback provider '%s'",
            ticker, api_exc, config.analyst_fallback_provider,
        )
        fallback_model = config.analyst_fallback_model or _DEFAULT_MODELS.get(
            config.analyst_fallback_provider, ""
        )
        text = _call_api(fallback_client, fallback_model, prompt)

    return parse_claude_response(text)


def build_sell_prompt(
    ticker: str,
    entry_price: float,
    current_price: float,
    pnl_pct: float,
    hold_days: int,
    rsi: float,
    headlines: list[str],
) -> str:
    """Build the sell analysis prompt for an open position (per D-07)."""
    headlines_block = (
        "\n".join(f"- {h}" for h in headlines) if headlines else "- No recent headlines available."
    )

    return f"""You are a stock analyst. Evaluate whether to SELL or HOLD the following position. Return exactly two lines.

Ticker: {ticker}

Position:
- Entry Price: ${entry_price:.2f}
- Current Price: ${current_price:.2f}
- P&L: {pnl_pct:+.1%}
- Hold Duration: {hold_days} days
- RSI: {rsi:.1f}

Recent news headlines:
{headlines_block}

Respond with exactly this format (no extra text):
SIGNAL: <SELL|HOLD>
REASONING: <2-3 sentences explaining your decision>"""


def analyze_sell_ticker(
    ticker: str,
    entry_price: float,
    current_price: float,
    pnl_pct: float,
    hold_days: int,
    rsi: float,
    headlines: list[str],
    config: Config,
    client=None,
    fallback_client=None,
) -> dict:
    """Call the analyst to get a SELL/HOLD signal for an open position.

    Reuses the same _call_api + fallback pipeline as analyze_ticker (per D-03).
    Returns {"signal": str, "reasoning": str}.
    """
    if client is None:
        client = create_analyst_client(config)

    model = config.analyst_model or _DEFAULT_MODELS.get(config.analyst_provider, "")
    prompt = build_sell_prompt(ticker, entry_price, current_price, pnl_pct, hold_days, rsi, headlines)

    if config.analyst_call_delay_s > 0:
        time.sleep(config.analyst_call_delay_s)

    try:
        text = _call_api(client, model, prompt)
    except Exception as api_exc:
        if fallback_client is None:
            raise
        logger.warning(
            "Primary analyst failed for %s sell analysis (%s), using fallback provider '%s'",
            ticker, api_exc, config.analyst_fallback_provider,
        )
        fallback_model = config.analyst_fallback_model or _DEFAULT_MODELS.get(
            config.analyst_fallback_provider, ""
        )
        text = _call_api(fallback_client, fallback_model, prompt)

    return parse_claude_response(text)
