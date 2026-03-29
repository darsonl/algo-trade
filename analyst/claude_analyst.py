from __future__ import annotations
from config import Config

_VALID_SIGNALS = {"BUY", "HOLD", "SKIP"}


def build_prompt(ticker: str, info: dict, headlines: list[str]) -> str:
    """Build the prompt sent to Claude for a single ticker analysis."""
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
    Parse Claude's response into {signal, reasoning}.
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

    # Reasoning may span multiple lines after the REASONING: prefix
    first_reasoning_line = lines[reasoning_start].split(":", 1)[1].strip()
    extra_lines = [l.strip() for l in lines[reasoning_start + 1:] if l.strip()]
    reasoning = " ".join([first_reasoning_line] + extra_lines).strip()

    return {"signal": signal, "reasoning": reasoning}


def analyze_ticker(
    ticker: str,
    info: dict,
    headlines: list[str],
    config: Config,
    client=None,
) -> dict:
    """
    Call Claude API to get a BUY/HOLD/SKIP signal for a ticker.
    Returns {"signal": str, "reasoning": str}.

    Pass a pre-built anthropic.Anthropic client, or one will be created from config.
    """
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    prompt = build_prompt(ticker, info, headlines)
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    return parse_claude_response(message.content[0].text)
