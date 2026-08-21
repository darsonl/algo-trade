"""Forward shadow log: every candidate the pipeline saw, and what became of it.

The recorder exists because no retrospective backtest of this strategy is
possible -- two of its three entry gates cannot be reconstructed historically
(see specs/2026-08-21-strategy-validation-design.md). Forward recording is the
only way to gather evidence about the fundamental filter, the analyst and the
human approver.

This module is PURE. Building an observation touches no database, no clock and
no network, so the funnel's semantics can be tested without mocks. The clock and
the database live in the caller.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

# Ordered: a candidate reaches stages left to right and stops where it fails.
STAGES = (
    "universe",      # entered the loop; skipped before any data was fetched
    "fundamental",   # fundamentals fetched
    "analyst",       # headlines fetched and the analyst consulted
    "technical",     # technical data fetched
    "recommended",   # posted to Discord
)

OUTCOMES = (
    "skipped_recommended_today",
    "skipped_open_position",
    "skipped_active_recommendation",
    "rejected_fundamental",
    "skipped_quota_exhausted",
    "rejected_signal",      # analyst returned HOLD or SKIP
    "rejected_technical",   # analyst said BUY; the technical filter refused
    "recommended",
    "error",
)


@dataclass
class ShadowObservation:
    """One candidate's exit from the pipeline. Field names match the columns."""

    session_date: str
    observed_at: str
    ticker: str
    scan_kind: str
    stage_reached: str
    outcome: str
    reject_reason: str | None = None
    fundamentals_json: str | None = None
    technicals_json: str | None = None
    headlines_json: str | None = None
    macro_json: str | None = None
    analyst_provider: str | None = None
    analyst_model: str | None = None
    analyst_signal: str | None = None
    analyst_confidence: str | None = None
    analyst_prompt_sha256: str | None = None
    analyst_raw_response: str | None = None
    cache_hit: int = 0
    recommendation_id: int | None = None
    reference_price: float | None = None


def _dumps(payload) -> str | None:
    """None stays None; {} becomes '{}'.

    The two mean different things -- "the stage was never reached" versus "it
    was reached and produced nothing" -- and the funnel exists to tell them
    apart, so they must not collapse. `default=str` keeps a stray Timestamp or
    Decimal from raising inside a recorder that must never raise.
    """
    if payload is None:
        return None
    return json.dumps(payload, default=str)


def build_observation(
    ticker: str,
    scan_kind: str,
    stage: str,
    outcome: str,
    *,
    session_date: str,
    observed_at: str,
    reject_reason: str | None = None,
    fundamentals: dict | None = None,
    technicals: dict | None = None,
    headlines: list | None = None,
    macro: dict | None = None,
    analysis: dict | None = None,
    cache_hit: bool = False,
    recommendation_id: int | None = None,
    reference_price: float | None = None,
) -> ShadowObservation:
    """Assemble one observation, validating the funnel position.

    `stage` and `outcome` are checked against the enums because a typo would
    silently create a bucket no report reads, and the error would surface as a
    quietly missing row rather than a failure.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}; expected one of {OUTCOMES}")

    analysis = analysis or {}
    return ShadowObservation(
        session_date=session_date,
        observed_at=observed_at,
        ticker=ticker,
        scan_kind=scan_kind,
        stage_reached=stage,
        outcome=outcome,
        reject_reason=reject_reason,
        fundamentals_json=_dumps(fundamentals),
        technicals_json=_dumps(technicals),
        headlines_json=_dumps(headlines),
        macro_json=_dumps(macro),
        analyst_provider=analysis.get("provider_used"),
        analyst_model=analysis.get("model_used"),
        analyst_signal=analysis.get("signal"),
        analyst_confidence=analysis.get("confidence"),
        analyst_prompt_sha256=analysis.get("prompt_sha256"),
        analyst_raw_response=analysis.get("raw_response"),
        cache_hit=1 if cache_hit else 0,
        recommendation_id=recommendation_id,
        reference_price=reference_price,
    )
