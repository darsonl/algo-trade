"""The observation builder is pure: no DB, no clock, no network, no mocks."""
import json

import pytest

from research.shadow_log import (
    OUTCOMES,
    STAGES,
    ShadowObservation,
    build_observation,
)


def _obs(**kw):
    base = dict(
        ticker="AAPL",
        scan_kind="stock",
        stage="fundamental",
        outcome="rejected_fundamental",
        session_date="2026-08-20",
        observed_at="2026-08-20T13:45:00Z",
    )
    base.update(kw)
    return build_observation(**base)


def test_minimal_observation_carries_the_funnel_position():
    o = _obs()
    assert isinstance(o, ShadowObservation)
    assert (o.ticker, o.scan_kind) == ("AAPL", "stock")
    assert (o.stage_reached, o.outcome) == ("fundamental", "rejected_fundamental")
    assert o.session_date == "2026-08-20"


def test_unknown_stage_is_rejected():
    """A typo'd stage would silently create a funnel bucket nobody reads."""
    with pytest.raises(ValueError, match="stage"):
        _obs(stage="fundamentals")


def test_unknown_outcome_is_rejected():
    with pytest.raises(ValueError, match="outcome"):
        _obs(outcome="nope")


def test_dicts_are_serialised_to_json_text():
    o = _obs(fundamentals={"trailingPE": 34.2}, technicals={"rsi": 53.8},
             macro={"vix_level": 14.1})
    assert json.loads(o.fundamentals_json) == {"trailingPE": 34.2}
    assert json.loads(o.technicals_json) == {"rsi": 53.8}
    assert json.loads(o.macro_json) == {"vix_level": 14.1}


def test_headlines_are_serialised_as_a_list():
    o = _obs(headlines=["a", "b"])
    assert json.loads(o.headlines_json) == ["a", "b"]


def test_absent_payloads_stay_none_not_empty_json():
    """None and {} are different: None means the stage was never reached, {}
    means it was reached and produced nothing. Collapsing them loses the
    distinction the funnel is FOR."""
    o = _obs()
    assert o.fundamentals_json is None
    assert o.technicals_json is None
    assert o.headlines_json is None


def test_empty_dict_is_recorded_as_empty_json():
    o = _obs(fundamentals={})
    assert o.fundamentals_json == "{}"


def test_analysis_is_unpacked_into_attribution_columns():
    """provider AND model, because Gemini meters per model and neither the
    analyst cache nor the analyst's return dict has ever recorded which model
    answered."""
    o = _obs(
        stage="analyst",
        outcome="rejected_signal",
        analysis={
            "signal": "HOLD",
            "confidence": "medium",
            "provider_used": "gemini",
            "model_used": "gemini-3.1-flash-lite",
            "raw_response": "SIGNAL: HOLD",
            "prompt_sha256": "abc123",
        },
    )
    assert o.analyst_signal == "HOLD"
    assert o.analyst_confidence == "medium"
    assert o.analyst_provider == "gemini"
    assert o.analyst_model == "gemini-3.1-flash-lite"
    assert o.analyst_raw_response == "SIGNAL: HOLD"
    assert o.analyst_prompt_sha256 == "abc123"


def test_analysis_missing_model_does_not_raise():
    """An older analyst result without model_used must still record."""
    o = _obs(stage="analyst", outcome="rejected_signal",
             analysis={"signal": "HOLD", "provider_used": "gemini"})
    assert o.analyst_model is None
    assert o.analyst_signal == "HOLD"


def test_cache_hit_is_stored_as_int():
    assert _obs(cache_hit=True).cache_hit == 1
    assert _obs(cache_hit=False).cache_hit == 0


def test_every_stage_and_outcome_constant_is_a_plain_string():
    assert all(isinstance(s, str) for s in STAGES)
    assert all(isinstance(o, str) for o in OUTCOMES)
