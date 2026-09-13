"""The fundamental gate values a stock on FORWARD P/E, not trailing.

Trailing P/E misleads around one-off events: on the 2026-08-22 scan HON read
8.3 trailing (a one-time gain) against 21.6 forward, and STX 61.4 trailing
against 15.4 forward (earnings recovering).

Swapping the field name is NOT this change, and would make the gate less safe.
Yahoo omits `trailingPE` when earnings are negative, so a loss-maker used to be
rejected as `pe_missing` by accident. `forwardPE` does not disappear -- it goes
NEGATIVE when analysts expect a loss, and a negative number passes
`pe > max`. The non-positive rule is what keeps the old protection.
"""
import discord
import pytest

from analyst.claude_analyst import build_prompt
from config import Config
from discord_bot.embeds import build_recommendation_embed
from screener.fundamentals import evaluate_fundamentals


def _config(**over):
    c = Config()
    c.max_forward_pe = 35.0
    c.min_dividend_yield = 0.02
    c.min_earnings_growth = 0.05
    for k, v in over.items():
        setattr(c, k, v)
    return c


_PASSING = {"forwardPE": 20.0, "dividendYield": 3.0, "earningsGrowth": 0.10}


# ─── The gate ────────────────────────────────────────────────────────────────

def test_a_negative_forward_pe_is_rejected_not_passed():
    """The one a plain field rename gets wrong: -12 is not above 35."""
    v = evaluate_fundamentals({**_PASSING, "forwardPE": -12.0}, _config())
    assert v.passed is False
    assert v.failed_on == "forward_pe_non_positive"


def test_a_zero_forward_pe_is_rejected():
    assert evaluate_fundamentals({**_PASSING, "forwardPE": 0.0}, _config()).failed_on == "forward_pe_non_positive"


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "Infinity", True])
def test_an_unusable_forward_pe_is_missing(bad):
    """NaN compares False to every threshold and would pass; Yahoo has served
    the STRING 'Infinity' in numeric fields; True is an int to isinstance."""
    info = {**_PASSING, "forwardPE": bad}
    assert evaluate_fundamentals(info, _config()).failed_on == "forward_pe_missing"


def test_an_absent_forward_pe_is_missing():
    info = {k: v for k, v in _PASSING.items() if k != "forwardPE"}
    assert evaluate_fundamentals(info, _config()).failed_on == "forward_pe_missing"


def test_the_maximum_is_inclusive():
    assert evaluate_fundamentals({**_PASSING, "forwardPE": 35.0}, _config()).passed is True
    assert evaluate_fundamentals({**_PASSING, "forwardPE": 35.01}, _config()).failed_on == "forward_pe_above_max"


def test_trailing_pe_no_longer_decides_anything():
    # STX-shaped: expensive on trailing earnings, cheap on forward -> passes.
    stx = {**_PASSING, "trailingPE": 61.37, "forwardPE": 15.35}
    assert evaluate_fundamentals(stx, _config()).passed is True
    # HON-shaped: cheap on trailing only because of a one-off gain -> judged on forward.
    hon = {**_PASSING, "trailingPE": 8.30, "forwardPE": 21.60}
    assert evaluate_fundamentals(hon, _config(max_forward_pe=20.0)).failed_on == "forward_pe_above_max"
    # And a trailing P/E alone no longer gets a candidate through.
    assert evaluate_fundamentals({"trailingPE": 15.0}, _config()).failed_on == "forward_pe_missing"


def test_the_threshold_is_recorded_under_its_new_name():
    """Rows from before the change carry `max_pe_ratio`, rows after carry
    `max_forward_pe`, so gate_config_json tells the two rules apart by itself."""
    v = evaluate_fundamentals(_PASSING, _config(max_forward_pe=18.0))
    assert v.thresholds["max_forward_pe"] == 18.0
    assert "max_pe_ratio" not in v.thresholds


# ─── Config ──────────────────────────────────────────────────────────────────

@pytest.fixture
def clean_env(monkeypatch):
    for name in ("MAX_FORWARD_PE", "MAX_PE_RATIO", "EXECUTION_MODE", "DRY_RUN", "PAPER_TRADING"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _valid(config):
    config.schwab_app_key = "k"
    config.schwab_app_secret = "s"
    config.discord_token = "t"
    config.analyst_api_key = "a"
    config.discord_channel_id = 1
    config.schwab_account_hash = "h"
    return config


def test_max_forward_pe_defaults_to_35(clean_env):
    assert Config().max_forward_pe == 35.0


def test_max_forward_pe_reads_the_environment(clean_env):
    clean_env.setenv("MAX_FORWARD_PE", "22.5")
    assert Config().max_forward_pe == 22.5


def test_the_old_name_is_gone(clean_env):
    assert not hasattr(Config(), "max_pe_ratio")


def test_a_leftover_max_pe_ratio_fails_startup(clean_env):
    """A ceiling chosen for trailing P/E silently applied to forward P/E is the
    failure. The message names the new variable and carries the old value."""
    clean_env.setenv("MAX_PE_RATIO", "30.0")
    with pytest.raises(ValueError, match=r"MAX_FORWARD_PE.*30\.0"):
        _valid(Config()).validate()


def test_a_config_without_the_old_name_validates(clean_env):
    _valid(Config()).validate()


# ─── Dividend floor: off by default ─────────────────────────────────────────
# With a floor, a token payer (META 0.38%) was rejected while a stock paying
# nothing (APP) skipped the check and passed -- it selected against small
# dividends, not for income. Off by default, not deleted: an income strategy
# can still set one.

def test_the_dividend_floor_defaults_to_off(clean_env):
    clean_env.delenv("MIN_DIVIDEND_YIELD", raising=False)
    assert Config().min_dividend_yield == 0.0


def test_a_token_dividend_passes_with_the_floor_off():
    meta = {"forwardPE": 15.85, "dividendYield": 0.38, "earningsGrowth": 0.10}
    v = evaluate_fundamentals(meta, _config(min_dividend_yield=0.0))
    assert v.passed is True
    assert v.thresholds["min_dividend_yield"] == 0.0  # the switch is visible per row


def test_a_floor_can_still_be_set():
    meta = {"forwardPE": 15.85, "dividendYield": 0.38, "earningsGrowth": 0.10}
    assert evaluate_fundamentals(meta, _config(min_dividend_yield=0.02)).failed_on == "yield_below_min"


# ─── Display ─────────────────────────────────────────────────────────────────

def _fields(embed: discord.Embed) -> dict:
    return {f.name: f.value for f in embed.fields}


def test_embed_shows_forward_pe_and_peg():
    embed = build_recommendation_embed("STX", "BUY", "r", 120.0, 0.0035, 15.36, peg_ratio=0.48)
    fields = _fields(embed)
    assert fields["Fwd P/E"] == "15.4"
    assert fields["PEG"] == "0.48"
    assert "P/E Ratio" not in fields


def test_embed_shows_na_for_missing_forward_pe_and_peg():
    fields = _fields(build_recommendation_embed("X", "BUY", "r", 1.0, None, None))
    assert fields["Fwd P/E"] == "N/A"
    assert fields["PEG"] == "N/A"


def test_prompt_carries_forward_pe_and_peg_beside_trailing():
    prompt = build_prompt("STX", {"trailingPE": 61.37, "forwardPE": 15.35, "pegRatio": 0.48}, [])
    assert "Trailing P/E: 61.37" in prompt
    assert "Forward P/E: 15.35" in prompt
    assert "PEG: 0.48" in prompt


def test_prompt_shows_na_when_forward_pe_and_peg_absent():
    prompt = build_prompt("X", {}, [])
    assert "Forward P/E: N/A" in prompt
    assert "PEG: N/A" in prompt
