"""Every exit point of both scan loops leaves a shadow row.

Rejections matter more than recommendations here: a conversion rate whose
denominator omits the rejects is not a conversion rate.

The ETF assertions instrument `partition_watchlist`, NOT `get_universe` -- the
ETF path never calls `get_universe`, and a test that patches it passes
vacuously. That mistake has already been made once in this repo.
"""
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main
from config import Config
from database.models import initialize_db


def _config(tmp_path):
    c = Config()
    c.db_path = str(tmp_path / "s.db")
    c.dry_run = True
    initialize_db(c.db_path)
    return c


def _outcomes(db_path):
    conn = sqlite3.connect(db_path)
    return [(r[0], r[1]) for r in conn.execute(
        "SELECT ticker, outcome FROM shadow_observations ORDER BY id")]


@pytest.mark.asyncio
async def test_open_position_skip_is_recorded(tmp_path):
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    with patch.object(main, "get_top_sp500_by_fundamentals", return_value=[]), \
         patch.object(main, "get_universe", return_value=["AAPL"]), \
         patch.object(main, "partition_watchlist",
                      side_effect=lambda t, i=None: (["AAPL"], [])), \
         patch.object(main, "fetch_macro_context", return_value={}), \
         patch.object(main, "alert_stuck_orders", new=AsyncMock()), \
         patch.object(main, "sweep_terminal_recommendations", new=AsyncMock()), \
         patch.object(main, "_drain_ops_outbox", new=AsyncMock()), \
         patch.object(main.queries, "has_open_position", return_value=True):
        await main.run_scan(bot, cfg)
    assert ("AAPL", "skipped_open_position") in _outcomes(cfg.db_path)


@pytest.mark.asyncio
async def test_fundamental_rejection_is_recorded(tmp_path):
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    with patch.object(main, "get_top_sp500_by_fundamentals", return_value=[]), \
         patch.object(main, "get_universe", return_value=["XOM"]), \
         patch.object(main, "partition_watchlist",
                      side_effect=lambda t, i=None: (["XOM"], [])), \
         patch.object(main, "fetch_macro_context", return_value={}), \
         patch.object(main, "alert_stuck_orders", new=AsyncMock()), \
         patch.object(main, "sweep_terminal_recommendations", new=AsyncMock()), \
         patch.object(main, "_drain_ops_outbox", new=AsyncMock()), \
         patch.object(main, "fetch_fundamental_info", return_value={"trailingPE": 900.0}), \
         patch.object(main, "passes_fundamental_filter", return_value=False):
        await main.run_scan(bot, cfg)
    assert ("XOM", "rejected_fundamental") in _outcomes(cfg.db_path)


@pytest.mark.asyncio
async def test_the_etf_scan_records_too(tmp_path):
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    with patch.object(main, "partition_watchlist",
                      side_effect=lambda t, i=None: ([], ["SPY"])), \
         patch.object(main, "fetch_macro_context", return_value={}), \
         patch.object(main, "alert_stuck_orders", new=AsyncMock()), \
         patch.object(main, "sweep_terminal_recommendations", new=AsyncMock()), \
         patch.object(main, "_drain_ops_outbox", new=AsyncMock()), \
         patch.object(main.queries, "has_open_position", return_value=True):
        await main.run_scan_etf(bot, cfg)
    rows = _outcomes(cfg.db_path)
    assert ("SPY", "skipped_open_position") in rows


@pytest.mark.asyncio
async def test_a_recorder_failure_does_not_abort_the_scan(tmp_path):
    """The scan's own instrumentation must not be able to end it."""
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    with patch.object(main, "get_top_sp500_by_fundamentals", return_value=[]), \
         patch.object(main, "get_universe", return_value=["AAPL"]), \
         patch.object(main, "partition_watchlist",
                      side_effect=lambda t, i=None: (["AAPL"], [])), \
         patch.object(main, "fetch_macro_context", return_value={}), \
         patch.object(main, "alert_stuck_orders", new=AsyncMock()), \
         patch.object(main, "sweep_terminal_recommendations", new=AsyncMock()), \
         patch.object(main, "_drain_ops_outbox", new=AsyncMock()), \
         patch.object(main.queries, "has_open_position", return_value=True), \
         patch.object(main.shadow_log, "observe", side_effect=RuntimeError("boom")):
        await main.run_scan(bot, cfg)  # must not raise


# --- forward marks run at scan start, on BOTH paths (Task 7) ---
#
# These live here rather than in test_shadow_outcomes.py because the question is
# about the SCAN WIRING, and this file already carries the eight-patch harness
# that drives both loops. test_shadow_outcomes.py covers the marker itself.

def _scan_patches(universe=("AAPL",), etf=()):
    return (
        patch.object(main, "get_top_sp500_by_fundamentals", return_value=[]),
        patch.object(main, "get_universe", return_value=list(universe)),
        patch.object(main, "partition_watchlist",
                     side_effect=lambda t, i=None: (list(universe), list(etf))),
        patch.object(main, "fetch_macro_context", return_value={}),
        patch.object(main, "alert_stuck_orders", new=AsyncMock()),
        patch.object(main, "sweep_terminal_recommendations", new=AsyncMock()),
        patch.object(main, "_drain_ops_outbox", new=AsyncMock()),
        patch.object(main.queries, "has_open_position", return_value=True),
    )


@pytest.mark.asyncio
async def test_the_stock_scan_marks_due_outcomes(tmp_path):
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    marker = AsyncMock(return_value=0)
    patches = _scan_patches() + (
        patch.object(main.outcomes, "mark_due_outcomes", new=marker),)
    for p in patches:
        p.start()
    try:
        await main.run_scan(bot, cfg)
    finally:
        for p in patches:
            p.stop()
    marker.assert_awaited()


@pytest.mark.asyncio
async def test_the_etf_scan_marks_due_outcomes_too(tmp_path):
    """A maintenance step wired into one scan path and not the other is the bug
    that left the ETF path never sweeping terminal orders. Not repeated here."""
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    marker = AsyncMock(return_value=0)
    patches = _scan_patches(universe=(), etf=("SPY",)) + (
        patch.object(main.outcomes, "mark_due_outcomes", new=marker),)
    for p in patches:
        p.start()
    try:
        await main.run_scan_etf(bot, cfg)
    finally:
        for p in patches:
            p.stop()
    marker.assert_awaited()


@pytest.mark.asyncio
async def test_a_marking_failure_does_not_abort_the_scan(tmp_path):
    """`mark_due_outcomes` promises never to raise; the scan does not rely on
    that promise alone, for the same reason `_record_shadow` wraps `observe`."""
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    patches = _scan_patches() + (
        patch.object(main.outcomes, "mark_due_outcomes",
                     new=AsyncMock(side_effect=RuntimeError("boom"))),)
    for p in patches:
        p.start()
    try:
        await main.run_scan(bot, cfg)  # must not raise
    finally:
        for p in patches:
            p.stop()


# --- rejected_signal vs rejected_technical (final-review fix wave) ---
#
# Two different refusals share one `should_recommend` bool, and the funnel pulls
# them apart with a re-check. No test asserted that split -- a gap in the plan's
# own test list, not an implementer deviation. It is the funnel's most
# attribution-sensitive pair: getting the two backwards would be invisible in
# the suite and would silently misattribute every rejection between the analyst
# and the technical gate.

def _reaches_the_technical_gate(signal):
    """Patches that carry one ticker from the universe to `should_recommend`."""
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
        patch.object(main, "fetch_fundamental_info", return_value={"trailingPE": 20.0}),
        patch.object(main, "passes_fundamental_filter", return_value=True),
        patch.object(main, "fetch_news_headlines", return_value=["a headline"]),
        patch.object(main, "analyze_with_cache",
                     new=AsyncMock(return_value={"signal": signal,
                                                 "reasoning": "r",
                                                 "confidence": "high"})),
        patch.object(main, "fetch_technical_data", return_value={"price": 100.0,
                                                                "rsi": 50.0}),
        # The refusal itself is forced; what is under test is how it is ATTRIBUTED.
        patch.object(main, "should_recommend", return_value=False),
    )


async def _scan_with(patches, bot, cfg):
    for p in patches:
        p.start()
    try:
        await main.run_scan(bot, cfg)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_a_non_buy_signal_is_attributed_to_the_analyst(tmp_path):
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    await _scan_with(_reaches_the_technical_gate("HOLD"), bot, cfg)
    assert ("AAPL", "rejected_signal") in _outcomes(cfg.db_path)


@pytest.mark.asyncio
async def test_a_buy_that_fails_the_technicals_is_attributed_to_the_technicals(tmp_path):
    """Same refusal, same code path, opposite attribution. The ONLY difference
    is the analyst's signal, which is exactly what the funnel needs to separate."""
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    await _scan_with(_reaches_the_technical_gate("BUY"), bot, cfg)
    assert ("AAPL", "rejected_technical") in _outcomes(cfg.db_path)
