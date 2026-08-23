"""What gate rejected a candidate, and what that gate was set to at the time.

`rejected_fundamental` names a cohort, and a cohort is only a cohort if its
members were judged by the same rule. Two things were missing:

  WHICH THRESHOLDS APPLIED. Config lives in `.env` and never reached the
  database, so a threshold changed mid-sample silently re-defines the cohort
  and nothing in the row says so.

  WHICH CRITERION FAILED. `reject_reason` existed and was NULL on every row.
  It is NOT redundant with recording the thresholds: recomputing the reason
  from the stored `.info` only reproduces it while the filter's LOGIC is
  unchanged, not merely its parameters.

Note what is NOT the problem. `fundamentals_json` already stores the whole
`.info` dict and `technicals_json` the whole indicator set, so "would this have
passed threshold set X" is already recomputable for any X. What no record can
undo is the PIPELINE BRANCH: a candidate rejected here never got an analyst
call, so re-grading it later still yields no signal.
"""
import pytest

from config import Config
from screener.fundamentals import evaluate_fundamentals
from screener.technicals import evaluate_technicals


def _config(**over):
    c = Config()
    c.max_pe_ratio = 25.0
    # a FRACTION -- `.info` reports dividendYield in percentage points and
    # `normalize_dividend_yield` divides by 100 before the comparison
    c.min_dividend_yield = 0.02
    c.min_earnings_growth = 0.05
    c.max_rsi = 70.0
    c.min_volume_ratio = 1.0
    for k, v in over.items():
        setattr(c, k, v)
    return c


_PASSING = {"trailingPE": 15.0, "dividendYield": 3.0, "earningsGrowth": 0.10}


# --- the fundamental gate ---

def test_a_passing_candidate_names_no_failing_criterion():
    v = evaluate_fundamentals(_PASSING, _config())
    assert v.passed is True
    assert v.failed_on is None


def test_a_missing_pe_is_reported_as_its_own_criterion():
    """Absent is not the same rejection as too-high, and the funnel exists to
    tell those apart: one is a data gap, the other is a judgement."""
    v = evaluate_fundamentals({**_PASSING, "trailingPE": None}, _config())
    assert v.passed is False
    assert v.failed_on == "pe_missing"


def test_a_pe_above_the_maximum_is_named():
    v = evaluate_fundamentals({**_PASSING, "trailingPE": 99.0}, _config())
    assert v.failed_on == "pe_above_max"


def test_a_yield_below_the_minimum_is_named():
    v = evaluate_fundamentals({**_PASSING, "dividendYield": 0.5}, _config())
    assert v.failed_on == "yield_below_min"


def test_growth_below_the_minimum_is_named():
    v = evaluate_fundamentals({**_PASSING, "earningsGrowth": -0.5}, _config())
    assert v.failed_on == "growth_below_min"


def test_the_first_failing_criterion_wins():
    """The gate short-circuits, so a candidate failing several criteria is
    recorded against the FIRST. Reporting a later one would misattribute the
    rejection, and reporting all of them would imply the gate evaluated all of
    them -- it did not."""
    v = evaluate_fundamentals(
        {"trailingPE": 99.0, "dividendYield": 0.1, "earningsGrowth": -0.9},
        _config())
    assert v.failed_on == "pe_above_max"


def test_the_thresholds_actually_applied_are_carried():
    """The whole point. Config never reached the database, so a threshold moved
    mid-sample silently redefined the cohort."""
    v = evaluate_fundamentals(_PASSING, _config(max_pe_ratio=12.0))
    assert v.thresholds["max_pe_ratio"] == 12.0
    assert v.thresholds["min_dividend_yield"] == 0.02
    assert v.thresholds["min_earnings_growth"] == 0.05


def test_thresholds_are_carried_on_a_pass_as_well_as_a_reject():
    """A cohort comparison needs the gate for BOTH sides. Recording it only on
    rejects would make gate provenance correlate with outcome -- the same
    selection effect the screen-price fix removed from `reference_price`."""
    assert evaluate_fundamentals(_PASSING, _config()).thresholds != {}


def test_an_absent_optional_field_does_not_reject():
    """Missing-data policy is unchanged: yield and growth are optional."""
    v = evaluate_fundamentals({"trailingPE": 15.0}, _config())
    assert v.passed is True
    assert v.failed_on is None


# --- the technical gate ---

_TECH_PASSING = {"rsi": 50.0, "price": 100.0, "ma50": 90.0,
                 "volume": 2_000_000, "avg_volume": 1_000_000}


def test_a_passing_technical_candidate_names_no_criterion():
    v = evaluate_technicals(_TECH_PASSING, _config())
    assert v.passed is True
    assert v.failed_on is None


def test_missing_indicator_data_is_its_own_criterion():
    """A gap in the data is not a judgement about the stock. Collapsing the two
    into one `rejected_technical` bucket would let a yfinance outage look like
    a cohort of genuinely weak candidates."""
    v = evaluate_technicals({**_TECH_PASSING, "ma50": None}, _config())
    assert v.passed is False
    assert v.failed_on == "data_missing"


def test_an_rsi_above_the_maximum_is_named():
    v = evaluate_technicals({**_TECH_PASSING, "rsi": 85.0}, _config())
    assert v.failed_on == "rsi_above_max"


def test_a_price_below_the_moving_average_is_named():
    v = evaluate_technicals({**_TECH_PASSING, "price": 80.0}, _config())
    assert v.failed_on == "price_below_ma50"


def test_volume_below_the_required_ratio_is_named():
    v = evaluate_technicals({**_TECH_PASSING, "volume": 100}, _config())
    assert v.failed_on == "volume_below_min_ratio"


def test_the_technical_gate_carries_its_thresholds():
    v = evaluate_technicals(_TECH_PASSING, _config(max_rsi=55.0))
    assert v.thresholds["max_rsi"] == 55.0
    assert v.thresholds["min_volume_ratio"] == 1.0


def test_the_first_failing_technical_criterion_wins():
    v = evaluate_technicals(
        {"rsi": 99.0, "price": 1.0, "ma50": 500.0, "volume": 1, "avg_volume": 10**9},
        _config())
    assert v.failed_on == "rsi_above_max"


def test_the_price_versus_ma50_rule_has_no_configurable_threshold():
    """Recorded here because it is a real asymmetry: `price >= ma50` is
    structural, so `thresholds` cannot describe it. That rule can still CHANGE
    -- in the code rather than in config -- which is exactly why `failed_on` is
    recorded independently of the threshold set."""
    assert "ma50" not in " ".join(evaluate_technicals(
        _TECH_PASSING, _config()).thresholds)


# --- recording the verdict on the observation ---

import sqlite3  # noqa: E402

from database.models import initialize_db  # noqa: E402
from research import shadow_log  # noqa: E402
from screener.fundamentals import Verdict  # noqa: E402


def _db(tmp_path):
    cfg = Config()
    cfg.db_path = str(tmp_path / "s.db")
    initialize_db(cfg.db_path)
    return cfg


def _row(cfg):
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM shadow_observations").fetchone()


def test_a_rejected_observation_records_the_gate_and_the_criterion(tmp_path):
    cfg = _db(tmp_path)
    verdict = evaluate_fundamentals({**_PASSING, "trailingPE": 99.0}, _config())

    shadow_log.observe(cfg, "AAPL", "stock", "fundamental",
                       "rejected_fundamental", gates=(verdict,))

    row = _row(cfg)
    assert row["reject_reason"] == "pe_above_max"
    import json
    assert json.loads(row["gate_config_json"])["max_pe_ratio"] == 25.0


def test_a_passing_observation_still_records_the_gate(tmp_path):
    """Gate provenance on rejects only would make it correlate with outcome --
    the selection effect the screen-price fix removed from `reference_price`.
    A cohort comparison needs the gate for both sides."""
    cfg = _db(tmp_path)

    shadow_log.observe(cfg, "AAPL", "stock", "technical", "recommended",
                       gates=(evaluate_technicals(_TECH_PASSING, _config()),))

    row = _row(cfg)
    assert row["reject_reason"] is None
    import json
    assert json.loads(row["gate_config_json"])["max_rsi"] == 70.0


def test_an_observation_with_no_gate_records_none(tmp_path):
    """Not every exit passes through a gate -- `error` and the ETF path do not.
    NULL says 'no gate was applied'; '{}' would say 'a gate with no settings'."""
    cfg = _db(tmp_path)

    shadow_log.observe(cfg, "SPY", "etf", "fundamental", "recommended")

    row = _row(cfg)
    assert row["gate_config_json"] is None
    assert row["reject_reason"] is None


def test_an_explicit_reject_reason_is_not_overwritten_by_the_gate(tmp_path):
    """Some rejections come from outside a gate (a quota exhaustion, an analyst
    SKIP). A caller that names its own reason keeps it."""
    cfg = _db(tmp_path)

    # The gate MUST carry a failed_on, or this test passes whether the rule
    # exists or not -- caught by mutation: replacing the guard with an
    # unconditional overwrite killed nothing.
    shadow_log.observe(cfg, "AAPL", "stock", "analyst", "rejected_signal",
                       reject_reason="analyst_skip",
                       gates=(Verdict(False, "pe_above_max", {"max_pe_ratio": 25.0}),))

    assert _row(cfg)["reject_reason"] == "analyst_skip"


def test_every_gate_applied_is_recorded_not_only_the_deciding_one(tmp_path):
    """A candidate that reached the analyst was LET THROUGH by the fundamental
    gate. Recording only the technical gate would make "did a fundamental
    threshold change alter who reached the analyst?" unanswerable -- and that
    is one of the questions this column exists to answer."""
    cfg = _db(tmp_path)
    fundamental = evaluate_fundamentals(_PASSING, _config())
    technical = evaluate_technicals({**_TECH_PASSING, "rsi": 85.0}, _config())

    shadow_log.observe(cfg, "AAPL", "stock", "technical", "rejected_technical",
                       gates=(fundamental, technical))

    row = _row(cfg)
    import json
    gate = json.loads(row["gate_config_json"])
    assert gate["max_pe_ratio"] == 25.0, "the gate it PASSED must be recorded too"
    assert gate["max_rsi"] == 70.0
    assert row["reject_reason"] == "rsi_above_max"
