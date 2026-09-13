"""/market_trend: recession and fear gauges.

The classifiers are pure, so almost everything here runs with no mocks and no
network. The emergency-cut oracle uses every real DFEDTARU change since the
series began (copied from FRED on 2026-09-13) -- the rule is checked against
history, not against dates invented to fit it.
"""
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pandas as pd
import pytest

from screener.fomc_calendar import SCHEDULED_DECISIONS
from screener.market_trend import (
    Reading,
    classify_move,
    classify_rate_cuts,
    classify_skew,
    classify_vix,
    classify_vvix,
    classify_yield_curve,
    fetch_market_trend,
    fetch_yf_closes,
    is_scheduled_change,
    parse_fred_csv,
    target_changes,
    worst_status,
)

# ─── Fixtures ────────────────────────────────────────────────────────────────

# Every change in FRED's DFEDTARU from its first observation (2008-12-16, 0.25)
# through 2026-09-12. Dates are EFFECTIVE dates, which FRED records either on the
# decision day (Dec 2015, Dec 2016) or the weekday after (everything since).
REAL_TARGET_CHANGES = [
    ("2015-12-16", 0.25, 0.5), ("2016-12-14", 0.5, 0.75), ("2017-03-16", 0.75, 1.0),
    ("2017-06-15", 1.0, 1.25), ("2017-12-14", 1.25, 1.5), ("2018-03-22", 1.5, 1.75),
    ("2018-06-14", 1.75, 2.0), ("2018-09-27", 2.0, 2.25), ("2018-12-20", 2.25, 2.5),
    ("2019-08-01", 2.5, 2.25), ("2019-09-19", 2.25, 2.0), ("2019-10-31", 2.0, 1.75),
    ("2020-03-04", 1.75, 1.25), ("2020-03-16", 1.25, 0.25), ("2022-03-17", 0.25, 0.5),
    ("2022-05-05", 0.5, 1.0), ("2022-06-16", 1.0, 1.75), ("2022-07-28", 1.75, 2.5),
    ("2022-09-22", 2.5, 3.25), ("2022-11-03", 3.25, 4.0), ("2022-12-15", 4.0, 4.5),
    ("2023-02-02", 4.5, 4.75), ("2023-03-23", 4.75, 5.0), ("2023-05-04", 5.0, 5.25),
    ("2023-07-27", 5.25, 5.5), ("2024-09-19", 5.5, 5.0), ("2024-11-08", 5.0, 4.75),
    ("2024-12-19", 4.75, 4.5), ("2025-09-18", 4.5, 4.25), ("2025-10-30", 4.25, 4.0),
    ("2025-12-11", 4.0, 3.75),
]
REAL_EMERGENCIES = {date(2020, 3, 4), date(2020, 3, 16)}


def real_target_series():
    series = [(date(2008, 12, 16), 0.25)]
    for d, _old, new in REAL_TARGET_CHANGES:
        series.append((date.fromisoformat(d), new))
    return series


def curve(now: date, then_2y, now_2y, then_10y, now_10y, extra=()):
    """Build aligned (2y, 10y) series with a point 3 months back and one today.

    `extra` adds (days_ago, y2, y10) points, e.g. an inversion earlier in the year.
    """
    then = now - timedelta(days=100)
    points = [(then, then_2y, then_10y), *[(now - timedelta(days=a), y2, y10) for a, y2, y10 in extra],
              (now, now_2y, now_10y)]
    points.sort()
    return [(d, y2) for d, y2, _ in points], [(d, y10) for d, _, y10 in points]


# ─── VIX / MOVE / VVIX thresholds ───────────────────────────────────────────

@pytest.mark.parametrize("level,status,word", [
    (19.99, "calm", "Calm"),
    (20.0, "warning", "Elevated"),
    (30.0, "warning", "Elevated"),
    (30.01, "warning", "High fear"),
])
def test_vix_thresholds(level, status, word):
    r = classify_vix(level)
    assert r.status == status
    assert word in r.detail


@pytest.mark.parametrize("level,status", [(79.9, "calm"), (80.0, "watch"), (120.0, "watch"), (120.1, "warning")])
def test_move_thresholds(level, status):
    assert classify_move(level).status == status


@pytest.mark.parametrize("level,status", [(89.9, "calm"), (90.0, "watch"), (110.0, "watch"), (110.1, "warning")])
def test_vvix_thresholds(level, status):
    assert classify_vvix(level).status == status


@pytest.mark.parametrize("classify", [classify_vix, classify_move, classify_vvix])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None])
def test_level_classifiers_refuse_non_finite(classify, bad):
    # NaN compares False to every threshold, so without a guard it would fall
    # through to whichever branch comes last and read as a real level.
    assert classify(bad).status == "unknown"


# ─── Yield curve ────────────────────────────────────────────────────────────

def test_curve_inverted_is_a_warning():
    y2, y10 = curve(date(2026, 9, 10), 4.5, 4.8, 4.6, 4.6)
    r = classify_yield_curve(y2, y10)
    assert r.status == "warning"
    assert "Inverted" in r.detail
    assert r.value.startswith("-20bp")


def test_curve_bear_flattener_is_watch():
    # 2y +60bp, 10y +20bp: short end rising faster, still positive.
    y2, y10 = curve(date(2026, 9, 10), 4.0, 4.6, 4.5, 4.7)
    r = classify_yield_curve(y2, y10)
    assert r.status == "watch"
    assert "Bear flattener" in r.detail


def test_curve_bull_steepener_without_inversion_is_calm():
    # 2y -50bp, 10y -20bp: short end falling faster.
    y2, y10 = curve(date(2026, 9, 10), 4.0, 3.5, 4.5, 4.3)
    r = classify_yield_curve(y2, y10)
    assert r.status == "calm"
    assert "Bull steepener" in r.detail


def test_curve_bear_steepener_and_bull_flattener_are_named():
    y2, y10 = curve(date(2026, 9, 10), 4.0, 4.1, 4.5, 4.9)
    assert "Bear steepener" in classify_yield_curve(y2, y10).detail
    y2, y10 = curve(date(2026, 9, 10), 4.0, 3.9, 4.5, 4.1)
    assert "Bull flattener" in classify_yield_curve(y2, y10).detail


def test_curve_little_changed_under_10bp():
    y2, y10 = curve(date(2026, 9, 10), 4.0, 4.05, 4.5, 4.5)
    r = classify_yield_curve(y2, y10)
    assert r.status == "calm"
    assert "little changed" in r.detail


def test_curve_un_inversion_within_a_year_is_a_warning():
    # Inverted 200 days ago, positive now. Historically the recession tends to
    # arrive after this re-steepening, not at the moment of inversion.
    y2, y10 = curve(date(2026, 9, 10), 4.0, 3.8, 4.1, 4.2, extra=[(200, 5.0, 4.5)])
    r = classify_yield_curve(y2, y10)
    assert r.status == "warning"
    assert "Un-inverted" in r.detail


def test_curve_inversion_older_than_a_year_does_not_count():
    y2, y10 = curve(date(2026, 9, 10), 4.0, 3.8, 4.1, 4.2, extra=[(400, 5.0, 4.5)])
    assert classify_yield_curve(y2, y10).status == "calm"


def test_curve_only_uses_dates_both_series_have():
    # FRED publishes the two series independently; a 2y print with no matching
    # 10y print must not be subtracted from a different day's 10y.
    y2, y10 = curve(date(2026, 9, 10), 4.0, 4.6, 4.5, 4.7)
    y2 = y2 + [(date(2026, 9, 11), 9.9)]
    r = classify_yield_curve(y2, y10)
    assert r.as_of == date(2026, 9, 10)
    assert r.status == "watch"


def test_curve_without_a_three_month_point_is_unknown():
    today = date(2026, 9, 10)
    y2 = [(today - timedelta(days=5), 4.0), (today, 4.1)]
    y10 = [(today - timedelta(days=5), 4.5), (today, 4.6)]
    assert classify_yield_curve(y2, y10).status == "unknown"


def test_curve_empty_is_unknown():
    assert classify_yield_curve([], []).status == "unknown"


# ─── Emergency cuts ─────────────────────────────────────────────────────────

def test_oracle_only_the_two_2020_cuts_were_unscheduled():
    """Every real target change since 2008 classifies correctly.

    Pins both halves of the effective-date rule: the Dec 2015 / Dec 2016 hikes
    took effect ON the decision day, everything since on the weekday after.
    A one-sided rule misfiles one group or the other as an emergency.
    """
    unscheduled = {
        date.fromisoformat(d) for d, _, _ in REAL_TARGET_CHANGES
        if not is_scheduled_change(date.fromisoformat(d))
    }
    assert unscheduled == REAL_EMERGENCIES


def test_sunday_decision_rolls_to_monday_only_when_scheduled():
    # 2020-03-15 was a Sunday and NOT scheduled: its Monday effective date is
    # an emergency. A scheduled Friday decision would roll to Monday too.
    assert not is_scheduled_change(date(2020, 3, 16))
    assert is_scheduled_change(date(2024, 1, 1), schedule=(date(2023, 12, 29),))


def test_target_changes_lists_old_and_new():
    changes = target_changes([(date(2020, 1, 1), 1.0), (date(2020, 1, 2), 1.0), (date(2020, 1, 3), 0.5)])
    assert changes == [(date(2020, 1, 3), 1.0, 0.5)]


def test_emergency_cut_in_the_last_year_is_a_warning():
    r = classify_rate_cuts(real_target_series(), today=date(2020, 6, 1))
    assert r.status == "warning"
    assert "2020-03-16" in r.detail and "-100bp" in r.detail
    assert "2020-03-04" in r.detail and "-50bp" in r.detail


def test_emergency_cut_older_than_the_lookback_is_calm():
    r = classify_rate_cuts(real_target_series(), today=date(2021, 6, 1))
    assert r.status == "calm"


def test_scheduled_50bp_cut_is_not_an_emergency():
    # Sept 2024 was 50bp at a scheduled meeting -- size is not the test.
    r = classify_rate_cuts(real_target_series(), today=date(2024, 10, 1))
    assert r.status == "calm"
    assert "-50bp" in r.detail and "scheduled" in r.detail


def test_latest_change_is_reported():
    r = classify_rate_cuts(real_target_series(), today=date(2026, 9, 12))
    assert r.status == "calm"
    assert "-25bp" in r.detail and "2025-12-11" in r.detail
    assert r.value == "3.75%"


def test_unscheduled_hike_is_not_an_emergency_cut():
    series = [(date(2024, 1, 1), 1.0), (date(2024, 2, 20), 1.5)]
    r = classify_rate_cuts(series, today=date(2024, 3, 1), schedule=(date(2024, 12, 18),))
    assert r.status == "calm"


def test_stale_calendar_is_unknown_not_a_guess():
    r = classify_rate_cuts(real_target_series(), today=date(2028, 1, 5))
    assert r.status == "unknown"
    assert "fomc_calendar" in r.detail


def test_empty_target_series_is_unknown():
    assert classify_rate_cuts([], today=date(2026, 9, 12)).status == "unknown"


def test_calendar_is_sorted_and_ends_after_today():
    assert list(SCHEDULED_DECISIONS) == sorted(SCHEDULED_DECISIONS)
    assert SCHEDULED_DECISIONS[-1] >= date(2026, 9, 13)


# ─── SKEW ───────────────────────────────────────────────────────────────────

def ramp(start, end, n):
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def dated(values):
    first = date(2025, 9, 1)
    return [(first + timedelta(days=i), v) for i, v in enumerate(values)]


def test_skew_rising_and_high_with_calm_vix_is_flagged():
    closes = dated([130.0] * 192 + ramp(130.0, 160.0, 60))
    r = classify_skew(closes, vix_level=15.0)
    assert r.status == "watch"
    assert "rising" in r.detail
    assert "VIX" in r.detail  # the divergence note


def test_skew_high_with_elevated_vix_has_no_divergence_note():
    closes = dated([130.0] * 192 + ramp(130.0, 160.0, 60))
    assert "VIX" not in classify_skew(closes, vix_level=25.0).detail


def test_skew_falling_below_150_is_calm():
    closes = dated([160.0] * 192 + ramp(160.0, 130.0, 60))
    r = classify_skew(closes, vix_level=15.0)
    assert r.status == "calm"
    assert "falling" in r.detail


def test_skew_flat_reports_mid_percentile():
    r = classify_skew(dated([140.0] * 252), vix_level=None)
    assert "flat" in r.detail
    assert "50th pct" in r.detail


def test_skew_needs_sixty_closes():
    assert classify_skew(dated([140.0] * 59), vix_level=15.0).status == "unknown"


# ─── FRED parsing / yfinance closes ─────────────────────────────────────────

def test_parse_fred_csv_skips_missing_and_non_finite():
    text = "observation_date,DGS2\n2026-09-08,4.50\n2026-09-09,.\n2026-09-10,\n2026-09-11,nan\n2026-09-12,4.56\n"
    assert parse_fred_csv(text) == [(date(2026, 9, 8), 4.50), (date(2026, 9, 12), 4.56)]


def test_parse_fred_csv_accepts_the_older_date_header():
    assert parse_fred_csv("DATE,DGS10\n2026-09-10,4.95\n") == [(date(2026, 9, 10), 4.95)]


def test_parse_fred_csv_refuses_an_html_error_page():
    # A 200 with an HTML body must not parse to [] -- that reads as "no data"
    # and the curve would silently disappear instead of saying why.
    with pytest.raises(ValueError):
        parse_fred_csv("<!DOCTYPE html><html>maintenance</html>")


def test_fetch_yf_closes_drops_nan_bars():
    idx = pd.to_datetime(["2026-09-09", "2026-09-10", "2026-09-11"])
    frame = pd.DataFrame({"Close": [15.0, float("nan"), 15.8]}, index=idx)
    ticker = MagicMock()
    ticker.history.return_value = frame
    with patch("screener.market_trend.yf.Ticker", return_value=ticker):
        assert fetch_yf_closes("^VIX") == [(date(2026, 9, 9), 15.0), (date(2026, 9, 11), 15.8)]


# ─── Orchestration ──────────────────────────────────────────────────────────

NAMES = ["Yield curve (2s10s)", "VIX", "Emergency rate cut", "MOVE", "SKEW", "VVIX"]


def _fake_yf(symbol, period="1y"):
    level = {"^VIX": 15.0, "^MOVE": 82.0, "^SKEW": 140.0, "^VVIX": 91.0}[symbol]
    return dated([level] * 252)


def _fake_fred(series_id, start=None):
    today = date(2026, 9, 10)
    if series_id == "DFEDTARU":
        return real_target_series()
    y2, y10 = curve(today, 4.0, 4.6, 4.5, 4.7)
    return y2 if series_id == "DGS2" else y10


def test_fetch_market_trend_returns_all_six_in_order():
    with patch("screener.market_trend.fetch_fred_series", side_effect=_fake_fred), \
         patch("screener.market_trend.fetch_yf_closes", side_effect=_fake_yf):
        readings = fetch_market_trend(today=date(2026, 9, 12))
    assert [r.name for r in readings] == NAMES
    assert all(r.status != "unknown" for r in readings)


def test_a_fred_outage_only_blanks_the_fred_indicators():
    with patch("screener.market_trend.fetch_fred_series", side_effect=OSError("down")), \
         patch("screener.market_trend.fetch_yf_closes", side_effect=_fake_yf):
        readings = {r.name: r for r in fetch_market_trend(today=date(2026, 9, 12))}
    assert readings["Yield curve (2s10s)"].status == "unknown"
    assert readings["Emergency rate cut"].status == "unknown"
    assert "OSError" in readings["Yield curve (2s10s)"].detail
    assert readings["VIX"].status == "calm"
    assert readings["MOVE"].status == "watch"


def test_a_vix_outage_still_classifies_skew():
    def yf_no_vix(symbol, period="1y"):
        if symbol == "^VIX":
            raise RuntimeError("yahoo")
        return _fake_yf(symbol, period)

    with patch("screener.market_trend.fetch_fred_series", side_effect=_fake_fred), \
         patch("screener.market_trend.fetch_yf_closes", side_effect=yf_no_vix):
        readings = {r.name: r for r in fetch_market_trend(today=date(2026, 9, 12))}
    assert readings["VIX"].status == "unknown"
    assert readings["SKEW"].status == "calm"


def test_worst_status_ranks_warning_over_unknown():
    mk = lambda s: Reading(name="x", status=s, value="", detail="")
    assert worst_status([mk("calm"), mk("unknown")]) == "unknown"
    assert worst_status([mk("unknown"), mk("watch")]) == "watch"
    assert worst_status([mk("watch"), mk("warning"), mk("calm")]) == "warning"


# ─── Embed ──────────────────────────────────────────────────────────────────

def _reading(status, name="VIX"):
    return Reading(name=name, status=status, value="15.8", detail="Calm", as_of=date(2026, 9, 11))


@pytest.mark.parametrize("statuses,color", [
    (["calm", "calm"], discord.Color.green()),
    (["calm", "watch"], discord.Color.gold()),
    (["watch", "warning"], discord.Color.red()),
    (["calm", "unknown"], discord.Color.light_grey()),
])
def test_embed_color_follows_the_worst_status(statuses, color):
    from discord_bot.embeds import build_market_trend_embed
    embed = build_market_trend_embed([_reading(s) for s in statuses])
    assert embed.color == color


def test_embed_has_one_field_per_reading_with_value_detail_and_date():
    from discord_bot.embeds import build_market_trend_embed
    readings = [_reading("calm", name=n) for n in NAMES]
    embed = build_market_trend_embed(readings)
    assert [f.name.split(" ", 1)[1] for f in embed.fields] == NAMES
    assert "15.8" in embed.fields[0].value
    assert "Calm" in embed.fields[0].value
    assert "2026-09-11" in embed.fields[0].value


# ─── Slash command ──────────────────────────────────────────────────────────

def _bot():
    from discord_bot.bot import TradingBot
    bot = TradingBot.__new__(TradingBot)
    bot.config = MagicMock()
    return bot


def _interaction(order):
    interaction = MagicMock()
    interaction.response.defer = AsyncMock(side_effect=lambda **_: order.append("defer"))
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_command_defers_before_fetching_then_sends_embed():
    # Six network reads take longer than Discord's 3-second reply window.
    order = []
    interaction = _interaction(order)

    async def fake_to_thread(fn, *a, **kw):
        order.append("fetch")
        return [_reading("calm")]

    with patch("discord_bot.bot.asyncio.to_thread", new=fake_to_thread):
        await _bot()._market_trend_command(interaction)

    assert order == ["defer", "fetch"]
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert embed.title == "Market Trend"


@pytest.mark.asyncio
async def test_command_reports_failure_instead_of_leaving_discord_thinking():
    interaction = _interaction([])
    with patch("discord_bot.bot.asyncio.to_thread", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await _bot()._market_trend_command(interaction)
    sent = interaction.followup.send.call_args
    assert "unavailable" in sent.args[0]


@pytest.mark.asyncio
async def test_market_trend_is_registered():
    bot = _bot()
    bot.tree = MagicMock()
    bot.tree.sync = AsyncMock()
    bot._register_persistent_views = MagicMock()
    await bot.setup_hook()
    names = [c.args[0].name for c in bot.tree.add_command.call_args_list]
    assert "market_trend" in names
