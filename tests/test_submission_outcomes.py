"""Order submission is never retried; its outcome is classified (spec v4 §3).

A timeout after Schwab accepts an order is an UNKNOWN outcome, not a failure.
The Schwab order API has no idempotency key, so a retry can place the same buy
twice -- and the second one is a real position nobody approved.

The replacement is classification. Only a definitive broker refusal releases
capital; everything ambiguous keeps reserving it, because the alternative is a
ceiling that has already been spent by an order we decided to forget.
"""
import pytest

from schwab_client.orders import _dispatch, classify_submission


class _Resp:
    def __init__(self, status=201, location="https://api.schwab.com/orders/1234"):
        self.status_code = status
        self.headers = {"Location": location} if location else {}


class _CountingClient:
    def __init__(self, exc=None, resp=None):
        self.exc, self.resp, self.calls = exc, resp, 0

    def place_order(self, account_hash, spec):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.resp


# --- never retried ---

def test_a_failing_submission_is_attempted_exactly_once():
    """The whole point. Three attempts against an API with no idempotency key
    is three chances to buy the same stock."""
    client = _CountingClient(exc=TimeoutError("read timed out"))
    with pytest.raises(TimeoutError):
        _dispatch(client, "HASH", {"spec": True})
    assert client.calls == 1


def test_a_successful_submission_is_also_attempted_once():
    client = _CountingClient(resp=_Resp())
    _dispatch(client, "HASH", {"spec": True})
    assert client.calls == 1


def test_submission_carries_no_retry_decorator():
    """A future refactor must not quietly re-wrap this. tenacity leaves
    `retry` attributes on what it decorates."""
    assert not hasattr(_dispatch, "retry")
    assert not hasattr(_dispatch, "retry_with")


# --- classification ---

def test_2xx_with_a_location_header_is_submitted():
    outcome = classify_submission(response=_Resp(201, "https://x/orders/9876"))
    assert outcome.status == "submitted"
    assert outcome.broker_order_id == "9876"
    assert outcome.reserves_capital


def test_2xx_without_a_location_header_is_unknown():
    """Accepted but unidentifiable: it exists and we cannot name it."""
    outcome = classify_submission(response=_Resp(201, location=None))
    assert outcome.status == "submit_unknown"
    assert outcome.broker_order_id is None
    assert outcome.reserves_capital


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_a_definitive_4xx_is_submit_failed(status):
    """The broker refused. Nothing exists, so nothing should stay reserved."""
    outcome = classify_submission(error=_http_error(status))
    assert outcome.status == "submit_failed"
    assert not outcome.reserves_capital


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_ambiguous_http_statuses_are_unknown(status):
    """408 and 429 are 4xx but say nothing about whether the order landed, and
    a 5xx can be raised by a proxy after Schwab already accepted it."""
    outcome = classify_submission(error=_http_error(status))
    assert outcome.status == "submit_unknown"
    assert outcome.reserves_capital


@pytest.mark.parametrize("exc", [
    TimeoutError("read timed out"),
    ConnectionError("connection reset"),
    OSError("network unreachable"),
])
def test_transport_errors_are_unknown(exc):
    outcome = classify_submission(error=exc)
    assert outcome.status == "submit_unknown"
    assert outcome.reserves_capital


def test_an_error_with_no_recognisable_status_is_unknown():
    """The fail-closed default. An exception we cannot classify might still
    have placed an order."""
    outcome = classify_submission(error=RuntimeError("something odd"))
    assert outcome.status == "submit_unknown"
    assert outcome.reserves_capital


def test_only_submit_failed_releases_capital():
    """Stated as a single assertion because it is the property that matters:
    every ambiguous outcome must keep holding the ceiling."""
    releasing = [
        classify_submission(error=_http_error(400)).status,
    ]
    holding = [
        classify_submission(response=_Resp(201)).status,
        classify_submission(response=_Resp(201, location=None)).status,
        classify_submission(error=_http_error(429)).status,
        classify_submission(error=_http_error(503)).status,
        classify_submission(error=TimeoutError()).status,
        classify_submission(error=RuntimeError()).status,
    ]
    assert releasing == ["submit_failed"]
    assert all(s in ("submitted", "submit_unknown") for s in holding)


def test_classification_needs_one_of_response_or_error():
    with pytest.raises(ValueError):
        classify_submission()


def test_the_unknown_outcome_message_tells_the_operator_what_to_do():
    outcome = classify_submission(error=TimeoutError("read timed out"))
    assert "may or may not" in outcome.message.lower()
    assert "/resolve" in outcome.message


def _http_error(status: int) -> Exception:
    """An exception shaped like an httpx HTTPStatusError."""
    exc = RuntimeError(f"HTTP {status}")
    exc.response = _Resp(status=status, location=None)
    return exc
