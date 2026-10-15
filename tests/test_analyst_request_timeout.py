"""An analyst request must have a deadline, and a request that hits it is not retried.

Measured 2026-10-12: Gemini answered `200 OK` but took 60-124 s per call (the
usual is a few seconds). Nothing FAILED, so neither tenacity nor the per-scan
model breaker had anything to act on -- a slow success is invisible to both.
The stock scan ran 21:35 -> 22:08 Taipei and the 22:00 ETF scan was skipped by
the scan lock: a whole session's ETF cohort lost to latency. On 10-14 a single
call took ~375 s.

No client passed `timeout=`, so each request inherited the SDK default of
600 s, and tenacity makes 3 attempts: one hung tier could hold ONE ticker for
~30 minutes, and the breaker needs three failed tickers to trip. #65 turned the
SDKs' retries off (`max_retries=0`) but left their timeout alone -- the two
bound different things, how many requests and how long each may take.

A timeout is not retried because the deadline has already been paid in full:
retrying triples the cost of the slot for a model that has just shown it is
slow, while the next tier is a different model. It still propagates out of
`_call_api` as a failure, so the fallback chain and the breaker handle it like
any other failed request.
"""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic
import httpx
import openai
import pytest

from analyst import claude_analyst
from analyst.claude_analyst import (
    _call_api,
    _should_retry,
    create_analyst_client,
    create_fallback2_client,
    create_fallback_client,
)
from config import Config


def _config(timeout_s=7.5):
    c = Config()
    c.analyst_request_timeout_s = timeout_s
    c.analyst_provider = "gemini"
    c.analyst_api_key = "fake"
    c.anthropic_api_key = "fake"
    c.analyst_fallback_provider = "gemini"
    c.analyst_fallback_model = "gemini-3.7-flash"
    c.analyst_fallback_api_key = "fake"
    c.analyst_fallback2_provider = "deepseek"
    c.analyst_fallback2_api_key = "fake"
    return c


@pytest.mark.parametrize("factory", [
    create_analyst_client, create_fallback_client, create_fallback2_client,
])
@pytest.mark.parametrize("provider", ["gemini", "claude"])
def test_every_client_carries_the_configured_timeout(factory, provider):
    c = _config(7.5)
    c.analyst_provider = c.analyst_fallback_provider = c.analyst_fallback2_provider = provider
    client = factory(c)
    assert client.timeout == 7.5


def test_the_default_bounds_the_sdk_default_of_600s():
    assert Config().analyst_request_timeout_s == 60.0


@pytest.mark.parametrize("bad", ["0", "-5"])
def test_a_non_positive_timeout_fails_startup(monkeypatch, bad):
    """0 would not mean 'off': the SDKs read a falsy timeout as NO deadline at
    all, strictly worse than the 600 s this replaces."""
    monkeypatch.setenv("ANALYST_REQUEST_TIMEOUT_S", bad)
    with pytest.raises(ValueError, match="ANALYST_REQUEST_TIMEOUT_S"):
        Config()._validate_analyst_timeout()


@pytest.mark.parametrize("exc", [
    openai.APITimeoutError(request=httpx.Request("POST", "https://x")),
    anthropic.APITimeoutError(request=httpx.Request("POST", "https://x")),
])
def test_a_timeout_is_not_retried(exc):
    assert _should_retry(exc) is False


def test_a_503_is_still_retried():
    """The control: on 2026-09-25 retrying 503s rescued 37 of 72 requests."""
    assert _should_retry(RuntimeError("503 UNAVAILABLE")) is True


@pytest.fixture
def slow_endpoint():
    """A real HTTP server that answers nothing for longer than the client waits."""
    hits = {"n": 0}

    class Slow(BaseHTTPRequestHandler):
        def do_POST(self):
            hits["n"] += 1
            time.sleep(1.5)
            try:
                self.send_response(200)
                self.end_headers()
            except OSError:
                pass

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1", hits
    server.shutdown()
    server.server_close()


def test_a_slow_request_through_the_real_sdk_is_one_request_not_three(
        slow_endpoint, monkeypatch):
    """End to end: the app's own client factory, the real openai SDK, the real
    `_call_api` with its tenacity policy. Only the network is local."""
    url, hits = slow_endpoint
    monkeypatch.setitem(claude_analyst._OPENAI_BASE_URLS, "gemini", url)
    monkeypatch.setattr(_call_api.retry, "sleep", lambda _s: None)
    client = create_analyst_client(_config(timeout_s=0.3))

    started = time.monotonic()
    with pytest.raises(openai.APITimeoutError):
        _call_api(client, "m", "prompt", provider="gemini")

    assert hits["n"] == 1
    assert time.monotonic() - started < 1.5
