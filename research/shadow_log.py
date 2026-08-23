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
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

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
    reference_price_source: str | None = None
    gate_config_json: str | None = None


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
    reference_price_source: str | None = None,
    gates: tuple = (),
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
    # EVERY gate applied to this candidate, merged -- not just the one that
    # decided the outcome. A row that reached the analyst was let through by
    # the fundamental gate, and "did a threshold change alter who reached the
    # analyst?" is unanswerable if only the last gate is recorded.
    thresholds: dict = {}
    for g in gates:
        thresholds.update(g.thresholds)
    # An explicit `reject_reason` wins. Not every rejection comes from a gate --
    # quota exhaustion and an analyst SKIP both name their own reason -- and a
    # gate that overwrote one would relabel a rejection it did not make.
    if reject_reason is None:
        reject_reason = next((g.failed_on for g in gates if g.failed_on), None)
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
        reference_price_source=reference_price_source,
        gate_config_json=_dumps(thresholds) if gates else None,
    )


def record(config, obs: ShadowObservation) -> int | None:
    """Persist one observation. Never raises."""
    try:
        # Local, so this module stays importable without the database package.
        # INSIDE the try, not above it: an import that raises would otherwise
        # escape and break the never-raises contract from the one line in the
        # function that is not covered by it.
        from database import queries
        return queries.record_shadow_observation(config.db_path, obs)
    except Exception:
        logger.exception("Shadow log write failed for %s; continuing", obs.ticker)
        return None


def observe(config, ticker: str, scan_kind: str, stage: str, outcome: str,
            *, instant=None, **kw) -> int | None:
    """Build and record one observation. NEVER RAISES -- this is the contract.

    Every failure mode is absorbed, including a bad stage/outcome from a typo at
    a call site. The scan is the product; this is instrumentation, and
    instrumentation that can abort the thing it measures is worse than none.

    `instant` is threaded through for the session date so tests can pin the
    clock, matching every other time-dependent function in this repo.
    """
    try:
        # Inside the try for the same reason as `record`'s import: the contract
        # in this docstring is only true if EVERY line of the function is
        # covered by the guard.
        from market_time import market_session_date
        now = instant or datetime.now(timezone.utc)
        obs = build_observation(
            ticker, scan_kind, stage, outcome,
            session_date=market_session_date(now).isoformat(),
            observed_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            **kw,
        )
    except Exception:
        logger.exception("Shadow log build failed for %s; continuing", ticker)
        return None
    return record(config, obs)
