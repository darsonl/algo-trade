from __future__ import annotations
import asyncio
import hashlib
import logging
import logging.handlers
import sqlite3
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import yfinance as yf

from apscheduler.triggers.date import DateTrigger

from market_time import (
    as_utc,
    is_trading_session,
    market_session_date,
    session_close_utc,
)

from config import Config
from keep_awake import hold_system_awake
from database.models import get_cursor, initialize_db
from risk import kill_switch
from database import queries
from research import outcomes, shadow_log
from screener.universe import get_watchlist, get_top_sp500_by_fundamentals, get_universe, partition_watchlist
from screener.fundamentals import evaluate_fundamentals, fetch_fundamental_info, fetch_eps_data, finite_number, normalize_dividend_yield, screen_price
from screener.technicals import passes_technical_filter, evaluate_technicals, fetch_technical_data
from analyst.news import fetch_news_headlines
from analyst.claude_analyst import analyze_ticker, create_analyst_client, create_fallback_client, create_fallback2_client, analyze_sell_ticker, analyze_etf_ticker
from screener.macro import fetch_macro_context
from screener.exit_signals import check_exit_signals
from database.order_accounting import DEFINITIVELY_UNFILLED_STATUSES, OPEN_ORDER_STATUSES
from risk.resolution import alert_stuck_orders
from risk.scan_lock import scan_lock
from schwab_client.auth import schwab_login_warning
from schwab_client.order_payload import extract_fills, map_broker_status
from schwab_client.orders import fetch_order, get_positions
from schwab_client.reconcile import diff_positions, format_reconciliation_report
from discord_bot.bot import TradingBot

logger = logging.getLogger(__name__)


def _record_shadow(config: Config, *args, **kwargs) -> None:
    """Call shadow_log.observe defensively.

    `observe()` itself never raises (Task 4 absorbs every failure mode inside
    it) -- but the scan loops are the product and this is only instrumentation
    for a research question, so even a violation of that contract (a bad
    monkeypatch, a future regression in observe()) must not be able to abort a
    scan. This is the one seam where that guarantee is enforced from the
    outside as well as the inside.
    """
    try:
        shadow_log.observe(config, *args, **kwargs)
    except Exception:
        logger.exception("shadow_log.observe raised unexpectedly; continuing")


# ---------------------------------------------------------------------------
# Pure orchestration helpers (tested in test_main.py)
# ---------------------------------------------------------------------------

def live_execution_banner(config: Config) -> str | None:
    """The message to log and post when the bot starts able to place real orders.

    Returns None when it cannot, so a dry run stays quiet.

    Both signals must say live, matching the sink's own predicate. A config with
    `execution_mode='live'` but `dry_run=True` cannot submit -- the sink refuses
    it -- so announcing "LIVE TRADING ACTIVE" for that state would be a false
    alarm, and a banner that cries wolf is a banner nobody reads.

    The old version required `DRY_RUN=false and PAPER_TRADING=false`: two
    variables, one of which gated nothing at all.
    """
    if config.execution_mode != "live" or config.dry_run:
        return None
    return (
        "LIVE TRADING ACTIVE: EXECUTION_MODE=live. Real orders will be placed "
        "on Schwab. The kill switch (/halt), OPS_USER_IDS and "
        "ALLOWED_DISCORD_USER_IDS are the remaining locks."
    )


def should_recommend(signal: str, tech_data: dict, config: Config) -> bool:
    """Return True only if signal is BUY and all technical filters pass."""
    if signal != "BUY":
        return False
    return passes_technical_filter(tech_data, config)


def _is_terminal(stream) -> bool:
    """True only for a stream that is an interactive terminal.

    Fails closed. `pythonw.exe` leaves sys.stderr as None, and a detached or
    closed stream can raise from isatty(); either way the answer is "no console",
    because dropping the console handler costs nothing while a StreamHandler
    built on a dead stream raises on the first record and takes the log with it.
    """
    try:
        return bool(stream is not None and stream.isatty())
    except Exception:
        return False


def build_log_handlers(log_dir: Path, stream=None) -> list[logging.Handler]:
    """The handlers root should carry: always the file, the console only if there is one.

    The rotating file handler is the log of record. The StreamHandler is added
    ONLY when `stream` is a terminal, i.e. when a human is watching.

    Under the Task Scheduler deployment stderr is redirected to logs/stdout.log,
    where a console handler would write a second, UNROTATED copy of every record
    the file handler already holds. It is also a hazard rather than a nicety: a
    Windows console has QuickEdit Mode on by default, a stray selection suspends
    every write to it, and the first thread to log then blocks forever holding
    the logging lock -- which on 2026-09-12 wedged the APScheduler executor and
    silently cost a scan.

    `stream` is compared against sys.stderr by the caller, not sys.stdout,
    because logging.StreamHandler() defaults to stderr.
    """
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        Path(log_dir) / "algo_trade.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    handlers: list[logging.Handler] = [file_handler]

    if _is_terminal(stream):
        stream_handler = logging.StreamHandler(stream)
        stream_handler.setFormatter(fmt)
        handlers.append(stream_handler)
    return handlers


def scheduler_summary(label: str, times: list[str], timezone: str | None) -> str:
    """Describe the schedule that was ACTUALLY registered.

    Built from the same list `configure_scheduler` iterates, because the
    previous version read `config.scan_hour`/`scan_minute` -- the FALLBACK
    fields, used only when SCAN_TIMES is empty. With SCAN_TIMES set the bot
    announced "daily scan at 22:00", a time at which nothing happens, and never
    mentioned the two times at which scans actually run.

    The timezone is named because this host is Asia/Taipei while the markets are
    in New York: "21:45 machine-local" is legible as an ET market time only if
    the reader is told which clock it is on.
    """
    where = timezone or "machine-local time — set SCAN_TIMEZONE to read these as market times"
    if not times:
        return f"{label}: NO times scheduled — this scan will never run."
    return f"{label} scheduled at {', '.join(times)} ({where})"


def configure_scheduler(
    scheduler: BackgroundScheduler,
    config: Config,
    job_fn,
    times: list[str] | None = None,
    job_id_prefix: str = "scan",
) -> None:
    """Register one scan job per time.

    Defaults to stock scan (config.scan_times, prefix 'scan'); pass times +
    job_id_prefix for ETF scheduling (per Phase 12 D-03).

    Times are interpreted in config.scan_timezone when set (e.g.
    "America/New_York" keeps the schedule market-aligned across DST);
    otherwise machine-local time, the historical behavior.
    """
    scan_times = times if times is not None else config.scan_times
    tz = config.scan_timezone or None
    for i, time_str in enumerate(scan_times):
        hour, minute = map(int, time_str.split(":"))
        scheduler.add_job(
            job_fn,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
            id=f"{job_id_prefix}_{i}",
            replace_existing=True,
        )


def compute_headline_hash(headlines: list[str]) -> str:
    """SHA-256 cache key over sorted headlines.

    An empty headline list is salted with today's date: a broken or empty news
    feed would otherwise produce one constant hash per ticker, pinning a single
    analyst_cache entry forever. The salt bounds that staleness to one day.
    """
    if headlines:
        content = "\n".join(sorted(headlines))
    else:
        content = f"NO_HEADLINES:{date.today().isoformat()}"
    return hashlib.sha256(content.encode()).hexdigest()


def analyst_tiers(config: Config) -> list[tuple[str, str, int]]:
    """The configured chain as (provider, model, daily_limit), skipping empties.

    Resolves the model the same way `_run_with_fallbacks` does, so the quota
    counter and the caller cannot disagree about which model a tier uses.
    """
    from analyst.claude_analyst import _DEFAULT_MODELS

    tiers = [
        (config.analyst_provider, config.analyst_model, config.analyst_daily_limit),
        (config.analyst_fallback_provider, config.analyst_fallback_model,
         config.analyst_fallback_daily_limit),
        (config.analyst_fallback2_provider, config.analyst_fallback2_model,
         config.analyst_fallback2_daily_limit),
    ]
    return [
        (provider, model or _DEFAULT_MODELS.get(provider, ""), limit)
        for provider, model, limit in tiers
        if provider
    ]


def all_providers_exhausted(config: Config) -> bool:
    """True only when every configured tier is at or over ITS OWN daily quota.

    Per tier, and per MODEL within a provider, because that is how the free tier
    meters: gemini-3.1-flash-lite allows 500 RPD and gemini-3.7-flash 20, both
    under provider 'gemini'. The previous version counted per provider against
    one shared limit, so those two tiers produced literally the same number and
    the larger budget was unreachable.

    An unconfigured tier is skipped rather than counted as exhausted: with no
    tiers configured at all there is nothing to exhaust, and `all()` over an
    empty list is True, which would stop analysis entirely -- hence the guard.
    """
    tiers = analyst_tiers(config)
    if not tiers:
        return False
    return all(
        queries.get_analyst_call_count_today(config.db_path, provider, model) >= limit
        for provider, model, limit in tiers
    )


# ---------------------------------------------------------------------------
# Position reconciliation (RISK-05)
# ---------------------------------------------------------------------------

def _untrustworthy_fill(status: str, payload: dict, shares: float, notional: float) -> str | None:
    """Why this payload's fills must not be booked, or None if they may be.

    Three cases, and only the third is safe to trust blindly:

    * a terminal status with no `filledQuantity` at all -- it says the order is
      done but not how much of it happened;
    * a quantity with no execution prices to value it at -- `extract_fills`
      reports (n, 0.0) here deliberately and says the caller must decide, and
      booking $0 for ten real shares releases the whole reservation;
    * a definitively refused order (rejected / submit_failed), where the broker
      turned it down outright, so no capital moved and the zero is real.
    """
    if status in DEFINITIVELY_UNFILLED_STATUSES:
        return None
    if "filledQuantity" not in payload:
        return "the payload reports no filledQuantity"
    if shares > 0 and notional <= 0:
        return f"{shares:g} shares reported with no execution prices to value them"
    return None


def _apply_broker_status(config: Config, order: dict, payload: dict) -> int | None:
    """Write one order's broker verdict to the ledger. Returns a rec_id to retire.

    Split out of the sweep so the DB work is one short-lived connection per
    order and `complete_recommendation` -- which opens its own -- is never
    called while this one holds the write lock.
    """
    order_id = order["id"]
    update = map_broker_status(payload)

    with get_cursor(config.db_path) as conn:
        if update.successor_id is not None:
            # Follow the pointer the broker gave us. adopt_replacement closes
            # the predecessor at its ACTUAL fills and inserts the successor with
            # its OWN quantity and limit, so a 5@$100 replaced by 10@$150 stops
            # reserving $500 against $1,500 of live exposure.
            queries.adopt_replacement(conn, order_id, payload)
            return None

        if update.status == "submit_unknown":
            queries.mark_order_submit_unknown(conn, order_id, update.reason or "")
            return None

        if not update.terminal:
            return None

        # `fills_observed` gates the release of capital, so the fills must be
        # recorded in the same breath as the terminal status. A terminal row
        # with an unverified zero fill releases the whole budget (finding 6) --
        # which is why the flag is only flipped for fills we can actually trust.
        filled_shares, filled_notional = extract_fills(payload)
        unusable = _untrustworthy_fill(update.status, payload, filled_shares, filled_notional)
        if unusable:
            logger.warning(
                "Sweep: order %s is %s but %s; keeping its full commitment",
                order_id, update.status, unusable,
            )
            queries.mark_order_terminal_unobserved(
                conn, order_id, update.status, f"terminal, fills unusable: {unusable}"
            )
        else:
            queries.observe_fills(
                conn, order_id, filled_shares, filled_notional, update.status
            )

    return order["recommendation_id"]


async def sweep_terminal_recommendations(config: Config) -> int:
    """Retire recommendations whose orders the broker says are done (§11).

    This is the release valve for the `approved`-covering partial unique index.
    Without it the first buy of any ticker blocks that ticker forever: the claim
    flips the row to `approved`, and nothing ever moves it off.

    Runs at the start of each scan. Every failure is per-order and non-fatal --
    a sweep that aborts on the first broker outage leaves every later ticker
    blocked for reasons that have nothing to do with those tickers, and an
    outage must never be read as "the order is gone".

    Skipped in DRY_RUN: simulated orders have no broker counterpart to ask about.
    """
    if config.dry_run:
        return 0

    with get_cursor(config.db_path) as conn:
        open_orders = queries.get_orders_by_status(conn, OPEN_ORDER_STATUSES)

    completed = 0
    for order in open_orders:
        broker_order_id = order["broker_order_id"]
        if not broker_order_id:
            # A pending_submit row has no id to ask about, and calling the
            # broker with a NULL id is a request for somebody else's order.
            continue

        try:
            payload = await asyncio.to_thread(fetch_order, config, broker_order_id)
        except Exception as exc:
            logger.warning(
                "Sweep: could not read broker order %s for %s (%s); leaving it open",
                broker_order_id, order["ticker"], exc,
            )
            continue

        try:
            rec_id = await asyncio.to_thread(_apply_broker_status, config, order, payload)
        except Exception:
            logger.exception(
                "Sweep: could not apply broker status for order %s", order["id"]
            )
            continue

        if rec_id is None:
            continue

        try:
            if await asyncio.to_thread(
                queries.complete_recommendation, config.db_path, rec_id, payload["status"]
            ):
                completed += 1
                logger.info(
                    "Sweep: recommendation %s (%s) completed — broker says %s",
                    rec_id, order["ticker"], payload["status"],
                )
        except Exception:
            logger.exception("Sweep: could not complete recommendation %s", rec_id)

    return completed


async def run_reconciliation(bot: TradingBot, config: Config, alert_on_discrepancy: bool = True) -> str:
    """Compare DB open positions against the Schwab account and report drift.

    Report-only: never mutates positions — correcting real-money state is a
    human decision, consistent with the approval flow for trades. Returns a
    human-readable summary string. When discrepancies are found, posts them as
    an ops alert unless alert_on_discrepancy=False (the /reconcile command
    passes False because it displays the returned summary itself).
    """
    if config.dry_run:
        msg = "Reconciliation skipped: DRY_RUN mode (positions are simulated, broker comparison is meaningless)."
        logger.info(msg)
        return msg

    try:
        broker_positions = await asyncio.to_thread(get_positions, config)
    except Exception as exc:
        msg = f"Reconciliation failed: could not fetch Schwab positions ({exc})."
        logger.warning(msg)
        # This is the RISK-05 safety monitor. A failure to RUN is not the same as
        # a clean result, and staying quiet about it manufactures confidence:
        # get_positions raised on every call for months and nothing surfaced it.
        # Gated on alert_on_discrepancy so /reconcile, which renders this string
        # itself, does not also post it.
        if alert_on_discrepancy:
            await bot.send_ops_alert(msg)
        return msg

    db_rows = await asyncio.to_thread(queries.get_open_positions, config.db_path)
    diff = diff_positions([dict(r) for r in db_rows], broker_positions)
    report = format_reconciliation_report(diff)

    if report is None:
        msg = f"Reconciliation clean: {len(db_rows)} open position(s) match Schwab."
        logger.info(msg)
        return msg

    logger.warning("Position reconciliation discrepancies:\n%s", report)
    if alert_on_discrepancy:
        await bot.send_ops_alert(report)
    return report


# ---------------------------------------------------------------------------
# Scan pipeline
# ---------------------------------------------------------------------------

async def analyze_with_cache(
    config: Config,
    ticker: str,
    headlines: list[str],
    analyze_fn,
) -> dict | None:
    """Shared analyst-cache + quota path for the buy and ETF scans.

    Returns the analysis dict — from analyst_cache on a hit, or by awaiting
    analyze_fn() on a miss (its result is then written to the cache). Returns
    None when every provider is at its daily quota, signalling the caller to
    skip this ticker (`if analysis is None: continue`).

    analyze_fn is an async zero-arg callable that builds the pass-specific
    prompt and runs the analyzer. It runs ONLY on a cache miss, so any expensive
    enrichment inside it (e.g. the buy pass's fetch_eps_data) is skipped on a hit.
    """
    headline_hash = compute_headline_hash(headlines)
    cached = queries.get_cached_analysis(config.db_path, ticker, headline_hash)
    if cached:
        logger.debug("Cache hit for %s (hash %s...)", ticker, headline_hash[:8])
        return cached

    # D-11: quota guard — skip if all providers exhausted
    if all_providers_exhausted(config):
        logger.warning(
            "Daily analyst quota reached for all providers, skipping analysis for %s",
            ticker,
        )
        return None

    analysis = await analyze_fn()
    # Load-bearing: None is reserved above as the "quota-exhausted, skip" signal, so a
    # real analysis must be a dict. analyze_ticker/analyze_etf_ticker always return one
    # (or raise) — this guards against a future analyzer silently reading as "skip".
    assert analysis is not None, "analyze_fn must return an analysis dict, not None"
    try:
        queries.set_cached_analysis(
            config.db_path, ticker, headline_hash,
            analysis["signal"], analysis["reasoning"],
            confidence=analysis.get("confidence"),
        )
    except Exception as cache_exc:
        logger.warning("Failed to write analyst cache for %s: %s", ticker, cache_exc)
    return analysis


async def alert_schwab_login(bot: TradingBot, config: Config, now=None) -> None:
    """Post an ops alert when the Schwab login has expired or cannot reach the next scan.

    Every scan, on both paths, like `alert_stuck_orders`: a refresh token lasts
    7 days and only a human at the machine can renew it, so the warning has to
    arrive while there is still time to act. Approve needs a Schwab quote even
    in dry run, so this matters in every execution mode.

    The deadline passed in is the NEXT SCAN, because a scan is the only moment
    this alert can be delivered -- see `schwab_login_warning`. Computing it here
    rather than inside that function keeps the scheduling knowledge on this side
    of the boundary, the same way `preflight` is handed `trading_enabled` rather
    than reading the kill switch itself.

    Never raises -- it is reporting, and must not abort the scan it runs in.
    """
    try:
        next_scan = next_scheduled_scan_utc(config, now)
        message = await asyncio.to_thread(schwab_login_warning, None, now, next_scan)
        if message:
            logger.warning("%s", message)
            await bot.send_ops_alert(message)
    except Exception:
        logger.exception("Schwab login check failed; continuing the scan")


async def _drain_ops_outbox(bot: TradingBot) -> None:
    """Retry ops alerts stranded by an earlier Discord outage.

    A scan is the natural retry point: it is the recurring beat of the system,
    and an alert about the previous scan is exactly what an outage would have
    eaten. Failures are contained — a broken outbox must not abort the scan it
    would be reporting on.
    """
    try:
        redelivered = await bot.drain_ops_alerts()
        if redelivered:
            logger.info("Redelivered %d backlogged ops alert(s)", redelivered)
    except Exception as exc:
        logger.error("Ops-alert outbox drain failed: %s", exc)


SCAN_JOB_PREFIXES = ("scan_", "etf_scan_")


def last_scheduled_scan_utc(config: Config, instant=None) -> datetime | None:
    """When the session's LAST scheduled scan is due, in UTC. None if none are scheduled.

    Both schedules count: the ETF scan runs after the stock scan. Each time is
    read on the session's Eastern date in SCAN_TIMEZONE -- the clock
    `configure_scheduler` registers it on -- so 10:00 ET is 14:00 UTC in summer
    and 15:00 UTC in winter. Without SCAN_TIMEZONE the scheduler reads times as
    machine-local, and so does this.
    """
    times = list(config.scan_times) + list(config.etf_scan_times)
    if not times:
        return None
    tz = ZoneInfo(config.scan_timezone) if config.scan_timezone else datetime.now().astimezone().tzinfo
    session = market_session_date(instant)
    due = []
    for time_str in times:
        hour, minute = map(int, time_str.split(":"))
        due.append(datetime.combine(session, dtime(hour, minute), tzinfo=tz))
    return max(due).astimezone(timezone.utc)


# A fortnight is far longer than any market closure, so failing to find a scan
# within it means the schedule is empty of usable times, not that the calendar
# is busy. Bounds the walk instead of trusting the calendar to terminate it.
_SCAN_SEARCH_DAYS = 14


def next_scheduled_scan_utc(config: Config, after=None) -> datetime | None:
    """The next instant a scan will actually run, in UTC. None if none will.

    The sibling of `last_scheduled_scan_utc`, reading the SAME two lists on the
    same clock, and walking forward instead of naming one session's last.

    "Actually" is the whole point: a cron trigger fires every calendar day, but
    `scan_allowed_now()` returns early off the real XNYS calendar, so a Saturday
    firing is not a scan. Anything that needs to know when the operator can next
    be reached -- which is what a scan is, for reporting purposes -- has to skip
    non-sessions, or it will name a Sunday and promise a message nobody sends.

    Returns None rather than raising when the calendar cannot answer; callers
    treat that as "the deadline is unknown" and fall back on something weaker.
    """
    times = list(config.scan_times) + list(config.etf_scan_times)
    tz = ZoneInfo(config.scan_timezone) if config.scan_timezone else datetime.now().astimezone().tzinfo
    start = as_utc(after)
    first_session = market_session_date(start)
    for offset in range(_SCAN_SEARCH_DAYS):
        day = first_session + timedelta(days=offset)
        runs = sorted(
            datetime.combine(day, dtime(*map(int, t.split(":"))), tzinfo=tz).astimezone(timezone.utc)
            for t in times
        )
        later = [run for run in runs if run > start]
        if not later:
            continue
        try:
            if not is_trading_session(later[0]):
                continue
        except Exception:
            # The deadline is a refinement; the warning it refines is not. A
            # calendar outage must degrade to "unknown" here rather than
            # propagate into `alert_schwab_login`, whose except would swallow
            # an EXPIRED alert along with it.
            logger.warning("Could not read the exchange calendar; the next scan time is unknown")
            return None
        return later[0]
    return None


def post_scan_shutdown_at(next_run_times, close_utc, now, window_min) -> datetime | None:
    """When to exit, given the scan jobs' next run times -- or None while a scan is still due today.

    A scan is still due only if its next run lies strictly between now and the
    close. A run time already in the past is being processed, not waiting:
    APScheduler emits a MISSED event before it advances that job's next run time,
    and counting it as pending would keep the bot up until the bell. A paused
    job (None) is not waiting either.
    """
    for run in next_run_times:
        if run is not None and now < run and (close_utc is None or run < close_utc):
            return None
    return now + timedelta(minutes=window_min)


def schedule_post_scan_shutdown(scheduler, shutdown_fn, when) -> None:
    """Register (or move) the one-shot exit after the last scan's approval window.

    `misfire_grace_time=None` means "run however late". APScheduler's default is
    ONE SECOND, and on 2026-09-17 the host slept from 22:01 to 06:58 -- straight
    through this job's 22:31 fire time. It was discarded as a misfire rather than
    run, `bot.close()` was never called, and the process was still up 25 hours
    later. Arriving at the exit eight hours late is not a stale job; it is still
    the exit. Scans keep the 1-second default on purpose -- see configure_scheduler.
    """
    scheduler.add_job(
        shutdown_fn,
        trigger=DateTrigger(run_date=when),
        id="post_scan_shutdown",
        replace_existing=True,
        misfire_grace_time=None,
    )


def scheduler_needs_setup(scheduler) -> bool:
    """False once the scheduler is running, so a Discord reconnect cannot set it up twice.

    `on_ready` is not a startup hook. discord.py fires it again on every gateway
    RESUME, and on 2026-09-17 it fired 46 times in one session. Re-running the
    setup re-added the scan jobs harmlessly (stable IDs, `replace_existing`), but
    it also appended ANOTHER post-scan listener each time -- `add_listener` has no
    dedup -- and then raised `SchedulerAlreadyRunningError` from `start()`.

    That raise is the damaging part: it abandoned the rest of `on_ready`,
    including the approval-window re-arm below the `start()` call, which is
    exactly the code that recovers a shutdown lost to a sleep. The recovery path
    was unreachable from the reconnect that needed it.
    """
    return not scheduler.running


def make_post_scan_listener(jobs_fn, schedule_fn, close_utc, window_min, clock):
    """The scheduler listener that arms the exit once the session's last scan is done.

    Called after every job event; only scan jobs count, both as the trigger and
    as "still pending" -- the bell shutdown sits before the close all day and
    would otherwise read as a scan that never arrives. Registered for EXECUTED,
    ERROR and MISSED alike: a scan that crashed or was slept through is just as
    finished, since nothing re-runs a missed scan.
    """
    def _listener(event):
        try:
            if not str(getattr(event, "job_id", "")).startswith(SCAN_JOB_PREFIXES):
                return
            runs = [j.next_run_time for j in jobs_fn() if j.id.startswith(SCAN_JOB_PREFIXES)]
            when = post_scan_shutdown_at(runs, close_utc, clock(), window_min)
            if when is not None:
                schedule_fn(when)
        except Exception:
            # A listener that raises is only logged by APScheduler; say what it was.
            logger.exception("Could not arm the post-scan shutdown; the bell shutdown still applies")

    return _listener


def session_window_status(instant=None, config: Config | None = None) -> str:
    """"run" | "not_a_session" | "already_closed" | "scans_done" -- should a process be up now?

    `scans_done` (only when `config` is given): the session's last scheduled scan
    plus its approval window is behind us. Nothing re-runs a missed scan, so a
    process started now would only idle -- and hold the PC awake -- until the
    bell. It is to the post-scan exit what `already_closed` is to the bell.

    Decided from the exchange calendar's own close, so half-days are handled:
    the Friday after Thanksgiving and Christmas Eve close at 13:00 ET, and a
    hardcoded 16:00 would keep the bot up three hours past the bell.

    "run" includes PRE-OPEN. That is deliberate and is why the task starts at
    08:00 ET: `risk/preflight.py` guard 4 relaxes quote staleness outside
    regular hours precisely because pre-open is when approvals are expected.

    `already_closed` is what makes the task's repeating trigger safe. The
    trigger repeats through the session so a bot that dies is back within
    minutes; without this state, a repetition firing after the bell would start
    a process that sat idle until the next day.
    """
    close = session_close_utc(instant)
    if close is None:
        return "not_a_session"
    if as_utc(instant) >= close:
        return "already_closed"
    if config is not None:
        last = last_scheduled_scan_utc(config, instant)
        if last is not None and as_utc(instant) >= last + timedelta(minutes=config.post_scan_window_min):
            return "scans_done"
    return "run"


def schedule_session_shutdown(scheduler, shutdown_fn, close_utc) -> None:
    """Register the one-shot job that ends the process at the closing bell.

    A DateTrigger rather than a cron entry, because the close is not a fixed
    time of day -- see session_close_utc. The bot exits daily and is started
    again by the task, so only today's close is ever needed.

    `misfire_grace_time=None` for the same reason as the post-scan exit: the
    2026-09-17 sleep swallowed this backstop too (missed by 2:59:07). The bell is
    what catches a post-scan exit that never armed, so the two being discarded by
    the same sleep is precisely the case that leaves nothing to stop the process.
    """
    scheduler.add_job(
        shutdown_fn,
        trigger=DateTrigger(run_date=close_utc),
        id="session_shutdown",
        replace_existing=True,
        misfire_grace_time=None,
    )


def scan_allowed_now(instant=None) -> bool:
    """False only when we can POSITIVELY establish this is not a trading session.

    Fails OPEN, unlike almost every other guard in this codebase, and the
    asymmetry is the point. The kill switch and the preflight table protect
    CAPITAL, so an unknown there must refuse. This one protects DATA QUALITY --
    a scan places no orders, approval is a separate human step -- so the costs
    run the other way: a junk weekend row is identifiable by `session_date` and
    can be deleted, while a trading day lost to a flaky calendar lookup is gone
    for good. When we cannot tell, scan.
    """
    try:
        return is_trading_session(instant)
    except Exception:
        logger.exception("Trading-calendar check failed; scanning anyway")
        return True


async def run_scan(bot: TradingBot, config: Config) -> None:
    """Run the full screening pipeline, one scan at a time.

    Skipped, never queued, when another scan already holds the lock. Running it
    afterwards would screen a market that has already moved on, spend analyst
    quota a second time, and post recommendations stamped to a moment that has
    passed. ONE lock covers both scan paths: a symbol can appear in the stock
    universe and in the ETF universe.
    """
    if not scan_allowed_now():
        logger.info(
            "Scan skipped: %s is not an NYSE trading session", market_session_date()
        )
        return
    lock = scan_lock()
    if lock.locked():
        logger.warning("Scan skipped: another scan is already running")
        return
    async with lock:
        await _run_scan_locked(bot, config)


async def _run_scan_locked(bot: TradingBot, config: Config) -> None:
    """Run the full screening pipeline and post qualifying tickers to Discord."""
    logger.info("Starting scan...")
    await _drain_ops_outbox(bot)
    # Repeated on every scan: guard 11 blocks this ticker until a human
    # runs /resolve, and an alert nobody repeats is a block nobody sees.
    await alert_stuck_orders(bot, config)
    await alert_schwab_login(bot, config)
    # Before anything is screened, not after: a ticker whose order the broker
    # has finished with should be eligible in THIS scan, not the next one.
    try:
        await sweep_terminal_recommendations(config)
    except Exception:
        # Reporting and housekeeping must never abort the scan they run inside.
        logger.exception("Terminal-order sweep failed; continuing the scan")
    # Research marks. Same contract as the sweep: never fatal to the scan.
    try:
        await outcomes.mark_due_outcomes(config)
    except Exception:
        logger.exception("Shadow outcome marking failed; continuing the scan")
    queries.expire_stale_recommendations(config.db_path)

    watchlist_path = str(Path(__file__).parent / "watchlist.txt")
    try:
        sp500 = await asyncio.to_thread(get_top_sp500_by_fundamentals, config)  # P8-audit: already wrapped (hotfix ae66e64)
    except Exception as exc:
        logger.warning("Could not fetch top S&P 500: %s — using watchlist only", exc)
        sp500 = []

    universe = get_universe(watchlist_path, extra_tickers=sp500)
    # Filter ETFs out of stock scan universe. partition_watchlist already fetches each
    # ticker's .info (the heaviest yfinance call) to read quoteType; capture it in
    # info_by_ticker so the loop below can reuse it instead of fetching .info twice.
    info_by_ticker: dict = {}
    try:
        stocks_only, _etfs = await asyncio.to_thread(partition_watchlist, universe, info_by_ticker)  # P8-audit: already wrapped (Phase 7)
        universe = stocks_only
    except Exception as exc:
        logger.warning("partition_watchlist failed: %s — using full universe", exc)
    logger.info("Universe: %d tickers", len(universe))

    # Fetch macro context once for all tickers (D-02)
    try:
        macro_context = await asyncio.to_thread(fetch_macro_context)
    except Exception as exc:
        logger.warning("Macro context fetch failed: %s — continuing without macro", exc)
        macro_context = {"spy_trend_1m": None, "spy_trend_1y": None, "vix_level": None}

    client = create_analyst_client(config)
    fallback_client = create_fallback_client(config)
    fallback2_client = create_fallback2_client(config)

    def on_attempt(provider: str, model: str) -> None:
        # Count every attempt against today's quota — calls that reach a
        # provider and then fail burn quota exactly like successes. Keyed on the
        # MODEL too, because the free tier meters per model, not per provider.
        queries.increment_analyst_call_count(config.db_path, provider, model)

    recommendations_posted = 0
    error_count = 0
    errors_posted = 0
    headline_fetches = 0
    empty_headline_fetches = 0
    scan_time = datetime.now().strftime("%H:%M")

    for ticker in universe:
        if queries.ticker_recommended_today(config.db_path, ticker):
            _record_shadow(config, ticker, "stock", "universe",
                           "skipped_recommended_today")
            continue
        if queries.has_open_position(config.db_path, ticker):
            logger.debug("Skipping %s: open position exists", ticker)
            _record_shadow(config, ticker, "stock", "universe",
                           "skipped_open_position")
            continue
        if queries.has_active_recommendation(config.db_path, ticker):
            # Not the same question as ticker_recommended_today, which is
            # session-scoped: an `approved` row left by an ambiguous submission
            # days ago is invisible to that guard but blocks this insert.
            logger.debug("Skipping %s: an active recommendation already exists", ticker)
            _record_shadow(config, ticker, "stock", "universe",
                           "skipped_active_recommendation")
            continue

        try:
            yf_ticker = yf.Ticker(ticker)
            # Reuse the .info already fetched by partition_watchlist; fetch only on a miss
            # (e.g. the ticker hit the allowlist fallback and was never fetched).
            info = info_by_ticker.get(ticker)
            if info is None:
                info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)
            # The verdict is kept, not just its bool: every later exit on this
            # candidate records the gate that LET IT THROUGH, so a threshold
            # change is traceable through the whole funnel and not only at the
            # stage it rejected someone.
            fundamental_gate = evaluate_fundamentals(info, config)
            if not fundamental_gate.passed:
                _screen_price, _screen_src = screen_price(info)
                _record_shadow(config, ticker, "stock", "fundamental",
                               "rejected_fundamental", fundamentals=info,
                               macro=macro_context,
                               reference_price=_screen_price,
                               reference_price_source=_screen_src,
                               gates=(fundamental_gate,))
                continue

            # Phase 16 (SIG-05, SIG-06): earnings date from info dict — zero extra HTTP call (D-09).
            _ts = info.get("earningsTimestamp")
            if _ts is None:
                earnings_date_embed = "N/A"
                earnings_date_prompt = None
            else:
                _earnings_dt = datetime.fromtimestamp(_ts, tz=timezone.utc).date()
                _today = date.today()
                if _earnings_dt < _today:
                    # Past earnings — suppress to N/A per D-02
                    earnings_date_embed = "N/A"
                    earnings_date_prompt = None
                else:
                    _days_until = (_earnings_dt - _today).days
                    _date_str = _earnings_dt.strftime("%b %d, %Y")
                    if 0 <= _days_until < 7:
                        # Within 7 days — warning prefix in embed (D-06), proximity note in prompt (D-08)
                        earnings_date_embed = f"⚠️ {_date_str}"
                        earnings_date_prompt = f"{_date_str} (in {_days_until} days — proximity risk)"
                    else:
                        earnings_date_embed = _date_str
                        earnings_date_prompt = _date_str

            headlines = await asyncio.to_thread(
                fetch_news_headlines, ticker,
                finnhub_api_key=config.finnhub_api_key,
                alpha_vantage_api_key=config.alpha_vantage_api_key,
            )
            headline_fetches += 1
            if not headlines:
                empty_headline_fetches += 1

            async def _analyze_buy():
                # Runs only on a cache miss (analyze_with_cache calls this closure only
                # then): the cached path never uses fundamental_trend, and fetch_eps_data
                # (quarterly_income_stmt) is a slow network call we shouldn't pay for on a
                # hit (perf, review item 5). Per D-07/D-08.
                trailing_pe = info.get("trailingPE")
                forward_pe = info.get("forwardPE")
                if trailing_pe is None or forward_pe is None or trailing_pe <= 0:
                    pe_direction = "N/A"  # D-03: graceful N/A on missing forwardPE or zero/negative trailingPE
                elif abs(forward_pe - trailing_pe) / abs(trailing_pe) < 0.05:
                    pe_direction = "stable"  # D-01: ±5% stable band
                elif forward_pe < trailing_pe:
                    pe_direction = "expanding"   # D-02: earnings growing → multiple contracting
                else:
                    pe_direction = "contracting"  # D-02: earnings shrinking → multiple expanding

                try:
                    eps_trend = await asyncio.to_thread(fetch_eps_data, yf_ticker)
                except Exception as exc:
                    logger.warning(
                        "EPS data fetch failed for %s: %s — continuing without EPS trend",
                        ticker, exc,
                    )
                    eps_trend = None

                fundamental_trend = {
                    "pe_direction": pe_direction,
                    "eps_trend": eps_trend,
                }
                logger.debug("fundamental_trend for %s: pe_direction=%s, eps_quarters=%s",
                             ticker, pe_direction, len(eps_trend) if eps_trend else 0)

                return await asyncio.to_thread(
                    analyze_ticker, ticker, info, headlines, config,
                    client, fallback_client, macro_context=macro_context,
                    fundamental_trend=fundamental_trend,  # Phase 15 SIG-07, SIG-08
                    earnings_date=earnings_date_prompt,   # Phase 16 SIG-06
                    fallback2_client=fallback2_client,
                    on_attempt=on_attempt,
                )

            # Technicals BEFORE the analyst, though the gate still decides after
            # it. The fetch is free and the call is metered, so a ticker whose
            # history cannot be read no longer spends quota on its way to
            # `error`; and every exit below -- quota-exhausted included -- can
            # record the gate's verdict, which is the counterfactual of a
            # pipeline without the analyst. Who gets recommended is unchanged:
            # both conditions must hold, in either order.
            tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)
            technical_gate = evaluate_technicals(tech_data, config)

            analysis = await analyze_with_cache(config, ticker, headlines, _analyze_buy)
            if analysis is None:
                _screen_price, _screen_src = screen_price(info)
                # Quota refused this row, not the gate: settings recorded,
                # criterion cleared, verdict kept in its own column.
                _record_shadow(config, ticker, "stock", "analyst",
                               "skipped_quota_exhausted", fundamentals=info,
                               technicals=tech_data,
                               headlines=headlines, macro=macro_context,
                               reference_price=_screen_price,
                               reference_price_source=_screen_src,
                               gates=(fundamental_gate,
                                      technical_gate._replace(failed_on=None)),
                               technical_verdict=technical_gate)
                continue  # all providers quota-exhausted

            if not should_recommend(analysis["signal"], tech_data, config):
                # Two different refusals share one bool; the funnel needs them
                # apart, so re-check rather than change should_recommend's
                # return type on the live path for a research need.
                outcome = ("rejected_signal" if analysis["signal"] != "BUY"
                           else "rejected_technical")
                _screen_price, _screen_src = screen_price(info)
                # On a rejected_signal the ANALYST refused, not the technical
                # gate, so naming a technical criterion would misattribute the
                # rejection -- the gate's settings are still recorded.
                _gates = ((fundamental_gate, technical_gate)
                          if outcome == "rejected_technical"
                          else (fundamental_gate,
                                technical_gate._replace(failed_on=None)))
                _record_shadow(config, ticker, "stock", "technical", outcome,
                               fundamentals=info, technicals=tech_data,
                               headlines=headlines, macro=macro_context,
                               analysis=analysis,
                               reference_price=_screen_price,
                               reference_price_source=_screen_src,
                               gates=_gates,
                               technical_verdict=technical_gate)
                continue

            div_yield = normalize_dividend_yield(info.get("dividendYield"))

            rec_id = queries.create_recommendation(
                db_path=config.db_path,
                ticker=ticker,
                signal=analysis["signal"],
                reasoning=analysis["reasoning"],
                price=tech_data["price"],
                dividend_yield=div_yield,
                # The column keeps TRAILING P/E even though the gate judges
                # forward: changing what it means would mislabel older rows.
                pe_ratio=info.get("trailingPE"),
                earnings_growth=info.get("earningsGrowth"),
                confidence=analysis.get("confidence"),
            )

            message_id = await bot.send_recommendation(
                rec_id=rec_id,
                ticker=ticker,
                signal=analysis["signal"],
                reasoning=analysis["reasoning"],
                price=tech_data["price"],
                dividend_yield=div_yield,
                forward_pe=finite_number(info.get("forwardPE")),
                peg_ratio=finite_number(info.get("pegRatio")),
                confidence=analysis.get("confidence"),
                earnings_date=earnings_date_embed,   # NEW — Phase 16 SIG-05
                scan_time=scan_time,                 # NEW — Phase 17 RISK-04
            )
            queries.set_discord_message_id(config.db_path, rec_id, message_id)
            logger.info("Recommended %s", ticker)
            recommendations_posted += 1
            _screen_price, _screen_src = screen_price(info)
            _record_shadow(config, ticker, "stock", "recommended", "recommended",
                           fundamentals=info, technicals=tech_data,
                           headlines=headlines, macro=macro_context,
                           analysis=analysis, recommendation_id=rec_id,
                           reference_price=_screen_price,
                           reference_price_source=_screen_src,
                           gates=(fundamental_gate, technical_gate),
                           technical_verdict=technical_gate)

        except Exception as exc:
            logger.error("Error processing %s: %s", ticker, exc)
            error_count += 1
            if errors_posted < 3:
                await bot.send_ops_alert(f"[ERROR] {ticker}: {type(exc).__name__}")
                errors_posted += 1
            _record_shadow(config, ticker, "stock", "universe", "error",
                           reject_reason=f"{type(exc).__name__}: {exc}")
            continue

    if error_count > 3:
        overflow = error_count - 3
        await bot.send_ops_alert(f"[{overflow} more errors not shown \u2014 check logs]")

    # Health check: every headline fetch coming back empty across a real scan means the
    # news pipeline is broken (e.g. a yfinance schema change), not that there is no news.
    if headline_fetches >= 3 and empty_headline_fetches == headline_fetches:
        logger.warning(
            "All %d headline fetches returned 0 headlines \u2014 news pipeline may be broken.",
            headline_fetches,
        )
        await bot.send_ops_alert(
            f"All {headline_fetches} headline fetches returned 0 headlines \u2014 "
            "news pipeline may be broken (yfinance schema change or Alpha Vantage outage)."
        )

    if recommendations_posted == 0:
        logger.warning("Scan complete: 0 recommendations posted.")
        await bot.send_ops_alert("Scan complete: 0 recommendations posted.")
    else:
        logger.info("Scan complete. %d recommendation(s) posted.", recommendations_posted)

    # --- Position reconciliation (RISK-05): surface DB/broker drift before the
    # sell pass acts on positions that may not exist at the broker ---
    try:
        await run_reconciliation(bot, config)
    except Exception as exc:
        logger.warning("Reconciliation error: %s — continuing with sell pass", exc)

    # --- Sell pass: evaluate open positions for exit signals ---
    open_positions = queries.get_open_positions(config.db_path)
    logger.info("Sell pass: evaluating %d open position(s)", len(open_positions))

    for pos in open_positions:
        ticker = pos["ticker"]

        # D-06: skip sell-blocked positions entirely
        if pos["sell_blocked"]:
            logger.debug("Skipping %s: sell_blocked", ticker)
            # But still check if RSI dropped — reset sell_blocked if so
            try:
                yf_ticker = yf.Ticker(ticker)
                tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)
                if tech_data.get("rsi") is not None and tech_data["rsi"] <= config.sell_rsi_threshold:
                    queries.reset_sell_blocked(config.db_path, ticker)
                    logger.info("Reset sell_blocked for %s (RSI %.1f <= %.1f)", ticker, tech_data["rsi"], config.sell_rsi_threshold)
            except Exception as exc:
                logger.warning("Could not check RSI for sell_blocked reset on %s: %s", ticker, exc)
            continue

        try:
            yf_ticker = yf.Ticker(ticker)
            tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)
            sell_info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)

            # D-01 stage 1: RSI exit signal check
            if not check_exit_signals(tech_data, config):
                continue

            # D-01 stage 2: analyst sell analysis
            entry_price = pos["avg_cost_usd"]
            current_price = tech_data["price"]
            pnl_pct = (current_price - entry_price) / entry_price if entry_price else 0.0

            try:
                entry_date = date.fromisoformat(pos["entry_date"])
                hold_days = (date.today() - entry_date).days
            except (ValueError, TypeError):
                hold_days = 0

            headlines = await asyncio.to_thread(
                fetch_news_headlines, ticker,
                finnhub_api_key=config.finnhub_api_key,
                alpha_vantage_api_key=config.alpha_vantage_api_key,
            )

            # D-11: quota guard for sell analyst call
            if all_providers_exhausted(config):
                logger.warning(
                    "Daily analyst quota reached for all providers, skipping sell analysis for %s",
                    ticker,
                )
                continue

            analysis = await asyncio.to_thread(
                analyze_sell_ticker,
                ticker, entry_price, current_price, pnl_pct, hold_days,
                tech_data["rsi"], headlines, config, client, fallback_client,
                macd_line=tech_data.get("macd_line"),
                signal_line=tech_data.get("signal_line"),
                macro_context=macro_context,
                info=sell_info,
                fallback2_client=fallback2_client,
                on_attempt=on_attempt,
            )

            if analysis["signal"] != "SELL":
                logger.info("Analyst says HOLD for %s", ticker)
                continue

            # Create sell recommendation
            rec_id = queries.create_recommendation(
                db_path=config.db_path,
                ticker=ticker,
                signal="SELL",
                reasoning=analysis["reasoning"],
                price=current_price,
                dividend_yield=None,
                pe_ratio=None,
                confidence=analysis.get("confidence"),
            )

            message_id = await bot.send_sell_recommendation(
                rec_id=rec_id,
                ticker=ticker,
                reasoning=analysis["reasoning"],
                entry_price=entry_price,
                current_price=current_price,
                pnl_pct=pnl_pct,
                shares=pos["shares"],
                rsi=tech_data["rsi"],
                confidence=analysis.get("confidence"),
            )
            queries.set_discord_message_id(config.db_path, rec_id, message_id)
            logger.info("Sell recommendation posted for %s", ticker)

        except Exception as exc:
            logger.error("Error in sell evaluation for %s: %s", ticker, exc)
            continue


# ---------------------------------------------------------------------------
# ETF scan pipeline
# ---------------------------------------------------------------------------

async def run_scan_etf(bot: TradingBot, config: Config) -> None:
    """Run the ETF-only screening pipeline, one scan at a time.

    Skipped, never queued, when another scan already holds the lock. Running it
    afterwards would screen a market that has already moved on, spend analyst
    quota a second time, and post recommendations stamped to a moment that has
    passed. ONE lock covers both scan paths: a symbol can appear in the stock
    universe and in the ETF universe.
    """
    if not scan_allowed_now():
        logger.info(
            "ETF scan skipped: %s is not an NYSE trading session", market_session_date()
        )
        return
    lock = scan_lock()
    if lock.locked():
        logger.warning("ETF scan skipped: another scan is already running")
        return
    async with lock:
        await _run_scan_etf_locked(bot, config)


async def _run_scan_etf_locked(bot: TradingBot, config: Config) -> None:
    """Run the ETF screening pipeline and post qualifying tickers to Discord (per ETF-02)."""
    logger.info("Starting ETF scan...")
    await _drain_ops_outbox(bot)
    # Repeated on every scan: guard 11 blocks this ticker until a human
    # runs /resolve, and an alert nobody repeats is a block nobody sees.
    await alert_stuck_orders(bot, config)
    await alert_schwab_login(bot, config)
    # Before anything is screened, not after: a ticker whose order the broker
    # has finished with should be eligible in THIS scan, not the next one.
    # Both scan paths post recommendations, so both must be able to release a
    # ticker the index is still holding.
    try:
        await sweep_terminal_recommendations(config)
    except Exception:
        # Reporting and housekeeping must never abort the scan they run inside.
        logger.exception("Terminal-order sweep failed; continuing the ETF scan")
    # Marked here too, for the same reason the sweep is: a maintenance step
    # wired into one scan path and not the other is exactly the bug that left
    # the ETF path never sweeping. Marking is idempotent and universe-agnostic,
    # so on a day only the ETF scan runs the marks still advance.
    try:
        await outcomes.mark_due_outcomes(config)
    except Exception:
        logger.exception("Shadow outcome marking failed; continuing the ETF scan")
    queries.expire_stale_recommendations(config.db_path)

    etf_watchlist_path = str(Path(__file__).parent / "etf_watchlist.txt")
    etf_tickers = get_watchlist(etf_watchlist_path)

    # D-08 / ASYNC-03: wrap partition_watchlist in asyncio.to_thread.
    # Capture .info per ticker so the loop reuses it instead of re-fetching for expense ratio.
    info_by_ticker: dict = {}
    _stocks, etfs = await asyncio.to_thread(partition_watchlist, etf_tickers, info_by_ticker)  # P8-audit: already wrapped (Phase 7)
    logger.info("ETF universe: %d tickers", len(etfs))

    # Fetch macro context once for all ETFs (D-02)
    try:
        macro_context = await asyncio.to_thread(fetch_macro_context)
    except Exception as exc:
        logger.warning("Macro context fetch failed: %s — continuing without macro", exc)
        macro_context = {"spy_trend_1m": None, "spy_trend_1y": None, "vix_level": None}

    client = create_analyst_client(config)
    fallback_client = create_fallback_client(config)
    fallback2_client = create_fallback2_client(config)

    def on_attempt(provider: str, model: str) -> None:
        # Count every attempt against today's quota, per model (see run_scan).
        queries.increment_analyst_call_count(config.db_path, provider, model)

    recommendations_posted = 0
    error_count = 0
    errors_posted = 0

    for ticker in etfs:
        if queries.ticker_recommended_today(config.db_path, ticker):
            _record_shadow(config, ticker, "etf", "universe",
                           "skipped_recommended_today")
            continue
        if queries.has_open_position(config.db_path, ticker):
            logger.debug("Skipping %s: open position exists", ticker)
            _record_shadow(config, ticker, "etf", "universe",
                           "skipped_open_position")
            continue
        if queries.has_active_recommendation(config.db_path, ticker):
            # Not the same question as ticker_recommended_today, which is
            # session-scoped: an `approved` row left by an ambiguous submission
            # days ago is invisible to that guard but blocks this insert.
            logger.debug("Skipping %s: an active recommendation already exists", ticker)
            _record_shadow(config, ticker, "etf", "universe",
                           "skipped_active_recommendation")
            continue

        try:
            yf_ticker = yf.Ticker(ticker)

            # Fetch technical data (no fundamental filter for ETFs)
            tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)

            # Fetch expense ratio from yfinance info (reuse partition_watchlist's .info; fetch on miss)
            info = info_by_ticker.get(ticker)
            if info is None:
                info = await asyncio.to_thread(fetch_fundamental_info, yf_ticker)
            expense_ratio = info.get("netExpenseRatio")
            if expense_ratio is None:
                logger.debug("Expense ratio unavailable for %s", ticker)

            # ONE screen price per cohort, stock or ETF. The technical price
            # (closes.iloc[-1]) is NOT interchangeable with .info: a different
            # endpoint, fetched minutes later, auto-adjusted where .info is
            # raw. Computed here, once, because .info is already in hand for
            # the expense ratio -- and passed as an ARGUMENT to _record_shadow
            # below, so it is evaluated before that wrapper's try block.
            # screen_price is total by contract and never raises.
            _screen_price, _screen_src = screen_price(info)

            # Fetch news headlines (per D-01)
            headlines = await asyncio.to_thread(
                fetch_news_headlines, ticker,
                finnhub_api_key=config.finnhub_api_key,
                alpha_vantage_api_key=config.alpha_vantage_api_key,
            )

            async def _analyze_etf():
                return await asyncio.to_thread(
                    analyze_etf_ticker, ticker, headlines, tech_data,
                    expense_ratio, config, client, fallback_client,
                    macro_context=macro_context,
                    fallback2_client=fallback2_client,
                    on_attempt=on_attempt,
                )

            # Shared analyst-cache + quota path (same helper as the run_scan buy pass)
            analysis = await analyze_with_cache(config, ticker, headlines, _analyze_etf)
            if analysis is None:
                _record_shadow(config, ticker, "etf", "analyst",
                               "skipped_quota_exhausted", technicals=tech_data,
                               headlines=headlines, macro=macro_context,
                               reference_price=_screen_price,
                               reference_price_source=_screen_src)
                continue  # all providers quota-exhausted

            # ETF uses BUY signal check but no technical filter (no fundamental filter per ETF-02)
            if analysis["signal"] != "BUY":
                _record_shadow(config, ticker, "etf", "technical", "rejected_signal",
                               technicals=tech_data, headlines=headlines,
                               macro=macro_context, analysis=analysis,
                               reference_price=_screen_price,
                               reference_price_source=_screen_src)
                continue

            rec_id = queries.create_recommendation(
                db_path=config.db_path,
                ticker=ticker,
                signal=analysis["signal"],
                reasoning=analysis["reasoning"],
                price=tech_data["price"] or 0.0,
                dividend_yield=None,
                pe_ratio=None,
                asset_type="etf",
                confidence=analysis.get("confidence"),
            )

            message_id = await bot.send_etf_recommendation(
                rec_id=rec_id,
                ticker=ticker,
                signal=analysis["signal"],
                reasoning=analysis["reasoning"],
                price=tech_data.get("price"),
                rsi=tech_data.get("rsi"),
                ma50=tech_data.get("ma50"),
                expense_ratio=expense_ratio,
                etf_max_expense_ratio=config.etf_max_expense_ratio,
                confidence=analysis.get("confidence"),
            )
            queries.set_discord_message_id(config.db_path, rec_id, message_id)
            logger.info("ETF recommended %s", ticker)
            recommendations_posted += 1
            _record_shadow(config, ticker, "etf", "recommended", "recommended",
                           technicals=tech_data, headlines=headlines,
                           macro=macro_context, analysis=analysis,
                           recommendation_id=rec_id,
                           reference_price=_screen_price,
                           reference_price_source=_screen_src)

        except sqlite3.OperationalError as exc:
            logger.error("ETF scan aborted — DB schema error: %s", exc)
            await bot.send_ops_alert(f"ETF scan aborted — DB schema error: {exc}")
            return
        except Exception as exc:
            logger.error("Error processing ETF %s: %s", ticker, exc)
            error_count += 1
            if errors_posted < 3:
                await bot.send_ops_alert(f"[ERROR] {ticker}: {type(exc).__name__}")
                errors_posted += 1
            _record_shadow(config, ticker, "etf", "universe", "error",
                           reject_reason=f"{type(exc).__name__}: {exc}")
            continue

    if error_count > 3:
        overflow = error_count - 3
        await bot.send_ops_alert(f"[{overflow} more errors not shown \u2014 check logs]")

    if recommendations_posted == 0:
        logger.warning("ETF scan complete: 0 recommendations posted.")
        await bot.send_ops_alert("[ETF] ETF scan complete: 0 recommendations posted.")
    else:
        logger.info("ETF scan complete. %d recommendation(s) posted.", recommendations_posted)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Configure logging and the DB, construct the Discord bot and scheduler, then block until the bot exits."""
    config = Config()
    config.validate()

    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    _log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    logging.root.setLevel(_log_level)
    for _handler in build_log_handlers(log_dir, stream=sys.stderr):
        logging.root.addHandler(_handler)

    # Decided BEFORE Discord is touched: a process that should not be up must
    # not open a gateway connection it will only have to tear down, and must
    # not leave anything behind to be orphaned.
    _status = session_window_status(config=config)
    if _status == "not_a_session":
        logger.info("%s is not an NYSE trading session — not starting.", market_session_date())
        return
    if _status == "already_closed":
        logger.info("Session %s has already closed — not starting.", market_session_date())
        return
    if _status == "scans_done":
        logger.info(
            "Session %s: the scheduled scans and their %d-min approval window are over — not starting.",
            market_session_date(), config.post_scan_window_min,
        )
        return

    # On the main thread, which lives exactly as long as the process: Windows
    # drops the request when it exits, so normal sleep resumes by itself.
    if hold_system_awake():
        logger.info("Holding the PC awake while the bot runs (released when it exits).")
    else:
        logger.warning("Could not hold the PC awake: an idle sleep can freeze the scheduled scans.")

    initialize_db(config.db_path)

    # Seeds only a database that has never been written; a persisted halt wins
    # over TRADING_ENABLED, so a restart cannot quietly re-arm the bot.
    state = kill_switch.init(config.db_path, config.trading_enabled)
    logger.info("Kill switch state at startup: %s", state)

    bot = TradingBot(config)
    bot._scan_callback = lambda: run_scan(bot, config)
    bot._scan_etf_callback = lambda: run_scan_etf(bot, config)
    bot._reconcile_callback = lambda: run_reconciliation(bot, config, alert_on_discrepancy=False)
    scheduler = BackgroundScheduler()

    @bot.event
    async def on_ready():
        """Validate the Discord channel, warn if live trading is active, then start the APScheduler scan jobs."""
        logger.info("Discord bot ready as %s", bot.user)
        try:
            await bot.fetch_channel(config.discord_channel_id)
            logger.info("Discord channel %s verified.", config.discord_channel_id)
        except Exception as exc:
            logger.error(
                "Cannot access Discord channel %s: %s — aborting startup.",
                config.discord_channel_id,
                exc,
            )
            raise RuntimeError(
                f"Discord channel {config.discord_channel_id} not accessible: {exc}"
            ) from exc

        # The channel check above runs on every reconnect -- it is a live health
        # check and costs one API call. Everything below runs exactly once.
        if not scheduler_needs_setup(scheduler):
            logger.info(
                "Reconnected to the gateway; the scheduler is already running, "
                "so its jobs and listener are left exactly as they are."
            )
            return

        banner = live_execution_banner(config)
        if banner:
            logger.warning("%s", banner)
            await bot.send_ops_alert(banner)

        configure_scheduler(
            scheduler,
            config,
            lambda: asyncio.run_coroutine_threadsafe(
                run_scan(bot, config), bot.loop
            ).result(),
        )
        configure_scheduler(
            scheduler,
            config,
            lambda: asyncio.run_coroutine_threadsafe(
                run_scan_etf(bot, config), bot.loop
            ).result(),
            times=config.etf_scan_times,
            job_id_prefix="etf_scan",
        )
        _close = session_close_utc()
        if _close is not None:
            def _shutdown_at_the_bell():
                logger.info("Session closed — shutting down until the next one.")
                # Deliberately NOT .result(): this runs on the scheduler thread,
                # and waiting on the coroutine that stops the loop invites a
                # deadlock. The scan jobs wait because they need the outcome;
                # this one only needs the request delivered.
                asyncio.run_coroutine_threadsafe(bot.close(), bot.loop)

            schedule_session_shutdown(scheduler, _shutdown_at_the_bell, _close)

            def _shutdown_after_scans():
                logger.info("Scans done and the approval window is over — shutting down until the next session.")
                # NOT .result(), for the same reason as the bell shutdown.
                asyncio.run_coroutine_threadsafe(bot.close(), bot.loop)

            def _arm_post_scan_shutdown(when):
                schedule_post_scan_shutdown(scheduler, _shutdown_after_scans, when)
                logger.info(
                    "Last scheduled scan is done — shutting down at %s (%d-min approval window).",
                    when.astimezone().strftime("%H:%M %Z"), config.post_scan_window_min,
                )

            scheduler.add_listener(
                make_post_scan_listener(
                    jobs_fn=scheduler.get_jobs,
                    schedule_fn=_arm_post_scan_shutdown,
                    close_utc=_close,
                    window_min=config.post_scan_window_min,
                    clock=lambda: datetime.now(timezone.utc),
                ),
                EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
            )

        scheduler.start()
        if _close is not None:
            logger.info(
                "Shutting down at the close: %s",
                _close.astimezone().strftime("%Y-%m-%d %H:%M %Z"),
            )
            # Restarted inside the approval window (the startup guard let it
            # through): no scan job will fire today, so no event will arm the
            # exit. Arm it here, at the window's end rather than a fresh 30 min.
            _last = last_scheduled_scan_utc(config)
            if _last is not None and datetime.now(timezone.utc) >= _last:
                _arm_post_scan_shutdown(_last + timedelta(minutes=config.post_scan_window_min))
        logger.info(
            "%s", scheduler_summary("Stock scan", config.scan_times, config.scan_timezone)
        )
        logger.info(
            "%s", scheduler_summary("ETF scan", config.etf_scan_times, config.scan_timezone)
        )

    # log_handler=None stops discord.py adding a StreamHandler to the 'discord'
    # logger. It does not set propagate=False when it does, so every discord
    # record was emitted twice on the console: once there, once by the root
    # StreamHandler above. The file handler is on root and never doubled, which
    # is why logs/algo_trade.log looked right while the terminal did not.
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
