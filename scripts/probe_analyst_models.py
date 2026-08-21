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

It calls through the app's own client construction, its own prompt builder and
its own `parse_claude_response`, because "the model replied" and "the reply
parses" are different questions and only the second one matters here.

    .venv/Scripts/python.exe scripts/probe_analyst_models.py
    .venv/Scripts/python.exe scripts/probe_analyst_models.py --repeat 6
    .venv/Scripts/python.exe scripts/probe_analyst_models.py gemini:gemini-2.5-flash

With no arguments it probes ALL THREE configured tiers, each through its own
provider's endpoint. Naming a model probes only that one; qualify it as
`provider:model` to probe a provider other than the primary's.

Each model draws on its own quota pool, so probing one does not spend another's.

## Pacing

The delay between calls defaults to ANALYST_CALL_DELAY_S -- the throttle the
scan itself runs at, which is what this account is calibrated to. A shorter
delay is REFUSED rather than obeyed: probing at ~40 RPM against a 5 RPM cap
produces 503s that read as model instability, and that misreading has already
happened twice on this path. RPM and RPD failures are indistinguishable from
the client; only the AI Studio dashboard separates them.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openai  # noqa: E402

from analyst.claude_analyst import (  # noqa: E402
    _DEFAULT_MODELS,
    _OPENAI_BASE_URLS,
    build_prompt,
    parse_claude_response,
)
from config import Config  # noqa: E402

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


@dataclass
class Tier:
    """One rung of the fallback chain, with the client that actually reaches it."""

    label: str
    provider: str
    model: str
    client: object


def _client_for(provider: str, api_key: str):
    """Build a client the same way the app does, via _OPENAI_BASE_URLS.

    The previous version hardcoded the Gemini base URL, so probing the deepseek
    tier posted a deepseek model id to Google and reported the resulting error
    as a failure OF THE MODEL -- the precise category of wrong conclusion this
    script exists to prevent.
    """
    return openai.OpenAI(api_key=api_key, base_url=_OPENAI_BASE_URLS.get(provider))


def _configured_tiers(config: Config) -> list[tuple[str, str, str, str]]:
    """(label, provider, model, api_key) for each configured tier."""
    raw = [
        ("primary  ", config.analyst_provider, config.analyst_model,
         config.analyst_api_key),
        ("fallback ", config.analyst_fallback_provider, config.analyst_fallback_model,
         config.analyst_fallback_api_key),
        ("fallback2", config.analyst_fallback2_provider, config.analyst_fallback2_model,
         config.analyst_fallback2_api_key),
    ]
    out = []
    for label, provider, model, key in raw:
        if not provider or not key:
            continue
        out.append((label, provider, model or _DEFAULT_MODELS.get(provider, ""), key))
    return out


def resolve_tiers(config: Config, model_args: list[str]) -> list[Tier]:
    """Which tiers to probe, each with its OWN provider's client.

    No arguments probes the whole configured chain including fallback2 -- the
    tier that catches the other two, and therefore the last one worth leaving
    unmeasured. An explicit `provider:model` overrides; a bare model id is
    assumed to be on the primary's provider.
    """
    configured = _configured_tiers(config)
    if not model_args:
        if not configured:
            print("no models to probe; set ANALYST_MODEL or pass them as arguments")
            raise SystemExit(1)
        return [
            Tier(label, provider, model, _client_for(provider, key))
            for label, provider, model, key in configured
        ]

    keys = {provider: key for _, provider, _, key in configured}
    tiers = []
    for arg in model_args:
        provider, _, model = arg.rpartition(":")
        provider = provider or config.analyst_provider
        key = keys.get(provider) or config.analyst_api_key
        tiers.append(Tier("explicit ", provider, model, _client_for(provider, key)))
    return tiers


def resolve_delay(config: Config, requested: float | None) -> float:
    """Seconds between calls. Refuses to go below the scan's own throttle.

    ANALYST_CALL_DELAY_S is set from the model's measured RPM. Probing faster
    than the scan runs manufactures 503s and invites the conclusion that the
    model is unstable, which is how gemini-3.7-flash was wrongly benched once
    already.
    """
    floor = float(config.analyst_call_delay_s or 0.0)
    if requested is None:
        return floor
    if requested < floor:
        print(f"refusing --delay {requested}: below ANALYST_CALL_DELAY_S={floor}.")
        print("Probing faster than the scan runs produces 503s that look like")
        print("model instability but are rate limiting. Raise the delay, or lower")
        print("ANALYST_CALL_DELAY_S if the model's real RPM allows it.")
        raise SystemExit(2)
    return requested


def probe(client, model: str, repeat: int, delay: float,
          sleep=time.sleep) -> tuple[int, int, list[str]]:
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
                hint = ("  <-- 503 here is as likely QUOTA as flakiness; check the"
                        " AI Studio dashboard") if "503" in detail else ""
                notes.append(f"{ticker}: API {detail}{hint}")
                sleep(delay)
                continue
            try:
                parse_claude_response(text)
                parsed += 1
            except ValueError as exc:
                notes.append(f"{ticker}: PARSE {exc}")
            sleep(delay)
    return parsed, attempted, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("models", nargs="*",
                    help="'model' or 'provider:model' (default: the configured chain)")
    ap.add_argument("--repeat", type=int, default=1, help="passes over the 3 cases")
    ap.add_argument("--delay", type=float, default=None,
                    help="seconds between calls (default & minimum: ANALYST_CALL_DELAY_S)")
    args = ap.parse_args()

    config = Config()
    tiers = resolve_tiers(config, args.models)
    delay = resolve_delay(config, args.delay)

    calls = len(CASES) * args.repeat
    print(f"probing {len(tiers)} tier(s), {calls} call(s) each, {delay}s apart")
    print(f"~{len(tiers) * calls * delay / 60:.1f} min total\n")

    worst = 1.0
    for tier in tiers:
        parsed, attempted, notes = probe(tier.client, tier.model, args.repeat, delay)
        rate = parsed / attempted if attempted else 0.0
        worst = min(worst, rate)
        print(f"{tier.label}  {tier.provider}:{tier.model:<24} "
              f"{parsed}/{attempted} parsed  ({rate:.0%})")
        for note in notes:
            print(f"      {note}")
        print()

    if worst < 1.0:
        print("NOT all calls parsed. A model that cannot parse contributes nothing to")
        print("the chain but latency -- see tests/test_fallback_is_real.py.")
        print("Before concluding a model is bad: a 503 on the Gemini free tier is")
        print("reported for QUOTA exhaustion too, and only the dashboard says which.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
