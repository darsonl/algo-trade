"""A model that keeps failing is skipped for the rest of the SCAN.

Tenacity asks whether *this request* is worth trying again. On 2026-09-24 the
right question was whether *this model* had been failing all scan: 171 of 172
Gemini requests failed, every one of 38 tickers went primary x3 -> fallback x3
-> DeepSeek after ~10 minutes of back-off, and `gemini-3.7-flash` spent its
whole 20 RPD on 503s before any ticker could use it. On 2026-09-25 retrying
HELPED (37 of 72 requests succeeded), so "never retry a 503" is wrong -- the
breaker is scoped to one model within one scan, and needs K CONSECUTIVE
failures of that model.

K=3 is the default because it was replayed against every session from 09-24 to
09-30 (the answering model per ticker, from `shadow_observations`): it trips
within the first six tickers of the 09-24 outage and never skips a model that
would have answered. K=2 would have sent 7 answerable tickers to the paid tier,
6 of them on 09-28.
"""
import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from analyst import claude_analyst
from analyst.claude_analyst import analyze_ticker
from analyst.model_breaker import ModelBreaker, ModelSkipped
from config import Config

GOOD = "SIGNAL: BUY\nREASONING: Fine.\nCONFIDENCE: high"


# --- the breaker itself ------------------------------------------------------

def test_trips_on_the_kth_consecutive_failure_not_before():
    b = ModelBreaker(3)
    b.record_failure("gemini", "m")
    b.record_failure("gemini", "m")
    assert not b.is_open("gemini", "m")
    b.record_failure("gemini", "m")
    assert b.is_open("gemini", "m")


def test_a_success_resets_the_streak():
    """09-25's shape: failures interleaved with successes are an overloaded
    model that still answers, and must never trip."""
    b = ModelBreaker(3)
    for _ in range(5):
        b.record_failure("gemini", "m")
        b.record_failure("gemini", "m")
        b.record_success("gemini", "m")
    assert not b.is_open("gemini", "m")


def test_models_are_counted_separately_even_on_one_provider():
    """Both Gemini tiers are provider 'gemini'; Google meters per model, and a
    shared streak would trip the primary for the fallback's failures."""
    b = ModelBreaker(2)
    b.record_failure("gemini", "gemini-3.7-flash")
    b.record_failure("gemini", "gemini-3.7-flash")
    assert b.is_open("gemini", "gemini-3.7-flash")
    assert not b.is_open("gemini", "gemini-3.1-flash-lite")


def test_a_threshold_of_zero_disables_the_breaker():
    b = ModelBreaker(0)
    for _ in range(50):
        b.record_failure("gemini", "m")
    assert not b.is_open("gemini", "m")


def test_tripped_names_each_model_once():
    b = ModelBreaker(1)
    b.record_failure("gemini", "a")
    b.record_failure("gemini", "a")
    assert b.tripped == [("gemini", "a")]


# --- through the real fallback chain ----------------------------------------

def _config():
    c = Config()
    c.analyst_call_delay_s = 0.0
    c.analyst_provider = "gemini"
    c.analyst_model = "primary-model"
    c.analyst_fallback_provider = "gemini"
    c.analyst_fallback_model = "fallback-model"
    c.analyst_fallback2_provider = "deepseek"
    c.analyst_fallback2_model = "paid-model"
    return c


def _client(counter, name, fail=False, text=GOOD):
    """Anthropic-shaped fake: counts every request that reaches it."""
    client = MagicMock()

    def create(**_kwargs):
        counter[name] = counter.get(name, 0) + 1
        if fail:
            raise RuntimeError("503 UNAVAILABLE: high demand")
        response = MagicMock()
        response.content = [MagicMock(text=text)]
        return response

    client.messages.create.side_effect = create
    return client


def _analyse(client, fallback, fallback2, breaker, on_attempt=None):
    return analyze_ticker(
        "AAPL", {}, [], _config(), client=client, fallback_client=fallback,
        fallback2_client=fallback2, breaker=breaker, on_attempt=on_attempt,
    )


@pytest.fixture(autouse=True)
def _no_retry_sleep():
    # The wait strategy is bound at import time; see CLAUDE.md on fn.retry.sleep.
    with patch.object(claude_analyst._call_api.retry, "sleep"):
        yield


def test_a_tripped_primary_is_not_asked_again_this_scan():
    """THE 09-24 fix: after three tickers of primary failure, the fourth goes
    straight to the fallback and the primary receives no request at all."""
    n = {}
    primary = _client(n, "primary", fail=True)
    fallback = _client(n, "fallback")
    breaker = ModelBreaker(3)

    for _ in range(3):
        _analyse(primary, fallback, None, breaker)
    assert n["primary"] == 9  # 3 tickers x 3 tenacity attempts

    result = _analyse(primary, fallback, None, breaker)
    assert n["primary"] == 9, "a tripped model must not be sent another request"
    assert result["model_used"] == "fallback-model"


def test_a_skipped_model_spends_no_quota():
    """No request is made, so nothing may be counted against the model's RPD."""
    n = {}
    breaker = ModelBreaker(1)
    primary = _client(n, "primary", fail=True)
    fallback = _client(n, "fallback")
    _analyse(primary, fallback, None, breaker)

    attempts = []
    _analyse(primary, fallback, None, breaker,
             on_attempt=lambda p, m: attempts.append(m))
    assert attempts == ["fallback-model"]


def test_a_parse_error_does_not_count_toward_the_breaker():
    """A model that ANSWERS is available; an unparseable answer is a quality
    problem the fallback already handles, not an outage."""
    n = {}
    primary = _client(n, "primary", text="gibberish")
    fallback = _client(n, "fallback")
    breaker = ModelBreaker(2)

    for _ in range(4):
        _analyse(primary, fallback, None, breaker)
    assert n["primary"] == 4
    assert not breaker.is_open("gemini", "primary-model")


def test_the_fallback_trips_on_its_own_streak():
    """The fallback is asked only when the primary fails, so its streak runs
    across the tickers IT saw -- 09-28's ETF scan: VEA, GLD, XLK."""
    n = {}
    primary = _client(n, "primary", fail=True)
    fallback = _client(n, "fallback", fail=True)
    paid = _client(n, "paid")
    breaker = ModelBreaker(2)

    for _ in range(3):
        result = _analyse(primary, fallback, paid, breaker)
    assert n["fallback"] == 6, "two tickers x 3 attempts, then skipped"
    assert result["model_used"] == "paid-model"


def test_when_the_last_tier_is_tripped_the_call_fails_without_a_request():
    n = {}
    primary = _client(n, "primary", fail=True)
    breaker = ModelBreaker(1)
    with pytest.raises(RuntimeError):
        _analyse(primary, None, None, breaker)

    with pytest.raises(ModelSkipped, match="primary-model"):
        _analyse(primary, None, None, breaker)
    assert n["primary"] == 3


def test_a_fresh_breaker_asks_the_model_again():
    """Scope is ONE scan: the next scan's breaker starts closed."""
    n = {}
    primary = _client(n, "primary", fail=True)
    fallback = _client(n, "fallback")
    _analyse(primary, fallback, None, ModelBreaker(1))
    _analyse(primary, fallback, None, ModelBreaker(1))
    assert n["primary"] == 6


def test_without_a_breaker_every_tier_is_always_asked():
    n = {}
    primary = _client(n, "primary", fail=True)
    fallback = _client(n, "fallback")
    for _ in range(4):
        _analyse(primary, fallback, None, None)
    assert n["primary"] == 12


# --- configuration and wiring ------------------------------------------------

def test_the_threshold_defaults_to_three(monkeypatch):
    monkeypatch.delenv("ANALYST_BREAKER_THRESHOLD", raising=False)
    assert Config().analyst_breaker_threshold == 3


def test_the_threshold_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("ANALYST_BREAKER_THRESHOLD", "0")
    assert Config().analyst_breaker_threshold == 0


_MAIN = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
_ANALYSE = {"analyze_ticker", "analyze_etf_ticker", "analyze_sell_ticker"}


def _func(name):
    return next(n for n in ast.walk(_MAIN)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


def _analyst_calls(node):
    """Every call that hands an analyze_* function to the analyst, directly or
    through asyncio.to_thread(analyze_x, ...)."""
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        names = [getattr(call.func, "id", None)] + [
            getattr(a, "id", None) for a in call.args[:1]]
        if _ANALYSE & set(names):
            yield call


def test_every_analyst_call_in_main_passes_the_scan_breaker():
    calls = list(_analyst_calls(_MAIN))
    assert len(calls) == 3, "buy, sell and ETF call sites"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert isinstance(kw.get("breaker"), ast.Name) and kw["breaker"].id == "breaker", (
            f"line {call.lineno}: analyst call does not pass breaker=breaker")


@pytest.mark.parametrize("scan", ["_run_scan_locked", "_run_scan_etf_locked"])
def test_each_scan_builds_its_own_breaker_from_config(scan):
    """Built INSIDE the scan body (which runs once per scan, under the lock),
    so its lifetime is one scan. A module-level breaker would carry a tripped
    model into tomorrow's session."""
    assigns = [n for n in ast.walk(_func(scan)) if isinstance(n, ast.Assign)
               and any(getattr(t, "id", None) == "breaker" for t in n.targets)]
    assert len(assigns) == 1
    call = assigns[0].value
    assert isinstance(call, ast.Call) and getattr(call.func, "id", None) == "ModelBreaker"
    assert ast.unparse(call.args[0]) == "config.analyst_breaker_threshold"
