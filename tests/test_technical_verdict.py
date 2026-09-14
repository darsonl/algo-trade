"""The technical gate's verdict, recorded on EVERY stock candidate that reached it.

The question this serves: "would the analyst be missed?" Answering it needs the
counterfactual of a pipeline WITHOUT the analyst -- and that is every candidate
that passed the fundamental and technical gates, whatever the analyst said.

`reject_reason` cannot carry that. On a `rejected_signal` the analyst refused,
and naming a technical criterion there would misattribute the rejection, so
the scan deliberately clears it. That was right for attribution and it erased
the counterfactual: the raw indicators survived in `technicals_json`, but
re-deriving the verdict from them only works while the gate's LOGIC is
unchanged -- the same argument that put `failed_on` beside `thresholds`.

So the verdict gets its own column, independent of who rejected the row.
"""
import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main
from config import Config
from database.models import initialize_db
from research import shadow_log
from screener.fundamentals import Verdict

_PASSING_TECH = {"price": 110.0, "ma50": 100.0, "rsi": 50.0,
                 "volume": 2_000_000, "avg_volume": 1_000_000}
_HOT_TECH = {**_PASSING_TECH, "rsi": 85.0}


# --- the label (pure) ---

def _build(**kw):
    return shadow_log.build_observation(
        "AAPL", "stock", "technical", "rejected_signal",
        session_date="2026-09-14", observed_at="2026-09-14T13:45:00Z", **kw)


def test_a_passing_gate_is_labelled_passed():
    obs = _build(technical_verdict=Verdict(True, None, {"max_rsi": 70.0}))
    assert obs.technical_verdict == "passed"


def test_a_failing_gate_is_labelled_with_its_criterion():
    obs = _build(technical_verdict=Verdict(False, "rsi_above_max", {"max_rsi": 70.0}))
    assert obs.technical_verdict == "rsi_above_max"


def test_no_gate_is_null_not_passed():
    """NULL means the gate never ran. 'passed' would claim a verdict nobody made."""
    assert _build().technical_verdict is None


def test_the_verdict_does_not_become_the_reject_reason():
    """The analyst refused this row. The verdict is the counterfactual, not the
    cause -- if it leaked into reject_reason, every analyst HOLD on a hot stock
    would be relabelled a technical rejection."""
    obs = _build(technical_verdict=Verdict(False, "rsi_above_max", {"max_rsi": 70.0}))
    assert obs.reject_reason is None


# --- schema ---

_LEGACY = """
CREATE TABLE shadow_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_date TEXT NOT NULL,
    observed_at TEXT NOT NULL, ticker TEXT NOT NULL, scan_kind TEXT NOT NULL,
    stage_reached TEXT NOT NULL, outcome TEXT NOT NULL, reject_reason TEXT,
    fundamentals_json TEXT, technicals_json TEXT, headlines_json TEXT,
    macro_json TEXT, analyst_provider TEXT, analyst_model TEXT,
    analyst_signal TEXT, analyst_confidence TEXT, analyst_prompt_sha256 TEXT,
    analyst_raw_response TEXT, cache_hit INTEGER NOT NULL DEFAULT 0,
    recommendation_id INTEGER, reference_price REAL,
    reference_price_source TEXT, gate_config_json TEXT,
    human_action TEXT, human_action_at TEXT
);
"""


def test_the_column_is_added_to_an_existing_table_and_old_rows_stay_null(tmp_path):
    """NULL on old rows, never a backfilled verdict: re-deriving one from
    technicals_json would silently apply TODAY's gate logic to a past row."""
    db = str(tmp_path / "legacy.db")
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(_LEGACY)
        conn.execute(
            """INSERT INTO shadow_observations (session_date, observed_at, ticker,
                   scan_kind, stage_reached, outcome, technicals_json)
               VALUES ('2026-08-22', '2026-08-22T13:45:00Z', 'ADBE', 'stock',
                       'technical', 'rejected_signal', '{"rsi": 50.0}')""")
        conn.commit()

    initialize_db(db)

    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM shadow_observations").fetchone()
    assert row["technical_verdict"] is None
    assert row["technicals_json"] == '{"rsi": 50.0}'


# --- the scan records it at every stock exit past the fundamental gate ---

def _config(tmp_path):
    c = Config()
    c.db_path = str(tmp_path / "s.db")
    c.dry_run = True
    c.max_rsi = 70.0
    initialize_db(c.db_path)
    return c


def _patches(*, analysis, tech=_PASSING_TECH, tech_error=None, analyzer=None):
    analyzer = analyzer or AsyncMock(return_value=analysis)
    tech_kw = ({"side_effect": tech_error} if tech_error
               else {"return_value": dict(tech)})
    return (
        patch.object(main, "get_top_sp500_by_fundamentals", return_value=[]),
        patch.object(main, "get_universe", return_value=["AAPL"]),
        patch.object(main, "partition_watchlist",
                     side_effect=lambda t, i=None: (["AAPL"], [])),
        patch.object(main, "fetch_macro_context", return_value={}),
        patch.object(main, "alert_stuck_orders", new=AsyncMock()),
        patch.object(main, "sweep_terminal_recommendations", new=AsyncMock()),
        patch.object(main, "_drain_ops_outbox", new=AsyncMock()),
        patch.object(main.outcomes, "mark_due_outcomes", new=AsyncMock(return_value=0)),
        patch.object(main, "fetch_fundamental_info",
                     return_value={"trailingPE": 20.0, "currentPrice": 195.9}),
        patch.object(main, "evaluate_fundamentals",
                     return_value=Verdict(True, None, {"max_forward_pe": 35.0})),
        patch.object(main, "fetch_news_headlines", return_value=["a headline"]),
        patch.object(main, "analyze_with_cache", new=analyzer),
        patch.object(main, "fetch_technical_data", **tech_kw),
    )


async def _run(patches, cfg):
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    bot.send_recommendation = AsyncMock(return_value=123)
    for p in patches:
        p.start()
    try:
        await main.run_scan(bot, cfg)
    finally:
        for p in patches:
            p.stop()


def _row(cfg):
    with closing(sqlite3.connect(cfg.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute(
            "SELECT outcome, reject_reason, technical_verdict, technicals_json"
            " FROM shadow_observations WHERE ticker='AAPL'").fetchone())


def _analysis(signal):
    return {"signal": signal, "reasoning": "r", "confidence": "medium"}


@pytest.mark.asyncio
async def test_an_analyst_hold_on_a_technically_clean_stock_records_passed(tmp_path):
    """The row the whole column exists for: a stock a no-analyst pipeline WOULD
    have posted, and only the analyst stopped."""
    cfg = _config(tmp_path)
    await _run(_patches(analysis=_analysis("HOLD")), cfg)
    row = _row(cfg)
    assert row["outcome"] == "rejected_signal"
    assert row["technical_verdict"] == "passed"
    assert row["reject_reason"] is None


@pytest.mark.asyncio
async def test_an_analyst_hold_on_a_hot_stock_records_the_criterion(tmp_path):
    cfg = _config(tmp_path)
    await _run(_patches(analysis=_analysis("HOLD"), tech=_HOT_TECH), cfg)
    row = _row(cfg)
    assert row["outcome"] == "rejected_signal"
    assert row["technical_verdict"] == "rsi_above_max"
    assert row["reject_reason"] is None, "the analyst refused, not the gate"


@pytest.mark.asyncio
async def test_a_technical_reject_records_the_same_criterion_in_both_columns(tmp_path):
    cfg = _config(tmp_path)
    await _run(_patches(analysis=_analysis("BUY"), tech=_HOT_TECH), cfg)
    row = _row(cfg)
    assert row["outcome"] == "rejected_technical"
    assert row["technical_verdict"] == row["reject_reason"] == "rsi_above_max"


@pytest.mark.asyncio
async def test_a_recommendation_records_passed(tmp_path):
    cfg = _config(tmp_path)
    await _run(_patches(analysis=_analysis("BUY")), cfg)
    assert _row(cfg)["outcome"] == "recommended"
    assert _row(cfg)["technical_verdict"] == "passed"


@pytest.mark.asyncio
async def test_a_quota_exhausted_candidate_carries_technicals_too(tmp_path):
    """Quota exhaustion depends on scan order and budget, not on the stock. If
    only analysed rows carried a verdict, WHETHER a row has one would correlate
    with how it exited -- the selection effect the screen-price rule removed."""
    cfg = _config(tmp_path)
    await _run(_patches(analysis=None, tech=_HOT_TECH), cfg)
    row = _row(cfg)
    assert row["outcome"] == "skipped_quota_exhausted"
    assert row["technical_verdict"] == "rsi_above_max"
    assert row["technicals_json"] is not None
    assert row["reject_reason"] is None, "quota refused it, not the gate"


@pytest.mark.asyncio
async def test_a_technical_fetch_failure_spends_no_analyst_call(tmp_path):
    """Technicals are fetched BEFORE the analyst now. A ticker whose history
    cannot be read ends in `error` either way; it should not cost quota first."""
    cfg = _config(tmp_path)
    analyzer = AsyncMock(return_value=_analysis("BUY"))
    await _run(_patches(analysis=None, analyzer=analyzer,
                        tech_error=RuntimeError("no history")), cfg)
    analyzer.assert_not_awaited()
    assert _row(cfg)["outcome"] == "error"


@pytest.mark.asyncio
async def test_etf_rows_carry_no_verdict(tmp_path):
    """The ETF path applies no technical gate at all. NULL says so; 'passed'
    would claim a gate ran."""
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    bot.send_etf_recommendation = AsyncMock(return_value=123)
    with patch.object(main, "partition_watchlist",
                      side_effect=lambda t, i=None: ([], ["SPY"])), \
         patch.object(main, "fetch_macro_context", return_value={}), \
         patch.object(main, "alert_stuck_orders", new=AsyncMock()), \
         patch.object(main, "sweep_terminal_recommendations", new=AsyncMock()), \
         patch.object(main, "_drain_ops_outbox", new=AsyncMock()), \
         patch.object(main.outcomes, "mark_due_outcomes", new=AsyncMock(return_value=0)), \
         patch.object(main, "fetch_technical_data", return_value=dict(_PASSING_TECH)), \
         patch.object(main, "fetch_fundamental_info", return_value={}), \
         patch.object(main, "fetch_news_headlines", return_value=[]), \
         patch.object(main, "analyze_with_cache",
                      new=AsyncMock(return_value=_analysis("HOLD"))):
        await main.run_scan_etf(bot, cfg)
    with closing(sqlite3.connect(cfg.db_path)) as conn:
        rows = conn.execute(
            "SELECT outcome, technical_verdict FROM shadow_observations"
            " WHERE ticker='SPY'").fetchall()
    assert rows == [("rejected_signal", None)]
