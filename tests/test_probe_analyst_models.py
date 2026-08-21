"""The probe tool must not reproduce the traps it exists to prevent.

`scripts/probe_analyst_models.py` was written to answer "does this model's
output parse", after two models were chosen on assumption and were wrong. It
had two defects that made it misreport in exactly the ways it was built to
catch:

* it slept a fixed 1.5s between calls -- ~40 RPM against a chain whose models
  are capped at 5-15 RPM. The handoff's own instruction was "pace at >= 4s",
  which the script offered no way to honour. Over-fast probing produces 503s
  that read as model instability; that misreading has already happened twice.
* it hardcoded the Gemini base URL, so probing the deepseek tier sent a
  deepseek model id to Google and reported the resulting error as a failure
  OF THE MODEL.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "probe_analyst_models",
    Path(__file__).resolve().parent.parent / "scripts" / "probe_analyst_models.py",
)
probe_mod = importlib.util.module_from_spec(_SPEC)
# Registered BEFORE exec: @dataclass resolves its string annotations (the module
# uses `from __future__ import annotations`) through sys.modules[cls.__module__],
# which is None for a module that was exec'd without being registered.
sys.modules["probe_analyst_models"] = probe_mod
_SPEC.loader.exec_module(probe_mod)

from config import Config  # noqa: E402


def _config() -> Config:
    c = Config()
    c.analyst_provider = "gemini"
    c.analyst_model = "gemini-3.1-flash-lite"
    c.analyst_api_key = "k1"
    c.analyst_fallback_provider = "gemini"
    c.analyst_fallback_model = "gemini-3.7-flash"
    c.analyst_fallback_api_key = "k2"
    c.analyst_fallback2_provider = "deepseek"
    c.analyst_fallback2_model = "deepseek-v4-flash"
    c.analyst_fallback2_api_key = "k3"
    c.analyst_call_delay_s = 4.0
    return c


# --- tiers carry their own provider, not the primary's -----------------------

def test_default_tiers_are_the_whole_configured_chain():
    """All THREE tiers, not just primary+fallback. fallback2 is part of the
    chain `_run_with_fallbacks` walks, so a probe that omits it leaves the tier
    that actually catches the other two unmeasured."""
    tiers = probe_mod.resolve_tiers(_config(), [])
    assert [(t.provider, t.model) for t in tiers] == [
        ("gemini", "gemini-3.1-flash-lite"),
        ("gemini", "gemini-3.7-flash"),
        ("deepseek", "deepseek-v4-flash"),
    ]


def test_deepseek_tier_is_routed_to_deepseek_not_google():
    """The bug this replaces: a deepseek model id sent to the Gemini endpoint
    fails, and the failure is reported as if the MODEL could not answer."""
    tiers = probe_mod.resolve_tiers(_config(), [])
    ds = [t for t in tiers if t.provider == "deepseek"][0]
    assert "deepseek.com" in str(ds.client.base_url)
    assert "googleapis" not in str(ds.client.base_url)


def test_explicit_provider_qualified_model_is_honoured():
    tiers = probe_mod.resolve_tiers(_config(), ["deepseek:deepseek-chat"])
    assert len(tiers) == 1
    assert tiers[0].provider == "deepseek"
    assert tiers[0].model == "deepseek-chat"
    assert "deepseek.com" in str(tiers[0].client.base_url)


def test_bare_model_defaults_to_the_primary_provider():
    tiers = probe_mod.resolve_tiers(_config(), ["gemini-2.5-flash"])
    assert len(tiers) == 1
    assert tiers[0].provider == "gemini"
    assert tiers[0].model == "gemini-2.5-flash"


# --- pacing: the trap-2 guard ------------------------------------------------

def test_delay_defaults_to_the_configured_call_delay():
    """Not a hardcoded constant. The scan's own throttle is the rate this
    account is calibrated to; the probe must not exceed it."""
    assert probe_mod.resolve_delay(_config(), None) == 4.0


def test_delay_below_the_configured_throttle_is_refused():
    """The whole defect: 1.5s against a 15 RPM cap is 40 RPM. An operator who
    asks for a faster probe is asking to reproduce the misreading."""
    with pytest.raises(SystemExit):
        probe_mod.resolve_delay(_config(), 1.5)


def test_delay_above_the_configured_throttle_is_allowed():
    assert probe_mod.resolve_delay(_config(), 10.0) == 10.0


def test_probe_sleeps_the_delay_between_every_call():
    """Pinned on the real loop, not on an argument being stored: the pacing has
    to happen between calls or it does not pace anything."""
    slept = []
    calls = []

    class _Resp:
        class _C:
            class _M:
                content = "SIGNAL: HOLD\nCONFIDENCE: high\nREASONING: x"
            message = _M()
        choices = [_C()]

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    calls.append(kw["model"])
                    return _Resp()

    probe_mod.probe(_Client(), "m", repeat=1, delay=4.0, sleep=slept.append)
    assert len(calls) == 3, "3 cases"
    assert slept == [4.0, 4.0, 4.0]
