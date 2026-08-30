"""Total-return marks: both legs of a return on ONE consistent price basis.

`reference_price` is a raw live quote frozen at capture; every close Yahoo
serves is on the basis as of FETCH TIME. The two ends of a return therefore sit
on different bases, and two corporate actions exploit the gap:

  splits    -- Yahoo back-applies them to every close it serves (verified: it
               does so even with auto_adjust=False), while `reference_price`
               predates them. A 10:1 split inside a window reads as -90%.
  dividends -- the benchmark's entry is a PAST date fetched NOW, so it absorbs
               every dividend since, making the benchmark leg a TOTAL return.
               `reference_price` absorbs nothing, making the stock leg a PRICE
               return. The bias is the stock's yield less SPY's -- correlated
               with the dividend-yield screen this system is testing.

The factors are computed over the window (session_date, as_of] rather than read
off fetch-time adjustment, so a delayed mark returns the same number as a
prompt one.
"""
import pandas as pd
import pytest

from research import outcomes


def _frame(rows):
    """A yfinance-shaped daily frame: (date, close, dividend, split) tuples."""
    index = pd.DatetimeIndex([pd.Timestamp(d, tz="America/New_York")
                              for d, _, _, _ in rows])
    return pd.DataFrame(
        {"Close": [c for _, c, _, _ in rows],
         "Dividends": [d for _, _, d, _ in rows],
         "Stock Splits": [s for _, _, _, s in rows]},
        index=index,
    )


_QUIET = _frame([
    ("2026-08-20", 100.0, 0.0, 0.0),
    ("2026-08-21", 101.0, 0.0, 0.0),
    ("2026-08-24", 102.0, 0.0, 0.0),
    ("2026-08-27", 110.0, 0.0, 0.0),
])


# --- split factor ---

def test_a_window_with_no_split_has_a_factor_of_exactly_one():
    """EXACTLY 1.0, not approximately. This factor divides the entry price, so
    a value that is merely close to 1 perturbs every mark in the sample -- the
    common case must be exact, not nearly exact."""
    assert outcomes.split_factor(_QUIET, "2026-08-20", "2026-08-27") == 1.0


def test_a_split_inside_the_window_is_the_split_ratio():
    """The defect this exists to kill. NVDA's 10:1 in June 2024: the entry is a
    pre-split quote near $1224 and every close Yahoo now serves is near $122,
    so an uncorrected return reads -90%."""
    hist = _frame([
        ("2026-08-20", 100.0, 0.0, 0.0),
        ("2026-08-24", 102.0, 0.0, 10.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
    ])
    assert outcomes.split_factor(hist, "2026-08-20", "2026-08-27") == 10.0


def test_two_splits_inside_the_window_compound():
    hist = _frame([
        ("2026-08-20", 100.0, 0.0, 0.0),
        ("2026-08-24", 102.0, 0.0, 4.0),
        ("2026-08-26", 108.0, 0.0, 3.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
    ])
    assert outcomes.split_factor(hist, "2026-08-20", "2026-08-27") == 12.0


def test_a_split_on_the_session_date_itself_is_excluded():
    """The window is half-open at the entry end. A split effective ON the
    session date is ALREADY in the quote we screened at, so correcting for it
    would divide the entry a second time and invent a 10x gain."""
    hist = _frame([
        ("2026-08-20", 100.0, 0.0, 10.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
    ])
    assert outcomes.split_factor(hist, "2026-08-20", "2026-08-27") == 1.0


def test_a_split_on_the_mark_date_itself_is_included():
    """Closed at the exit end: the mark reads that day's close, which is
    already on the post-split basis."""
    hist = _frame([
        ("2026-08-20", 100.0, 0.0, 0.0),
        ("2026-08-27", 110.0, 0.0, 10.0),
    ])
    assert outcomes.split_factor(hist, "2026-08-20", "2026-08-27") == 10.0


def test_a_split_after_the_mark_date_is_excluded():
    """A later fetch sees splits past `as_of`. They belong to no part of this
    window and must not move a mark that has already matured."""
    hist = _frame([
        ("2026-08-20", 100.0, 0.0, 0.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
        ("2026-09-15", 11.5, 0.0, 10.0),
    ])
    assert outcomes.split_factor(hist, "2026-08-20", "2026-08-27") == 1.0


# --- dividend factor ---

def test_a_window_with_no_dividend_has_a_factor_of_exactly_one():
    """Exactly 1.0, for the same reason as the split factor: it multiplies the
    entry basis of EVERY mark, so the common case must not drift."""
    assert outcomes.dividend_factor(_QUIET, "2026-08-20", "2026-08-27") == 1.0


def test_a_dividend_is_priced_against_the_close_before_its_ex_date():
    """Yahoo's own back-adjustment convention: (1 - D / prior close). Pricing
    it against the ex-date's own close instead would use a price the dividend
    has already been subtracted from, understating the factor."""
    hist = _frame([
        ("2026-08-20", 100.0, 0.0, 0.0),
        ("2026-08-24", 100.0, 2.0, 0.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
    ])
    assert outcomes.dividend_factor(
        hist, "2026-08-20", "2026-08-27") == pytest.approx(0.98)


def test_two_dividends_compound_each_against_its_own_prior_close():
    hist = _frame([
        ("2026-08-20", 100.0, 0.0, 0.0),
        ("2026-08-24", 100.0, 2.0, 0.0),
        ("2026-08-25", 200.0, 0.0, 0.0),
        ("2026-08-26", 200.0, 4.0, 0.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
    ])
    assert outcomes.dividend_factor(
        hist, "2026-08-20", "2026-08-27") == pytest.approx(0.98 * 0.98)


def test_a_dividend_on_the_session_date_itself_is_excluded():
    """Half-open at the entry end, like the split factor: the quote we screened
    at already sits on the ex-dividend side of that morning."""
    hist = _frame([
        ("2026-08-19", 100.0, 0.0, 0.0),
        ("2026-08-20", 100.0, 2.0, 0.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
    ])
    assert outcomes.dividend_factor(hist, "2026-08-20", "2026-08-27") == 1.0


def test_a_dividend_on_the_mark_date_itself_is_included():
    hist = _frame([
        ("2026-08-20", 100.0, 0.0, 0.0),
        ("2026-08-26", 100.0, 0.0, 0.0),
        ("2026-08-27", 110.0, 2.0, 0.0),
    ])
    assert outcomes.dividend_factor(
        hist, "2026-08-20", "2026-08-27") == pytest.approx(0.98)


def test_a_dividend_with_no_prior_close_cannot_be_priced():
    """None, never 1.0. Silently skipping an unpriceable dividend returns a
    factor that LOOKS clean and understates the correction -- the same shape as
    booking a zero fill for an order that really filled."""
    hist = _frame([
        ("2026-08-21", 100.0, 2.0, 0.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
    ])
    assert outcomes.dividend_factor(hist, "2026-08-20", "2026-08-27") is None


# --- putting the entry on the exit's basis ---

def test_a_quiet_window_leaves_the_entry_price_untouched():
    """Exactly equal. The overwhelming majority of windows have no corporate
    action, and those marks must be bit-for-bit what they were before."""
    assert outcomes.adjusted_entry(123.45, 1.0, 1.0) == 123.45


def test_a_split_divides_the_entry_price():
    assert outcomes.adjusted_entry(1224.40, 10.0, 1.0) == pytest.approx(122.44)


def test_a_dividend_scales_the_entry_price_down():
    assert outcomes.adjusted_entry(100.0, 1.0, 0.98) == pytest.approx(98.0)


def test_an_unpriceable_dividend_yields_no_adjusted_entry():
    """None propagates. An uncorrectable window must produce no mark at all,
    rather than a mark computed as if the window were quiet."""
    assert outcomes.adjusted_entry(100.0, 1.0, None) is None


def test_a_missing_entry_price_yields_no_adjusted_entry():
    assert outcomes.adjusted_entry(None, 1.0, 1.0) is None
    assert outcomes.adjusted_entry(0.0, 1.0, 1.0) is None


def test_a_zero_split_factor_yields_no_adjusted_entry():
    """Division by zero must not become an exception inside a scan."""
    assert outcomes.adjusted_entry(100.0, 0.0, 1.0) is None


def test_the_correction_reproduces_yfinances_own_adjusted_close():
    """The oracle test, on REAL data: NVDA's 10:1 split of 2024-06-10.

    Fetched 2026-08-23, yfinance reports for 2024-06-05 a raw Close of
    122.440002 and an adjusted Close of 122.228477 -- so the traded price that
    day was 1224.40, and `reference_price` would have captured it as such.

    If the correction is right, the return it produces from the RAW closes must
    equal the return yfinance's own ADJUSTED closes give over the same window.
    Two independent routes to one number; only a correct factor reconciles them.
    """
    entry_traded = 1224.400020          # 2024-06-05, pre-split, as .info saw it
    exit_raw = 129.610001               # 2024-06-13 Close, auto_adjust=False
    split = 10.0                        # 2024-06-10
    dividend = 1.0 - 0.01 / 121.790001  # 2024-06-11 ex-date, prior close 06-10

    ours = outcomes.compute_return(
        outcomes.adjusted_entry(entry_traded, split, dividend), exit_raw)

    oracle = outcomes.compute_return(122.228477, 129.396729)  # yfinance adjusted
    assert ours == pytest.approx(oracle, rel=1e-5)
    assert ours == pytest.approx(5.8646, abs=1e-3)


# --- reading a close out of the window ---

def test_a_close_is_read_at_its_own_date():
    assert outcomes.close_on_or_before(_QUIET, "2026-08-24") == pytest.approx(102.0)


def test_a_non_trading_date_resolves_back_to_the_last_real_bar():
    """Horizons are CALENDAR days, so a mark date lands on a weekend or holiday
    roughly a third of the time. It must resolve backwards to a real close, not
    to nothing."""
    assert outcomes.close_on_or_before(_QUIET, "2026-08-23") == pytest.approx(101.0)


def test_a_date_before_every_bar_has_no_close():
    assert outcomes.close_on_or_before(_QUIET, "2026-08-01") is None


def test_an_empty_window_has_no_close():
    assert outcomes.close_on_or_before(_frame([]), "2026-08-24") is None


def test_a_close_after_the_mark_date_is_never_used():
    """The frame is fetched with a tail past `as_of` so the exit bar is always
    included. Reading the LAST row rather than the last row on or before
    `as_of` would silently mark against a later, unmatured price."""
    hist = _frame([
        ("2026-08-20", 100.0, 0.0, 0.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
        ("2026-08-28", 999.0, 0.0, 0.0),
    ])
    assert outcomes.close_on_or_before(hist, "2026-08-27") == pytest.approx(110.0)


# --- one window, one mark: the shape both legs use ---

def test_a_quiet_window_marks_at_the_plain_price_change():
    """The common case must be unchanged by all of this."""
    mark = outcomes.mark_from_window(_QUIET, "2026-08-20", "2026-08-27", 100.0)
    assert mark.exit_close == pytest.approx(110.0)
    assert mark.adjusted_entry_price == pytest.approx(100.0)
    assert mark.split_factor == 1.0
    assert mark.dividend_factor == 1.0
    assert mark.return_pct == pytest.approx(10.0)


def test_a_split_no_longer_reads_as_a_catastrophic_loss():
    """THE defect. A 10:1 split with the stock up 10%: uncorrected this reads
    -89%, which is the single largest error this subsystem could report."""
    hist = _frame([
        ("2026-08-20", 1000.0, 0.0, 0.0),
        ("2026-08-24", 105.0, 0.0, 10.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
    ])
    mark = outcomes.mark_from_window(hist, "2026-08-20", "2026-08-27", 1000.0)
    assert mark.split_factor == 10.0
    assert mark.adjusted_entry_price == pytest.approx(100.0)
    assert mark.return_pct == pytest.approx(10.0)


def test_a_dividend_makes_the_mark_a_total_return():
    """A holder who collected $2 on a $100 entry did better than the chart
    shows. The price return is 10%; the total return is 12.2%."""
    hist = _frame([
        ("2026-08-20", 100.0, 0.0, 0.0),
        ("2026-08-24", 100.0, 2.0, 0.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
    ])
    mark = outcomes.mark_from_window(hist, "2026-08-20", "2026-08-27", 100.0)
    assert mark.dividend_factor == pytest.approx(0.98)
    assert mark.adjusted_entry_price == pytest.approx(98.0)
    assert mark.return_pct == pytest.approx(12.2449, abs=1e-3)


def test_a_window_with_no_exit_close_is_not_a_mark():
    assert outcomes.mark_from_window(
        _QUIET, "2026-08-20", "2026-08-01", 100.0) is None


def test_a_window_whose_dividend_cannot_be_priced_is_not_a_mark():
    """No mark, rather than an under-corrected one. Recording it would make the
    row permanently unmarkable at a wrong value; leaving it pending costs a
    retry."""
    hist = _frame([
        ("2026-08-21", 100.0, 2.0, 0.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
    ])
    assert outcomes.mark_from_window(
        hist, "2026-08-20", "2026-08-27", 100.0) is None


def test_a_window_with_no_usable_entry_price_is_not_a_mark():
    assert outcomes.mark_from_window(
        _QUIET, "2026-08-20", "2026-08-27", 0.0) is None


# --- persistence: a mark and its correction travel together ---

def _db(tmp_path):
    from config import Config
    from database.models import initialize_db
    cfg = Config()
    cfg.db_path = str(tmp_path / "s.db")
    initialize_db(cfg.db_path)
    return cfg


def _rows(cfg):
    import sqlite3
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM shadow_outcomes").fetchall()


def test_a_recorded_mark_carries_the_correction_that_produced_it(tmp_path):
    """The Mark is passed whole rather than as loose floats, so a return_pct
    can never be stored next to factors that disagree with it -- which is the
    precise defect this whole change exists to remove."""
    from database import queries
    cfg = _db(tmp_path)
    mark = outcomes.Mark(110.0, 100.0, 10.0, 0.98, 10.0)
    bench = outcomes.Mark(505.0, 500.0, 1.0, 1.0, 1.0)

    queries.record_shadow_outcome(cfg.db_path, 1, "1w", "2026-08-27", mark, bench)

    row = _rows(cfg)[0]
    assert row["price"] == pytest.approx(110.0)
    assert row["return_pct"] == pytest.approx(10.0)
    assert row["adjusted_entry_price"] == pytest.approx(100.0)
    assert row["split_factor"] == pytest.approx(10.0)
    assert row["dividend_factor"] == pytest.approx(0.98)
    assert row["benchmark_price"] == pytest.approx(505.0)
    assert row["benchmark_return_pct"] == pytest.approx(1.0)


def test_a_missing_benchmark_leg_still_records_the_stock_mark(tmp_path):
    """A SPY outage must not cost every stock its mark. The benchmark columns
    go NULL and the row is still a real observation of what the stock did."""
    from database import queries
    cfg = _db(tmp_path)

    queries.record_shadow_outcome(
        cfg.db_path, 1, "1w", "2026-08-27",
        outcomes.Mark(110.0, 100.0, 1.0, 1.0, 10.0), None)

    row = _rows(cfg)[0]
    assert row["return_pct"] == pytest.approx(10.0)
    assert row["benchmark_price"] is None
    assert row["benchmark_return_pct"] is None


# --- end to end through mark_due_outcomes ---

import sqlite3  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


def _observe(cfg, ticker, session_date, price):
    conn = sqlite3.connect(cfg.db_path)
    conn.execute(
        "INSERT INTO shadow_observations (session_date, observed_at, ticker,"
        " scan_kind, stage_reached, outcome, reference_price)"
        " VALUES (?,?,?,'stock','recommended','recommended',?)",
        (session_date, session_date + "T13:45:00Z", ticker, price),
    )
    conn.commit()
    conn.close()


def _fake_windows(mapping, calls=None):
    """A `_fetch_window` stub keyed by ticker, recording the dates asked for."""
    def _fetch(ticker, start, end):
        if calls is not None:
            calls.append((ticker, start, end))
        return mapping.get(ticker, _frame([]))
    return _fetch


_SPY_WINDOW = _frame([
    ("2026-08-20", 500.0, 0.0, 0.0),
    ("2026-08-27", 505.0, 0.0, 0.0),
])


@pytest.mark.asyncio
async def test_a_split_between_observation_and_mark_is_corrected_end_to_end(
        tmp_path, monkeypatch):
    """The headline case, through the real job. Uncorrected this row would
    enter the sample as -89% and drag every cohort statistic it lands in."""
    cfg = _db(tmp_path)
    _observe(cfg, "NVDA", "2026-08-20", 1000.0)
    monkeypatch.setattr(outcomes, "_fetch_window", _fake_windows({
        "NVDA": _frame([
            ("2026-08-20", 1000.0, 0.0, 0.0),
            ("2026-08-24", 105.0, 0.0, 10.0),
            ("2026-08-27", 110.0, 0.0, 0.0),
        ]),
        "SPY": _SPY_WINDOW,
    }))

    n = await outcomes.mark_due_outcomes(
        cfg, instant=datetime(2026, 8, 28, tzinfo=timezone.utc))

    assert n == 1
    row = _rows(cfg)[0]
    assert row["return_pct"] == pytest.approx(10.0)
    assert row["split_factor"] == pytest.approx(10.0)
    assert row["adjusted_entry_price"] == pytest.approx(100.0)
    assert row["benchmark_return_pct"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_the_fetched_window_spans_the_whole_horizon(tmp_path, monkeypatch):
    """A window that merely ENDS at the mark date cannot see the corporate
    actions inside the horizon, so the factors would silently come back 1.0 --
    a correction that is absent rather than wrong, and therefore invisible."""
    cfg = _db(tmp_path)
    _observe(cfg, "AAPL", "2026-08-20", 100.0)
    calls = []
    monkeypatch.setattr(outcomes, "_fetch_window", _fake_windows({
        "AAPL": _QUIET, "SPY": _SPY_WINDOW}, calls=calls))

    await outcomes.mark_due_outcomes(
        cfg, instant=datetime(2026, 8, 28, tzinfo=timezone.utc))

    start, end = next((s, e) for t, s, e in calls if t == "AAPL")
    assert start <= "2026-08-20", "window must start at or before the session date"
    assert end > "2026-08-27", "window must extend past the mark date"


@pytest.mark.asyncio
async def test_the_benchmark_window_is_fetched_once_per_session_and_horizon(
        tmp_path, monkeypatch):
    """Every observation sharing a session date shares one SPY window. Fetching
    it per row multiplies a scan's network cost by the size of the universe."""
    cfg = _db(tmp_path)
    for ticker in ("AAPL", "MSFT", "XOM"):
        _observe(cfg, ticker, "2026-08-20", 100.0)
    calls = []
    monkeypatch.setattr(outcomes, "_fetch_window", _fake_windows({
        "AAPL": _QUIET, "MSFT": _QUIET, "XOM": _QUIET, "SPY": _SPY_WINDOW},
        calls=calls))

    await outcomes.mark_due_outcomes(
        cfg, instant=datetime(2026, 8, 28, tzinfo=timezone.utc))

    assert len([c for c in calls if c[0] == "SPY"]) == 1
    assert len(_rows(cfg)) == 3


@pytest.mark.asyncio
async def test_an_uncorrectable_window_leaves_the_horizon_pending(
        tmp_path, monkeypatch):
    """A dividend with no prior close cannot be priced. Recording the mark
    anyway would bake an under-correction in permanently; leaving it pending
    costs one repeat fetch."""
    from database import queries
    cfg = _db(tmp_path)
    _observe(cfg, "AAPL", "2026-08-20", 100.0)
    monkeypatch.setattr(outcomes, "_fetch_window", _fake_windows({
        "AAPL": _frame([
            ("2026-08-21", 100.0, 2.0, 0.0),
            ("2026-08-27", 110.0, 0.0, 0.0),
        ]),
        "SPY": _SPY_WINDOW,
    }))

    n = await outcomes.mark_due_outcomes(
        cfg, instant=datetime(2026, 8, 28, tzinfo=timezone.utc))

    assert n == 0
    assert _rows(cfg) == []
    assert len(queries.pending_shadow_marks(cfg.db_path, "1w", "2026-08-21")) == 1


# --- non-finite bars: SQLite has no NaN, it has NULL ---
# Found in production on the very first real marking run. yfinance emitted a
# 2026-08-28 bar with Close=NaN (a placeholder for a session with no data yet).
# NaN survived `is None` checks all the way into the INSERT, where SQLite
# silently stored it as NULL -- and because the ROW then existed,
# `pending_shadow_marks` excluded it forever. 50 observations were one command
# away from being permanently unmarkable.

def test_a_nan_close_resolves_back_to_the_last_real_bar():
    """The docstring already promised "a real bar". A NaN row is a session with
    no data, which is the same thing as a weekend for this purpose."""
    hist = _frame([
        ("2026-08-27", 110.0, 0.0, 0.0),
        ("2026-08-28", float("nan"), 0.0, 0.0),
    ])
    assert outcomes.close_on_or_before(hist, "2026-08-29") == pytest.approx(110.0)


def test_a_window_of_only_nan_closes_has_no_price():
    """None, not NaN. NaN reaches SQLite as NULL and books an unusable mark that
    can never be retried -- the `fills_observed` rule in a new place."""
    hist = _frame([
        ("2026-08-27", float("nan"), 0.0, 0.0),
        ("2026-08-28", float("nan"), 0.0, 0.0),
    ])
    assert outcomes.close_on_or_before(hist, "2026-08-29") is None


def test_an_infinite_close_is_refused_like_a_nan():
    hist = _frame([("2026-08-28", float("inf"), 0.0, 0.0)])
    assert outcomes.close_on_or_before(hist, "2026-08-29") is None


def test_a_window_with_no_finite_close_is_not_a_mark():
    hist = _frame([("2026-08-28", float("nan"), 0.0, 0.0)])
    assert outcomes.mark_from_window(
        hist, "2026-08-22", "2026-08-29", 100.0) is None


def test_a_nan_split_value_does_not_poison_the_factor():
    """`if value:` is True for NaN, so a NaN in the Stock Splits column
    multiplied the factor to NaN and took the whole mark with it."""
    hist = _frame([
        ("2026-08-22", 100.0, 0.0, 0.0),
        ("2026-08-24", 102.0, 0.0, float("nan")),
        ("2026-08-27", 110.0, 0.0, 0.0),
    ])
    assert outcomes.split_factor(hist, "2026-08-22", "2026-08-27") == 1.0


def test_a_nan_dividend_does_not_poison_the_factor():
    hist = _frame([
        ("2026-08-22", 100.0, 0.0, 0.0),
        ("2026-08-24", 100.0, float("nan"), 0.0),
        ("2026-08-27", 110.0, 0.0, 0.0),
    ])
    assert outcomes.dividend_factor(hist, "2026-08-22", "2026-08-27") == 1.0


def test_the_production_regression_a_trailing_nan_bar_still_marks():
    """The exact shape that shipped: real bars, then a trailing NaN placeholder
    for a session yfinance has no data for yet."""
    hist = _frame([
        ("2026-08-21", 1596.08, 0.0, 0.0),
        ("2026-08-27", 1484.95, 0.0, 0.0),
        ("2026-08-28", float("nan"), 0.0, 0.0),
    ])
    mark = outcomes.mark_from_window(hist, "2026-08-22", "2026-08-29", 1596.08)
    assert mark is not None
    assert mark.exit_close == pytest.approx(1484.95)
    assert mark.return_pct == pytest.approx(-6.9627, abs=1e-3)
