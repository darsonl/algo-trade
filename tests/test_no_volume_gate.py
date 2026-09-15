"""The technical gate has no volume criterion.

It compared `hist["Volume"].iloc[-1]` against a 20-bar average. During market
hours the last bar is TODAY'S, still forming, so at the 09:45 ET scan every
stock read ~0.09x its average against a 0.5x minimum: on 2026-09-14 0 of 42
candidates could pass, whatever their price or RSI, and the stock path could
never post a recommendation.

It was not fixed but removed, because on COMPLETE bars it did nothing worth
keeping. Four years of the current S&P 500 (2022-09..2026-09): it blocked 3.55%
of RSI+MA50 passers, a quarter of those on ten half-day/holiday sessions; the
blocked candidates did not do worse afterwards (21d excess vs passers +0.75pp,
se ~1.34); and 95% of them still traded $30M+ that day against a $500 order.
The "use the last completed bar" fix would have kept the holiday artifact and
moved it one session later.
"""
import pytest

from config import Config
from main import should_recommend
from screener.technicals import evaluate_technicals, passes_technical_filter

# The shape the 2026-09-14 09:45 ET scan actually saw: 15 minutes of volume
# against a 20-day average of full sessions (median ratio 0.087).
_OPENING_BAR = {"rsi": 55.0, "price": 110.0, "ma50": 100.0,
                "volume": 87_000, "avg_volume": 1_000_000}


def _config():
    c = Config()
    c.max_rsi = 70.0
    return c


def test_a_partial_opening_bar_does_not_reject_a_candidate():
    """Fails if any volume comparison is still in `evaluate_technicals`."""
    v = evaluate_technicals(_OPENING_BAR, _config())
    assert v.passed is True
    assert v.failed_on is None


def test_a_buy_on_the_opening_scan_is_recommended():
    """The regression that mattered: a BUY passing RSI and MA50 must reach
    Discord. Fails if `should_recommend` still routes through a volume rule."""
    assert should_recommend("BUY", dict(_OPENING_BAR), _config()) is True


def test_no_volume_at_all_is_not_missing_data():
    """Fails if `volume`/`avg_volume` remain in the `data_missing` check -- the
    removed criterion would survive as a side effect, rejecting a candidate
    whose volume history happens to be unreadable."""
    data = {"rsi": 55.0, "price": 110.0, "ma50": 100.0,
            "volume": None, "avg_volume": None}
    assert passes_technical_filter(data, _config()) is True


def test_the_gate_no_longer_records_a_volume_threshold():
    """`gate_config_json` is how a cohort is split on its rules. Fails if
    `min_volume_ratio` is still carried, which would claim rows after the
    boundary were judged by a rule that no longer ran."""
    assert "min_volume_ratio" not in evaluate_technicals(_OPENING_BAR, _config()).thresholds


def test_the_remaining_criteria_still_reject():
    """Removing one criterion must not loosen the other two."""
    assert evaluate_technicals({**_OPENING_BAR, "rsi": 85.0}, _config()).failed_on == "rsi_above_max"
    assert evaluate_technicals({**_OPENING_BAR, "price": 90.0}, _config()).failed_on == "price_below_ma50"
    assert evaluate_technicals({**_OPENING_BAR, "ma50": None}, _config()).failed_on == "data_missing"


def test_config_has_no_volume_ratio_field():
    """Fails if the dead setting is still a `Config` field someone could tune."""
    assert not hasattr(Config(), "min_volume_ratio")


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("MIN_VOLUME_RATIO", "MAX_PE_RATIO", "EXECUTION_MODE", "DRY_RUN", "PAPER_TRADING"):
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


def test_a_leftover_min_volume_ratio_fails_startup(clean_env):
    """Same precedent as MAX_PE_RATIO and DRY_RUN: a setting that silently does
    nothing reads as a safety or selection rule that is still in force. Fails if
    `validate()` ignores it."""
    clean_env.setenv("MIN_VOLUME_RATIO", "0.5")
    with pytest.raises(ValueError, match=r"MIN_VOLUME_RATIO.*0\.5"):
        _valid(Config()).validate()
