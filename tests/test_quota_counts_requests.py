"""The analyst quota counter must count what the PROVIDER meters: requests.

Measured against the live Gemini dashboard on 2026-09-23. In one session the
`analyst_calls` table recorded **50** attempts while Google recorded **205**
requests -- a 4.1x undercount. The consequence was not cosmetic: the fallback
tier `gemini-3.7-flash` blew both its limits (23/20 RPD, 7/5 RPM) while our own
counter read `7`, so every `ANALYST_*_DAILY_LIMIT` guard was reasoning about a
number four times below the one that actually refuses the call.

`gemini-3.7-flash` is the clean control for this: nothing else on the machine
calls it, so its 7-counted-vs-23-real is the amplification with no other caller
to confound it. 7 requests in one minute against a 5 RPM ceiling, out of at most
7 logical calls spread over 15 minutes, is only possible if retries fire
back-to-back.

Two layers multiplied under one counted attempt:

  * `@_retry` (tenacity) -- `stop_after_attempt(3)`
  * the OpenAI SDK's own `max_retries`, which DEFAULTS TO 2 (verified on
    openai 2.41.1) because no constructor here passed the argument

so one counted attempt was worth up to 3 x 3 = 9 real requests. `_should_retry`
cannot bound this: it short-circuits only on a `QuotaFailure`/`PerDay` detail,
which a 503 "high demand" body does not carry, and it governs only the tenacity
layer -- the SDK retries underneath it either way.

The fix is one retry authority (tenacity) and a counter that increments where
the request is actually made.
"""
from unittest.mock import MagicMock, patch

import pytest

from analyst import claude_analyst
from analyst.claude_analyst import (
    analyze_ticker,
    create_analyst_client,
    create_fallback2_client,
    create_fallback_client,
)
from config import Config


def _make_config(provider="claude", model="test-model"):
    c = Config()
    c.analyst_call_delay_s = 0.0
    c.analyst_provider = provider
    c.analyst_model = model
    c.analyst_api_key = "fake-key"
    c.anthropic_api_key = "fake-key"
    c.analyst_fallback_provider = ""
    c.analyst_fallback2_provider = ""
    return c


def _client_that_always_fails(counter):
    """An Anthropic-shaped client whose every request raises, counting requests.

    A bare RuntimeError carries no `response`, so `_should_retry` returns True
    and tenacity exhausts all three attempts -- the 503 case, which is 157 of
    the 205 requests measured.
    """
    client = MagicMock()

    def boom(**_kwargs):
        counter["requests"] += 1
        raise RuntimeError("503 UNAVAILABLE: the model is experiencing high demand")

    client.messages.create.side_effect = boom
    return client


def test_quota_counter_counts_every_request_not_every_tier():
    """THE bug. One tier that retries 3 times consumed 3 of the provider's
    budget and reported 1."""
    counter = {"requests": 0}
    client = _client_that_always_fails(counter)
    attempts = []

    # The wait strategy is bound at import time, so this is the only patch that
    # stops a real sleep -- see CLAUDE.md's note on `fn.retry.sleep`.
    with patch.object(claude_analyst._call_api.retry, "sleep"):
        with pytest.raises(RuntimeError):
            analyze_ticker(
                "AAPL", {}, [], _make_config(),
                client=client,
                on_attempt=lambda provider, model: attempts.append((provider, model)),
            )

    assert counter["requests"] == 3, "tenacity should make three requests"
    assert len(attempts) == counter["requests"], (
        f"counted {len(attempts)} attempt(s) for {counter['requests']} real "
        "requests -- the counter must match what the provider meters"
    )


def test_every_counted_attempt_names_the_model_that_served_it():
    """Quota is metered per (provider, MODEL), so each request must be
    attributed to the model it was actually sent to."""
    counter = {"requests": 0}
    client = _client_that_always_fails(counter)
    attempts = []

    with patch.object(claude_analyst._call_api.retry, "sleep"):
        with pytest.raises(RuntimeError):
            analyze_ticker(
                "AAPL", {}, [], _make_config(model="claude-opus-4-8"),
                client=client,
                on_attempt=lambda provider, model: attempts.append((provider, model)),
            )

    assert attempts == [("claude", "claude-opus-4-8")] * 3


# --- the SDK's own retry layer ---------------------------------------------
#
# Tenacity has to be the SINGLE retry authority. These assert on a real client
# object built by our own constructors, not on a mock.

def _openai_config():
    c = _make_config(provider="gemini", model="gemini-3.1-flash-lite")
    c.analyst_fallback_provider = "gemini"
    c.analyst_fallback_api_key = "fake-fallback-key"
    c.analyst_fallback_model = "gemini-3.7-flash"
    c.analyst_fallback2_provider = "deepseek"
    c.analyst_fallback2_api_key = "fake-fallback2-key"
    c.analyst_fallback2_model = "deepseek-flash"
    return c


def test_primary_client_does_not_retry_underneath_tenacity():
    assert create_analyst_client(_openai_config()).max_retries == 0


def test_fallback_client_does_not_retry_underneath_tenacity():
    assert create_fallback_client(_openai_config()).max_retries == 0


def test_fallback2_client_does_not_retry_underneath_tenacity():
    assert create_fallback2_client(_openai_config()).max_retries == 0


def test_anthropic_client_does_not_retry_underneath_tenacity():
    """The Anthropic SDK defaults to max_retries=2 as well."""
    assert create_analyst_client(_make_config()).max_retries == 0
