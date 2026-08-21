"""The analyst result must say WHICH MODEL answered, not just which provider.

Both gemini tiers are provider 'gemini', so `provider_used` cannot distinguish
the 500-RPD primary from the 20-RPD fallback. PR #34 removed exactly this
conflation from quota accounting; the result dict still had it.
"""
from analyst.claude_analyst import _run_with_fallbacks
from config import Config


class _Client:
    """Returns a fixed parseable body, or raises, per construction."""

    def __init__(self, text=None, boom=False):
        self._text, self._boom = text, boom
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kw):
        if self._boom:
            raise RuntimeError("provider down")

        class _R:
            choices = [type("C", (), {"message": type(
                "M", (), {"content": self._text})()})()]
        return _R()


def _config():
    c = Config()
    c.analyst_provider = "gemini"
    c.analyst_model = "gemini-3.1-flash-lite"
    c.analyst_fallback_provider = "gemini"
    c.analyst_fallback_model = "gemini-3.7-flash"
    c.analyst_fallback2_provider = "deepseek"
    c.analyst_fallback2_model = "deepseek-v4-flash"
    return c


_BODY = "SIGNAL: BUY\nREASONING: cheap\nCONFIDENCE: high"


def test_primary_success_reports_the_primary_model():
    r = _run_with_fallbacks(
        "prompt", _config(), _Client(_BODY), None, None, "AAPL")
    assert r["provider_used"] == "gemini"
    assert r["model_used"] == "gemini-3.1-flash-lite"


def test_fallback_success_reports_the_FALLBACK_model_not_the_primary():
    """The mutation this kills: reporting config.analyst_model unconditionally.
    Both tiers are provider 'gemini', so only the model distinguishes them."""
    r = _run_with_fallbacks(
        "prompt", _config(), _Client(boom=True), _Client(_BODY), None, "AAPL")
    assert r["provider_used"] == "gemini"
    assert r["model_used"] == "gemini-3.7-flash"


def test_fallback2_success_reports_the_fallback2_model():
    r = _run_with_fallbacks(
        "prompt", _config(), _Client(boom=True), _Client(boom=True),
        _Client(_BODY), "AAPL")
    assert r["provider_used"] == "deepseek"
    assert r["model_used"] == "deepseek-v4-flash"


def test_raw_response_and_prompt_hash_are_returned():
    r = _run_with_fallbacks(
        "the-prompt", _config(), _Client(_BODY), None, None, "AAPL")
    assert r["raw_response"] == _BODY
    assert len(r["prompt_sha256"]) == 64


def test_prompt_hash_is_stable_and_differs_per_prompt():
    a = _run_with_fallbacks("p1", _config(), _Client(_BODY), None, None, "A")
    b = _run_with_fallbacks("p1", _config(), _Client(_BODY), None, None, "A")
    c = _run_with_fallbacks("p2", _config(), _Client(_BODY), None, None, "A")
    assert a["prompt_sha256"] == b["prompt_sha256"]
    assert a["prompt_sha256"] != c["prompt_sha256"]


def test_existing_keys_are_unchanged():
    r = _run_with_fallbacks(
        "prompt", _config(), _Client(_BODY), None, None, "AAPL")
    assert r["signal"] == "BUY"
    assert r["confidence"] == "high"
    assert "reasoning" in r
