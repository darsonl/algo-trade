"""Measure analyst-model parse reliability against the real API.

Run this before changing ANALYST_MODEL or ANALYST_FALLBACK_MODEL. It exists
because the models on this path have twice been chosen on assumption and been
wrong both times:

* `gemma-4-31b-it` was the configured fallback from April to August 2026 and
  scores 0/3 -- it echoes the prompt template (`<BUY|HOLD|SKIP>`) and leaks
  `<thought>` blocks on EVERY call, so the fallback tier could never have helped;
* `gemini-3.7-flash` was then judged unstable on a 2/6 HTTP 503 rate, which
  turned out to be its DAILY QUOTA being exhausted by the probing itself.

The second is the trap worth remembering: **this free tier reports per-model
exhaustion as 503, not 429**, so a spent budget is indistinguishable from
flakiness unless you check the AI Studio dashboard. Quota resets at 00:00 PT.

It calls through the app's own OpenAI-compat client and its own
`parse_claude_response`, because "the model replied" and "the reply parses" are
different questions and only the second one matters here.

    .venv/Scripts/python.exe scripts/probe_analyst_models.py
    .venv/Scripts/python.exe scripts/probe_analyst_models.py --repeat 6 gemini-3.7-flash

Each model draws on its own quota pool, so probing one does not spend another's.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openai  # noqa: E402

from analyst.claude_analyst import build_prompt, parse_claude_response  # noqa: E402
from config import Config  # noqa: E402

GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Varied on purpose: bullish, bearish and mixed, so a model cannot pass by
# always answering the same way.
CASES = [
    ("AAPL", {"trailingPE": 34.2, "dividendYield": 0.0044, "earningsGrowth": 0.11},
     ["Apple beats quarterly earnings on services growth",
      "iPhone demand steady in China despite competition"]),
    ("XOM", {"trailingPE": 12.1, "dividendYield": 0.033, "earningsGrowth": -0.18},
     ["Oil prices slide on demand worries",
      "Exxon cuts capital spending guidance for next year"]),
    ("NVDA", {"trailingPE": 61.5, "dividendYield": 0.0002, "earningsGrowth": 0.94},
     ["Nvidia data-center revenue doubles year over year",
      "Regulators weigh new export restrictions on AI chips"]),
]


def probe(client, model: str, repeat: int) -> tuple[int, int, list[str]]:
    """Return (parsed, attempted, failure notes) for one model."""
    parsed = attempted = 0
    notes: list[str] = []
    for _ in range(repeat):
        for ticker, info, headlines in CASES:
            attempted += 1
            prompt = build_prompt(ticker=ticker, info=info, headlines=headlines)
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=400,
                )
                text = resp.choices[0].message.content or ""
            except Exception as exc:
                detail = str(exc)[:70].replace("\n", " ")
                hint = "  <-- may be QUOTA, not flakiness; check AI Studio" if "503" in detail else ""
                notes.append(f"{ticker}: API {detail}{hint}")
                continue
            try:
                parse_claude_response(text)
                parsed += 1
            except ValueError as exc:
                notes.append(f"{ticker}: PARSE {exc}")
            time.sleep(1.5)
    return parsed, attempted, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("models", nargs="*", help="model ids (default: the configured chain)")
    ap.add_argument("--repeat", type=int, default=1, help="passes over the 3 cases")
    args = ap.parse_args()

    config = Config()
    models = args.models or [
        m for m in (config.analyst_model, config.analyst_fallback_model) if m
    ]
    if not models:
        print("no models to probe; pass them as arguments or set ANALYST_MODEL")
        return 1

    client = openai.OpenAI(api_key=config.analyst_api_key, base_url=GEMINI_OPENAI_BASE)
    print(f"probing {len(models)} model(s), {len(CASES) * args.repeat} call(s) each\n")

    worst = 1.0
    for model in models:
        parsed, attempted, notes = probe(client, model, args.repeat)
        rate = parsed / attempted if attempted else 0.0
        worst = min(worst, rate)
        print(f"{model:<26} {parsed}/{attempted} parsed  ({rate:.0%})")
        for note in notes:
            print(f"    {note}")
        print()

    if worst < 1.0:
        print("NOT all calls parsed. A model that cannot parse contributes nothing to")
        print("the chain but latency — see tests/test_fallback_is_real.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
