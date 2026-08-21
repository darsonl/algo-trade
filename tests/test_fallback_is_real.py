"""A fallback that resolves to the primary is not a fallback.

`_run_with_fallbacks` resolves each tier as `config.<tier>_model or
_DEFAULT_MODELS[provider]`. So with ANALYST_FALLBACK_PROVIDER=gemini and
ANALYST_FALLBACK_MODEL unset, the fallback resolves to the SAME model as the
primary — and retries it against the same exhausted per-model quota, silently.

Gemini quota is enforced per project and per model, so a second key buys
nothing; the fallback tier's entire value is being a DIFFERENT MODEL. When it
is not, the tier costs a wasted call and its latency, and returns the same
failure.
"""
from analyst.claude_analyst import degenerate_fallback_reason


def test_a_different_model_is_a_real_fallback():
    assert degenerate_fallback_reason(
        "gemini", "gemini-3.7-flash", "gemini", "gemini-3.1-flash-lite"
    ) is None


def test_the_same_provider_and_model_is_degenerate():
    reason = degenerate_fallback_reason(
        "gemini", "gemini-3.7-flash", "gemini", "gemini-3.7-flash"
    )

    assert reason is not None
    assert "gemini-3.7-flash" in reason


def test_a_different_provider_is_fine_even_with_the_same_model_name():
    """Same name on two providers is two different services and two quotas."""
    assert degenerate_fallback_reason(
        "gemini", "gpt-4o-mini", "github", "gpt-4o-mini"
    ) is None


def test_no_fallback_configured_is_not_flagged():
    """Absence of a fallback is a choice, not a misconfiguration."""
    assert degenerate_fallback_reason("gemini", "gemini-3.7-flash", "", "") is None


def test_the_unset_fallback_model_case_this_exists_for():
    """The real trap: provider set, model left blank, so it defaults to the
    primary's default. This is what a `.env` one deletion away looks like."""
    from analyst.claude_analyst import _DEFAULT_MODELS

    primary = _DEFAULT_MODELS["gemini"]
    resolved_fallback = "" or _DEFAULT_MODELS["gemini"]  # what line 354 does

    assert degenerate_fallback_reason(
        "gemini", primary, "gemini", resolved_fallback
    ) is not None
